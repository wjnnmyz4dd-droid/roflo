"""From an agreed price to an order the pipeline can run.

The second missing join. :mod:`solvent.dealmaking` connected a
qualified opportunity to the negotiation engine; this connects the
other end. Before it existed, ``Proposal(`` and ``Engagement(``
appeared nowhere in ``solvent/`` except inside
``engagement.engagement_from_dict``, which rebuilds one that already
exists. So in production nothing ever drafted a proposal, nothing ever
took a deposit, and nothing ever turned a signed agreement into work:
every engagement in the repository had been constructed by a line of
test code, exactly as every negotiation had been before dealmaking.

A client could therefore agree a price and wait for ever. The Chief of
Staff said ``PROPOSE``, then ``REQUEST_DEPOSIT``, then ``ADMIT_WORK``,
every fifteen minutes, and nothing performed any of them.

**This adds no admission authority.** The storefront already takes
orders and ``workorder.next_work_order`` already hands them to the
business cycle, which asks Qualification, the Governor, the Action
Gate and the owner in that order. :func:`admit` places an order
through ``Storefront.place`` and stops. It does not submit to the
pipeline -- ``next_work_order`` does that, and a second route into the
pipeline is a second way to get two jobs for one payment.

**It invents no terms.** Price comes from the negotiation, scope and
revisions from the published offer, turnaround from a figure the owner
recorded, and the materials from what the client actually sent. The
one thing it will not do is make something up to get a proposal out of
the door: every refusal below names the authority or the absent fact.

**It moves no money and clears no deposit.** ``Engagement`` already
refuses to authorise work without a verified payment event, and that
gate is asked here, never reimplemented.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .crm import Standing
from .engagement import Deliverable, Engagement, Proposal, Stage
from .negotiation import Outcome
from .storefront import OFFERS, OrderState
from .types import Cents

#: Written into every audit record this module makes, so a reader can
#: tell a connector's decision from an authority's.
ORIGIN = "contracting"

#: Where the owner records how long a job is promised to take. There is
#: no default: the published ``turnaround`` text is prose, and reading
#: a number out of a sentence is interpretation. A promise the owner
#: never made is not one this module gets to make for them.
TURNAROUND_KEY = ("contracting", "turnaround_days")


class Refused(Enum):
    """Why nothing happened. One member per authority that can say no."""

    NONE = "NONE"
    NO_CHIEF_OF_STAFF = "NO_CHIEF_OF_STAFF"
    NO_STOREFRONT = "NO_STOREFRONT"
    NO_NEGOTIATION = "NO_NEGOTIATION"
    NOT_AGREED = "NOT_AGREED"
    ALREADY_ENGAGED = "ALREADY_ENGAGED"
    NO_OFFER = "NO_OFFER"
    AMBIGUOUS_OFFER = "AMBIGUOUS_OFFER"
    NOT_SELLABLE = "NOT_SELLABLE"
    NO_TURNAROUND = "NO_TURNAROUND"
    NO_PRICE = "NO_PRICE"
    #: :func:`admit` only, from here down.
    NO_ENGAGEMENT = "NO_ENGAGEMENT"
    NOT_AUTHORISED = "NOT_AUTHORISED"
    MATERIALS_MISSING = "MATERIALS_MISSING"
    ALREADY_ADMITTED = "ALREADY_ADMITTED"
    ORDER_REFUSED = "ORDER_REFUSED"


@dataclass(frozen=True, slots=True)
class Result:
    """What happened to one prospect, with the reason either way."""

    prospect_id: str
    done: bool
    refusal: Refused
    why: str
    service: str = ""
    price_cents: Cents = 0
    order_id: str = ""

    def summary(self) -> str:
        if self.done:
            return f"{self.prospect_id}: {self.why}"
        return f"{self.prospect_id}: refused ({self.refusal.value}) {self.why}"


def _no(prospect_id: str, refusal: Refused, why: str, **extra) -> Result:
    return Result(prospect_id=prospect_id, done=False, refusal=refusal,
                  why=why, **extra)


def offer_for(service: str):
    """The published offer that sells this capability, or a reason.

    Returns ``(offer, "")`` or ``(None, why)``. Ambiguity is refused
    rather than resolved: if two offers sell the same capability on
    different terms, picking one is choosing what the client is
    promised, and that is the owner's choice to publish, not this
    module's to guess.
    """
    matches = [o for o in OFFERS if o.capability == service]
    if not matches:
        return None, (f"no published offer sells {service!r}, so there are "
                      "no terms to propose")
    if len(matches) > 1:
        names = ", ".join(sorted(o.offer_id for o in matches))
        return None, (f"{len(matches)} offers sell {service!r} ({names}); "
                      "which terms the client gets is not this connector's "
                      "choice")
    return matches[0], ""


def turnaround_days(policy) -> int:
    """The owner's commitment, or ``0`` when they have not made one."""
    section, key = TURNAROUND_KEY
    try:
        return int(policy.get(section, key, default=0) or 0)
    except (TypeError, ValueError):
        return 0


def proposal_from(offer, *, prospect_id: str, price_cents: Cents,
                  days: int) -> Proposal:
    """The published offer, restated as what this client is owed.

    One deliverable per ``OfferTerm``, because a term is already a
    promise with a named check against it -- the advert and the checker
    say the same sentence, and writing a prettier description here
    would be marketing copy about work that has not been done. The
    offer's ``turnaround`` prose is carried in the notes so the client
    still reads the words they were sold, while the number the contract
    is held to is the owner's.
    """
    return Proposal(
        proposal_id=f"prop-{prospect_id}",
        prospect_id=prospect_id,
        service=offer.capability,
        price_cents=price_cents,
        deliverables=tuple(
            Deliverable(description=term.acceptance, check=term.check,
                        acceptance=term.acceptance)
            for term in offer.requirements),
        turnaround_days=days,
        revisions_included=offer.revisions.included,
        scope_excluded=tuple(offer.scope_excluded),
        client_must_supply=tuple(q.key for q in offer.intake if q.required),
        notes=f"{offer.title}. Published turnaround: {offer.turnaround}. "
              f"Revisions: {offer.revisions.covers}")


# ---------------------------------------------------------------- propose


def propose(solvent, *, prospect_id: str, initiator: str) -> Result:
    """Draft and send the proposal an agreed negotiation has earned.

    Refuses on every authority in turn rather than at the end, so the
    reason in the log is the first thing that was actually wrong.
    """
    chief = getattr(solvent, "chief_of_staff", None)
    if chief is None:
        return _no(prospect_id, Refused.NO_CHIEF_OF_STAFF,
                   "no coordination layer is configured")

    if prospect_id in chief.engagements:
        existing = chief.engagements[prospect_id]
        return _no(prospect_id, Refused.ALREADY_ENGAGED,
                   f"an engagement is already open at {existing.stage.value}",
                   service=existing.proposal.service)

    deal = chief.negotiations.get(prospect_id)
    if deal is None:
        return _no(prospect_id, Refused.NO_NEGOTIATION,
                   "there is no negotiation to draw a contract from")
    if deal.outcome is not Outcome.AGREED_PENDING_AUTHORISATION:
        return _no(prospect_id, Refused.NOT_AGREED,
                   f"the negotiation is {deal.outcome.value}, and a proposal "
                   "against an unagreed price is an offer nobody made",
                   service=deal.service)

    offer, why = offer_for(deal.service)
    if offer is None:
        return _no(prospect_id,
                   Refused.AMBIGUOUS_OFFER if "offers sell" in why
                   else Refused.NO_OFFER, why, service=deal.service)

    shop = getattr(solvent, "storefront", None)
    if shop is None:
        return _no(prospect_id, Refused.NO_STOREFRONT,
                   "no storefront is configured, so the offer's terms cannot "
                   "be checked against the registry", service=deal.service)
    sellable, why = shop.sellable(offer.offer_id)
    if not sellable:
        # Asked here as well as at admission. Sending a client terms for
        # work that cannot be delivered is the expensive mistake; finding
        # out at admission means they have already paid a deposit.
        return _no(prospect_id, Refused.NOT_SELLABLE, why,
                   service=deal.service)

    days = turnaround_days(solvent.policy)
    if days <= 0:
        return _no(prospect_id, Refused.NO_TURNAROUND,
                   "the owner has recorded no turnaround in "
                   f"{'.'.join(TURNAROUND_KEY)}, and a delivery date this "
                   "connector chose is a promise they never made",
                   service=deal.service)

    price = deal.current_cents
    if price <= 0:
        return _no(prospect_id, Refused.NO_PRICE,
                   "the negotiation carries no agreed figure",
                   service=deal.service)

    proposal = proposal_from(offer, prospect_id=prospect_id,
                             price_cents=price, days=days)
    engagement = Engagement(engagement_id=f"eng-{prospect_id}",
                            proposal=proposal)
    # Filed before it is sent, for the reason dealmaking files a deal
    # before opening it: the book attaches the durability hook on
    # assignment, so a proposal sent first would have its sending
    # recorded nowhere.
    chief.engagements[prospect_id] = engagement
    engagement.send()

    solvent.crm.advance(prospect_id, Standing.PROPOSED,
                        f"{offer.capability} proposed at {price}c")
    solvent.audit.record(
        event="engagement.proposed", authority=ORIGIN, initiator=initiator,
        why=f"{offer.offer_id} at {price}c over {days} day(s)",
        decision="PROPOSED", result=engagement.engagement_id)

    return Result(prospect_id=prospect_id, done=True, refusal=Refused.NONE,
                  why=f"proposed {offer.capability} at {price}c",
                  service=offer.capability, price_cents=price)


# ------------------------------------------------------------------ admit


def admit(solvent, *, prospect_id: str, initiator: str) -> Result:
    """Place the order an authorised engagement has paid for.

    The deposit gate is ``Engagement.work_authorised``, which is a
    property computed from verified payment events. It is read here and
    never set: there is deliberately no argument to this function that
    can stand in for money arriving.
    """
    chief = getattr(solvent, "chief_of_staff", None)
    if chief is None:
        return _no(prospect_id, Refused.NO_CHIEF_OF_STAFF,
                   "no coordination layer is configured")
    shop = getattr(solvent, "storefront", None)
    if shop is None:
        return _no(prospect_id, Refused.NO_STOREFRONT,
                   "no storefront is configured, so there is nowhere to "
                   "place the order")

    engagement = chief.engagements.get(prospect_id)
    if engagement is None:
        return _no(prospect_id, Refused.NO_ENGAGEMENT,
                   "there is no engagement to admit work for")
    service = engagement.proposal.service

    if not engagement.work_authorised:
        return _no(prospect_id, Refused.NOT_AUTHORISED,
                   f"accepted={engagement.acceptance is not None}, deposit "
                   f"{engagement.collected_cents} of "
                   f"{engagement.deposit_due_cents}c; work is not authorised",
                   service=service)

    offer, why = offer_for(service)
    if offer is None:
        return _no(prospect_id,
                   Refused.AMBIGUOUS_OFFER if "offers sell" in why
                   else Refused.NO_OFFER, why, service=service)

    order_id = f"eng-{prospect_id}"
    if shop.order(order_id) is not None:
        # Idempotence lives in the storefront rather than in a flag
        # here. A flag can be lost in a restart between placing the
        # order and writing it down; the order itself cannot.
        return _no(prospect_id, Refused.ALREADY_ADMITTED,
                   f"order {order_id!r} was already placed",
                   service=service, order_id=order_id)

    missing = offer.missing_answers(engagement.materials)
    if missing:
        # Asked of the offer rather than of Proposal.client_must_supply:
        # the storefront's rule is the one the order is judged by, and
        # two copies of it would drift.
        return _no(prospect_id, Refused.MATERIALS_MISSING,
                   "the client has not sent " + ", ".join(missing),
                   service=service)

    order = shop.place(order_id=order_id, offer_id=offer.offer_id,
                       client_ref=prospect_id,
                       answers=dict(engagement.materials),
                       offered_cents=engagement.proposal.price_cents)
    if order.state is not OrderState.RECEIVED:
        # The storefront refused on its own terms. Reported, not
        # retried and not worked around: the engagement keeps its
        # deposit and its stage, and the owner sees why.
        solvent.audit.record(
            event="engagement.order_refused", authority=ORIGIN,
            initiator=initiator, why=order.why[:200],
            decision=order.state.value, result=order_id)
        return _no(prospect_id, Refused.ORDER_REFUSED,
                   f"the storefront returned {order.state.value}: "
                   f"{order.why or ', '.join(order.missing)}",
                   service=service, order_id=order_id)

    # Moved only once the order exists. The other order -- authorise,
    # then place -- leaves an engagement that believes work is under
    # way against an order the storefront declined.
    if engagement.stage is not Stage.WORK_AUTHORISED:
        engagement.authorise_work()

    solvent.crm.advance(prospect_id, Standing.CONTRACTED,
                        f"deposit cleared; order {order_id} placed")
    solvent.audit.record(
        event="engagement.admitted", authority=ORIGIN, initiator=initiator,
        why=f"{offer.offer_id} for {prospect_id} at "
            f"{engagement.proposal.price_cents}c",
        decision="ORDER_PLACED", result=order_id)

    return Result(prospect_id=prospect_id, done=True, refusal=Refused.NONE,
                  why=f"order {order_id} placed with the storefront",
                  service=service,
                  price_cents=engagement.proposal.price_cents,
                  order_id=order_id)


# ------------------------------------------------------------- the sweeps


def awaiting_a_proposal(solvent) -> list[str]:
    """Prospects whose negotiation is agreed and unengaged."""
    chief = getattr(solvent, "chief_of_staff", None)
    if chief is None:
        return []
    return [pid for pid, deal in chief.negotiations.items()
            if deal.outcome is Outcome.AGREED_PENDING_AUTHORISATION
            and pid not in chief.engagements]


def awaiting_admission(solvent) -> list[str]:
    """Engagements whose deposit has cleared and whose work has not begun."""
    chief = getattr(solvent, "chief_of_staff", None)
    if chief is None:
        return []
    return [pid for pid, deal in chief.engagements.items()
            if deal.work_authorised
            and deal.stage in (Stage.ACCEPTED, Stage.DEPOSIT_REQUESTED,
                               Stage.DEPOSIT_RECEIVED)]


def carry_forward(solvent, *, limit: int = 1, initiator: str) -> list[Result]:
    """Perform the next step for each deal that has earned one.

    Proposals first, then admissions, each bounded by ``limit``. Two
    sweeps rather than one pass with a branch, so that a shop which
    cannot admit anything today still gets its proposals out.
    """
    out: list[Result] = []
    for prospect_id in awaiting_a_proposal(solvent)[:max(0, limit)]:
        out.append(propose(solvent, prospect_id=prospect_id,
                           initiator=initiator))
    for prospect_id in awaiting_admission(solvent)[:max(0, limit)]:
        out.append(admit(solvent, prospect_id=prospect_id,
                         initiator=initiator))
    return out
