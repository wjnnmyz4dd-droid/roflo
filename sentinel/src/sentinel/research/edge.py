"""Edge research harness (research only; never imported by the live path).

Trade model (identical for every ablation):
  signal on the CLOSE of bar i (Finder rule, bars <= i only)
  entry at OPEN of bar i+1 +/- (half spread + slippage)
  stop = signal close -/+ stop_atr * ATR(i); if a bar opens beyond the stop it fills at that open (gap)
  else exit at CLOSE of bar i+H -/+ (half spread + slippage); round-trip commission deducted
  R = net P&L / (|entry - stop|); one position per instrument at a time
"""

from __future__ import annotations

import math
import random
import statistics
from dataclasses import dataclass, replace

from sentinel.arbiter import Arbiter
from sentinel.contracts import ArbiterVerdict, Bar, Direction, ValidationVerdict
from sentinel.features import atr_sma
from sentinel.finder import Finder, FinderParams
from sentinel.validator import CostModel, Validator


@dataclass(frozen=True)
class Trade:
    instrument: str
    signal_ts: int
    direction: int
    entry: float
    exit: float
    r: float
    exit_reason: str
    validator: str | None = None
    arbiter: str | None = None


def scale_cost(c: CostModel, k: float) -> CostModel:
    return CostModel(c.spread * k, c.commission_price * k, c.slippage * k)


def signals(bars: list[Bar], p: FinderParams):
    """Yield (i, direction, stop) for every Finder signal using bars[:i+1] only."""
    N = p.channel
    for i in range(max(p.min_bars, N + p.atr_n + 1), len(bars) - 1):
        last = bars[i]
        hi = max(b.high for b in bars[i - N:i])
        lo = min(b.low for b in bars[i - N:i])
        if last.close > hi:
            d = 1
        elif last.close < lo:
            d = -1
        else:
            continue
        atr = atr_sma(bars[i - p.atr_n - 1:i + 1], p.atr_n)
        if not math.isfinite(atr) or atr <= 0:
            continue
        yield i, d, last.close - d * p.stop_atr * atr


def simulate(bars: list[Bar], i: int, d: int, stop: float, H: int, cost: CostModel):
    if i + 1 >= len(bars):
        return None
    side = cost.spread / 2 + cost.slippage
    entry = bars[i + 1].open + d * side
    risk = (entry - stop) * d
    if risk <= 0:
        return None
    exit_px, reason, j_exit = None, "time", min(i + H, len(bars) - 1)
    for j in range(i + 1, j_exit + 1):
        b = bars[j]
        if j > i + 1 and (b.open - stop) * d <= 0:  # gapped through the stop at the open
            exit_px, reason, j_exit = b.open - d * cost.slippage, "gap_stop", j
            break
        adverse = b.low if d == 1 else b.high
        if (adverse - stop) * d <= 0:
            exit_px, reason, j_exit = stop - d * cost.slippage, "stop", j
            break
    if exit_px is None:
        exit_px = bars[j_exit].close - d * side
    pnl = (exit_px - entry) * d - cost.commission_price
    return entry, exit_px, pnl / risk, reason, j_exit


def run(bars: list[Bar], instrument: str, cost: CostModel, p: FinderParams | None = None, use_validator=False,
        use_arbiter=False, independence=0.8, start_ts: int | None = None, end_ts: int | None = None, bar_seconds=86400,
        validator_params=None) -> list[Trade]:
    p = p or FinderParams()
    v = Validator({instrument: cost}, validator_params, channel=p.channel, horizon=p.horizon_bars, stop_atr=p.stop_atr) if use_validator else None
    arb = Arbiter(measured_independence=independence)
    finder = Finder(p, source="research")
    out: list[Trade] = []
    busy_until = -1
    for i, d, stop in signals(bars, p):
        ts = bars[i].ts
        if (start_ts and ts < start_ts) or (end_ts and ts >= end_ts) or i <= busy_until:
            continue
        vv = av = None
        if use_validator:
            now = ts + bar_seconds
            hist = bars[max(0, i - 2000):i + 1]
            cand = finder.scan(instrument, hist, bar_seconds, now, now)
            if cand is None:
                continue
            rep = v.validate(cand, hist, bar_seconds, now)
            vv = rep.verdict.value
            if use_arbiter:
                av = arb.decide(cand, rep, now).verdict.value
                if av != ArbiterVerdict.APPROVE_FOR_RISK_REVIEW.value:
                    continue
            elif rep.verdict is not ValidationVerdict.VALID:
                continue
        sim = simulate(bars, i, d, stop, p.horizon_bars, cost)
        if sim is None:
            continue
        entry, ex, r, reason, j = sim
        out.append(Trade(instrument, ts, d, entry, ex, r, reason, vv, av))
        busy_until = j
    return out


def validator_labels(bars, instrument, cost, p=None, bar_seconds=86400, start_ts=None, end_ts=None):
    """Every Finder signal with the Validator verdict AND the realised outcome (for independence)."""
    p = p or FinderParams()
    v = Validator({instrument: cost}, channel=p.channel, horizon=p.horizon_bars, stop_atr=p.stop_atr)
    finder = Finder(p, source="research")
    out = []
    for i, d, stop in signals(bars, p):
        ts = bars[i].ts
        if (start_ts and ts < start_ts) or (end_ts and ts >= end_ts):
            continue
        now = ts + bar_seconds
        hist = bars[max(0, i - 2000):i + 1]
        cand = finder.scan(instrument, hist, bar_seconds, now, now)
        sim = simulate(bars, i, d, stop, p.horizon_bars, cost)
        if cand is None or sim is None:
            continue
        rep = v.validate(cand, hist, bar_seconds, now)
        out.append({"validator_valid": rep.verdict is ValidationVerdict.VALID, "verdict": rep.verdict.value, "r": sim[2], "ts": ts})
    return out


def stats(rs: list[float]) -> dict:
    n = len(rs)
    if n == 0:
        return {"n": 0}
    mean = statistics.fmean(rs)
    sd = statistics.pstdev(rs) if n > 1 else 0.0
    wins = [x for x in rs if x > 0]
    losses = [x for x in rs if x <= 0]
    gp, gl = sum(wins), -sum(losses)
    eq, peak, mdd = 0.0, 0.0, 0.0
    for x in rs:
        eq += x
        peak = max(peak, eq)
        mdd = max(mdd, peak - eq)
    down = [min(0.0, x) for x in rs]
    dsd = math.sqrt(sum(x * x for x in down) / n)
    return {
        "n": n, "expectancy_r": round(mean, 4), "t_stat": round(mean / (sd / math.sqrt(n)), 3) if sd > 0 else None,
        "profit_factor": round(gp / gl, 3) if gl > 0 else None, "win_rate": round(len(wins) / n, 3),
        "avg_win_r": round(statistics.fmean(wins), 3) if wins else 0.0, "avg_loss_r": round(statistics.fmean(losses), 3) if losses else 0.0,
        "max_drawdown_r": round(mdd, 2), "total_r": round(sum(rs), 2),
        "sharpe_per_trade": round(mean / sd, 4) if sd > 0 else None, "sortino_per_trade": round(mean / dsd, 4) if dsd > 0 else None,
    }


def block_bootstrap_p5(rs: list[float], block=20, n=5000, seed=1) -> float:
    if len(rs) < block * 2:
        return float("nan")
    rnd = random.Random(seed)
    k = math.ceil(len(rs) / block)
    means = []
    for _ in range(n):
        sample = []
        for _ in range(k):
            s = rnd.randrange(0, len(rs) - block + 1)
            sample.extend(rs[s:s + block])
        means.append(statistics.fmean(sample[:len(rs)]))
    means.sort()
    return means[int(0.05 * n)]


def future_perturbation_check(bars: list[Bar], instrument: str, cost: CostModel, samples=25, seed=3, bar_seconds=86400) -> dict:
    """Leakage probe: decisions at bar i must not change when bars AFTER i are altered."""
    rnd = random.Random(seed)
    p = FinderParams()
    finder = Finder(p, source="leak")
    val = Validator({instrument: cost})
    sigs = list(signals(bars, p))
    picks = rnd.sample(sigs, min(samples, len(sigs)))
    changed = 0
    for i, d, stop in picks:
        now = bars[i].ts + bar_seconds
        base = bars[max(0, i - 2000):i + 1]
        poisoned = base + [replace(b, close=b.close * 3, high=b.high * 3, open=b.open * 3, low=b.low * 3) for b in bars[i + 1:i + 30]]
        c1 = finder.scan(instrument, base, bar_seconds, now, now)
        c2 = finder.scan(instrument, poisoned, bar_seconds, now, now)
        v1 = val.validate(c1, base, bar_seconds, now).verdict if c1 else None
        v2 = val.validate(c2, [b for b in poisoned if b.ts + bar_seconds <= now], bar_seconds, now).verdict if c2 else None
        same = (c1 is None) == (c2 is None) and (c1 is None or (c1.direction, c1.evidence) == (c2.direction, c2.evidence)) and v1 == v2
        changed += not same
    return {"probes": len(picks), "decisions_changed_by_future_data": changed}


def lookahead_sensitivity(bars, instrument, cost) -> dict:
    """Positive control: a deliberately leaky variant (enter at the signal bar's OPEN, i.e. before the
    signal is known) must look much better. If it does not, the harness cannot detect leakage."""
    p = FinderParams()
    honest = [simulate(bars, i, d, stop, p.horizon_bars, cost) for i, d, stop in signals(bars, p)]
    leaky = [simulate(bars, i - 1, d, stop, p.horizon_bars, cost) for i, d, stop in signals(bars, p)]
    h = [x[2] for x in honest if x]
    lk = [x[2] for x in leaky if x]
    return {"honest_expectancy_r": round(statistics.fmean(h), 4), "leaky_expectancy_r": round(statistics.fmean(lk), 4)}


def random_entry_null(bars, instrument, cost, n=2000, seed=5) -> dict:
    """Null model: random direction at random bars. Expectancy should be about -costs."""
    rnd = random.Random(seed)
    p = FinderParams()
    rs = []
    for _ in range(n):
        i = rnd.randrange(p.min_bars, len(bars) - p.horizon_bars - 2)
        d = rnd.choice((1, -1))
        atr = atr_sma(bars[i - p.atr_n - 1:i + 1], p.atr_n)
        sim = simulate(bars, i, d, bars[i].close - d * p.stop_atr * atr, p.horizon_bars, cost)
        if sim:
            rs.append(sim[2])
    return stats(rs)
