// Step 2: what LogPacker found. Header, key numbers and one tab per finding type.
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

const RENDER = {
  overview: overviewTab, issues: issuesTab, cockpit: cockpitTab, bulk: bulkTab,
  fullload: fullLoadTab, fileops: fileOpsTab, patterns: patternsTab,
};

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
  const tabs = r.manifest.tabs.filter((t) => t.available).map((t) => ({
    key: t.id, label: t.label,
    count: t.id === "privacy" ? hidden : t.id === "overview" || t.id === "cockpit" ? null : t.count,
    render: () => (t.id === "privacy" ? privacyTab(id, r) : RENDER[t.id](r)),
  }));
  const panel = h("div", { role: "tabpanel" });
  const bar = h("div", { class: "tabs", role: "tablist" });
  const select = (key) => {
    mount(bar, tabs.map((t) => h("button", {
      class: "tab", role: "tab", type: "button", "aria-selected": String(t.key === key), onclick: () => select(t.key),
    }, t.label, t.count != null ? h("span", { class: "count" }, fmt.num(t.count)) : null)));
    mount(panel, tabs.find((t) => t.key === key).render());
  };

  const title = meta.task || r.input.filename;
  mount(root,
    h("div", { class: "row spread" },
      h("div", {},
        h("h1", {}, title),
        h("div", { class: "chips" },
          chip("Version", meta.version), chip("Source", meta.source_endpoint), chip("Target", meta.target_endpoint),
          chip("Mode", meta.mode), chip("From", fmt.ts(stats.first_ts)), chip("To", fmt.ts(stats.last_ts)))),
      h("a", { class: "btn primary", href: `#/job/${id}/pack` }, "Continue to pack", icon("arrow", 16))),
    h("div", { class: "kpis" },
      kpi("Health", `${r.manifest.health.score}/5`, r.manifest.health.score >= 4 ? "good" : r.manifest.health.score === 3 ? "warn" : "error"),
      kpi("Lines", fmt.num(stats.lines)),
      kpi("Errors", fmt.num(stats.errors), stats.errors ? "error" : "good"),
      kpi("Warnings", fmt.num(stats.warnings), stats.warnings ? "warn" : "good"),
      kpi("Avg latency", r.latency.samples ? `${fmt.num(Math.round(r.latency.target.avg))}s` : "—"),
      kpi("Values hidden", fmt.num(hidden), "good")),
    qualityNote(r),
    h("section", { class: "card" }, bar, panel),
  );
  select("overview");
}

function outdated(root) {
  mount(root, h("div", { class: "card empty" },
    h("p", {}, "This log was analysed by an older version of LogPacker, before the full reports existed."),
    h("a", { class: "btn primary", href: "#/" }, "Upload it again")));
}

function chip(name, value) {
  return value ? h("span", { class: "chip" }, name, h("b", {}, value)) : null;
}

function kpi(name, value, tone = "") {
  return h("div", { class: `kpi ${tone}` }, h("div", { class: "value" }, value), h("div", { class: "name" }, name));
}

function qualityNote(r) {
  const notes = [];
  if (!r.privacy.enabled) notes.push("Sanitizing was turned off for this log, so the package contains original values.");
  if (r.quality.unparsed_lines) notes.push(`${fmt.num(r.quality.unparsed_lines)} lines at the start had no Replicate prefix.`);
  if (r.quality.decode_errors) notes.push(`${fmt.num(r.quality.decode_errors)} lines had invalid characters and were repaired.`);
  if (!notes.length) return null;
  return h("div", { class: `callout ${r.privacy.enabled ? "" : "warn"}`, style: "margin-bottom:16px" }, notes.join(" "));
}
