"""Independent Validator: tries to FALSIFY a candidate. It never proposes trades.

It re-derives what it needs from raw bars with its own estimators (Wilder ATR,
efficiency ratio) and only uses bars strictly before the candidate's signal bar
for its statistical test. Shared inputs with the Finder are declared in
``VALIDATOR_FEATURES`` so independence can be measured, not assumed.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import UTC, datetime

from sentinel.contracts import Bar, Direction, TradeCandidate, ValidationReport, ValidationVerdict
from sentinel.features import atr_wilder, dataset_hash, efficiency_ratio, percentile_rank, true_ranges

VALIDATOR_VERSION = "falsifier_v1"
VALIDATOR_FEATURES = ("close", "high", "low", "donchian", "atr_wilder", "efficiency_ratio", "historical_analog_returns")
REQUIRED_CHECKS = ("data_integrity", "no_future_data", "freshness", "stop_sanity", "spike_explanation", "regime", "cost_survival")


@dataclass(frozen=True)
class CostModel:
    spread: float  # price units
    commission_price: float  # round-trip commission expressed in price units per unit volume
    slippage: float  # price units per side


@dataclass(frozen=True)
class ValidatorParams:
    lookback: int = 1500
    min_history: int = 500
    min_analogs: int = 30
    min_t_stat: float = 1.0
    min_edge_r: float = 0.05
    max_gap_bars: int = 3
    max_bar_move_atr: float = 15.0
    spike_range_atr: float = 4.0
    min_efficiency: float = 0.25
    max_vol_rank: float = 0.98
    stop_atr_bounds: tuple = (0.5, 5.0)
    max_staleness_bars: float = 2.5


def _weekend_gap(a: int, b: int) -> bool:
    """A gap is excused if it spans a Saturday and is at most 3.5 days (weekend / holiday)."""
    if b - a > 3.5 * 86400:
        return False
    t = a
    while t <= b:
        if datetime.fromtimestamp(t, UTC).weekday() == 5:
            return True
        t += 3600
    return False


class Validator:
    def __init__(self, costs: dict, params: ValidatorParams | None = None, channel: int = 20, horizon: int = 10, stop_atr: float = 2.0):
        self.costs = costs
        self.p = params or ValidatorParams()
        self.channel = channel
        self.horizon = horizon
        self.stop_atr = stop_atr

    def validate(self, c: TradeCandidate, bars: list[Bar], bar_seconds: int, now: int, market_open: bool = True) -> ValidationReport:
        checks: dict = {}
        p = self.p

        def done(verdict, reasons):
            return ValidationReport(c.candidate_id, verdict, checks, tuple(reasons), VALIDATOR_VERSION, VALIDATOR_FEATURES,
                                    (dataset_hash([b for b in bars if b.ts <= c.evidence.get("signal_bar_ts", -1)]),), now)

        # data integrity -------------------------------------------------------------------
        bad = []
        for i, b in enumerate(bars):
            if b.instrument != c.instrument:
                bad.append("foreign instrument bar")
            if not all(math.isfinite(x) and x > 0 for x in (b.open, b.high, b.low, b.close)):
                bad.append(f"non-positive/NaN price at {b.ts}")
            elif not (b.low <= min(b.open, b.close) and b.high >= max(b.open, b.close)):
                bad.append(f"OHLC inconsistent at {b.ts}")
            if i and b.ts <= bars[i - 1].ts:
                bad.append(f"out-of-order or duplicate bar at {b.ts}")
        checks["data_integrity"] = {"passed": not bad, "detail": "; ".join(bad[:3])}
        if bad:
            return done(ValidationVerdict.INVALID, ["corrupted input data"])

        future = [b for b in bars if b.ts + bar_seconds > now]
        checks["no_future_data"] = {"passed": not future, "detail": f"{len(future)} unfinished/future bars supplied"}
        if future:
            return done(ValidationVerdict.INVALID, ["bars from the future / unfinished bars in input"])

        sig_ts = c.evidence.get("signal_bar_ts")
        hist = [b for b in bars if sig_ts is not None and b.ts <= sig_ts]
        if not hist or hist[-1].ts != sig_ts:
            checks["freshness"] = {"passed": False, "detail": "signal bar not present in validator data"}
            return done(ValidationVerdict.INDETERMINATE, ["cannot locate signal bar"])
        stale_bars = (now - (hist[-1].ts + bar_seconds)) / bar_seconds
        fresh = stale_bars <= p.max_staleness_bars and market_open
        checks["freshness"] = {"passed": fresh, "detail": f"{stale_bars:.2f} bars since signal close; market_open={market_open}"}
        if not fresh:
            return done(ValidationVerdict.INDETERMINATE, ["signal is stale or market closed"])

        window = hist[-(p.lookback + 1):]
        if len(window) < p.min_history:
            checks["cost_survival"] = {"passed": None, "detail": f"only {len(window)} bars of history"}
            return done(ValidationVerdict.INDETERMINATE, [f"insufficient history ({len(window)} < {p.min_history} bars)"])
        gaps = [i for i in range(1, len(window))
                if window[i].ts - window[i - 1].ts > p.max_gap_bars * bar_seconds
                and not _weekend_gap(window[i - 1].ts, window[i].ts)]
        atr = atr_wilder(window, 14)
        if not math.isfinite(atr) or atr <= 0:
            checks["stop_sanity"] = {"passed": None, "detail": "ATR unavailable"}
            return done(ValidationVerdict.INDETERMINATE, ["insufficient history for ATR"])
        jumps = [b.ts for i, b in enumerate(window[1:], 1)
                 if abs(b.close - window[i - 1].close) > p.max_bar_move_atr * atr
                 and not _weekend_gap(window[i - 1].ts, b.ts)]  # a weekend gap-open is real, not a bad tick
        if jumps:
            checks["data_integrity"] = {"passed": False, "detail": f"impossible jump(s) at {jumps[:3]}"}
            return done(ValidationVerdict.INVALID, ["price jump inconsistent with volatility (bad tick?)"])
        if len(gaps) > 5:
            checks["data_integrity"] = {"passed": False, "detail": f"{len(gaps)} large gaps"}
            return done(ValidationVerdict.INDETERMINATE, ["history has too many gaps"])

        # stop sanity -------------------------------------------------------------------
        dist = (c.entry_reference - c.invalidation_price) * c.direction.sign
        lo, hi = p.stop_atr_bounds
        ok = lo * atr <= dist <= hi * atr
        checks["stop_sanity"] = {"passed": ok, "detail": f"stop {dist / atr:.2f} ATR (wilder)"}
        if not ok:
            return done(ValidationVerdict.INVALID, ["invalidation distance implausible vs independent ATR"])

        # alternative explanation: one-bar spike (likely news / bad print) ------------------
        last = window[-1]
        spike = (last.high - last.low) > p.spike_range_atr * atr
        checks["spike_explanation"] = {"passed": not spike, "detail": f"signal bar range {(last.high - last.low) / atr:.2f} ATR"}
        if spike:
            return done(ValidationVerdict.INVALID, ["signal explained by a single-bar spike"])

        # regime -------------------------------------------------------------------------
        closes = [b.close for b in window]
        er = efficiency_ratio(closes, 30)
        # volatility REGIME before the signal bar (the signal bar itself is judged by the spike check)
        trs = true_ranges(window[-266:-1])
        means = [sum(trs[i - 14:i]) / 14 for i in range(14, len(trs) + 1)]
        vr = percentile_rank(means, means[-1]) if means else math.nan
        regime_ok = er >= p.min_efficiency and vr <= p.max_vol_rank
        checks["regime"] = {"passed": regime_ok, "detail": f"efficiency={er:.3f} vol_rank={vr:.3f}"}
        if not regime_ok:
            return done(ValidationVerdict.INVALID, ["regime does not support continuation (choppy or extreme volatility)"])

        # cost survival via strictly-prior analogs ---------------------------------------
        cost = self.costs.get(c.instrument)
        if cost is None:
            checks["cost_survival"] = {"passed": None, "detail": "no cost model"}
            return done(ValidationVerdict.INDETERMINATE, ["no cost model for instrument"])
        rs = self.analog_returns(window[:-1], c.direction, cost)
        n = len(rs)
        if n < p.min_analogs:
            checks["cost_survival"] = {"passed": None, "detail": f"only {n} analogs"}
            return done(ValidationVerdict.INDETERMINATE, [f"insufficient analogs ({n})"])
        mean = statistics.fmean(rs)
        sd = statistics.pstdev(rs) or 1e-9
        t = mean / (sd / math.sqrt(n))
        passed = mean >= p.min_edge_r and t >= p.min_t_stat
        checks["cost_survival"] = {"passed": passed, "detail": f"n={n} mean_R={mean:.3f} t={t:.2f}",
                                   "n": n, "mean_r": round(mean, 4), "t_stat": round(t, 3)}
        if not passed:
            return done(ValidationVerdict.INVALID, ["historical analogs do not survive costs with statistical support"])
        return done(ValidationVerdict.VALID, ["no falsifying evidence found"])

    def analog_returns(self, bars: list[Bar], direction: Direction, cost: CostModel) -> list[float]:
        """Net R of past breakouts in the same direction, each fully resolved before the signal bar."""
        out = []
        N, H = self.channel, self.horizon
        next_free = 0
        for i in range(max(N + 15, 30), len(bars) - H):
            if i < next_free:
                continue  # analogs must not overlap: overlapping outcomes are not independent samples
            win = bars[i - N : i]
            b = bars[i]
            if direction is Direction.LONG and not b.close > max(x.high for x in win):
                continue
            if direction is Direction.SHORT and not b.close < min(x.low for x in win):
                continue
            atr = sum(true_ranges(bars[i - 14 : i + 1])[1:]) / 14
            if atr <= 0:
                continue
            stop_d = self.stop_atr * atr
            entry = b.close + direction.sign * (cost.spread / 2 + cost.slippage)
            exit_px = None
            for f in bars[i + 1 : i + H + 1]:
                adverse = f.low if direction is Direction.LONG else f.high
                if (adverse - (entry - direction.sign * stop_d)) * direction.sign <= 0:
                    worst = f.open if (f.open - (entry - direction.sign * stop_d)) * direction.sign < 0 else entry - direction.sign * stop_d
                    exit_px = worst - direction.sign * cost.slippage
                    break
            if exit_px is None:
                exit_px = bars[i + H].close - direction.sign * (cost.spread / 2 + cost.slippage)
            r = ((exit_px - entry) * direction.sign - cost.commission_price) / stop_d
            out.append(r)
            next_free = i + H
        return out
