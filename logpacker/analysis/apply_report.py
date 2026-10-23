"""Turn raw apply data into the Bulk Apply report: batch profile, tables, insights.

Thresholds follow the old Performance Cockpit / Bulk Apply reports.
"""

from __future__ import annotations

from collections import Counter
from statistics import median
from typing import TYPE_CHECKING

from logpacker.analysis.numbers import describe, share

if TYPE_CHECKING:
    from logpacker.analysis.apply import ApplyCollector

MAX_BATCH_ROWS = 3000

CLOSE_REASONS = {
    "TIM": ("Stream timeout", "Batch waited for more changes and closed after the minimum wait.",
            "Batch tuning: 'Longer than (seconds)', default 1"),
    "TMO": ("Bulk timeout", "Batch was released on schedule while the sorter was still feeding changes.",
            "Batch tuning: 'But less than (seconds)', default 30"),
    "MEM": ("Memory limit", "Batch closed because the memory threshold was reached.",
            "Batch tuning: 'Force apply a batch when processing memory exceeds (MB)'"),
    "PKi": ("PK insert conflict", "Two INSERTs on the same primary key in one batch.", None),
    "PKu": ("PK update conflict", "An UPDATE changed a primary key already touched in this batch.", None),
    "PKd": ("PK delete conflict", "A DELETE hit a primary key already touched in this batch.", None),
    "SNG": ("Single table", "Batch finished for tables with PK.", None),
    "RES": ("Resume", "Batch closed for a resume operation.", None),
    "LOAD": ("Load table", "Batch closed for a Full Load table event.", None),
    "Normal": ("Normal", "Batch completed normally.", None),
}

RPS_BUCKETS = (("stalled", 0, 10), ("low", 10, 100), ("moderate", 100, 1_000),
               ("good", 1_000, 10_000), ("fast", 10_000, float("inf")))


def build_apply_report(c: "ApplyCollector") -> dict:
    batches = c.batches
    tables = [_table_row(t) for t in c.tables.values()]
    tables.sort(key=lambda t: -t["pain"])
    report = {
        "batches": _batch_profile(batches, c.batch_overflow),
        "tables": tables[:300],
        "table_count": len(tables),
        "pain_tables": tables[:10],
        "operations": _operations(c.table_ops),
        "one_by_one": c.episodes,
        "merge": _merge(tables, c.merge_seen),
        "no_pk_events": c.no_pk,
        "no_bulk_events": c.no_bulk,
        "close_reason_info": {k: {"label": v[0], "meaning": v[1], "setting": v[2]} for k, v in CLOSE_REASONS.items()},
    }
    report["insights"] = _insights(report)
    return report


def _table_row(t) -> dict:
    total = sum(t.ops.values())
    apply_time = round(t.apply_time, 3)
    return {
        "table": t.name, **{k.lower(): v for k, v in t.ops.items()}, "total": total,
        "applies": t.applies, "apply_time": apply_time,
        "avg_apply_time": round(t.apply_time / t.applies, 3) if t.applies else None,
        "max_apply_time": round(t.max_apply_time, 3), "one_by_one": t.one_by_one, "errors": t.errors,
        "has_pk": t.has_pk, "pk_columns": t.pk_columns, "target": t.target, "first_line": t.first_line,
        "pain": round(apply_time + 10 * t.errors + 5 * t.one_by_one, 2),
    }


def _batch_profile(batches: list[dict], overflow: int) -> dict:
    sizes = [b["changes"] for b in batches if b["changes"] > 0]
    durations = [b["duration"] for b in batches if b["duration"]]
    reasons = Counter(b["reason"] for b in batches)
    singles = sum(1 for s in sizes if s == 1)
    size_stats = {**describe(sizes, 1), "total_changes": sum(sizes), "single_record_batches": singles,
                  "single_record_pct": share(singles, len(sizes)),
                  "small_batches_pct": share(sum(1 for s in sizes if s < 100), len(sizes))} if sizes else {"count": 0}
    return {
        "count": len(batches) + overflow,
        "closure_reasons": dict(reasons.most_common()),
        "size_stats": size_stats,
        "duration_stats": describe(durations, 3),
        "rps": _rps(batches),
        "issues": _batch_issues(len(batches), size_stats, reasons),
        "list": batches[:MAX_BATCH_ROWS],
        "list_truncated": len(batches) > MAX_BATCH_ROWS,
    }


def _rps(batches: list[dict]) -> dict:
    rates = [(b["changes"] / b["duration"], b["changes"]) for b in batches if b["duration"] and b["changes"]]
    buckets = []
    for name, low, high in RPS_BUCKETS:
        inside = [(r, ch) for r, ch in rates if low <= r < high]
        if inside:
            total = sum(ch for _, ch in inside)
            buckets.append({"bucket": name, "range": f"{low:,}–{'∞' if high == float('inf') else f'{high:,}'}",
                            "batches": len(inside), "changes": total, "avg_batch_size": round(total / len(inside), 1),
                            "share": share(len(inside), len(rates))})
    return {"buckets": buckets, "median_rps": round(median([r for r, _ in rates]), 1) if rates else None,
            "measured_batches": len(rates), "unknown_duration": len(batches) - len(rates)}


def _batch_issues(count: int, sizes: dict, reasons: Counter) -> list[dict]:
    issues = []
    if sizes.get("single_record_pct", 0) > 20:
        issues.append(_insight("warning" if sizes["single_record_pct"] > 40 else "info", "Many single-record batches",
                               f"{sizes['single_record_pct']}% of batches contain only 1 record.",
                               "Review for PK conflicts or very small source transactions."))
    pk = reasons["PKi"] + reasons["PKu"] + reasons["PKd"]
    if count and pk / count > 0.10:
        issues.append(_insight("warning", "PK conflicts close batches",
                               f"{pk} batches closed due to PK conflicts ({share(pk, count)}%).",
                               "Consider batch settings or review the source transaction pattern."))
    timeouts = reasons["TIM"] + reasons["TMO"]
    if count and timeouts / count > 0.30:
        issues.append(_insight("info", "Batches close on timeouts", f"{timeouts} batches closed due to timeouts.",
                               "Normal for CDC with low activity; adjust the batch timeouts if latency matters."))
    return issues


def _operations(ops: list[dict]) -> dict:
    if not ops:
        return {"count": 0}
    rows = [o["rows"] for o in ops]
    singles = sum(1 for r in rows if r == 1)
    return {"count": len(ops), "single_record": singles, "single_record_pct": share(singles, len(rows)),
            "avg_rows": round(sum(rows) / len(rows), 1), "max_rows": max(rows),
            "apply_seconds": describe([o["seconds"] for o in ops if o["seconds"] is not None], 3),
            "list": ops[:MAX_BATCH_ROWS]}


def _merge(tables: list[dict], seen: bool) -> dict:
    merges = sum(t["merge"] for t in tables)
    standard = sum(t["insert"] + t["update"] + t["delete"] for t in tables)
    using = sorted((t for t in tables if t["merge"]), key=lambda t: -t["merge"])
    return {"enabled": seen or merges > 0, "merge_ops": merges, "standard_ops": standard,
            "merge_pct": share(merges, merges + standard), "tables_using_merge": len(using),
            "top_tables": [{"table": t["table"], "merge": t["merge"]} for t in using[:10]]}


def _insights(r: dict) -> list[dict]:
    b, out = r["batches"], list(r["batches"]["issues"])
    reasons, n = b["closure_reasons"], b["count"] or 1
    if b["size_stats"].get("small_batches_pct", 0) > 50:
        out.append(_insight("info", "Many small batches", f"{b['size_stats']['small_batches_pct']}% of batches have fewer than 100 changes.",
                            "Low change volume or early closes; check close reasons and batch timeouts."))
    if reasons.get("MEM", 0) / n > 0.10:
        out.append(_insight("warning", "Memory pressure closes batches", f"{reasons['MEM']} batches closed on the memory limit.",
                            "Raise the batch memory limit or look for very large transactions."))
    if (reasons.get("TIM", 0) + reasons.get("TMO", 0)) / n > 0.20 and not any(i["title"] == "Batches close on timeouts" for i in out):
        out.append(_insight("info", "Timeout-triggered batches", "More than 20% of batches closed on a timeout.",
                            "Expected with low activity; tune 'Longer than' / 'But less than' if latency matters."))
    if any(k.startswith("PK") for k in reasons) and not any(i["title"] == "PK conflicts close batches" for i in out):
        out.append(_insight("info", "PK conflicts", "Some batches closed because of primary-key conflicts.", None))
    if r["one_by_one"]:
        tables = Counter(e["table"] for e in r["one_by_one"])
        top = ", ".join(f"{t} ×{c}" for t, c in tables.most_common(3))
        out.append(_insight("error", "Fell back to one-by-one apply",
                            f"{len(r['one_by_one'])} one-by-one episode(s): {top}.",
                            "A batch statement failed (data or constraint problem); check the errors around the first episode."))
    if r["no_pk_events"]:
        out.append(_insight("warning", "Tables without a primary key", f"{r['no_pk_events']} 'no PK' events.",
                            "Tables without PK cannot use efficient batch apply; add a PK or unique index."))
    return out


def _insight(severity: str, title: str, message: str, recommendation: str | None) -> dict:
    return {"severity": severity, "title": title, "message": message, "recommendation": recommendation}
