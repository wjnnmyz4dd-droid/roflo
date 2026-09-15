"""Qualification & Ranking — deciding which work is worth taking.

Owns one decision: *accept, reject, or escalate this opportunity — and of the
acceptable ones, which first?*

It combines four answers it does not produce itself:

* **Capability & Conformance** — can Solvent actually do this, and does it fit?
* **Financial Governor** — is it worth doing? Every number here comes from the
  Governor. This module contains no margin arithmetic, no floor, and no cost
  model, because a second place that computes economics is a second financial
  authority.
* **Policy** — is the action class permitted at all?
* **Business Memory** — what has this client done before?

Ranking consumes Governor-approved economics and adds only the things economics
cannot see: how likely Solvent is to finish, what the client has been like, and
what else is competing for the same capacity. **Opportunity cost lives here, as a
ranking input — never as a second opinion on whether a job is profitable.**

**Understanding a job is not being able to do it, and being able to do it is not
being allowed to.** The separation between those is the point of this module:

    DISCOVERY ≠ QUALIFICATION ≠ CAPABILITY ≠ PROFITABILITY ≠ PERMISSION
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .audit import AuditLog, new_id, now
from .content import RequirementSet, confirm, extract_requirements
from .discovery import Opportunity, OpportunityStatus
from .errors import FailClosed
from .store import Store
from .types import (
    CapabilityVerdict, Cents, Conformance, CostCategory, GovernorVerdict, JobState,
    Requirement, RequirementSource, fmt,
)


class Verdict(Enum):
    """What Solvent decided about one opportunity."""

    ACCEPT = "ACCEPT"
    NEEDS_OWNER_APPROVAL = "NEEDS_OWNER_APPROVAL"
    NEEDS_INFORMATION = "NEEDS_INFORMATION"
    REJECT = "REJECT"


class RejectReason(Enum):
    """Why Solvent said no. Recorded because *why* is a business metric.

    Solvent is not optimised for number of jobs. Declining well is a capability,
    and the distribution of these reasons is how the owner can see whether it is
    declining for good reasons.
    """

    CANNOT_EXECUTE = "CANNOT_EXECUTE"
    MISSING_CAPABILITY = "MISSING_CAPABILITY"
    NON_CONFORMING = "NON_CONFORMING"
    INSUFFICIENT_INFORMATION = "INSUFFICIENT_INFORMATION"
    UNSUPPORTED_JURISDICTION = "UNSUPPORTED_JURISDICTION"
    MARGIN_TOO_LOW = "MARGIN_TOO_LOW"
    BELOW_MINIMUM_VALUE = "BELOW_MINIMUM_VALUE"
    EXCESSIVE_RISK = "EXCESSIVE_RISK"
    NO_CAPACITY = "NO_CAPACITY"
    UNREALISTIC_DEADLINE = "UNREALISTIC_DEADLINE"
    MISSING_PERMISSION = "MISSING_PERMISSION"
    PROHIBITED = "PROHIBITED"
    BAD_PAYMENT_HISTORY = "BAD_PAYMENT_HISTORY"
    BETTER_OPPORTUNITY_AVAILABLE = "BETTER_OPPORTUNITY_AVAILABLE"
    SOURCE_NOT_PERMITTED = "SOURCE_NOT_PERMITTED"


@dataclass(slots=True)
class Decision:
    """One opportunity's outcome, with everything needed to explain it."""

    opportunity: Opportunity
    verdict: Verdict
    reason: str
    reject_reason: RejectReason | None = None
    job_id: str | None = None
    governor_verdict: GovernorVerdict | None = None
    conformance: Conformance | None = None
    expected_profit_cents: Cents = 0
    estimated_hours: float = 0.0
    score: float = 0.0
    rank_position: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def accepted(self) -> bool:
        return self.verdict is Verdict.ACCEPT


class Qualification:
    """The one accept/reject/escalate authority. It owns no economics."""

    def __init__(self, store: Store, audit: AuditLog, policy, governor, capability,
                 orchestrator, discovery, memory=None, ledger=None) -> None:
        self._db = store.for_authority("qualification")
        self._audit = audit
        self._policy = policy
        self._governor = governor
        self._capability = capability
        self._orchestrator = orchestrator
        self._discovery = discovery
        self._memory = memory
        self._ledger = ledger

    # ---------------------------------------------------------------- triage

    def triage(self, candidates: list[Opportunity]) -> tuple[list[Opportunity],
                                                             list[Decision]]:
        """Cheap screening before anything expensive happens.

        A marketplace can return hundreds of postings. Creating a job and running a
        full estimate for each would cost real money to reject obvious non-starters,
        so the cheap, certain checks run first. Everything that survives gets the
        full pipeline — triage can only reject, never accept.
        """
        minimum = int(self._policy.get("qualification", "minimum_value_cents",
                                       default=0))
        survivors: list[Opportunity] = []
        rejected: list[Decision] = []
        for candidate in candidates:
            source = self._discovery.source_state(candidate.source)
            if source is None or source["compliance"] != "PERMITTED":
                rejected.append(self._cheap_reject(
                    candidate, RejectReason.SOURCE_NOT_PERMITTED,
                    f"source {candidate.source!r} is not permitted for automation"))
                continue
            if candidate.quoted_cents < minimum:
                rejected.append(self._cheap_reject(
                    candidate, RejectReason.BELOW_MINIMUM_VALUE,
                    f"quoted {fmt(candidate.quoted_cents)} is below the "
                    f"{fmt(minimum)} minimum worth evaluating"))
                continue
            if not candidate.needs:
                rejected.append(self._cheap_reject(
                    candidate, RejectReason.INSUFFICIENT_INFORMATION,
                    "posting states no identifiable required capability"))
                continue
            survivors.append(candidate)
        return survivors, rejected

    def _cheap_reject(self, candidate: Opportunity, reason: RejectReason,
                      detail: str) -> Decision:
        decision = Decision(opportunity=candidate, verdict=Verdict.REJECT,
                            reason=detail, reject_reason=reason)
        self._record(decision)
        self._discovery.set_status(candidate.id, OpportunityStatus.REJECTED)
        return decision

    # ----------------------------------------------------------- qualifying

    def qualify(self, candidate: Opportunity, *, initiator: str = "qualification",
                job_class: str = "general") -> Decision:
        """Put one opportunity through every authority, in the required order.

        Conformance before price. Location before a binding price. Governor before
        any commitment. None of those orders is an implementation detail — each is
        a law, and this method is where they are actually applied.
        """
        job_id = self._orchestrator.intake(
            title=candidate.title, client_id=candidate.client_ref or "unknown",
            quoted_cents=candidate.quoted_cents, signals=candidate.signals,
            initiator=initiator, job_class=job_class)
        self._discovery.set_status(candidate.id, OpportunityStatus.QUALIFYING, job_id)

        requirements = self._requirements_for(candidate)
        for requirement in requirements.items:
            self._orchestrator.add_requirement(job_id, requirement)

        assessment = self._capability.assess(
            job_id=job_id, requirements=requirements, assessor=initiator,
            needs=list(candidate.needs))

        if assessment.conformance is Conformance.FAILS:
            return self._reject(candidate, RejectReason.MISSING_CAPABILITY,
                                assessment.note, initiator, job_id, assessment)
        if not assessment.may_commit:
            # Unknown conformance is not conforming. The posting cannot confirm
            # its own requirements, so a human must.
            return self._escalate(
                candidate, Verdict.NEEDS_INFORMATION,
                f"conformance is {assessment.conformance.value}: {assessment.note}",
                initiator, job_id, assessment,
                reject_reason=RejectReason.NON_CONFORMING)

        hours = float(self._policy.get("qualification", "default_hours",
                                       default=2.0))
        estimate = self._governor.estimate(
            job_id=job_id, revenue_cents=candidate.quoted_cents,
            signals=candidate.signals,
            quantities={CostCategory.ON_SITE_LABOR: hours}, job_class=job_class)

        # Held in QUALIFYING: nothing is committed until ranking has seen every
        # candidate, so a later, better opportunity can still win.
        governor_verdict, governor_reason = self._orchestrator.assess_financials(
            job_id=job_id, assessment=assessment, estimate=estimate,
            initiator=initiator)

        expected_profit = candidate.quoted_cents - estimate.effective_cost_cents
        decision = self._from_governor(
            candidate, governor_verdict, job_id, assessment, expected_profit, hours)
        self._record(decision)
        self._audit.record(
            event="qualification.decided", authority="qualification",
            initiator=initiator, why=decision.reason, job_id=job_id,
            input_ref=candidate.id, decision=decision.verdict.value,
            result=governor_verdict.value)
        if decision.verdict is Verdict.REJECT:
            self._orchestrator.apply_verdict(
                job_id=job_id, verdict=governor_verdict, reason=decision.reason,
                initiator=initiator)
            self._discovery.set_status(candidate.id, OpportunityStatus.REJECTED,
                                       job_id)
        elif decision.verdict is Verdict.NEEDS_INFORMATION:
            self._orchestrator.apply_verdict(
                job_id=job_id, verdict=governor_verdict, reason=decision.reason,
                initiator=initiator)
        return decision

    def _requirements_for(self, candidate: Opportunity) -> RequirementSet:
        """Separate the offer's terms from the offer's prose.

        A platform's structured fields are the client stating terms through a
        schema, so they may gate a commitment. The body is attacker-authored text
        and stays an unconfirmed draft no matter what it says about itself. This is
        what lets Solvent qualify real postings without letting a paragraph inside
        one grant itself authority.
        """
        requirements = RequirementSet()
        if candidate.owner_entered and candidate.needs:
            # The owner typed these in. Nothing is more confirmed than that.
            for need in candidate.needs:
                requirements.add(confirm(
                    Requirement(id=new_id("req"), text=need,
                                source=RequirementSource.MODEL_EXTRACTED_UNCONFIRMED),
                    by=self._owner_identity(),
                    source=RequirementSource.OWNER_CONFIRMED))
            return requirements
        if candidate.structured_terms and candidate.needs:
            for need in candidate.needs:
                requirements.add(confirm(
                    Requirement(id=new_id("req"), text=need,
                                source=RequirementSource.MODEL_EXTRACTED_UNCONFIRMED),
                    by=f"platform:{candidate.source}",
                    source=RequirementSource.CLIENT_CONFIRMED_STRUCTURED))
            return requirements

        # No structured terms: everything must come from prose, so everything is
        # a draft and conformance stays UNKNOWN until a human confirms it. That is
        # the correct outcome for a scraped posting, not a failure of the pipeline.
        if candidate.posting:
            requirements.extend(extract_requirements(
                candidate.posting, [candidate.title]))
        return requirements

    def _from_governor(self, candidate, governor_verdict, job_id, assessment,
                       expected_profit, hours) -> Decision:
        """Translate the Governor's financial verdict into a business verdict.

        The mapping adds no economics: it only names what the Governor already
        decided.
        """
        mapping = {
            GovernorVerdict.PROFITABLE: (
                Verdict.ACCEPT, None, "clears the floor and conforms"),
            GovernorVerdict.REQUIRES_APPROVAL: (
                Verdict.NEEDS_OWNER_APPROVAL, None,
                "above the owner-approval threshold"),
            GovernorVerdict.MARGIN_TOO_LOW: (
                Verdict.REJECT, RejectReason.MARGIN_TOO_LOW,
                "margin below the policy floor"),
            GovernorVerdict.HIGH_RISK: (
                Verdict.REJECT, RejectReason.EXCESSIVE_RISK,
                "exposure or risk beyond policy limits"),
            GovernorVerdict.PRICING_LOCATION_UNKNOWN: (
                Verdict.NEEDS_INFORMATION, RejectReason.UNSUPPORTED_JURISDICTION,
                "material pricing jurisdiction could not be established"),
            GovernorVerdict.NEEDS_INFORMATION: (
                Verdict.NEEDS_INFORMATION, RejectReason.INSUFFICIENT_INFORMATION,
                "insufficient information to price"),
            GovernorVerdict.REJECT: (
                Verdict.REJECT, RejectReason.CANNOT_EXECUTE, "refused by the Governor"),
        }
        verdict, reject_reason, why = mapping[governor_verdict]
        return Decision(
            opportunity=candidate, verdict=verdict, reason=why,
            reject_reason=reject_reason, job_id=job_id,
            governor_verdict=governor_verdict, conformance=assessment.conformance,
            expected_profit_cents=expected_profit, estimated_hours=hours)

    def _reject(self, candidate, reason, detail, initiator, job_id=None,
                assessment=None) -> Decision:
        decision = Decision(
            opportunity=candidate, verdict=Verdict.REJECT, reason=detail,
            reject_reason=reason, job_id=job_id,
            conformance=assessment.conformance if assessment else None)
        self._record(decision)
        if job_id:
            self._orchestrator.reject(job_id=job_id, reason=detail,
                                      initiator=initiator)
            self._discovery.set_status(candidate.id, OpportunityStatus.REJECTED,
                                       job_id)
        else:
            self._discovery.set_status(candidate.id, OpportunityStatus.REJECTED)
        self._audit.record(
            event="qualification.rejected", authority="qualification",
            initiator=initiator, why=detail, job_id=job_id, input_ref=candidate.id,
            decision=reason.value)
        return decision

    def _escalate(self, candidate, verdict, detail, initiator, job_id, assessment,
                  reject_reason=None) -> Decision:
        decision = Decision(
            opportunity=candidate, verdict=verdict, reason=detail,
            reject_reason=reject_reason, job_id=job_id,
            conformance=assessment.conformance if assessment else None)
        self._record(decision)
        self._audit.record(
            event="qualification.escalated", authority="qualification",
            initiator=initiator, why=detail, job_id=job_id, input_ref=candidate.id,
            decision=verdict.value)
        return decision

    def _committed_count(self) -> int:
        """Jobs that actually consume capacity.

        Evaluating a candidate costs nothing but a moment's thought, so a job still
        in INTAKE or QUALIFYING must not count. Counting them made Solvent unable
        to assess more than a handful of postings, which would defeat discovery
        entirely.
        """
        return len([j for j in self._orchestrator.jobs()
                    if not j.state.is_terminal
                    and j.state not in (JobState.INTAKE, JobState.QUALIFYING)])

    def _owner_identity(self) -> str:
        owners = self._policy.get("governance", "owner_identities", default=[])
        if not owners:
            raise FailClosed("no registered owner identity to attribute to")
        return owners[0]

    def _at_capacity(self) -> bool:
        limit = int(self._policy.get("qualification", "max_concurrent_jobs",
                                     default=3))
        return self._committed_count() >= limit

    # -------------------------------------------------------------- ranking

    def rank(self, decisions: list[Decision]) -> list[Decision]:
        """Order acceptable work best-first.

        Every economic input here was produced by the Financial Governor. Ranking
        adds only what economics cannot see: likelihood of finishing, what the
        client has been like, and how tight the deadline is. A job that is
        profitable but worse than another profitable job is still profitable —
        ranking never overturns the Governor, it only chooses among its approvals.
        """
        rankable = [d for d in decisions
                    if d.verdict in (Verdict.ACCEPT, Verdict.NEEDS_OWNER_APPROVAL)]
        for decision in rankable:
            decision.score = self._score(decision)
        rankable.sort(key=lambda d: d.score, reverse=True)
        for position, decision in enumerate(rankable, start=1):
            decision.rank_position = position
            self._record(decision, update=True)
        return rankable

    def _score(self, decision: Decision) -> float:
        """Expected profit per hour, discounted by completion risk and client history."""
        hours = max(decision.estimated_hours, 0.1)
        profit_per_hour = decision.expected_profit_cents / hours
        probability = 1.0
        if decision.conformance is not Conformance.CONFORMS:
            probability *= 0.5
        probability *= self._client_factor(decision.opportunity.client_ref)
        preference = self._bootstrap_preference(decision)
        decision.notes.append(
            f"score = {fmt(decision.expected_profit_cents)}/{hours:g}h "
            f"x {probability:.2f} x {preference:.2f}")
        return profit_per_hour * probability * preference

    def _bootstrap_preference(self, decision: Decision) -> float:
        """Prefer work that needs no money up front. A nudge, never a veto.

        Before there is an operating reserve, a job requiring cash up front is
        worse than an equivalent job that does not — but it is not *disqualified*,
        and a clearly superior opportunity must still be able to win. The penalty
        is therefore a bounded multiplier applied to an already Governor-approved
        job, and it can never move a job out of the acceptable set.
        """
        if not self._policy.get("bootstrap", "prefer_low_upfront", default=False):
            return 1.0
        if decision.opportunity.upfront_cost_cents <= 0:
            return 1.0
        penalty = float(self._policy.get("bootstrap", "upfront_penalty",
                                         default=0.15))
        penalty = min(max(penalty, 0.0), 0.5)   # bounded: never a disguised veto
        decision.notes.append(
            f"bootstrap: {fmt(decision.opportunity.upfront_cost_cents)} needed "
            f"up front, preference reduced by {penalty:.0%}")
        return 1.0 - penalty

    def _client_factor(self, client_ref: str) -> float:
        """What this client has actually done, from verified history only.

        Absent evidence the factor is neutral: an unknown client is not a bad
        client, and Solvent does not invent a reputation for them.
        """
        if not client_ref or self._memory is None:
            return 1.0
        facts = self._memory.recall(kind="client_performance", subject=client_ref)
        if not facts:
            return 1.0
        payload = facts[0].get("payload", {})
        if payload.get("disputed"):
            return 0.4
        if payload.get("paid_on_time"):
            return 1.2
        return 1.0

    def select(self, ranked: list[Decision]) -> tuple[list[Decision], list[Decision]]:
        """Take as many of the best as capacity allows; defer the rest.

        Deferred work is recorded as BETTER_OPPORTUNITY_AVAILABLE rather than as a
        failure — this is opportunity cost, and it is a legitimate reason to say no.
        """
        limit = int(self._policy.get("qualification", "max_concurrent_jobs",
                                     default=3))
        room = max(0, limit - self._committed_count())
        selected, deferred = ranked[:room], ranked[room:]
        for decision in selected:
            # Commitment happens here, and only here: after every candidate has
            # been judged and compared.
            if decision.job_id and decision.governor_verdict:
                self._orchestrator.apply_verdict(
                    job_id=decision.job_id, verdict=decision.governor_verdict,
                    reason=f"selected at rank {decision.rank_position}: "
                           f"{decision.reason}",
                    initiator="qualification")
                self._discovery.set_status(decision.opportunity.id,
                                           OpportunityStatus.ACCEPTED,
                                           decision.job_id)
        for decision in deferred:
            decision.reason = (
                f"deferred: {len(selected)} better-scoring opportunit"
                f"{'y consumes' if len(selected) == 1 else 'ies consume'} "
                "the available capacity")
            decision.reject_reason = RejectReason.BETTER_OPPORTUNITY_AVAILABLE
            decision.verdict = Verdict.REJECT
            self._record(decision, update=True)
            if decision.job_id:
                self._orchestrator.reject(job_id=decision.job_id,
                                          reason=decision.reason,
                                          initiator="qualification")
                self._discovery.set_status(decision.opportunity.id,
                                           OpportunityStatus.REJECTED,
                                           decision.job_id)
            self._audit.record(
                event="qualification.deferred", authority="qualification",
                initiator="qualification", why=decision.reason,
                job_id=decision.job_id, input_ref=decision.opportunity.id,
                decision=RejectReason.BETTER_OPPORTUNITY_AVAILABLE.value)
        return selected, deferred

    # ------------------------------------------------------------- recording

    def _record(self, decision: Decision, update: bool = False) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO qualification_verdicts(id,ts,opportunity_id,"
            "job_id,verdict,reason,governor_verdict,conformance,score,rank_position) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (f"qv_{decision.opportunity.id}", now(), decision.opportunity.id,
             decision.job_id, decision.verdict.value,
             f"{decision.reject_reason.value + ': ' if decision.reject_reason else ''}"
             f"{decision.reason}",
             decision.governor_verdict.value if decision.governor_verdict else "",
             decision.conformance.value if decision.conformance else "",
             decision.score, decision.rank_position))
        self._db.commit()

    def verdicts(self) -> list[dict]:
        return [dict(r) for r in self._db.query(
            "SELECT * FROM qualification_verdicts ORDER BY rank_position, ts")]

    def rejection_reasons(self) -> dict[str, int]:
        """Distribution of why Solvent declined. A first-class business metric."""
        counts: dict[str, int] = {}
        for row in self._db.query(
                "SELECT reason FROM qualification_verdicts WHERE verdict = 'REJECT'"):
            key = str(row["reason"]).split(":")[0]
            counts[key] = counts.get(key, 0) + 1
        return counts
