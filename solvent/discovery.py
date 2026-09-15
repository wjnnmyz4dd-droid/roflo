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


class Discovery:
    """The candidate-sourcing authority. It finds work; it never takes it."""

    def __init__(self, store: Store, audit: AuditLog, policy, gate=None) -> None:
        self._db = store.for_authority("discovery")
        self._audit = audit
        self._policy = policy
        self._gate = gate
        self._sources: dict[str, WorkSource] = {}

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
        self._sources[source.name] = source
        self._db.execute(
            "INSERT OR REPLACE INTO work_sources(name,kind,readiness,compliance,"
            "determination,determined_by,determined_at,is_fixture,healthy) "
            "VALUES(?,?,?,?,?,?,?,?,1)",
            (source.name, source.kind, readiness.value, compliance.value,
             determination, owner_identity, now(), 1 if source.is_fixture else 0))
        self._db.commit()
        self._audit.record(
            event="discovery.source_registered", authority="discovery",
            initiator=owner_identity, why=determination, decision=source.name,
            readiness=readiness.value, compliance=compliance.value,
            is_fixture=source.is_fixture)

    def source_state(self, name: str) -> dict | None:
        row = self._db.query_one("SELECT * FROM work_sources WHERE name = ?", (name,))
        return dict(row) if row else None

    def sources(self) -> list[dict]:
        return [dict(r) for r in self._db.query(
            "SELECT * FROM work_sources ORDER BY name")]

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
        if not int(state["healthy"]):
            return False, f"source {source.name!r} is marked unhealthy"
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
        owner_entered = state["kind"] == "manual" and request is None
        parsed = [replace(o, owner_entered=owner_entered)
                  for o in source.parse(payload)]
        found, duplicates = self._deduplicate(source_name, parsed)
        for opportunity in found:
            self._record(opportunity)
        self._audit.record(
            event="discovery.polled", authority="discovery", initiator=initiator,
            why=f"polled {source_name}",
            result=f"{len(found)} new candidate(s), {duplicates} already seen",
            external_effect=f"poll {source_name}", is_fixture=source.is_fixture)
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

    def _record(self, opportunity: Opportunity) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO opportunities(id,ts,source,external_ref,title,"
            "quoted_cents,deadline,client_ref,needs,signals,raw_ref,status) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (opportunity.id, now(), opportunity.source, opportunity.external_ref,
             opportunity.title, opportunity.quoted_cents, opportunity.deadline,
             opportunity.client_ref, json.dumps(list(opportunity.needs)),
             json.dumps(_signals(opportunity.signals)),
             opportunity.posting.id if opportunity.posting else "",
             OpportunityStatus.DISCOVERED.value))
        self._db.commit()

    def set_status(self, opportunity_id: str, status: OpportunityStatus,
                   job_id: str | None = None) -> None:
        self._db.execute(
            "UPDATE opportunities SET status = ?, job_id = COALESCE(?, job_id) "
            "WHERE id = ?", (status.value, job_id, opportunity_id))
        self._db.commit()

    def opportunities(self, status: OpportunityStatus | None = None) -> list[dict]:
        sql = "SELECT * FROM opportunities"
        params: tuple = ()
        if status is not None:
            sql += " WHERE status = ?"
            params = (status.value,)
        return [dict(r) for r in self._db.query(sql + " ORDER BY ts DESC", params)]

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
