"""The places work might be found, and what is actually known about each.

This is a **catalogue**, not a set of integrations. Every entry below records
one thing above all: whether anybody has established how DeskPilot may
legitimately get work out of that platform. For every named commercial
marketplace, the answer today is *no*, and the entry says so.

**Why every marketplace is UNDETERMINED.** A platform's terms of service decide
whether automated access is permitted, they differ per platform, they differ by
account type, and they change. That determination is a reading of a legal
document about a specific account at a specific time. It cannot be made from
inside this repository, it cannot be inferred from the fact that a site has a
documented API, and it must not be guessed at -- a wrong guess gets the owner's
account banned, which costs them the acquisition channel permanently.

So no entry here claims an access method. ``access_method`` is
``UNDETERMINED`` and ``compliance`` is ``UNDETERMINED`` until the owner records
a written determination, which :meth:`~solvent.discovery.Discovery.
register_source` already refuses to accept as PERMITTED without.

**What this is still for.** Two things, and both matter:

* **Manual ingestion works today.** Every entry supports the owner pasting in
  an opportunity they found by hand. That path is blocked by nothing external,
  needs no permission from anyone, and feeds exactly the same pipeline --
  qualification, capability, conformance, the Governor, the Gate -- as an
  automated one would. The first dollar does not require an API.
* **It names the decision.** An owner looking at the Work sources page sees
  the platforms that exist, what would have to be established for each, and
  that nothing is pretending otherwise.

Nothing here bypasses CAPTCHA, evades anti-bot systems, rotates identities,
shares credentials, or scrapes against a restriction. A source that would need
any of those is recorded ``UNSUPPORTED_AUTOMATION`` and never polled.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import sourceaccess as access

# Categories, used only for grouping on the owner's page.
FREELANCE_MARKETPLACE = "FREELANCE_MARKETPLACE"
DIRECT = "DIRECT"


#: What a platform's own terms say about one activity. Recorded separately
#: from what the owner granted, because they are different facts: the owner
#: can decide DeskPilot should apply for work, and the platform can still
#: forbid doing it automatically. A grant is an intention; a finding is an
#: observation, and the observation wins.
TERMS_PERMITTED = "PERMITTED"
TERMS_FORBIDDEN = "FORBIDDEN"
#: Nobody has read the clause. Treated exactly like FORBIDDEN at the point of
#: use -- "we have not checked" is not a reason to go ahead -- but kept
#: distinct so a report can tell "known not allowed" from "unknown".
TERMS_UNDETERMINED = "UNDETERMINED"

TERMS_FINDINGS = (TERMS_PERMITTED, TERMS_FORBIDDEN, TERMS_UNDETERMINED)

#: Activities a platform's terms get a say in. Reading and importing what a
#: source already published to the public is not on this list: those are
#: governed by the source's compliance determination and by robots/rate
#: limits, not by a per-activity clause. Everything that reaches into the
#: platform -- signing in to search it, applying, bidding, messaging,
#: accepting, delivering, managing the account, spending -- is.
GOVERNED_BY_TERMS = (
    access.DISCOVER_AUTHENTICATED, access.SUBMIT_APPLICATION,
    access.SUBMIT_BID, access.SEND_MESSAGE, access.ACCEPT_CONTRACT,
    access.DELIVER_THROUGH_PLATFORM, access.ACCOUNT_MANAGEMENT,
    access.SPEND_MONEY, access.HOURLY_MINIMUM,
)


@dataclass(frozen=True, slots=True)
class TermsFinding:
    """One reading of one clause, about one activity, on one date."""

    activity: str
    finding: str = TERMS_UNDETERMINED
    #: Where it was read. A finding with no citation is somebody's memory.
    citation: str = ""
    read_on: str = ""

    def __post_init__(self) -> None:
        if self.activity not in access.PERMISSIONS:
            raise ValueError(
                f"{self.activity!r} is not a source permission, so a terms "
                "finding about it could never be enforced")
        if self.finding not in TERMS_FINDINGS:
            raise ValueError(f"{self.finding!r} is not a terms finding")
        if self.finding != TERMS_UNDETERMINED and not (self.citation
                                                       and self.read_on):
            raise ValueError(
                f"a {self.finding} finding for {self.activity} needs a "
                "citation and a date; an uncited reading of a platform's "
                "terms is an opinion about them")

    @property
    def allows(self) -> bool:
        return self.finding == TERMS_PERMITTED


@dataclass(frozen=True, slots=True)
class CatalogueEntry:
    """What is known about one place work might come from.

    Every field is either an observed fact or an explicit "not established".
    There is no field for a guess.
    """

    source_id: str
    display_name: str
    source_type: str
    #: What would have to be true for automated discovery, in the owner's words.
    determination_needed: str
    #: Established access method. ``UNDETERMINED`` until someone establishes one.
    access_method: str = access.UNDETERMINED
    #: Whether the platform requires an account to see listings at all. This is
    #: a widely-observable property of the product, not a claim about its terms.
    auth_required: bool = True
    #: Manual ingestion is always available: it is the owner reading a page and
    #: typing what they found. No platform permission is involved.
    manual_import: bool = True
    notes: str = ""
    #: The day the access terms were read from their authoritative source.
    #: A determination is true of a moment, not forever (§9).
    verified_on: str = ""
    #: Where it was read from, so the reading can be repeated.
    evidence: str = ""
    #: Whether a real adapter exists, as opposed to an entry in a list.
    adapter_implemented: bool = False
    #: What this platform's terms say, activity by activity. Empty means
    #: nothing has been read, which reads as UNDETERMINED everywhere.
    terms: tuple = ()

    def terms_for(self, activity: str) -> "TermsFinding":
        """The finding for one activity, or an explicit UNDETERMINED.

        Never returns None. A caller that has to remember to handle a
        missing finding is a caller that will one day forget.
        """
        for finding in self.terms:
            if finding.activity == activity:
                return finding
        return TermsFinding(activity=activity, finding=TERMS_UNDETERMINED)

    def permitted_activities(self) -> tuple:
        return tuple(f.activity for f in self.terms if f.allows)

    @property
    def automated_discovery_supported(self) -> bool:
        """False for every entry until a determination says otherwise."""
        return self.access_method in access.AUTOMATABLE

    def to_dict(self) -> dict:
        return {
            "source_id": self.source_id, "display_name": self.display_name,
            "source_type": self.source_type, "access_method": self.access_method,
            "automated_discovery": self.automated_discovery_supported,
            "manual_import": self.manual_import,
            "auth_required": self.auth_required,
            "determination_needed": self.determination_needed,
            "notes": self.notes, "verified_on": self.verified_on,
            "evidence": self.evidence,
            "adapter_implemented": self.adapter_implemented,
            "terms": {f.activity: {"finding": f.finding,
                                   "citation": f.citation,
                                   "read_on": f.read_on}
                      for f in self.terms},
        }

    def stale(self, *, today: str = "", max_age_days: int = 180) -> bool:
        """Whether the determination is old enough to need re-reading.

        Terms change. A determination with no date is stale by definition --
        "somebody checked at some point" is not a finding.
        """
        import datetime as _dt

        if self.access_method == access.MANUAL_IMPORT:
            # The owner typing work in has no external terms to expire. A
            # staleness flag here would be permanent noise on the one path
            # that is never blocked by anybody.
            return False
        if not self.verified_on:
            return True
        try:
            checked = _dt.date.fromisoformat(self.verified_on)
            now = (_dt.date.fromisoformat(today) if today
                   else _dt.date.today())
        except ValueError:
            return True
        return (now - checked).days > max_age_days


#: robots.txt is a platform's own machine-readable statement about automated
#: access, published for exactly this purpose. Reading one is not scraping,
#: and honouring it is not optional. These were fetched and read on the date
#: recorded against each finding; a `stale()` check will ask for them again.
_READ_ON = "2026-10-13"


def _robots(source_id: str, finding: str, detail: str) -> "TermsFinding":
    return TermsFinding(
        activity=access.DISCOVER, finding=finding,
        citation=f"https://www.{source_id}/robots.txt — {detail}",
        read_on=_READ_ON)


_TERMS = ("the owner must read this platform's current terms of service for "
          "their own account and record, in writing, whether automated access "
          "is permitted and by what method")

CATALOGUE: tuple[CatalogueEntry, ...] = (

    # ------------------------------------------------ freelance marketplaces
    CatalogueEntry(
        "upwork", "Upwork", FREELANCE_MARKETPLACE,
        "Upwork publishes a GraphQL API with OAuth2 and a marketplace job "
        "search. Using it needs the owner to register an application on the "
        "Upwork developer site under their own account, complete identity "
        "verification, and accept the API terms — and to establish that their "
        "intended use is permitted by those terms",
        verified_on="2026-10-14",
        evidence="upwork.com/developer — GraphQL API at api.upwork.com/graphql, "
                 "OAuth 2.0 authorization code flow, documented job-search "
                 "queries. Re-verified 2026-10-14: POST to that endpoint "
                 "without a credential returns HTTP 401 "
                 "{\"message\":\"Authentication failed\"}, so the endpoint "
                 "is real and closed; the developer documentation page "
                 "returns 403 to an automated fetch. Published access "
                 "conditions: a client ID and shared secret, an account "
                 "under a company with a verified ID, a stated description "
                 "of the intended use, and a documented daily cap of 40,000 "
                 "requests. Whether a given automated use is permitted is "
                 "in the API terms for the owner's own account and was not "
                 "established here.",
        notes="An API demonstrably exists, which is not the same as permission "
              "to use it this way. Left manual-only until the owner registers "
              "an application and records what their terms allow.",
        terms=(
            _robots("upwork.com", TERMS_FORBIDDEN,
                    "Disallow: /jobs/ , /*/jobs/search* , /o/jobs/job/* and "
                    "/api* ; no Crawl-delay. Note the contrast with "
                    "Freelancer, whose robots.txt does not mention /api: "
                    "Upwork disallows its API path to crawlers and offers "
                    "the GraphQL endpoint instead, under credentials"),
            # The official GraphQL API is the sanctioned route, and its own
            # terms have not been read: the developer documentation itself
            # returned HTTP 403 to an automated fetch on the date above,
            # which is the platform declining to be read by a machine.
            TermsFinding(access.DISCOVER_AUTHENTICATED),
            TermsFinding(access.SUBMIT_BID),
            TermsFinding(access.SEND_MESSAGE),
            TermsFinding(access.ACCEPT_CONTRACT),
            TermsFinding(access.DELIVER_THROUGH_PLATFORM),
        )),
    CatalogueEntry(
        "fiverr", "Fiverr", FREELANCE_MARKETPLACE, _TERMS,
        notes="Seller-led rather than brief-led, so the acquisition shape "
              "differs from a bid board. Not established; manual only. No "
              "public seller API was found on the date recorded below.",
        terms=(
            _robots("fiverr.com", TERMS_FORBIDDEN,
                    "Disallow: /search/ , /search_results/ , /gigs/search ; "
                    "no Crawl-delay"),
            TermsFinding(access.DISCOVER_AUTHENTICATED),
            TermsFinding(access.SEND_MESSAGE),
            TermsFinding(access.DELIVER_THROUGH_PLATFORM),
        )),
    CatalogueEntry(
        "freelancer", "Freelancer.com", FREELANCE_MARKETPLACE, _TERMS,
        # Not UNDETERMINED any more: the agreement has been read and it
        # says no. UNSUPPORTED_AUTOMATION is the honest value while that
        # is true, and the value it moves to is APPROVED_INTEGRATION --
        # not PUBLIC_FEED -- because what unblocks it is a permission
        # granted to this owner, not a property of the endpoint.
        access_method=access.UNSUPPORTED_AUTOMATION,
        # An adapter exists: solvent.marketplaces.FreelancerActiveProjects
        # parses the documented response, verified field by field against a
        # live sample. It changes nothing about permission. This flag is
        # only reached once the terms say yes, and they say UNDETERMINED,
        # so the status below is still PENDING -- which is the honest
        # shape of "we can read it the moment we are allowed to".
        adapter_implemented=True,
        verified_on="2026-10-14",
        evidence="GET https://www.freelancer.com/api/projects/0.1/projects/"
                 "active/?limit=1 returned HTTP 200 with live project JSON "
                 "and no credential, on 2026-10-14. The response carries "
                 "the platform's own limits in a header: "
                 "`ratelimit-limit: 200, 200;window=60, 1000;window=3600`.",
        notes="Bid-based, and bidding may consume paid credits -- which is "
              "why SUBMIT_BID and SPEND_MONEY are separate permissions.\n\n"
              "**A correction to the 2026-10-13 finding.** That reading "
              "recorded public discovery as FORBIDDEN, citing robots.txt. "
              "The citation was accurate -- /projects/browse.php*, /jobs/?*, "
              "/work/* and /find/* really are disallowed, and still are -- "
              "but the conclusion drawn from it was too broad. Those are "
              "*web page* paths. robots.txt says nothing about /api/, and "
              "the documented JSON API answers an unauthenticated request "
              "for active projects and tells the caller its own rate limits "
              "in the response headers, which is a platform describing "
              "machine access rather than refusing it.\n\n"
              "So the finding is UNDETERMINED rather than FORBIDDEN: the "
              "page-crawling route is shut, the API route is open and "
              "nobody has read the API terms of service, which is a "
              "separate document from robots.txt. Operationally nothing "
              "changes -- unread is not permitted, and both answers refuse "
              "-- but the record now says the true thing, and it tells the "
              "owner exactly which document to read.\n\n"
              "**2026-10-15: that document has now been read, and the "
              "answer is no.** Section 33 of the user agreement -- "
              "Access and Interference -- says: \"You agree that you will "
              "not use any robot, spider, scraper or other automated "
              "means to access the Website via any means, including for "
              "the avoidance of doubt access to our API or application "
              "programming interface, for any purpose without our express "
              "written permission.\"\n\n"
              "This settles three things that were open. The 200 response "
              "and the published rate-limit header are not permission: a "
              "platform can answer a request it has told you not to make. "
              "robots.txt not disallowing /api/ is not permission either, "
              "because the agreement is the stricter document and the "
              "agreement says API access is covered. And it applies to "
              "reading, not only to bidding -- DISCOVER is FORBIDDEN here "
              "rather than merely undetermined.\n\n"
              "What would change it is a single thing: express written "
              "permission from Freelancer. The endpoint, the OAuth2 flow "
              "at accounts.freelancer.com/oauth/token, the documented "
              "POST /api/projects/0.1/bids/ body and the sandbox at "
              "freelancer-sandbox.com are all real and all waiting on "
              "that one document.",
        terms=(
            TermsFinding(
                access.DISCOVER, TERMS_FORBIDDEN,
                citation="https://www.freelancer.com/about/terms section 33 (Access and "
                         "Interference), read 2026-10-15: \"You agree that you "
                         "will not use any robot, spider, scraper or other "
                         "automated means to access the Website via any means, "
                         "including for the avoidance of doubt access to our API "
                         "or application programming interface, for any purpose "
                         "without our express written permission.\" The API "
                         "returning 200 is not that permission",
                read_on="2026-10-15"),
            TermsFinding(
                access.DISCOVER_AUTHENTICATED, TERMS_FORBIDDEN,
                citation="same clause: section 33 covers API access 'for "
                         "any purpose', which includes an authenticated "
                         "read, https://www.freelancer.com/about/terms",
                read_on="2026-10-15"),
            TermsFinding(
                access.SUBMIT_BID, TERMS_FORBIDDEN,
                citation="POST /api/projects/0.1/bids/ is documented at "
                         "developers.freelancer.com and the endpoint "
                         "exists, but section 33 of the user agreement "
                         "forbids automated access to the API without "
                         "express written permission. A documented "
                         "endpoint is a capability, not a permission",
                read_on="2026-10-15"),
            TermsFinding(access.SPEND_MONEY),
        )),
    CatalogueEntry(
        "peopleperhour", "PeoplePerHour", FREELANCE_MARKETPLACE, _TERMS,
        notes="Not established; manual only. The 2026-10-13 request for "
              "robots.txt came back empty; the same request on 2026-10-14 "
              "returned it, so the earlier blank was a transport failure "
              "rather than an absent file -- worth recording, because "
              "\"we got nothing\" and \"there is nothing\" are different "
              "facts and only one of them is about the platform.\n\n"
              "What it says is narrow and specific: `Disallow: /*?` shuts "
              "every URL carrying a query string, which is how the site's "
              "search works, so search is out. Listing pages without a "
              "query string are not disallowed. No public API was found.",
        terms=(
            TermsFinding(
                access.DISCOVER, TERMS_UNDETERMINED,
                citation="https://www.peopleperhour.com/robots.txt read "
                         "2026-10-14: `Disallow: /*?` rules out "
                         "query-string search; /job/bidders* and /job/new* "
                         "are disallowed; plain listing paths are not "
                         "mentioned either way, and no terms-of-service "
                         "reading has been done",
                read_on="2026-10-14"),
        )),
    CatalogueEntry(
        "guru", "Guru", FREELANCE_MARKETPLACE, _TERMS,
        notes="Not established; manual only. robots.txt returned HTTP 403 to "
              "an automated request on 2026-10-13 and again on 2026-10-14, "
              "from a different user agent -- the site declining to be read "
              "by a machine, consistently, which is itself an answer and "
              "must not be worked around. https://www.guru.com/api returned "
              "403 as well. Two refusals a day apart are not a transient "
              "failure, and the correct response to a 403 is to stop."),
    CatalogueEntry(
        "contra", "Contra", FREELANCE_MARKETPLACE, _TERMS,
        notes="The only one of the six whose robots.txt does not disallow "
              "its listing pages -- it blocks /@internal and two redirect "
              "parameters and nothing else. That makes it the single "
              "candidate among them for a permitted automated read, and it "
              "is still not an established route: robots.txt governs "
              "crawlers, the terms of service are a separate document, and "
              "nobody has read Contra's. A read-only public API exists at "
              "contra.com/public-api/ behind an X-API-Key; it returned 401 "
              "to an unauthenticated request, and third-party listings "
              "describe it as covering expert profiles and programs -- "
              "which helps clients find freelancers, the opposite of what "
              "DeskPilot needs.\n\n"
              "And the permission turns out to apply to the wrong pages. "
              "Contra's Opportunities board -- the part that lists jobs -- "
              "is behind Contra Pro, a paid subscription, so reading it is "
              "DISCOVER_AUTHENTICATED and SPEND_MONEY rather than the "
              "public DISCOVER its robots.txt allows. The crawl permission "
              "is real and covers pages that do not contain the work. That "
              "is precisely the conflation the seven separate permissions "
              "exist to prevent.",
        terms=(
            TermsFinding(
                activity=access.DISCOVER, finding=TERMS_PERMITTED,
                citation="https://contra.com/robots.txt — User-agent: * "
                         "disallows only /@internal and redirectTo/replyId "
                         "parameters; no job, project or search path is "
                         "disallowed, and there is no Crawl-delay",
                read_on=_READ_ON),
            # The API exists and needs a key; what it actually exposes was
            # not verifiable without one.
            TermsFinding(access.DISCOVER_AUTHENTICATED),
            TermsFinding(access.SUBMIT_BID),
            TermsFinding(access.SEND_MESSAGE),
            TermsFinding(access.ACCEPT_CONTRACT),
        )),

    # ------------------------------------------------- public / government
    # --------------------------------------------------------------- direct
    CatalogueEntry(
        "owner_entered", "Owner-entered opportunity", DIRECT,
        "none: the owner found this themselves",
        access_method=access.MANUAL_IMPORT, auth_required=False,
        notes="The path that is blocked by nothing external. The owner brings "
              "the work; DeskPilot qualifies, prices, executes, verifies and "
              "delivers it. Because the owner entered the terms, they are "
              "owner-confirmed rather than merely client-asserted."),
    CatalogueEntry(
        "file_import", "File import (CSV)", DIRECT,
        "none: the owner supplies the file",
        access_method=access.MANUAL_IMPORT, auth_required=False,
        notes="Uses the CSV reading DeskPilot already has. The file's contents "
              "are untrusted like any other external text."),
    CatalogueEntry(
        "client_inquiry", "Direct client inquiry", DIRECT,
        "none: the client approached the owner",
        access_method=access.MANUAL_IMPORT, auth_required=False,
        notes="Someone got in touch. Recorded so it goes through the same "
              "qualification as anything found on a board."),
)

BY_ID = {entry.source_id: entry for entry in CATALOGUE}


def entry(source_id: str) -> CatalogueEntry | None:
    return BY_ID.get(source_id)


def by_type(source_type: str) -> list:
    return [e for e in CATALOGUE if e.source_type == source_type]


def automated_sources() -> list:
    """Entries with an established automated access method.

    Empty, and that is the honest answer. It is a function rather than a
    constant so the page showing it cannot be written to say anything else.
    """
    return [e for e in CATALOGUE if e.automated_discovery_supported]


def manual_sources() -> list:
    return [e for e in CATALOGUE if e.manual_import]


# ---------------------------------------------------------------------------
# What state each platform is actually in
# ---------------------------------------------------------------------------
#
# Seven statuses, **derived** from the facts above rather than stored beside
# them. A stored status would be a second opinion about the same question, and
# the first time it disagreed with ``access_method`` nobody would know which
# was right -- which is the mistake this project has already made twice, once
# with diagnostics and once with the two setup pages.
#
# So this is a function of the entry and the deployment, and the only inputs
# are things somebody established: the determination the owner recorded, the
# credentials actually present, and whether an adapter exists.

#: An established automated path: an API, an approved integration, a feed, or
#: a page whose terms permit fetching. Automated discovery may run.
ESTABLISHED_AUTOMATED = "ESTABLISHED - AUTOMATED"

#: The owner drives the fetch -- they open the page, they are present, and
#: DeskPilot reads what they hand it. Not a lesser outcome: it is the
#: difference between a business that works and an account that is banned.
ESTABLISHED_OWNER_AUTHORIZED = "ESTABLISHED - OWNER-AUTHORIZED"

#: The owner pastes or uploads what they found. Blocked by nobody, needs
#: permission from nobody, and feeds exactly the same pipeline.
ESTABLISHED_MANUAL_IMPORT = "ESTABLISHED - MANUAL IMPORT"

#: A determination exists and the owner has not authorised the permissions it
#: would need. Nothing is wrong; a decision is outstanding.
PENDING_OWNER_AUTHORIZATION = "PENDING OWNER AUTHORIZATION"

#: The platform's own terms forbid it. Recorded so nobody tries again, and so
#: that "we could not" is never confused with "we must not".
BLOCKED_PLATFORM_RESTRICTION = "BLOCKED - PLATFORM RESTRICTION"

#: Permitted, and the credential to do it is not on this machine.
BLOCKED_MISSING_CREDENTIALS = "BLOCKED - MISSING CREDENTIALS"

#: Nobody has read this platform's terms, or the reading is too old to rely
#: on. The honest default and, today, the answer for every marketplace.
UNVERIFIED = "UNVERIFIED"

OPERATIONAL_STATUSES = (
    ESTABLISHED_AUTOMATED, ESTABLISHED_OWNER_AUTHORIZED,
    ESTABLISHED_MANUAL_IMPORT, PENDING_OWNER_AUTHORIZATION,
    BLOCKED_PLATFORM_RESTRICTION, BLOCKED_MISSING_CREDENTIALS, UNVERIFIED,
)


def operational_status(entry: "CatalogueEntry", *,
                       registered: bool = False,
                       permissions: tuple[str, ...] = (),
                       credential_present: bool | None = None,
                       today: str = "") -> tuple[str, str]:
    """``(status, why)`` for one platform. Derived, never stored.

    The order of these tests is the design. Each one asks a question that
    makes the later ones irrelevant:

    1. **Does the platform forbid it?** A prohibition outranks everything,
       including a credential being present and an adapter existing. Checking
       it first means no later branch can talk its way past it.
    2. **Is the reading current?** A determination with no date, or one older
       than the window, is not a finding -- terms change, and "somebody
       checked at some point" is not evidence. This comes before the
       credential check so that a stale entry with working credentials reads
       UNVERIFIED rather than ready.
    3. **Has the owner authorised the permissions?** Registering a source
       grants the read side only; discovering and applying are different
       grants, and a platform nobody authorised is pending rather than broken.
    4. **Is the credential here?** Only now, because a missing credential for
       something permitted and authorised is the one case where the answer is
       "fetch me a key".

    ``credential_present=None`` means nobody asked, which is not the same as
    absent: an entry needing no credential must not read BLOCKED because a
    caller did not look.
    """
    if entry.access_method == access.UNSUPPORTED_AUTOMATION:
        return (BLOCKED_PLATFORM_RESTRICTION,
                entry.notes or "this platform's terms do not permit automated "
                               "access, so DeskPilot does not attempt it")
    if entry.access_method == access.UNDETERMINED:
        return (UNVERIFIED, entry.determination_needed)
    if entry.access_method == access.MANUAL_IMPORT:
        # No external terms to expire and no credential to hold. The one path
        # that is never blocked by anybody.
        return (ESTABLISHED_MANUAL_IMPORT,
                "the owner enters what they found; no platform permission is "
                "involved")
    if entry.stale(today=today):
        return (UNVERIFIED,
                f"the determination for this platform was recorded "
                f"{entry.verified_on or 'at no recorded date'} and is old "
                f"enough to need re-reading")
    if not registered:
        return (PENDING_OWNER_AUTHORIZATION,
                "a determination exists and this source is not registered in "
                "this deployment")
    if access.DISCOVER not in permissions:
        return (PENDING_OWNER_AUTHORIZATION,
                "registered without the DISCOVER permission, so nothing may "
                "be fetched from it yet")
    if credential_present is False:
        return (BLOCKED_MISSING_CREDENTIALS,
                "permitted and authorised; the credential it needs is not on "
                "this machine")
    if entry.access_method == access.OWNER_ASSISTED_BROWSER:
        return (ESTABLISHED_OWNER_AUTHORIZED,
                "the owner opens the platform and DeskPilot reads what they "
                "hand it")
    if not entry.adapter_implemented:
        # The distinction §9 already makes about rehydration: a row naming a
        # source is a registration, not an implementation.
        return (PENDING_OWNER_AUTHORIZATION,
                "permitted and authorised, and no adapter has been written "
                "for it -- a row naming a source is not code that fetches "
                "from one")
    return (ESTABLISHED_AUTOMATED,
            f"{entry.access_method} established on {entry.verified_on} "
            f"({entry.evidence or 'no source recorded'})")


def marketplace_interface(*, today: str = "") -> list[dict]:
    """One row per marketplace: what is permitted there, activity by activity.

    The six are the whole list now, and this is the view that answers
    "what can DeskPilot actually do on each of them?" without blurring
    the seven activities into one word. Permission for one is not
    permission for another: a platform may publish an API for managing
    an account and forbid crawling its job pages in the same breath, and
    reading that as "Upwork is integrated" is the mistake the split
    exists to prevent.

    Derived from the recorded findings every time it is called. Nothing
    here is stored, so a row cannot drift away from the research behind
    it, and an activity nobody has read about reads UNDETERMINED rather
    than disappearing.
    """
    rows = []
    for entry in by_type(FREELANCE_MARKETPLACE):
        activities = {}
        for label in sorted(access.ACTIVITY_KINDS):
            activity = access.ACTIVITY_KINDS[label]
            finding = entry.terms_for(activity)
            activities[label] = {
                "activity": activity,
                "finding": finding.finding,
                "citation": finding.citation,
                "read_on": finding.read_on,
            }
        status, why = operational_status(entry, today=today)
        rows.append({
            "source_id": entry.source_id,
            "display_name": entry.display_name,
            "status": status,
            "why": why,
            "permitted": list(entry.permitted_activities()),
            "activities": activities,
            # True of all six and the reason any of them is usable at
            # all: the owner reading a listing and typing it in needs
            # nobody's permission, makes no automated request, and goes
            # through exactly the same qualification as anything else.
            "manual_import_available": True,
        })
    return rows


def marketplace_summary(*, today: str = "") -> str:
    """The six-platform interface as text, for the CLI and the report."""
    rows = marketplace_interface(today=today)
    out = [f"{len(rows)} marketplaces; manual import is available on all of "
           "them and needs nobody's permission", ""]
    for row in rows:
        permitted = ", ".join(row["permitted"]) or "nothing"
        out.append(f"{row['display_name']}  [{row['status']}]")
        out.append(f"    automated: {permitted}")
        for label, finding in row["activities"].items():
            mark = {TERMS_PERMITTED: "yes", TERMS_FORBIDDEN: "no "}.get(
                finding["finding"], "?  ")
            cite = finding["citation"]
            out.append(f"      [{mark}] {label}"
                       + (f" — {cite[:70]}" if cite else " — nobody has read this"))
        out.append("")
    return "\n".join(out).rstrip() + "\n"
