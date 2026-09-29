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
PUBLIC_PROCUREMENT = "PUBLIC_PROCUREMENT"
RFP_BID_BOARD = "RFP_BID_BOARD"
DIRECT = "DIRECT"


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
        verified_on="2026-09-29",
        evidence="upwork.com/developer — GraphQL API at api.upwork.com/graphql, "
                 "OAuth 2.0 authorization code flow, documented job-search "
                 "queries. Whether a given automated use is permitted is in "
                 "the API terms for the owner's own account and was not "
                 "established here.",
        notes="An API demonstrably exists, which is not the same as permission "
              "to use it this way. Left manual-only until the owner registers "
              "an application and records what their terms allow."),
    CatalogueEntry(
        "fiverr", "Fiverr", FREELANCE_MARKETPLACE, _TERMS,
        notes="Seller-led rather than brief-led, so the acquisition shape "
              "differs from a bid board. Not established; manual only."),
    CatalogueEntry(
        "freelancer", "Freelancer.com", FREELANCE_MARKETPLACE, _TERMS,
        notes="Bid-based, and bidding may consume paid credits -- which is "
              "why SUBMIT_BID and SPEND_MONEY are separate permissions."),
    CatalogueEntry(
        "peopleperhour", "PeoplePerHour", FREELANCE_MARKETPLACE, _TERMS,
        notes="Not established; manual only."),
    CatalogueEntry(
        "guru", "Guru", FREELANCE_MARKETPLACE, _TERMS,
        notes="Not established; manual only."),
    CatalogueEntry(
        "contra", "Contra", FREELANCE_MARKETPLACE, _TERMS,
        notes="Not established; manual only."),

    # ------------------------------------------------- public / government
    CatalogueEntry(
        "sam_gov", "SAM.gov (US federal contract opportunities)",
        PUBLIC_PROCUREMENT,
        "the owner must obtain a free public API key from their SAM.gov "
        "account, and must separately establish whether they are registered "
        "and eligible to bid on federal contracts at all",
        access_method=access.OFFICIAL_API, auth_required=True,
        verified_on="2026-09-29",
        evidence="open.gsa.gov/api/get-opportunities-public-api/ — api_key "
                 "required, GET https://api.sam.gov/opportunities/v2/search, "
                 "pagination required, limit max 1000, postedFrom/postedTo "
                 "required and at most one year apart",
        adapter_implemented=True,
        notes="Contract work, which is DeskPilot's actual mission. Visible is "
              "not eligible: eligibility, registration and reporting "
              "obligations attach to the contractor, not to the contract, and "
              "nothing here infers that the owner may bid."),
    CatalogueEntry(
        "grants_gov", "Grants.gov (US federal funding opportunities)",
        PUBLIC_PROCUREMENT,
        "none for access; the owner decides whether grant-funded work is in "
        "scope, since a grant is applied for rather than sold",
        access_method=access.PUBLIC_FEED, auth_required=False,
        verified_on="2026-09-29",
        evidence="grants.gov/api/api-guide — \"Authentication and "
                 "authorization are not required for the following APIs: "
                 "Endpoint: search2\"; POST https://api.grants.gov/v1/api/"
                 "search2; confirmed against a live response on the same date",
        adapter_implemented=True,
        notes="The only source needing no credential of any kind, so the only "
              "one that can demonstrate autonomous discovery on a machine the "
              "owner has not configured. Returns funding opportunities rather "
              "than client work, which is why it is labelled and left for the "
              "owner to enable."),
    CatalogueEntry(
        "state_local_procurement", "State / local procurement portal",
        PUBLIC_PROCUREMENT,
        "each jurisdiction runs its own portal on its own terms; one entry is "
        "registered per portal the owner actually uses",
        auth_required=False,
        notes="Deliberately generic. Hard-coding one state would make the "
              "registry wrong everywhere else; the owner registers the portal "
              "they need as its own source."),

    # ------------------------------------------------------ rfp / bid boards
    CatalogueEntry(
        "rfp_board", "RFP / bid board", RFP_BID_BOARD,
        "the owner records which board, and what its terms allow",
        auth_required=False,
        notes="A shape, so a legitimate board can be added later without "
              "changing the Job Orchestrator or anything downstream of it."),

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
