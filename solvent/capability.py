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
    """Something Solvent can actually do, and what it is proven on.

    ``proven`` is a claim, and a claim needs evidence behind it. The fields below
    are what turns "we can do CSV cleanup" into something a reader can check: the
    exact code version it was proven at, what it accepts and produces, which
    checks decide its output, and the count of fixtures that passed. A capability
    proven at one version is not proven at the next — that is why ``version`` is
    recorded rather than assumed.

    None of this makes something proven. A model saying so, a worker declaring
    itself able, a client insisting, Business Memory recalling a success, a high
    price or a deadline — none of them may set ``proven``. Only evidence does,
    and :func:`promotion_verdict` says what evidence is enough.
    """

    name: str
    covers: frozenset[str]
    proven: bool = False
    privacy_ceiling: str = "CLIENT_CONFIDENTIAL"
    #: The code version the evidence was gathered against.
    version: str = ""
    inputs: tuple[str, ...] = field(default_factory=tuple)
    outputs: tuple[str, ...] = field(default_factory=tuple)
    #: Registered checks that can decide this capability's output.
    verifiable_by: tuple[str, ...] = field(default_factory=tuple)
    #: Complexity levels demonstrated, e.g. ("L1", "L2", "L3").
    proven_levels: tuple[str, ...] = field(default_factory=tuple)
    #: Where the evidence lives, and how much of it there is.
    evidence_ref: str = ""
    fixtures_passed: int = 0
    fixtures_total: int = 0
    false_completions: int = 0
    requires_tools: tuple[str, ...] = field(default_factory=tuple)
    requires_network: bool = False
    requires_owner: bool = False

    @property
    def evidence_summary(self) -> str:
        return (f"{self.fixtures_passed}/{self.fixtures_total} fixtures, "
                f"{self.false_completions} false completion(s), "
                f"levels {', '.join(self.proven_levels) or 'none'}, "
                f"at {self.version or 'an unrecorded version'}")


#: What a capability must show before it may be trusted with real client work.
#: Deliberately modest numbers with the reasoning attached rather than a
#: confident-sounding percentage computed from a handful of runs.
MIN_FIXTURES = 8          # enough that one lucky path cannot carry it
MIN_LEVELS = 3            # simple, moderate and complex all demonstrated
MAX_FALSE_COMPLETIONS = 0  # the one number that cannot be traded away


def promotion_verdict(capability: "Capability") -> tuple[bool, str]:
    """May this capability be marked proven? Evidence only.

    A single successful fixture is not proof; neither is a high pass rate over
    three runs. And no amount of passing offsets a false completion — shipping
    wrong work while reporting success is the failure mode this whole pipeline
    exists to prevent, so one of them resets the answer to no.
    """
    reasons = []
    if capability.false_completions > MAX_FALSE_COMPLETIONS:
        reasons.append(
            f"{capability.false_completions} false completion(s): work shipped "
            "while reporting success. Nothing else counts until that is zero")
    if capability.fixtures_total < MIN_FIXTURES:
        reasons.append(f"only {capability.fixtures_total} fixture(s); "
                       f"{MIN_FIXTURES} is the floor")
    if capability.fixtures_passed < capability.fixtures_total:
        reasons.append(f"{capability.fixtures_total - capability.fixtures_passed} "
                       "fixture(s) did not behave as expected")
    if len(capability.proven_levels) < MIN_LEVELS:
        reasons.append(f"only {len(capability.proven_levels)} complexity level(s) "
                       f"demonstrated; {MIN_LEVELS} is the floor")
    if not capability.verifiable_by:
        reasons.append("no check can decide this capability's output, so a "
                       "deliverable could never be verified")
    if not capability.version:
        reasons.append("no version recorded, so the evidence names no code")
    if reasons:
        return False, "; ".join(reasons)
    return True, (f"evidence supports promotion: {capability.evidence_summary}")


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

    def __init__(self, store: Store, audit: AuditLog, policy=None) -> None:
        self._db = store.for_authority("capability")
        self._audit = audit
        self._policy = policy
        self._capabilities: dict[str, Capability] = {}

    def register(self, capability: Capability, *, owner_identity: str) -> None:
        """Add a capability. Owner path only — Solvent cannot extend itself."""
        registered = (self._policy.is_owner(owner_identity) if self._policy
                      else owner_identity.startswith("owner:"))
        if not registered:
            raise FailClosed(
                f"capabilities are owner-registered (got {owner_identity!r}); "
                "Solvent may propose growth, never authorise it")

        decision, _ = self.development_decision(capability.name)
        if decision == self.DENIED:
            raise FailClosed(
                f"the owner denied development of {capability.name!r}; "
                "registering it anyway would make the denial meaningless. "
                "Record a new decision first if the answer has changed")

        # A capability the owner asked Solvent to build must earn PROVEN with
        # evidence. Approval to try is not approval to say it worked — and the
        # thing being approved is exactly the thing Solvent would be grading.
        # A capability the owner registers with no proposal behind it is their
        # own judgement about their own business, and needs no fixture count.
        if capability.proven and decision:
            good, reason = promotion_verdict(capability)
            if not good:
                raise FailClosed(
                    f"{capability.name} was developed under an owner decision, so "
                    f"calling it proven needs evidence: {reason}")

        self._capabilities[capability.name] = capability
        self._audit.record(
            event="capability.registered", authority="capability",
            initiator=owner_identity, why=f"register {capability.name}",
            decision=capability.name, proven=capability.proven)

    # ------------------------------------------------------- growth proposals
    #
    # Solvent detects that a job needs something it cannot do, and says so. The
    # owner answers. Neither half is new authority: the registry already owns
    # what Solvent can do, and Policy already owns who counts as the owner. What
    # was missing was a way for a detected gap to become a question, so every
    # gap produced the same refusal and the owner's answer had nowhere to live.

    #: The three answers an owner can give, and what each permits.
    APPROVED = "APPROVED"   # build it, test it, and when proven, deploy it
    LIMITED = "LIMITED"     # build it and test it; deploying it is a separate yes
    DENIED = "DENIED"       # build nothing

    DECISIONS = (APPROVED, LIMITED, DENIED)

    def propose(self, *, name: str, covers: frozenset, why: str,
                requested_by: str = "capability", job_id: str = "") -> str:
        """Record that a job needed something Solvent cannot do.

        **Solvent may call this.** Proposing is not deciding: the record is a
        question, and until an owner answers it nothing may be built. A second
        proposal for a name the owner has already answered is refused, because
        re-asking until the answer changes is how a denial gets worn down.
        """
        decision, _ = self.development_decision(name)
        if decision:
            raise FailClosed(
                f"{name!r} already has an owner decision ({decision}); "
                "re-proposing it would be asking again until the answer changes. "
                "The owner may record a new decision directly")

        proposal_id = new_id("prop")
        self._db.execute(
            "INSERT INTO capability_proposals(id,ts,name,kind,covers,why,"
            "requested_by,job_id) VALUES(?,?,?,?,?,?,?,?)",
            (proposal_id, now(), name, "PROPOSED", ",".join(sorted(covers)), why,
             requested_by, job_id))
        self._db.commit()
        self._audit.record(
            event="capability.proposed", authority="capability",
            initiator=requested_by, why=why, job_id=job_id or None,
            input_ref=proposal_id, decision="PROPOSED",
            result=f"{name} would cover {', '.join(sorted(covers))}; "
                   "awaiting an owner decision")
        return proposal_id

    def decide(self, *, name: str, decision: str, owner_identity: str,
               why: str, scope: str = "") -> str:
        """The owner answers a proposal. **Owner path only.**

        ``scope`` is free text for the owner to bound a LIMITED grant — it is
        recorded and shown, never parsed into permissions, because a permission
        Solvent inferred from prose is not a permission the owner gave.
        """
        if not (self._policy.is_owner(owner_identity) if self._policy
                else owner_identity.startswith("owner:")):
            raise FailClosed(
                f"only a registered owner decides capability growth "
                f"(got {owner_identity!r}); Solvent may ask, never answer")
        if decision not in self.DECISIONS:
            raise FailClosed(
                f"{decision!r} is not an answer; expected one of "
                f"{', '.join(self.DECISIONS)}")
        if not self._proposals_for(name):
            raise FailClosed(f"there is no proposal for {name!r} to decide")

        decision_id = new_id("dec")
        self._db.execute(
            "INSERT INTO capability_proposals(id,ts,name,kind,decision,why,"
            "decided_by,scope) VALUES(?,?,?,?,?,?,?,?)",
            (decision_id, now(), name, "DECIDED", decision, why, owner_identity,
             scope))
        self._db.commit()
        self._audit.record(
            event="capability.decided", authority="capability",
            initiator=owner_identity, why=why, input_ref=decision_id,
            decision=decision,
            result=f"{name}: {decision}" + (f" (scope: {scope})" if scope else ""))
        return decision_id

    def development_decision(self, name: str) -> tuple[str, str]:
        """``(decision, scope)`` — the owner's latest answer, or ``("", "")``."""
        rows = [r for r in self._proposals_for(name) if r["kind"] == "DECIDED"]
        if not rows:
            return "", ""
        latest = rows[-1]
        return latest["decision"], latest["scope"]

    def may_develop(self, name: str) -> tuple[bool, str]:
        """May Solvent build and test this? Silence is not consent."""
        decision, scope = self.development_decision(name)
        if not decision:
            if self._proposals_for(name):
                return False, f"{name} is proposed and awaiting an owner decision"
            return False, f"{name} has not been proposed"
        if decision == self.DENIED:
            return False, f"the owner denied development of {name}"
        return True, (f"{decision}" + (f" within scope: {scope}" if scope else ""))

    def may_deploy(self, name: str) -> tuple[bool, str]:
        """May its output reach a client?

        This is the distinction LIMITED exists to make. Permission to *build*
        something is not permission to *use* it on a client, and a capability
        that conflates them turns a cautious owner's "try it and show me" into
        a deployment they never agreed to.
        """
        decision, scope = self.development_decision(name)
        if decision == self.LIMITED:
            return False, (f"{name} is approved for development and testing only"
                           + (f" ({scope})" if scope else "")
                           + "; deploying it needs a separate owner decision")
        permitted, why = self.may_develop(name)
        if not permitted:
            return False, why
        capability = self._capabilities.get(name)
        if capability is None or not capability.proven:
            return False, (f"{name} is approved but not yet registered as proven; "
                           "approval to build is not evidence that it works")
        return True, f"{name} is approved and proven"

    def proposals(self, name: str | None = None) -> list[dict]:
        rows = self._proposals_for(name) if name else [
            dict(r) for r in self._db.query(
                "SELECT * FROM capability_proposals ORDER BY ts")]
        return rows

    def awaiting_owner(self) -> list[dict]:
        """Proposals nobody has answered. The owner's queue."""
        out = []
        for row in self.proposals():
            if row["kind"] != "PROPOSED":
                continue
            if not self.development_decision(row["name"])[0]:
                out.append(row)
        return out

    def _proposals_for(self, name: str) -> list[dict]:
        return [dict(r) for r in self._db.query(
            "SELECT * FROM capability_proposals WHERE name = ? ORDER BY ts",
            (name,))]

    def capabilities(self) -> list[Capability]:
        return list(self._capabilities.values())

    # -------------------------------------------------- verifier certification
    #
    # The registry already owns "what is proven, on what evidence". A verifier's
    # competence is the same kind of question about a different subject, so it
    # lives here rather than in a new authority. Nothing below decides whether a
    # deliverable may ship — that remains the verification/delivery controls'
    # job. This only answers: *may this verifier's word count?*

    #: A verifier that has not been put through its capability's battery.
    UNTESTED = "UNTESTED"
    #: Every trial correct: it accepted correct work and rejected wrong work.
    CERTIFIED = "CERTIFIED"
    #: Correct on some checks and not others. Trusted only on the ones it passed.
    CERTIFIED_WITH_LIMITS = "CERTIFIED_WITH_LIMITS"
    #: It let defective work through, or refused correct work. Not usable.
    FAILED = "FAILED"
    #: It was certified and later produced a demonstrated false PASS.
    REVOKED = "REVOKED"

    def certify_verifier(self, *, verifier_ref: str, capability_version: str,
                         report, decided_by: str = "capability") -> dict:
        """Record what a verifier did against hidden ground truth, and judge it.

        ``report`` is a :class:`solvent.verifiercert.TrialReport` — measurements
        taken elsewhere. This method does not run the trials and cannot change
        them; it reads the counts and applies one rule:

        * a **false accept** means defective work would ship while reporting
          success, which is the failure the whole pipeline exists to prevent
        * a **false reject** means correct work can never ship, which is a
          different failure and equally disqualifying
        * a verifier that got some checks entirely right and others wrong is
          certified **only for the checks it got right**

        A verifier with no trials at all is not certified. An empty battery is
        not a clean sheet — it is no evidence, and no evidence is not trust.
        """
        results = list(getattr(report, "results", []))
        false_accepts = list(report.false_accepts)
        false_rejects = list(report.false_rejects)
        passed = sorted(report.checks_passed()) if results else []

        if not results:
            state = self.FAILED
            why = (f"no trials exist for {capability_version!r}, so this verifier "
                   "has demonstrated nothing; an untested verifier is not a "
                   "trusted one")
        elif not passed:
            state = self.FAILED
            why = (f"every check was wrong on at least one trial: "
                   f"{report.summary}")
        elif false_accepts and not false_rejects:
            state = self.CERTIFIED_WITH_LIMITS if passed else self.FAILED
            why = (f"accepted {len(false_accepts)} defective artifact(s) "
                   f"({', '.join(r.trial.name for r in false_accepts[:3])}); "
                   f"trusted only on {len(passed)} check(s) it got right")
        elif false_rejects and not false_accepts:
            state = self.CERTIFIED_WITH_LIMITS if passed else self.FAILED
            why = (f"refused {len(false_rejects)} correct artifact(s) "
                   f"({', '.join(r.trial.name for r in false_rejects[:3])}); "
                   f"trusted only on {len(passed)} check(s) it got right")
        elif false_accepts or false_rejects:
            state = self.CERTIFIED_WITH_LIMITS
            why = (f"wrong in both directions; trusted only on {len(passed)} "
                   f"check(s) it got right: {report.summary}")
        else:
            state = self.CERTIFIED
            why = f"correct on every trial: {report.summary}"

        return self._record_certification(
            verifier_ref=verifier_ref, capability_version=capability_version,
            state=state, certified_checks=passed, report=report, why=why,
            decided_by=decided_by)

    def revoke_verifier(self, *, verifier_ref: str, capability_version: str,
                        why: str, decided_by: str) -> dict:
        """Withdraw trust after a demonstrated false PASS. History is untouched.

        The earlier CERTIFIED row stays exactly where it is. Revocation is a new
        record, because the fact that this verifier *was* trusted is part of how
        any artifact it passed came to be delivered, and erasing it would hide
        the reason those deliveries happened.
        """
        if not why:
            raise FailClosed("revoking a verifier requires a reason on the record")
        return self._record_certification(
            verifier_ref=verifier_ref, capability_version=capability_version,
            state=self.REVOKED, certified_checks=[], report=None, why=why,
            decided_by=decided_by)

    def _record_certification(self, *, verifier_ref, capability_version, state,
                              certified_checks, report, why, decided_by) -> dict:
        row = {
            "id": new_id("vcert"), "ts": now(), "verifier_ref": verifier_ref,
            "capability_version": capability_version, "state": state,
            "certified_checks": ",".join(certified_checks),
            "trials_total": len(getattr(report, "results", []) or []),
            "trials_correct": sum(1 for r in getattr(report, "results", []) or []
                                  if r.correct),
            "false_accepts": len(report.false_accepts) if report else 0,
            "false_rejects": len(report.false_rejects) if report else 0,
            "why": why[:600], "decided_by": decided_by,
        }
        self._db.execute(
            "INSERT INTO verifier_certifications(id,ts,verifier_ref,"
            "capability_version,state,certified_checks,trials_total,"
            "trials_correct,false_accepts,false_rejects,why,decided_by) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (row["id"], row["ts"], verifier_ref, capability_version, state,
             row["certified_checks"], row["trials_total"], row["trials_correct"],
             row["false_accepts"], row["false_rejects"], row["why"], decided_by))
        self._db.commit()
        self._audit.record(
            event="verifier.certification", authority="capability",
            initiator=decided_by,
            why=f"{verifier_ref} against {capability_version}",
            decision=state, result=why[:300],
            trials=f"{row['trials_correct']}/{row['trials_total']}",
            false_accepts=row["false_accepts"], false_rejects=row["false_rejects"])
        return row

    def verifier_certification(self, verifier_ref: str,
                               capability_version: str) -> dict | None:
        """The current certification, which is the most recent record."""
        rows = self._db.query(
            "SELECT * FROM verifier_certifications WHERE verifier_ref = ? "
            "AND capability_version = ? ORDER BY ts, rowid", 
            (verifier_ref, capability_version))
        return dict(rows[-1]) if rows else None

    def verifier_history(self, verifier_ref: str,
                         capability_version: str = "") -> list[dict]:
        """Every certification record, oldest first. Append-only, never edited."""
        if capability_version:
            rows = self._db.query(
                "SELECT * FROM verifier_certifications WHERE verifier_ref = ? "
                "AND capability_version = ? ORDER BY ts, rowid",
                (verifier_ref, capability_version))
        else:
            rows = self._db.query(
                "SELECT * FROM verifier_certifications WHERE verifier_ref = ? "
                "ORDER BY ts, rowid", (verifier_ref,))
        return [dict(r) for r in rows]

    def verifier_state(self, verifier_ref: str, capability_version: str) -> str:
        record = self.verifier_certification(verifier_ref, capability_version)
        return record["state"] if record else self.UNTESTED

    def may_authorize_delivery(self, *, verifier_ref: str,
                               capability_version: str,
                               check: str) -> tuple[bool, str]:
        """May this verifier's PASS for this check count towards delivery?

        Scoped to the exact ``(verifier_ref, capability_version, check)``. A
        verifier proven on ``csv-cleanup/1.0`` says nothing about its competence
        on ``report-builder/1.0``, and being right about duplicate rows is not
        evidence about dates. Trust does not spread sideways, so each triple is
        asked separately and an unasked one is UNTESTED.
        """
        if not verifier_ref:
            return False, ("evidence does not name the verifier that produced it, "
                           "so there is no way to ask whether it can be trusted")
        record = self.verifier_certification(verifier_ref, capability_version)
        if record is None:
            return False, (f"{verifier_ref} has never been tested against "
                           f"{capability_version}; an untested verifier's PASS is "
                           "an opinion, not evidence")
        state = record["state"]
        if state == self.FAILED:
            return False, (f"{verifier_ref} failed certification for "
                           f"{capability_version}: {record['why']}")
        if state == self.REVOKED:
            return False, (f"{verifier_ref} had its certification revoked for "
                           f"{capability_version}: {record['why']}")
        certified = [c for c in record["certified_checks"].split(",") if c]
        if state == self.CERTIFIED and not certified:
            return False, (f"{verifier_ref} is recorded CERTIFIED for "
                           f"{capability_version} but on no checks")
        if check and check not in certified:
            return False, (f"{verifier_ref} is not certified to decide "
                           f"{check!r} for {capability_version}; it is trusted "
                           f"only on: {', '.join(certified) or 'nothing'}")
        return True, f"{verifier_ref} is {state} for {check!r}"

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

        # A gap that only ever produces a refusal teaches the owner nothing. If
        # the job needs something Solvent cannot do, that becomes a question in
        # the owner's queue — once. The job is still refused: proposing is not
        # permission, and nothing here changes this assessment's verdict.
        if verdict is CapabilityVerdict.REQUIRES_NEW_CAPABILITY:
            for gap in gaps:
                if self.development_decision(gap)[0] or self._proposals_for(gap):
                    continue
                self.propose(name=gap, covers=frozenset({gap}), job_id=job_id,
                             why=f"job {job_id} needed {gap!r}, which no "
                                 "registered capability covers")
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
