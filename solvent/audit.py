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
        #: Answers "may this verifier's PASS count?". Injected at composition
        #: rather than imported, because the capability registry imports this
        #: module. It is consulted, never commanded: this module refuses on a
        #: no, and has no way to change a certification.
        self._verifier_trust = None

    def trust_verifiers_via(self, registry) -> None:
        """Wire the certification authority. Composition-root only.

        Deliberately not a constructor argument: an :class:`AuditLog` exists
        before the registry does, and the registry needs the audit log. Passing
        it afterwards is what breaks that cycle without either module importing
        the other.
        """
        self._verifier_trust = registry

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
        requirement_id: str = "", artifact_digest: str = "", artifact_id: str = "",
        verifier_ref: str = "", capability_version: str = "", check: str = "",
    ) -> str:
        """Record verification evidence, refusing self-attestation *and* hearsay.

        Two separate controls live here, and they close different holes.

        **Who may attest.** At C_HIGH and above the component that benefits from
        claiming success may not be the sole source of evidence for it, and a
        self-report is never verification above C_LOW. Enforced at write time.

        **What was attested.** A verification that says only "it passed" cannot
        be checked by anyone later, and cannot be invalidated when the artifact
        changes underneath it. So evidence above T0 must name the requirement it
        concerns and the digest of the artifact it inspected. Those two fields
        are what let :meth:`Orchestrator.verification_satisfied` ask the only
        question that matters before delivery: *does every committed requirement
        have a pass for this exact file?*

        **Whether the attester can tell right from wrong.** The two controls
        above both assume the verifier is competent, and nothing established
        that. A capability that supplied an approve-everything verifier put six
        PASS rows in this table, each recording a ``method`` of "recomputed from
        source and deliverable" when nothing had been recomputed, and the client
        received an invented total. ``verifier_identity`` was ``qc`` and
        ``executor_identity`` was ``worker``, so the independence control above
        was satisfied — because those are labels the caller chooses, not facts
        about the code that ran.

        So evidence above T0 must also name the **implementation** that produced
        it (``verifier_ref``, derived from the callable, not typed), and that
        implementation must be certified to decide that check for that
        capability. The certification lives in the capability registry, which
        already owns what-is-proven-on-what-evidence; this method asks it and
        refuses, which is the same chokepoint doing one more check rather than a
        second gate somewhere else.

        The digest is not validated for truthfulness here — this records what a
        verifier claims to have inspected. What makes it meaningful is that the
        Orchestrator compares it against the digest it computed from the file it
        is about to deliver, so a wrong digest fails the gate rather than passing
        it.
        """
        if tier is not VerificationTier.T0_SELF_REPORT:
            if not requirement_id:
                raise FailClosed(
                    f"{tier.value} evidence must name the requirement it verifies; "
                    "evidence that is not about anything cannot be checked")
            if not artifact_digest:
                raise FailClosed(
                    f"{tier.value} evidence must name the artifact digest it "
                    "inspected, or it cannot be invalidated when the artifact "
                    "changes")
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

        verifier_state = ""
        if tier is not VerificationTier.T0_SELF_REPORT:
            if not verifier_ref:
                raise FailClosed(
                    f"{tier.value} evidence must name the verifier implementation "
                    "that produced it; an identity label says who signed, not "
                    "what ran, and a capability can choose its own label")
            if self._verifier_trust is None:
                raise FailClosed(
                    "no verifier certification is available, so this process "
                    f"cannot establish that {verifier_ref} detects wrong work; "
                    "evidence above T0 is refused rather than recorded untrusted")
            allowed, why = self._verifier_trust.may_authorize_delivery(
                verifier_ref=verifier_ref, capability_version=capability_version,
                check=check)
            if not allowed:
                raise FailClosed(f"refusing to record verification evidence: {why}")
            verifier_state = self._verifier_trust.verifier_state(
                verifier_ref, capability_version)

        evidence_id = new_id("ver")
        self._db.execute(
            "INSERT INTO verification_evidence(id,ts,subject_ref,tier,method,"
            "executor_identity,verifier_identity,verdict,raw_output,consequence,"
            "requirement_id,artifact_digest,artifact_id,verifier_ref,verifier_state) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (evidence_id, now(), subject_ref, tier.value, method, executor_identity,
             verifier_identity, "PASS" if verdict else "FAIL", raw_output,
             consequence.value, requirement_id, artifact_digest, artifact_id,
             verifier_ref, verifier_state),
        )
        self._db.commit()
        self.record(
            event="verification.recorded", authority="audit", initiator=verifier_identity,
            why=f"verification of {subject_ref}", verification_ref=evidence_id,
            result="PASS" if verdict else "FAIL", tier=tier.value, method=method,
            requirement=requirement_id, artifact=artifact_digest[:16],
        )
        return evidence_id

    def passes_for_artifact(self, subject_ref: str, artifact_digest: str) -> dict:
        """``{requirement_id: evidence_row}`` of PASSes for **this exact** artifact.

        Scoped by digest on purpose. Evidence for a previous version of the file
        is not evidence for this one, so a correction silently invalidates every
        earlier pass rather than inheriting it.
        """
        rows = self._db.query(
            "SELECT * FROM verification_evidence WHERE subject_ref = ? "
            "AND artifact_digest = ? AND verdict = 'PASS' ORDER BY ts",
            (subject_ref, artifact_digest))
        return {r["requirement_id"]: dict(r) for r in rows if r["requirement_id"]}

    def evidence_for(self, subject_ref: str) -> list[dict]:
        return [dict(r) for r in self._db.query(
            "SELECT * FROM verification_evidence WHERE subject_ref = ? ORDER BY ts",
            (subject_ref,))]

    def best_tier(self, subject_ref: str) -> VerificationTier | None:
        """Highest passing verification tier recorded for a subject."""
        tiers = [VerificationTier(r["tier"]) for r in self.evidence_for(subject_ref)
                 if r["verdict"] == "PASS"]
        return max(tiers, key=lambda t: t.rank) if tiers else None
