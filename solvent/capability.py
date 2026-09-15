"""Capability & Conformance — can Solvent actually do this, and does it fit?

Owns one decision: *can this job be performed, and does a candidate option
satisfy the stated requirement?*

Two rules, both ordering rules:

**Conformance before price.** The Governor is not asked until this authority has
answered. A non-conforming option is not a cheap option — it is not an option, so
it never reaches the stage where price is compared. That is what makes the
"$8,900 that does not meet the spec" trap structurally impossible rather than a
matter of good judgement.

**Unknown conformance is not conforming.** Absence of evidence is not capability.
A requirement nobody confirmed, or one no registered capability covers, leaves the
assessment UNKNOWN, and UNKNOWN fails closed for commitment purposes.

At P0 the assessor is the owner. The record type and the pipeline slot are the
same ones an automated registry will fill later, so nothing about the interfaces
changes when it arrives.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .audit import AuditLog, new_id, now
from .content import RequirementSet
from .errors import FailClosed
from .store import Store
from .types import CapabilityVerdict, Conformance


@dataclass(frozen=True, slots=True)
class Capability:
    """Something Solvent can actually do, and what it is proven on."""

    name: str
    covers: frozenset[str]
    proven: bool = False
    privacy_ceiling: str = "CLIENT_CONFIDENTIAL"


@dataclass(frozen=True, slots=True)
class Assessment:
    id: str
    job_id: str
    verdict: CapabilityVerdict
    conformance: Conformance
    assessor: str
    note: str = ""
    gaps: tuple[str, ...] = field(default_factory=tuple)

    @property
    def may_commit(self) -> bool:
        return self.conformance.may_commit and self.verdict.can_proceed_unaided


class CapabilityRegistry:
    """What Solvent can do. It may veto work; it may never add to itself."""

    def __init__(self, store: Store, audit: AuditLog) -> None:
        self._db = store.for_authority("capability")
        self._audit = audit
        self._capabilities: dict[str, Capability] = {}

    def register(self, capability: Capability, *, owner_identity: str) -> None:
        """Add a capability. Owner path only — Solvent cannot extend itself."""
        if not owner_identity.startswith("owner:"):
            raise FailClosed(
                f"capabilities are owner-registered (got {owner_identity!r}); "
                "Solvent may propose growth, never authorise it")
        self._capabilities[capability.name] = capability
        self._audit.record(
            event="capability.registered", authority="capability",
            initiator=owner_identity, why=f"register {capability.name}",
            decision=capability.name, proven=capability.proven)

    def capabilities(self) -> list[Capability]:
        return list(self._capabilities.values())

    def assess(self, *, job_id: str, requirements: RequirementSet,
               assessor: str, needs: list[str]) -> Assessment:
        """Judge capability and conformance together, and record the evidence."""
        verdict, conformance, note, gaps = self._judge(requirements, needs)
        assessment = Assessment(
            id=new_id("cap"), job_id=job_id, verdict=verdict, conformance=conformance,
            assessor=assessor, note=note, gaps=tuple(gaps))
        self._db.execute(
            "INSERT INTO capability_assessments(id,job_id,verdict,conformance,"
            "assessor,ts,note) VALUES(?,?,?,?,?,?,?)",
            (assessment.id, job_id, verdict.value, conformance.value, assessor,
             now(), note))
        self._db.commit()
        self._audit.record(
            event="capability.assessed", authority="capability", initiator=assessor,
            why=note, job_id=job_id, input_ref=assessment.id,
            decision=f"{verdict.value}/{conformance.value}", gaps=list(gaps))
        return assessment

    def _judge(self, requirements: RequirementSet, needs: list[str]):
        """Ordered checks, strictest first."""
        if not requirements.items:
            return (CapabilityVerdict.CANNOT_EXECUTE, Conformance.UNKNOWN,
                    "no requirements recorded; nothing to conform to", [])

        unconfirmed = requirements.unconfirmed
        if unconfirmed:
            # This is the conformance-capture defence. Injected text becomes an
            # unconfirmed requirement, and unconfirmed requirements cannot bind.
            return (CapabilityVerdict.REQUIRES_HUMAN, Conformance.UNKNOWN,
                    f"{len(unconfirmed)} requirement(s) are unconfirmed drafts; "
                    "conformance cannot be established from untrusted content",
                    [r.text for r in unconfirmed])

        covered = {c for cap in self._capabilities.values() for c in cap.covers}
        gaps = [need for need in needs if need not in covered]
        if gaps:
            return (CapabilityVerdict.REQUIRES_NEW_CAPABILITY, Conformance.FAILS,
                    f"no registered capability covers: {', '.join(gaps)}", gaps)

        unproven = [cap.name for cap in self._capabilities.values()
                    if not cap.proven and cap.covers & set(needs)]
        if unproven:
            return (CapabilityVerdict.REQUIRES_OWNER_APPROVAL, Conformance.UNKNOWN,
                    f"capability not yet proven: {', '.join(unproven)}", unproven)

        return (CapabilityVerdict.CAN_EXECUTE, Conformance.CONFORMS,
                f"all {len(needs)} need(s) covered by proven capabilities", [])

    def conforming_options(self, options: list[dict],
                           required: set[str]) -> tuple[list[dict], list[dict]]:
        """Split candidate options into conforming and not, **before** any pricing.

        Cost is never consulted here. An option that does not meet the requirement
        is removed from consideration rather than ranked cheaply.
        """
        conforming, rejected = [], []
        for option in options:
            provides = set(option.get("provides", []))
            missing = required - provides
            if missing:
                rejected.append({**option, "conformance": Conformance.FAILS.value,
                                 "missing": sorted(missing)})
            elif option.get("verified", False):
                conforming.append({**option, "conformance": Conformance.CONFORMS.value})
            else:
                rejected.append({**option, "conformance": Conformance.UNKNOWN.value,
                                 "missing": ["unverified claim"]})
        return conforming, rejected
