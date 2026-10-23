// Cockpit sections for batches, pain tables, MERGE, pipeline/source health and Oracle redo.
import { fmt, h } from "../../dom.js";
import { bars, insights, pill, section, seconds, stat, stats, table } from "../../components.js";

const HEALTH_TONE = { healthy: "good", warning: "warn", critical: "error" };

export function applySections(r) {
  return [batchBehavior(r.apply), painTables(r.apply), merge(r.apply.merge), pipeline(r.health), oracle(r.oracle)];
}

function batchBehavior(apply) {
  const b = apply.batches;
  if (!b.count) return null;
  const size = b.size_stats, dur = b.duration_stats;
  const reasons = Object.entries(b.closure_reasons).map(([k, v]) => ({
    label: `${k} · ${apply.close_reason_info[k]?.label || k}`, value: v, note: apply.close_reason_info[k]?.meaning }));
  return section("Batch behaviour", h("div", { class: "stack" },
    stats(stat("Batches", fmt.num(b.count)), stat("Avg changes", fmt.num(Math.round(size.avg || 0))),
      stat("Avg apply", seconds(dur.avg)), stat("p95 apply", seconds(dur.p95)),
      stat("Single-record", `${size.single_record_pct ?? 0}%`, (size.single_record_pct ?? 0) > 20 ? "warn" : ""),
      stat("Median rate", b.rps.median_rps != null ? `${fmt.num(b.rps.median_rps)}/s` : "—")),
    h("h4", {}, "Why batches closed"), bars(reasons),
    b.rps.buckets.length ? table([
      { label: "Changes/s", render: (x) => `${x.bucket} (${x.range})` }, { label: "Batches", key: "batches", num: true },
      { label: "Changes", key: "changes", num: true }, { label: "Avg batch", key: "avg_batch_size", num: true },
      { label: "Share", render: (x) => `${x.share}%`, num: true }], b.rps.buckets) : null,
    insights(b.issues)), { open: false });
}

function painTables(apply) {
  if (!apply.pain_tables.length) return null;
  return section("Pain tables", table([
    { label: "Table", key: "table" }, { label: "Pain", key: "pain", num: true },
    { label: "Apply time", render: (t) => seconds(t.apply_time), num: true },
    { label: "Ops", key: "total", num: true }, { label: "1-by-1", key: "one_by_one", num: true },
    { label: "Errors", key: "errors", num: true },
    { label: "PK", render: (t) => (t.has_pk === false ? "✗" : t.has_pk ? "✓" : "?") }], apply.pain_tables),
  { note: "pain = apply seconds + 10 × errors + 5 × one-by-one", open: false });
}

function merge(m) {
  if (!m.enabled) return null;
  return section("MERGE vs standard apply", h("div", { class: "stack" },
    stats(stat("MERGE share", `${m.merge_pct}%`), stat("MERGE ops", fmt.num(m.merge_ops)), stat("Standard ops", fmt.num(m.standard_ops)),
      stat("Tables using MERGE", fmt.num(m.tables_using_merge))),
    table([{ label: "Table", key: "table" }, { label: "MERGE ops", key: "merge", num: true }], m.top_tables)), { open: false });
}

function pipeline(health) {
  const p = health.pipeline, s = health.source;
  return section("CDC pipeline & source health", h("div", { class: "stack" },
    h("div", { class: "row" }, "Pipeline ", pill(p.health, HEALTH_TONE[p.health]), " Source ", pill(s.health, HEALTH_TONE[s.health])),
    stats(stat("Sorter memory warnings", fmt.num(p.memory_warnings), p.memory_warnings ? "warn" : ""),
      stat("Overflow events", fmt.num(p.overflow_events), p.overflow_events ? "error" : ""),
      stat("Target disconnects", fmt.num(p.disconnections), p.disconnections ? "warn" : ""),
      stat("Source reconnects", fmt.num(s.reconnects), s.reconnects ? "warn" : ""),
      stat("Source errors", fmt.num(s.errors), s.errors ? "warn" : "")),
    s.hints.length ? h("ul", { class: "small" }, s.hints.map((x) => h("li", {}, x))) : null,
    p.memory_warnings ? h("p", { class: "muted small" }, "Sorter memory pressure: consider larger stream_buffer_size or more memory for the task.") : null),
  { open: false });
}

function oracle(o) {
  if (!o.available) return null;
  const reads = ["online", "archived"].filter((k) => o.reads[k].count);
  return section("Oracle redo reading", h("div", { class: "stack" },
    stats(...reads.map((k) => stat(`${k} redo reads`, fmt.num(o.reads[k].count), "",
      `avg ${o.reads[k].ms.avg} ms · p95 ${o.reads[k].ms.p95} · max ${o.reads[k].ms.max}`)),
      stat(`Reads > ${o.floor_ms} ms`, fmt.num(o.slow_reads_over_floor), o.slow_reads_over_floor ? "warn" : "")),
    o.variance_groups.length ? table([
      { label: "Kind", key: "kind" }, { label: "Bytes", key: "bytes", num: true }, { label: "Thread", key: "thread" },
      { label: "Reads", key: "count", num: true }, { label: "Min ms", key: "min_ms", num: true },
      { label: "Max ms", key: "max_ms", num: true }, { label: "×", key: "multiplier", num: true },
      { label: "Slowest lines", render: (g) => g.lines.join(", "), mono: true }], o.variance_groups.slice(0, 8)) : null,
    o.sessions.count ? h("p", { class: "small" }, `${fmt.num(o.sessions.count)} redo log sessions; longest ${seconds(o.sessions.seconds.max)}, p95 ${seconds(o.sessions.seconds.p95)}.`) : null),
  { open: false });
}
