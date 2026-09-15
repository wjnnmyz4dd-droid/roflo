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
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .audit import AuditLog, new_id, now
from .capability import Assessment
from .errors import FailClosed
from .governor import Estimate, FinancialGovernor
from .policy import PolicyStore
from .store import Store
from .types import (
    BlockedOn, Cents, ConsequenceTier, GovernorVerdict, JobState, LocationSignals,
    OperatingMode, Requirement, VerificationTier,
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
                                 JobState.FAILED}),
    JobState.VERIFYING: frozenset({JobState.READY_FOR_DELIVERY, JobState.EXECUTING,
                                   JobState.BLOCKED, JobState.FAILED}),
    JobState.READY_FOR_DELIVERY: frozenset({JobState.AWAITING_PAYMENT,
                                            JobState.BLOCKED, JobState.FAILED}),
    JobState.AWAITING_PAYMENT: frozenset({JobState.COMPLETE, JobState.BLOCKED,
                                          JobState.FAILED}),
    JobState.COMPLETE: frozenset(),
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
    JobState.AWAITING_PAYMENT: 20_160,  # 14 days
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
                 governor: FinancialGovernor) -> None:
        self._db = store.for_authority("orchestrator")
        self._audit = audit
        self._policy = policy
        self._governor = governor

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
        self._db.execute(
            "INSERT OR REPLACE INTO requirements(id,job_id,text,source,confirmed_by) "
            "VALUES(?,?,?,?,?)",
            (requirement.id, job_id, requirement.text, requirement.source.value,
             requirement.confirmed_by))
        self._db.commit()

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

    def qualify(self, *, job_id: str, assessment: Assessment, estimate: Estimate,
                initiator: str) -> GovernorVerdict:
        """Run conformance, then the Governor, then move to the right state.

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
        if not owner_identity.startswith("owner:"):
            raise FailClosed(
                f"approval requires an authenticated owner identity (got "
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
        """Does recorded evidence meet what this job's consequence demands?"""
        required = self.required_tier(job_id)
        best = self._audit.best_tier(job_id)
        if best is None:
            return False, f"no verification evidence; {required.value} required"
        if best.rank < required.rank:
            return False, (f"best evidence is {best.value}, but {required.value} "
                           "is required at this consequence tier")
        return True, f"{best.value} satisfies {required.value}"

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

    def complete(self, *, job_id: str, initiator: str, collected_cents: Cents,
                 verification_method: str) -> None:
        """Completion requires externally verified payment, not an invoice."""
        if not verification_method:
            raise FailClosed(
                "completion requires externally verified payment; "
                "expected revenue is not collected revenue")
        if collected_cents <= 0:
            raise FailClosed("completion requires collected revenue")
        self._transition(job_id, JobState.COMPLETE,
                         why=f"payment verified via {verification_method}",
                         initiator=initiator)

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
