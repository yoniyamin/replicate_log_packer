"""Which report tabs have data, why the others are missing, and an overall health verdict."""

from __future__ import annotations


def _tab(tab_id: str, label: str, available: bool, reason: str, count: int | None = None) -> dict:
    return {"id": tab_id, "label": label, "available": available, "count": count,
            "reason": None if available else reason}


def build_manifest(r: dict) -> dict:
    stats, apply, fl, files = r["stats"], r["apply"], r["full_load"], r["file_ops"]
    tabs = [
        _tab("overview", "Overview", True, ""),
        _tab("issues", "Errors & warnings", bool(r["issues"]["groups"]),
             "No errors or warnings in this log.", len(r["issues"]["groups"])),
        _tab("cockpit", "Performance cockpit", r["cockpit"]["available"],
             "No latency samples or batch data. Set the PERFORMANCE logger to TRACE to record latency every 30 seconds."),
        _tab("bulk", "Bulk apply", apply["batches"]["count"] > 0 or apply["table_count"] > 0,
             "No batch-apply activity. Set TARGET_APPLY to TRACE to log batches and per-table applies.",
             apply["batches"]["count"]),
        _tab("fullload", "Full Load", fl["available"], fl.get("reason") or "No Full Load activity in this log.",
             (fl.get("summary") or {}).get("tables_total")),
        _tab("fileops", "File operations", files["count"] > 0,
             "No staging-file uploads. These appear only for cloud targets that stage files (e.g. Snowflake, Databricks).",
             files["count"]),
        _tab("patterns", "Repeated lines", True, "", r["repetition"]["distinct"]),
        _tab("privacy", "What was hidden", True, ""),
    ]
    return {"tabs": tabs, "health": _health(r)}


def _health(r: dict) -> dict:
    """1 (critical) .. 5 (healthy), with the reasons that lowered it."""
    score, reasons = 5, []
    errors = r["stats"]["errors"]
    if errors >= 100:
        score -= 2
        reasons.append(f"{errors:,} errors")
    elif errors:
        score -= 1
        reasons.append(f"{errors:,} errors")
    if r["metadata"].get("fatal"):
        score -= 2
        reasons.append("fatal error")
    if r["health"]["pipeline"]["health"] == "critical" or r["health"]["source"]["health"] == "critical":
        score -= 1
        reasons.append("pipeline or source issues")
    if r["apply"]["one_by_one"]:
        score -= 1
        reasons.append("one-by-one apply")
    if r["full_load"].get("available") and r["full_load"]["summary"]["reload_events"]:
        score -= 1
        reasons.append("Full Load reloads")
    lat = r["latency"]
    if lat.get("samples") and lat["target"]["avg"] >= 60:
        score -= 1
        reasons.append(f"average latency {lat['target']['avg']:,.0f}s")
    score = max(1, score)
    label = {5: "Healthy", 4: "Minor issues", 3: "Needs attention", 2: "Degraded", 1: "Critical"}[score]
    return {"score": score, "label": label, "reasons": reasons}
