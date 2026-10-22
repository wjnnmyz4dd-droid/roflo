"""Who we are talking to, what we know, and how we know it.

A prospect record is mostly a discipline about belief. Everything here
separates three things that a CRM usually blurs into one column:

* **What the company published.** A filing, a careers page, a posted
  job. Checkable by anyone.
* **What someone told us.** A client assertion. Often true, never
  evidence of itself.
* **What DeskPilot inferred.** A hypothesis. "They advertised for a
  data-entry clerk twice this year" is an observation; "they are
  drowning in manual data entry" is a guess, and the second must never
  be filed as the first.

:class:`Evidence` makes the distinction structural, and
:attr:`Prospect.verified_need` is false until something checkable says
otherwise. Phase 3's instruction -- "distinguish verified business
needs from hypotheses, do not invent company problems" -- is not a
style note; a pipeline full of invented problems reads exactly like a
pipeline full of real ones.

Contacts are held to a stricter rule still. A contact with no source
is refused outright: there is no legitimate way to hold a business
email address whose origin nobody recorded, and the US rules on
commercial email begin with being able to say where you got it.

This module is **not** a second Finder and decides nothing about
money. It records. :mod:`solvent.qualification` triages, the Governor
prices, and the owner approves.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from enum import Enum

from .errors import FailClosed

#: Phase 3's five, pursued concurrently. A sixth would be a decision.
INDUSTRIES = ("construction", "real-estate", "accounting",
              "property-management", "general-small-business")

#: The memory kind these records live under.
_KIND = "crm_prospect"

#: Two letters. Not validated against a list of real states here --
#: that belongs with jurisdiction handling -- but a free-text country
#: name in this field would quietly break every geographic comparison.
_STATE = re.compile(r"^[A-Z]{2}$")


class Standing(Enum):
    """Where a relationship has got to. One direction, mostly.

    ``DISQUALIFIED`` is deliberately terminal and carries a reason: a
    prospect refused for a good reason and then rediscovered next week
    is how an outreach system becomes a nuisance.
    """

    DISCOVERED = "DISCOVERED"
    RESEARCHED = "RESEARCHED"
    QUALIFIED = "QUALIFIED"
    DISQUALIFIED = "DISQUALIFIED"
    CONTACTED = "CONTACTED"
    REPLIED = "REPLIED"
    NEGOTIATING = "NEGOTIATING"
    PROPOSED = "PROPOSED"
    CONTRACTED = "CONTRACTED"
    DELIVERED = "DELIVERED"
    PAID = "PAID"
    REPEAT = "REPEAT"
    #: They asked not to be contacted. Terminal, and honoured for ever.
    OPTED_OUT = "OPTED_OUT"

    @property
    def terminal(self) -> bool:
        return self in (Standing.DISQUALIFIED, Standing.OPTED_OUT)


class Basis(Enum):
    """How much weight a piece of evidence will bear."""

    #: The company published it. Checkable by anyone.
    PUBLISHED = "PUBLISHED"
    #: They said it. True or not, it is an assertion.
    CLIENT_STATED = "CLIENT_STATED"
    #: We worked it out. A guess with reasoning is still a guess.
    INFERRED = "INFERRED"


@dataclass(frozen=True, slots=True)
class Evidence:
    """One thing we believe, and why.

    ``source`` is required even for an inference, where it names the
    reasoning rather than a URL. An inference with no stated basis is
    indistinguishable from an invention.
    """

    claim: str
    basis: Basis
    source: str
    seen_on: str

    def __post_init__(self) -> None:
        if not self.claim.strip():
            raise FailClosed("evidence with no claim says nothing")
        if not self.source.strip():
            raise FailClosed(
                f"evidence {self.claim[:40]!r} names no source; an "
                "inference with no stated basis is an invention")
        if not self.seen_on.strip():
            raise FailClosed("evidence with no date cannot go stale")

    @property
    def checkable(self) -> bool:
        return self.basis is Basis.PUBLISHED


@dataclass(frozen=True, slots=True)
class Contact:
    """A way to reach someone, and where it came from.

    Refusing a contact with no provenance is the whole value of this
    class. The US commercial-email rules start with knowing how you
    obtained an address, and "it was in the CRM" is not an answer.
    """

    name: str
    email: str
    role: str
    source: str
    seen_on: str

    def __post_init__(self) -> None:
        if not self.source.strip():
            raise FailClosed(
                f"contact {self.email!r} has no recorded source; there is no "
                "legitimate way to hold a business address whose origin "
                "nobody wrote down")
        if "@" not in self.email:
            raise FailClosed(f"{self.email!r} is not an address")


@dataclass
class Prospect:
    """One company, everything known about it, and what is only guessed."""

    prospect_id: str
    company: str
    industry: str
    state: str
    standing: Standing = Standing.DISCOVERED
    evidence: list[Evidence] = field(default_factory=list)
    contacts: list[Contact] = field(default_factory=list)
    #: Set only by :meth:`CRM.disqualify`, and it has to say why.
    disqualified_because: str = ""
    history: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.industry not in INDUSTRIES:
            raise FailClosed(
                f"{self.industry!r} is not one of the five target "
                f"industries: {', '.join(INDUSTRIES)}")
        if not _STATE.match(self.state):
            raise FailClosed(
                f"{self.state!r} is not a two-letter US state; the target "
                "market is the United States and a free-text region here "
                "breaks every geographic comparison")
        if not self.company.strip():
            raise FailClosed("a prospect with no company name is a blank row")

    @property
    def verified_need(self) -> bool:
        """True only on published evidence. An inference is not a need."""
        return any(e.checkable for e in self.evidence)

    @property
    def hypotheses(self) -> list[Evidence]:
        return [e for e in self.evidence if e.basis is Basis.INFERRED]

    @property
    def contactable(self) -> bool:
        return bool(self.contacts) and not self.standing.terminal

    def summary(self) -> dict:
        return {
            "prospect_id": self.prospect_id, "company": self.company,
            "industry": self.industry, "state": self.state,
            "standing": self.standing.value,
            "verified_need": self.verified_need,
            "evidence": len(self.evidence),
            "published_evidence": sum(1 for e in self.evidence if e.checkable),
            "hypotheses": len(self.hypotheses),
            "contacts": len(self.contacts),
            "contactable": self.contactable,
            "disqualified_because": self.disqualified_because,
        }


def fingerprint(company: str, state: str) -> str:
    """What makes two records the same company.

    Normalised hard, because "Acme Builders, LLC" and "ACME Builders
    LLC" arriving a week apart is the ordinary case, and contacting
    one company twice under two records is the ordinary consequence.
    """
    text = re.sub(r"[^a-z0-9]+", "", company.lower())
    for suffix in ("llc", "inc", "incorporated", "corp", "corporation",
                   "ltd", "limited", "co", "company", "lp", "llp", "pllc"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
            break
    return hashlib.sha256(f"{text}|{state.upper()}".encode()).hexdigest()[:16]


class CRM:
    """Durable prospect records. Decides nothing; remembers everything.

    Backed by the append-only store when one is supplied, so records
    survive a restart; in memory otherwise, and :attr:`durable` says
    which rather than leaving a caller to assume.
    """

    def __init__(self, memory=None, audit=None) -> None:
        #: :class:`solvent.memory.BusinessMemory`, not a table of our
        #: own. ``memory_facts`` already exists, is owned by the memory
        #: authority, and the store refuses a write from anybody else --
        #: which is how the first version of this class was caught
        #: inventing a ``business_memory`` table nobody owned.
        self._memory = memory
        self._audit = audit
        self._prospects: dict[str, Prospect] = {}
        self._by_fingerprint: dict[str, str] = {}
        if self._memory is not None:
            self._rehydrate()

    @property
    def durable(self) -> bool:
        return self._memory is not None

    def _rehydrate(self) -> None:
        """Rebuild each prospect from its latest recorded state.

        ``learn`` appends, so a prospect that changed five times has
        five rows and only the last is current. Two things make picking
        the last one less obvious than it looks.

        ``recall`` returns newest first, not oldest first. The first
        version of this iterated in the order given and let each row
        overwrite the one before, which restored the *oldest* state:
        a prospect came back from a restart as freshly DISCOVERED with
        no evidence and no contacts, silently undoing everything
        learned about them.

        And ``ts`` can tie. Several writes inside the same instant sort
        arbitrarily against each other, so ordering alone cannot decide
        which is current. The record's own history length can: each
        change appends exactly one entry, so the longest history is the
        latest state regardless of how the timestamps sorted.
        """
        try:
            facts = self._memory.recall(kind=_KIND)
        except Exception:
            return
        latest: dict[str, dict] = {}
        for fact in facts:
            payload = fact.get("payload")
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except ValueError:
                    continue
            if not isinstance(payload, dict) or not payload.get("prospect_id"):
                continue
            known = latest.get(payload["prospect_id"])
            if known is None or len(payload.get("history", [])) > len(
                    known.get("history", [])):
                latest[payload["prospect_id"]] = payload
        for payload in latest.values():
            try:
                self._restore(payload)
            except (ValueError, KeyError, FailClosed):
                continue

    def _restore(self, doc: dict) -> None:
        prospect = Prospect(
            prospect_id=doc["prospect_id"], company=doc["company"],
            industry=doc["industry"], state=doc["state"],
            standing=Standing(doc.get("standing", "DISCOVERED")),
            disqualified_because=doc.get("disqualified_because", ""),
            history=list(doc.get("history", [])))
        prospect.evidence = [Evidence(e["claim"], Basis(e["basis"]),
                                      e["source"], e["seen_on"])
                             for e in doc.get("evidence", [])]
        prospect.contacts = [Contact(c["name"], c["email"], c["role"],
                                     c["source"], c["seen_on"])
                             for c in doc.get("contacts", [])]
        self._prospects[prospect.prospect_id] = prospect
        self._by_fingerprint[fingerprint(prospect.company,
                                         prospect.state)] = prospect.prospect_id

    def _persist(self, prospect: Prospect) -> None:
        if self._memory is None:
            return
        doc = {
            "prospect_id": prospect.prospect_id, "company": prospect.company,
            "industry": prospect.industry, "state": prospect.state,
            "standing": prospect.standing.value,
            "disqualified_because": prospect.disqualified_because,
            "history": prospect.history,
            "evidence": [{"claim": e.claim, "basis": e.basis.value,
                          "source": e.source, "seen_on": e.seen_on}
                         for e in prospect.evidence],
            "contacts": [{"name": c.name, "email": c.email, "role": c.role,
                          "source": c.source, "seen_on": c.seen_on}
                         for c in prospect.contacts],
        }
        from .types import VerificationTier

        # T1: this is a deterministic record of what was recorded, not a
        # claim about the client's business. The evidence inside it
        # carries its own basis, which is the distinction that matters.
        self._memory.learn(
            kind=_KIND, subject=prospect.prospect_id, payload=doc,
            evidence_ref=f"crm:{prospect.prospect_id}",
            tier=VerificationTier.T1_DETERMINISTIC)

    # ---- recording ----------------------------------------------------

    def discover(self, *, prospect_id: str, company: str, industry: str,
                 state: str) -> Prospect:
        """Record a company, or hand back the one already held.

        Never raises on a duplicate. A discovery run that crashes
        because it saw a company twice is a discovery run that cannot
        be re-run, and re-running it is the ordinary recovery.
        """
        mark = fingerprint(company, state)
        existing = self._by_fingerprint.get(mark)
        if existing is not None:
            return self._prospects[existing]
        prospect = Prospect(prospect_id=prospect_id, company=company,
                            industry=industry, state=state)
        self._prospects[prospect_id] = prospect
        self._by_fingerprint[mark] = prospect_id
        self._note(prospect, "discovered", company)
        return prospect

    def add_evidence(self, prospect_id: str, evidence: Evidence) -> Prospect:
        prospect = self.get(prospect_id)
        if prospect.standing is Standing.OPTED_OUT:
            raise FailClosed(
                f"{prospect.company} opted out; researching them further is "
                "not something an opt-out permits")
        prospect.evidence.append(evidence)
        if prospect.standing is Standing.DISCOVERED:
            prospect.standing = Standing.RESEARCHED
        self._note(prospect, "evidence", f"{evidence.basis.value}: "
                                         f"{evidence.claim[:60]}")
        return prospect

    def add_contact(self, prospect_id: str, contact: Contact) -> Prospect:
        prospect = self.get(prospect_id)
        if prospect.standing is Standing.OPTED_OUT:
            raise FailClosed(
                f"{prospect.company} opted out; holding a new contact for "
                "them is not something an opt-out permits")
        if any(c.email.lower() == contact.email.lower()
               for c in prospect.contacts):
            return prospect
        prospect.contacts.append(contact)
        self._note(prospect, "contact", contact.email)
        return prospect

    def advance(self, prospect_id: str, standing: Standing, why: str = "") -> Prospect:
        prospect = self.get(prospect_id)
        if prospect.standing.terminal:
            raise FailClosed(
                f"{prospect.company} is {prospect.standing.value}, which is "
                f"terminal; moving them to {standing.value} would undo a "
                "decision that was made for a reason")
        prospect.standing = standing
        self._note(prospect, "standing", f"{standing.value}: {why}")
        return prospect

    def disqualify(self, prospect_id: str, why: str) -> Prospect:
        if not why.strip():
            raise FailClosed(
                "disqualifying without a reason means the next discovery "
                "run finds them again and nobody knows why not to")
        prospect = self.get(prospect_id)
        prospect.standing = Standing.DISQUALIFIED
        prospect.disqualified_because = why
        self._note(prospect, "disqualified", why)
        return prospect

    def opt_out(self, prospect_id: str, why: str = "they asked") -> Prospect:
        """Honoured for ever, and not reversible from here."""
        prospect = self.get(prospect_id)
        prospect.standing = Standing.OPTED_OUT
        prospect.disqualified_because = why
        self._note(prospect, "opted_out", why)
        return prospect

    # ---- reading -------------------------------------------------------

    def get(self, prospect_id: str) -> Prospect:
        try:
            return self._prospects[prospect_id]
        except KeyError:
            raise FailClosed(f"no prospect {prospect_id!r}") from None

    def all(self) -> list[Prospect]:
        return list(self._prospects.values())

    def by_industry(self, industry: str) -> list[Prospect]:
        return [p for p in self._prospects.values() if p.industry == industry]

    def contactable(self) -> list[Prospect]:
        """Who may be written to: has a contact, and has not said no."""
        return [p for p in self._prospects.values() if p.contactable]

    def counts(self) -> dict:
        tally = {s.value: 0 for s in Standing}
        for prospect in self._prospects.values():
            tally[prospect.standing.value] += 1
        return {
            "prospects": len(self._prospects),
            "with_a_verified_need": sum(1 for p in self._prospects.values()
                                        if p.verified_need),
            "contactable": len(self.contactable()),
            "by_standing": tally,
            "by_industry": {i: len(self.by_industry(i)) for i in INDUSTRIES},
        }

    def _note(self, prospect: Prospect, what: str, detail: str) -> None:
        prospect.history.append({"at": _now(), "what": what, "detail": detail})
        self._persist(prospect)
        if self._audit is not None:
            try:
                self._audit.record(
                    event=f"crm.{what}", authority="orchestrator",
                    initiator="chief-of-staff", why=detail[:200],
                    decision="RECORDED", result=prospect.prospect_id)
            except Exception:
                pass


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
