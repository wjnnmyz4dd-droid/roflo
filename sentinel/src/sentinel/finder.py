"""Finder: proposes candidates. No approval, sizing, or broker authority.

Hypothesis (``donchian_breakout_v1``): a close beyond the prior N-bar range in
the direction of a volatility-normalised move tends to continue over the next
H bars by more than costs. This is a well-known trend-following family; it is a
research hypothesis here, not an established edge for these instruments.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from sentinel.contracts import Bar, DataProvenance, Direction, TradeCandidate
from sentinel.features import FEATURE_VERSIONS, atr_sma, dataset_hash, donchian, percentile_rank, true_ranges
from sentinel.runtime import new_id

STRATEGY_VERSION = "donchian_breakout_v1"
FINDER_FEATURES = ("close", "high", "low", "donchian", "atr_sma")


@dataclass(frozen=True)
class FinderParams:
    channel: int = 20
    atr_n: int = 14
    stop_atr: float = 2.0
    horizon_bars: int = 10
    min_bars: int = 120
    candidate_ttl_s: int = 900


class Finder:
    def __init__(self, params: FinderParams | None = None, source: str = "unknown"):
        self.p = params or FinderParams()
        self.source = source

    def scan(self, instrument: str, bars: list[Bar], bar_seconds: int, now: int, retrieved_at: int) -> TradeCandidate | None:
        p = self.p
        closed = [b for b in bars if b.ts + bar_seconds <= now]  # never look at an unfinished bar
        if len(closed) < p.min_bars:
            return None
        last = closed[-1]
        hi, lo = donchian(closed, p.channel)
        atr = atr_sma(closed, p.atr_n)
        if not math.isfinite(atr) or atr <= 0:
            return None
        if last.close > hi:
            d = Direction.LONG
        elif last.close < lo:
            d = Direction.SHORT
        else:
            return None
        stop = last.close - d.sign * p.stop_atr * atr
        if stop <= 0:
            return None
        trs = true_ranges(closed[-100:])
        regime = {"vol_pct_rank": round(percentile_rank(trs, trs[-1]), 3), "atr": atr}
        breakout = (last.close - (hi if d is Direction.LONG else lo)) * d.sign / atr
        prov = DataProvenance(self.source, retrieved_at, dataset_hash(closed), last.ts, bar_seconds)
        return TradeCandidate(
            candidate_id=new_id("cand"),
            created_at=now,
            instrument=instrument,
            direction=d,
            setup=f"close beyond {p.channel}-bar channel",
            strategy_version=STRATEGY_VERSION,
            regime=regime,
            entry_reference=last.close,
            entry_hypothesis=f"breakout of {p.channel}-bar range continues over {p.horizon_bars} bars",
            invalidation_price=round(stop, 10),
            invalidation_hypothesis=f"price returns {p.stop_atr} ATR against the breakout",
            horizon_bars=p.horizon_bars,
            expires_at=now + p.candidate_ttl_s,
            evidence={"channel_high": hi, "channel_low": lo, "atr": atr, "breakout_atr": round(breakout, 4),
                      "signal_bar_ts": last.ts},
            confidence=None,  # no calibrated probability model; refusing to invent one
            provenance=prov,
            feature_versions={k: FEATURE_VERSIONS[k] for k in ("donchian", "atr_sma", "percentile_rank")},
            model_versions={},
        )
