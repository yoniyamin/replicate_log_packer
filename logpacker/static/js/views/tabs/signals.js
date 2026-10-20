// Known Replicate behaviours, task runs and settings found in the log.
import { fmt, h } from "../../dom.js";

const AREAS = {
  apply: "Target apply", connectivity: "Connectivity", resources: "Resources", full_load: "Full Load",
  source: "Source", target_files: "Target files", data: "Data",
};

export function signalsTab(r) {
  const signals = r.signals.signals;
  const settings = Object.values(r.metadata.settings || {});
  return h("div", { class: "stack" },
    signals.length
      ? h("div", { class: "table-wrap" }, h("table", {},
          h("thead", {}, h("tr", {}, h("th", {}, "Signal"), h("th", {}, "Area"), h("th", { class: "num" }, "Count"), h("th", {}, "Most common"))),
          h("tbody", {}, signals.map((s) => h("tr", {},
            h("td", {}, h("b", {}, s.label), h("div", { class: "muted small" }, s.hint)),
            h("td", {}, AREAS[s.area] || s.area),
            h("td", { class: "num" }, fmt.num(s.count)),
            h("td", { class: "small mono" }, Object.entries(s.values).slice(0, 3).map(([k, v]) => h("div", {}, `${k} ×${v}`))))))))
      : h("div", { class: "empty" }, "No known Replicate behaviours detected."),
    r.runs.count > 1 ? runsTable(r.runs.runs) : null,
    settings.length ? h("div", {}, h("h3", {}, "Task settings seen in the log"),
      h("div", { class: "chips" }, settings.map((s) => h("span", { class: "chip" }, s.label, h("b", {}, s.value))))) : null,
  );
}

function runsTable(runs) {
  return h("div", {}, h("h3", {}, `The task ran ${runs.length} times in this log`),
    h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, ["Run", "Started", "Line", "Stopped", "Reason"].map((c) => h("th", {}, c)))),
      h("tbody", {}, runs.slice(0, 30).map((run) => h("tr", {},
        h("td", {}, run.run), h("td", {}, fmt.ts(run.start_ts)), h("td", {}, run.start_line),
        h("td", {}, fmt.ts(run.end_ts)), h("td", { class: "small" }, run.stop_reason || "")))))));
}
