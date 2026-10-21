// Performance cockpit: recommendations, bottleneck, latency profile/chart, spikes, plateaus,
// then the apply/health sections from cockpit_apply.js.
import { lineChart } from "../../chart.js";
import { fmt, h } from "../../dom.js";
import { insights, section, stat, stats, table } from "../../components.js";
import { applySections } from "./cockpit_apply.js";

const SERIES = [
  { key: "source", label: "Source", color: "var(--chart-source)" },
  { key: "handling", label: "Handling", color: "var(--chart-handling)" },
  { key: "target", label: "Target (total)", color: "var(--chart-target)" },
];
const VERDICT = {
  source: "Source capture dominant", target_apply: "Target apply dominant", balanced: "Balanced", unknown: "Not enough data",
};

export function cockpitTab(r) {
  const c = r.cockpit, lat = r.latency;
  return h("div", { class: "stack" },
    section(`Recommendations (${c.recommendations.length})`,
      insights(c.recommendations, h("p", { class: "muted small" }, "No rule fired. Nothing stands out in performance.")),
      { note: `Top contributors: ${c.top_contributors.join(", ")}` }),
    config(c.config),
    lat.samples ? latencySections(lat, c.error_correlation) : h("div", { class: "callout" },
      "No PERFORMANCE samples in this log. Set the PERFORMANCE logger to TRACE to record latency every 30 seconds."),
    ...applySections(r),
  );
}

function config(cfg) {
  const items = [["Bulk timeout (ms)", cfg.bulk_timeout_ms], ["Min timeout (ms)", cfg.bulk_timeout_min_ms],
    ["Max file size (MB)", cfg.bulk_max_file_size_mb], ["Parallel threads", cfg.parallel_apply_threads],
    ["Apply mode", cfg.apply_mode], ["Source", cfg.source], ["Target", cfg.target]].filter(([, v]) => v != null);
  return section("Task configuration", h("dl", { class: "props" },
    items.map(([k, v]) => h("div", {}, h("dt", {}, k), h("dd", {}, String(v))))), { open: false });
}

function latencySections(lat, corr) {
  const bn = lat.bottleneck;
  const srcPct = bn.source_pct ?? 0;
  return h("div", { class: "stack" },
    section("Latency bottleneck", h("div", { class: "stack" },
      h("div", { class: "split-bar", role: "img", "aria-label": `Source ${srcPct}%, apply ${100 - srcPct}%` },
        h("span", { style: `width:${srcPct}%;background:var(--chart-source)` }, srcPct >= 12 ? `Source ${srcPct}%` : ""),
        h("span", { style: `width:${100 - srcPct}%;background:var(--chart-handling)` }, 100 - srcPct >= 12 ? `Apply ${(100 - srcPct).toFixed(1)}%` : "")),
      h("p", { class: "small", style: "margin:0" }, h("b", {}, VERDICT[bn.primary] || bn.primary),
        h("span", { class: "muted" }, " · Target latency = source capture + handling (apply). Share is source vs handling average.")),
      bn.periods.length > 1 ? table([
        { label: "From", render: (p) => fmt.ts(p.start_ts) }, { label: "To", render: (p) => fmt.ts(p.end_ts) },
        { label: "Verdict", render: (p) => VERDICT[p.bottleneck] || p.bottleneck },
        { label: "Source avg", key: "source_avg", num: true }, { label: "Handling avg", key: "handling_avg", num: true },
        { label: "Target avg", key: "target_avg", num: true }], bn.periods) : null)),
    section("Latency profile", h("div", { class: "stack" },
      stats(...SERIES.map((s) => stat(s.label, `${lat[s.key].avg}s`, "",
        `p50 ${lat[s.key].p50} · p95 ${lat[s.key].p95} · max ${lat[s.key].max}`))),
      h("div", { class: "legend" }, SERIES.map((s) => h("span", {}, h("i", { style: `background:${s.color}` }), s.label))),
      lineChart(lat.series, SERIES),
      h("p", { class: "muted small" }, `${fmt.num(lat.samples)} samples · peak ${lat.peak_target.seconds}s at L${lat.peak_target.line}`))),
    section(`Latency spikes (${lat.spikes.count})`, lat.spikes.count ? table([
      { label: "Line", key: "line", num: true }, { label: "Time", render: (x) => fmt.ts(x.ts) },
      { label: "Target", render: (x) => `${x.value}s`, num: true }, { label: "Baseline", render: (x) => `${x.baseline}s`, num: true },
      { label: "×", key: "multiplier", num: true }, { label: "Driver", key: "driver" }], lat.spikes.top)
      : h("p", { class: "muted small" }, "✓ No sudden jumps above 2.5× the recent baseline."), { open: lat.spikes.count > 0 }),
    section(`Sustained high latency (${lat.plateaus.count})`, lat.plateaus.count ? table([
      { label: "From", render: (x) => `L${x.start_line} · ${fmt.ts(x.start_ts)}` }, { label: "To", render: (x) => `L${x.end_line} · ${fmt.ts(x.end_ts)}` },
      { label: "Samples", key: "points", num: true }, { label: "Avg target", render: (x) => `${x.avg}s`, num: true }], lat.plateaus.top)
      : h("p", { class: "muted small" }, "✓ No plateaus above 1.3× the average."), { open: lat.plateaus.count > 0 }),
    corr.available ? section(`Errors during high latency (${corr.errors_in_windows})`, h("div", { class: "stack" },
      h("p", { class: "small", style: "margin:0" },
        `${corr.windows} windows where target latency was above its 75th percentile (${corr.threshold_seconds}s); `,
        `${corr.errors_in_windows} of ${corr.errors_total} errors/warnings fell inside them.`),
      corr.samples.length ? table([{ label: "Line", key: "line", num: true }, { label: "Pattern", key: "group" },
        { label: "Message", key: "text", mono: true }], corr.samples) : null), { open: corr.errors_in_windows > 0 }) : null,
  );
}
