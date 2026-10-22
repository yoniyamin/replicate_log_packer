"""Performance Cockpit: combines latency, apply, health and Oracle results into
error correlation, top contributors and prioritized recommendations."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import Counter

from logpacker.analysis.numbers import pct


def build_cockpit(report: dict, samples: list[tuple], timeline: list[dict]) -> dict:
    correlation = _correlation(samples, timeline)
    return {
        "available": bool(samples) or report["apply"]["batches"]["count"] > 0,
        "error_correlation": correlation,
        "config": _config(report),
        "top_contributors": _contributors(report),
        "recommendations": _recommendations(report, report["latency"], correlation),
    }


def _correlation(samples: list[tuple], timeline: list[dict]) -> dict:
    timed = [s for s in samples if s[5] is not None]
    if len(timed) < 4:
        return {"available": False}
    threshold = pct([s[3] for s in timed], 0.75)
    windows, start = [], None
    last = None
    for cur in timed:
        if cur[3] > threshold:
            start = start if start is not None else cur[5]
            last = cur[5]
        elif start is not None:
            windows.append((start, last))
            start = None
    if start is not None:
        windows.append((start, last))

    errors = sorted((e for e in timeline if e.get("t") is not None), key=lambda e: e["t"])
    times = [e["t"] for e in errors]
    hits = []
    for begin, end in windows:
        hits.extend(errors[bisect_left(times, begin):bisect_right(times, end)])
    return {
        "available": True, "threshold_seconds": round(threshold, 2), "windows": len(windows),
        "errors_in_windows": len(hits), "errors_total": len(errors),
        "by_component": dict(Counter(e["component"] for e in hits).most_common(8)),
        "by_type": dict(Counter(_error_type(e["text"]) for e in hits).most_common()),
        "samples": [{"line": e["line"], "ts": e["ts"], "group": e["group"], "text": e["text"]} for e in hits[:10]],
    }


def _error_type(text: str) -> str:
    low = text.lower()
    if "reconnect" in low or "connection" in low:
        return "connection"
    if "timeout" in low or "timed out" in low:
        return "timeout"
    if "memory" in low:
        return "memory"
    if "sql_error" in low or "sqlstate" in low:
        return "sql"
    return "other"


def _config(report: dict) -> dict:
    meta, merge = report["metadata"], report["apply"]["merge"]
    values = {k: v["value"] for k, v in meta["settings"].items()}
    return {**values, "source": meta.get("source_endpoint"), "target": meta.get("target_endpoint"),
            "apply_mode": "MERGE" if merge["enabled"] else "standard", "merge_enabled": merge["enabled"]}


def _contributors(report: dict) -> list[str]:
    out = []
    primary = (report["latency"].get("bottleneck") or {}).get("primary")
    if primary == "source":
        out.append("Source capture")
    elif primary == "target_apply":
        out.append("Target apply")
    batches = report["apply"]["batches"]
    reasons = batches["closure_reasons"]
    if batches["count"] and sum(reasons.get(k, 0) for k in ("PKi", "PKu", "PKd")) / batches["count"] > 0.10:
        out.append("PK conflicts")
    pain = report["apply"]["pain_tables"]
    if pain and pain[0]["pain"] > 100:
        out.append(f"Table {pain[0]['table']}")
    if report["file_ops"]["count"] and (report["file_ops"]["upload_seconds"].get("avg") or 0) > 1:
        out.append("File uploads")
    return out or ["Normal operation"]


def _duration(seconds: float) -> str:
    if seconds >= 3600:
        return f"{seconds / 3600:.1f} h"
    if seconds >= 60:
        return f"{seconds / 60:.1f} min"
    return f"{seconds:.1f} s"


def _rec(priority: str, area: str, title: str, description: str, actions: list[str] | None = None) -> dict:
    return {"priority": priority, "area": area, "title": title, "description": description, "actions": actions or []}


def _recommendations(report: dict, lat: dict, corr: dict) -> list[dict]:
    recs = []
    bn = lat.get("bottleneck") or {}
    if bn.get("primary") == "source":
        recs.append(_rec("high", "Source", "Source capture latency dominates",
                         f"Source capture accounts for {bn['source_pct']}% of the delay.",
                         ["Check load and log-reading speed on the source database",
                          "Check network latency between Replicate and the source",
                          "Review the source endpoint's read/batch settings"]))
    elif bn.get("primary") == "target_apply":
        recs.append(_rec("high", "Target", "Target apply latency dominates",
                         f"Handling/apply accounts for {bn['apply_pct']}% of the delay.",
                         ["Check target database performance and locks",
                          "Review batch tuning so batches are larger (fewer, bigger applies)",
                          "Look for one-by-one fallbacks and tables without PK",
                          "Consider parallel apply if the target allows it"]))
    if (lat.get("spikes") or {}).get("count", 0) > 5:
        recs.append(_rec("medium", "Stability", "Frequent latency spikes",
                         f"{lat['spikes']['count']} spikes above {2.5}× the recent baseline.",
                         ["Correlate the spike times with errors and source activity"]))
    if (lat.get("plateaus") or {}).get("count"):
        recs.append(_rec("medium", "Performance", "Sustained high latency",
                         f"{lat['plateaus']['count']} period(s) where latency stayed high.",
                         ["Check what ran on the source or target during those periods"]))
    apply = report["apply"]
    for issue in apply["batches"]["issues"]:
        if issue["severity"] == "warning":
            recs.append(_rec("medium", "Batching", issue["title"], issue["message"], [issue["recommendation"]]))
    obo = [t for t in apply["pain_tables"] if t["one_by_one"]]
    if obo:
        t = obo[0]
        recs.append(_rec("high" if t["one_by_one"] > 10 or apply["one_by_one"] else "medium", "Tables",
                         f"Table {t['table']} falls back to one-by-one",
                         f"{t['one_by_one']} one-by-one switch(es); {_duration(t['apply_time'])} total apply time.",
                         ["Check the primary key / unique index", "Look for data or constraint errors on this table"]))
    health = report["health"]
    if health["pipeline"]["health"] != "healthy":
        p = health["pipeline"]
        crit = p["health"] == "critical"
        recs.append(_rec("high" if crit else "medium", "Pipeline",
                         "Critical CDC pipeline issues" if crit else "CDC pipeline warnings",
                         f"{p['memory_warnings']} sorter memory warnings, {p['overflow_events']} overflow events, "
                         f"{p['disconnections']} target disconnections.",
                         ["Review sorter memory / stream buffer settings", "Check target connectivity"]))
    src = health["source"]
    if src["health"] == "critical" or (src["health"] == "warning" and src["hints"]):
        recs.append(_rec("high" if src["health"] == "critical" else "medium", "Source",
                         "Critical source issues" if src["health"] == "critical" else "Source issues detected",
                         f"{src['errors']} source errors, {src['reconnects']} reconnects.", src["hints"]))
    if report["oracle"].get("has_red_flags"):
        recs.append(_rec("high", "Source", "Oracle redo read times vary widely",
                         "Reads of the same size take very different times (trace logging).",
                         ["Check I/O on the redo/archive storage and ASM", "Look for contention at the slow times"]))
    if corr.get("errors_in_windows"):
        recs.append(_rec("medium", "Errors", "Errors coincide with high latency",
                         f"{corr['errors_in_windows']} errors happened while latency was above its 75th percentile.",
                         ["Start with the errors listed under Error correlation"]))
    ls_signal = next((s for s in report["signals"]["signals"] if s["id"] == "logstream_missing_files"), None)
    if ls_signal:
        recs.append(_rec("high", "Source", "Missing Logstream audit files",
                         f"{ls_signal['count']} warning(s) about missing audit files. "
                         "This means the Logstream source is behind and required data is not available for replication.",
                         ["Check the Logstream source task — it may have stopped or fallen behind",
                          "Verify storage for the Logstream landing directory",
                          "Restart the Logstream source task if needed to resend missing files"]))
    order = {"high": 0, "medium": 1, "low": 2}
    return sorted(recs, key=lambda r: order[r["priority"]])
