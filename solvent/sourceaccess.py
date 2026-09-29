"""What may be done with a work source, and where it may point.

Two things live here, and neither is an authority. This module decides nothing:
it supplies the vocabulary that :mod:`solvent.discovery` stores and enforces,
and the checks that stop a source pointing somewhere it must not.

**Permissions (§10).** Finding work and doing something about it are different
powers, and the gap between them is where an autonomous system either stays
safe or quietly stops being one. So they are separate, named, and stored one by
one:

    DISCOVER < READ < IMPORT          the read side: looking, and taking a copy
    PREPARE_* < SUBMIT_*              drafting, and sending
    SEND_MESSAGE                      talking to a human being
    SPEND_MONEY                       the one that costs something

None of these implies another. Holding ``DISCOVER`` is not permission to apply;
holding ``SUBMIT_APPLICATION`` is not permission to spend the application credit
the platform charges for it; holding ``PREPARE_MESSAGE`` is not permission to
send. Each is granted on its own, by the owner, for one source at a time.

**Registration grants the read side and nothing else (§11).** Registering a
source is *for* discovering work, so a registered source may be looked at. It
may never, by the same act, apply, bid, message or spend: those are what
"external consequential action" means, and a source that could acquire them by
being added is a source that can commit the business by being added.

**Where a source may point (§60, §61).** The Action Gate already refuses any
destination that is not on the owner's egress allowlist, which is the real
defence. This adds the check one layer earlier, at registration, so a source
naming a loopback address, a private network or a cloud metadata service is
rejected before anyone can be asked to allowlist it. The metadata services are
listed explicitly because reaching one from inside a host is how an ordinary
fetch becomes a credential disclosure.
"""

from __future__ import annotations

import ipaddress
import urllib.parse

from .errors import FailClosed

# --------------------------------------------------------------- permissions

#: Looking at what a source lists.
DISCOVER = "DISCOVER"
#: Retrieving the detail of one listing.
READ = "READ"
#: Taking a copy into DeskPilot's own records.
IMPORT = "IMPORT"
#: Drafting an application. Not sending one.
PREPARE_APPLICATION = "PREPARE_APPLICATION"
#: Sending one. A consequential external effect.
SUBMIT_APPLICATION = "SUBMIT_APPLICATION"
PREPARE_BID = "PREPARE_BID"
SUBMIT_BID = "SUBMIT_BID"
PREPARE_MESSAGE = "PREPARE_MESSAGE"
#: Talking to a person. Customer Service and the Action Gate still apply.
SEND_MESSAGE = "SEND_MESSAGE"
#: Application credits, membership fees, lead fees, boosts.
SPEND_MONEY = "SPEND_MONEY"

PERMISSIONS = (DISCOVER, READ, IMPORT, PREPARE_APPLICATION, SUBMIT_APPLICATION,
               PREPARE_BID, SUBMIT_BID, PREPARE_MESSAGE, SEND_MESSAGE,
               SPEND_MONEY)

#: What registering a source grants. The read side, and nothing else.
DEFAULT_PERMISSIONS = (DISCOVER, READ, IMPORT)

#: Everything that reaches outside DeskPilot and changes something there.
#: Never granted by registration; each needs its own owner decision.
CONSEQUENTIAL = (SUBMIT_APPLICATION, SUBMIT_BID, SEND_MESSAGE, SPEND_MONEY)

#: Drafting powers. Safe because preparing is not sending -- which is only true
#: while the SUBMIT/SEND counterpart is a separate permission, as above.
PREPARATORY = (PREPARE_APPLICATION, PREPARE_BID, PREPARE_MESSAGE)


def validate_permissions(permissions) -> tuple[str, ...]:
    """Reject anything that is not a known permission.

    An unknown permission string stored as granted would be indistinguishable
    from a granted one nobody enforces, which is the worst of both.
    """
    out = []
    for permission in permissions:
        if permission not in PERMISSIONS:
            raise FailClosed(
                f"{permission!r} is not a source permission; known ones are "
                + ", ".join(PERMISSIONS))
        if permission not in out:
            out.append(permission)
    return tuple(out)


# -------------------------------------------------------------------- health

HEALTHY = "HEALTHY"
DEGRADED = "DEGRADED"
AUTH_REQUIRED = "AUTH_REQUIRED"
RATE_LIMITED = "RATE_LIMITED"
UNSUPPORTED = "UNSUPPORTED"
DISABLED = "DISABLED"
FAILED = "FAILED"

HEALTH_STATES = (HEALTHY, DEGRADED, AUTH_REQUIRED, RATE_LIMITED, UNSUPPORTED,
                 DISABLED, FAILED)

#: Health states in which polling should not be attempted. A source that cannot
#: be reached is not represented as healthy (§29), and one that is rate limited
#: is left alone rather than retried harder (§41).
NOT_POLLABLE = (AUTH_REQUIRED, RATE_LIMITED, UNSUPPORTED, DISABLED, FAILED)


# ------------------------------------------------- submission capability (§7)
#
# Whether a source will accept an application *from a machine* is a separate
# question from whether it will let one search. Having an API is not permission
# to apply through it, and a documented search endpoint says nothing about a
# submission endpoint. Each is determined on its own evidence.

#: An official, documented endpoint exists and needs no credential.
AUTOMATED_SUBMISSION_SUPPORTED = "AUTOMATED_SUBMISSION_SUPPORTED"
#: An official, documented endpoint exists behind the owner's credentials.
AUTOMATED_SUBMISSION_WITH_AUTH = "AUTOMATED_SUBMISSION_SUPPORTED_WITH_AUTH"
#: DeskPilot may assemble and verify a package; a person sends it. Not a
#: lesser outcome -- for most sources it is the only correct one.
APPLICATION_PREPARATION_ONLY = "APPLICATION_PREPARATION_ONLY"
#: The source accepts applications, but by a route no machine may drive
#: (per-solicitation email, an agency portal, a human-only workflow).
MANUAL_SUBMISSION_ONLY = "MANUAL_SUBMISSION_ONLY"
#: No submission route of any kind is documented.
SUBMISSION_UNSUPPORTED = "UNSUPPORTED"
#: The source's own terms forbid automated submission. Stronger than
#: unsupported: somebody has said no, and a workaround is a violation.
SUBMISSION_PROHIBITED = "PROHIBITED"
#: Nobody has established which of the above applies. The honest default, and
#: it means no automated submission.
SUBMISSION_UNDETERMINED = "UNDETERMINED"

SUBMISSION_MODES = (
    AUTOMATED_SUBMISSION_SUPPORTED, AUTOMATED_SUBMISSION_WITH_AUTH,
    APPLICATION_PREPARATION_ONLY, MANUAL_SUBMISSION_ONLY,
    SUBMISSION_UNSUPPORTED, SUBMISSION_PROHIBITED, SUBMISSION_UNDETERMINED)

#: The only two modes under which a machine may transmit. Everything else --
#: including UNDETERMINED -- means a person sends it.
SUBMITTABLE = (AUTOMATED_SUBMISSION_SUPPORTED, AUTOMATED_SUBMISSION_WITH_AUTH)


def may_transmit(mode: str) -> tuple[bool, str]:
    """Whether a machine may transmit an application to a source in this mode.

    Fails closed on an unrecognised mode, because a mode nobody wrote down is
    not a permission somebody granted.
    """
    if mode not in SUBMISSION_MODES:
        return False, (f"{mode!r} is not a submission mode; no automated "
                       "transmission is possible under an unknown one")
    if mode == SUBMISSION_PROHIBITED:
        return False, ("the source's terms forbid automated submission; a way "
                       "around that is a violation, not an implementation")
    if mode not in SUBMITTABLE:
        return False, (f"submission mode is {mode}; DeskPilot may prepare and "
                       "verify the application, and a person sends it")
    return True, f"submission mode is {mode}"


# ------------------------------------------------------------- access method

#: How work is actually got out of a source. Recorded per source, from a human
#: determination -- never inferred from the fact that a page loaded.
OFFICIAL_API = "OFFICIAL_API"
APPROVED_INTEGRATION = "APPROVED_INTEGRATION"
PUBLIC_FEED = "PUBLIC_FEED"
PERMITTED_PUBLIC_PAGE = "PERMITTED_PUBLIC_PAGE"
MANUAL_IMPORT = "MANUAL_IMPORT"
OWNER_ASSISTED_BROWSER = "OWNER_ASSISTED_BROWSER"
UNSUPPORTED_AUTOMATION = "UNSUPPORTED_AUTOMATION"
#: Nobody has established which of the above applies. The honest default.
UNDETERMINED = "UNDETERMINED"

ACCESS_METHODS = (OFFICIAL_API, APPROVED_INTEGRATION, PUBLIC_FEED,
                  PERMITTED_PUBLIC_PAGE, MANUAL_IMPORT, OWNER_ASSISTED_BROWSER,
                  UNSUPPORTED_AUTOMATION, UNDETERMINED)

#: Access methods under which an automated fetch may even be considered. The
#: rest are manual or owner-assisted, which is not a lesser outcome: it is the
#: difference between a business that works and one that is banned.
AUTOMATABLE = (OFFICIAL_API, APPROVED_INTEGRATION, PUBLIC_FEED,
               PERMITTED_PUBLIC_PAGE)


# ---------------------------------------------------------------- url safety

ALLOWED_SCHEMES = ("https",)

#: Cloud instance-metadata addresses. Reaching one of these from inside a host
#: is the standard way a fetch of an attacker-supplied URL turns into a
#: disclosure of the machine's own credentials.
METADATA_HOSTS = (
    "169.254.169.254",      # AWS, Azure, DigitalOcean, OpenStack
    "metadata.google.internal",
    "metadata.goog",
    "100.100.100.200",      # Alibaba
    "169.254.170.2",        # AWS ECS task metadata
)

_BLOCKED_NAMES = ("localhost", "localhost.localdomain", "ip6-localhost",
                  "broadcasthost")


def url_problem(url: str) -> str:
    """Why this URL may not be used as a source destination, or ``""``.

    Returns a reason rather than raising so callers can record it. Everything
    it rejects is rejected because a server-side fetch of it would reach
    somewhere the owner did not mean to expose: their own loopback services,
    their private network, or a metadata endpoint holding credentials.
    """
    if not url or not url.strip():
        return "no URL given"
    url = url.strip()
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError as exc:
        return f"unparseable URL ({exc})"

    scheme = (parsed.scheme or "").lower()
    if not scheme:
        return "no scheme; a bare host is ambiguous and will not be guessed at"
    if scheme not in ALLOWED_SCHEMES:
        return (f"scheme {scheme!r} is not allowed; only "
                + "/".join(ALLOWED_SCHEMES) + " may be fetched")
    if parsed.username or parsed.password:
        return "credentials embedded in a URL are never accepted"

    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        return "no host"
    if host in _BLOCKED_NAMES:
        return f"{host!r} is this machine; a source may not point at DeskPilot"
    if host in METADATA_HOSTS:
        return (f"{host!r} is a cloud metadata service, which holds this "
                "machine's own credentials")

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        # A name. It may still resolve somewhere private, which is why the
        # Action Gate's allowlist -- not this check -- is the real control.
        return ""
    if address.is_loopback:
        return f"{host} is a loopback address"
    if address.is_private:
        return f"{host} is a private network address"
    if address.is_link_local:
        return f"{host} is a link-local address"
    if address.is_reserved or address.is_multicast or address.is_unspecified:
        return f"{host} is not a routable public address"
    return ""


def safe_url(url: str) -> str:
    """The URL, or a refusal naming the reason."""
    problem = url_problem(url)
    if problem:
        raise FailClosed(f"unsafe source URL {url!r}: {problem}")
    return url.strip()
