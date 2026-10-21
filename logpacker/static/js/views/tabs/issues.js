// Error and warning patterns, each expandable to show its first excerpt.
import { fmt, h, withTokens } from "../../dom.js";

export function issuesTab(r) {
  const groups = r.issues.groups;
  if (!groups.length) {
    return h("div", { class: "empty" }, h("div", { class: "big" }, "✓"), h("p", {}, "No errors or warnings in this log."));
  }
  const strip = timeline(r.issues.timeline, r.stats.lines);
  return h("div", {},
    h("p", { class: "muted small" },
      `${fmt.num(r.stats.errors)} errors and ${fmt.num(r.stats.warnings)} warnings grouped into ${groups.length} patterns. `,
      "Numbers, quoted values and timestamps are ignored when grouping."),
    strip.el,
    groups.slice(0, 150).map((g) => issueCard(g, strip.highlight)),
    groups.length > 150 ? h("p", { class: "muted small" }, `${groups.length - 150} smaller patterns are in findings.json.`) : null,
  );
}

/** Dots placed by line position; opening a group highlights its own dots. */
function timeline(events, totalLines) {
  const el = h("div", { class: "timeline", role: "img", "aria-label": `Timeline of ${events.length} errors and warnings` });
  for (const e of events) {
    el.append(h("i", { class: e.severity, dataset: { group: e.group }, title: `L${e.line} · ${e.group}`,
      style: `left:calc(${((e.line / Math.max(1, totalLines)) * 100).toFixed(2)}% - 2px)` }));
  }
  const highlight = (groupId) => {
    for (const dot of el.children) dot.classList.toggle("dim", groupId != null && dot.dataset.group !== groupId);
  };
  return { el, highlight };
}

function issueCard(g, highlight) {
  const objects = Object.entries(g.objects || {});
  return h("details", { class: "issue", ontoggle: (e) => highlight(e.target.open ? g.id : null) },
    h("summary", {},
      h("span", { class: `badge ${g.severity}` }, g.severity === "E" ? "ERROR" : "WARN"),
      h("span", { class: "count-pill" }, `${fmt.num(g.count)}×`),
      h("span", { class: "pattern", title: g.pattern }, g.pattern),
      h("span", { class: "muted small" }, g.component || "")),
    h("div", { class: "body stack" },
      h("div", { class: "chips" },
        h("span", { class: "chip" }, "ID", h("b", {}, g.id)),
        g.code ? h("span", { class: "chip" }, "Code", h("b", {}, g.code)) : null,
        h("span", { class: "chip" }, "Lines", h("b", {}, `${g.first_line} → ${g.last_line}`)),
        h("span", { class: "chip" }, "First", h("b", {}, fmt.ts(g.first_ts))),
        objects.length ? h("span", { class: "chip" }, "Tables", h("b", {}, objects.map(([k, v]) => `${k} (${v})`).join(", "))) : null),
      g.excerpts.map(excerpt),
      g.count > g.excerpts.length ? h("p", { class: "muted small" }, `… and ${fmt.num(g.count - g.excerpts.length)} more occurrences (lines ${g.sample_lines.join(", ")}${g.count > 10 ? ", …" : ""}).`) : null),
  );
}

export function excerpt(ex) {
  const pre = h("pre", { class: "log" });
  for (const [n, t] of ex.before) pre.append(h("span", {}, `${n}|`, withTokens(t)), "\n");
  pre.append(h("span", { class: "hl" }, `${ex.line}|`, withTokens(ex.text)));
  for (const [n, t] of ex.after) pre.append(h("span", {}, `${n}|`, withTokens(t)), "\n");
  return pre;
}
