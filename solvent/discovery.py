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
from . import opportunityrisk as risk
from . import sourceaccess as access
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
        except Exception:  # noqa: BLE001 - a fresh database has no table yet
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
        self._db.execute(
            "INSERT OR REPLACE INTO work_sources(name,kind,readiness,compliance,"
            "determination,determined_by,determined_at,is_fixture,healthy,"
            "display_name,source_type,enabled,access_method,auth_required,"
            "manual_import,base_url,health,permissions,credential_configured,"
            "rate_limit_note,terms_notes,opportunities_found) "
            "VALUES(?,?,?,?,?,?,?,?,1,?,?,?,?,?,?,?,?,?,?,?,?,?)",
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
             getattr(source, "terms_notes", ""),
             int(existing.get("opportunities_found", 0))))
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
        if permission in access.CONSEQUENTIAL:
            # A granted consequential permission is still not a licence to act
            # now: the Action Gate decides that, every time, and simulation
            # keeps it shut. Saying so here stops a caller reading a True from
            # this method as "go ahead".
            return True, (f"{name!r} has {permission}; the Action Gate still "
                          "decides each individual effect")
        return True, f"{name!r} has {permission}"

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
        permitted, why = self.may(source.name, access.DISCOVER)
        if not permitted:
            return False, why
        health = state.get("health") or access.HEALTHY
        if health in access.NOT_POLLABLE:
            # §29/§41. A source that cannot be reached is not polled again on
            # the same tick, and a rate-limited one is backed off rather than
            # retried harder.
            return False, (f"source {source.name!r} is {health}"
                           + (f": {state.get('failure_reason')}"
                              if state.get("failure_reason") else ""))
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
        try:
            parsed = [replace(o, owner_entered=owner_entered)
                      for o in source.parse(payload)]
        except Exception as exc:
            # §42. A source whose shape changed is not reinterpreted, guessed
            # at, or partially accepted. It fails closed and says why.
            self.record_health(source_name, access.FAILED,
                               reason=f"{type(exc).__name__} while parsing: {exc}")
            self._audit.record(
                event="discovery.parse_failed", authority="discovery",
                initiator=initiator,
                why=f"{source_name} returned something this adapter cannot read",
                decision="REFUSED", result=f"{type(exc).__name__}: {exc}")
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
             "owner" if opportunity.owner_entered else opportunity.source,
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
