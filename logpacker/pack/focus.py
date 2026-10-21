"""Pick what the prompt should concentrate on when the user chose `auto`."""

from __future__ import annotations

FOCUS_LABELS = {
    "errors": "Errors & root cause",
    "performance": "Latency & throughput",
    "configuration": "Task configuration review",
    "general": "General health review",
}

FOCUS_ASKS = {
    "errors": "Find the root cause of the errors and warnings in this task log, ordered by impact, "
              "and tell me how to fix them.",
    "performance": "Explain where the replication latency comes from in this task log and what I should tune.",
    "configuration": "Review the task settings visible in this log and suggest improvements.",
    "general": "Give me a health review of this Qlik Replicate task and anything that needs attention.",
}


def resolve_focus(report: dict, requested: str) -> tuple[str, str]:
    """Return (focus, reason)."""
    if requested != "auto":
        return requested, "Chosen by you"
    stats, latency = report["stats"], report["latency"]
    if stats["errors"]:
        groups = report["issues"]["error_groups"]
        return "errors", f"{stats['errors']:,} errors in {groups} patterns"
    if latency.get("samples") and latency["target"]["avg"] >= 5:
        return "performance", f"Average target latency {latency['target']['avg']}s"
    if stats["warnings"]:
        return "errors", f"No errors, but {stats['warnings']:,} warnings"
    return "general", "No errors and latency looks healthy"
