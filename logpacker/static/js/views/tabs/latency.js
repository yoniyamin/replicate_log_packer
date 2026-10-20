// Latency summary table and chart from PERFORMANCE samples.
import { lineChart } from "../../chart.js";
import { fmt, h } from "../../dom.js";

const SERIES = [
  { key: "source", label: "Source", color: "var(--chart-source)" },
  { key: "handling", label: "Handling", color: "var(--chart-handling)" },
  { key: "target", label: "Target", color: "var(--chart-target)" },
];

const BOTTLENECK = {
  healthy: ["good", "Latency stays low. Nothing here points to a bottleneck."],
  source: ["warn", "Most of the delay is already present on the source side (capture or reading the source log)."],
  handling: ["warn", "A large part of the delay comes from handling (sorter / transaction processing)."],
  target: ["warn", "Most of the delay is added on the target side (applying changes)."],
};

export function latencyTab(r) {
  const lat = r.latency;
  if (!lat.samples) {
    return h("div", { class: "empty" }, h("p", {}, "No latency samples in this log."),
      h("p", { class: "small" }, "Set the PERFORMANCE logger to Trace in the task to record latency every 30 seconds."));
  }
  const [tone, text] = BOTTLENECK[lat.bottleneck] || BOTTLENECK.target;
  return h("div", { class: "stack" },
    h("div", { class: `callout ${tone}` }, h("span", {}, h("b", {}, "Rough read: "), text)),
    h("div", { class: "legend" }, SERIES.map((s) => h("span", {}, h("i", { style: `background:${s.color}` }), s.label))),
    lineChart(lat.series, SERIES),
    h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, h("th", {}, "Seconds"), ...["Average", "95th pct", "Max", "Last"].map((c) => h("th", { class: "num" }, c)))),
      h("tbody", {}, SERIES.map((s) => h("tr", {}, h("td", {}, s.label),
        ...["avg", "p95", "max", "last"].map((k) => h("td", { class: "num" }, lat[s.key][k]))))))),
    h("p", { class: "muted small" },
      `${fmt.num(lat.samples)} samples. Peak target latency ${lat.peak_target.seconds}s at line ${lat.peak_target.line} (${fmt.ts(lat.peak_target.ts)}).`),
  );
}
