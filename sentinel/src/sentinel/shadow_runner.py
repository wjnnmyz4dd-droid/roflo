"""Continuous SHADOW runner and counterfactual journal.

The runner builds the system in SHADOW mode only (``Components.execution is None``: no order-capable
path exists) and repeats: refresh feeds -> staleness check -> one decision per closed bar -> resolve
counterfactuals. Every candidate is journaled by the orchestrator (CANDIDATE / CANDIDATE_OUTCOME).

Failure handling (all fail CLOSED, i.e. no decision is made, and the reason is journaled):
  * bar feed fetch fails        -> keep previous bars; staleness then decides
  * last closed bar too old     -> FEED_STALE (no decision for that instrument set)
  * calendar refresh fails      -> NewsEngine keeps previous snapshot; its staleness rule blocks
  * restart                     -> state is the on-disk journals; a bar already decided is skipped

Counterfactuals go to a SEPARATE journal (``counterfactual.db``). Nothing reads it back into a
production decision; it is research evidence only.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from sentinel.journal import DuplicateKey, Journal
from sentinel.research import edge
from sentinel.system import DEFAULT_COSTS, ReplayMarket, build

COMPONENT = "shadow_runner"


@dataclass
class ShadowConfig:
    instruments: tuple = ("XAUUSD", "USOIL", "USDJPY", "BTCUSD")
    bar_seconds: int = 3600
    max_feed_age_s: int = 3 * 3600  # Yahoo hourly bars arrive delayed; older than this = stale
    provenance: str = "PROXY / RESEARCH-ONLY"
    account_start_utc: int = 0


class ShadowRunner:
    def __init__(self, workdir: str, cfg: ShadowConfig, fetch_bars, fetch_calendar, clock, wall_clock=None):
        """``fetch_bars() -> {instrument: [Bar]}``; ``fetch_calendar(now) -> CalendarSnapshot``. Either may raise."""
        self.wd = workdir
        self.cfg = cfg
        self.fetch_bars = fetch_bars
        self.fetch_calendar = fetch_calendar
        self.clock = clock
        self.wall_clock = wall_clock
        os.makedirs(workdir, exist_ok=True)
        self.ops = Journal(os.path.join(workdir, "shadow_ops.db"), clock=clock)
        self.cf = Journal(os.path.join(workdir, "counterfactual.db"), clock=clock)
        self.bars: dict = {}
        self.calendar = None

    # ------------------------------------------------------------------ one iteration
    def step(self) -> dict:
        now = int(self.clock.now())
        bs = self.cfg.bar_seconds
        try:
            fresh = self.fetch_bars()
            if fresh:  # keep only bar-aligned, fully CLOSED bars (feeds append an in-progress bar stamped "now")
                self.bars = {i: [b for b in v if b.ts % bs == 0 and b.ts + bs <= now] for i, v in fresh.items()}
        except Exception as e:  # noqa: BLE001 - feed outage: keep previous bars, staleness decides
            self.ops.append("FEED_ERROR", COMPONENT, {"error": repr(e)[:300], "now": now})
        try:
            self.calendar = self.fetch_calendar(now)
        except Exception as e:  # noqa: BLE001 - calendar outage: previous snapshot kept, news gate judges staleness
            self.ops.append("CALENDAR_ERROR", COMPONENT, {"error": repr(e)[:300], "now": now})
        if not self.bars or any(i not in self.bars or not self.bars[i] for i in self.cfg.instruments):
            self.ops.append("NO_DECISION", COMPONENT, {"reason": "no bars", "now": now})
            return {"status": "NO_DATA"}
        last_close = {i: self.bars[i][-1].ts + bs for i in self.cfg.instruments}
        stale = {i: now - t for i, t in last_close.items() if now - t > self.cfg.max_feed_age_s and self._open(i, now)}
        if stale:
            self.ops.append("FEED_STALE", COMPONENT, {"age_s": stale, "now": now, "provenance": self.cfg.provenance})
            return {"status": "FEED_STALE", "age_s": stale}
        decision_bar = max(last_close.values())
        try:
            self.ops.append("SHADOW_CYCLE", COMPONENT, {"decision_bar_close": decision_bar, "now": now,
                                                        "provenance": self.cfg.provenance},
                            unique=[("shadow_bar", str(decision_bar))])
        except DuplicateKey:
            return {"status": "ALREADY_DECIDED", "bar_close": decision_bar}
        b = self._build(now)
        assert b.comps.execution is None, "shadow runner must never hold an execution service"
        res = b.orch.cycle(now)
        b.journal.close()
        self.ops.append("SHADOW_RESULT", COMPONENT, {"decision_bar_close": decision_bar, "result": res})
        resolved = self.resolve_counterfactuals()
        return {"status": "DECIDED", "result": res, "counterfactuals_resolved": resolved}

    def _open(self, instrument: str, now: int) -> bool:
        return ReplayMarket({}, self.cfg.bar_seconds, "", now).market_open(instrument, now)

    def _build(self, now: int):
        market = ReplayMarket(self.bars, self.cfg.bar_seconds, self.cfg.provenance, now)
        b = build(os.path.join(self.wd, "sys"), self.clock, market, list(self.cfg.instruments), mode="shadow",
                  account_start_utc=self.cfg.account_start_utc or now - 86400, news_snapshot=self.calendar,
                  wall_clock=self.wall_clock)
        for i in self.cfg.instruments:
            last = self.bars[i][-1]
            half = DEFAULT_COSTS[i].spread / 2
            b.broker.set_quote(i, last.close - half, last.close + half, now)
        return b

    # ------------------------------------------------------------------ counterfactual journal
    def resolve_counterfactuals(self) -> int:
        """For every journaled candidate whose horizon has fully elapsed in the bars we hold, record what
        the trade would have returned (in R, with DEFAULT_COSTS), whatever its verdict was."""
        j = Journal(os.path.join(self.wd, "sys", "journal-A.db"), clock=self.clock)
        outcomes = {e.correlation_id: e.payload for e in j.events(type_="CANDIDATE_OUTCOME")}
        n = 0
        bs = self.cfg.bar_seconds
        for ev in j.events(type_="CANDIDATE"):
            c = ev.payload["candidate"]
            cid = c["candidate_id"]
            if self.cf.has_key("cf", cid) or cid not in outcomes:
                continue
            bars = self.bars.get(c["instrument"], [])
            idx = [k for k, b in enumerate(bars) if b.ts + bs <= c["created_at"]]
            if not idx:
                continue
            i = idx[-1]
            H = int(c["horizon_bars"])
            if i + H >= len(bars):
                continue  # horizon not yet observable: resolve later, never on partial data
            d = 1 if c["direction"] in ("LONG", 1) else -1
            sim = edge.simulate(bars, i, d, float(c["invalidation_price"]), H, DEFAULT_COSTS[c["instrument"]])
            r = None if sim is None else round(sim[2], 4)
            out = outcomes[cid]
            self.cf.append("COUNTERFACTUAL", COMPONENT,
                           {"candidate_id": cid, "instrument": c["instrument"], "created_at": c["created_at"],
                            "stopped_at": out["stopped_at"], "verdict": out["verdict"], "r": r,
                            "exit_reason": None if sim is None else sim[3], "provenance": self.cfg.provenance},
                           correlation_id=cid, unique=[("cf", cid)])
            n += 1
        j.close()
        return n

    def counterfactual_summary(self) -> dict:
        by: dict = {}
        for e in self.cf.events(type_="COUNTERFACTUAL"):
            p = e.payload
            if p["r"] is None:
                continue
            k = f"{p['stopped_at']}:{p['verdict']}"
            by.setdefault(k, []).append(p["r"])
        return {k: {"n": len(v), "mean_r": round(sum(v) / len(v), 4)} for k, v in sorted(by.items())}
