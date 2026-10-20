// Error and warning patterns, each expandable to show its first excerpt.
import { fmt, h, withTokens } from "../../dom.js";

export function issuesTab(r) {
  const groups = r.issues.groups;
  if (!groups.length) {
    return h("div", { class: "empty" }, h("div", { class: "big" }, "✓"), h("p", {}, "No errors or warnings in this log."));
  }
  return h("div", {},
    h("p", { class: "muted small" },
      `${fmt.num(r.stats.errors)} errors and ${fmt.num(r.stats.warnings)} warnings grouped into ${groups.length} patterns. `,
      "Numbers, quoted values and timestamps are ignored when grouping."),
    groups.slice(0, 150).map(issueCard),
    groups.length > 150 ? h("p", { class: "muted small" }, `${groups.length - 150} smaller patterns are in findings.json.`) : null,
  );
}

function issueCard(g) {
  const objects = Object.entries(g.objects || {});
  return h("details", { class: "issue" },
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
      g.excerpts[0] ? excerpt(g.excerpts[0]) : null),
  );
}

export function excerpt(ex) {
  const pre = h("pre", { class: "log" });
  for (const [n, t] of ex.before) pre.append(h("span", {}, `${n}|`, withTokens(t)), "\n");
  pre.append(h("span", { class: "hl" }, `${ex.line}|`, withTokens(ex.text)));
  for (const [n, t] of ex.after) pre.append(h("span", {}, `${n}|`, withTokens(t)), "\n");
  return pre;
}
