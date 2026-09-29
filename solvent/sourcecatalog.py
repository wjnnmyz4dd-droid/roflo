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
            "notes": self.notes,
        }


_TERMS = ("the owner must read this platform's current terms of service for "
          "their own account and record, in writing, whether automated access "
          "is permitted and by what method")

CATALOGUE: tuple[CatalogueEntry, ...] = (

    # ------------------------------------------------ freelance marketplaces
    CatalogueEntry(
        "upwork", "Upwork", FREELANCE_MARKETPLACE, _TERMS,
        notes="Listings are behind an account. Nothing about automated access "
              "has been established here; manual ingestion is available now."),
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
        "sam_gov", "SAM.gov (US federal opportunities)", PUBLIC_PROCUREMENT,
        "the owner must establish which access route applies to them, and "
        "whether federal contracting is work DeskPilot is registered and "
        "eligible to perform at all",
        auth_required=False,
        notes="Public procurement is materially different from marketplace "
              "work: eligibility, registration and reporting obligations "
              "attach to the contractor, not just to the contract. Eligibility "
              "is a separate question from access and is not assumed here."),
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
