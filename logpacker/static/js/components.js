// Reusable report building blocks: stat tiles, insight rows, tables, bars, sections.
import { fmt, h } from "./dom.js";

export function stat(name, value, tone = "", note = null) {
  return h("div", { class: `kpi ${tone}` }, h("div", { class: "value" }, value ?? "—"),
    h("div", { class: "name" }, name), note ? h("div", { class: "muted small" }, note) : null);
}

export function stats(...tiles) {
  return h("div", { class: "kpis compact" }, tiles);
}

const SEVERITY_TONE = { error: "error", warning: "warn", info: "info", high: "error", medium: "warn", low: "info" };

export function insight(item) {
  const tone = SEVERITY_TONE[item.severity || item.priority] || "info";
  return h("div", { class: `insight ${tone}` },
    h("div", { class: "insight-head" },
      h("span", { class: `badge ${tone}` }, (item.severity || item.priority || "info").toUpperCase()),
      h("b", {}, item.title), item.area ? h("span", { class: "muted small" }, item.area) : null),
    h("div", {}, item.message || item.description),
    item.recommendation ? h("div", { class: "muted small" }, "→ ", item.recommendation) : null,
    item.actions?.length ? h("ul", { class: "small" }, item.actions.map((a) => h("li", {}, a))) : null);
}

export function insights(list, empty = null) {
  if (!list?.length) return empty;
  return h("div", { class: "stack" }, list.map(insight));
}

/** columns: [{label, key|render, num}] */
export function table(columns, rows, { onRow, empty = "Nothing to show." } = {}) {
  if (!rows?.length) return h("p", { class: "muted small" }, empty);
  return h("div", { class: "table-wrap" }, h("table", {},
    h("thead", {}, h("tr", {}, columns.map((c) => h("th", { class: c.num ? "num" : null }, c.label)))),
    h("tbody", {}, rows.map((row) => h("tr", { class: onRow ? "clickable" : null, onclick: onRow ? () => onRow(row) : null },
      columns.map((c) => h("td", { class: c.num ? "num" : c.mono ? "mono small" : null },
        c.render ? c.render(row) : fmtCell(row[c.key]))))))));
}

function fmtCell(v) {
  if (typeof v === "number") return Number.isInteger(v) ? fmt.num(v) : v.toLocaleString("en-US", { maximumFractionDigits: 3 });
  if (v === true) return "✓";
  if (v === false) return "✗";
  return v ?? "—";
}

/** Horizontal share bars: items [{label, value, note}] */
export function bars(items, total = null) {
  const sum = total ?? items.reduce((a, i) => a + i.value, 0);
  return h("div", { class: "bars" }, items.map((i) => {
    const pct = sum ? (100 * i.value) / sum : 0;
    return h("div", { class: "bar-row", title: i.note || "" },
      h("span", { class: "bar-label" }, i.label),
      h("span", { class: "bar-track" }, h("span", { class: "bar-fill", style: `width:${pct.toFixed(1)}%` })),
      h("span", { class: "bar-value" }, `${fmt.num(i.value)} · ${pct.toFixed(0)}%`));
  }));
}

export function section(title, body, { note = null, open = true } = {}) {
  if (body == null || body === false) return null;
  return h("details", { class: "report-section", open },
    h("summary", {}, h("h3", {}, title), note ? h("span", { class: "muted small" }, note) : null),
    h("div", { class: "report-body" }, body));
}

export function pill(text, tone = "") {
  return h("span", { class: `pill ${tone}` }, text);
}

export function seconds(s) {
  if (s == null) return "—";
  if (s < 60) return `${s.toFixed(s < 10 ? 2 : 1)}s`;
  if (s < 3600) return `${(s / 60).toFixed(1)}m`;
  return `${(s / 3600).toFixed(1)}h`;
}

/** Simple pager for long tables. */
export function paged(render, rows, pageSize = 15) {
  const host = h("div");
  let page = 0;
  const draw = () => {
    const pages = Math.max(1, Math.ceil(rows.length / pageSize));
    host.replaceChildren(render(rows.slice(page * pageSize, (page + 1) * pageSize)),
      pages > 1 ? h("div", { class: "row pager" },
        h("button", { class: "btn small", type: "button", disabled: page === 0, onclick: () => { page--; draw(); } }, "‹ Prev"),
        h("span", { class: "muted small" }, `Page ${page + 1} of ${pages} · ${fmt.num(rows.length)} rows`),
        h("button", { class: "btn small", type: "button", disabled: page >= pages - 1, onclick: () => { page++; draw(); } }, "Next ›")) : null);
  };
  draw();
  return host;
}
