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
import { openEndpointParamsModal } from "./tabs/endpoint_params.js";
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

  // Only show available tabs
  const tabs = r.manifest.tabs.filter((t) => t.available && t.id !== "endpoint_params").map((t) => ({
    key: t.id, label: t.label,
    count: t.id === "privacy" ? hidden : t.id === "overview" || t.id === "cockpit" ? null : t.count,
    render: () => (t.id === "privacy" ? privacyTab(id, r) : RENDER[t.id](r)),
  }));

  const defaultTab = pickDefaultTab(r, tabs);
  const panel = h("div", { role: "tabpanel" });
  const bar = h("div", { class: "tabs", role: "tablist" });
  const sideNav = h("nav", { class: "section-nav", "aria-label": "Page sections" });
  let scrollSpyCleanup = null;

  const select = (key) => {
    try { sessionStorage.setItem("lp-tab", key); } catch {}
    mount(bar, tabs.map((t) =>
      h("button", {
        class: "tab", role: "tab", type: "button",
        "aria-selected": String(t.key === key),
        "aria-controls": "review-panel",
        onclick: () => select(t.key),
      }, t.label, t.count != null ? h("span", { class: "count" }, fmt.num(t.count)) : null)));
    panel.id = "review-panel";
    mount(panel, tabs.find((t) => t.key === key).render());

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

  // H1: task name + log filename
  const taskName = meta.task;
  const fileName = r.input.filename;
  const titleEl = taskName && taskName !== fileName
    ? h("div", { class: "review-header" },
        h("h1", {}, taskName),
        h("div", { class: "muted small", style: "margin-top:-4px;margin-bottom:6px" }, fileName))
    : h("div", { class: "review-header" }, h("h1", {}, fileName || "Log review"));

  // Meta bar: clickable endpoints (open params modal), info chips, timeline with duration
  const duration = computeDuration(stats.first_ts, stats.last_ts);
  const hasEndpoints = meta.source_endpoint || meta.target_endpoint;
  const metaBar = h("div", { class: "meta-bar" },
    h("div", { class: "meta-row" },
      h("div", { class: "meta-endpoints" },
        meta.source_endpoint
          ? h("button", { class: "chip chip-btn", type: "button",
              title: "View source endpoint internal parameters",
              onclick: () => openEndpointParamsModal(r, "source") },
              "Source ", h("b", {}, meta.source_endpoint), h("span", { class: "chip-caret" }, "▸"))
          : null,
        meta.source_endpoint && meta.target_endpoint
          ? h("span", { class: "meta-arrow" }, "→") : null,
        meta.target_endpoint
          ? h("button", { class: "chip chip-btn", type: "button",
              title: "View target endpoint internal parameters",
              onclick: () => openEndpointParamsModal(r, "target") },
              "Target ", h("b", {}, meta.target_endpoint), h("span", { class: "chip-caret" }, "▸"))
          : null,
        hasEndpoints
          ? h("span", { class: "meta-hint" }, "View parameters") : null),
      h("div", { class: "meta-info" },
        meta.is_logstream ? h("span", { class: "chip chip-logstream" }, "⛓ ", h("b", {}, "Logstream")) : null,
        meta.mode ? h("span", { class: "chip" }, "Mode ", h("b", {}, meta.mode)) : null,
        meta.version ? h("span", { class: "chip" }, "Version ", h("b", {}, meta.version)) : null)),
    (stats.first_ts || stats.last_ts)
      ? h("div", { class: "meta-timeline" },
          h("span", { class: "meta-timeline-icon" }, icon("calendar", 15)),
          h("span", { class: "meta-ts" }, fmtReadableTs(stats.first_ts)),
          h("span", { class: "meta-timeline-arrow" }, "→"),
          h("span", { class: "meta-ts" }, fmtReadableTs(stats.last_ts)),
          duration ? h("span", { class: "meta-duration" }, duration) : null)
      : null);

  // Token savings footer — centered, fancier
  const rawTokens = Math.max(1, Math.round((r.input.bytes || 0) / CHARS_PER_TOKEN));
  const packTokens = Math.min(rawTokens, STANDARD_PACK_TOKENS);
  const savedPct = rawTokens > 0 ? Math.round((1 - packTokens / rawTokens) * 100) : 0;
  const savingsEl = rawTokens > packTokens
    ? h("div", { class: "savings-strip" },
        h("span", { class: "savings-icon" }, "⚡"),
        h("span", {}, `${fmt.tokens(rawTokens)} tokens`, h("span", { class: "savings-arrow" }, " → "), `${fmt.tokens(packTokens)} tokens`),
        h("span", { class: "savings-badge" }, `${savedPct}% smaller`))
    : h("div", { class: "savings-strip" },
        h("span", { class: "savings-icon" }, "📦"),
        h("span", {}, `Log ≈ ${fmt.tokens(rawTokens)} tokens`));

  const footer = h("div", { class: "review-footer" },
    savingsEl,
    h("a", { class: "btn primary", href: `#/job/${id}/pack` }, "Continue to pack", icon("arrow", 16)));

  const tabSection = h("section", { class: "card", id: "s-findings" }, bar, panel);

  const content = h("div", { class: "review-content" },
    titleEl,
    metaBar,
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
 * Rebuild the side nav: ▲ Top, dynamic tab sections, ▼ Bottom.
 * Returns a cleanup function to disconnect the scroll-spy observer.
 */
function rebuildSectionNav(nav, panel) {
  const stops = [];

  // Discover sections inside the active tab content
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
    stops.push({ id: secId, label });
  }

  // Build DOM: ▲ Top + dynamic stops + ▼ Bottom
  mount(nav,
    h("div", { class: "nav-line" }),
    h("a", { href: "#", class: "nav-stop nav-fixed", onclick: (e) => { e.preventDefault(); window.scrollTo({ top: 0, behavior: "smooth" }); } },
      h("span", { class: "nav-dot" }),
      h("span", { class: "nav-label" }, "▲ Top")),
    stops.map((s) =>
      h("a", {
        href: `#${s.id}`, class: "nav-stop", dataset: { target: s.id },
        onclick: (e) => { e.preventDefault(); scrollToSection(s.id); },
      },
        h("span", { class: "nav-dot" }),
        h("span", { class: "nav-label" }, s.label))),
    h("a", { href: "#", class: "nav-stop nav-fixed", onclick: (e) => { e.preventDefault(); window.scrollTo({ top: document.body.scrollHeight, behavior: "smooth" }); } },
      h("span", { class: "nav-dot" }),
      h("span", { class: "nav-label" }, "▼ Bottom")));

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

/** Scroll to a section by id, expanding it if collapsed, accounting for the sticky topbar. */
function scrollToSection(id) {
  const el = document.getElementById(id);
  if (!el) return;
  // If it's a collapsed <details>, open it first
  if (el.tagName === "DETAILS" && !el.open) el.open = true;
  const topbar = document.querySelector(".topbar");
  const offset = topbar ? topbar.offsetHeight + 12 : 60;
  const y = el.getBoundingClientRect().top + window.scrollY - offset;
  window.scrollTo({ top: y, behavior: "smooth" });
}

function pickDefaultTab(r, tabs) {
  // Deep-link: ?tab=cockpit in the URL selects that tab directly (used by MCP open_report).
  try {
    const params = new URLSearchParams(location.hash.split("?")[1] || "");
    const requested = params.get("tab");
    if (requested && tabs.some((t) => t.key === requested)) return requested;
  } catch {}
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

function fmtReadableTs(ts) {
  if (!ts) return "—";
  const d = new Date(ts.includes("T") ? ts : ts.replace(" ", "T"));
  if (isNaN(d)) return ts.replace("T", " ");
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const pad = (n) => String(n).padStart(2, "0");
  return `${months[d.getMonth()]} ${d.getDate()}, ${d.getFullYear()} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function computeDuration(startTs, endTs) {
  if (!startTs || !endTs) return null;
  const s = new Date(startTs.includes("T") ? startTs : startTs.replace(" ", "T"));
  const e = new Date(endTs.includes("T") ? endTs : endTs.replace(" ", "T"));
  if (isNaN(s) || isNaN(e)) return null;
  const ms = e - s;
  if (ms <= 0) return null;
  const totalMin = Math.floor(ms / 60000);
  const totalHrs = Math.floor(totalMin / 60);
  const mins = totalMin % 60;
  const days = Math.floor(totalHrs / 24);
  const hrs = totalHrs % 24;
  if (days > 0) return `${days}d ${hrs}h ${mins}m`;
  if (hrs > 0) return `${hrs}h ${mins}m`;
  if (mins > 0) return `${mins}m`;
  return "< 1m";
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
