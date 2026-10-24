"""A client who writes in, recorded once, through one owner act.

The gap this closes was not a missing decision. It was a missing
**door**. ``CRM.discover`` had no caller anywhere in ``solvent/``: no
scheduled task, no CLI command, no web route. The whole relationship
layer -- the CRM, the negotiation engine, the engagement, and three
scheduled tasks -- sat behind it, reachable only from test code. The
connectors built on top of it worked and had nothing to work on.

**The facts cannot come from the system.** A storefront order carries
a client reference and a form; it does not carry who the client is,
and a company name DeskPilot invented would be a forged record in the
one file whose purpose is to say what is actually known about a real
person. The prospecting module that might find such facts says of
itself that it is EXPERIMENTAL and deliberately not wired into
commercial operation.

So the facts come from the owner, and this is the door they come
through -- :func:`solvent.cli` exposes it as ``solvent client add``.
It is the same philosophy as ``ManualSource``: *"the owner brings an
opportunity they found themselves... the only acquisition path that
is blocked by nothing external."* Applied to the client rather than
to the work.

**One act, not five.** A prospect, their evidence, their contact,
their standing and their enquiry recorded as five separate calls is
how a half-entered client comes to exist -- a prospect with no
contact cannot be written to, an enquiry with no prospect cannot be
priced, and nothing says which half is missing. Here they happen
together and the refusals come first.

**It decides nothing.** Whether the work is worth doing is
Qualification's; what it should cost is the Governor's and the
negotiation engine's; whether the client may be written to is the
suppression list's. This records what the owner was told, and the
record says that is what it is.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .crm import Basis, Contact, Evidence, Standing
from .errors import FailClosed
from .types import Cents

#: Written into every audit record and every evidence row this module
#: makes, so a reader of the CRM can see where the claim came from.
ORIGIN = "clientintake"

#: What the evidence says about itself. The owner is repeating what a
#: client told them, which is a client assertion arriving by a longer
#: route -- not a published fact, and not an inference.
SOURCE = "an enquiry the owner received and entered"


class Refused(Enum):
    """Why nothing was recorded. One member per missing fact or rule."""

    NONE = "NONE"
    NO_SOURCE = "NO_SOURCE"
    ALREADY_KNOWN = "ALREADY_KNOWN"
    NOT_THE_OWNER = "NOT_THE_OWNER"
    MISSING_DETAIL = "MISSING_DETAIL"
    NOT_A_SKILL = "NOT_A_SKILL"
    OPTED_OUT = "OPTED_OUT"


@dataclass(frozen=True, slots=True)
class Recorded:
    """What was written down, or why nothing was."""

    prospect_id: str
    done: bool
    refusal: Refused
    why: str
    opportunity_id: str = ""

    def summary(self) -> str:
        if self.done:
            return f"{self.prospect_id}: {self.why}"
        return f"{self.prospect_id}: refused ({self.refusal.value}) {self.why}"


def _no(prospect_id: str, refusal: Refused, why: str) -> Recorded:
    return Recorded(prospect_id=prospect_id, done=False, refusal=refusal,
                    why=why)


def record_enquiry(solvent, *, prospect_id: str, company: str, industry: str,
                   state: str, contact_name: str, contact_email: str,
                   wants: str, budget_cents: Cents, seen_on: str,
                   owner_identity: str, source: str = "inbox",
                   initiator: str = ORIGIN) -> Recorded:
    """Record a client who asked for a price, and what they asked for.

    Every argument is a fact the owner has; none is derived, and the
    refusals below are all "you have not told me X" rather than
    judgements about the client. The one judgement is the capability
    registry's: an enquiry for something DeskPilot does not sell is
    refused here rather than priced and then declined later.

    The prospect's standing goes to REPLIED because that is what
    happened. A client who wrote in has engaged without being
    contacted, and recording them as merely DISCOVERED would leave
    ``dealmaking`` refusing to price somebody who is waiting on a
    price.
    """
    missing = [name for name, value in (
        ("prospect_id", prospect_id), ("company", company),
        ("industry", industry), ("state", state),
        ("contact_email", contact_email), ("wants", wants),
        ("seen_on", seen_on)) if not str(value).strip()]
    if missing:
        return _no(prospect_id, Refused.MISSING_DETAIL,
                   "the owner has not said " + ", ".join(missing))
    if int(budget_cents) < 0:
        return _no(prospect_id, Refused.MISSING_DETAIL,
                   "a negative budget is not a budget")

    if not solvent.policy.is_owner(owner_identity):
        return _no(prospect_id, Refused.NOT_THE_OWNER,
                   f"only a registered owner enters a client "
                   f"(got {owner_identity!r})")

    permitted, why = solvent.capability.may_deploy(wants)
    if not permitted:
        # Refused here rather than at pricing. Recording a client who
        # wants something that cannot be delivered produces a CRM
        # entry whose only future is a refusal, and an address held
        # for no reason is an address held.
        return _no(prospect_id, Refused.NOT_A_SKILL,
                   f"{wants} is not cleared for delivery: {why}")

    if solvent.discovery.source_state(source) is None:
        return _no(prospect_id, Refused.NO_SOURCE,
                   f"no source {source!r} is registered to enter work under; "
                   "the owner registers one before entering an enquiry")

    existing = None
    try:
        existing = solvent.crm.get(prospect_id)
    except FailClosed:
        pass
    if existing is not None:
        if existing.standing is Standing.OPTED_OUT:
            return _no(prospect_id, Refused.OPTED_OUT,
                       f"{existing.company} asked not to be contacted, and "
                       "an enquiry typed in is not a withdrawal of that")
        return _no(prospect_id, Refused.ALREADY_KNOWN,
                   f"{existing.company} is already held as "
                   f"{existing.standing.value}")

    crm = solvent.crm
    held = crm.discover(prospect_id=prospect_id, company=company,
                        industry=industry, state=state)
    if held.prospect_id != prospect_id:
        # The CRM deduplicates on company and state and has handed
        # back somebody it already holds. Reported rather than
        # written over: two companies with one name in one place are
        # one company as far as this record is concerned, and the
        # owner is the one who knows whether that is right.
        return _no(prospect_id, Refused.ALREADY_KNOWN,
                   f"{company} in {state} is already held as "
                   f"{held.prospect_id}")

    crm.add_evidence(prospect_id, Evidence(
        claim=f"they asked what {wants} would cost",
        basis=Basis.CLIENT_STATED, source=SOURCE, seen_on=seen_on))
    crm.add_contact(prospect_id, Contact(
        name=contact_name or "the person who wrote in", email=contact_email,
        role="the client's own contact", source=SOURCE, seen_on=seen_on))
    crm.advance(prospect_id, Standing.QUALIFIED,
                f"they named {wants}, which is a skill this shop sells")
    crm.advance(prospect_id, Standing.REPLIED,
                "they wrote in asking for a price, which is the reply")

    entered = solvent.discovery.insert(
        source, owner_identity=owner_identity, postings=[{
            "ref": f"enq-{prospect_id}",
            "title": f"{wants} for {company}",
            "quoted_cents": int(budget_cents),
            "needs": [wants],
            "client_ref": prospect_id,
            "project_state": state,
            "client_state": state,
            "body": f"{SOURCE}: {company} asked what {wants} would cost.",
        }])
    opportunity_id = entered[0].id if entered else ""

    solvent.audit.record(
        event="client.enquiry_recorded", authority=ORIGIN,
        initiator=initiator, why=f"{company} ({state}) asked about {wants}",
        decision="RECORDED", result=opportunity_id or prospect_id)

    return Recorded(prospect_id=prospect_id, done=True, refusal=Refused.NONE,
                    why=f"recorded {company} and their {wants} enquiry",
                    opportunity_id=opportunity_id)
