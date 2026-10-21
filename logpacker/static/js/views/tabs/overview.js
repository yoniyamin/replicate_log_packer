// Overview: task properties, lifecycle pills, signals, key events, loggers, runs, unavailable reports.
// Health verdict and top insights are now in the Review verdict band — not duplicated here.
import { fmt, h } from "../../dom.js";
import { insights, pill, section, seconds, table } from "../../components.js";
import { signalsSection } from "./signals.js";
import { buildHighlightInsights } from "../highlightInsights.js";

export function overviewTab(r) {
  const m = r.metadata, s = r.stats;
  const duration = s.first_ts && s.last_ts ? (Date.parse(s.last_ts) - Date.parse(s.first_ts)) / 1000 : null;
  const missing = r.manifest.tabs.filter((t) => !t.available);

  // Show "All insights" only when there are more than the 3 shown in the verdict strip
  const allInsights = buildHighlightInsights(r, 10);
  const extra = allInsights.length > 3 ? allInsights : [];

  return h("div", { class: "stack" },
    section("Task properties", h("dl", { class: "props" },
      prop("Task", m.task), prop("Version", m.version), prop("Run mode", [m.mode, m.start_mode].filter(Boolean).join(" · ")),
      prop("Source", [m.source_endpoint, m.source_type].filter(Boolean).join(" · ")),
      prop("Target", [m.target_endpoint, m.target_type].filter(Boolean).join(" · ")),
      prop("Duration", duration != null ? seconds(duration) : null), prop("Tables seen", fmt.num(m.tables_seen)),
      prop("Batches", fmt.num(r.apply.batches.count)), prop("OS", m.os), prop("License", m.license ? [m.license.holder, m.license.type].filter(Boolean).join(" · ") : null),
      prop("ODBC drivers", (m.odbc_drivers || []).join(", ") || null))),
    h("div", { class: "chips" },
      pill(m.full_load_completed ? "✓ Full Load completed" : "○ Full Load not completed", m.full_load_completed ? "good" : ""),
      pill(m.cdc_started ? "✓ CDC started" : "○ CDC not started", m.cdc_started ? "good" : ""),
      pill(m.log_closed ? "✓ Log closed normally" : "! Log ends abruptly (cut off or still running)", m.log_closed ? "good" : "warn"),
      m.rollover ? pill("Rolled-over log", "warn") : null,
      m.fatal ? pill(`Fatal error at L${m.fatal.line}`, "error") : null),
    signalsSection(r),
    extra.length > 3 ? section("All insights", insights(extra), { open: false }) : null,
    section("Key events", table([{ label: "Line", key: "line", num: true }, { label: "Time", render: (e) => fmt.ts(e.ts) }, { label: "Event", key: "event" }],
      m.key_events, { empty: "No key task events found." }), { open: m.key_events.length > 0 }),
    section("Logging levels", m.loggers.length
      ? table([{ label: "Logger", key: "logger" }, { label: "From", key: "from" }, { label: "To", key: "to" }, { label: "Line", key: "line", num: true }], m.loggers)
      : h("p", { class: "muted small" }, "No logger changes. Raise TARGET_APPLY and PERFORMANCE to TRACE for richer reports."),
      { note: m.verbose_loggers.length ? `TRACE/VERBOSE: ${m.verbose_loggers.join(", ")}` : null, open: false }),
    r.runs.count > 1 ? section(`Task runs (${r.runs.count})`, table([
      { label: "Run", key: "run", num: true }, { label: "Started", render: (x) => fmt.ts(x.start_ts) },
      { label: "Line", key: "start_line", num: true }, { label: "Stopped", render: (x) => fmt.ts(x.end_ts) },
      { label: "Reason", key: "stop_reason" }], r.runs.runs.slice(0, 50))) : null,
    missing.length ? section("Unavailable reports", h("ul", { class: "small" },
      missing.map((t) => h("li", {}, h("b", {}, t.label), ": ", t.reason))), { open: false }) : null,
  );
}

function prop(name, value) {
  return value ? h("div", {}, h("dt", {}, name), h("dd", {}, value)) : null;
}
