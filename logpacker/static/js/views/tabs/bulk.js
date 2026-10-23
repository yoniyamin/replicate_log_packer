// Bulk apply: insights, close-reason mix, filterable batch list, per-table operations, one-by-one episodes.
import { fmt, h, mount } from "../../dom.js";
import { bars, insights, paged, section, seconds, stat, stats, table } from "../../components.js";

export function bulkTab(r) {
  const a = r.apply, b = a.batches;
  return h("div", { class: "stack" },
    insights(a.insights),
    stats(stat("Batches", fmt.num(b.count)), stat("Changes", fmt.num(b.size_stats.total_changes || 0)),
      stat("Tables", fmt.num(a.table_count)), stat("One-by-one episodes", fmt.num(a.one_by_one.length), a.one_by_one.length ? "error" : ""),
      stat("No-PK events", fmt.num(a.no_pk_events), a.no_pk_events ? "warn" : ""), stat("No-bulk events", fmt.num(a.no_bulk_events))),
    b.count ? section("Close reason mix", h("div", { class: "stack" },
      bars(Object.entries(b.closure_reasons).map(([k, v]) => ({ label: k, value: v }))),
      reasonLegend(a.close_reason_info, b.closure_reasons))) : null,
    b.count ? section("Batches", batchList(b, a.close_reason_info), { note: b.list_truncated ? `first ${fmt.num(b.list.length)} shown` : null }) : null,
    a.tables.length ? section(`Tables (${fmt.num(a.table_count)})`, tablesView(a)) : null,
    a.one_by_one.length ? section(`One-by-one episodes (${a.one_by_one.length})`, table([
      { label: "Table", key: "table" }, { label: "Start", render: (e) => `L${e.start_line} · ${fmt.ts(e.start_ts)}` },
      { label: "End", render: (e) => (e.end_line ? `L${e.end_line} · ${fmt.ts(e.end_ts)}` : "still running at end of log") },
      { label: "Duration", render: (e) => seconds(e.seconds), num: true },
      { label: "Failed executions", key: "failed_executions", num: true }], a.one_by_one)) : null,
  );
}

function reasonLegend(info, seen) {
  const rows = Object.keys(seen).map((k) => ({ code: k, ...info[k] }));
  return h("details", { class: "small" }, h("summary", {}, "What do these close reasons mean?"),
    table([{ label: "Code", key: "code" }, { label: "Meaning", render: (x) => x.meaning || x.label },
      { label: "Setting", render: (x) => x.setting || "—" }], rows));
}

function batchList(b, info) {
  const host = h("div");
  const counts = b.closure_reasons;
  let filter = "All";
  const columns = [
    { label: "Line", key: "line", num: true }, { label: "Time", render: (x) => fmt.ts(x.ts) },
    { label: "Changes", key: "changes", num: true }, { label: "Statements", key: "statements", num: true },
    { label: "Apply", render: (x) => seconds(x.duration), num: true },
    { label: "Rate/s", render: (x) => (x.duration ? fmt.num(Math.round(x.changes / x.duration)) : "—"), num: true },
    { label: "Reason", render: (x) => h("span", { class: "badge info", title: info[x.reason]?.meaning || "" }, x.reason) },
    { label: "Tables", render: (x) => x.tables.join(", "), mono: true }];
  const draw = () => {
    const rows = filter === "All" ? b.list : b.list.filter((x) => x.reason === filter);
    mount(host,
      h("div", { class: "chip-filter" }, ["All", ...Object.keys(counts)].map((k) => h("button", {
        type: "button", "aria-pressed": String(filter === k), onclick: () => { filter = k; draw(); },
      }, k === "All" ? `All ${fmt.num(b.list.length)}` : `${k} ${fmt.num(counts[k])}`))),
      paged((page) => table(columns, page), rows));
  };
  draw();
  return host;
}

function tableOpsBody(row, apply) {
  const ops = (apply.operations.list || []).filter((o) => o.table === row.table);
  return h("div", { class: "stack" },
    stats(...["insert", "update", "delete", "merge"].map((k) => stat(k.toUpperCase(), fmt.num(row[k]))),
      stat("Avg apply", seconds(row.avg_apply_time)), stat("Max apply", seconds(row.max_apply_time))),
    ops.length
      ? paged((page) => table([
        { label: "Line", key: "line", num: true }, { label: "Time", render: (o) => fmt.ts(o.ts) },
        { label: "Op", key: "op" }, { label: "Rows", key: "rows", num: true },
        { label: "Apply", render: (o) => seconds(o.seconds), num: true },
        { label: "Rows/s", render: (o) => (o.seconds ? fmt.num(Math.round(o.rows / o.seconds)) : "—"), num: true }], page), ops)
      : h("p", { class: "muted small" }, "No individual apply operations recorded for this table."));
}

function openTableOpsModal(row, apply) {
  const backdrop = h("div", { class: "modal-backdrop", onclick: (e) => { if (e.target === backdrop) close(); } });
  const dialog = h("div", { class: "modal modal-wide", role: "dialog", "aria-label": `Apply operations — ${row.table}` });

  function close() {
    backdrop.classList.add("closing");
    setTimeout(() => backdrop.remove(), 200);
    document.removeEventListener("keydown", onKey);
  }

  const onKey = (e) => { if (e.key === "Escape") close(); };
  document.addEventListener("keydown", onKey);

  mount(dialog,
    h("div", { class: "modal-header" },
      h("h2", { class: "mono small", style: "margin:0;font-size:1rem;font-weight:600" }, row.table),
      h("button", { class: "icon-btn", type: "button", "aria-label": "Close", onclick: close }, "✕")),
    h("div", { class: "modal-body" }, tableOpsBody(row, apply)));

  backdrop.append(dialog);
  document.body.append(backdrop);
  requestAnimationFrame(() => backdrop.classList.add("open"));
}

function tablesView(a) {
  const pick = (row) => openTableOpsModal(row, a);
  const ops = a.operations;
  return h("div", { class: "stack" },
    ops.count ? stats(stat("Apply operations", fmt.num(ops.count)), stat("Single-row ops", `${ops.single_record_pct}%`,
      ops.single_record_pct > 40 ? "error" : ops.single_record_pct > 20 ? "warn" : ""),
      stat("Avg rows/op", fmt.num(ops.avg_rows)), stat("Max rows/op", fmt.num(ops.max_rows))) : null,
    table([{ label: "Table", key: "table" }, { label: "Inserts", key: "insert", num: true }, { label: "Updates", key: "update", num: true },
      { label: "Deletes", key: "delete", num: true }, { label: "Merges", key: "merge", num: true },
      { label: "Apply time", render: (t) => seconds(t.apply_time), num: true }, { label: "1-by-1", key: "one_by_one", num: true }],
    a.tables.slice(0, 100), { onRow: pick }),
    h("p", { class: "muted small" }, "Click a table to view its apply operations."));
}
