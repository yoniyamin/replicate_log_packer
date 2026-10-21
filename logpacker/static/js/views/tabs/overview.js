// Overview: health verdict, task properties, key events, loggers, unavailable reports.
import { fmt, h } from "../../dom.js";
import { insights, pill, section, seconds, table } from "../../components.js";
import { signalsSection } from "./signals.js";

const HEALTH_TONE = { 5: "good", 4: "good", 3: "warn", 2: "error", 1: "error" };

export function overviewTab(r) {
  const m = r.metadata, s = r.stats, health = r.manifest.health;
  const duration = s.first_ts && s.last_ts ? (Date.parse(s.last_ts) - Date.parse(s.first_ts)) / 1000 : null;
  const missing = r.manifest.tabs.filter((t) => !t.available);
  const top = [...r.cockpit.recommendations.slice(0, 3),
    ...["apply", "full_load", "file_ops"].flatMap((k) => (r[k].insights || []).filter((i) => i.severity === "error"))].slice(0, 5);

  return h("div", { class: "stack" },
    h("div", { class: "card flat health" },
      h("div", { class: "score" }, `${health.score}/5`),
      h("div", {}, h("div", {}, pill(health.label, HEALTH_TONE[health.score])),
        h("div", { class: "muted small", style: "margin-top:4px" },
          health.reasons.length ? `Lowered by: ${health.reasons.join(", ")}.` : "No problems found by the parser."))),
    top.length ? section("What stands out", insights(top)) : null,
    section("Task properties", h("dl", { class: "props" },
      prop("Task", m.task), prop("Version", m.version), prop("Run mode", [m.mode, m.start_mode].filter(Boolean).join(" · ")),
      prop("Source", [m.source_endpoint, m.source_type].filter(Boolean).join(" · ")),
      prop("Target", [m.target_endpoint, m.target_type].filter(Boolean).join(" · ")),
      prop("Duration", duration != null ? seconds(duration) : null), prop("Tables seen", fmt.num(m.tables_seen)),
      prop("Errors / warnings", `${fmt.num(s.errors)} / ${fmt.num(s.warnings)}`),
      prop("Batches", fmt.num(r.apply.batches.count)), prop("OS", m.os), prop("License", m.license ? [m.license.holder, m.license.type].filter(Boolean).join(" · ") : null),
      prop("ODBC drivers", (m.odbc_drivers || []).join(", ") || null))),
    h("div", { class: "chips" },
      pill(m.full_load_completed ? "✓ Full Load completed" : "○ Full Load not completed", m.full_load_completed ? "good" : ""),
      pill(m.cdc_started ? "✓ CDC started" : "○ CDC not started", m.cdc_started ? "good" : ""),
      pill(m.log_closed ? "✓ Log closed normally" : "! Log ends abruptly (cut off or still running)", m.log_closed ? "good" : "warn"),
      m.rollover ? pill("Rolled-over log", "warn") : null,
      m.fatal ? pill(`Fatal error at L${m.fatal.line}`, "error") : null),
    signalsSection(r),
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
