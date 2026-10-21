"""PERFORMANCE latency samples: profile, bottleneck, spikes, plateaus and a chart series."""

from __future__ import annotations

import re

from logpacker.analysis import latency_patterns as patterns
from logpacker.analysis.numbers import describe
from logpacker.analysis.timeutil import seconds
from logpacker.ingest.lines import LogRecord

_PERF_RE = re.compile(
    r"Source latency ([0-9.]+) seconds,\s*Target latency ([0-9.]+) seconds,\s*Handling latency ([0-9.]+) seconds"
)
CHART_POINTS = 400


class LatencyCollector:
    name = "latency"

    def __init__(self) -> None:
        # (ts, line, source, target, handling, epoch seconds)
        self.samples: list[tuple] = []

    def feed(self, record: LogRecord) -> None:
        if record.component != "PERFORMANCE" or record.continuation or "latency" not in record.message:
            return
        if m := _PERF_RE.search(record.message):
            src, tgt, hnd = (float(x) for x in m.groups())
            self.samples.append((record.ts, record.line_no, src, tgt, hnd, seconds(record)))

    def result(self) -> dict:
        s = self.samples
        if not s:
            return {"samples": 0}
        profile = {name: {**describe([x[i] for x in s]), "last": round(s[-1][i], 2)}
                   for name, i in (("source", 2), ("target", 3), ("handling", 4))}
        peak = max(s, key=lambda x: x[3])
        return {
            "samples": len(s),
            **profile,
            "peak_target": {"ts": peak[0], "line": peak[1], "seconds": peak[3]},
            "bottleneck": patterns.bottleneck(s),
            "spikes": patterns.spikes(s),
            "plateaus": patterns.plateaus(s),
            "series": _downsample(s, CHART_POINTS),
        }


def _downsample(samples: list, points: int) -> list[dict]:
    """Bucket max keeps spikes visible while bounding the payload size."""
    if len(samples) <= points:
        buckets = [[x] for x in samples]
    else:
        size = len(samples) / points
        buckets = [samples[int(i * size):int((i + 1) * size)] for i in range(points)]
    out = []
    for bucket in buckets:
        if bucket:
            top = max(bucket, key=lambda x: x[3])
            out.append({"ts": top[0], "line": top[1], "source": top[2], "target": top[3], "handling": top[4]})
    return out
