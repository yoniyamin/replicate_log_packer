// The line patterns that make up most of the log (what condensing collapses).
import { fmt, h } from "../../dom.js";

export function patternsTab(r) {
  const rep = r.repetition;
  const total = r.stats.lines || 1;
  const top10 = rep.top.slice(0, 10).reduce((a, p) => a + p.count, 0);
  return h("div", { class: "stack" },
    h("div", { class: "callout" }, h("span", {},
      `${fmt.num(r.stats.lines)} lines follow ${fmt.num(rep.distinct)} distinct patterns. `,
      `The 10 most frequent cover ${Math.round((top10 / total) * 100)}% of the log; the packed attachment keeps their first and last occurrences and collapses the rest.`)),
    h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, h("th", {}, "ID"), h("th", { class: "num" }, "Lines"), h("th", { class: "num" }, "Share"), h("th", {}, "Component"), h("th", {}, "Pattern"))),
      h("tbody", {}, rep.top.map((p) => h("tr", {},
        h("td", { class: "mono" }, p.id),
        h("td", { class: "num" }, fmt.num(p.count)),
        h("td", { class: "num" }, `${((p.count / total) * 100).toFixed(1)}%`),
        h("td", {}, p.component, " ", h("span", { class: `badge ${p.severity}` }, p.severity)),
        h("td", { class: "mono small" }, p.pattern)))))),
  );
}
