"""Evaluating Upwork opportunities, and asking the owner about them.

DeskPilot discovers an opportunity, works out whether it is worth bidding
on and at what price, shows the owner the figures, and stops. If the
owner approves, the proposal becomes *eligible* for submission through a
route Upwork permits -- and nothing here submits it, because nothing here
is allowed to.

**What is and is not established about Upwork.** On 2026-10-18 Upwork
operates an official MCP server: its discovery documents answer
unauthenticated and name the resource ``Upwork MCP``, and the resource
itself returns 401. What tools it exposes is *unknown* -- reading the
list needs an authenticated session, and every Upwork page that would
describe it returns 403 to an automated fetch.
``docs/deskpilot-upwork-official-integration-review.md`` has the whole
record. So this module is built on what is verified rather than on what
is likely:

* no submission path exists here, so there is none to disable;
* competing proposal counts, competing bid amounts, client hiring
  history, Connects cost and the freelancer service fee cannot be read
  from an official source, so every one of them is reported
  ``UNAVAILABLE`` or ``MODEL_ESTIMATE`` and never as platform data;
* the owner's approval is DeskPilot's own control and is not claimed to
  satisfy any confirmation Upwork requires in its own interface.

**Why every number carries its provenance.** The failure this guards
against is specific and has a name in the engagement's history:
presenting an estimate as a fact. A recommendation that says "competing
bids around $300" reads as something Upwork told us. It is not; it is
arithmetic over whatever history exists, which at the outset is none.
:class:`Figure` makes that inseparable from the number -- a figure
cannot be constructed without saying where it came from, and
:data:`Provenance.UNAVAILABLE` is a first-class answer rather than a
zero.

**Why the approval binds a digest.** :class:`~solvent.owner.OwnerChannel`
signs a fixed set of fields, and adding more would change what every
existing approval means. Instead the recommendation's material fields
are hashed and the digest travels in ``subject``, which *is* covered by
the signature. Change the bid, the proposal, the Connects estimate or
the fee estimate and the digest changes, so the old approval no longer
matches and a fresh one is required. That is the brief's "if any
material proposal detail changes, require fresh approval", enforced by
arithmetic rather than by remembering to re-ask.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace
from enum import Enum

from .errors import FailClosed
from .types import ActionClass, Cents, fmt

#: The action class an approved proposal would eventually sit under. A
#: proposal commits money -- the Connects it costs to send and the work it
#: promises -- so it is a financial commitment, not a communication.
PROPOSAL_ACTION_CLASS = ActionClass.C3_FINANCIAL_COMMITMENT

#: How many observed outcomes before a win rate is worth quoting at all.
#: Below this the honest answer is that there is no answer; see
#: :func:`win_probability`.
MINIMUM_OUTCOMES_FOR_A_RATE = 10

#: And how many before the quoted rate is called anything but low
#: confidence. A rate from twelve observations is a number, not a
#: calibration.
OUTCOMES_FOR_MEDIUM_CONFIDENCE = 30
OUTCOMES_FOR_HIGH_CONFIDENCE = 100


class Provenance(Enum):
    """Where a number came from. Required, never inferred, never blank."""

    #: Read from Upwork, through an official route, for this opportunity.
    VERIFIED_PLATFORM_DATA = "VERIFIED PLATFORM DATA"
    #: Measured from DeskPilot's own record of what actually happened.
    HISTORICAL_OBSERVATION = "HISTORICAL OBSERVATION"
    #: Computed. A guess with arithmetic behind it is still a guess.
    MODEL_ESTIMATE = "MODEL ESTIMATE"
    #: Not obtainable. Distinct from zero, and distinct from "we did not look".
    UNAVAILABLE = "UNAVAILABLE"


class Confidence(Enum):
    """How much weight the figure will bear."""

    INSUFFICIENT_DATA = "INSUFFICIENT DATA"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


@dataclass(frozen=True, slots=True)
class Figure:
    """A number and where it came from, which travel together or not at all.

    ``value`` is ``None`` exactly when the provenance is ``UNAVAILABLE``;
    the constructor refuses any other combination, because a number
    without a source and a source without a number are both ways of
    saying something the evidence does not support.
    """

    value: float | int | None
    provenance: Provenance
    detail: str = ""

    def __post_init__(self) -> None:
        unavailable = self.provenance is Provenance.UNAVAILABLE
        if unavailable and self.value is not None:
            raise FailClosed(
                "a figure marked UNAVAILABLE carries a value, which is a "
                f"contradiction: {self.value!r} ({self.detail})")
        if not unavailable and self.value is None:
            raise FailClosed(
                f"a figure with no value is marked {self.provenance.value}; "
                "an absent number is UNAVAILABLE, not an estimate")
        if unavailable and not self.detail:
            raise FailClosed(
                "an UNAVAILABLE figure must say why it is unavailable, or "
                "the owner cannot tell 'we could not get it' from 'we did "
                "not look'")

    @property
    def known(self) -> bool:
        return self.provenance is not Provenance.UNAVAILABLE

    @property
    def verified(self) -> bool:
        return self.provenance is Provenance.VERIFIED_PLATFORM_DATA

    def render(self, money: bool = False) -> str:
        """For the owner to read. The provenance is never dropped."""
        if not self.known:
            return f"UNAVAILABLE ({self.detail})"
        shown = fmt(int(self.value)) if money else f"{self.value:g}"
        return f"{shown} [{self.provenance.value}]" + (
            f" — {self.detail}" if self.detail else "")


def unavailable(why: str) -> Figure:
    return Figure(None, Provenance.UNAVAILABLE, why)


def estimate(value, why: str) -> Figure:
    return Figure(value, Provenance.MODEL_ESTIMATE, why)


def observed(value, why: str) -> Figure:
    return Figure(value, Provenance.HISTORICAL_OBSERVATION, why)


def platform(value, why: str) -> Figure:
    return Figure(value, Provenance.VERIFIED_PLATFORM_DATA, why)


# --------------------------------------------------------- the recommendation

@dataclass(frozen=True, slots=True)
class Recommendation:
    """One opportunity, priced, with every figure's provenance attached.

    ``version`` exists so that a redrafted proposal for the same
    opportunity is a different thing to approve. It is part of the
    material digest, so bumping it invalidates an approval given for the
    previous draft -- which is the point.
    """

    opportunity_id: str
    title: str
    summary: str
    category: str
    skill: str
    version: int

    client_budget_cents: Figure
    bid_cents: int
    bid_floor_cents: int
    floor_reason: str

    competing_proposals: Figure
    competing_bids_cents: Figure
    client_hiring_history: Figure
    market_rate_cents: Figure

    service_fee_cents: Figure
    connects_required: Figure
    connects_cost_cents: Figure
    execution_cost_cents: Figure
    checker_cost_cents: Figure
    revision_allowance_cents: Figure

    win_probability: Figure
    win_confidence: Confidence
    why: str
    expires_at: str

    #: Set only once an owner approval has been verified against this exact
    #: recommendation. Nothing in this module submits anything either way.
    approved_nonce: str = ""

    # ---- the money ------------------------------------------------------

    @property
    def known_costs_cents(self) -> int:
        """What the costs add up to, counting only what is known.

        Deliberately not a total: see :attr:`costs_complete`. A sum over
        a list with holes in it is not a total, and calling it one is how
        an unprofitable job gets recommended.
        """
        parts = (self.service_fee_cents, self.connects_cost_cents,
                 self.execution_cost_cents, self.checker_cost_cents,
                 self.revision_allowance_cents)
        return sum(int(p.value) for p in parts if p.known)

    @property
    def costs_complete(self) -> bool:
        return all(p.known for p in (
            self.service_fee_cents, self.connects_cost_cents,
            self.execution_cost_cents, self.checker_cost_cents,
            self.revision_allowance_cents))

    @property
    def net_contribution(self) -> Figure:
        """What is left after the costs -- or UNAVAILABLE if any is missing.

        An unknown cost makes the contribution unknown. It does not make
        it larger, which is what treating the unknown as zero would do.
        """
        if not self.costs_complete:
            missing = [name for name, fig in (
                ("the Upwork service fee", self.service_fee_cents),
                ("the Connects cost", self.connects_cost_cents),
                ("the execution cost", self.execution_cost_cents),
                ("the checker cost", self.checker_cost_cents),
                ("the revision allowance", self.revision_allowance_cents),
            ) if not fig.known]
            return unavailable(
                "not every cost is known, so what this earns cannot be "
                "stated: " + ", ".join(missing) + " "
                + ("is" if len(missing) == 1 else "are") + " missing")
        return estimate(self.bid_cents - self.known_costs_cents,
                        "the bid less every modelled cost")

    @property
    def margin(self) -> Figure:
        net = self.net_contribution
        if not net.known or self.bid_cents <= 0:
            return unavailable("the net contribution is not known, so "
                               "neither is the margin")
        return estimate(round(float(net.value) / self.bid_cents, 4),
                        "net contribution over the bid")

    @property
    def profit_per_connect(self) -> Figure:
        """The ranking metric the brief asks for, where it can be computed."""
        net = self.net_contribution
        if not net.known or not self.connects_required.known:
            return unavailable(
                "either the net contribution or the Connects requirement is "
                "not known, so profit per Connect cannot be computed")
        connects = float(self.connects_required.value)
        if connects <= 0:
            return unavailable("the Connects requirement is zero or negative, "
                               "which is not a cost to divide by")
        return estimate(round(float(net.value) / connects, 2),
                        "net contribution per Connect spent")

    # ---- what must not drift between approval and action ----------------

    def material_fields(self) -> tuple:
        """Exactly what a fresh approval is required for.

        Anything the owner's yes was *about*. A changed summary is not
        here -- rewording the pitch does not change what is being agreed
        to -- but the version is, so a genuinely redrafted proposal does
        require asking again.
        """
        number = lambda f: "none" if not f.known else repr(f.value)
        return (
            self.opportunity_id, self.version, self.bid_cents,
            self.bid_floor_cents,
            number(self.connects_required), number(self.connects_cost_cents),
            number(self.service_fee_cents), number(self.execution_cost_cents),
            number(self.checker_cost_cents),
            number(self.revision_allowance_cents),
            number(self.net_contribution),
        )

    def material_digest(self) -> str:
        joined = "|".join(str(part) for part in self.material_fields())
        return hashlib.sha256(joined.encode()).hexdigest()[:16]

    def approval_subject(self) -> str:
        """What the owner's signature covers.

        ``OwnerChannel`` signs ``subject``, so putting the digest here
        binds every material field without changing the signed field
        list -- which would silently alter what every previously issued
        approval means.
        """
        return (f"upwork proposal {self.opportunity_id} rev {self.version} "
                f"at {fmt(self.bid_cents)} #{self.material_digest()}")

    # ---- what the owner is shown ----------------------------------------

    def notification(self) -> str:
        """Every field the brief requires, each with its provenance.

        Long on purpose. The owner is being asked to commit money on
        figures of mixed quality, and a short message would have to drop
        either a figure or its provenance -- and dropping the provenance
        is how an estimate becomes a fact.
        """
        lines = [
            f"Upwork opportunity {self.opportunity_id} — {self.title}",
            f"  {self.summary}",
            f"  category: {self.category}   DeskPilot skill: {self.skill}",
            f"  client budget: {self.client_budget_cents.render(money=True)}",
            "",
            "Competition",
            f"  proposals so far: {self.competing_proposals.render()}",
            f"  competing bids:   {self.competing_bids_cents.render(money=True)}",
            f"  client history:   {self.client_hiring_history.render()}",
            f"  market rate:      {self.market_rate_cents.render(money=True)}",
            "",
            "Money",
            f"  recommended bid:      {fmt(self.bid_cents)}",
            f"  floor:                {fmt(self.bid_floor_cents)} "
            f"({self.floor_reason})",
            f"  Upwork service fee:   {self.service_fee_cents.render(money=True)}",
            f"  Connects required:    {self.connects_required.render()}",
            f"  Connects cost:        {self.connects_cost_cents.render(money=True)}",
            f"  execution cost:       {self.execution_cost_cents.render(money=True)}",
            f"  checker cost:         {self.checker_cost_cents.render(money=True)}",
            f"  revision allowance:   {self.revision_allowance_cents.render(money=True)}",
            f"  net contribution:     {self.net_contribution.render(money=True)}",
            f"  margin:               {self.margin.render()}",
            f"  profit per Connect:   {self.profit_per_connect.render()}",
            "",
            "Odds",
            f"  win probability: {self.win_probability.render()}",
            f"  confidence:      {self.win_confidence.value}",
            "",
            f"Why: {self.why}",
            f"Approval expires: {self.expires_at}",
            "",
            "Approve or reject in DeskPilot. Approving makes this proposal "
            "eligible for submission; it does not submit it. Submission "
            "happens in Upwork's own interface, by you.",
        ]
        return "\n".join(lines)


# ------------------------------------------------------------- the odds

def win_probability(*, won: int, lost: int) -> tuple[Figure, Confidence]:
    """A win rate from observed outcomes, or an honest refusal to give one.

    The brief says never to fabricate a win probability and to report
    INSUFFICIENT DATA where history is thin. At the outset history is not
    thin, it is empty, so the ordinary case for a long while is no number
    at all -- and a ranking that depends on one has to cope with that
    rather than being handed a default.
    """
    outcomes = won + lost
    if outcomes < MINIMUM_OUTCOMES_FOR_A_RATE:
        return (unavailable(
            f"{outcomes} observed outcome(s); a rate needs at least "
            f"{MINIMUM_OUTCOMES_FOR_A_RATE} before it means anything"),
            Confidence.INSUFFICIENT_DATA)
    rate = round(won / outcomes, 4)
    if outcomes >= OUTCOMES_FOR_HIGH_CONFIDENCE:
        confidence = Confidence.HIGH
    elif outcomes >= OUTCOMES_FOR_MEDIUM_CONFIDENCE:
        confidence = Confidence.MEDIUM
    else:
        confidence = Confidence.LOW
    return (observed(rate, f"{won} won of {outcomes} observed"), confidence)


# ------------------------------------------------------------- the strategy

def recommended_bid(*, client_budget_cents: int, floor_cents: int,
                    competing_bids: Figure,
                    undercut: float = 0.9) -> tuple[int, str]:
    """What to bid, and why, never below the floor.

    The owner's preference is to compete on price and win profitable
    work. So the bid aims below the competition where the competition is
    known, and at the client's budget only when nothing better can be
    said -- but the floor from :meth:`Finder.bid_floor_cents` binds
    absolutely, because a bid that wins at a loss is worse than one that
    loses.

    Bidding at the client's budget by default is deliberately *not* what
    happens: a posting budgeting $500 that everyone else is bidding $300
    on is a posting where $500 does not win.
    """
    if floor_cents <= 0:
        raise FailClosed("a bid needs a floor; a price with no floor under "
                         "it is how work gets taken at a loss")
    if competing_bids.known:
        aimed = int(float(competing_bids.value) * undercut)
        reason = (f"{undercut:.0%} of the {fmt(int(competing_bids.value))} "
                  f"competing bids are estimated at "
                  f"[{competing_bids.provenance.value}]")
    else:
        aimed = int(client_budget_cents)
        reason = ("nothing is known about the competition, so this is the "
                  "client's own budget rather than a guess at undercutting "
                  "it")
    if aimed < floor_cents:
        return floor_cents, (
            f"{reason}, but that is below the floor, so the floor stands")
    if aimed > client_budget_cents > 0:
        return int(client_budget_cents), (
            f"{reason}, capped at the client's stated budget")
    return aimed, reason


def rank(recommendations) -> list:
    """Most worth the owner's attention first.

    Ordered on what can actually be computed, in the brief's priority:
    expected contribution, then the odds, then confidence in the odds.
    A recommendation whose contribution is unknown sorts last whatever
    else is true about it -- not because it is bad, but because nobody
    can say that it is good, and a ranking that floats unknowns to the
    top is a ranking that spends Connects on them.
    """
    def key(rec):
        net = rec.net_contribution
        per_connect = rec.profit_per_connect
        odds = rec.win_probability
        order = {Confidence.HIGH: 3, Confidence.MEDIUM: 2,
                 Confidence.LOW: 1, Confidence.INSUFFICIENT_DATA: 0}
        return (
            0 if not net.known else 1,
            float(per_connect.value) if per_connect.known else 0.0,
            float(net.value) if net.known else 0.0,
            float(odds.value) if odds.known else 0.0,
            order[rec.win_confidence],
        )
    return sorted(recommendations, key=key, reverse=True)


# ------------------------------------------------------------ the approval

def approval_problem(approval, recommendation: Recommendation) -> str:
    """Why this approval does not authorise this recommendation, or "".

    Checked against the recommendation *as it now stands*, not as it was
    when the owner was asked. That is the whole reason the digest exists:
    a bid edited after approval, a redrafted proposal, a Connects
    estimate that moved -- each changes the digest, and each needs asking
    again.

    Signature, expiry, single use and owner identity are
    :class:`~solvent.owner.OwnerChannel`'s to check; this adds only the
    binding to *this* recommendation.
    """
    wanted = recommendation.approval_subject()
    if approval.subject != wanted:
        return (
            "this approval was given for a different proposal: it covers "
            f"{approval.subject!r}, and what is in front of us is "
            f"{wanted!r}. A material detail changed after the owner "
            "answered, so their answer is to a question nobody is asking "
            "any more")
    if approval.action_class is not PROPOSAL_ACTION_CLASS:
        return (f"the approval is for {approval.action_class.value}; a "
                f"proposal is a {PROPOSAL_ACTION_CLASS.value} because it "
                "spends Connects and promises work")
    if approval.max_cents < recommendation.bid_cents:
        return (f"the approval authorises up to {fmt(approval.max_cents)} "
                f"and the bid is {fmt(recommendation.bid_cents)}")
    return ""


def eligibility(recommendation: Recommendation) -> tuple[bool, str]:
    """Whether an approved proposal may go to Upwork at all.

    Always ``False`` today, and the reason is not squeamishness. Upwork's
    own GraphQL API exposes no submission mutation, automated submission
    is prohibited by its terms including through browser automation, and
    whether the official MCP server exposes it is unverified. DeskPilot
    treats unverified as not permitted.

    Returned as a pair rather than raised, because the caller's job is to
    tell the owner what to do next -- and what to do next is submit it
    themselves.
    """
    if not recommendation.approved_nonce:
        return False, ("the owner has not approved this proposal, so there "
                       "is nothing to submit")
    return False, (
        "approved, and eligible for you to submit. DeskPilot will not send "
        "it: Upwork's API exposes no proposal-submission mutation, automated "
        "submission is prohibited by its terms including through browser "
        "automation, and whether the official MCP server exposes submission "
        "has not been verified. Sign in to Upwork and submit it yourself")


# ------------------------------------------------------------- the budget

@dataclass(frozen=True, slots=True)
class BidEvent:
    """One thing that happened, in the units it happened in."""

    kind: str
    opportunity_id: str = ""
    skill: str = ""
    cents: int = 0
    connects: int = 0


#: Everything that moves a figure in the budget. A kind not in here is
#: rejected rather than counted as nothing, so a typo cannot silently
#: zero a cost.
EVENT_KINDS = frozenset({
    "connects_purchased", "connects_consumed", "proposal_cost",
    "proposal_approved", "proposal_rejected", "proposal_submitted",
    "contract_won", "contract_lost", "revenue_collected",
    "platform_fee", "processing_cost",
})


@dataclass(frozen=True, slots=True)
class BidBudget:
    """What the acquisition channel has spent and earned.

    Computed from recorded events rather than from assumptions. The
    brief is explicit that neither the per-proposal cost nor the service
    fee may be assumed constant, so nothing here multiplies a count by a
    rate: every figure is a sum of what was actually recorded, and a
    figure with nothing recorded is zero occurrences rather than an
    estimate.

    **Not yet persisted.** These are value objects over a list of events;
    no table backs them. Persisting them means adding a table to
    ``solvent/store.py``, which is under the capacity examination's seal,
    and resealing a sealed paper to store figures for a channel that
    cannot yet be used is the wrong trade. The right moment is when live
    operation is authorised, and
    ``docs/deskpilot-upwork-bid-economics.md`` records that as the open
    item it is.
    """

    monthly_budget_cents: int
    events: tuple = field(default_factory=tuple)

    def _sum(self, kind: str, attribute: str = "cents") -> int:
        return sum(getattr(e, attribute) for e in self.events
                   if e.kind == kind)

    def _count(self, kind: str) -> int:
        return sum(1 for e in self.events if e.kind == kind)

    @property
    def connects_purchased(self) -> int:
        return self._sum("connects_purchased", "connects")

    @property
    def connects_consumed(self) -> int:
        return self._sum("connects_consumed", "connects")

    @property
    def connects_remaining(self) -> int:
        return self.connects_purchased - self.connects_consumed

    @property
    def actual_proposal_cost_cents(self) -> int:
        return self._sum("proposal_cost")

    @property
    def proposals_approved(self) -> int:
        return self._count("proposal_approved")

    @property
    def proposals_submitted(self) -> int:
        return self._count("proposal_submitted")

    @property
    def proposals_rejected(self) -> int:
        return self._count("proposal_rejected")

    @property
    def contracts_won(self) -> int:
        return self._count("contract_won")

    @property
    def contracts_lost(self) -> int:
        return self._count("contract_lost")

    @property
    def revenue_collected_cents(self) -> int:
        return self._sum("revenue_collected")

    @property
    def platform_fees_cents(self) -> int:
        return self._sum("platform_fee")

    @property
    def processing_costs_cents(self) -> int:
        return self._sum("processing_cost")

    @property
    def net_contribution_cents(self) -> int:
        return (self.revenue_collected_cents - self.platform_fees_cents
                - self.processing_costs_cents
                - self.actual_proposal_cost_cents)

    @property
    def acquisition_cost_per_won_contract(self) -> Figure:
        """Every proposal's cost over the contracts that came of them.

        Portfolio-level, and labelled as such: the proposals that lost
        are part of what the won ones cost. The per-contract figure --
        what one job's own execution and fees came to -- is a different
        number and lives on the job.
        """
        if not self.contracts_won:
            return unavailable(
                f"no contract has been won yet; "
                f"{fmt(self.actual_proposal_cost_cents)} has been spent on "
                f"{self.proposals_submitted} proposal(s) so far")
        return observed(
            round(self.actual_proposal_cost_cents / self.contracts_won, 2),
            f"{fmt(self.actual_proposal_cost_cents)} of proposal cost over "
            f"{self.contracts_won} won contract(s), losses included")

    @property
    def spent_this_month_cents(self) -> int:
        return self.actual_proposal_cost_cents

    @property
    def budget_remaining_cents(self) -> int:
        return max(0, self.monthly_budget_cents - self.spent_this_month_cents)

    def affordable(self, cost_cents: int) -> tuple[bool, str]:
        """Whether one more proposal fits, and what to tell the owner.

        A quota is not a reason to spend. The brief says to allocate
        fifteen opportunities per skill per month and in the same breath
        says not to spend merely to satisfy quotas, so this answers only
        "is there money", and profitability answers "is it worth it" --
        separately, and first.
        """
        if cost_cents <= 0:
            return False, ("a proposal with no recorded cost cannot be "
                           "checked against the budget; the cost must come "
                           "from the platform, not from an assumption")
        if cost_cents > self.budget_remaining_cents:
            return False, (
                f"{fmt(cost_cents)} would exceed the "
                f"{fmt(self.budget_remaining_cents)} left of this month's "
                f"{fmt(self.monthly_budget_cents)} proposal budget")
        return True, (f"{fmt(self.budget_remaining_cents)} of this month's "
                      f"budget remains")

    def record(self, event: BidEvent) -> "BidBudget":
        if event.kind not in EVENT_KINDS:
            raise FailClosed(
                f"{event.kind!r} is not a recorded kind of bidding event. "
                "A kind nobody recognises would be counted as nothing, and "
                "a cost counted as nothing is a cost that disappears")
        return replace(self, events=self.events + (event,))
