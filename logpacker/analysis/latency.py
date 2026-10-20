"""PERFORMANCE latency samples: summary statistics plus a small chart series."""

from __future__ import annotations

import re
from statistics import fmean

from logpacker.ingest.lines import LogRecord

_PERF_RE = re.compile(
    r"Source latency ([0-9.]+) seconds,\s*Target latency ([0-9.]+) seconds,\s*Handling latency ([0-9.]+) seconds"
)
CHART_POINTS = 240


class LatencyCollector:
    name = "latency"

    def __init__(self) -> None:
        self.samples: list[tuple[str | None, int, float, float, float]] = []

    def feed(self, record: LogRecord) -> None:
        if record.component != "PERFORMANCE" or record.continuation:
            return
        if m := _PERF_RE.search(record.message):
            src, tgt, hnd = (float(x) for x in m.groups())
            self.samples.append((record.ts, record.line_no, src, tgt, hnd))

    def result(self) -> dict:
        if not self.samples:
            return {"samples": 0}
        cols = {"source": 2, "target": 3, "handling": 4}
        stats = {name: _stats([s[i] for s in self.samples]) for name, i in cols.items()}
        peak = max(self.samples, key=lambda s: s[3])
        return {
            "samples": len(self.samples),
            **stats,
            "peak_target": {"ts": peak[0], "line": peak[1], "seconds": peak[3]},
            "bottleneck": _bottleneck(stats),
            "series": _downsample(self.samples, CHART_POINTS),
        }


def _stats(values: list[float]) -> dict:
    ordered = sorted(values)
    p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
    return {"avg": round(fmean(values), 2), "p95": round(p95, 2), "max": round(ordered[-1], 2),
            "last": round(values[-1], 2)}


def _bottleneck(stats: dict) -> str:
    """Target latency includes source latency; the gap is what the target side adds."""
    src, tgt = stats["source"]["avg"], stats["target"]["avg"]
    if tgt < 5:
        return "healthy"
    if src >= tgt * 0.7:
        return "source"
    if stats["handling"]["avg"] >= (tgt - src) * 0.5:
        return "handling"
    return "target"


def _downsample(samples: list, points: int) -> list[dict]:
    """Bucket max keeps spikes visible while bounding the payload size."""
    if len(samples) <= points:
        buckets = [[s] for s in samples]
    else:
        size = len(samples) / points
        buckets = [samples[int(i * size):int((i + 1) * size)] for i in range(points)]
    out = []
    for bucket in buckets:
        if not bucket:
            continue
        top = max(bucket, key=lambda s: s[3])
        out.append({"ts": top[0], "line": top[1], "source": top[2], "target": top[3], "handling": top[4]})
    return out
