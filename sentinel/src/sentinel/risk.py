"""The single, non-bypassable capital/risk authority.

Inputs are broker truth (AccountSnapshot, broker deals, broker instrument spec,
broker quote). Persistent state that cannot be derived from the broker (manual
halts) lives in the journal via ControlState. Risk may ALLOW, REDUCE, BLOCK or
HALT. It never creates a trade: it only answers about a proposal it is handed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from sentinel.contracts import AccountSnapshot, Direction, GateDecision, GateVerdict, InstrumentSpec, Quote, content_hash
from sentinel.runtime import cet_midnight_utc


@dataclass(frozen=True)
class RiskLimits:
    risk_per_trade_pct: float = 0.0025  # 0.25% of equity at the stop
    max_open_risk_pct: float = 0.0075  # sum of distance-to-stop losses across positions
    max_positions_total: int = 3
    max_positions_per_instrument: int = 1
    max_positions_per_asset_class: int = 2
    max_same_direction_usd_bucket: int = 2  # crude correlation bucket: USD-denominated risk
    internal_daily_loss_pct: float = 0.015  # tighter than any FTMO profile
    internal_drawdown_halt_pct: float = 0.06
    loss_streak_halt: int = 5
    max_snapshot_age_s: int = 10
    max_quote_age_s: int = 15
    max_spread_to_stop_fraction: float = 0.10  # spread may be at most 10% of stop distance
    min_margin_level: float = 3.0  # equity / margin after the trade
    min_stop_ticks: int = 10


class RiskEngine:
    def __init__(self, limits: RiskLimits, initial_capital: float, signer=None):
        self.signer = signer
        self.limits = limits
        self.initial = initial_capital

    @staticmethod
    def position_risk(p, spec: InstrumentSpec) -> float:
        if p.stop_loss is None:
            return math.inf  # unknown downside: treated as unbounded
        dist = (p.open_price - p.stop_loss) * p.direction.sign
        return max(dist, 0.0) / spec.tick_size * spec.tick_value * p.volume

    def open_risk(self, snap: AccountSnapshot, specs: dict) -> float:
        total = 0.0
        for p in snap.positions:
            spec = specs.get(p.instrument)
            total += math.inf if spec is None else self.position_risk(p, spec)
        return total

    def _decide(self, verdict, reasons, inputs, now, volume=None, detail=None, subject="") -> GateDecision:
        d = GateDecision("risk", verdict, tuple(reasons), content_hash(inputs), now, approved_volume=volume, detail=detail or {},
                         subject=subject)
        return self.signer.sign(d) if self.signer else d

    def account_check(self, snap: AccountSnapshot | None, deals: list | None, now: int, streak_reset_ts: int = 0) -> GateDecision:
        """``streak_reset_ts``: time of the last operator RESUME; losses before it were already reviewed."""
        L = self.limits
        if snap is None or deals is None:
            return self._decide(GateVerdict.BLOCK, ["broker truth unavailable"], [now], now)
        age = now - snap.taken_at
        if age > L.max_snapshot_age_s or age < -5:
            return self._decide(GateVerdict.BLOCK, [f"account snapshot age {age}s invalid"], [snap.snapshot_hash, now], now)
        if not all(isinstance(x, (int, float)) and math.isfinite(x) for x in (snap.equity, snap.balance, snap.margin)) \
                or snap.balance <= 0 or snap.equity <= 0:
            return self._decide(GateVerdict.HALT, ["impossible account values"], [snap.snapshot_hash], now)
        midnight = cet_midnight_utc(now)
        day_flow = sum((d.get("profit") or 0) - (d.get("commission") or 0) + (d.get("swap") or 0)
                       for d in deals if d["ts"] >= midnight)  # swaps are cash flows too (consistent with compliance)
        day_start_balance = snap.balance - day_flow
        day_pnl = snap.equity - day_start_balance  # realised + unrealised since midnight CE(S)T
        dd = (self.initial - snap.equity) / self.initial
        detail = {"day_pnl": round(day_pnl, 2), "day_start_balance": round(day_start_balance, 2), "drawdown": round(dd, 5)}
        inputs = [snap.snapshot_hash, len(deals), now]
        if day_pnl <= -L.internal_daily_loss_pct * self.initial:
            return self._decide(GateVerdict.HALT, [f"internal daily loss limit hit ({day_pnl:.2f})"], inputs, now, detail=detail | {"scope": "day"})
        if dd >= L.internal_drawdown_halt_pct:
            return self._decide(GateVerdict.HALT, [f"internal drawdown halt ({dd:.2%})"], inputs, now, detail=detail | {"scope": "manual"})
        closed = [d for d in deals if str(d["kind"]).startswith("OUT") and (not streak_reset_ts or d["ts"] > streak_reset_ts)]
        streak = 0
        for d in reversed(closed):
            if (d.get("profit") or 0) - (d.get("commission") or 0) + (d.get("swap") or 0) < 0:
                streak += 1
            else:
                break
        if streak >= L.loss_streak_halt:
            # A streak cannot clear itself (no trades while halted), so it needs an operator decision.
            return self._decide(GateVerdict.HALT, [f"loss streak {streak}"], inputs, now, detail=detail | {"scope": "manual"})
        return self._decide(GateVerdict.ALLOW, ["account ok"], inputs, now, detail=detail)

    def evaluate(self, snap: AccountSnapshot | None, deals: list | None, specs: dict, spec: InstrumentSpec,
                 quote: Quote | None, direction: Direction, entry: float, stop: float, now: int, subject: str = "",
                 streak_reset_ts: int = 0) -> GateDecision:
        L = self.limits
        acct = self.account_check(snap, deals, now, streak_reset_ts)
        if acct.verdict is not GateVerdict.ALLOW:
            return acct
        if not all(isinstance(x, (int, float)) and math.isfinite(x) and x > 0 for x in (entry, stop)):
            return self._decide(GateVerdict.BLOCK, ["invalid entry/stop"], [snap.snapshot_hash, spec.instrument, now], now)
        inputs = [snap.snapshot_hash, spec.instrument, direction.value, entry, stop, now]
        if quote is None or now - quote.ts > L.max_quote_age_s or quote.bid <= 0 or quote.ask < quote.bid:
            return self._decide(GateVerdict.BLOCK, ["stale or invalid quote"], inputs, now)
        if not all(math.isfinite(x) and x > 0 for x in (entry, stop)):
            return self._decide(GateVerdict.BLOCK, ["invalid entry/stop"], inputs, now)
        fill = quote.ask if direction is Direction.LONG else quote.bid
        dist = (fill - stop) * direction.sign
        if dist / spec.tick_size < L.min_stop_ticks:
            return self._decide(GateVerdict.BLOCK, ["stop missing, on wrong side, or too close to market"], inputs, now)
        spread = quote.ask - quote.bid
        if spread > L.max_spread_to_stop_fraction * dist:
            return self._decide(GateVerdict.BLOCK, [f"spread {spread} too wide vs stop distance {dist}"], inputs, now)
        pos = snap.positions
        if len(pos) >= L.max_positions_total:
            return self._decide(GateVerdict.BLOCK, ["max positions"], inputs, now)
        if sum(p.instrument == spec.instrument for p in pos) >= L.max_positions_per_instrument:
            return self._decide(GateVerdict.BLOCK, ["instrument already has a position"], inputs, now)
        same_class = sum(specs[p.instrument].asset_class == spec.asset_class for p in pos if p.instrument in specs)
        if same_class >= L.max_positions_per_asset_class:
            return self._decide(GateVerdict.BLOCK, ["asset-class concentration"], inputs, now)
        if "USD" in spec.currencies:
            usd_dir = sum(1 for p in pos if p.instrument in specs and "USD" in specs[p.instrument].currencies
                          and _usd_exposure(specs[p.instrument], p.direction) == _usd_exposure(spec, direction))
            if usd_dir >= L.max_same_direction_usd_bucket:
                return self._decide(GateVerdict.BLOCK, ["correlated USD exposure bucket full"], inputs, now)
        open_r = self.open_risk(snap, specs)
        if not math.isfinite(open_r):
            return self._decide(GateVerdict.BLOCK, ["an open position has no stop or unknown spec: open risk unbounded"], inputs, now)
        budget_trade = L.risk_per_trade_pct * snap.equity
        budget_total = L.max_open_risk_pct * snap.equity - open_r
        budget = min(budget_trade, budget_total)
        if budget <= 0:
            return self._decide(GateVerdict.BLOCK, ["aggregate open-risk budget exhausted"], inputs, now)
        loss_per_lot = (dist / spec.tick_size + 2 * spread / spec.tick_size) * spec.tick_value
        raw = budget / loss_per_lot
        vol = math.floor(raw / spec.volume_step) * spec.volume_step
        vol = round(min(vol, spec.volume_max), 8)
        if vol < spec.volume_min:
            return self._decide(GateVerdict.BLOCK, [f"risk budget allows {raw:.4f} lots < broker minimum {spec.volume_min}"], inputs, now)
        margin_needed = vol * spec.contract_size * fill / 30.0
        if snap.margin + margin_needed > 0 and snap.equity / (snap.margin + margin_needed) < L.min_margin_level:
            return self._decide(GateVerdict.BLOCK, ["margin level would fall below minimum"], inputs, now)
        verdict = GateVerdict.REDUCE if budget_total < budget_trade else GateVerdict.ALLOW
        return self._decide(verdict, [f"approved {vol} lots (budget {budget:.2f})"], inputs, now, volume=vol,
                            detail={"open_risk": round(open_r, 2), "loss_per_lot": round(loss_per_lot, 4), "fill_ref": fill,
                                    "instrument": spec.instrument, "direction": direction.value, "stop": stop},
                            subject=subject)


def _usd_exposure(spec: InstrumentSpec, direction: Direction) -> int:
    """+1 if the position is long USD, -1 if short USD."""
    if spec.currencies and spec.currencies[0] == "USD":
        return direction.sign
    return -direction.sign
