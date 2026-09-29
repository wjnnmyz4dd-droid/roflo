"""Two ways work arrives, kept apart on purpose.

DeskPilot's normal operating model is that it **finds** work. The owner
handing it a job is a fallback, not the business. But both end up as an
opportunity, and the moment they become indistinguishable, two things that
must never be confused become confusable:

**FOUND** work is attacker-adjacent. It came off a public board written by
whoever posted it, its terms are client assertions, and reaching it needed a
network call that the Action Gate had to permit.

**INSERTED** work came from the owner. Its terms are owner-confirmed, which is
a stronger claim than any marketplace can make -- and precisely because it is
stronger, nothing that arrived over the network may ever claim it.

So the roles are separate here, at the front, with separate entry points,
separate provenance, separate permissions and separate identity. They converge
only *after* intake, into the one downstream pipeline that already exists:
dedup, risk, capability, conformance, the Financial Governor, Policy, the Job
Orchestrator. There is no second pipeline and this module does not contain one.

What this module owns is the boundary, and nothing else. It holds no table,
makes no decision, and every refusal it produces comes from asking the
authority that already had the answer.
"""

from __future__ import annotations

from . import sourceaccess as access
from .errors import FailClosed

#: DeskPilot went looking and found this. Network, adapter, Action Gate.
FOUND = "FOUND"
#: A person handed this over. No network, no adapter, no discovery authority.
INSERTED = "INSERTED"

ROLES = (FOUND, INSERTED)

#: How an inserted opportunity got here. Recorded so "the owner typed it" and
#: "a client emailed it" stay distinguishable, because they are not equally
#: authoritative about the terms.
OWNER_ENTERED = "OWNER_ENTERED"
FILE_IMPORT = "FILE_IMPORT"
MANUAL_URL = "MANUAL_URL"
CLIENT_INQUIRY = "CLIENT_INQUIRY"

INSERTION_METHODS = (OWNER_ENTERED, FILE_IMPORT, MANUAL_URL, CLIENT_INQUIRY)

#: Permissions an inserted source may never hold. Insertion is someone typing
#: into a box; it is not a licence to go out and fetch things, and a manual
#: source that could discover would be an unaudited way to reach the network.
DENIED_TO_INSERTED = (access.DISCOVER,)

#: Permissions a found source may never hold *by virtue of being found*. These
#: are the consequential ones and they are granted per source by the owner --
#: never by the act of discovering something.
NEVER_FROM_FINDING = access.CONSEQUENTIAL


def role_of(source_state: dict) -> str:
    """Which intake role a registered source belongs to.

    Read from the source's recorded kind, which the Discovery authority set at
    registration. Not from anything the adapter says about itself: a remote
    adapter that could nominate its own role could nominate the one whose
    content is trusted more.
    """
    kind = (source_state or {}).get("kind", "")
    return INSERTED if kind == "manual" else FOUND


def assert_may_discover(source_state: dict) -> None:
    """Refuse discovery on an inserted-role source.

    The failure this prevents is not theoretical: an inserted source that
    acquired DISCOVER would make a network call whose results are recorded as
    owner-confirmed, which is content laundering -- attacker-authored text
    arriving with the one provenance that lets it gate a commitment.
    """
    role = role_of(source_state)
    if role == INSERTED:
        raise FailClosed(
            f"source {source_state.get('name')!r} is an {INSERTED} intake "
            "source; inserted work is typed in, not fetched, and giving it "
            "discovery would let network content arrive as owner-confirmed")


def assert_may_insert(source_state: dict) -> None:
    """Refuse owner-insertion provenance to a found-role source."""
    role = role_of(source_state)
    if role == FOUND:
        raise FailClosed(
            f"source {source_state.get('name')!r} is a {FOUND} intake source; "
            "it may not record work as owner-entered, because owner-confirmed "
            "terms may gate a commitment and a board posting may not")


def provenance(source_state: dict, *, method: str = "") -> dict:
    """The provenance stamp for an opportunity from this source.

    Returned rather than written: Discovery owns the row. This decides what
    the stamp says, in one place, so the two roles cannot drift into each
    other one call site at a time.
    """
    role = role_of(source_state)
    if role == INSERTED:
        if method and method not in INSERTION_METHODS:
            raise FailClosed(
                f"{method!r} is not an insertion method; known ones are "
                + ", ".join(INSERTION_METHODS))
        return {"intake_role": INSERTED,
                "insertion_method": method or OWNER_ENTERED,
                "owner_entered": True,
                "discovered_via": "owner"}
    return {"intake_role": FOUND, "insertion_method": "",
            "owner_entered": False,
            "discovered_via": source_state.get("name", "")}


def permissions_for(role: str, granted) -> tuple[str, ...]:
    """Filter a permission set down to what this role may hold.

    Applied on the way in rather than checked on the way out, so an inserted
    source cannot be *stored* holding DISCOVER even if something tries.
    """
    if role not in ROLES:
        raise FailClosed(f"{role!r} is not an intake role")
    if role == INSERTED:
        return tuple(p for p in granted if p not in DENIED_TO_INSERTED)
    return tuple(granted)
