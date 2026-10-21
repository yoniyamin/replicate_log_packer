"""Prompt sections for performance: latency, rule-based findings, target apply, Full Load."""

from __future__ import annotations

from logpacker.pack.evidence import Section, _table
from logpacker.pack.presets import PromptDetail


def latency(report: dict, _: PromptDetail, focus: str) -> Section | None:
    lat = report["latency"]
    if not lat.get("samples"):
        return None
    rows = [[name.title(), lat[name]["avg"], lat[name]["p95"], lat[name]["max"], lat[name]["last"]]
            for name in ("source", "handling", "target")]
    peak, bn = lat["peak_target"], lat["bottleneck"]
    spikes, plateaus = lat["spikes"], lat["plateaus"]
    lines = [f"Peak target latency {peak['seconds']}s at line {peak['line']} ({peak['ts']})."]
    if bn.get("source_pct") is not None:
        lines.append(f"Bottleneck: **{bn['primary']}** (source capture {bn['source_pct']}% vs "
                     f"handling/apply {bn['apply_pct']}% of source+handling latency).")
    if spikes["count"]:
        top = "; ".join(f"L{s['line']} {s['value']}s ({s['multiplier']}×, {s['driver']})" for s in spikes["top"][:3])
        lines.append(f"{spikes['count']} spikes above 2.5× the 5-sample baseline. Largest: {top}.")
    if plateaus["count"]:
        top = "; ".join(f"L{p['start_line']}–L{p['end_line']} avg {p['avg']}s" for p in plateaus["top"][:3])
        lines.append(f"{plateaus['count']} sustained high-latency plateaus: {top}.")
    text = (f"## Latency (computed from {lat['samples']:,} PERFORMANCE samples, seconds)\n\n"
            + _table(["Latency", "Avg", "P50", "P95", "Max", "Last"],
                     [[r[0], r[1], lat[r[0].lower()]["p50"], *r[2:]] for r in rows])
            + "\n\n" + "\n".join(f"- {x}" for x in lines))
    return Section("latency", text, 2 if focus == "performance" else 3)


def recommendations(report: dict, detail: PromptDetail, focus: str) -> Section | None:
    recs = report["cockpit"]["recommendations"]
    insights = [i for key in ("apply", "full_load", "file_ops") for i in report[key].get("insights", [])
                if i["severity"] in ("error", "warning")]
    if not recs and not insights:
        return None
    parts = ["## Rule-based findings (computed — validate and extend, do not ignore)"]
    parts += [f"- [{r['priority'].upper()}] **{r['title']}** ({r['area']}): {r['description']}" for r in recs[:8]]
    parts += [f"- [{i['severity'].upper()}] **{i['title']}**: {i['message']}" for i in insights[:8]]
    return Section("recommendations", "\n".join(parts), 1)


def apply_profile(report: dict, detail: PromptDetail, focus: str) -> Section | None:
    apply = report["apply"]
    b = apply["batches"]
    if not b["count"] or focus == "configuration":
        return None
    reasons = ", ".join(f"{k} {v} ({round(100 * v / b['count'])}%)" for k, v in b["closure_reasons"].items())
    sizes, durations = b["size_stats"], b["duration_stats"]
    lines = [f"- Batches: **{b['count']:,}**; close reasons: {reasons}"]
    if sizes.get("count"):
        lines.append(f"- Changes per batch: avg {sizes['avg']}, median {sizes['median']}, max {sizes['max']:,}; "
                     f"single-record batches {sizes['single_record_pct']}%")
    if durations.get("count"):
        lines.append(f"- Batch apply seconds: avg {durations['avg']}, p95 {durations['p95']}, max {durations['max']}")
    if b["rps"]["median_rps"] is not None:
        lines.append(f"- Median throughput {b['rps']['median_rps']:,} changes/s over {b['rps']['measured_batches']} batches")
    if apply["one_by_one"]:
        lines.append(f"- One-by-one episodes: {len(apply['one_by_one'])} (first at L{apply['one_by_one'][0]['start_line']})")
    text = "## Target apply (computed)\n\n" + "\n".join(lines)
    pain = apply["pain_tables"][:min(8, detail.issue_rows)]
    if pain:
        text += "\n\n" + _table(["Table", "Ins", "Upd", "Del", "Merge", "Apply s", "1-by-1", "Errors", "PK"],
                                [[t["table"], t["insert"], t["update"], t["delete"], t["merge"], t["apply_time"],
                                  t["one_by_one"], t["errors"], {True: "yes", False: "no", None: "?"}[t["has_pk"]]]
                                 for t in pain])
    return Section("apply", text, 2 if focus == "performance" else 3)


def full_load(report: dict, detail: PromptDetail, focus: str) -> Section | None:
    fl = report["full_load"]
    if not fl.get("available"):
        return None
    s = fl["summary"]
    text = (f"## Full Load (computed)\n\n- Completed: **{'yes' if s['completed'] else 'no'}**; tables {s['tables_total']} "
            f"(loaded {s['tables_loaded']}, loading {s['tables_loading']}, failed {s['tables_failed']}, "
            f"reloaded {s['tables_reloaded']}); rows received {s['rows_received']:,}; reload events {s['reload_events']}")
    slow = [t for t in fl["tables"] if t["duration_seconds"]][:min(8, detail.issue_rows)]
    if slow:
        text += "\n\n" + _table(["Table", "Status", "Segments", "Rows", "Duration s", "Reloads"],
                                [[t["table"], t["status"], f"{t['segments_complete']}/{t['segment_count']}",
                                  f"{t['rows_received']:,}", round(t["duration_seconds"]), t["reload_count"]] for t in slow])
    return Section("full_load", text, 3)
