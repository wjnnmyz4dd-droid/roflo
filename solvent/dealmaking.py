"""Opening a negotiation: the one step production never took.

Everything needed to price a deal existed. The Finder found work, the
Qualification judged it, the Chief of Staff could carry a deal from
first contact to signed contract, and :mod:`solvent.negotiation` held
the arithmetic. Nothing joined the first half to the second. The only
``Negotiation(...)`` in the package rebuilt one that already existed,
every negotiation in the repository had been opened by a line of test
code, and a business whose deals are opened by its test harness does
not open any.

This module is that join and nothing else. It decides no price, holds
no limit and owns no table: it asks the authorities that already own
those answers, in the order the owner's model puts them in, and either
opens a negotiation through the existing engine or says which
authority refused. It is deliberately not a second Chief of Staff --
the coordinator still says what should happen next, and this performs
exactly the step the coordinator names.

**It sends nothing.** Opening a negotiation computes an asking price
and writes it down. Telling the client is a separate act through the
outreach path, which has its own consent rules. A qualified
opportunity authorises a *price*, never a *message*.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .capacity import EXECUTE
from .crm import Standing

#: Standings that mean the client has actually engaged. Pricing anyone
#: else is pricing a job nobody asked for: it sends nothing, so no
#: client is bothered by it, but it puts a live negotiation in the book
#: for somebody who never replied, and from then on the coordinator
#: reports a conversation that is not happening.
#:
#: Found by the iFixAi cross-review export, which drove the gate
#: directly rather than through ``candidates`` and opened a deal with a
#: prospect at CONTACTED. ``candidates`` filters on REPLIED, so the
#: scheduler never asked -- which is exactly what made the hole
#: invisible, and is the third guard in this module that was
#: unreachable from the only caller.
ENGAGED = (Standing.REPLIED, Standing.NEGOTIATING, Standing.PROPOSED,
           Standing.CONTRACTED, Standing.DELIVERED, Standing.PAID,
           Standing.REPEAT)
from .errors import FailClosed
from .negotiation import Costs, Negotiation, NegotiationPolicy, floor_cents
from .quoting import rates_from_policy
from .types import Cents

#: Who the audit log records these decisions under.
ORIGIN = "dealmaking"

#: What a job is assumed to take when nothing has measured it. Matches
#: ``Qualification._expected_seconds`` deliberately: two different
#: nominal hours would make the same job take two different amounts of
#: the same capacity budget depending on which path reserved it.
NOMINAL_SECONDS = 3600.0


class Refused(Enum):
    """Why no negotiation was opened. Each names a different authority.

    ``NONE`` is the opened case. Every other member is a refusal some
    existing authority gave, recorded under the name of the thing that
    refused rather than flattened into one "not now": an owner reading
    the log needs to know whether to add capacity, fix a price or
    nothing at all.
    """

    NONE = "NONE"
    #: No CRM prospect by that id. See :func:`open_negotiation`.
    NO_CLIENT_RECORD = "NO_CLIENT_RECORD"
    #: Opted out or disqualified. Terminal, and honoured for ever.
    TERMINAL = "TERMINAL"
    #: They have not asked for a price. Written to is not the same as
    #: interested, and silence is not a request.
    NOT_ENGAGED = "NOT_ENGAGED"
    #: They asked not to be written to.
    SUPPRESSED = "SUPPRESSED"
    #: A negotiation is already open with them. The idempotence gate.
    ALREADY_OPEN = "ALREADY_OPEN"
    #: They already have a contract. A second deal is a second contract.
    ALREADY_ENGAGED = "ALREADY_ENGAGED"
    #: The opportunity names no skill, and guessing one is not pricing.
    NO_SERVICE = "NO_SERVICE"
    #: The registry has not cleared this skill to reach a client.
    SKILL_NOT_DEPLOYABLE = "SKILL_NOT_DEPLOYABLE"
    #: The owner has stopped new work, or the mode blocks it.
    HALTED = "HALTED"
    #: Capacity refused. Its own reason is carried in ``why``.
    NO_CAPACITY = "NO_CAPACITY"
    #: The price on the table cannot clear the floor.
    UNPROFITABLE = "UNPROFITABLE"


@dataclass(frozen=True, slots=True)
class Opening:
    """What happened to one opportunity, and why.

    A refusal is as much a result as an opening, so both come back in
    the same shape. ``opened`` is the only field a caller should branch
    on: ``refusal`` says which authority spoke and ``why`` says what it
    said.
    """

    prospect_id: str
    opened: bool
    refusal: Refused
    why: str
    service: str = ""
    opportunity_id: str = ""
    floor_cents: Cents = 0
    asking_cents: Cents = 0

    def summary(self) -> str:
        if self.opened:
            return (f"{self.prospect_id}: opened {self.service} at "
                    f"{self.asking_cents}c (floor {self.floor_cents}c)")
        return f"{self.prospect_id}: {self.refusal.value} — {self.why}"


def costs_for(policy, *, price_cents: Cents = 0,
              expected_seconds: float = NOMINAL_SECONDS) -> Costs:
    """Build the full cost stack from what the owner has recorded.

    Every line defaults to zero and zero is the honest answer: an
    unrecorded cost is one nobody has told us about, and inventing a
    plausible figure would put a number in a quote that cannot be
    defended. A stack that is all zeros produces a floor of zero, and
    the profitability gate below refuses to open on one rather than
    pricing against nothing.

    ``acquisition`` and ``supervision`` are read from policy like the
    rest. They are the two lines the cost-floor audit found missing
    altogether -- at the figures in that audit they were $55 of a $157
    stack, and a job at $146 cleared the owner's authorised floor while
    losing $11. They are no longer representable-but-unfilled: this is
    the producer that fills them.
    """
    rates = rates_from_policy(policy)

    def cents(name: str) -> Cents:
        return int(policy.get("commercial", name, default=0) or 0)

    compute = round(rates.compute_cents_per_cpu_hour
                    * float(expected_seconds) / 3600.0)
    platform = round(price_cents * rates.platform_fee_fraction)
    if price_cents and rates.platform_fee_minimum_cents > platform:
        platform = rates.platform_fee_minimum_cents
    payment = ((round(price_cents * rates.payment_fee_fraction)
                + rates.payment_fee_flat_cents) if price_cents else 0)

    return Costs(
        service_cents=compute + cents("other_direct_cost_per_job_cents"),
        ai_cents=cents("ai_cents_per_job"),
        verification_cents=cents("verification_cents_per_job"),
        expected_revision_cents=cents("expected_revision_cents_per_job"),
        platform_fee_cents=int(platform),
        payment_processing_cents=int(payment),
        risk_allowance_cents=cents("risk_allowance_cents_per_job"),
        acquisition_cents=cents("acquisition_cents_per_job"),
        supervision_cents=cents("supervision_cents_per_job"),
    )


def negotiation_policy_from(policy) -> NegotiationPolicy:
    """The owner's negotiating limits, read from the one place that holds them.

    Defaults match :class:`~solvent.negotiation.NegotiationPolicy`'s own
    so that an owner who has configured nothing still negotiates under
    limits rather than under none.
    """
    blank = NegotiationPolicy()

    def value(name: str, default):
        got = policy.get("negotiation", name, default=None)
        return default if got is None else got

    # The margin floor is read from ``commercial`` first, because that is
    # where the Governor already reads it. Two authorities enforcing a
    # margin from two different keys is how a deal clears one floor and
    # breaches the other.
    margin = policy.get("commercial", "minimum_margin_fraction", default=None)
    if margin is None:
        margin = value("minimum_margin_fraction",
                       blank.minimum_margin_fraction)

    return NegotiationPolicy(
        minimum_margin_fraction=float(margin),
        minimum_contribution_cents=int(
            value("minimum_contribution_cents",
                  blank.minimum_contribution_cents)),
        max_discount_fraction=float(
            value("max_discount_fraction", blank.max_discount_fraction)),
        max_concessions=int(value("max_concessions", blank.max_concessions)),
        owner_signs_above_cents=int(
            value("owner_signs_above_cents", blank.owner_signs_above_cents)),
        max_revisions_without_owner=int(
            value("max_revisions_without_owner",
                  blank.max_revisions_without_owner)),
    )


def _refuse(prospect_id: str, refusal: Refused, why: str, *,
            service: str = "", opportunity_id: str = "",
            floor: Cents = 0) -> Opening:
    return Opening(prospect_id=prospect_id, opened=False, refusal=refusal,
                   why=why, service=service, opportunity_id=opportunity_id,
                   floor_cents=floor)


def _suppressed(solvent, prospect) -> str:
    """Has this prospect asked not to be written to?

    Asks :class:`~solvent.outreach.Suppressions` directly rather than
    going through ``may_write``. ``may_write`` is the right question
    before *sending* and the wrong one here: it also enforces follow-up
    cadence, so a client who was written to yesterday and replied this
    morning asking for a price would be refused a price. Suppression is
    unconditional; cadence is about messages, and this sends none.
    """
    suppressions = getattr(solvent, "suppressions", None)
    if suppressions is None:
        return ""
    for contact in prospect.contacts:
        held = suppressions.active(contact.email)
        if held is not None:
            return f"{contact.email} is suppressed: {held.why}"
    return ""


def open_negotiation(solvent, *, prospect_id: str, service: str,
                     opportunity_id: str = "", quoted_cents: Cents = 0,
                     scope_units: int = 0,
                     expected_seconds: float = NOMINAL_SECONDS,
                     initiator: str = ORIGIN) -> Opening:
    """Open one negotiation, or say which authority refused.

    The gates run in the owner's order -- eligibility, capacity,
    profitability -- and each one asks the component that already owns
    that answer. Nothing here second-guesses them: a refusal is
    returned as the refusing authority worded it.

    The negotiation is keyed in the Chief of Staff's book by
    **prospect id**, not by opportunity id, and a prospect that is not
    in the CRM is refused rather than opened. That is not bookkeeping
    fussiness: ``ChiefOfStaff.pipeline`` iterates ``crm.all()``, so a
    negotiation filed under a key the CRM does not know is one the
    pipeline never shows, the escalation sweep never reads and the
    owner never sees. An invisible open deal is worse than no deal.
    """
    crm = solvent.crm
    chief = getattr(solvent, "chief_of_staff", None)
    if chief is None:
        return _refuse(prospect_id, Refused.NO_CLIENT_RECORD,
                       "no chief of staff is configured to hold the deal")

    # ---- eligibility ---------------------------------------------------
    try:
        prospect = crm.get(prospect_id)
    except FailClosed as problem:
        return _refuse(prospect_id, Refused.NO_CLIENT_RECORD, str(problem))

    if prospect.standing.terminal:
        return _refuse(prospect_id, Refused.TERMINAL,
                       f"{prospect.company} is {prospect.standing.value}")

    if prospect.standing not in ENGAGED:
        return _refuse(
            prospect_id, Refused.NOT_ENGAGED,
            f"{prospect.company} is {prospect.standing.value}; they have "
            "not asked for a price, and silence is not a request")

    if prospect_id in chief.engagements:
        return _refuse(prospect_id, Refused.ALREADY_ENGAGED,
                       f"{prospect.company} already has a contract; a second "
                       "negotiation is a second contract")

    if prospect_id in chief.negotiations:
        existing = chief.negotiations[prospect_id]
        return _refuse(prospect_id, Refused.ALREADY_OPEN,
                       f"a {existing.service} negotiation is already open "
                       f"with {prospect.company}",
                       service=existing.service,
                       opportunity_id=existing.opportunity_id)

    held = _suppressed(solvent, prospect)
    if held:
        return _refuse(prospect_id, Refused.SUPPRESSED, held)

    service = (service or "").strip()
    if not service:
        return _refuse(prospect_id, Refused.NO_SERVICE,
                       "the opportunity names no skill, and a guessed skill "
                       "is a price for work nobody asked for")

    permitted, why = solvent.capability.may_deploy(service)
    if not permitted:
        return _refuse(prospect_id, Refused.SKILL_NOT_DEPLOYABLE, why,
                       service=service, opportunity_id=opportunity_id)

    if solvent.policy.operating_mode.blocks_new_work:
        return _refuse(prospect_id, Refused.HALTED,
                       "the operating mode blocks new work",
                       service=service, opportunity_id=opportunity_id)

    # ---- capacity ------------------------------------------------------
    room = solvent.capacity.room_for(expected_seconds, kind=EXECUTE,
                                     capability=service)
    if not room.allowed:
        return _refuse(prospect_id, Refused.NO_CAPACITY, room.why,
                       service=service, opportunity_id=opportunity_id)

    # ---- profitability -------------------------------------------------
    costs = costs_for(solvent.policy, price_cents=quoted_cents,
                      expected_seconds=expected_seconds)
    limits = negotiation_policy_from(solvent.policy)
    floor = floor_cents(costs, limits)

    if floor <= 0:
        return _refuse(
            prospect_id, Refused.UNPROFITABLE,
            "every cost line is zero, so the floor is zero and the price "
            "would be checked against nothing; record the operating costs "
            "before opening a deal",
            service=service, opportunity_id=opportunity_id, floor=floor)

    minimum_fee = int(solvent.policy.get("commercial",
                                         "minimum_project_fee_cents",
                                         default=0) or 0)
    if quoted_cents and quoted_cents < floor:
        return _refuse(
            prospect_id, Refused.UNPROFITABLE,
            f"their budget {quoted_cents}c is below the floor {floor}c",
            service=service, opportunity_id=opportunity_id, floor=floor)
    if quoted_cents and quoted_cents < minimum_fee:
        return _refuse(
            prospect_id, Refused.UNPROFITABLE,
            f"their budget {quoted_cents}c is below the owner's minimum "
            f"project fee {minimum_fee}c",
            service=service, opportunity_id=opportunity_id, floor=floor)

    # ---- open, through the existing engine -----------------------------
    deal = Negotiation(opportunity_id=opportunity_id or prospect_id,
                       service=service, costs=costs, policy=limits,
                       scope_units=scope_units)
    # Assignment first, then ``open()``. The book attaches the durability
    # hook on assignment, so a deal opened before it is filed would have
    # its opening step recorded nowhere, and a crash between the two
    # would lose the price while keeping the deal.
    chief.negotiations[prospect_id] = deal
    step = deal.open()

    crm.advance(prospect_id, Standing.NEGOTIATING,
                f"{service} priced at {step.cents}c")

    solvent.audit.record(
        event="negotiation.opened", authority=ORIGIN, initiator=initiator,
        why=f"{prospect.company}: {service}",
        decision="OPENED",
        result=f"asking={step.cents} floor={floor} "
               f"opportunity={opportunity_id or prospect_id}")

    return Opening(prospect_id=prospect_id, opened=True, refusal=Refused.NONE,
                   why=step.why, service=service,
                   opportunity_id=opportunity_id or prospect_id,
                   floor_cents=floor, asking_cents=int(step.cents))


@dataclass(frozen=True, slots=True)
class Candidate:
    """A prospect with an accepted opportunity and no deal yet."""

    prospect_id: str
    service: str
    opportunity_id: str
    quoted_cents: Cents = 0
    scope_units: int = 0


def candidates(solvent) -> list[Candidate]:
    """Who is ready for a price, read from the records rather than guessed.

    A candidate is a CRM prospect who has replied, has no negotiation
    and no engagement, and has an opportunity naming a skill. The
    opportunity is where the skill comes from: a prospect record
    holds no service, and matching one to a company by industry would
    be a recommendation engine wearing a connector's clothes.

    **DISCOVERED counts as well as ACCEPTED, and that is the repair.**
    This asked for ACCEPTED only, and ``OpportunityStatus.ACCEPTED``
    is set in exactly one place: ``qualification.select``, which also
    mints a job and applies the Governor's verdict. So the
    precondition for *quoting* a client was a commitment to *doing*
    their job -- which is backwards, and is why nothing in production
    could produce the state. Measured: a job minted that way for a
    negotiated enquiry sits in ACCEPTED with nothing to execute it,
    and no watchdog looked at it.

    Nothing is bypassed by widening it. A price is not a commitment:
    the floor, the capability registry and Capacity all still refuse
    here, and when the agreed price becomes an order every authority
    downstream -- Qualification, the Governor, the Action Gate and
    the owner's threshold -- gets its say before any work starts.
    REJECTED, QUALIFYING and EXPIRED are still excluded: the first is
    a decision already made, the second is a cycle in flight, and the
    third is gone.
    """
    from .discovery import OpportunityStatus

    chief = getattr(solvent, "chief_of_staff", None)
    if chief is None:
        return []

    by_client: dict[str, dict] = {}
    for status in (OpportunityStatus.ACCEPTED, OpportunityStatus.DISCOVERED):
        for row in solvent.discovery.opportunities(status):
            client = str(row.get("client_ref") or "")
            if client and client not in by_client:
                by_client[client] = row

    out: list[Candidate] = []
    for prospect in solvent.crm.all():
        pid = prospect.prospect_id
        if prospect.standing is not Standing.REPLIED:
            continue
        if pid in chief.negotiations or pid in chief.engagements:
            continue
        row = by_client.get(pid)
        if row is None:
            continue
        needs = _needs(row)
        if not needs:
            continue
        out.append(Candidate(prospect_id=pid, service=needs[0],
                             opportunity_id=str(row.get("id") or ""),
                             quoted_cents=int(row.get("quoted_cents") or 0)))
    return out


def _needs(row: dict) -> list[str]:
    import json

    raw = row.get("needs") or "[]"
    if isinstance(raw, (list, tuple)):
        return [str(n) for n in raw if str(n).strip()]
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    return [str(n) for n in parsed if str(n).strip()]


def open_ready(solvent, *, limit: int = 1,
               initiator: str = ORIGIN) -> list[Opening]:
    """Open negotiations for everyone ready for one, up to ``limit``.

    Bounded on purpose. An unbounded sweep that opens forty deals in one
    tick commits the shop to forty conversations before the owner has
    seen the first, and capacity is checked per deal against a budget
    that only moves when work is actually reserved -- so forty would all
    pass the same check. One per tick is the conservative default; the
    caller raises it deliberately.

    **The limit counts deals opened, not clients looked at, and that
    is a repair.** It counted the list length, so a candidate who is
    refused consumed the budget for the tick -- and ``candidates`` is
    deterministically ordered, so the *same* client was refused on
    every tick for ever and nobody behind them was ever reached.
    Measured in a twelve-day simulated run: one prospect whose budget
    sat 143c under the owner's floor was refused 1,584 times while
    two affordable clients waited behind them, and the shop's entire
    relationship channel produced nothing at all.

    A refusal is not a commitment, so it does not spend the bound.
    The bound still does its job: at most ``limit`` negotiations are
    actually opened per tick, which is the thing it was protecting.
    The repeated refusals do not flood the audit log either --
    ``Runtime._tick_dealmaking`` records each prospect-and-reason
    once.
    """
    out: list[Opening] = []
    ceiling = max(0, int(limit))
    for candidate in candidates(solvent):
        if sum(1 for opening in out if opening.opened) >= ceiling:
            break
        out.append(open_negotiation(
            solvent, prospect_id=candidate.prospect_id,
            service=candidate.service,
            opportunity_id=candidate.opportunity_id,
            quoted_cents=candidate.quoted_cents,
            scope_units=candidate.scope_units, initiator=initiator))
    return out
