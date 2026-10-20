// Step 2: what LogPacker found. Header, key numbers and one tab per finding type.
import { api } from "../api.js";
import { fmt, h, icon, mount } from "../dom.js";
import { store } from "../store.js";
import { issuesTab } from "./tabs/issues.js";
import { latencyTab } from "./tabs/latency.js";
import { signalsTab } from "./tabs/signals.js";
import { privacyTab } from "./tabs/privacy.js";
import { patternsTab } from "./tabs/patterns.js";

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
  const meta = r.metadata, stats = r.stats;
  const hidden = r.privacy.categories.reduce((a, c) => a + c.replacements, 0);
  const tabs = [
    { key: "issues", label: "Errors & warnings", count: r.issues.groups.length, render: () => issuesTab(r) },
    { key: "latency", label: "Latency", count: r.latency.samples ? null : 0, render: () => latencyTab(r) },
    { key: "signals", label: "Replicate signals", count: r.signals.signals.length, render: () => signalsTab(r) },
    { key: "patterns", label: "Repeated lines", count: r.repetition.distinct, render: () => patternsTab(r) },
    { key: "privacy", label: "What was hidden", count: hidden, render: () => privacyTab(id, r) },
  ];
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
      kpi("Lines", fmt.num(stats.lines)),
      kpi("Errors", fmt.num(stats.errors), stats.errors ? "error" : "good"),
      kpi("Warnings", fmt.num(stats.warnings), stats.warnings ? "warn" : "good"),
      kpi("Issue patterns", fmt.num(r.issues.groups.length)),
      kpi("Task runs", fmt.num(r.runs.count)),
      kpi("Values hidden", fmt.num(hidden), "good")),
    qualityNote(r),
    h("section", { class: "card" }, bar, panel),
  );
  select(r.issues.groups.length ? "issues" : r.latency.samples ? "latency" : "signals");
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
