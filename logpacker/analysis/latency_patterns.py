"""Latency shape detection over PERFORMANCE samples: bottleneck, spikes, plateaus.

Samples are tuples (ts, line, source, target, handling, t). Target latency is the
end-to-end figure (source capture + handling/apply). Algorithms match the old cockpit.
"""

from __future__ import annotations

from math import sqrt
from statistics import fmean

from logpacker.analysis.numbers import cv

SPIKE_WINDOW, SPIKE_FACTOR = 5, 2.5
PLATEAU_WINDOW, PLATEAU_CV, PLATEAU_FACTOR = 10, 0.15, 1.3


def classify(source: float, handling: float) -> tuple[str, float]:
    """Return (bottleneck, source share %) from average source vs handling latency."""
    total = source + handling
    if total <= 0:
        return "balanced", 0.0
    src_share = source / total
    label = "source" if src_share > 0.65 else "target_apply" if src_share < 0.35 else "balanced"
    return label, round(src_share * 100, 1)


def bottleneck(samples: list[tuple]) -> dict:
    if len(samples) < 2:
        return {"primary": "unknown", "source_pct": None, "periods": []}
    label, src_pct = classify(fmean(s[2] for s in samples), fmean(s[4] for s in samples))
    window = max(5, len(samples) // 10)
    periods = []
    for i in range(0, len(samples), window):
        chunk = samples[i:i + window]
        src, hdl = fmean(s[2] for s in chunk), fmean(s[4] for s in chunk)
        kind, share = classify(src, hdl)
        periods.append({"start_ts": chunk[0][0], "end_ts": chunk[-1][0], "start_line": chunk[0][1],
                        "bottleneck": kind, "source_pct": share, "source_avg": round(src, 2),
                        "handling_avg": round(hdl, 2), "target_avg": round(fmean(s[3] for s in chunk), 2)})
    return {"primary": label, "source_pct": src_pct, "apply_pct": round(100 - src_pct, 1), "periods": periods}


def spikes(samples: list[tuple], limit: int = 10) -> dict:
    found = []
    for i in range(SPIKE_WINDOW, len(samples)):
        window = samples[i - SPIKE_WINDOW:i]
        base = fmean(s[3] for s in window)
        ts, line, src, tgt, hdl, _ = samples[i]
        if base > 0 and tgt > SPIKE_FACTOR * base:
            src_jump = src - fmean(s[2] for s in window)
            hdl_jump = hdl - fmean(s[4] for s in window)
            found.append({"ts": ts, "line": line, "value": round(tgt, 2), "baseline": round(base, 2),
                          "multiplier": round(tgt / base, 1), "driver": "source" if src_jump > hdl_jump else "handling",
                          "source": src, "handling": hdl})
    top = sorted(found, key=lambda s: -s["multiplier"])[:limit]
    return {"count": len(found), "top": top}


def plateaus(samples: list[tuple], limit: int = 5) -> dict:
    n = len(samples)
    if n < PLATEAU_WINDOW:
        return {"count": 0, "top": []}
    target = [s[3] for s in samples]
    overall = fmean(target)
    found, i = [], 0
    while i < n - PLATEAU_WINDOW:
        window = target[i:i + PLATEAU_WINDOW]
        mean, spread = fmean(window), cv(window)
        if mean > PLATEAU_FACTOR * overall and spread < PLATEAU_CV:
            end = i + PLATEAU_WINDOW - 1
            total, squares = sum(window), sum(v * v for v in window)
            while end + 1 < n:  # running sums keep the extension linear
                v = target[end + 1]
                k = end + 2 - i
                t2, s2 = total + v, squares + v * v
                m2 = t2 / k
                spread2 = sqrt(max(0.0, s2 / k - m2 * m2)) / m2 if m2 else 1.0
                if spread2 < PLATEAU_CV * 1.5 and m2 > overall:
                    end, total, squares = end + 1, t2, s2
                else:
                    break
            seg = target[i:end + 1]
            found.append({"start_ts": samples[i][0], "end_ts": samples[end][0], "start_line": samples[i][1],
                          "end_line": samples[end][1], "points": len(seg), "avg": round(fmean(seg), 2),
                          "variance_pct": round(spread * 100, 1)})
            i = end
        else:
            i += 1
    top = sorted(found, key=lambda p: -p["avg"])[:limit]
    return {"count": len(found), "top": top}
