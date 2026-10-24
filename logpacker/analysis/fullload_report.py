"""Shape Full Load state into the report: per-table rows, summary and insights."""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

from logpacker.analysis.timeutil import epoch

if TYPE_CHECKING:
    from logpacker.analysis.fullload import FullLoadCollector

SLOW_SEGMENT_SECONDS = 3600
GAP_SECONDS = 30
MAX_TABLE_ROWS = 500
LOOP_MIN_RELOADS = 2
CONSTANT_SPREAD_SECONDS = 60  # time-to-failure spread still called "near-constant": 60 s or 20 %
CONSTANT_SPREAD = 0.2


def build_full_load_report(c: "FullLoadCollector") -> dict:
    reloads = [_clean(x) for x in c.reloads]
    tables = [_table(t, [x for x in reloads if x["table_id"] == t["id"]]) for t in c.tables.values()]
    # Reload loops first (the table that keeps failing has no duration yet), then longest.
    tables.sort(key=lambda t: (-t["reload_count"], -(t["duration_seconds"] or 0)))
    if not tables and not c.completed:
        return {"available": False, "reason": "No Full Load table or segment activity was found in this log.",
                "tables": [], "insights": []}
    summary = _summary(c, tables)
    return {"available": True, "summary": summary, "tables": tables[:MAX_TABLE_ROWS],
            "reloads": reloads[:200], "insights": _insights(summary, tables)}


def _table(t: dict, reloads: list[dict]) -> dict:
    segs = [_clean(s) for s in t["segments"].values()]
    current = sorted((s for s in segs if s["attempt"] == t["attempt"]), key=lambda s: s["segment"])
    prior = [s for s in segs if s["attempt"] != t["attempt"]]
    start, end = t["init_start"] or t["load_start"], t["load_end"]
    return {
        **{k: v for k, v in t.items() if not k.startswith("_") and k != "segments"},
        "reload_loop": _reload_loop(reloads, prior) if len(reloads) >= LOOP_MIN_RELOADS else None,
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


def _reload_loop(reloads: list[dict], prior: list[dict]) -> dict:
    """What repeats across the reloads of one table: the failing segment, how long it lasts, what is re-done."""
    failing = Counter(x["segment"] for x in reloads)
    segment, fails = failing.most_common(1)[0]
    lasted = sorted(x["segment_seconds"] for x in reloads if x["segment"] == segment and x["segment_seconds"] is not None)
    waits = [x["wait_seconds"] for x in reloads if x["wait_seconds"] is not None]
    causes = Counter(x["cause"] for x in reloads if x["cause"])
    cause = causes.most_common(1)[0][0] if causes else None
    first_cause = next((x for x in reloads if x["cause"] == cause), None) if cause else None
    # Segments that completed in an attempt that was then thrown away by the reload.
    redone = [s for s in prior if s["status"] == "complete" and (s["volume"] or s["rows_received"])]
    by_seg: dict[int, list[dict]] = {}
    for s in redone:
        by_seg.setdefault(s["segment"], []).append(s)
    biggest = max(by_seg.items(), key=lambda kv: sum(s["volume"] or 0 for s in kv[1]), default=None)
    median = lasted[len(lasted) // 2] if lasted else None
    return {
        "reloads": len(reloads), "failing_segment": segment, "failing_segment_reloads": fails,
        "seconds_to_failure": {"min": lasted[0], "median": median, "max": lasted[-1]} if lasted else None,
        # A near-constant time to failure points to a fixed limit (a timeout) rather than data or locking.
        "constant_failure_time": bool(lasted) and len(lasted) >= LOOP_MIN_RELOADS
                                 and lasted[-1] - lasted[0] <= max(CONSTANT_SPREAD_SECONDS, CONSTANT_SPREAD * median),
        "cause": cause, "cause_line": first_cause["cause_line"] if first_cause else None,
        "cause_count": causes[cause] if cause else 0,
        "wait_seconds": {"min": min(waits), "max": max(waits)} if waits else None,
        "executed_by_sorter": len(waits),
        "redone_rows": sum(s["rows_received"] or 0 for s in redone),
        "redone_volume": sum(s["volume"] or 0 for s in redone),
        "largest_redone_segment": {
            "segment": biggest[0], "times": len(biggest[1]),
            "rows": max(s["rows_received"] or 0 for s in biggest[1]),
            "volume": max(s["volume"] or 0 for s in biggest[1]),
        } if biggest else None,
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
    loops = [t for t in tables if t["reload_loop"]]
    for t in loops[:3]:
        out.append(_loop_insight(t["table"], t["reload_loop"]))
    if summary["reload_events"] and not loops:
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


def loop_sentences(table: str, loop: dict) -> list[str]:
    """The reload loop in plain sentences; shared by the insight and the prompt."""
    seg, n = loop["failing_segment"], loop["reloads"]
    out = [f"{table} was reloaded {n} times; segment #{seg} failed in {loop['failing_segment_reloads']} of them."]
    if t := loop["seconds_to_failure"]:
        when = (f"after {_dur(t['min'])}" if t["min"] == t["max"] else f"{_dur(t['min'])}–{_dur(t['max'])} after it started")
        out.append(f"Each time it failed {when}"
                   + (" — a near-constant time, which points to a fixed limit such as a timeout rather than to the data "
                      "or to locking." if loop["constant_failure_time"] else "."))
    if loop["cause"]:
        out.append(f"Cause logged first (L{loop['cause_line']}, {loop['cause_count']}×): \"{loop['cause'][:160]}\".")
    if big := loop["largest_redone_segment"]:
        out.append(f"Work thrown away by the reloads: segment #{big['segment']} ({big['rows']:,} rows, "
                   f"{_bytes(big['volume'])}) completed {big['times']}× and was loaded again each time; "
                   f"{_bytes(loop['redone_volume'])} in total.")
    if w := loop["wait_seconds"]:
        out.append(f"The SORTER line \"Reload for table Id … is requested\" that follows each reload is Replicate "
                   f"carrying out that reload once the segments still running had finished ({_dur(w['min'])}–"
                   f"{_dur(w['max'])} later), not a separate trigger.")
    return out


def _loop_insight(table: str, loop: dict) -> dict:
    fix = (f"Fix the error that fails segment #{loop['failing_segment']} first"
           + (" — with a constant time to failure, check the source endpoint's query/command timeout"
              if loop["constant_failure_time"] else "")
           + ". Splitting the large segment into smaller ranges keeps a retry from re-loading it.")
    return _i("error", f"Reload loop on {table}", " ".join(loop_sentences(table, loop)), fix)


def _dur(s: float) -> str:
    if s < 120:
        return f"{s:.0f}s"
    if s < 7200:
        return f"{s / 60:.0f}m"
    return f"{s / 3600:.1f}h"


def _bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:,.0f} {unit}" if unit == "B" else f"{n:,.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _i(severity: str, title: str, message: str, recommendation: str | None) -> dict:
    return {"severity": severity, "title": title, "message": message, "recommendation": recommendation}
