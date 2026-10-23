"""Negotiating inside limits the owner set, and never outside them.

The owner authorises DeskPilot to haggle. That authorisation is worth
something only if the boundary is not a matter of judgement, so the
floor here is **arithmetic, not advice**: :func:`floor_cents` computes
it from costs and the owner's policy, :meth:`Negotiation.respond`
refuses anything below it, and no recommendation, model, client
message or argument about long-term client value can move it. A limit
that persuasion can shift is a suggestion.

What this module does *not* do, deliberately:

* It is **not** a financial authority. The Financial Governor still
  prices and approves every job. This decides what to *say* to a
  client; the Governor decides whether the work may be taken at all,
  and it is asked afterwards, not instead.
* It does **not** price from thin air. The market band comes from
  :mod:`solvent.marketrates`, which refuses to produce one without
  evidence, and when there is no band this falls back to cost-plus and
  says so rather than inventing a competitive number.
* It does **not** accept. Acceptance is a contract, and a contract
  above the owner's threshold is the owner's to sign.

The asymmetry worth stating: the floor is enforced deterministically
and the *opening* price is only a recommendation. Being wrong about
the opening price costs a negotiation. Being wrong about the floor
costs money on every job that follows.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum

from .errors import FailClosed
from .evidence import Confidence, Figure, Provenance, estimate, unavailable
from .marketrates import Band, band_from_dict, supported_price_cents
from .types import Cents, fmt


class Move(Enum):
    """What was said, by whom. The record is the negotiation."""

    OPENED = "OPENED"
    CLIENT_COUNTERED = "CLIENT_COUNTERED"
    CONCEDED = "CONCEDED"
    HELD = "HELD"
    SCOPE_OFFERED = "SCOPE_OFFERED"
    REFUSED = "REFUSED"
    ESCALATED = "ESCALATED"
    ACCEPTED_BY_CLIENT = "ACCEPTED_BY_CLIENT"


class Outcome(Enum):
    OPEN = "OPEN"
    #: Terms agreed, and still subject to the Governor and the owner.
    AGREED_PENDING_AUTHORISATION = "AGREED_PENDING_AUTHORISATION"
    #: Below the floor, or outside the policy. Not a failure.
    REFUSED = "REFUSED"
    #: The owner has to answer this one.
    ESCALATED = "ESCALATED"


@dataclass(frozen=True, slots=True)
class NegotiationPolicy:
    """The owner's limits. Read, never written, by anything here.

    ``max_discount_fraction`` bounds the concession from the opening
    price, separately from the floor. Both bind: a job can be above the
    floor and still be a bigger climb-down than the owner wants to make.
    """

    #: Smallest contribution margin an agreed price may leave.
    minimum_margin_fraction: float = 0.30
    #: Smallest absolute contribution, whatever the margin says.
    minimum_contribution_cents: Cents = 0
    #: How far below the opening price this may go, at most.
    max_discount_fraction: float = 0.25
    #: How many concessions before it stops moving.
    max_concessions: int = 2
    #: Above this contract value, the owner signs rather than DeskPilot.
    owner_signs_above_cents: Cents = 100_000
    #: Revisions DeskPilot may promise without being asked.
    max_revisions_without_owner: int = 2

    def __post_init__(self) -> None:
        if not 0 <= self.max_discount_fraction < 1:
            raise FailClosed(
                "max_discount_fraction must be a fraction below 1; "
                f"got {self.max_discount_fraction!r}")
        if self.minimum_margin_fraction < 0:
            raise FailClosed("a negative minimum margin is not a limit")
        if self.max_concessions < 0:
            raise FailClosed("a negative concession count is not a limit")


@dataclass(frozen=True, slots=True)
class Costs:
    """Everything the job will cost us, named so none is forgotten.

    Phase 5 lists these individually because a floor computed from a
    single lumped "cost" is a floor nobody can check, and the one that
    gets dropped is always a fee.
    """

    service_cents: Cents = 0
    ai_cents: Cents = 0
    verification_cents: Cents = 0
    expected_revision_cents: Cents = 0
    platform_fee_cents: Cents = 0
    payment_processing_cents: Cents = 0
    risk_allowance_cents: Cents = 0
    #: What it cost to find this client: research, outreach, and the
    #: share of proposals that were written and lost. Added because
    #: the docstring above says "named so none is forgotten" and two
    #: were. An audit priced the worked example this file is built
    #: around -- $200 in, $35 execution, $20 verification, $40
    #: revisions, $30 acquisition, $7 fees, $25 supervision -- and the
    #: floor believed contribution was $98 when it was $43, because
    #: acquisition and supervision had nowhere to go. At those numbers
    #: the floor was $145.71, so a job at $146 cleared the owner's
    #: authorised profit floor while losing $11.
    #:
    #: Acquisition is per job here, not per proposal. Spend on
    #: proposals that never became jobs belongs to no job and is
    #: recorded against the business through ``Ledger.record_cost``
    #: with no job id, where ``total_costs_cents`` already counts it.
    acquisition_cents: Cents = 0
    #: The owner's own time: reviewing a proposal, answering a
    #: clarification, approving a delivery. Unpaid attention is still
    #: a cost, and a business that prices at zero for it is pricing a
    #: job it cannot repeat at scale.
    supervision_cents: Cents = 0

    @property
    def total_cents(self) -> Cents:
        return (self.service_cents + self.ai_cents + self.verification_cents
                + self.expected_revision_cents + self.platform_fee_cents
                + self.payment_processing_cents + self.risk_allowance_cents
                + self.acquisition_cents + self.supervision_cents)

    def breakdown(self) -> dict:
        return {
            "service": self.service_cents, "ai": self.ai_cents,
            "verification": self.verification_cents,
            "expected_revisions": self.expected_revision_cents,
            "platform_fees": self.platform_fee_cents,
            "payment_processing": self.payment_processing_cents,
            "risk_allowance": self.risk_allowance_cents,
            "acquisition": self.acquisition_cents,
            "supervision": self.supervision_cents,
            "total": self.total_cents,
        }


def floor_cents(costs: Costs, policy: NegotiationPolicy) -> Cents:
    """The lowest price that still clears the owner's limits.

    Both limits bind and the higher wins. A margin fraction alone lets
    a tiny job clear at a few pence of contribution; an absolute
    minimum alone lets a large job clear at a margin the owner would
    refuse. Taking the maximum is what makes "the floor" one number
    rather than two that disagree.
    """
    total = costs.total_cents
    if policy.minimum_margin_fraction >= 1:
        raise FailClosed(
            "a minimum margin of 100% or more cannot be priced: no finite "
            "price leaves that much contribution")
    by_margin = int(round(total / (1 - policy.minimum_margin_fraction)))
    by_absolute = total + policy.minimum_contribution_cents
    return max(by_margin, by_absolute)


def opening_cents(costs: Costs, policy: NegotiationPolicy, band: Band | None,
                  scope_units: int = 0) -> Figure:
    """What to ask for first, and on what grounds.

    Market-led when there is evidence, cost-plus when there is not --
    and the Figure says which, so a cost-plus number is never read as
    a competitive one. Either way it is raised to the floor if it sits
    below, because an opening price under the floor has nowhere to go.
    """
    floor = floor_cents(costs, policy)
    if band is not None and band.usable and scope_units > 0:
        supported = supported_price_cents(band, scope_units)
        if supported.known:
            asked = max(int(supported.value), floor)
            why = (f"market band supports {fmt(int(supported.value))} "
                   f"({band.confidence.value.lower()}, {band.observations} "
                   f"observation(s))")
            if asked > int(supported.value):
                why += f"; raised to the floor of {fmt(floor)}"
            return Figure(asked, supported.provenance, why)
    # No evidence. Say so rather than dressing cost-plus as a market price.
    headroom = int(round(floor / (1 - min(policy.max_discount_fraction, 0.9))))
    return estimate(headroom,
                    "no usable market band; cost-plus with room to concede "
                    f"down to the floor of {fmt(floor)}")


@dataclass(frozen=True, slots=True)
class Step:
    """One thing that was said, and what it was worth."""

    move: Move
    cents: Cents
    why: str
    contribution_cents: Cents = 0


@dataclass
class Negotiation:
    """One conversation about one price.

    Mutable because a negotiation is a sequence of events, but the
    limits it is held to are not: ``policy`` and ``costs`` are frozen
    and the floor is recomputed from them on every single response
    rather than cached, so there is no stored number for a bug or an
    argument to move.
    """

    opportunity_id: str
    service: str
    costs: Costs
    policy: NegotiationPolicy
    scope_units: int = 0
    band: Band | None = None
    steps: list[Step] = field(default_factory=list)
    outcome: Outcome = Outcome.OPEN
    escalation: str = ""
    #: Called with this negotiation after every move. Durability is
    #: not this class's job -- it is a record of a conversation, not a
    #: store -- but a conversation that changes without telling anybody
    #: cannot be made durable from outside, and a negotiation that a
    #: restart silently forgot is one a client gets to reopen at their
    #: own number.
    on_change: Callable[["Negotiation"], None] | None = field(
        default=None, repr=False, compare=False)

    @property
    def floor(self) -> Cents:
        """Recomputed every time. Never stored, never adjusted."""
        return floor_cents(self.costs, self.policy)

    @property
    def opening(self) -> Figure:
        return opening_cents(self.costs, self.policy, self.band, self.scope_units)

    @property
    def concessions(self) -> int:
        return sum(1 for s in self.steps if s.move is Move.CONCEDED)

    @property
    def current_cents(self) -> Cents:
        for step in reversed(self.steps):
            if step.move in (Move.OPENED, Move.CONCEDED, Move.HELD):
                return step.cents
        return 0

    def contribution_at(self, cents: Cents) -> Cents:
        return cents - self.costs.total_cents

    def open(self) -> Step:
        try:
            return self._open()
        finally:
            self._changed()

    def _open(self) -> Step:
        if self.steps:
            raise FailClosed(
                f"negotiation {self.opportunity_id!r} is already open; a "
                "second opening price is two prices for one job")
        asked = self.opening
        step = Step(Move.OPENED, int(asked.value), asked.detail,
                    self.contribution_at(int(asked.value)))
        self.steps.append(step)
        return step

    def respond(self, client_cents: Cents, *, why: str = "") -> Step:
        """Answer a counteroffer, inside the limits. The whole point.

        Order matters and is deliberate: the floor is checked before
        anything else, so no later consideration -- client value,
        long-term potential, how close the number is -- can be weighed
        against it. Those are reasons to *concede within* the limits,
        never reasons to cross them.

        The hook fires in a ``finally`` so that a refusal is recorded
        as durably as an agreement. A refused counteroffer that a
        restart forgot would let the same client come back with the
        same number and be answered afresh.
        """
        try:
            return self._respond(client_cents, why=why)
        finally:
            self._changed()

    def _respond(self, client_cents: Cents, *, why: str = "") -> Step:
        if not self.steps:
            raise FailClosed("a counteroffer arrived before an opening price")
        if self.outcome is not Outcome.OPEN:
            raise FailClosed(
                f"negotiation {self.opportunity_id!r} is {self.outcome.value}; "
                "it is not still being negotiated")
        if client_cents <= 0:
            raise FailClosed("a counteroffer of nothing is not an offer")

        self.steps.append(Step(Move.CLIENT_COUNTERED, client_cents,
                               why or "client counteroffer",
                               self.contribution_at(client_cents)))

        floor = self.floor
        if client_cents < floor:
            # The one unconditional refusal. Nothing below is consulted.
            step = Step(Move.REFUSED, floor,
                        f"{fmt(client_cents)} is below the floor of "
                        f"{fmt(floor)}; costs are {fmt(self.costs.total_cents)} "
                        f"and the owner's minimum margin is "
                        f"{self.policy.minimum_margin_fraction:.0%}",
                        self.contribution_at(client_cents))
            self.steps.append(step)
            self.outcome = Outcome.REFUSED
            return step

        opened = self.steps[0].cents
        discount = (opened - client_cents) / opened if opened else 0.0
        if discount > self.policy.max_discount_fraction:
            return self._escalate(
                client_cents,
                f"{discount:.0%} below the opening price, and the owner "
                f"allows {self.policy.max_discount_fraction:.0%}")
        if self.concessions >= self.policy.max_concessions:
            step = Step(Move.HELD, self.current_cents,
                        f"already conceded {self.concessions} time(s), which "
                        "is the owner's limit", 
                        self.contribution_at(self.current_cents))
            self.steps.append(step)
            return step
        if client_cents >= self.current_cents:
            step = Step(Move.ACCEPTED_BY_CLIENT, client_cents,
                        "the client met or exceeded the standing price",
                        self.contribution_at(client_cents))
            self.steps.append(step)
            self.outcome = Outcome.AGREED_PENDING_AUTHORISATION
            return step
        if client_cents > self.policy.owner_signs_above_cents:
            return self._escalate(
                client_cents,
                f"{fmt(client_cents)} is above the owner's signing "
                f"threshold of {fmt(self.policy.owner_signs_above_cents)}")

        step = Step(Move.CONCEDED, client_cents,
                    f"inside the floor ({fmt(floor)}) and inside the "
                    f"{self.policy.max_discount_fraction:.0%} discount limit",
                    self.contribution_at(client_cents))
        self.steps.append(step)
        self.outcome = Outcome.AGREED_PENDING_AUTHORISATION
        return step

    def _escalate(self, cents: Cents, why: str) -> Step:
        step = Step(Move.ESCALATED, cents, why, self.contribution_at(cents))
        self.steps.append(step)
        self.outcome = Outcome.ESCALATED
        self.escalation = why
        return step

    def _changed(self) -> None:
        if self.on_change is not None:
            self.on_change(self)

    def as_dict(self) -> dict:
        """Everything needed to carry on the conversation after a restart.

        Distinct from :meth:`record`, which is for a person to read and
        renders the band as a sentence. This keeps the band's figures
        and their provenance, because the opening price is derived from
        them and a band restored as prose would come back as no band at
        all -- which silently turns a market-supported opening price
        into a cost-plus one.
        """
        return {
            "opportunity_id": self.opportunity_id, "service": self.service,
            "costs": {
                "service_cents": self.costs.service_cents,
                "ai_cents": self.costs.ai_cents,
                "verification_cents": self.costs.verification_cents,
                "expected_revision_cents": self.costs.expected_revision_cents,
                "platform_fee_cents": self.costs.platform_fee_cents,
                "payment_processing_cents":
                    self.costs.payment_processing_cents,
                "risk_allowance_cents": self.costs.risk_allowance_cents,
            },
            "policy": {
                "minimum_margin_fraction":
                    self.policy.minimum_margin_fraction,
                "minimum_contribution_cents":
                    self.policy.minimum_contribution_cents,
                "max_discount_fraction": self.policy.max_discount_fraction,
                "max_concessions": self.policy.max_concessions,
                "owner_signs_above_cents":
                    self.policy.owner_signs_above_cents,
                "max_revisions_without_owner":
                    self.policy.max_revisions_without_owner,
            },
            "scope_units": self.scope_units,
            "band": self.band.as_dict() if self.band is not None else None,
            "steps": [{"move": s.move.value, "cents": s.cents, "why": s.why,
                       "contribution_cents": s.contribution_cents}
                      for s in self.steps],
            "outcome": self.outcome.value,
            "escalation": self.escalation,
        }

    def record(self) -> dict:
        """Everything Phase 6 asks to be logged, per step."""
        return {
            "opportunity_id": self.opportunity_id,
            "service": self.service,
            "outcome": self.outcome.value,
            "floor_cents": self.floor,
            "costs": self.costs.breakdown(),
            "market_band": self.band.render() if self.band else
                           "no band; cost-plus",
            "limits": {
                "minimum_margin_fraction": self.policy.minimum_margin_fraction,
                "minimum_contribution_cents": self.policy.minimum_contribution_cents,
                "max_discount_fraction": self.policy.max_discount_fraction,
                "max_concessions": self.policy.max_concessions,
                "owner_signs_above_cents": self.policy.owner_signs_above_cents,
            },
            "escalation": self.escalation,
            "steps": [{"move": s.move.value, "cents": s.cents,
                       "contribution_cents": s.contribution_cents,
                       "why": s.why} for s in self.steps],
        }


def negotiation_from_dict(doc: dict) -> Negotiation:
    """Rebuild a negotiation exactly as it stood.

    The floor is *not* read from the stored row: it is recomputed from
    the restored costs and policy by the property that always computes
    it. A floor that came back from storage would be a stored number,
    and a stored number is one an edit, a migration or a bug can move.
    """
    deal = Negotiation(
        opportunity_id=doc["opportunity_id"], service=doc["service"],
        costs=Costs(**doc["costs"]),
        policy=NegotiationPolicy(**doc["policy"]),
        scope_units=int(doc.get("scope_units", 0)),
        band=band_from_dict(doc["band"]) if doc.get("band") else None,
        outcome=Outcome(doc.get("outcome", Outcome.OPEN.value)),
        escalation=doc.get("escalation", ""))
    deal.steps = [Step(Move(s["move"]), int(s["cents"]), s.get("why", ""),
                       int(s.get("contribution_cents", 0)))
                  for s in doc.get("steps", [])]
    return deal
