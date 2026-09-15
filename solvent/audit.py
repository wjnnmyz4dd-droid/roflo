"""Audit Log — immutable evidence of consequential decisions and actions.

Owns one decision: *what happened, and on whose authority?*

Two properties make this evidence rather than logging:

* **Append-only at the storage layer.** UPDATE and DELETE abort in SQLite
  triggers, so the guarantee survives code that bypasses this module.
* **Hash-chained.** Each event carries the hash of its predecessor, so removing
  or altering an event anywhere in the chain is detectable by
  :meth:`AuditLog.verify_chain` even if an attacker reached the file directly.

The law this enforces: **if the action cannot be logged, the action does not
proceed.** An unlogged consequential action is a governance hole, so every
authority writes here before it acts, not after.

The log also holds :class:`VerificationEvidence`, deliberately *not* Business
Memory. Learning writes to Memory; if evidence lived there, the component whose
autonomy depends on looking successful could edit the record of its success.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone

from .errors import FailClosed
from .store import Store
from .types import ConsequenceTier, VerificationTier

GENESIS = "0" * 64


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


class AuditLog:
    """The append-only evidence authority."""

    def __init__(self, store: Store) -> None:
        self._db = store.for_authority("audit")

    # ---------------------------------------------------------------- events

    def record(
        self, *, event: str, authority: str, initiator: str, why: str,
        job_id: str | None = None, input_ref: str = "", decision: str = "",
        permission: str = "", financial_authorization: str = "",
        external_effect: str = "", result: str = "", verification_ref: str = "",
        **payload: object,
    ) -> str:
        """Append one event and return its hash.

        Every field the P0 contract requires is a named parameter, so a caller
        cannot quietly omit *who authorised this* by burying it in a dict.
        """
        prev = self._head_hash()
        ts = now()
        body = {
            "ts": ts, "event": event, "authority": authority, "initiator": initiator,
            "job_id": job_id, "why": why, "input_ref": input_ref, "decision": decision,
            "permission": permission, "financial_authorization": financial_authorization,
            "external_effect": external_effect, "result": result,
            "verification_ref": verification_ref, "payload": payload,
        }
        serialised = json.dumps(body, sort_keys=True, default=str)
        digest = hashlib.sha256((prev + serialised).encode()).hexdigest()
        self._db.execute(
            "INSERT INTO audit_log(ts,event,authority,initiator,job_id,why,input_ref,"
            "decision,permission,financial_authorization,external_effect,result,"
            "verification_ref,payload,prev_hash,hash) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ts, event, authority, initiator, job_id, why, input_ref, decision,
             permission, financial_authorization, external_effect, result,
             verification_ref, json.dumps(payload, sort_keys=True, default=str),
             prev, digest),
        )
        self._db.commit()
        return digest

    def _head_hash(self) -> str:
        row = self._db.query_one("SELECT hash FROM audit_log ORDER BY seq DESC LIMIT 1")
        return row["hash"] if row else GENESIS

    def events(self, job_id: str | None = None) -> list[dict]:
        sql = "SELECT * FROM audit_log"
        params: tuple = ()
        if job_id is not None:
            sql += " WHERE job_id = ?"
            params = (job_id,)
        return [dict(r) for r in self._db.query(sql + " ORDER BY seq", params)]

    def verify_chain(self) -> tuple[bool, str]:
        """Recompute the chain. Returns ``(ok, detail)``."""
        prev = GENESIS
        for row in self._db.query("SELECT * FROM audit_log ORDER BY seq"):
            body = {
                "ts": row["ts"], "event": row["event"], "authority": row["authority"],
                "initiator": row["initiator"], "job_id": row["job_id"], "why": row["why"],
                "input_ref": row["input_ref"], "decision": row["decision"],
                "permission": row["permission"],
                "financial_authorization": row["financial_authorization"],
                "external_effect": row["external_effect"], "result": row["result"],
                "verification_ref": row["verification_ref"],
                "payload": json.loads(row["payload"]),
            }
            expect = hashlib.sha256(
                (prev + json.dumps(body, sort_keys=True, default=str)).encode()
            ).hexdigest()
            if row["prev_hash"] != prev:
                return False, f"seq {row['seq']}: prev_hash mismatch"
            if row["hash"] != expect:
                return False, f"seq {row['seq']}: content hash mismatch"
            prev = row["hash"]
        return True, "chain intact"

    # ------------------------------------------------------------ evidence

    def record_verification(
        self, *, subject_ref: str, tier: VerificationTier, method: str,
        executor_identity: str, verifier_identity: str, verdict: bool,
        raw_output: str, consequence: ConsequenceTier,
    ) -> str:
        """Record verification evidence, refusing self-attestation where it matters.

        At C_HIGH and above the component that benefits from claiming success may
        not be the sole source of evidence for it. That rule is enforced here, at
        write time, rather than trusted to reviewers.
        """
        if tier is VerificationTier.T0_SELF_REPORT and consequence is not ConsequenceTier.C_LOW:
            raise FailClosed(
                f"T0 self-report is not verification for {consequence.value}"
            )
        if consequence.requires_independent_verifier and verifier_identity == executor_identity:
            raise FailClosed(
                f"verifier must differ from executor for {consequence.value}: "
                f"both are {executor_identity!r}"
            )
        if not verifier_identity:
            raise FailClosed("verification evidence requires a verifier identity")

        evidence_id = new_id("ver")
        self._db.execute(
            "INSERT INTO verification_evidence(id,ts,subject_ref,tier,method,"
            "executor_identity,verifier_identity,verdict,raw_output,consequence) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (evidence_id, now(), subject_ref, tier.value, method, executor_identity,
             verifier_identity, "PASS" if verdict else "FAIL", raw_output,
             consequence.value),
        )
        self._db.commit()
        self.record(
            event="verification.recorded", authority="audit", initiator=verifier_identity,
            why=f"verification of {subject_ref}", verification_ref=evidence_id,
            result="PASS" if verdict else "FAIL", tier=tier.value, method=method,
        )
        return evidence_id

    def evidence_for(self, subject_ref: str) -> list[dict]:
        return [dict(r) for r in self._db.query(
            "SELECT * FROM verification_evidence WHERE subject_ref = ? ORDER BY ts",
            (subject_ref,))]

    def best_tier(self, subject_ref: str) -> VerificationTier | None:
        """Highest passing verification tier recorded for a subject."""
        tiers = [VerificationTier(r["tier"]) for r in self.evidence_for(subject_ref)
                 if r["verdict"] == "PASS"]
        return max(tiers, key=lambda t: t.rank) if tiers else None
