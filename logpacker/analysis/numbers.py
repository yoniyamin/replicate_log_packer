"""Small statistics helpers shared by the report builders."""

from __future__ import annotations

from statistics import fmean, median, pstdev


def pct(values: list[float], q: float) -> float:
    """Nearest-rank percentile on a sorted copy (same rule as the old app)."""
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * q))]


def describe(values: list[float], digits: int = 2) -> dict:
    if not values:
        return {"count": 0}
    r = lambda x: round(x, digits)  # noqa: E731
    return {
        "count": len(values), "min": r(min(values)), "max": r(max(values)), "avg": r(fmean(values)),
        "median": r(median(values)), "p50": r(pct(values, .5)), "p90": r(pct(values, .9)),
        "p95": r(pct(values, .95)), "p99": r(pct(values, .99)),
    }


def share(part: float, whole: float) -> float:
    return round(100 * part / whole, 1) if whole else 0.0


def cv(values: list[float]) -> float:
    """Coefficient of variation; 1.0 when the mean is zero."""
    mean = fmean(values)
    return pstdev(values) / mean if mean else 1.0
