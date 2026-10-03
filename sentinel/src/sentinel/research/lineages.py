"""Phase-2 research lineages, implemented exactly as pre-registered in research/PREREGISTRATION_PHASE2.md.

Research only: never imported by the live decision path.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime

from sentinel.contracts import Bar
from sentinel.features import atr_sma
from sentinel.validator import CostModel


@dataclass(frozen=True)
class LTrade:
    lineage: str
    instrument: str
    signal_ts: int
    entry_ts: int
    direction: int
    r: float
    pnl_price: float
    leg: str = ""


def _scale(c: CostModel, k: float) -> CostModel:
    return CostModel(c.spread * k, c.commission_price * k, c.slippage * k)


def _d(ts: int) -> datetime:
    return datetime.fromtimestamp(ts, UTC)


# ------------------------------------------------------------------ L2 time-series momentum
def tsmom(bars: list[Bar], instrument: str, cost: CostModel, cost_mult=1.0, swap_annual=0.04, lookback=252,
          start_ts=None, end_ts=None) -> list[LTrade]:
    c = _scale(cost, cost_mult)
    swap = swap_annual * cost_mult
    month_first, month_last = {}, {}
    for i, b in enumerate(bars):
        key = (_d(b.ts).year, _d(b.ts).month)
        month_first.setdefault(key, i)
        month_last[key] = i
    keys = sorted(month_first)
    out = []
    for n in range(len(keys) - 2):
        i = month_last[keys[n]]
        if i < lookback:
            continue
        ts = bars[i].ts
        if (start_ts and ts < start_ts) or (end_ts and ts >= end_ts):
            continue
        r12 = bars[i].close / bars[i - lookback].close - 1
        if r12 == 0:
            continue
        d = 1 if r12 > 0 else -1
        j, k = month_first[keys[n + 1]], month_first[keys[n + 2]]
        atr = atr_sma(bars[max(0, i - 21):i + 1], 20)
        if not math.isfinite(atr) or atr <= 0:
            continue
        side = c.spread / 2 + c.slippage
        entry = bars[j].open + d * side
        exit_ = bars[k].open - d * side
        days = (bars[k].ts - bars[j].ts) / 86400
        financing = swap * entry * days / 365
        pnl = (exit_ - entry) * d - c.commission_price - financing
        out.append(LTrade("L2_tsmom_12m_v1", instrument, ts, bars[j].ts, d, pnl / (3 * atr), pnl))
    return out


# ------------------------------------------------------------------ L3 FX local-hours effect
LEGS = {"T_long_tokyo": (0, 7, 1), "N_short_newyork": (13, 20, -1)}


def fx_local_hours(bars: list[Bar], cost: CostModel, cost_mult=1.0, start_ts=None, end_ts=None, legs=None) -> list[LTrade]:
    c = _scale(cost, cost_mult)
    legs = legs or LEGS
    idx = {b.ts: i for i, b in enumerate(bars)}
    days = sorted({b.ts - b.ts % 86400 for b in bars})
    out = []
    for day in days:
        if _d(day).weekday() >= 5:
            continue
        for leg, (h_in, h_out, d) in legs.items():
            t_in, t_out = day + h_in * 3600, day + h_out * 3600
            if (start_ts and t_in < start_ts) or (end_ts and t_in >= end_ts):
                continue
            if t_in not in idx or t_out not in idx:
                continue
            i, k = idx[t_in], idx[t_out]
            if i < 25:
                continue
            atr = atr_sma(bars[i - 25:i], 24)
            if not math.isfinite(atr) or atr <= 0:
                continue
            side = c.spread / 2 + c.slippage
            entry = bars[i].open + d * side
            exit_ = bars[k].open - d * side
            pnl = (exit_ - entry) * d - c.commission_price
            out.append(LTrade("L3_fx_local_hours_v1", "USDJPY", t_in, t_in, d, pnl / (atr * math.sqrt(h_out - h_in)), pnl, leg))
    return out


def break_even_cost_multiple(fn, *args, lo=0.0, hi=10.0, **kw) -> float | None:
    """Smallest cost multiple at which pooled expectancy <= 0 (bisection). None if positive even at hi."""
    def exp(k):
        rs = [t.r for t in fn(*args, cost_mult=k, **kw)]
        return sum(rs) / len(rs) if rs else float("nan")
    if not exp(lo) > 0:
        return 0.0
    if exp(hi) > 0:
        return None
    for _ in range(30):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if exp(mid) > 0 else (lo, mid)
    return round(hi, 3)
