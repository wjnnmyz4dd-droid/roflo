"""FTMO compliance authority (separate from risk). Policy is versioned DATA.

Fail-closed cases: no profile, unknown profile, schema violation, contradictory
values, expired validity window, unreviewed profile outside simulation, missing
account parameters, broker-truth inputs unavailable, calendar UNKNOWN when the
profile has a news restriction.

Day-start balance and the end-of-day trailing high-water mark are recomputed from
broker deal history on every evaluation, so a restart cannot reset them.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sentinel.contracts import AccountSnapshot, GateDecision, GateVerdict, InstrumentSpec, content_hash
from sentinel.news import NewsEngine
from sentinel.runtime import FTMO_TZ, cet_midnight_utc

POLICY_DIR = os.path.join(os.path.dirname(__file__), "policy")
REQUIRED_RULE_KEYS = {
    "max_daily_loss_pct_of_initial", "daily_reset_tz", "daily_base", "max_loss_pct_of_initial", "max_loss_mode",
    "equity_definition", "news_restriction", "weekend_close_required",
}


class PolicyError(RuntimeError):
    pass


def load_profile(profile_id: str, policy_dir: str = POLICY_DIR) -> dict:
    path = os.path.join(policy_dir, f"{profile_id}.json")
    if not os.path.exists(path):
        raise PolicyError(f"profile {profile_id} not found")
    with open(path) as f:
        prof = json.load(f)
    validate_profile(prof)
    prof["_hash"] = content_hash({k: v for k, v in prof.items() if k != "_hash"})
    return prof


def validate_profile(p: dict) -> None:
    for k in ("profile_id", "version", "phase", "account_type", "rules", "sources", "review", "valid_from", "valid_until"):
        if k not in p:
            raise PolicyError(f"profile missing {k}")
    r = p["rules"]
    missing = REQUIRED_RULE_KEYS - set(r)
    if missing:
        raise PolicyError(f"rules missing {sorted(missing)}")
    unknown = set(r) - REQUIRED_RULE_KEYS
    if unknown:
        raise PolicyError(f"unknown rule keys {sorted(unknown)} (refusing to ignore rules)")
    d, m = r["max_daily_loss_pct_of_initial"], r["max_loss_pct_of_initial"]
    if not (isinstance(d, (int, float)) and isinstance(m, (int, float)) and 0 < d <= m < 1):
        raise PolicyError(f"contradictory loss limits daily={d} max={m}")
    if r["max_loss_mode"] not in ("static", "eod_trailing"):
        raise PolicyError(f"unknown max_loss_mode {r['max_loss_mode']}")
    if r["daily_reset_tz"] != "Europe/Prague" or r["daily_base"] != "balance_at_midnight":
        raise PolicyError("unsupported day definition")
    if not p["sources"] or any(not s.get("url") or not s.get("sha256") for s in p["sources"]):
        raise PolicyError("profile lacks source provenance")
    nr = r["news_restriction"]
    if nr is not None:
        for k in ("window_before_s", "window_after_s", "restricted_titles", "affected"):
            if k not in nr:
                raise PolicyError(f"news_restriction missing {k}")


@dataclass(frozen=True)
class AccountConfig:
    profile_id: str
    initial_capital: float
    account_start_utc: int
    simulation: bool  # unreviewed profiles are usable only against a simulated broker
    safety_buffer_fraction: float = 0.25  # stop at 75% of any FTMO allowance
    news_no_new_positions_before_s: int = 3600
    news_flatten_before_s: int = 600
    weekend_no_new_after_utc: tuple = (4, 18)  # Friday 18:00 UTC
    weekend_flatten_after_utc: tuple = (4, 20)  # Friday 20:00 UTC


class ComplianceEngine:
    def __init__(self, cfg: AccountConfig, profile: dict | None, news: NewsEngine, signer=None, wall_clock=None):
        """``wall_clock`` judges whether the rule text is still current (real time);
        ``now`` arguments are market time (simulated during replay)."""
        import time as _time

        self.wall_clock = wall_clock or _time
        self.signer = signer
        self.cfg = cfg
        self.profile = profile
        self.news = news

    # ---------- broker-truth derived state ---------------------------------------------------
    @staticmethod
    def balance_at(snapshot_balance: float, deals: list, ts: int) -> float:
        """Balance at instant ts = balance now minus every realised cash flow after ts."""
        flow = sum((d.get("profit") or 0.0) - (d.get("commission") or 0.0) + (d.get("swap") or 0.0)
                   for d in deals if d["ts"] >= ts)
        return snapshot_balance - flow

    def limits(self, snap: AccountSnapshot, deals: list, now: int) -> dict:
        r = self.profile["rules"]
        init = self.cfg.initial_capital
        midnight = cet_midnight_utc(now)
        if midnight <= self.cfg.account_start_utc:
            day_base = init
        else:
            day_base = self.balance_at(snap.balance, deals, midnight)
        daily_limit = day_base - r["max_daily_loss_pct_of_initial"] * init
        if r["max_loss_mode"] == "static":
            max_limit = init - r["max_loss_pct_of_initial"] * init
            hwm = init
        else:
            hwm = init
            day = datetime.fromtimestamp(midnight, UTC).astimezone(FTMO_TZ)
            start = datetime.fromtimestamp(self.cfg.account_start_utc, UTC).astimezone(FTMO_TZ)
            while day > start:
                hwm = max(hwm, self.balance_at(snap.balance, deals, int(day.astimezone(UTC).timestamp())))
                day = (day - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            max_limit = hwm - r["max_loss_pct_of_initial"] * init
        return {"day_base": day_base, "daily_limit": daily_limit, "max_limit": max_limit, "hwm": hwm,
                "midnight_utc": midnight}

    # ---------- checks ----------------------------------------------------------------------
    def _profile_ok(self, now: int) -> tuple[bool, str]:
        if self.profile is None:
            return False, "no compliance profile configured"
        p = self.profile
        if p["profile_id"] != self.cfg.profile_id:
            return False, "profile id mismatch"
        vf = int(datetime.fromisoformat(p["valid_from"]).replace(tzinfo=UTC).timestamp())
        vu = int(datetime.fromisoformat(p["valid_until"]).replace(tzinfo=UTC).timestamp())
        wall = int(self.wall_clock.time()) if hasattr(self.wall_clock, "time") else int(self.wall_clock.now())
        if not (vf <= wall < vu):
            return False, f"profile outside validity window {p['valid_from']}..{p['valid_until']} (re-verify rules)"
        if p["review"].get("status") != "APPROVED" and not self.cfg.simulation:
            return False, "profile not human-reviewed; only simulation permitted"
        if self.cfg.initial_capital <= 0:
            return False, "initial capital not configured"
        return True, "OK"

    def _decision(self, verdict, reasons, inputs, now, detail=None, subject="") -> GateDecision:
        d = GateDecision("compliance", verdict, tuple(reasons), content_hash(inputs), now, detail=detail or {}, subject=subject)
        return self.signer.sign(d) if self.signer else d

    def evaluate_account(self, snap: AccountSnapshot | None, deals: list | None, now: int) -> GateDecision:
        ok, why = self._profile_ok(now)
        if not ok:
            return self._decision(GateVerdict.HALT, [why], [now], now)
        if snap is None or deals is None:
            return self._decision(GateVerdict.BLOCK, ["broker truth unavailable"], [now], now)
        lim = self.limits(snap, deals, now)
        inputs = [snap.snapshot_hash, self.profile["_hash"], lim]
        if snap.equity <= lim["daily_limit"] or snap.equity <= lim["max_limit"]:
            return self._decision(GateVerdict.HALT, [f"FTMO limit breached: equity {snap.equity} vs daily {lim['daily_limit']:.2f} / max {lim['max_limit']:.2f}"], inputs, now, lim)
        allowance_daily = self.profile["rules"]["max_daily_loss_pct_of_initial"] * self.cfg.initial_capital
        buf = self.cfg.safety_buffer_fraction * allowance_daily
        if snap.equity <= lim["daily_limit"] + buf or snap.equity <= lim["max_limit"] + buf:
            return self._decision(GateVerdict.BLOCK, ["within safety buffer of an FTMO loss limit"], inputs, now, lim)
        return self._decision(GateVerdict.ALLOW, ["account within limits"], inputs, now, lim)

    def worst_case_loss(self, spec: InstrumentSpec, volume: float, entry: float, stop: float, slippage_ticks: int = 20) -> float:
        dist = abs(entry - stop) / spec.tick_size + slippage_ticks
        return dist * spec.tick_value * volume

    def evaluate_trade(self, snap: AccountSnapshot | None, deals: list | None, now: int, spec: InstrumentSpec,
                       volume: float, entry: float, stop: float, commission_per_lot: float, open_risk: float,
                       subject: str = "") -> GateDecision:
        acct = self.evaluate_account(snap, deals, now)
        if acct.verdict is not GateVerdict.ALLOW:
            return acct
        lim = acct.detail
        wc = self.worst_case_loss(spec, volume, entry, stop) + 2 * commission_per_lot * volume
        floor = max(lim["daily_limit"], lim["max_limit"])
        buf = self.cfg.safety_buffer_fraction * self.profile["rules"]["max_daily_loss_pct_of_initial"] * self.cfg.initial_capital
        projected = snap.equity - open_risk - wc
        inputs = [snap.snapshot_hash, self.profile["_hash"], spec.instrument, volume, entry, stop, open_risk]
        if projected <= floor + buf:
            return self._decision(GateVerdict.BLOCK, [f"worst case {wc:.2f} + open risk {open_risk:.2f} would cross FTMO floor {floor:.2f} (+buffer)"], inputs, now, lim)
        nb = self._news_rule_block(spec, now, opening=True)
        if nb:
            return self._decision(GateVerdict.BLOCK, [nb], inputs, now, lim)
        wb = self._weekend_block(now, opening=True)
        if wb:
            return self._decision(GateVerdict.BLOCK, [wb], inputs, now, lim)
        return self._decision(GateVerdict.ALLOW, ["trade within FTMO profile"], inputs + [subject], now,
                              dict(lim, instrument=spec.instrument, volume=volume, stop=stop), subject=subject)

    def evaluate_close(self, spec: InstrumentSpec, now: int) -> GateDecision:
        ok, why = self._profile_ok(now)
        if not ok:
            # A missing/expired profile must not trap risk-reducing closes; only the news window can.
            pass
        nb = self._news_rule_block(spec, now, opening=False) if self.profile else None
        if nb:
            return self._decision(GateVerdict.BLOCK, [nb], [spec.instrument, now], now)
        return self._decision(GateVerdict.ALLOW, ["close permitted"], [spec.instrument, now], now)

    def required_flattening(self, snap: AccountSnapshot, specs: dict, now: int) -> list[tuple[str, str]]:
        """Positions compliance requires closed now (before a restricted event / weekend)."""
        out = []
        for p in snap.positions:
            spec = specs.get(p.instrument)
            if spec is None:
                continue
            if self._restricted_soon(spec, now, self.cfg.news_flatten_before_s):
                out.append((p.ticket, "restricted news event approaching"))
            elif self._weekend_block(now, opening=False):
                out.append((p.ticket, "weekend close required by profile"))
        return out

    # ---------- profile-specific rules ------------------------------------------------------
    def _targeted(self, spec: InstrumentSpec, ccy: str) -> bool:
        aff = self.profile["rules"]["news_restriction"]["affected"].get(ccy, [])
        if spec.instrument in aff:
            return True
        return spec.asset_class == "forex" and f"forex:{ccy}" in aff and ccy in spec.currencies

    def _restricted_events(self):
        nr = self.profile["rules"]["news_restriction"]
        snap = self.news.snapshot
        for e in snap.events if snap else []:
            titles = nr["restricted_titles"].get(e.currency, [])
            matched = any(e.title.lower().startswith(t.lower()) for t in titles)
            unmatched_high = e.currency == "USD" and e.impact == "HIGH"  # conservative internal policy
            if matched or unmatched_high:
                yield e

    def _restricted_soon(self, spec: InstrumentSpec, now: int, lead_s: int) -> bool:
        if not self.profile or self.profile["rules"]["news_restriction"] is None:
            return False
        nr = self.profile["rules"]["news_restriction"]
        for e in self._restricted_events():
            if self._targeted(spec, e.currency) and e.scheduled_utc - lead_s - nr["window_before_s"] <= now <= e.scheduled_utc + nr["window_after_s"]:
                return True
        return False

    def _news_rule_block(self, spec: InstrumentSpec, now: int, opening: bool) -> str | None:
        nr = self.profile["rules"]["news_restriction"]
        if nr is None:
            return None
        ok, why = self.news.health(now)
        if not ok:
            return f"profile has a news restriction and calendar is UNKNOWN: {why}"
        for e in self._restricted_events():
            if not self._targeted(spec, e.currency):
                continue
            if e.scheduled_utc - nr["window_before_s"] <= now <= e.scheduled_utc + nr["window_after_s"]:
                return f"FTMO restricted window: {e.currency} '{e.title}'"
            if opening and e.scheduled_utc - self.cfg.news_no_new_positions_before_s <= now < e.scheduled_utc:
                return f"no new positions before restricted event {e.currency} '{e.title}' (internal lead time)"
        return None

    def _weekend_block(self, now: int, opening: bool) -> str | None:
        if not self.profile["rules"]["weekend_close_required"]:
            return None
        d = datetime.fromtimestamp(now, UTC)
        wd, hr = d.weekday(), d.hour
        if wd >= 5:
            return "weekend: market closed, profile forbids weekend holding"
        dl, hl = self.cfg.weekend_no_new_after_utc if opening else self.cfg.weekend_flatten_after_utc
        if wd == dl and hr >= hl:
            return "weekend close approaching (profile requires flat before weekend)"
        return None
