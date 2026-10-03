"""Execution: the only component holding a broker handle that can open or close.

Lifecycle per intent, every step journaled BEFORE the next action:

    INTENT_PERSISTED  (unique permit_id + intent_id -> a permit can never be used twice)
    ORDER_SENT        (written before the network call; marks the attempt as possibly live)
    ORDER_ACKED | ORDER_REJECTED | ORDER_UNKNOWN
    ORDER_RESOLVED    (written by the reconciler for UNKNOWN / unacked SENT intents)

There is no resend. A rejected or unresolved intent is terminal; trading again
requires a new candidate, new gate decisions and a new permit.
"""

from __future__ import annotations

from sentinel.authority import Permit, PermitError, PermitKey, verify_permit
from sentinel.broker.base import BrokerError, BrokerUncertain
from sentinel.contracts import GateDecision, GateVerdict
from sentinel.journal import DuplicateKey, Journal
from sentinel.lease import Lease, LeaseLost
from sentinel.runtime import crashpoint, log

COMPONENT = "execution"

TERMINAL = {"ACKED", "REJECTED", "RESOLVED_CONFIRMED", "RESOLVED_NOT_EXECUTED"}


class ExecutionRefused(RuntimeError):
    pass


def intent_state(journal: Journal, intent_id: str) -> str:
    state = "NONE"
    for ev in journal.events(correlation_id=intent_id):
        if ev.type == "INTENT_PERSISTED":
            state = "PERSISTED"
        elif ev.type == "ORDER_SENT":
            state = "SENT"
        elif ev.type == "ORDER_ACKED":
            state = "ACKED"
        elif ev.type == "ORDER_REJECTED":
            state = "REJECTED"
        elif ev.type == "ORDER_UNKNOWN":
            state = "UNKNOWN"
        elif ev.type == "ORDER_RESOLVED":
            state = "RESOLVED_" + ev.payload["outcome"]
    return state


def unresolved_intents(journal: Journal) -> list[dict]:
    """Intents whose broker outcome is not known (SENT without ack, UNKNOWN, or PERSISTED)."""
    out = []
    for ev in journal.events(type_="INTENT_PERSISTED"):
        iid = ev.payload["intent"]["intent_id"]
        st = intent_state(journal, iid)
        if st not in TERMINAL:
            out.append({"intent_id": iid, "state": st, "intent": ev.payload["intent"]})
    return out


class ExecutionService:
    def __init__(self, broker, journal: Journal, key: PermitKey, lease: Lease, clock, control):
        self._broker = broker
        self._journal = journal
        self._key = key
        self._lease = lease
        self._clock = clock
        self._control = control  # ControlState: knows whether trading is permitted

    def submit(self, permit: Permit) -> dict:
        now = int(self._clock.now())
        try:
            verify_permit(self._key, permit, now)
        except PermitError as e:
            self._journal.append("EXECUTION_REFUSED", COMPONENT, {"permit_id": permit.permit_id, "reason": str(e)},
                                 correlation_id=permit.intent.intent_id)
            raise ExecutionRefused(str(e)) from e
        ok, why = self._control.trading_permitted()
        if not ok:
            self._journal.append("EXECUTION_REFUSED", COMPONENT, {"permit_id": permit.permit_id, "reason": why},
                                 correlation_id=permit.intent.intent_id)
            raise ExecutionRefused(why)
        token = self._lease.assert_leader()
        intent = permit.intent
        try:
            self._journal.append(
                "INTENT_PERSISTED", COMPONENT,
                {"intent": intent, "permit_id": permit.permit_id, "decisions_hash": permit.decisions_hash, "fence": token},
                correlation_id=intent.intent_id,
                unique=[("permit", permit.permit_id), ("intent", intent.intent_id),
                        ("decisions", permit.decisions_hash), ("candidate", intent.candidate_id)],
            )
        except DuplicateKey as e:
            raise ExecutionRefused(f"duplicate submission refused: {e}") from e
        crashpoint("exec_after_intent_persisted")
        self._journal.append("ORDER_SENT", COMPONENT, {"intent_id": intent.intent_id, "fence": token},
                             correlation_id=intent.intent_id)
        crashpoint("exec_before_broker_send")
        try:
            token = self._lease.record_send(intent.intent_id)  # fenced: refuses if leadership was lost meanwhile
        except LeaseLost as e:
            self._journal.append("ORDER_REJECTED", COMPONENT, {"intent_id": intent.intent_id, "error": f"fence: {e}"},
                                 correlation_id=intent.intent_id)
            return {"status": "REJECTED", "error": f"fence: {e}"}
        crashpoint("exec_after_fenced_record_before_send")
        try:
            ack = self._broker.send_order(
                intent.intent_id, intent.instrument, intent.direction, intent.volume, intent.stop_loss,
                intent.take_profit, intent.max_slippage_points, token,
            )
        except BrokerError as e:
            self._journal.append("ORDER_REJECTED", COMPONENT, {"intent_id": intent.intent_id, "error": str(e)},
                                 correlation_id=intent.intent_id)
            log("order_rejected", intent_id=intent.intent_id, error=str(e))
            return {"status": "REJECTED", "error": str(e)}
        except BrokerUncertain as e:
            self._journal.append("ORDER_UNKNOWN", COMPONENT, {"intent_id": intent.intent_id, "error": str(e)},
                                 correlation_id=intent.intent_id)
            self._control.halt("execution", f"order outcome unknown for {intent.intent_id}: {e}", scope="reconcile")
            log("order_unknown", intent_id=intent.intent_id, error=str(e))
            return {"status": "UNKNOWN", "error": str(e)}
        crashpoint("exec_after_broker_ack_before_persist")
        return self.record_ack(intent.intent_id, ack)

    def record_ack(self, intent_id: str, ack: dict) -> dict:
        """Idempotent: a duplicate acknowledgement is recorded once."""
        try:
            self._journal.append("ORDER_ACKED", COMPONENT, {"intent_id": intent_id, "ack": {k: v for k, v in ack.items() if not k.startswith("_")}},
                                 correlation_id=intent_id, unique=[("ack", intent_id)])
        except DuplicateKey:
            log("duplicate_ack_ignored", intent_id=intent_id)
            return {"status": "DUPLICATE_ACK_IGNORED"}
        if ack.get("status") == "PARTIAL":
            log("partial_fill", intent_id=intent_id, filled=ack["filled_volume"], requested=ack["requested_volume"])
        return {"status": ack["status"], "ticket": ack["ticket"], "filled_volume": ack["filled_volume"]}

    def close(self, ticket: str, reason: str, authority: str, compliance: GateDecision) -> dict:
        """Close an existing position. Requires a compliance decision for the close itself
        (FTMO restricted-news windows forbid closing as well as opening)."""
        if authority not in ("risk", "compliance", "exit_rule", "operator"):
            raise ExecutionRefused(f"{authority} may not close positions")
        if compliance.authority != "compliance" or compliance.verdict is not GateVerdict.ALLOW:
            raise ExecutionRefused(f"compliance does not allow close now: {compliance.reasons}")
        token = self._lease.assert_leader()
        attempts = [e for e in self._journal.events(type_="CLOSE_PERSISTED") if e.payload["ticket"] == ticket]
        for prev in attempts:
            outcome = {e.type for e in self._journal.events(correlation_id=prev.correlation_id)}
            if not outcome & {"CLOSE_REJECTED", "CLOSE_ACKED", "CLOSE_RESOLVED"}:
                raise ExecutionRefused("a previous close attempt has unknown outcome; reconcile first")
        close_id = f"close-{ticket}-{len(attempts)}"
        try:
            self._journal.append("CLOSE_PERSISTED", COMPONENT, {"ticket": ticket, "reason": reason, "authority": authority},
                                 correlation_id=close_id, unique=[("close", close_id)])
        except DuplicateKey:
            raise ExecutionRefused("concurrent close attempt refused") from None
        crashpoint("exec_close_before_send")
        try:
            res = self._broker.close_position(ticket, close_id, token)
        except BrokerError as e:
            self._journal.append("CLOSE_REJECTED", COMPONENT, {"ticket": ticket, "error": str(e)}, correlation_id=close_id)
            return {"status": "REJECTED", "error": str(e)}
        except BrokerUncertain as e:
            self._journal.append("CLOSE_UNKNOWN", COMPONENT, {"ticket": ticket, "error": str(e)}, correlation_id=close_id)
            self._control.halt("execution", f"close outcome unknown for {ticket}", scope="reconcile")
            return {"status": "UNKNOWN"}
        crashpoint("exec_close_after_send_before_persist")
        self._journal.append("CLOSE_ACKED", COMPONENT, {"ticket": ticket, "result": res}, correlation_id=close_id)
        return res
