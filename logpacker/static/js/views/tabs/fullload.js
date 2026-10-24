// Full Load: summary, insights, per-table rows with expandable segments and reloads.
import { fmt, h } from "../../dom.js";
import { insights, pill, section, seconds, stat, stats, table } from "../../components.js";

const STATUS_TONE = { complete: "good", loading: "", initializing: "", reloading: "warn", error: "error" };

export function fullLoadTab(r) {
  const fl = r.full_load, s = fl.summary;
  return h("div", { class: "stack" },
    h("div", { class: "row" }, pill(s.completed ? "✓ Full Load completed" : "○ Full Load not completed", s.completed ? "good" : "warn"),
      s.mode ? h("span", { class: "muted small" }, `Mode: ${s.mode}`) : null),
    stats(stat("Tables", fmt.num(s.tables_total)), stat("Loaded", fmt.num(s.tables_loaded), "good"),
      stat("Loading", fmt.num(s.tables_loading)), stat("Failed", fmt.num(s.tables_failed), s.tables_failed ? "error" : ""),
      stat("Reloads", fmt.num(s.reload_events), s.reload_events ? "error" : ""), stat("Rows loaded", fmt.num(s.rows_received)),
      stat("Volume", fmt.bytes(s.volume)), stat("Max segments", fmt.num(s.max_parallel_segments)),
      stat("Duration", seconds(s.duration_seconds))),
    insights(fl.insights),
    section(`Tables (${fmt.num(fl.tables.length)})`, h("div", { class: "stack" }, fl.tables.slice(0, 200).map(tableCard)),
      { note: "longest first" }),
    fl.reloads.length ? section(`Reloads (${fl.reloads.length})`, table([
      { label: "Line", key: "line", num: true }, { label: "Table", key: "table" }, { label: "Segment", key: "segment", num: true },
      { label: "Segment ran", render: (x) => seconds(x.segment_seconds), num: true },
      { label: "Cause", render: (x) => x.cause ? h("span", { title: x.cause }, `L${x.cause_line} · ${x.cause.slice(0, 80)}`) : "—", mono: true },
      { label: "Executed", render: (x) => x.executed_line ? `L${x.executed_line} (+${seconds(x.wait_seconds)})` : "not yet", mono: true }],
      fl.reloads), { note: "“Executed” is the SORTER’s “Reload for table … is requested”, after the running segments finished" }) : null,
  );
}

function tableCard(t) {
  return h("details", { class: "issue" },
    h("summary", {},
      pill(t.status, STATUS_TONE[t.status]), h("span", { class: "count-pill" }, `${t.segments_complete}/${t.segment_count}`),
      h("span", { class: "pattern" }, t.table),
      h("span", { class: "muted small" }, `${fmt.num(t.rows_received)} rows · ${seconds(t.duration_seconds)}`,
        t.reload_count ? ` · ${t.reload_count} reload(s)` : "")),
    h("div", { class: "body stack" },
      stats(stat("Rows sent", fmt.num(t.rows_sent)), stat("Rows received", fmt.num(t.rows_received)),
        stat("Volume", fmt.bytes(t.volume)), stat("Unload", seconds(t.unload_seconds)), stat("Max gap", seconds(t.max_gap_seconds)),
        stat("Attempts", fmt.num(t.attempts), t.attempts > 1 ? "error" : ""),
        t.reload_loop ? stat("Re-loaded", fmt.bytes(t.reload_loop.redone_volume), "error", "thrown away by reloads") : null),
      segments(t.segments),
      t.prior_attempt_segments.length ? h("div", {}, h("h4", {}, "Earlier attempts"), segments(t.prior_attempt_segments)) : null));
}

function segments(rows) {
  return table([
    { label: "Seg", render: (s) => `#${s.segment}/${s.of}` }, { label: "Subtask", key: "subtask" },
    { label: "Status", render: (s) => pill(s.status, STATUS_TONE[s.status]) },
    { label: "Sent", key: "rows_sent", num: true }, { label: "Received", key: "rows_received", num: true },
    { label: "Unload", render: (s) => seconds(s.unload_seconds), num: true },
    { label: "Gap", render: (s) => seconds(s.gap_seconds), num: true }, { label: "Total", render: (s) => seconds(s.total_seconds), num: true },
    { label: "Split predicate", render: (s) => s.predicate || s.error || "", mono: true }], rows);
}
