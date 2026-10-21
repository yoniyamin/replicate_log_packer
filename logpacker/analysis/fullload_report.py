"""Shape Full Load state into the report: per-table rows, summary and insights."""

from __future__ import annotations

from typing import TYPE_CHECKING

from logpacker.analysis.timeutil import epoch

if TYPE_CHECKING:
    from logpacker.analysis.fullload import FullLoadCollector

SLOW_SEGMENT_SECONDS = 3600
GAP_SECONDS = 30
MAX_TABLE_ROWS = 500


def build_full_load_report(c: "FullLoadCollector") -> dict:
    tables = [_table(t) for t in c.tables.values()]
    tables.sort(key=lambda t: -(t["duration_seconds"] or 0))
    if not tables and not c.completed:
        return {"available": False, "reason": "No Full Load table or segment activity was found in this log.",
                "tables": [], "insights": []}
    summary = _summary(c, tables)
    return {"available": True, "summary": summary, "tables": tables[:MAX_TABLE_ROWS],
            "reloads": c.reloads[:200], "insights": _insights(summary, tables)}


def _table(t: dict) -> dict:
    segs = [_clean(s) for s in t["segments"].values()]
    current = sorted((s for s in segs if s["attempt"] == t["attempt"]), key=lambda s: s["segment"])
    prior = [s for s in segs if s["attempt"] != t["attempt"]]
    start, end = t["init_start"] or t["load_start"], t["load_end"]
    return {
        **{k: v for k, v in t.items() if not k.startswith("_") and k != "segments"},
        "attempts": t["attempt"],
        "segments_complete": sum(1 for s in current if s["status"] == "complete"),
        "segments_loading": sum(1 for s in current if s["status"] == "loading"),
        "segments_error": sum(1 for s in current if s["status"] == "error"),
        "unload_seconds": round(sum(s["unload_seconds"] or 0 for s in current), 3),
        "max_segment_seconds": max((s["total_seconds"] or 0 for s in current), default=0),
        "max_gap_seconds": max((s["gap_seconds"] or 0 for s in current), default=0),
        "duration_seconds": _duration(start, end),
        "segments": current, "prior_attempt_segments": prior[:50],
    }


def _clean(seg: dict) -> dict:
    return {k: v for k, v in seg.items() if not k.startswith("_")}


def _duration(start: str | None, end: str | None) -> float | None:
    if not start or not end:
        return None
    return max(0.0, epoch(end) - epoch(start))


def _summary(c: "FullLoadCollector", tables: list[dict]) -> dict:
    by = lambda *states: sum(1 for t in tables if t["status"] in states)  # noqa: E731
    return {
        "completed": c.completed, "mode": c.mode,
        "tables_total": len(c.tables), "tables_loaded": by("complete"),
        "tables_loading": by("loading", "initializing", "reloading"), "tables_failed": by("error"),
        "tables_reloaded": sum(1 for t in tables if t["reload_count"]),
        "rows_received": sum(t["rows_received"] for t in tables), "rows_sent": sum(t["rows_sent"] for t in tables),
        "volume": sum(t["volume"] for t in tables),
        "max_parallel_segments": max((t["segment_count"] for t in tables), default=0),
        "reload_events": len(c.reloads), "start_ts": c.first_ts, "end_ts": c.last_ts,
        "duration_seconds": _duration(c.first_ts, c.last_ts),
    }


def _insights(summary: dict, tables: list[dict]) -> list[dict]:
    out = []
    if summary["reload_events"]:
        out.append(_i("error", "Full Load reload loops",
                      f"{summary['reload_events']} reload event(s) across {summary['tables_reloaded']} table(s). "
                      "A segment failed after a partial target load, forcing a full table reload.",
                      "Check SOURCE_UNLOAD errors on the failing subtask/segment; reduce segment size or fix "
                      "source connectivity/timeouts."))
    segs = [(t, s) for t in tables for s in t["segments"]]
    for t, s in sorted((x for x in segs if (x[1]["total_seconds"] or 0) >= SLOW_SEGMENT_SECONDS),
                       key=lambda x: -x[1]["total_seconds"])[:5]:
        out.append(_i("warning", f"Slow segment on {t['table']}",
                      f"Segment #{s['segment']} took {s['total_seconds'] / 3600:.1f}h ({s['rows_received'] or 0:,} rows).",
                      "Review segment split balance and source unload parallelism; skewed ranges cause long-tail segments."))
    for t, s in sorted((x for x in segs if (x[1]["gap_seconds"] or 0) >= GAP_SECONDS),
                       key=lambda x: -x[1]["gap_seconds"])[:3]:
        out.append(_i("warning", f"Unload→load gap on {t['table']}",
                      f"Segment #{s['segment']} waited {s['gap_seconds']:.0f}s between unload finish and load finish.",
                      "Check TARGET_LOAD throughput, bulk insert performance and target contention."))
    mismatches = [(t, s) for t, s in segs if s["rows_sent"] is not None and s["rows_received"] is not None
                  and s["rows_sent"] != s["rows_received"]]
    for t, s in mismatches[:5]:
        out.append(_i("error", f"Row count mismatch on {t['table']}",
                      f"Segment #{s['segment']}: {s['rows_sent']:,} unloaded vs {s['rows_received']:,} loaded.",
                      "Investigate TARGET_LOAD errors or skipped rows for this segment."))
    if summary["tables_loading"] and not summary["completed"]:
        out.append(_i("info", "Full Load still in progress",
                      f"{summary['tables_loading']} table(s) still loading; no 'Full load completed' message.", None))
    return out


def _i(severity: str, title: str, message: str, recommendation: str | None) -> dict:
    return {"severity": severity, "title": title, "message": message, "recommendation": recommendation}
