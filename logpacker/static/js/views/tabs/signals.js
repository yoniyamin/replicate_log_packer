// Known Replicate behaviours detected in the log (shown inside Overview).
import { fmt, h } from "../../dom.js";
import { section, table } from "../../components.js";

const AREAS = {
  apply: "Target apply", connectivity: "Connectivity", resources: "Resources", full_load: "Full Load",
  source: "Source", target_files: "Target files", data: "Data",
};

export function signalsSection(r) {
  const signals = r.signals.signals;
  return section(`Replicate behaviours (${signals.length})`, table([
    { label: "Signal", render: (s) => h("div", {}, h("b", {}, s.label), h("div", { class: "muted small" }, s.hint)) },
    { label: "Area", render: (s) => AREAS[s.area] || s.area },
    { label: "Count", render: (s) => fmt.num(s.count), num: true },
    { label: "First line", key: "first_line", num: true },
    { label: "Most common", render: (s) => Object.entries(s.values).slice(0, 3).map(([k, v]) => h("div", {}, `${k} ×${v}`)), mono: true },
  ], signals, { empty: "No known Replicate behaviours detected." }), { open: signals.length > 0 });
}
