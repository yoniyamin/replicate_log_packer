// Error and warning patterns, each expandable to show its first excerpt.
import { fmt, h, parseLogTs, withTokens } from "../../dom.js";

export function issuesTab(r) {
  const groups = r.issues.groups;
  if (!groups.length) {
    return h("div", { class: "empty" }, h("div", { class: "big" }, "✓"), h("p", {}, "No errors or warnings in this log."));
  }
  const strip = timeline(r.issues.timeline, r.stats.lines, r.stats.first_ts, r.stats.last_ts);
  return h("div", {},
    h("p", { class: "muted small" },
      `${fmt.num(r.stats.errors)} errors and ${fmt.num(r.stats.warnings)} warnings grouped into ${groups.length} patterns. `,
      "Numbers, quoted values and timestamps are ignored when grouping."),
    strip.el,
    groups.slice(0, 150).map((g) => issueCard(g, strip.highlight)),
    groups.length > 150 ? h("p", { class: "muted small" }, `${groups.length - 150} smaller patterns are in findings.json.`) : null,
  );
}

/** Time-based or line-based timeline with axis labels, legend, and rich tooltips. */
function timeline(events, totalLines, firstTs, lastTs) {
  const start = parseLogTs(firstTs);
  const end = parseLogTs(lastTs);
  const hasTime = !isNaN(start) && !isNaN(end) && end > start;

  const strip = h("div", { class: "timeline" });
  for (const e of events) {
    let left = null;
    if (hasTime && e.ts) {
      const t = parseLogTs(e.ts);
      if (!isNaN(t)) left = (((t - start) / (end - start)) * 100).toFixed(2);
    }
    if (left == null) left = ((e.line / Math.max(1, totalLines)) * 100).toFixed(2);

    const tip = (e.ts ? `${fmt.ts(e.ts)} · ` : "") + `L${e.line} · ${e.group}`;
    strip.append(h("i", {
      class: e.severity, dataset: { group: e.group }, title: tip,
      "aria-label": tip,
      style: `left:calc(${left}% - 2px)`,
    }));
  }

  const axis = h("div", { class: "timeline-axis" },
    h("span", {}, hasTime ? fmt.tsShort(firstTs) : "Start"),
    h("span", {}, hasTime ? fmt.tsShort(lastTs) : "End"));

  const legend = h("div", { class: "timeline-legend" },
    h("span", {}, h("i", { class: "swatch E" }), " Error"),
    h("span", {}, h("i", { class: "swatch W" }), " Warning"));

  const el = h("figure", { class: "timeline-block", "aria-label": `Timeline of ${events.length} errors and warnings` },
    h("div", { class: "timeline-head" },
      h("span", { class: "muted small" }, "Timeline"),
      legend),
    strip,
    axis,
    !hasTime && events.length
      ? h("figcaption", { class: "muted small" }, "Timestamps missing — markers shown by line order.")
      : null);

  const highlight = (groupId) => {
    for (const dot of strip.children) dot.classList.toggle("dim", groupId != null && dot.dataset.group !== groupId);
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
