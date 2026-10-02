"""Pipeline router. Owns no decision; every decision comes from its authority and is journaled.

Order per cycle:
  lease -> control/reconcile -> clock drift -> broker truth -> account compliance (veto)
  -> account risk (veto) -> required exits -> per instrument:
     finder -> news (veto) -> validator -> arbiter -> risk (size/veto) -> compliance (veto)
     -> news re-check -> permit -> execution   (shadow mode stops before execution)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from sentinel.arbiter import Arbiter
from sentinel.authority import PermitIssuer
from sentinel.broker.base import BrokerError, BrokerUncertain
from sentinel.compliance import ComplianceEngine
from sentinel.contracts import ArbiterVerdict, Direction, GateVerdict, OrderIntent
from sentinel.control import ControlState
from sentinel.execution import ExecutionRefused, ExecutionService
from sentinel.finder import Finder
from sentinel.journal import Journal
from sentinel.lease import Lease, LeaseLost
from sentinel.news import NewsEngine
from sentinel.reconcile import Reconciler
from sentinel.risk import RiskEngine
from sentinel.runtime import crashpoint, new_id
from sentinel.validator import Validator


@dataclass
class Components:
    journal: Journal
    broker: object
    lease: Lease
    control: ControlState
    reconciler: Reconciler
    compliance: ComplianceEngine
    news: NewsEngine
    risk: RiskEngine
    finder: Finder
    validator: Validator
    arbiter: Arbiter
    issuer: PermitIssuer
    execution: ExecutionService | None  # None in SHADOW mode: no execution path exists at all
    market: object  # .bars(instrument, now) -> (bars, bar_seconds, retrieved_at, source); .market_open(instrument, now)
    instruments: list = field(default_factory=list)
    max_clock_drift_s: int = 5
    commission_per_lot: float = 5.0


class Orchestrator:
    def __init__(self, c: Components, mode: str):
        assert mode in ("shadow", "paper")
        if mode == "shadow" and c.execution is not None:
            raise ValueError("shadow mode must be constructed without an execution service")
        self.c = c
        self.mode = mode

    def _j(self, type_, payload, corr=None, comp="orchestrator"):
        return self.c.journal.append(type_, comp, payload, correlation_id=corr)

    def cycle(self, now: int) -> dict:
        c = self.c
        crashpoint("cycle_start")
        try:
            c.lease.assert_leader()
        except LeaseLost as e:
            return {"status": "STANDBY", "reason": str(e)}
        ok, why = c.control.trading_permitted()
        if not ok and ("reconcile" in why):
            res = c.reconciler.run()
            ok, why = c.control.trading_permitted()
            if not res["clean"]:
                return {"status": "HALTED", "reason": "reconcile not clean", "findings": res["findings"]}
        try:
            drift = c.broker.server_time() - now
        except BrokerUncertain as e:
            c.control.halt("orchestrator", f"broker unreachable: {e}", scope="reconcile")
            return {"status": "HALTED", "reason": "broker unreachable"}
        if abs(drift) > c.max_clock_drift_s:
            c.control.halt("orchestrator", f"clock drift {drift}s vs broker", scope="reconcile")
            return {"status": "HALTED", "reason": f"clock drift {drift}s"}
        # keep closed-position facts current from broker truth
        rec = c.reconciler.run(record_if_clean=False)
        if not rec["clean"]:
            return {"status": "HALTED", "reason": "reconcile not clean", "findings": rec["findings"]}
        ok, why = c.control.trading_permitted()
        if not ok:
            return {"status": "HALTED", "reason": why}
        try:
            snap = c.broker.account()
            deals = c.broker.deals_since(c.compliance.cfg.account_start_utc)
        except BrokerUncertain as e:
            c.control.halt("orchestrator", f"broker truth unavailable: {e}", scope="reconcile")
            return {"status": "HALTED", "reason": "broker truth unavailable"}
        specs = {i: c.broker.spec(i) for i in c.instruments}

        comp_acct = c.compliance.evaluate_account(snap, deals, now)
        self._j("COMPLIANCE_ACCOUNT", {"decision": comp_acct}, comp="compliance")
        if comp_acct.verdict is GateVerdict.HALT:
            c.control.halt("compliance", "; ".join(comp_acct.reasons), scope="manual")
            return {"status": "HALTED", "reason": comp_acct.reasons}
        risk_acct = c.risk.account_check(snap, deals, now)
        self._j("RISK_ACCOUNT", {"decision": risk_acct}, comp="risk")
        if risk_acct.verdict is GateVerdict.HALT:
            c.control.halt("risk", "; ".join(risk_acct.reasons), scope=risk_acct.detail.get("scope", "manual"))
            return {"status": "HALTED", "reason": risk_acct.reasons}

        exits = self._exits(snap, specs, now)
        results = {"status": "OK", "exits": exits, "candidates": []}
        if comp_acct.verdict is not GateVerdict.ALLOW or risk_acct.verdict is not GateVerdict.ALLOW:
            results["status"] = "NO_NEW_TRADES"
            return results
        for inst in c.instruments:
            results["candidates"].append(self._instrument(inst, specs, now))
        return results

    # ------------------------------------------------------------------------------------
    def _exits(self, snap, specs, now) -> list:
        c = self.c
        out = []
        demands = c.compliance.required_flattening(snap, specs, now)
        horizon = {}
        for ev in c.journal.events(type_="CANDIDATE"):
            horizon[ev.payload["candidate"]["candidate_id"]] = ev.payload
        intent_by_ticket = {}
        for ev in c.journal.events(type_="ORDER_ACKED"):
            intent_by_ticket[ev.payload["ack"]["ticket"]] = ev.payload["intent_id"]
        for ev in c.journal.events(type_="ORDER_RESOLVED"):
            if ev.payload["outcome"] == "CONFIRMED":
                intent_by_ticket[ev.payload["ticket"]] = ev.payload["intent_id"]
        for p in snap.positions:
            reason = next((r for t, r in demands if t == p.ticket), None)
            authority = "compliance"
            if reason is None:
                iid = intent_by_ticket.get(p.ticket)
                ip = next((e.payload for e in c.journal.events(type_="INTENT_PERSISTED", correlation_id=iid)), None) if iid else None
                if ip:
                    cand = horizon.get(ip["intent"]["candidate_id"])
                    if cand:
                        _, bar_s, _, _ = c.market.bars(p.instrument, now)
                        if now - p.open_time >= cand["candidate"]["horizon_bars"] * bar_s:
                            reason, authority = "horizon reached", "exit_rule"
            if reason is None or c.execution is None:
                continue
            crashpoint("exit_before_close")
            cd = c.compliance.evaluate_close(specs[p.instrument], now)
            self._j("COMPLIANCE_CLOSE", {"ticket": p.ticket, "decision": cd}, comp="compliance")
            try:
                r = c.execution.close(p.ticket, reason, authority, cd)
            except ExecutionRefused as e:
                r = {"status": "REFUSED", "error": str(e)}
            out.append({"ticket": p.ticket, "reason": reason, "result": r})
        return out

    def _outcome(self, cand_id, stage, verdict, reasons):
        self._j("CANDIDATE_OUTCOME", {"candidate_id": cand_id, "stopped_at": stage, "verdict": verdict, "reasons": list(reasons)}, cand_id)
        return {"candidate_id": cand_id, "stage": stage, "verdict": verdict}

    def _instrument(self, inst, specs, now) -> dict:
        c = self.c
        if not c.market.market_open(inst, now):
            return {"instrument": inst, "stage": "market_closed"}
        bars, bar_s, retrieved_at, source = c.market.bars(inst, now)
        cand = c.finder.scan(inst, bars, bar_s, now, retrieved_at)
        if cand is None:
            return {"instrument": inst, "stage": "no_setup"}
        self._j("CANDIDATE", {"candidate": cand}, cand.candidate_id, comp="finder")
        crashpoint("after_candidate")
        spec = specs[inst]
        nd = c.news.evaluate(inst, spec.currencies, now, horizon_s=0)
        self._j("NEWS_DECISION", {"decision": nd}, cand.candidate_id, comp="news")
        if nd.verdict is not GateVerdict.ALLOW:
            return self._outcome(cand.candidate_id, "news", nd.verdict.value, nd.reasons)
        crashpoint("during_validation")
        vr = c.validator.validate(cand, bars, bar_s, now, market_open=True)
        self._j("VALIDATION", {"report": vr}, cand.candidate_id, comp="validator")
        ad = c.arbiter.decide(cand, vr, now)
        self._j("ARBITER_DECISION", {"decision": ad}, cand.candidate_id, comp="arbiter")
        crashpoint("after_arbiter")
        if ad.verdict is not ArbiterVerdict.APPROVE_FOR_RISK_REVIEW:
            return self._outcome(cand.candidate_id, "arbiter", ad.verdict.value, ad.reasons)
        crashpoint("before_risk")
        try:
            snap = c.broker.account()
            deals = c.broker.deals_since(c.compliance.cfg.account_start_utc)
            q = c.broker.quote(inst)
        except (BrokerUncertain, BrokerError) as e:
            return self._outcome(cand.candidate_id, "risk", "BLOCK", [f"broker truth unavailable: {e}"])
        rd = c.risk.evaluate(snap, deals, specs, spec, q, cand.direction, cand.entry_reference, cand.invalidation_price, now)
        self._j("RISK_DECISION", {"decision": rd}, cand.candidate_id, comp="risk")
        crashpoint("after_risk")
        if rd.verdict not in (GateVerdict.ALLOW, GateVerdict.REDUCE):
            if rd.verdict is GateVerdict.HALT:
                c.control.halt("risk", "; ".join(rd.reasons), scope=rd.detail.get("scope", "manual"))
            return self._outcome(cand.candidate_id, "risk", rd.verdict.value, rd.reasons)
        fill = rd.detail["fill_ref"]
        open_risk = c.risk.open_risk(snap, specs)
        cd = c.compliance.evaluate_trade(snap, deals, now, spec, rd.approved_volume, fill, cand.invalidation_price,
                                         c.commission_per_lot, open_risk if math.isfinite(open_risk) else 1e18)
        self._j("COMPLIANCE_DECISION", {"decision": cd}, cand.candidate_id, comp="compliance")
        if cd.verdict is not GateVerdict.ALLOW:
            if cd.verdict is GateVerdict.HALT:
                c.control.halt("compliance", "; ".join(cd.reasons), scope="manual")
            return self._outcome(cand.candidate_id, "compliance", cd.verdict.value, cd.reasons)
        nd2 = c.news.evaluate(inst, spec.currencies, now)
        if nd2.verdict is not GateVerdict.ALLOW:
            self._j("NEWS_DECISION", {"decision": nd2, "recheck": True}, cand.candidate_id, comp="news")
            return self._outcome(cand.candidate_id, "news_recheck", nd2.verdict.value, nd2.reasons)
        intent = OrderIntent(new_id("intent"), cand.candidate_id, inst, cand.direction, rd.approved_volume,
                             cand.invalidation_price, None, 20, now, cand.expires_at)
        crashpoint("before_permit")
        permit = c.issuer.issue(intent, ad, cd, nd2, rd, now)
        self._j("PERMIT_ISSUED", {"permit_id": permit.permit_id, "intent": intent}, cand.candidate_id, comp="orchestrator")
        if self.mode == "shadow":
            return self._outcome(cand.candidate_id, "shadow", "WOULD_EXECUTE", [f"{intent.direction.value} {intent.volume} {inst}"])
        try:
            r = c.execution.submit(permit)
        except ExecutionRefused as e:
            return self._outcome(cand.candidate_id, "execution", "REFUSED", [str(e)])
        crashpoint("after_submit_before_outcome")
        return self._outcome(cand.candidate_id, "execution", r["status"], [str(r)])
