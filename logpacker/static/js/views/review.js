// Step 2: what LogPacker found. Verdict, KPIs, insight strip, tabs, sticky CTA, section nav.
import { api } from "../api.js";
import { fmt, h, icon, mount } from "../dom.js";
import { store } from "../store.js";
import { overviewTab } from "./tabs/overview.js";
import { issuesTab } from "./tabs/issues.js";
import { cockpitTab } from "./tabs/cockpit.js";
import { bulkTab } from "./tabs/bulk.js";
import { fullLoadTab } from "./tabs/fullload.js";
import { fileOpsTab } from "./tabs/fileops.js";
import { privacyTab } from "./tabs/privacy.js";
import { patternsTab } from "./tabs/patterns.js";
import { buildHighlightInsights } from "./highlightInsights.js";
import { pill } from "../components.js";

const RENDER = {
  overview: overviewTab, issues: issuesTab, cockpit: cockpitTab, bulk: bulkTab,
  fullload: fullLoadTab, fileops: fileOpsTab, patterns: patternsTab,
};

const CHARS_PER_TOKEN = 4;
const STANDARD_PACK_TOKENS = 155_000;

export async function loadReport(id) {
  if (!store.reports.has(id)) store.reports.set(id, await api.report(id));
  return store.reports.get(id);
}

export function renderReview(root, id) {
  mount(root, h("div", { class: "empty" }, h("span", { class: "spinner" }), " Loading the review…"));
  loadReport(id).then((report) => draw(root, id, report)).catch((err) =>
    mount(root, h("div", { class: "empty" }, h("p", {}, err.message), h("a", { class: "btn", href: "#/" }, "Upload a log"))));
}

function draw(root, id, r) {
  if (!r.manifest) return outdated(root);
  const meta = r.metadata, stats = r.stats;
  const hidden = r.privacy.categories.reduce((a, c) => a + c.replacements, 0);
  const health = r.manifest.health;

  const tabs = r.manifest.tabs.map((t) => ({
    key: t.id, label: t.label, available: t.available, reason: t.reason || "",
    count: t.id === "privacy" ? hidden : t.id === "overview" || t.id === "cockpit" ? null : t.count,
    render: t.available ? () => (t.id === "privacy" ? privacyTab(id, r) : RENDER[t.id](r)) : null,
  }));

  const defaultTab = pickDefaultTab(r, tabs);
  const panel = h("div", { role: "tabpanel" });
  const bar = h("div", { class: "tabs", role: "tablist" });
  const sideNav = h("nav", { class: "section-nav", "aria-label": "Page sections" });
  let scrollSpyCleanup = null;

  const select = (key) => {
    try { sessionStorage.setItem("lp-tab", key); } catch {}
    mount(bar, tabs.map((t) => {
      if (!t.available) {
        return h("button", {
          class: "tab tab-unavailable", role: "tab", type: "button",
          "aria-selected": "false", "aria-disabled": "true",
          title: t.reason || "Not available for this log",
        }, t.label);
      }
      return h("button", {
        class: "tab", role: "tab", type: "button",
        "aria-selected": String(t.key === key),
        "aria-controls": "review-panel",
        onclick: () => select(t.key),
      }, t.label, t.count != null ? h("span", { class: "count" }, fmt.num(t.count)) : null);
    }));
    panel.id = "review-panel";
    mount(panel, tabs.find((t) => t.key === key).render());

    // Rebuild section nav after tab content is in the DOM
    if (scrollSpyCleanup) scrollSpyCleanup();
    scrollSpyCleanup = rebuildSectionNav(sideNav, panel);
  };

  // Verdict band
  const scoreTone = health.score >= 4 ? "good" : health.score === 3 ? "warn" : "error";
  const highlights = buildHighlightInsights(r, 3);
  const verdictBand = h("div", { class: "verdict-band", id: "s-health" },
    h("div", { class: `verdict-score ${scoreTone}` }, `${health.score}/5`),
    h("div", { class: "verdict-body" },
      pill(health.label, scoreTone),
      health.reasons.length
        ? h("div", { class: "muted small", style: "margin-top:4px" }, `Lowered by: ${health.reasons.join(", ")}.`)
        : h("div", { class: "muted small", style: "margin-top:4px" }, "No problems found by the parser.")),
    highlights.length ? h("div", { class: "verdict-insights" },
      highlights.map((ins) => {
        const tone = ins.severity === "error" ? "error" : ins.severity === "warning" ? "warn" : "info";
        const targetTab = tone === "error" || tone === "warn" ? "issues" : "overview";
        return h("div", { class: "insight-strip", onclick: () => select(targetTab), title: ins.message || ins.description || "" },
          h("span", { class: `badge ${tone}` }, (ins.severity || "info").toUpperCase()),
          h("span", {}, ins.title));
      })) : null);

  // 4 KPIs (clickable)
  const kpis = h("div", { class: "kpis kpis-review", id: "s-metrics" },
    kpi("Lines", fmt.num(stats.lines), "", () => select("overview")),
    kpi("Errors", fmt.num(stats.errors), stats.errors ? "error" : "good", () => select("issues")),
    kpi("Warnings", fmt.num(stats.warnings), stats.warnings ? "warn" : "good", () => select("issues")),
    kpi("Values hidden", fmt.num(hidden), "good", () => select("privacy")));

  // Header chips
  const title = meta.task || r.input.filename;
  const chips = h("div", { class: "chips" },
    chip("Version", meta.version), chip("Source", meta.source_endpoint), chip("Target", meta.target_endpoint),
    chip("Mode", meta.mode), chip("From", fmt.ts(stats.first_ts)), chip("To", fmt.ts(stats.last_ts)));

  // Token savings footer
  const rawTokens = Math.max(1, Math.round((r.input.bytes || 0) / CHARS_PER_TOKEN));
  const packTokens = Math.min(rawTokens, STANDARD_PACK_TOKENS);
  const savedPct = rawTokens > 0 ? Math.round((1 - packTokens / rawTokens) * 100) : 0;
  const savingsEl = rawTokens > packTokens
    ? h("span", { class: "muted small" },
        `Raw log ≈ ${fmt.tokens(rawTokens)} tokens → packed ≈ ${fmt.tokens(packTokens)} tokens`,
        h("span", { class: "savings-badge" }, `${savedPct}% smaller`))
    : h("span", { class: "muted small" }, `Log ≈ ${fmt.tokens(rawTokens)} tokens`);

  const footer = h("div", { class: "review-footer" },
    savingsEl,
    h("a", { class: "btn primary", href: `#/job/${id}/pack` }, "Continue to pack", icon("arrow", 16)));

  const tabSection = h("section", { class: "card", id: "s-findings" }, bar, panel);

  const content = h("div", { class: "review-content" },
    h("div", { class: "review-header" },
      h("h1", {}, title),
      chips),
    verdictBand,
    kpis,
    qualityNote(r),
    tabSection,
    footer,
  );

  mount(root, h("div", { class: "review-layout" }, content, sideNav));
  select(defaultTab);
}

/**
 * Scan the rendered tab panel for navigable landmarks, rebuild the side nav,
 * and wire up IntersectionObserver scroll-spy. Returns a cleanup function.
 *
 * Stops are two-level:
 *   depth 0 — Health, Metrics, Findings (top-level page sections)
 *   depth 1 — sections discovered inside the active tab content (nested under Findings)
 */
function rebuildSectionNav(nav, panel) {
  const stops = [
    { id: "s-health",  label: "Health",   depth: 0 },
    { id: "s-metrics", label: "Metrics",  depth: 0 },
    { id: "s-findings", label: "Findings", depth: 0 },
  ];

  // Discover sections inside the active tab content (only report-sections and timeline)
  let idx = 0;
  for (const el of panel.querySelectorAll(".report-section, .timeline-block")) {
    const secId = `s-tab-${idx++}`;
    el.id = secId;

    let label;
    if (el.classList.contains("report-section")) {
      const h3 = el.querySelector("summary h3");
      label = h3?.textContent || "Section";
    } else if (el.classList.contains("timeline-block")) {
      label = "Timeline";
    }
    stops.push({ id: secId, label, depth: 1 });
  }

  // Build DOM — each stop gets a depth class for nesting
  mount(nav,
    h("div", { class: "nav-line" }),
    stops.map((s) =>
      h("a", {
        href: `#${s.id}`,
        class: `nav-stop${s.depth ? ` depth-${s.depth}` : ""}`,
        dataset: { target: s.id },
        onclick: (e) => {
          e.preventDefault();
          scrollToSection(s.id);
        },
      },
        h("span", { class: "nav-dot" }),
        h("span", { class: "nav-label" }, s.label))));

  // Scroll-spy via IntersectionObserver
  const observer = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      const link = nav.querySelector(`[data-target="${entry.target.id}"]`);
      if (link) link.classList.toggle("active", entry.isIntersecting);
    }
  }, { rootMargin: "-10% 0px -70% 0px", threshold: 0 });

  for (const s of stops) {
    const el = document.getElementById(s.id);
    if (el) observer.observe(el);
  }

  return () => observer.disconnect();
}

/** Scroll to a section by id, accounting for the sticky topbar height. */
function scrollToSection(id) {
  const el = document.getElementById(id);
  if (!el) return;
  const topbar = document.querySelector(".topbar");
  const offset = topbar ? topbar.offsetHeight + 12 : 60;
  const y = el.getBoundingClientRect().top + window.scrollY - offset;
  window.scrollTo({ top: y, behavior: "smooth" });
}

function pickDefaultTab(r, tabs) {
  try {
    const saved = sessionStorage.getItem("lp-tab");
    if (saved && tabs.some((t) => t.key === saved && t.available)) return saved;
  } catch {}
  if (r.stats.errors > 0 && tabs.some((t) => t.key === "issues" && t.available)) return "issues";
  if (r.manifest.health.score <= 2 && tabs.some((t) => t.key === "issues" && t.available)) return "issues";
  return "overview";
}

function outdated(root) {
  mount(root, h("div", { class: "card empty" },
    h("p", {}, "This log was analysed by an older version of LogPacker, before the full reports existed."),
    h("a", { class: "btn primary", href: "#/" }, "Upload it again")));
}

function chip(name, value) {
  return value ? h("span", { class: "chip" }, name, h("b", {}, value)) : null;
}

function kpi(name, value, tone = "", onclick = null) {
  return h("div", { class: `kpi ${tone}${onclick ? " clickable" : ""}`, onclick },
    h("div", { class: "value" }, value), h("div", { class: "name" }, name));
}

function qualityNote(r) {
  const notes = [];
  if (!r.privacy.enabled) notes.push("Sanitizing was turned off for this log, so the package contains original values.");
  if (r.quality.unparsed_lines) notes.push(`${fmt.num(r.quality.unparsed_lines)} lines at the start had no Replicate prefix.`);
  if (r.quality.decode_errors) notes.push(`${fmt.num(r.quality.decode_errors)} lines had invalid characters and were repaired.`);
  if (!notes.length) return null;
  return h("div", { class: `callout ${r.privacy.enabled ? "" : "warn"}`, style: "margin-bottom:16px" }, notes.join(" "));
}
