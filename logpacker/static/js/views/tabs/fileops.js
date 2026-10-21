// File operations: compress/upload pairs for staging targets, with throughput flags.
import { fmt, h } from "../../dom.js";
import { insights, paged, seconds, stat, stats, table } from "../../components.js";

export function fileOpsTab(r) {
  const f = r.file_ops;
  return h("div", { class: "stack" },
    insights(f.insights),
    stats(stat("Files", fmt.num(f.count)), stat("Data", fmt.bytes(f.total_bytes)),
      stat("Avg upload", seconds(f.upload_seconds.avg)), stat("Avg compress", seconds(f.compress_seconds.avg)),
      stat("Throughput", f.avg_throughput_kbps != null ? `${fmt.num(f.avg_throughput_kbps)} KB/s` : "—"),
      stat("Size range", `${fmt.bytes(f.size.min)} – ${fmt.bytes(f.size.max)}`)),
    paged((rows) => table([
      { label: "File", key: "file", mono: true }, { label: "Tables", render: (x) => x.tables.join(", "), mono: true },
      { label: "Size", render: (x) => fmt.bytes(x.size), num: true },
      { label: "Compress", render: (x) => seconds(x.compress_seconds), num: true },
      { label: "Upload", render: (x) => seconds(x.upload_seconds), num: true },
      { label: "Total", render: (x) => seconds(x.total_seconds), num: true },
      { label: "KB/s", render: (x) => h("span", { class: x.throughput_flag === "slow" ? "badge error" : x.throughput_flag ? "badge warn" : "" },
        x.throughput_kbps != null ? fmt.num(x.throughput_kbps) : "—"), num: true },
      { label: "Completed", render: (x) => fmt.ts(x.completed_ts) }], rows), f.files),
  );
}
