"""Broker-truth reconciliation.

Runs at startup and after any UNKNOWN outcome. It never trades. It:
1. resolves every intent whose outcome is unknown by looking the client id up in
   broker deal history (the intent id is the client id);
2. resolves unknown close attempts by checking whether the ticket still exists;
3. compares broker positions with what the journal says we opened;
4. records POSITION_CLOSED facts from broker deals for positions that vanished;
5. writes RECONCILED(clean=True|False). Unknown foreign exposure => not clean.
"""

from __future__ import annotations

from sentinel.broker.base import BrokerUncertain
from sentinel.execution import unresolved_intents
from sentinel.journal import Journal

COMPONENT = "reconciler"


class Reconciler:
    def __init__(self, broker, journal: Journal, control=None, foreign_position_policy: str = "halt"):
        self._control = control
        self._b = broker
        self._j = journal
        self._policy = foreign_position_policy

    def _known_open_tickets(self) -> dict[str, str]:
        """ticket -> intent_id for positions the journal believes are open."""
        opened: dict[str, str] = {}
        for ev in self._j.events(type_="ORDER_ACKED"):
            opened[ev.payload["ack"]["ticket"]] = ev.payload["intent_id"]
        for ev in self._j.events(type_="ORDER_RESOLVED"):
            if ev.payload["outcome"] == "CONFIRMED":
                opened[ev.payload["ticket"]] = ev.payload["intent_id"]
        for ev in self._j.events(type_="POSITION_CLOSED"):
            opened.pop(ev.payload["ticket"], None)
        return opened

    def run(self, record_if_clean: bool = True) -> dict:
        findings: list[dict] = []
        try:
            snap = self._b.account()
        except BrokerUncertain as e:
            self._j.append("RECONCILED", COMPONENT, {"clean": False, "findings": [{"kind": "BROKER_UNREACHABLE", "detail": str(e)}]})
            if self._control:
                self._control.halt(COMPONENT, f"broker unreachable during reconcile: {e}", scope="reconcile")
            return {"clean": False, "findings": findings}

        # 1. unresolved order intents
        lagging = False
        for u in unresolved_intents(self._j):
            try:
                deals = self._b.find_by_client_id(u["intent_id"])
            except BrokerUncertain as e:  # e.g. MT5 history not yet synchronised: NOT knowable yet
                lagging = True
                findings.append({"kind": "INTENT_UNRESOLVED_RETRY", "intent_id": u["intent_id"], "detail": str(e),
                                 "blocking": True, "retry": True})
                continue
            ins = [d for d in deals if d["kind"] == "IN"]
            if ins:
                d = ins[0]
                self._j.append("ORDER_RESOLVED", COMPONENT,
                               {"intent_id": u["intent_id"], "outcome": "CONFIRMED", "ticket": d["ticket"],
                                "filled_volume": d["volume"], "price": d["price"], "prior_state": u["state"]},
                               correlation_id=u["intent_id"])
                findings.append({"kind": "INTENT_CONFIRMED_BY_BROKER", "intent_id": u["intent_id"]})
            else:
                self._j.append("ORDER_RESOLVED", COMPONENT,
                               {"intent_id": u["intent_id"], "outcome": "NOT_EXECUTED", "prior_state": u["state"]},
                               correlation_id=u["intent_id"])
                findings.append({"kind": "INTENT_NOT_EXECUTED", "intent_id": u["intent_id"]})

        broker_tickets = {p.ticket: p for p in snap.positions}

        # 2. unknown close attempts
        for ev in self._j.events(type_="CLOSE_PERSISTED"):
            outcome = {e.type for e in self._j.events(correlation_id=ev.correlation_id)}
            if outcome & {"CLOSE_ACKED", "CLOSE_REJECTED", "CLOSE_RESOLVED"}:
                continue
            closed = ev.payload["ticket"] not in broker_tickets
            self._j.append("CLOSE_RESOLVED", COMPONENT, {"ticket": ev.payload["ticket"], "closed": closed},
                           correlation_id=ev.correlation_id)
            findings.append({"kind": "CLOSE_RESOLVED", "ticket": ev.payload["ticket"], "closed": closed})

        # 3/4. positions
        known = self._known_open_tickets()
        recorded_closed = {e.payload["ticket"] for e in self._j.events(type_="POSITION_CLOSED")}
        deals = None
        for ticket, intent_id in known.items():
            if ticket in broker_tickets:
                continue
            if deals is None:
                deals = self._b.deals_since(0)
            outs = [d for d in deals if d["ticket"] == ticket and d["kind"].startswith("OUT")]
            if outs and ticket not in recorded_closed:
                d = outs[-1]
                self._j.append("POSITION_CLOSED", COMPONENT,
                               {"ticket": ticket, "intent_id": intent_id, "price": d["price"], "profit": d["profit"],
                                "commission": d["commission"], "reason": d["kind"], "source": "broker_deal", "deal_id": d["deal_id"]},
                               correlation_id=intent_id)
            elif not outs:
                findings.append({"kind": "LOCAL_POSITION_MISSING_AT_BROKER", "ticket": ticket, "blocking": True})

        known = self._known_open_tickets()
        for t, p in broker_tickets.items():
            if t not in known:
                findings.append({"kind": "FOREIGN_POSITION", "ticket": t, "instrument": p.instrument,
                                 "volume": p.volume, "client_id": p.client_id,
                                 "blocking": self._policy == "halt",
                                 # while an intent is unresolved, an unattributed position may be that intent
                                 "retry": lagging})

        clean = not any(f.get("blocking") for f in findings)
        if record_if_clean or not clean or findings:
            self._j.append("RECONCILED", COMPONENT, {"clean": clean, "findings": findings, "snapshot_hash": snap.snapshot_hash})
        if not clean and self._control:
            blocking = [f for f in findings if f.get("blocking")]
            scope = "reconcile" if all(f.get("retry") for f in blocking) else "manual"
            self._control.halt(COMPONENT, "unreconciled exposure: " + ",".join(f["kind"] for f in blocking), scope=scope)
        return {"clean": clean, "findings": findings}
