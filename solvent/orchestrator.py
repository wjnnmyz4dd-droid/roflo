"""Job Orchestrator — the one component that advances a job.

Owns one decision: *what state is this job in, and may it advance?*

It holds no money rule and no permission rule. It asks the Governor whether a
commitment is affordable and the Policy Store whether an action class is allowed,
and it may not proceed when either refuses. The Orchestrator cannot bypass Policy
any more than a worker can.

**No silent stalls.** Every state carries a timeout. A timeout escalates and never
auto-advances, because a job that quietly stops is indistinguishable from a job
that quietly failed.

**Verify before learn, enforced structurally.** A job cannot reach delivery or any
terminal success state without a :class:`VerificationEvidence` record at the tier
its consequence demands. That is a guard in the transition table, not a
convention, so there is no path to "delivered" that skips proving the work.

Twelve states, consolidated from the fifteen originally proposed: four waiting
states collapse into ``BLOCKED`` plus a required ``blocked_on``, ``DELIVERED``
became an event rather than a state, and ``REJECTED`` was added because declining
work is a success of the system and conflating it with ``FAILED`` would corrupt
every learning statistic.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from datetime import datetime, timedelta, timezone

from .csvverify import CHECKS
from .csvwork import digest_of

from .audit import AuditLog, new_id, now
from .capability import Assessment
from .errors import FailClosed
from .governor import Estimate, FinancialGovernor
from .policy import PolicyStore
from .store import Store
from .types import (
    Artifact, BlockedOn, Cents, ConsequenceTier, Criticality, GovernorVerdict,
    JobState, LocationSignals, OperatingMode, Requirement, RequirementSource,
    RequirementStatus, VerificationTier,
)

#: The only legal moves. Anything absent here is not a transition, it is a bug.
TRANSITIONS: dict[JobState, frozenset[JobState]] = {
    JobState.INTAKE: frozenset({JobState.QUALIFYING, JobState.REJECTED,
                                JobState.BLOCKED, JobState.FAILED}),
    JobState.QUALIFYING: frozenset({JobState.AWAITING_OWNER_APPROVAL,
                                    JobState.ACCEPTED, JobState.REJECTED,
                                    JobState.BLOCKED, JobState.FAILED}),
    JobState.AWAITING_OWNER_APPROVAL: frozenset({JobState.ACCEPTED, JobState.REJECTED,
                                                 JobState.BLOCKED, JobState.FAILED}),
    JobState.ACCEPTED: frozenset({JobState.EXECUTING, JobState.BLOCKED,
                                  JobState.FAILED}),
    JobState.EXECUTING: frozenset({JobState.VERIFYING, JobState.BLOCKED,
                                   JobState.FAILED}),
    # Every state that may enter BLOCKED must be reachable from it, or a job can
    # be blocked into a corner it cannot leave. ``test_orchestrator`` asserts that
    # invariant so this set cannot drift out of step with the ones above.
    JobState.BLOCKED: frozenset({JobState.INTAKE, JobState.QUALIFYING,
                                 JobState.AWAITING_OWNER_APPROVAL,
                                 JobState.ACCEPTED, JobState.EXECUTING,
                                 JobState.VERIFYING, JobState.READY_FOR_DELIVERY,
                                 JobState.AWAITING_PAYMENT, JobState.REJECTED,
                                 JobState.FAILED, JobState.REVISION_REQUESTED}),
    JobState.VERIFYING: frozenset({JobState.READY_FOR_DELIVERY, JobState.EXECUTING,
                                   JobState.BLOCKED, JobState.FAILED}),
    JobState.READY_FOR_DELIVERY: frozenset({JobState.AWAITING_PAYMENT,
                                            JobState.BLOCKED, JobState.FAILED}),
    JobState.AWAITING_PAYMENT: frozenset({JobState.COMPLETE, JobState.BLOCKED,
                                          JobState.FAILED,
                                          JobState.REVISION_REQUESTED}),
    # COMPLETE is no longer a dead end, but it is not editable either: the only
    # way out is REVISION_REQUESTED, which the client-feedback path opens and
    # which leaves every historical record exactly where it was.
    JobState.COMPLETE: frozenset({JobState.REVISION_REQUESTED}),
    # Either the job goes back to work, or the review concludes nothing is owed
    # and it settles again. Both are explicit; neither rewrites the first pass.
    JobState.REVISION_REQUESTED: frozenset({JobState.EXECUTING, JobState.COMPLETE,
                                            JobState.BLOCKED, JobState.FAILED}),
    JobState.REJECTED: frozenset(),
    JobState.FAILED: frozenset(),
}

#: How long a state may sit before it escalates. Every state has one.
TIMEOUTS_MINUTES: dict[JobState, int] = {
    JobState.INTAKE: 60,
    JobState.QUALIFYING: 60,
    JobState.AWAITING_OWNER_APPROVAL: 20,
    JobState.ACCEPTED: 120,
    JobState.EXECUTING: 240,
    JobState.BLOCKED: 120,
    JobState.VERIFYING: 60,
    JobState.READY_FOR_DELIVERY: 60,
    JobState.AWAITING_PAYMENT: 20_160,   # 14 days
    # Not a stall: the window in which a delivered job may still be revised.
    # After it, the job is settled and a complaint is new scope.
    JobState.COMPLETE: 43_200,           # 30 days
    # A client has raised something. Two days is how long it may sit unanswered.
    JobState.REVISION_REQUESTED: 2_880,  # 48 hours
}

#: Verification a job must carry before it may be delivered or completed.
REQUIRED_TIER: dict[ConsequenceTier, VerificationTier] = {
    ConsequenceTier.C_LOW: VerificationTier.T1_DETERMINISTIC,
    ConsequenceTier.C_MED: VerificationTier.T1_DETERMINISTIC,
    ConsequenceTier.C_HIGH: VerificationTier.T2_INDEPENDENT_REVIEW,
    ConsequenceTier.C_CRITICAL: VerificationTier.T3_EXTERNAL_FACT,
}


@dataclass(slots=True)
class Job:
    id: str
    state: JobState
    title: str
    client_id: str
    quoted_cents: Cents
    consequence: ConsequenceTier
    blocked_on: BlockedOn | None = None
    resume_state: JobState | None = None
    grant_id: str | None = None
    job_class: str = "general"
    fail_reason: str = ""


class JobOrchestrator:
    """The job state machine. The only writer of job state."""

    def __init__(self, store: Store, audit: AuditLog, policy: PolicyStore,
                 governor: FinancialGovernor, ledger=None) -> None:
        self._db = store.for_authority("orchestrator")
        self._audit = audit
        self._policy = policy
        self._governor = governor
        # Completion asks the Ledger what was actually collected rather than
        # believing its caller. Without it, "complete" is an assertion.
        self._ledger = ledger

    # ----------------------------------------------------------------- intake

    def intake(self, *, title: str, client_id: str, quoted_cents: Cents,
               signals: LocationSignals, initiator: str,
               job_class: str = "general") -> str:
        """Record an opportunity. P0 inserts these manually; no discovery."""
        if self._policy.operating_mode.blocks_new_work:
            raise FailClosed(
                f"operating_mode={self._policy.operating_mode.value} blocks new work")
        job_id = new_id("job")
        consequence = self._policy.consequence_for(quoted_cents)
        self._db.execute(
            "INSERT INTO jobs(id,created_at,updated_at,state,title,client_id,"
            "quoted_cents,consequence,state_entered_at,signals,job_class) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (job_id, now(), now(), JobState.INTAKE.value, title, client_id,
             quoted_cents, consequence.value, now(),
             json.dumps(_signals_to_dict(signals)), job_class))
        self._db.commit()
        self._audit.record(
            event="job.intake", authority="orchestrator", initiator=initiator,
            why="opportunity recorded", job_id=job_id, decision=JobState.INTAKE.value,
            title=title, consequence=consequence.value)
        return job_id

    def add_requirement(self, job_id: str, requirement: Requirement) -> None:
        """Add a requirement to the draft checklist.

        A committed requirement cannot be overwritten through this path. That is
        what makes the baseline a baseline: once execution starts against it, the
        only way it changes is :meth:`amend_requirement`, on the record.
        """
        existing = self._db.query_one(
            "SELECT status FROM requirements WHERE job_id = ? AND id = ?",
            (job_id, requirement.id))
        if existing and existing["status"] == RequirementStatus.COMMITTED.value:
            raise FailClosed(
                f"{requirement.id} is committed; use amend_requirement so the "
                "change is visible rather than silent")
        self._write_requirement(job_id, requirement)

    def _write_requirement(self, job_id: str, requirement: Requirement,
                           committed_at: str | None = None) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO requirements(id,job_id,text,source,confirmed_by,"
            "acceptance,check_name,params,criticality,status,supersedes,committed_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (requirement.id, job_id, requirement.text, requirement.source.value,
             requirement.confirmed_by, requirement.acceptance, requirement.check,
             json.dumps(requirement.params), requirement.criticality.value,
             requirement.status.value, requirement.supersedes, committed_at))
        self._db.commit()

    def commit_requirements(self, *, job_id: str, initiator: str,
                            known_checks=None) -> list[Requirement]:
        """Freeze the checklist this job will be judged against.

        Every mandatory requirement must name a registered check. A checklist
        item nobody can test is not a requirement — it is a hope, and it would
        pass the delivery gate by being unverifiable rather than by being met.

        Returns the committed baseline.
        """
        drafts = [r for r in self.requirement_records(job_id)
                  if r.status is RequirementStatus.DRAFT]
        if not drafts:
            raise FailClosed(f"{job_id} has no requirements to commit; a job with "
                             "no checklist cannot be verified and must not start")

        # Which checks exist is a property of the capability running this job,
        # which the caller knows and the Orchestrator should not. It owns the
        # *rule* — a mandatory requirement must be testable — not the catalogue.
        checks = CHECKS if known_checks is None else known_checks
        untestable = [r.id for r in drafts
                      if r.criticality.blocks_delivery and r.check not in checks]
        if untestable:
            raise FailClosed(
                f"mandatory requirement(s) {untestable} name no registered check; "
                "an untestable requirement would pass the gate by being "
                "unverifiable rather than by being satisfied")

        ungated = [r.id for r in drafts if not r.source.may_gate_commitment]
        if ungated:
            raise FailClosed(
                f"requirement(s) {ungated} are model-extracted and unconfirmed; "
                "they may not gate a commitment")

        stamp = now()
        committed = []
        for draft in drafts:
            frozen = replace(draft, status=RequirementStatus.COMMITTED)
            self._write_requirement(job_id, frozen, committed_at=stamp)
            committed.append(frozen)
        self._audit.record(
            event="requirements.committed", authority="orchestrator",
            initiator=initiator, why="baseline frozen before execution",
            job_id=job_id, decision="COMMITTED",
            result=f"{len(committed)} requirement(s): "
                   + ", ".join(sorted(r.id for r in committed)))
        return committed

    def amend_requirement(self, *, job_id: str, replacing: str,
                          replacement: Requirement, owner_identity: str,
                          why: str) -> Requirement:
        """Change a committed requirement, visibly, on the owner's authority.

        The original row is not edited. It is marked AMENDED and the replacement
        records what it supersedes, so the history shows both what was agreed and
        what it became.
        """
        if not self._policy.is_owner(owner_identity):
            raise FailClosed(
                f"amending a committed requirement is an owner act; "
                f"{owner_identity!r} is not a registered owner")
        current = {r.id: r for r in self.requirement_records(job_id)}
        if replacing not in current:
            raise FailClosed(f"{replacing} is not a requirement of {job_id}")
        if replacement.id == replacing:
            raise FailClosed("an amendment needs its own id; reusing the old one "
                             "would overwrite the record it is meant to preserve")

        self._write_requirement(
            job_id, replace(current[replacing], status=RequirementStatus.AMENDED),
            committed_at=now())
        amended = replace(replacement, supersedes=replacing,
                          status=RequirementStatus.COMMITTED)
        self._write_requirement(job_id, amended, committed_at=now())
        self._audit.record(
            event="requirements.amended", authority="orchestrator",
            initiator=owner_identity, why=why, job_id=job_id, decision="AMENDED",
            input_ref=replacing, result=f"{replacing} -> {amended.id}")
        return amended

    def withdraw_requirement(self, *, job_id: str, requirement_id: str,
                             owner_identity: str, why: str) -> None:
        """Remove a committed requirement. Owner only, and never silently."""
        if not self._policy.is_owner(owner_identity):
            raise FailClosed(f"withdrawing a requirement is an owner act; "
                             f"{owner_identity!r} is not a registered owner")
        current = {r.id: r for r in self.requirement_records(job_id)}
        if requirement_id not in current:
            raise FailClosed(f"{requirement_id} is not a requirement of {job_id}")
        self._write_requirement(
            job_id, replace(current[requirement_id],
                            status=RequirementStatus.WITHDRAWN),
            committed_at=now())
        self._audit.record(
            event="requirements.withdrawn", authority="orchestrator",
            initiator=owner_identity, why=why, job_id=job_id, decision="WITHDRAWN",
            input_ref=requirement_id, result="removed from the baseline")

    def requirement_records(self, job_id: str) -> list[Requirement]:
        """The checklist as typed objects, in insertion order."""
        out = []
        for row in self._db.query(
                "SELECT * FROM requirements WHERE job_id = ? ORDER BY rowid",
                (job_id,)):
            out.append(Requirement(
                id=row["id"], text=row["text"],
                source=RequirementSource(row["source"]),
                confirmed_by=row["confirmed_by"], acceptance=row["acceptance"],
                check=row["check_name"], params=json.loads(row["params"] or "{}"),
                criticality=Criticality(row["criticality"]),
                status=RequirementStatus(row["status"]),
                supersedes=row["supersedes"]))
        return out

    def baseline(self, job_id: str) -> list[Requirement]:
        """The committed requirements delivery will actually be judged against."""
        return [r for r in self.requirement_records(job_id)
                if r.status is RequirementStatus.COMMITTED]

    # -------------------------------------------------------------- artifacts

    def register_artifact(self, *, job_id: str, role: str, path: str,
                          produced_by: str = "", capability_version: str = "",
                          source_digest: str = "", media_type: str = "text/csv",
                          initiator: str = "execution") -> Artifact:
        """Record a file by its content, computing the digest here.

        The digest is never taken from the caller. An artifact identity supplied
        by whoever produced the artifact would let a worker name a file it did
        not write, which is precisely the binding this exists to make real.
        """
        target = Path(path)
        if not target.exists():
            raise FailClosed(f"cannot register an artifact that does not exist: {path}")
        digest = digest_of(target)
        artifact = Artifact(
            id=new_id("art"), job_id=job_id, role=role, path=str(target),
            digest=digest, size=target.stat().st_size, media_type=media_type,
            produced_by=produced_by, capability_version=capability_version,
            source_digest=source_digest)
        self._db.execute(
            "INSERT INTO artifacts(id,ts,job_id,role,path,digest,size,media_type,"
            "produced_by,capability_version,source_digest) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (artifact.id, now(), job_id, role, artifact.path, digest, artifact.size,
             media_type, produced_by, capability_version, source_digest))
        self._db.commit()
        self._audit.record(
            event="artifact.registered", authority="orchestrator", initiator=initiator,
            why=f"{role} artifact", job_id=job_id, input_ref=artifact.id,
            decision=role, result=f"{digest[:16]}… {artifact.size} bytes")
        return artifact

    def artifacts(self, job_id: str, role: str | None = None) -> list[Artifact]:
        sql = "SELECT * FROM artifacts WHERE job_id = ?"
        params: tuple = (job_id,)
        if role:
            sql += " AND role = ?"
            params = (job_id, role)
        return [Artifact(id=r["id"], job_id=r["job_id"], role=r["role"],
                         path=r["path"], digest=r["digest"], size=r["size"],
                         media_type=r["media_type"], produced_by=r["produced_by"],
                         capability_version=r["capability_version"],
                         source_digest=r["source_digest"])
                for r in self._db.query(sql + " ORDER BY ts", params)]

    def current_deliverable(self, job_id: str) -> Artifact | None:
        """The newest deliverable. Earlier ones stay on the record."""
        produced = self.artifacts(job_id, role="DELIVERABLE")
        return produced[-1] if produced else None

    # ------------------------------------------------------------ transitions

    def job(self, job_id: str) -> Job:
        row = self._db.query_one("SELECT * FROM jobs WHERE id = ?", (job_id,))
        if row is None:
            raise FailClosed(f"unknown job {job_id!r}")
        return Job(
            id=row["id"], state=JobState(row["state"]), title=row["title"],
            client_id=row["client_id"], quoted_cents=int(row["quoted_cents"]),
            consequence=ConsequenceTier(row["consequence"]),
            blocked_on=BlockedOn(row["blocked_on"]) if row["blocked_on"] else None,
            resume_state=JobState(row["resume_state"]) if row["resume_state"] else None,
            grant_id=row["grant_id"], job_class=row["job_class"],
            fail_reason=row["fail_reason"])

    def _transition(self, job_id: str, target: JobState, *, why: str, initiator: str,
                    blocked_on: BlockedOn | None = None,
                    resume_state: JobState | None = None,
                    fail_reason: str = "", grant_id: str | None = None) -> None:
        job = self.job(job_id)
        if target not in TRANSITIONS[job.state]:
            raise FailClosed(
                f"illegal transition {job.state.value} -> {target.value} for {job_id}")
        if target is JobState.BLOCKED and blocked_on is None:
            raise FailClosed("BLOCKED requires blocked_on: a blocker with no owner "
                             "is a silent stall")
        self._db.execute(
            "UPDATE jobs SET state=?, blocked_on=?, resume_state=?, updated_at=?, "
            "state_entered_at=?, fail_reason=?, grant_id=COALESCE(?, grant_id) "
            "WHERE id=?",
            (target.value, blocked_on.value if blocked_on else None,
             resume_state.value if resume_state else None, now(), now(),
             fail_reason or job.fail_reason, grant_id, job_id))
        self._db.commit()
        self._audit.record(
            event="job.transition", authority="orchestrator", initiator=initiator,
            why=why, job_id=job_id,
            decision=f"{job.state.value} -> {target.value}",
            result=blocked_on.value if blocked_on else "")

    # ------------------------------------------------------------ qualifying

    def assess_financials(self, *, job_id: str, assessment: Assessment,
                          estimate: Estimate, initiator: str) -> tuple[GovernorVerdict, str]:
        """Get the financial verdict and hold the job in QUALIFYING.

        Separated from committing so that several candidates can be judged and
        ranked *before* any of them is accepted. Deciding a job is worth doing and
        deciding to start it now are different questions, and answering the second
        one first is how a business ends up taking the first job it sees instead of
        the best one.
        """
        job = self.job(job_id)
        if job.state is JobState.INTAKE:
            self._transition(job_id, JobState.QUALIFYING, why="assessing",
                             initiator=initiator)
        return self._governor.decide(
            job_id=job_id, revenue_cents=job.quoted_cents, estimate=estimate,
            conformance_ok=assessment.may_commit, initiator=initiator)

    def apply_verdict(self, *, job_id: str, verdict: GovernorVerdict, reason: str,
                      initiator: str) -> None:
        """Move a held job to the state its verdict implies."""
        if verdict is GovernorVerdict.PROFITABLE:
            self._transition(job_id, JobState.ACCEPTED, why=reason, initiator=initiator)
        elif verdict is GovernorVerdict.REQUIRES_APPROVAL:
            self._transition(job_id, JobState.AWAITING_OWNER_APPROVAL, why=reason,
                             initiator=initiator)
        elif verdict in (GovernorVerdict.NEEDS_INFORMATION,
                         GovernorVerdict.PRICING_LOCATION_UNKNOWN):
            self._transition(job_id, JobState.BLOCKED, why=reason, initiator=initiator,
                             blocked_on=BlockedOn.INFORMATION,
                             resume_state=JobState.QUALIFYING)
        else:
            self._transition(job_id, JobState.REJECTED, why=reason,
                             initiator=initiator, fail_reason=reason)

    def qualify(self, *, job_id: str, assessment: Assessment, estimate: Estimate,
                initiator: str) -> GovernorVerdict:
        """Assess and commit in one step, for the single-job path.

        The order is the law: conformance before price, location before a binding
        price, Governor before any commitment.
        """
        job = self.job(job_id)
        if job.state is JobState.INTAKE:
            self._transition(job_id, JobState.QUALIFYING, why="assessing",
                             initiator=initiator)

        verdict, reason = self._governor.decide(
            job_id=job_id, revenue_cents=job.quoted_cents, estimate=estimate,
            conformance_ok=assessment.may_commit, initiator=initiator)

        if verdict is GovernorVerdict.PROFITABLE:
            self._transition(job_id, JobState.ACCEPTED, why=reason, initiator=initiator)
        elif verdict is GovernorVerdict.REQUIRES_APPROVAL:
            self._transition(job_id, JobState.AWAITING_OWNER_APPROVAL, why=reason,
                             initiator=initiator)
        elif verdict in (GovernorVerdict.NEEDS_INFORMATION,
                         GovernorVerdict.PRICING_LOCATION_UNKNOWN):
            self._transition(job_id, JobState.BLOCKED, why=reason, initiator=initiator,
                             blocked_on=BlockedOn.INFORMATION,
                             resume_state=JobState.QUALIFYING)
        else:
            self._transition(job_id, JobState.REJECTED, why=reason, initiator=initiator,
                             fail_reason=reason)
        return verdict

    def owner_approves(self, *, job_id: str, owner_identity: str, why: str) -> None:
        if not self._policy.is_owner(owner_identity):
            raise FailClosed(
                f"approval requires a registered owner identity (got "
                f"{owner_identity!r}); a notification is not an authorization")
        self._transition(job_id, JobState.ACCEPTED, why=why, initiator=owner_identity)

    def owner_declines(self, *, job_id: str, owner_identity: str, why: str) -> None:
        self._transition(job_id, JobState.REJECTED, why=why, initiator=owner_identity,
                         fail_reason=why)

    # ------------------------------------------------------------- execution

    def begin_execution(self, *, job_id: str, grant_id: str, initiator: str) -> None:
        """Work may not start without a Governor budget grant."""
        if not grant_id:
            raise FailClosed("execution requires a Financial Governor budget grant")
        self._transition(job_id, JobState.EXECUTING, why="work started",
                         initiator=initiator, grant_id=grant_id)

    def block(self, *, job_id: str, blocked_on: BlockedOn, why: str,
              initiator: str) -> None:
        """Raise a blocker. Never fabricate the missing information instead."""
        job = self.job(job_id)
        self._transition(job_id, JobState.BLOCKED, why=why, initiator=initiator,
                         blocked_on=blocked_on, resume_state=job.state)

    def unblock(self, *, job_id: str, why: str, initiator: str) -> None:
        job = self.job(job_id)
        if job.state is not JobState.BLOCKED or job.resume_state is None:
            raise FailClosed(f"{job_id} is not blocked with a resume state")
        self._transition(job_id, job.resume_state, why=why, initiator=initiator)

    def submit_for_verification(self, *, job_id: str, initiator: str) -> None:
        self._transition(job_id, JobState.VERIFYING, why="work submitted",
                         initiator=initiator)

    # ---------------------------------------------------------- verification

    def required_tier(self, job_id: str) -> VerificationTier:
        return REQUIRED_TIER[self.job(job_id).consequence]

    def verification_satisfied(self, job_id: str) -> tuple[bool, str]:
        """May this job be delivered?

        The old version of this method asked only whether *somebody had attested
        at a high enough tier*. That is how three client requirements could be
        recorded, none met, none checked, and the job delivered anyway. It now
        asks the question that actually protects the client:

            does **every mandatory committed requirement** have a **PASS**
            recorded for the **exact artifact about to be delivered**?

        Scoping by digest is what makes a correction safe. A new artifact has a
        new digest, so every pass earned by the previous version stops counting
        the moment the file changes — there is no way to inherit a verification
        across a correction.
        """
        required = self.required_tier(job_id)
        baseline = self.baseline(job_id)
        if not baseline:
            return False, ("no committed requirement baseline; there is nothing "
                           "to have verified")

        deliverable = self.current_deliverable(job_id)
        if deliverable is None:
            return False, "no deliverable artifact has been registered"

        on_disk = Path(deliverable.path)
        if not on_disk.exists():
            return False, f"the registered deliverable is missing: {deliverable.path}"
        actual = digest_of(on_disk)
        if actual != deliverable.digest:
            # The file changed after it was registered. Every pass was earned
            # against different bytes.
            return False, (f"the deliverable changed since it was registered "
                           f"({deliverable.short}… on record, {actual[:16]}… on "
                           "disk); its verifications no longer apply")

        passes = self._audit.passes_for_artifact(job_id, deliverable.digest)
        mandatory = [r for r in baseline if r.criticality.blocks_delivery]
        missing = [r.id for r in mandatory if r.id not in passes]
        if missing:
            return False, (f"{len(missing)} of {len(mandatory)} mandatory "
                           f"requirement(s) have no pass for {deliverable.short}…: "
                           + ", ".join(sorted(missing)))

        weak = sorted(r.id for r in mandatory
                      if VerificationTier(passes[r.id]["tier"]).rank < required.rank)
        if weak:
            return False, (f"requirement(s) {weak} are verified below "
                           f"{required.value}, which this consequence tier requires")

        return True, (f"{len(mandatory)} mandatory requirement(s) pass at "
                      f"{required.value} for artifact {deliverable.short}…")

    def verification_report(self, job_id: str) -> list[dict]:
        """Requirement-by-requirement status for the owner and the client.

        Reads; decides nothing. A requirement with no evidence for the current
        artifact reads as ``NO_EVIDENCE`` rather than as a failure, because those
        are different situations and collapsing them hides which one happened.
        """
        deliverable = self.current_deliverable(job_id)
        passes = (self._audit.passes_for_artifact(job_id, deliverable.digest)
                  if deliverable else {})
        out = []
        for req in self.baseline(job_id):
            row = passes.get(req.id)
            out.append({
                "requirement_id": req.id, "text": req.text,
                "source": req.source.value, "criticality": req.criticality.value,
                "check": req.check,
                "status": "PASS" if row else "NO_EVIDENCE",
                "tier": row["tier"] if row else "",
                "evidence": row["raw_output"] if row else "",
                "artifact": deliverable.short if deliverable else "",
            })
        return out

    def mark_verified(self, *, job_id: str, initiator: str) -> None:
        """Advance to delivery only when the evidence actually exists."""
        ok, reason = self.verification_satisfied(job_id)
        if not ok:
            raise FailClosed(f"cannot mark verified: {reason}")
        self._transition(job_id, JobState.READY_FOR_DELIVERY, why=reason,
                         initiator=initiator)

    def record_delivery(self, *, job_id: str, initiator: str, gate_request_id: str,
                        why: str = "delivered") -> None:
        """Delivery is an event; the resulting state is AWAITING_PAYMENT."""
        ok, reason = self.verification_satisfied(job_id)
        if not ok:
            raise FailClosed(f"refusing to deliver unverified work: {reason}")
        self._audit.record(
            event="delivery.completed", authority="orchestrator", initiator=initiator,
            why=why, job_id=job_id, external_effect=f"delivery via {gate_request_id}",
            verification_ref=reason)
        self._transition(job_id, JobState.AWAITING_PAYMENT, why=why,
                         initiator=initiator)

    def complete(self, *, job_id: str, initiator: str) -> None:
        """Completion requires externally verified payment, read from the Ledger.

        The caller does not get to assert that it was paid. The Ledger is the only
        authority on what money arrived, so this asks it.
        """
        if self._ledger is None:
            raise FailClosed(
                "completion requires a Ledger; the Orchestrator may not take a "
                "caller's word for payment")
        collected = self._ledger.collected_for(job_id)
        if collected <= 0:
            raise FailClosed(
                f"cannot complete {job_id}: the Ledger records no externally "
                "verified collected revenue. Expected revenue is not collected "
                "revenue")
        payment = self._ledger.payment_for(job_id) or {}
        method = payment.get("verification_method", "")
        self._transition(job_id, JobState.COMPLETE,
                         why=f"payment of {collected} cents verified via {method}",
                         initiator=initiator)

    def reject(self, *, job_id: str, reason: str, initiator: str) -> None:
        """Decline work. A rejection is an outcome, not a failure.

        Kept distinct from :meth:`fail` because declining well is a capability:
        conflating the two would corrupt every learning and autonomy statistic.
        """
        job = self.job(job_id)
        if job.state.is_terminal:
            return
        if job.state is JobState.INTAKE:
            self._transition(job_id, JobState.QUALIFYING, why=reason,
                             initiator=initiator)
        self._transition(job_id, JobState.REJECTED, why=reason, initiator=initiator,
                         fail_reason=reason)

    def fail(self, *, job_id: str, reason: str, initiator: str) -> None:
        self._transition(job_id, JobState.FAILED, why=reason, initiator=initiator,
                         fail_reason=reason)

    # -------------------------------------------------------------- timeouts

    def overdue(self) -> list[dict]:
        """Jobs past their state timeout. Escalation material, never auto-advance."""
        overdue = []
        for row in self._db.query("SELECT * FROM jobs"):
            state = JobState(row["state"])
            if state.is_terminal:
                continue
            limit = TIMEOUTS_MINUTES.get(state)
            if limit is None:
                continue
            entered = datetime.fromisoformat(row["state_entered_at"])
            if datetime.now(timezone.utc) - entered > timedelta(minutes=limit):
                overdue.append({"job_id": row["id"], "state": state.value,
                                "blocked_on": row["blocked_on"],
                                "minutes_allowed": limit,
                                "entered_at": row["state_entered_at"]})
        return overdue

    def escalate_overdue(self, initiator: str = "orchestrator") -> list[dict]:
        """Escalate every overdue job. A timeout never advances a job by itself."""
        items = self.overdue()
        for item in items:
            self._audit.record(
                event="job.timeout", authority="orchestrator", initiator=initiator,
                why=f"state {item['state']} exceeded {item['minutes_allowed']} minutes",
                job_id=item["job_id"], decision="ESCALATE",
                result="owner attention required")
        return items

    def jobs(self) -> list[Job]:
        return [self.job(r["id"]) for r in self._db.query("SELECT id FROM jobs")]

    def requirements(self, job_id: str) -> list[dict]:
        return [dict(r) for r in self._db.query(
            "SELECT * FROM requirements WHERE job_id = ?", (job_id,))]


def _signals_to_dict(signals: LocationSignals) -> dict:
    out = {}
    for name in ("project", "client", "worker", "material_destination",
                 "contract_clause"):
        value = getattr(signals, name, None)
        if value:
            out[name] = value.path()
    return out
