"""How work arrives, and which of those routes is open.

The owner's instruction is "no unnecessary competitive bidding", and
it turns out to be the thing that unblocks the business rather than a
preference about style.

Every freelance agreement read in this engagement forbids the same
act: *submitting proposals automatically*. Freelancer's section 33
forbids automated API access outright; Freelancehunt's clause 3.1.11
forbids "automatic proposal submission on projects" by name. None of
them forbids being hired. A client who picks a freelancer and sends
them work directly has not received a proposal, so none of those
clauses is engaged -- and the channel that looked like a workaround is
actually the only compliant route to a first paying job.

So work is classified by **how it arrived**, not by which platform it
came from:

``DIRECT_CLIENT``
    Somebody approached the owner. No platform is involved and no
    platform's permission is needed. Always open.

``DIRECT_INVITATION``
    A platform posting addressed to this freelancer specifically
    rather than opened for proposals. Freelancehunt marks these
    ``is_personal``. Discovering one is permitted; *accepting* it is
    a separate permission and is gated separately below.

``COMPETITIVE_BID``
    An open posting that can only be won by submitting a proposal.
    **Frozen.** Not because it is unprofitable but because no
    platform read so far permits a machine to do it.

The freeze is an *additional* control. It refuses things the source
catalogue might otherwise allow, and it removes nothing: a route
already forbidden by a platform's terms is still forbidden, and
:func:`channel_problem` checks the catalogue first so that the
stricter of the two answers always wins.

**Standing authorization** is the other half. The owner wants no
manual job management during normal operation, which means something
has to be able to say yes without asking. :class:`StandingAuthority`
is the owner's pre-agreed yes, bounded per capability by value,
deadline and count, and it applies *only* to direct-hire work. It can
never authorise a bid, because a bid is frozen, and it can never
authorise more than the owner wrote down.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import sourceaccess as access
from . import sourcecatalog as catalogue

DIRECT_CLIENT = "DIRECT_CLIENT"
DIRECT_INVITATION = "DIRECT_INVITATION"
COMPETITIVE_BID = "COMPETITIVE_BID"

CHANNELS = (DIRECT_CLIENT, DIRECT_INVITATION, COMPETITIVE_BID)

#: The channels work may arrive through. Competitive bidding is
#: absent and that absence is the control.
OPEN_CHANNELS = (DIRECT_CLIENT, DIRECT_INVITATION)

#: Why the frozen one is frozen, in one sentence the owner can read
#: in a refusal.
BID_FREEZE = (
    "competitive bidding is frozen: no marketplace read so far permits "
    "a machine to submit proposals, and Freelancehunt's clause 3.1.11 "
    "forbids it by name"
)


def classify(opportunity) -> str:
    """Which channel this arrived through.

    Reads what the source already recorded rather than guessing from
    the text. ``owner_entered`` is the owner's own channel;
    ``is_personal`` is the platform saying this posting is addressed
    to one freelancer. Anything else on a marketplace is an open
    posting, which is a bid.
    """
    if getattr(opportunity, "owner_entered", False):
        return DIRECT_CLIENT
    flags = " ".join(getattr(opportunity, "risk_indicators", ()) or ())
    if "addressed to a specific freelancer" in flags:
        return DIRECT_INVITATION
    if not getattr(opportunity, "source", ""):
        return DIRECT_CLIENT
    return COMPETITIVE_BID


def channel_problem(opportunity, *, source_id: str = "") -> str:
    """Why this cannot be acquired through the channel it arrived on.

    Two checks, and the order matters. The platform's own terms are
    consulted first, so a direct invitation on a platform that
    forbids automated acceptance is still refused -- the freeze is an
    extra gate in front of the existing ones, never a replacement for
    them.
    """
    channel = classify(opportunity)
    if channel == COMPETITIVE_BID:
        return BID_FREEZE
    if channel == DIRECT_CLIENT:
        return ""
    entry = catalogue.entry(source_id or getattr(opportunity, "source", ""))
    if entry is None:
        return ("this invitation came from a source that is not in the "
                "catalogue, so nothing is known about what its terms "
                "permit")
    finding = entry.terms_for(access.ACCEPT_CONTRACT)
    if finding.finding == catalogue.TERMS_PERMITTED:
        return ""
    return (f"{entry.display_name} has not been established as permitting "
            f"automated contract acceptance ({finding.finding}); a direct "
            "invitation still has to be accepted, and accepting is what "
            "binds the owner")


@dataclass(frozen=True, slots=True)
class StandingAuthority:
    """The owner's pre-agreed yes, with its edges written down.

    Exists so that normal operation needs no owner. It is deliberately
    narrow: one capability, a ceiling in money, a floor in notice, and
    a count. Everything outside those edges goes back to the owner,
    which is what "manage by exception" means when it is implemented
    rather than described.
    """

    capability: str
    max_value_cents: int = 0
    min_deadline_hours: float = 0.0
    max_jobs: int = 0
    channels: tuple = OPEN_CHANNELS

    @property
    def configured(self) -> bool:
        return bool(self.capability) and self.max_jobs > 0

    def problem(self, *, channel: str, value_cents: int,
                deadline_hours: float, already_taken: int) -> str:
        """Why this particular job is outside what the owner agreed."""
        if not self.configured:
            return (f"the owner has granted no standing authority for "
                    f"{self.capability or 'this capability'}, so every job "
                    "needs them")
        if channel not in self.channels:
            return (f"standing authority covers "
                    f"{', '.join(self.channels)}, not {channel}")
        if channel == COMPETITIVE_BID:
            # Belt and braces. No grant can reach this because the
            # channel list excludes it, and an owner editing policy by
            # hand should still not be able to authorise a frozen act.
            return BID_FREEZE
        if already_taken >= self.max_jobs:
            return (f"{already_taken} job(s) already taken under this "
                    f"grant, which is the {self.max_jobs} the owner "
                    "allowed")
        if self.max_value_cents and value_cents > self.max_value_cents:
            return (f"${value_cents / 100:,.2f} is above the "
                    f"${self.max_value_cents / 100:,.2f} the owner "
                    "authorised without being asked")
        if value_cents <= 0:
            return ("this job has no agreed value, and a standing yes to "
                    "an unpriced job is a blank cheque")
        if (self.min_deadline_hours
                and 0 < deadline_hours < self.min_deadline_hours):
            return (f"due in {deadline_hours:.1f}h, inside the "
                    f"{self.min_deadline_hours:.0f}h of notice the owner "
                    "required")
        return ""


def standing_authority(policy, capability: str) -> StandingAuthority:
    """Read the owner's grant. Absent reads as no authority at all."""
    grants = policy.get("acquisition", "standing", default={}) or {}
    grant = grants.get(capability) or {}
    return StandingAuthority(
        capability=capability,
        max_value_cents=int(grant.get("max_value_cents") or 0),
        min_deadline_hours=float(grant.get("min_deadline_hours") or 0),
        max_jobs=int(grant.get("max_jobs") or 0),
        channels=tuple(grant.get("channels") or OPEN_CHANNELS),
    )


def may_accept(policy, opportunity, *, capability: str,
               value_cents: int, deadline_hours: float = 0.0,
               already_taken: int = 0, source_id: str = "") -> tuple:
    """May DeskPilot take this on without asking? And why not.

    Three gates in order, each able to refuse on its own: the channel
    must be open, the platform must permit acceptance where a platform
    is involved, and the job must fall inside the owner's standing
    grant. Nothing here checks capacity or price -- the capacity gate
    and the Finder own those and are consulted separately, because a
    second opinion on a question somebody else owns is how two
    authorities start disagreeing.
    """
    problem = channel_problem(opportunity, source_id=source_id)
    if problem:
        return False, problem
    authority = standing_authority(policy, capability)
    problem = authority.problem(
        channel=classify(opportunity), value_cents=value_cents,
        deadline_hours=deadline_hours, already_taken=already_taken)
    if problem:
        return False, problem
    return True, (f"within the owner's standing authority for {capability}: "
                  f"{classify(opportunity)}, "
                  f"${value_cents / 100:,.2f}, "
                  f"{already_taken + 1} of {authority.max_jobs}")
