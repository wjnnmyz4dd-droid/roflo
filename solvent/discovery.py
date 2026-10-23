"""Opportunity Discovery — finding legitimate paid work.

This is the front of Solvent's business loop. Solvent exists to find, qualify,
complete, deliver and profit from client work, and nothing gets found without
this authority.

Owns one decision: *which candidate opportunities exist, from which approved
sources?*

**Discovery is not acceptance.** This module cannot accept, bid, claim, commit,
create an account, or spend. It produces candidates and nothing else; every one of
them must still pass Capability, Conformance, the Financial Governor, Policy and
the Action Gate before anything happens. That separation is the whole reason
finding work is safe to automate before deciding what to take.

Three rules govern a source:

* **A source is usable only when Policy approves it and its compliance
  determination says automation is permitted.** Finding a platform is not
  permission to operate on it.
* **Every external fetch goes through the Action Gate.** Discovery gets no bypass,
  no special credential, and no private network path.
* **Everything a source returns is untrusted.** A job posting is attacker-authored
  text; requirements taken from it are unconfirmed drafts that cannot gate a
  commitment.

No anti-bot circumvention, no CAPTCHA bypass, no impersonation, no credential
sharing, no rate-limit evasion. A source that would require any of those is
recorded ``PROHIBITED`` and never polled.
"""

from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, replace
from enum import Enum

from .audit import AuditLog, new_id, now
from .content import UntrustedContent, quarantine
from .errors import FailClosed
from . import intake
from . import opportunityrisk as risk
from . import sourceaccess as access
from .resilience import redact
from .store import Store
from .types import ActionClass, Cents, Jurisdiction, LocationSignals, PrivacyClass


class Readiness(Enum):
    """How far a source has actually got. Claims require evidence at each rung.

    Scraping a page is not marketplace readiness. Each level below is a separate,
    checkable claim, so "Solvent supports platform X" can never be asserted from
    the fact that a page loaded.
    """

    RESEARCH_REQUIRED = "RESEARCH_REQUIRED"
    RESEARCHED = "RESEARCHED"
    SUPPORTED_BY_API = "SUPPORTED_BY_API"
    SUPPORTED_BY_BROWSER = "SUPPORTED_BY_BROWSER"
    PERMITTED_AUTOMATION = "PERMITTED_AUTOMATION"
    IMPLEMENTED = "IMPLEMENTED"
    TESTED = "TESTED"
    ENFORCED = "ENFORCED"
    PRODUCTION_READY = "PRODUCTION_READY"

    @property
    def rank(self) -> int:
        order = [
            "RESEARCH_REQUIRED", "RESEARCHED", "SUPPORTED_BY_API",
            "SUPPORTED_BY_BROWSER", "PERMITTED_AUTOMATION", "IMPLEMENTED",
            "TESTED", "ENFORCED", "PRODUCTION_READY",
        ]
        return order.index(self.value)


class Compliance(Enum):
    """Whether automation of this source is permitted, as determined by a human."""

    UNDETERMINED = "UNDETERMINED"
    PERMITTED = "PERMITTED"
    PROHIBITED = "PROHIBITED"
    SUSPENDED = "SUSPENDED"

    @property
    def may_poll(self) -> bool:
        """Only an explicit, recorded permission allows polling."""
        return self is Compliance.PERMITTED


class OpportunityStatus(Enum):
    DISCOVERED = "DISCOVERED"
    QUALIFYING = "QUALIFYING"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True, slots=True)
class Opportunity:
    """A candidate piece of paid work. Not a job, and not a commitment."""

    id: str
    source: str
    external_ref: str
    title: str
    quoted_cents: Cents
    needs: tuple[str, ...] = ()
    deadline: str = ""
    client_ref: str = ""
    signals: LocationSignals = field(default_factory=LocationSignals)
    posting: UntrustedContent | None = None
    #: True when title/budget/needs arrived as platform schema fields.
    structured_terms: bool = False
    #: True when the owner entered this by hand, which is stronger than either.
    owner_entered: bool = False
    deliverables: tuple[str, ...] = ()
    payment_structure: str = ""
    platform_fee_cents: Cents = 0
    #: External money that must be spent before the work can start.
    upfront_cost_cents: Cents = 0
    external_url: str = ""
    attachments: tuple[str, ...] = ()
    risk_indicators: tuple[str, ...] = ()
    terms_ref: str = ""
    expires_at: str = ""
    confidence: float = 1.0

    @property
    def net_budget_cents(self) -> Cents:
        """Budget after the platform's cut. What Solvent could actually earn."""
        return self.quoted_cents - self.platform_fee_cents

    @property
    def description(self) -> str:
        """The posting text, explicitly marked as untrusted at the call site."""
        return self.posting.text if self.posting else ""


class WorkSource(ABC):
    """A place paid work can be found.

    Implementations must not hold credentials, open sockets, or call anything
    directly. They receive a ``fetch`` callable supplied by Discovery, which routes
    through the Action Gate, so a source physically cannot reach the network on its
    own.
    """

    name: str = "unnamed"
    kind: str = "generic"
    #: Host contacted, for the Gate's allowlist. Empty means in-process fixture.
    host: str = ""
    is_fixture: bool = False
    #: Whether this source delivers the offer terms as structured fields under a
    #: platform schema (an API), rather than as prose to be interpreted.
    #:
    #: This is the difference between "the client stated a budget and a category
    #: in the platform's own fields" and "a paragraph claims things". Structured
    #: terms are a client assertion through a structured channel and may gate a
    #: commitment; the free-text body never can, however it is worded. A source
    #: that scrapes prose must leave this False.
    structured_offer_terms: bool = False
    #: What this source carries. Declared, never inferred: a source that does
    #: not say is UNDECLARED and stays out of the active interface. See
    #: ``sourceaccess.WORK_TYPES``.
    work_type: str = access.UNDECLARED

    @abstractmethod
    def parse(self, payload: object) -> list[Opportunity]:
        """Turn a fetched payload into normalised candidates."""

    def request(self) -> object | None:
        """What to ask the Gate for. ``None`` means no external call is needed."""
        return None


class FixtureSource(WorkSource):
    """An in-process source of invented postings, for tests and the harness.

    Marked ``is_fixture`` so nothing can mistake these for real opportunities, and
    it makes no external call at all. ``structured`` models an API-backed board
    whose title, budget and category are schema fields rather than prose.
    """

    kind = "fixture"
    is_fixture = True
    #: A fixture carries invented postings. It is excluded from the active
    #: interface by ``is_fixture`` long before this matters, but declaring it
    #: means no source anywhere is silently UNDECLARED by omission.
    work_type = access.UNDECLARED

    def __init__(self, name: str, postings: list[dict], *,
                 structured: bool = True) -> None:
        self.name = name
        # Copied, not aliased: a source must not share mutable state with whoever
        # constructed it, or one caller's edit silently changes another's board.
        self._postings = list(postings)
        self.structured_offer_terms = structured

    def parse(self, payload: object) -> list[Opportunity]:
        out = []
        for posting in self._postings:
            signals = LocationSignals(
                project=Jurisdiction(state=posting["project_state"])
                if posting.get("project_state") else None,
                client=Jurisdiction(state=posting["client_state"])
                if posting.get("client_state") else None)
            out.append(Opportunity(
                id=new_id("opp"), source=self.name,
                external_ref=posting.get("ref", ""), title=posting["title"],
                quoted_cents=int(posting["quoted_cents"]),
                needs=tuple(posting.get("needs", ())),
                deadline=posting.get("deadline", ""),
                client_ref=posting.get("client_ref", ""), signals=signals,
                posting=quarantine(posting.get("body", ""), source=self.name,
                                   privacy=PrivacyClass.PUBLIC),
                structured_terms=self.structured_offer_terms,
                owner_entered=getattr(self, "owner_entered_source", False),
                deliverables=tuple(posting.get("deliverables", ())),
                payment_structure=posting.get("payment_structure", ""),
                platform_fee_cents=int(posting.get("platform_fee_cents", 0)),
                upfront_cost_cents=int(posting.get("upfront_cost_cents", 0)),
                external_url=posting.get("url", ""),
                attachments=tuple(posting.get("attachments", ())),
                risk_indicators=tuple(posting.get("risk_indicators", ())),
                terms_ref=posting.get("terms_ref", ""),
                expires_at=posting.get("expires_at", ""),
                confidence=float(posting.get("confidence", 1.0))))
        return out


class ManualSource(FixtureSource):
    """Work the owner found and entered by hand — the semi-automated bridge.

    Full marketplace automation is not a prerequisite for the first dollar. The
    owner brings an opportunity they found themselves, and Solvent does the rest:
    qualification, pricing, the Governor, execution, verification, delivery
    support, payment verification and actual profit.

    This keeps the business moving while platform permissions are unresolved, and
    it is the *only* acquisition path that is blocked by nothing external. Because
    the owner entered the terms, its requirements are OWNER_CONFIRMED rather than
    merely client-asserted.
    """

    kind = "manual"
    is_fixture = False
    owner_entered_source = True
    #: Whatever the owner entered. They saw the work and said what it is,
    #: which is a stronger statement about scope than any feed can make.
    work_type = access.OWNER_DECLARED


class Discovery:
    """The candidate-sourcing authority. It finds work; it never takes it."""

    def __init__(self, store: Store, audit: AuditLog, policy, gate=None,
                 capability=None) -> None:
        self._db = store.for_authority("discovery")
        self._audit = audit
        self._policy = policy
        self._gate = gate
        # Read-only reference, used to tell a gap from a skill
        # DeskPilot already has. Discovery never writes to it.
        self._capability = capability
        self._sources: dict[str, WorkSource] = self._rehydrate()

    def _rehydrate(self) -> dict:
        """Re-create the sources a restart would otherwise lose.

        Registration wrote a row and a live object. Only the row survived a
        restart, so the owner could register their source, restart the service,
        and have every poll fail closed with ``unknown source`` — an owner act
        silently undone by a process restart. Found by restarting a configured
        Solvent rather than by reading the code.

        Only a **manual** source can be rebuilt here, and that is the point: its
        behaviour is entirely described by its record plus the postings the owner
        typed, so re-creating it invents nothing. An adapter that fetches from a
        platform *is* code; its row is a registration, not a definition, and
        whatever constructs it must register it again. Such a source is left
        absent rather than approximated, so polling it fails closed and says so.
        """
        sources: dict[str, WorkSource] = {}
        try:
            rows = self._db.query(
                "SELECT name, kind FROM work_sources WHERE kind = 'manual'")
        except Exception as exc:  # noqa: BLE001 - see below
            # A fresh database has no table yet, which is an empty result. A
            # query naming a column that does not exist is a defect, and
            # catching it here would make it invisible — which is exactly how
            # the control centre's policy read spent its life returning
            # defaults about a halted business.
            if "no such table" not in str(exc):
                raise
            return sources
        for row in rows:
            source = ManualSource(row["name"], [])
            sources[row["name"]] = source
        return sources

    # ---------------------------------------------------------------- sources

    def register_source(self, source: WorkSource, *, owner_identity: str,
                        readiness: Readiness, compliance: Compliance,
                        determination: str) -> None:
        """Record a source and the human determination about automating it.

        ``determination`` is the written finding — what the terms actually say —
        and it is required. A source cannot be registered as permitted without
        someone recording why.
        """
        if not self._policy.is_owner(owner_identity):
            raise FailClosed(
                f"work sources are owner-registered (got {owner_identity!r}); "
                "discovering a platform is not permission to operate on it")
        if compliance is Compliance.PERMITTED and not determination:
            raise FailClosed(
                f"source {source.name!r} cannot be PERMITTED without a recorded "
                "determination of what its terms allow")
        if compliance is Compliance.PERMITTED and readiness.rank < Readiness.PERMITTED_AUTOMATION.rank:
            raise FailClosed(
                f"source {source.name!r} is {readiness.value}; automation may not "
                "be permitted before it reaches PERMITTED_AUTOMATION")
        base_url = (getattr(source, "base_url", "") or "").strip()
        if base_url:
            # §60/§61. Rejected here, before anyone can be asked to put it on
            # the egress allowlist. The Gate is the real control; this stops a
            # loopback or metadata address ever reaching it.
            access.safe_url(base_url)

        self._sources[source.name] = source
        existing = self.source_state(source.name) or {}
        # §11. Re-registering never *widens* what a source may do: whatever was
        # granted stays, and a new source starts with the read side only. The
        # consequential permissions are never granted by registration, so a
        # source cannot acquire the power to apply, bid, message or spend by
        # being added or re-added.
        permissions = (json.loads(existing["permissions"])
                       if existing.get("permissions") else
                       list(access.DEFAULT_PERMISSIONS))
        # §4/§5. Filtered on the way in, not checked on the way out: an
        # inserted-role source must not be *stored* holding DISCOVER, because a
        # stored permission is one a later code path may read and honour.
        permissions = list(intake.permissions_for(
            intake.INSERTED if source.kind == "manual" else intake.FOUND,
            permissions))
        self._db.execute(
            "INSERT OR REPLACE INTO work_sources(name,kind,readiness,compliance,"
            "determination,determined_by,determined_at,is_fixture,healthy,"
            "display_name,source_type,enabled,access_method,auth_required,"
            "manual_import,base_url,health,permissions,credential_configured,"
            "rate_limit_note,min_poll_interval_seconds,retention_note,"
            "terms_notes,opportunities_found) "
            "VALUES(?,?,?,?,?,?,?,?,1,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (source.name, source.kind, readiness.value, compliance.value,
             determination, owner_identity, now(), 1 if source.is_fixture else 0,
             getattr(source, "display_name", "") or source.name,
             getattr(source, "source_type", "GENERIC"),
             1 if existing.get("enabled", 1) else 0,
             getattr(source, "access_method", access.UNDETERMINED),
             1 if getattr(source, "auth_required", False) else 0,
             1 if getattr(source, "manual_import", True) else 0,
             base_url,
             existing.get("health", access.HEALTHY),
             json.dumps(access.validate_permissions(permissions)),
             int(existing.get("credential_configured", 0)),
             getattr(source, "rate_limit_note", ""),
             float(getattr(source, "min_poll_interval_seconds", 0.0) or 0.0),
             getattr(source, "retention_note", ""),
             getattr(source, "terms_notes", ""),
             int(existing.get("opportunities_found", 0))))
        # §7. The submission determination is recorded per source, from the
        # client that knows it, so the owner sees "may DeskPilot submit here"
        # as its own answer rather than inferring it from the search route.
        from . import submission as _tx
        client = _tx.client_for(source.name)
        self._db.execute(
            "UPDATE work_sources SET submission_mode = ?, "
            "submission_verified_on = ?, submission_evidence = ? WHERE name = ?",
            (getattr(source, "submission_mode", "") or client.mode,
             getattr(source, "submission_verified_on", "")
             or client.verified_on,
             getattr(source, "submission_evidence", "") or client.evidence,
             source.name))
        self._db.commit()
        self._audit.record(
            event="discovery.source_registered", authority="discovery",
            initiator=owner_identity, why=determination, decision=source.name,
            readiness=readiness.value, compliance=compliance.value,
            is_fixture=source.is_fixture)

    @staticmethod
    def _fetches(source) -> bool:
        """Does polling this source contact anybody?

        Asked of the source rather than of its registration, because this
        is the one question the object itself is the authority on: a
        source that returns no request makes no call, whatever the
        record says about it.
        """
        try:
            return source.request() is not None
        except Exception:
            # A source whose request cannot even be built is not one to
            # poll unthrottled.
            return True

    def _rate_limit_respected(self, source, state: dict) -> tuple[bool, str]:
        """Has long enough passed since this source was last fetched?

        Refusing rather than sleeping, deliberately. A sleep inside a poll
        would hold the runtime's tick for however long the limit says, which
        turns a politeness rule into an availability problem -- and the task
        runs again on its own timer anyway, so the next sweep picks it up.

        The interval comes from the registration record rather than from the
        adapter object, because the record is what an owner can inspect and
        what survives a restart. An adapter that reported a shorter interval
        than the one registered would be a source deciding its own rate limit,
        which is the same mistake as a source deciding how much its content is
        trusted.
        """
        import datetime as _dt

        interval = float(state.get("min_poll_interval_seconds") or 0.0)
        if interval <= 0:
            # Nothing established. Manual and owner-entered sources make no
            # external call at all, so there is no limit to respect -- but a
            # source that *does* fetch and has no interval recorded is a gap
            # in the registration, not permission to poll freely.
            #
            # This used to say exactly that and then return True anyway,
            # trusting "the registration path records a conservative default
            # for every adapter that fetches". The test guarding it checked
            # that the two shipped adapters happened to declare an interval,
            # which is a property of those two classes rather than a rule
            # anything enforced; retiring them left the test passing over an
            # empty list and the rule never once applied.
            if self._fetches(source):
                return False, (
                    f"source {source.name!r} makes an external request and "
                    "no minimum poll interval is recorded for it, so there "
                    "is nothing to hold it back; register one before polling")
            return True, ""
        last = str(state.get("last_polled_at") or "")
        if not last:
            return True, ""
        try:
            when = _dt.datetime.fromisoformat(last)
        except ValueError:
            # An unreadable timestamp is treated as "polled just now", which
            # is the cautious direction: a corrupt record must not become a
            # licence to hammer somebody's API.
            return False, (f"source {source.name!r} has an unreadable "
                           f"last-polled time, so it is left alone until the "
                           f"next sweep")
        if when.tzinfo is not None:
            when = when.astimezone(_dt.timezone.utc).replace(tzinfo=None)
        waited = (_dt.datetime.utcnow() - when).total_seconds()
        if waited < 0:
            return False, (f"source {source.name!r} records a last-polled "
                           f"time in the future; left alone rather than "
                           f"fetched on a clock nobody can trust")
        if waited < interval:
            return False, (f"source {source.name!r} was polled "
                           f"{waited:.0f}s ago and its rate limit asks for "
                           f"{interval:.0f}s between fetches")
        return True, ""

    def _record_poll(self, name: str) -> None:
        """Stamp the fetch. Written whether or not anything came back.

        A poll that returned nothing still consumed a request against the
        platform's limit, so recording only successful fetches would let an
        empty result reset the clock.
        """
        import datetime as _dt

        self._db.execute(
            "UPDATE work_sources SET last_polled_at = ? WHERE name = ?",
            (_dt.datetime.utcnow().isoformat(timespec="seconds"), name))

    def source_state(self, name: str) -> dict | None:
        row = self._db.query_one("SELECT * FROM work_sources WHERE name = ?", (name,))
        return dict(row) if row else None

    def sources(self) -> list[dict]:
        return [dict(r) for r in self._db.query(
            "SELECT * FROM work_sources ORDER BY name")]

    def source_suitability(self, name: str) -> tuple[str, str]:
        """Should this source appear as a live route to paid work?

        Four separate questions, and a source has to pass all of them. They
        are kept apart because they fail for different reasons and only the
        owner can fix some of them:

        * is it allowed (compliance, and not a fixture)?
        * can it be reached (health, credentials)?
        * is it switched on?
        * **does it carry work this installation can actually be paid for?**

        The last one is the one that is easy to leave out, and leaving it out
        is how an interface comes to list a feed that answers every request
        in 200 milliseconds, is perfectly compliant, and has never once
        contained a job any certified skill could do. Every field on that row
        is true and the row as a whole is a lie.

        Returns ``(verdict, why)``. The verdict is one of ``ACTIVE``,
        ``FIXTURE``, ``SUSPENDED``, ``DISABLED``, ``NEEDS_CREDENTIAL``,
        ``UNREACHABLE``, ``OUT_OF_SCOPE`` or ``UNKNOWN_SOURCE``.
        """
        state = self.source_state(name)
        if state is None:
            return "UNKNOWN_SOURCE", f"{name!r} is not registered"

        if int(state.get("is_fixture", 0)):
            return "FIXTURE", ("an in-process fixture; it exists for tests "
                               "and demonstrations and is never real work")

        compliance = state.get("compliance", "")
        if compliance == Compliance.SUSPENDED.value:
            return "SUSPENDED", (state.get("last_error")
                                 or "suspended on a compliance question")
        if compliance != Compliance.PERMITTED.value:
            return "SUSPENDED", f"compliance is {compliance or 'undetermined'}"

        health = state.get("health") or access.HEALTHY
        if health == access.DISABLED:
            return "DISABLED", "the owner has switched this source off"
        if health == access.AUTH_REQUIRED or (
                self._needs_credential(name)
                and not int(state.get("credential_configured", 0))):
            return "NEEDS_CREDENTIAL", (
                f"{name} needs a credential that is not configured")
        if health in access.NOT_POLLABLE:
            return "UNREACHABLE", (
                f"health is {health}: {state.get('failure_reason') or 'no detail'}")

        # What the platform itself says about being read by a machine. A
        # robots.txt disallowing the job pages is the platform declining,
        # in the one format published for exactly this question, and no
        # amount of owner enthusiasm changes it.
        crawlable, crawl_why = self._public_discovery_allowed(name)
        if not crawlable:
            return "TERMS_FORBID", crawl_why

        work_type = self._work_type(name)
        if work_type not in access.SERVES_COMMERCIAL_SKILLS:
            return "OUT_OF_SCOPE", (
                f"carries {work_type}, which the certified commercial skills "
                "are not paid for; the owner decides whether to bring it "
                "into scope")

        return "ACTIVE", f"permitted, reachable and carrying {work_type}"

    def _public_discovery_allowed(self, name: str) -> tuple[bool, str]:
        """May this source's listings be read automatically at all?

        Only catalogued sources are held to this: a fixture and an
        owner-entered posting have no external terms. A catalogued source
        whose public discovery is FORBIDDEN cannot be an active automated
        route, whatever else is true of it -- and one where nobody has read
        the answer is not active either, because an unread clause is not
        permission.
        """
        from . import sourcecatalog

        entry = sourcecatalog.entry(name)
        if entry is None:
            return self._uncatalogued(name, "reading its listings")
        if entry.access_method == access.MANUAL_IMPORT:
            # The owner reading a page and typing what they found involves
            # no automated access and so no robots directive.
            return True, ""
        state = self.source_state(name)
        if state is not None and int(state.get("is_fixture", 0)):
            # Same reasoning as `_terms_allow`: an in-process fixture named
            # after a real marketplace is not that marketplace. Needed here
            # too now that `_may_poll` asks this question, which it did not
            # when only `source_suitability` did -- and that returns FIXTURE
            # before ever reaching this.
            return True, ""

        finding = entry.terms_for(access.DISCOVER)
        if finding.allows:
            return True, ""
        if finding.finding != sourcecatalog.TERMS_FORBIDDEN and (
                entry.automated_discovery_supported and entry.verified_on):
            # The catalogue already carries a dated, evidenced determination
            # that this source supports automated discovery. Demanding a
            # second finding saying the same thing would be one fact in two
            # places, which is how the two come to disagree.
            return True, ""
        if finding.finding == sourcecatalog.TERMS_FORBIDDEN:
            return False, (
                f"{name} disallows automated reading of its listings "
                f"({finding.citation}). Manual import by the owner remains "
                "available and needs nobody's permission")
        return False, (
            f"nobody has established whether {name} permits automated "
            "reading of its listings; until someone does, it is not an "
            "automated route")

    def _uncatalogued(self, name: str, activity: str) -> tuple[bool, str]:
        """A source nobody researched, deciding whether it may act anyway.

        Both terms gates used to answer "no catalogue entry" with yes, on
        the reasoning that a source nobody wrote terms for should not be
        held to a standard nobody wrote down. That is right for an
        in-process fixture and for work the owner typed in themselves --
        neither contacts anybody, so no external terms exist to honour.

        It is wrong the moment a source has a real host. Then "nobody
        researched this" is exactly the situation the terms gates exist
        for, and answering yes means an unresearched site is governed
        less than a researched one. Retiring four catalogue entries made
        that reachable: delete an entry and its source would have become
        *more* permitted than before.

        So: no host, no external party, carry on. A host, and nobody has
        read its terms, stop.
        """
        source = self._sources.get(name)
        host = str(getattr(source, "host", "") or "")
        is_fixture = bool(getattr(source, "is_fixture", False))
        if not host or is_fixture:
            return True, ""
        return False, (
            f"{name!r} contacts {host} and is not in the source catalogue, "
            f"so nobody has established whether {activity} is permitted "
            "there. An unresearched site is not a permitted one")

    def _work_type(self, name: str) -> str:
        source = self._sources.get(name)
        declared = getattr(source, "work_type", access.UNDECLARED)
        return declared if declared in access.WORK_TYPES else access.UNDECLARED

    def _needs_credential(self, name: str) -> bool:
        source = self._sources.get(name)
        if source is None:
            return False
        try:
            request = source.request()
        except Exception:
            return False
        return bool(isinstance(request, dict)
                    and request.get("requires_credential"))

    def active_sources(self) -> list[dict]:
        """The sources an owner should see as live routes to paid work.

        Everything else stays registered and keeps its history -- a source is
        never deleted, because its determination, its permission grants and
        the opportunities it produced are the record of a decision somebody
        made. It is simply not presented as somewhere money might come from.
        """
        out = []
        for row in self.sources():
            verdict, why = self.source_suitability(row["name"])
            if verdict == "ACTIVE":
                out.append({**row, "suitability": verdict, "why": why})
        return out

    def source_report(self) -> list[dict]:
        """Every source with its verdict. For the audit, not the interface."""
        out = []
        for row in self.sources():
            verdict, why = self.source_suitability(row["name"])
            out.append({
                "name": row["name"], "kind": row.get("kind", ""),
                "work_type": self._work_type(row["name"]),
                "compliance": row.get("compliance", ""),
                "health": row.get("health", ""),
                "is_fixture": bool(int(row.get("is_fixture", 0))),
                "suitability": verdict, "why": why,
                "permissions": self.permissions(row["name"]),
            })
        return out

    def suspend_source(self, name: str, why: str) -> None:
        """Stop polling a source. Used when its terms or behaviour change.

        Suspension is automatic on any compliance ambiguity: the safe response to
        "we are not sure this is still allowed" is to stop.
        """
        self._db.execute(
            "UPDATE work_sources SET compliance = ?, healthy = 0, last_error = ? "
            "WHERE name = ?", (Compliance.SUSPENDED.value, why, name))
        self._db.commit()
        self._audit.record(
            event="discovery.source_suspended", authority="discovery",
            initiator="discovery", why=why, decision=name)

    # ---------------------------------------------------------- permissions

    def permissions(self, name: str) -> tuple[str, ...]:
        """What this source is currently permitted to do. Empty is normal."""
        state = self.source_state(name)
        if state is None:
            return ()
        return tuple(json.loads(state["permissions"] or "[]"))

    def may(self, name: str, permission: str) -> tuple[bool, str]:
        """Is one named action permitted on one named source?

        Fails closed on every unknown: an unregistered source, a disabled one,
        a health state that means it cannot be reached, or a permission nobody
        granted. "We are not sure" and "no" are the same answer here.
        """
        if permission not in access.PERMISSIONS:
            return False, f"{permission!r} is not a source permission"
        state = self.source_state(name)
        if state is None:
            return False, f"source {name!r} is not registered"
        if not int(state.get("enabled", 1)):
            return False, f"source {name!r} is disabled"
        granted = json.loads(state["permissions"] or "[]")
        if permission not in granted:
            return False, (
                f"{name!r} does not have {permission}; it has "
                + (", ".join(granted) if granted else "no permissions")
                + ". Discovering work on a source is not permission to act on it")
        allowed, finding = self._terms_allow(name, permission)
        if not allowed:
            return False, finding
        if permission in access.CONSEQUENTIAL:
            # A granted consequential permission is still not a licence to act
            # now: the Action Gate decides that, every time, and simulation
            # keeps it shut. Saying so here stops a caller reading a True from
            # this method as "go ahead".
            return True, (f"{name!r} has {permission}; the Action Gate still "
                          "decides each individual effect")
        return True, f"{name!r} has {permission}"

    def _terms_allow(self, name: str, permission: str) -> tuple[bool, str]:
        """Does the platform's own terms record allow this activity?

        Separate from the owner's grant, and checked as well as it rather
        than instead of it. These are different facts: the owner can decide
        DeskPilot should apply for work, and the platform can still forbid
        doing it automatically. The owner's intention does not change what
        the terms say.

        A source with no catalogue entry is not held to a standard nobody
        wrote down -- fixtures and owner-entered work have no external terms
        -- but a catalogued source with nothing read about this activity
        fails closed. "We have not checked" is not a reason to go ahead.
        """
        from . import sourcecatalog

        entry = sourcecatalog.entry(name)
        if entry is None:
            if permission not in sourcecatalog.GOVERNED_BY_TERMS:
                return True, ""
            return self._uncatalogued(name, permission)
        if permission not in sourcecatalog.GOVERNED_BY_TERMS:
            return True, ""
        # An in-process fixture named after a real marketplace is not that
        # marketplace, and holding it to Freelancer.com's terms of service
        # would be a category error. Not a loophole: a fixture can never be
        # an active source (`source_suitability` returns FIXTURE first), so
        # nothing registered this way is a live route to anything.
        state = self.source_state(name)
        if state is not None and int(state.get("is_fixture", 0)):
            return True, ""

        finding = entry.terms_for(permission)
        if finding.allows:
            return True, ""
        if finding.finding == sourcecatalog.TERMS_FORBIDDEN:
            return False, (
                f"{name!r} forbids {permission} in its terms "
                f"({finding.citation}, read {finding.read_on}); the owner's "
                "grant does not change what the platform allows")
        return False, (
            f"nobody has read whether {name!r} permits {permission}. An "
            "unread clause is not permission, and the owner must record a "
            "finding with a citation before this can be used")

    def grant(self, name: str, permission: str, *, owner_identity: str,
              why: str) -> None:
        """Give one source one more power. Owner only, one at a time.

        There is deliberately no "grant everything". Each consequential
        permission is a separate decision with its own recorded reason, because
        the difference between applying for work and spending money on applying
        for work is exactly the kind of difference a bulk grant erases.
        """
        if not self._policy.is_owner(owner_identity):
            raise FailClosed(
                f"source permissions are owner-granted (got {owner_identity!r})")
        if not why:
            raise FailClosed(
                f"granting {permission} on {name!r} needs a recorded reason")
        if self.source_state(name) is None:
            raise FailClosed(f"unknown source {name!r}")
        access.validate_permissions([permission])
        granted = list(self.permissions(name))
        if permission not in granted:
            granted.append(permission)
        self._db.execute("UPDATE work_sources SET permissions = ? WHERE name = ?",
                         (json.dumps(access.validate_permissions(granted)), name))
        self._db.commit()
        self._audit.record(
            event="discovery.permission_granted", authority="discovery",
            initiator=owner_identity, why=why, decision=f"{name}:{permission}",
            permission=permission)

    def revoke(self, name: str, permission: str, *, owner_identity: str,
               why: str) -> None:
        if not self._policy.is_owner(owner_identity):
            raise FailClosed(
                f"source permissions are owner-held (got {owner_identity!r})")
        granted = [p for p in self.permissions(name) if p != permission]
        self._db.execute("UPDATE work_sources SET permissions = ? WHERE name = ?",
                         (json.dumps(granted), name))
        self._db.commit()
        self._audit.record(
            event="discovery.permission_revoked", authority="discovery",
            initiator=owner_identity, why=why, decision=f"{name}:{permission}",
            permission=permission)

    def set_enabled(self, name: str, enabled: bool, *, owner_identity: str,
                    why: str) -> None:
        if not self._policy.is_owner(owner_identity):
            raise FailClosed(f"sources are owner-controlled (got {owner_identity!r})")
        self._db.execute(
            "UPDATE work_sources SET enabled = ?, health = ? WHERE name = ?",
            (1 if enabled else 0,
             access.HEALTHY if enabled else access.DISABLED, name))
        self._db.commit()
        self._audit.record(
            event="discovery.source_enabled" if enabled
            else "discovery.source_disabled",
            authority="discovery", initiator=owner_identity, why=why,
            decision=name)

    def set_credential_configured(self, name: str, configured: bool, *,
                                  owner_identity: str) -> None:
        """Record *that* a credential exists. Never what it is.

        The registry stores a boolean. The secret itself lives where secrets
        live, and nothing here can read it back -- so a compromised control
        centre learns only that a source has been set up.
        """
        if not self._policy.is_owner(owner_identity):
            raise FailClosed(f"credentials are owner-held (got {owner_identity!r})")
        self._db.execute(
            "UPDATE work_sources SET credential_configured = ? WHERE name = ?",
            (1 if configured else 0, name))
        self._db.commit()
        self._audit.record(
            event="discovery.credential_state_changed", authority="discovery",
            initiator=owner_identity,
            why="owner recorded that a credential is configured" if configured
                else "owner recorded that no credential is configured",
            decision=name)

    # -------------------------------------------------------------- health

    def record_health(self, name: str, health: str, *, reason: str = "") -> None:
        """Set a source's operational state, and keep the failure counters.

        An unreachable source is never represented as healthy (§29). A run of
        failures raises the count, which is what the circuit breaker reads --
        so a source that has stopped working is left alone rather than polled
        harder (§30, §41).
        """
        if health not in access.HEALTH_STATES:
            raise FailClosed(f"{health!r} is not a source health state")
        state = self.source_state(name)
        if state is None:
            raise FailClosed(f"unknown source {name!r}")
        stamp = now()
        if health == access.HEALTHY:
            self._db.execute(
                "UPDATE work_sources SET health = ?, last_checked = ?, "
                "last_success = ?, consecutive_failures = 0, failure_reason = '',"
                " healthy = 1 WHERE name = ?",
                (health, stamp, stamp, name))
        else:
            self._db.execute(
                "UPDATE work_sources SET health = ?, last_checked = ?, "
                "last_failure = ?, failure_reason = ?, "
                "consecutive_failures = consecutive_failures + 1, healthy = 0 "
                "WHERE name = ?",
                (health, stamp, stamp, reason, name))
        self._db.commit()

    # -------------------------------------------------------------- polling

    def _may_poll(self, source: WorkSource) -> tuple[bool, str]:
        state = self.source_state(source.name)
        if state is None:
            return False, f"source {source.name!r} is not registered"
        if not self._policy.get("discovery", "approved_sources", default=[]) \
                or source.name not in self._policy.get("discovery", "approved_sources",
                                                       default=[]):
            return False, f"source {source.name!r} is not approved in Policy"
        compliance = Compliance(state["compliance"])
        if not compliance.may_poll:
            return False, (f"source {source.name!r} compliance is "
                           f"{compliance.value}; only PERMITTED may be polled")
        if not int(state.get("enabled", 1)):
            return False, f"source {source.name!r} is disabled"
        try:
            # Belt and braces with the permission filter above: one stops the
            # permission being stored, this stops the call being made. They
            # fail for different reasons, so a mutation removing either one is
            # visible.
            intake.assert_may_discover(state)
        except FailClosed as refusal:
            return False, str(refusal)
        permitted, why = self.may(source.name, access.DISCOVER)
        if not permitted:
            return False, why
        # The platform's own machine-readable answer, which is a different
        # fact from the owner's compliance determination and from the
        # DISCOVER grant. Checked here as well as in `source_suitability`
        # because that one governs what the interface *shows*: without this
        # line a catalogued source whose robots.txt disallows automated
        # reading could still be polled, as long as somebody had marked it
        # PERMITTED. Public discovery is not in GOVERNED_BY_TERMS, so
        # `may` does not cover it. Found by the sealed exam.
        readable, readable_why = self._public_discovery_allowed(source.name)
        if not readable:
            return False, readable_why
        rested, rest_why = self._rate_limit_respected(source, state)
        if not rested:
            return False, rest_why
        # One question, one answer. ``health`` is the named state; ``healthy``
        # is the boolean that predates it and is still written by older paths
        # and older databases. Folding the boolean into the state here means
        # there is a single place that decides whether a source may be polled
        # -- when both existed as separate checks, either one alone was enough
        # to refuse, so neither was load-bearing and a mutation test could
        # remove one without any test noticing.
        health = state.get("health") or access.HEALTHY
        if health == access.HEALTHY and not int(state.get("healthy", 1)):
            health = access.FAILED
        if health in access.NOT_POLLABLE:
            # §29/§41. A source that cannot be reached is not polled again on
            # the same tick, and a rate-limited one is backed off rather than
            # retried harder.
            return False, (f"source {source.name!r} is {health}"
                           + (f": {state.get('failure_reason')}"
                              if state.get("failure_reason") else ""))
        return True, "permitted"

    def poll(self, source_name: str, *, initiator: str = "discovery") -> list[Opportunity]:
        """Fetch candidates from one approved source.

        A real source's fetch goes through the Action Gate like any other external
        read. If the Gate refuses, Discovery returns nothing — it has no fallback
        path, by design.
        """
        source = self._sources.get(source_name)
        if source is None:
            raise FailClosed(f"unknown source {source_name!r}")

        state = self.source_state(source.name) or {}
        allowed, why = self._may_poll(source)
        if not allowed:
            self._audit.record(
                event="discovery.poll_refused", authority="discovery",
                initiator=initiator, why=why, decision="REFUSED",
                external_effect=f"poll {source_name}")
            return []

        payload: object | None = None
        request = source.request()
        if request is not None:
            if self._gate is None:
                raise FailClosed(
                    f"source {source_name!r} needs an external fetch but Discovery "
                    "has no Action Gate; there is no path around it")
            # Stamped before the request, not after. A fetch that raises, or
            # one the Gate simulates, still counted against the platform's
            # limit the moment the decision to call was made -- and recording
            # it only on success would let a failing source be retried in a
            # tight loop, which is the exact behaviour a rate limit exists to
            # prevent.
            self._record_poll(source_name)
            result = self._gate.request(
                action_class=ActionClass.C1_EXTERNAL_READ, destination=source.host,
                privacy=PrivacyClass.PUBLIC, purpose=f"poll {source_name} for work",
                initiator=initiator)
            if not result.allowed:
                self._audit.record(
                    event="discovery.poll_refused", authority="discovery",
                    initiator=initiator, why=result.reason, decision="REFUSED",
                    external_effect=f"poll {source_name}")
                return []
            payload = result.response

        # A source does not get to say how much its own content is trusted.
        # Discovery decides, from its own registration record: only a source
        # registered as "manual" *and* making no external call is owner-entered.
        # Otherwise a remote adapter could mark attacker-authored postings as
        # owner-confirmed and walk straight past the injection defence.
        # §4. One place decides what the provenance says. A remote adapter
        # cannot nominate its own role, and an inserted source that somehow
        # issued a request is not owner-confirmed on the strength of its kind.
        stamp = intake.provenance(state)
        owner_entered = stamp["owner_entered"] and request is None
        try:
            parsed = [replace(o, owner_entered=owner_entered)
                      for o in source.parse(payload)]
        except Exception as exc:
            # §42. A source whose shape changed is not reinterpreted, guessed
            # at, or partially accepted. It fails closed and says why.
            #
            # The "why" is redacted here, at the call site, rather than inside
            # `record` -- redacting every result globally once ate the
            # artifact digests, which are hex and look exactly like a leaked
            # key. An exception raised while reading an HTTP response carries
            # a slice of that response, and an API that echoes the request
            # back in its error body puts a credential in it. Both
            # destinations matter: `failure_reason` is rendered by the
            # Control Centre, and the audit log is append-only and so cannot
            # be redacted afterwards.
            detail = redact(f"{type(exc).__name__} while parsing: {exc}")
            self.record_health(source_name, access.FAILED, reason=detail)
            self._audit.record(
                event="discovery.parse_failed", authority="discovery",
                initiator=initiator,
                why=f"{source_name} returned something this adapter cannot read",
                decision="REFUSED", result=detail)
            return []

        found, duplicates = self._deduplicate(source_name, parsed)
        for opportunity in found:
            self._record(opportunity)
        self.record_health(source_name, access.HEALTHY)
        if found:
            self._db.execute(
                "UPDATE work_sources SET opportunities_found = "
                "opportunities_found + ? WHERE name = ?",
                (len(found), source_name))
            self._db.commit()
        self._audit.record(
            event="discovery.polled", authority="discovery", initiator=initiator,
            why=f"polled {source_name}",
            result=f"{len(found)} new candidate(s), {duplicates} already seen",
            external_effect=f"poll {source_name}", is_fixture=source.is_fixture)
        return found

    def insert(self, source_name: str, *, owner_identity: str,
               method: str = intake.OWNER_ENTERED,
               postings: list | None = None) -> list[Opportunity]:
        """The **Inserted Work** entry point. A separate door on purpose.

        Work the owner brings does not arrive by polling. It is typed in or
        uploaded, there is no adapter fetch, no Action Gate request and no
        network, and this method physically has no path to any of them -- which
        is the difference §4 asks for, expressed as two methods rather than as
        a flag inside one.

        It refuses a found-role source outright. Owner-confirmed terms may gate
        a commitment where a board posting may not, so a source that reaches
        the network must never be able to record work under this provenance.
        """
        if not self._policy.is_owner(owner_identity):
            raise FailClosed(
                f"inserting work is an owner action (got {owner_identity!r}); "
                "inserted terms are owner-confirmed and only the owner can "
                "confirm them")
        source = self._sources.get(source_name)
        if source is None:
            raise FailClosed(f"unknown source {source_name!r}")
        state = self.source_state(source_name)
        if state is None:
            raise FailClosed(f"source {source_name!r} is not registered")
        # The role check, from the one place that decides roles.
        intake.assert_may_insert(state)

        if postings is not None:
            source = ManualSource(source_name, list(postings))
        # A source registered as manual but declaring an external fetch is
        # misregistered, and the combination is exactly how attacker-authored
        # text would arrive wearing owner-confirmed provenance. Refused rather
        # than quietly downgraded: the registration is wrong and should be
        # fixed, not worked around.
        if source.request() is not None:
            raise FailClosed(
                f"source {source_name!r} is registered for inserted work but "
                "declares an external fetch; inserted work is typed in, and a "
                "source that reaches the network may not record owner-"
                "confirmed terms")
        parsed = [replace(o, owner_entered=True) for o in source.parse(None)]
        found, duplicates = self._deduplicate(source_name, parsed)
        for opportunity in found:
            self._record(opportunity, stamp=intake.provenance(state,
                                                              method=method))
        if found:
            self._db.execute(
                "UPDATE work_sources SET opportunities_found = "
                "opportunities_found + ? WHERE name = ?",
                (len(found), source_name))
            self._db.commit()
        self._audit.record(
            event="discovery.inserted", authority="discovery",
            initiator=owner_identity,
            why=f"owner inserted work via {method}",
            result=f"{len(found)} new, {duplicates} already seen",
            decision=intake.INSERTED)
        return found

    def _deduplicate(self, source_name: str,
                     found: list[Opportunity]) -> tuple[list[Opportunity], int]:
        """Drop postings already seen from this source.

        Boards repeat. Without this, polling twice would produce two candidates
        for one posting, two jobs, and potentially two commitments to the same
        client for the same work — which is a business failure, not a tidiness
        problem. A source with no stable reference gets a content key instead, so
        deduplication does not depend on the platform being well behaved.
        """
        seen = {r["external_ref"] for r in self._db.query(
            "SELECT external_ref FROM opportunities WHERE source = ?", (source_name,))}
        fresh: list[Opportunity] = []
        duplicates = 0
        for opportunity in found:
            key = opportunity.external_ref or _content_key(opportunity)
            if key in seen:
                duplicates += 1
                continue
            seen.add(key)
            fresh.append(opportunity if opportunity.external_ref
                         else replace(opportunity, external_ref=key))
        return fresh, duplicates

    def poll_all(self, *, initiator: str = "discovery") -> list[Opportunity]:
        found: list[Opportunity] = []
        for name in sorted(self._sources):
            found.extend(self.poll(name, initiator=initiator))
        return found

    # ------------------------------------------------------------- recording

    def _record(self, opportunity: Opportunity, *,
                stamp: dict | None = None) -> None:
        """Store a candidate, with the evidence that it came from somewhere.

        §49/§50: nothing here invents. ``client_ref`` stays empty when the
        source did not supply one rather than being inferred from the text, and
        ``evidence_ref`` points at the quarantined posting, so every normalised
        field can be traced back to what was actually observed.
        """
        screening = risk.screen(opportunity)
        self._db.execute(
            "INSERT OR REPLACE INTO opportunities(id,ts,source,external_ref,title,"
            "quoted_cents,deadline,client_ref,needs,signals,raw_ref,status,"
            "external_url,risk_worst,risk_signals,discovered_via,evidence_ref,"
            "platform_fee_cents,upfront_cost_cents) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (opportunity.id, now(), opportunity.source, opportunity.external_ref,
             opportunity.title, opportunity.quoted_cents, opportunity.deadline,
             opportunity.client_ref, json.dumps(list(opportunity.needs)),
             json.dumps(_signals(opportunity.signals)),
             opportunity.posting.id if opportunity.posting else "",
             OpportunityStatus.DISCOVERED.value,
             opportunity.external_url,
             screening.worst if screening.signals else "",
             json.dumps([s.to_dict() for s in screening.signals]),
             (stamp or {}).get("discovered_via",
                               "owner" if opportunity.owner_entered
                               else opportunity.source),
             opportunity.posting.id if opportunity.posting else "",
             opportunity.platform_fee_cents, opportunity.upfront_cost_cents))
        self._db.commit()
        if screening.severe:
            self._audit.record(
                event="discovery.risk_flagged", authority="discovery",
                initiator="discovery",
                why=f"{len(screening.severe)} severe signal(s) on "
                    f"{opportunity.id}",
                decision=",".join(s.code for s in screening.severe),
                result="recorded as evidence; no decision taken here")

    def set_status(self, opportunity_id: str, status: OpportunityStatus,
                   job_id: str | None = None) -> None:
        self._db.execute(
            "UPDATE opportunities SET status = ?, job_id = COALESCE(?, job_id) "
            "WHERE id = ?", (status.value, job_id, opportunity_id))
        self._db.commit()

    def capability_gaps(self, *, capability=None, min_occurrences: int = 3
                        ) -> list[dict]:
        """Skills that opportunities keep asking for and DeskPilot cannot do.

        §55. Evidence for the recommendation pathway, and nothing more. This
        cannot create a skill, propose one, promote one or change what
        DeskPilot claims it can do -- the Skills Lab and the owner keep every
        one of those decisions. All it says is "this was asked for N times".

        Deliberately conservative in two ways. It reports *advertised* budget,
        labelled as such, because what a listing says it pays is not profit and
        this module has no business implying otherwise (§51). And it requires a
        run of occurrences, because one unusual request is not a gap in the
        business -- it is one unusual request.
        """
        registry = capability if capability is not None else getattr(
            self, "_capability", None)
        proven: set[str] = set()
        if registry is not None:
            proven = {c.name for c in registry.capabilities() if c.proven}

        counts: dict[str, dict] = {}
        for row in self.opportunities():
            try:
                needs = json.loads(row.get("needs") or "[]")
            except ValueError:
                continue
            for need in needs:
                if need in proven:
                    continue
                entry = counts.setdefault(need, {
                    "need": need, "occurrences": 0,
                    "advertised_cents": 0, "sources": set(),
                    "examples": []})
                entry["occurrences"] += 1
                entry["advertised_cents"] += int(row.get("quoted_cents") or 0)
                entry["sources"].add(row.get("source", ""))
                if len(entry["examples"]) < 3:
                    entry["examples"].append(row.get("title", "")[:70])

        out = []
        for entry in counts.values():
            if entry["occurrences"] < min_occurrences:
                continue
            entry["sources"] = sorted(s for s in entry["sources"] if s)
            #: Named so nobody reads it as profit. It is what postings claimed.
            entry["advertised_only"] = True
            out.append(entry)
        return sorted(out, key=lambda e: (-e["occurrences"], e["need"]))

    def opportunities(self, status: OpportunityStatus | None = None) -> list[dict]:
        sql = "SELECT * FROM opportunities"
        params: tuple = ()
        if status is not None:
            sql += " WHERE status = ?"
            params = (status.value,)
        return [dict(r) for r in self._db.query(sql +
            " ORDER BY ts DESC, rowid DESC", params)]

    def counts(self) -> dict:
        rows = self._db.query(
            "SELECT status, COUNT(*) AS n FROM opportunities GROUP BY status")
        return {r["status"]: int(r["n"]) for r in rows}


def _signals(signals: LocationSignals) -> dict:
    out = {}
    for name in ("project", "client", "worker", "material_destination",
                 "contract_clause"):
        value = getattr(signals, name, None)
        if value:
            out[name] = value.path()
    return out


def _content_key(opportunity: Opportunity) -> str:
    """A stable key for a source that supplies no reference of its own."""
    digest = hashlib.sha256(
        f"{opportunity.source}|{opportunity.title}|{opportunity.quoted_cents}".encode()
    ).hexdigest()[:16]
    return f"content:{digest}"
