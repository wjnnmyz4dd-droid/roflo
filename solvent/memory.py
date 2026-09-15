"""Business Memory — evolving operational knowledge.

Holds what Solvent has learned: client patterns, estimate performance, failure
patterns, tool and quality outcomes, value-add results. It is the one store
Learning may write to, and that is exactly why it may never hold financial truth,
governance, or evidence.

**Verify before learn**, enforced at the write:

* ``T0`` self-report is refused outright. An agent reporting COMPLETE is not
  evidence that anything succeeded.
* Economic claims — profitability, pricing, margin — require ``T3``: an external
  fact such as a verified payment. A deterministic test proves the spreadsheet
  parses; it does not prove the job made money.

Memory has no write path to the Ledger, the Audit Log, the Policy Store, or the
Governor's calibration. That is not policed here; the store hands this authority a
connection that physically cannot write those tables.
"""

from __future__ import annotations

import json

from .audit import AuditLog, new_id, now
from .errors import FailClosed
from .store import Store
from .types import VerificationTier

#: Kinds whose claims are economic, and so need an external fact behind them.
ECONOMIC_KINDS = frozenset({
    "estimate_accuracy", "pricing", "margin", "profitability", "client_value",
    "job_economics",
})


class BusinessMemory:
    """Learning's only writable surface."""

    def __init__(self, store: Store, audit: AuditLog) -> None:
        self._db = store.for_authority("memory")
        self._audit = audit

    def learn(self, *, kind: str, subject: str, payload: dict, evidence_ref: str,
              tier: VerificationTier) -> str:
        """Record a lesson, or refuse for want of evidence."""
        if not tier.may_feed_learning:
            raise FailClosed(
                f"{tier.value} is not evidence; an agent reporting completion does "
                "not prove success")
        if kind in ECONOMIC_KINDS and not tier.may_feed_economic_learning:
            raise FailClosed(
                f"economic learning ({kind}) requires {VerificationTier.T3_EXTERNAL_FACT.value}, "
                f"got {tier.value}: a passing test does not prove a job was profitable")
        if not evidence_ref:
            raise FailClosed("a lesson must cite the evidence it came from")

        fact_id = new_id("mem")
        self._db.execute(
            "INSERT INTO memory_facts(id,ts,kind,subject,payload,evidence_ref,tier) "
            "VALUES(?,?,?,?,?,?,?)",
            (fact_id, now(), kind, subject, json.dumps(payload, sort_keys=True,
                                                       default=str),
             evidence_ref, tier.value))
        self._db.commit()
        self._audit.record(
            event="memory.learned", authority="memory", initiator="learning",
            why=f"{kind} for {subject}", input_ref=fact_id,
            verification_ref=evidence_ref, decision=kind, tier=tier.value)
        return fact_id

    def recall(self, *, kind: str | None = None,
               subject: str | None = None) -> list[dict]:
        sql = "SELECT * FROM memory_facts WHERE 1=1"
        params: list = []
        if kind:
            sql += " AND kind = ?"
            params.append(kind)
        if subject:
            sql += " AND subject = ?"
            params.append(subject)
        rows = self._db.query(sql + " ORDER BY ts DESC", params)
        return [{**dict(r), "payload": json.loads(r["payload"])} for r in rows]

    def client_history(self, client_id: str) -> list[dict]:
        return self.recall(subject=client_id)
