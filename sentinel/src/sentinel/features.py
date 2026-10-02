"""Pure, causal feature functions over closed bars. Each has a version string."""

from __future__ import annotations

import hashlib
import math

from sentinel.contracts import Bar

FEATURE_VERSIONS = {
    "atr_sma": "1",
    "atr_wilder": "1",
    "donchian": "1",
    "efficiency_ratio": "1",
    "percentile_rank": "1",
}


def dataset_hash(bars: list[Bar]) -> str:
    h = hashlib.sha256()
    for b in bars:
        h.update(f"{b.instrument}|{b.ts}|{b.open}|{b.high}|{b.low}|{b.close}\n".encode())
    return h.hexdigest()


def true_ranges(bars: list[Bar]) -> list[float]:
    out = []
    for i, b in enumerate(bars):
        prev = bars[i - 1].close if i else b.close
        out.append(max(b.high - b.low, abs(b.high - prev), abs(b.low - prev)))
    return out


def atr_sma(bars: list[Bar], n: int) -> float:
    tr = true_ranges(bars)
    if len(tr) < n + 1:
        return math.nan
    return sum(tr[-n:]) / n


def atr_wilder(bars: list[Bar], n: int) -> float:
    tr = true_ranges(bars)
    if len(tr) < 3 * n:
        return math.nan
    a = sum(tr[1 : n + 1]) / n
    for x in tr[n + 1 :]:
        a = (a * (n - 1) + x) / n
    return a


def donchian(bars: list[Bar], n: int) -> tuple[float, float]:
    """Channel of the n bars BEFORE the last bar (the last bar is the breakout bar)."""
    window = bars[-n - 1 : -1]
    return max(b.high for b in window), min(b.low for b in window)


def efficiency_ratio(closes: list[float], n: int) -> float:
    if len(closes) < n + 1:
        return math.nan
    net = abs(closes[-1] - closes[-n - 1])
    path = sum(abs(closes[i] - closes[i - 1]) for i in range(len(closes) - n, len(closes)))
    return net / path if path > 0 else 0.0


def percentile_rank(values: list[float], x: float) -> float:
    vals = [v for v in values if math.isfinite(v)]
    if not vals:
        return math.nan
    return sum(v <= x for v in vals) / len(vals)
