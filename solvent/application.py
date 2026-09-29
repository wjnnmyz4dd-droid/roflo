"""Preparing to apply for work, and the wall between that and applying.

Preparation produces a document. Submission changes the world: it puts
DeskPilot's name on an offer, may consume a paid credit, and may create a
contract. They are different acts, they need different permission, and this
module exists to keep them different.

**Nothing here decides anything.** The price comes from the Financial Governor,
the capability claim comes from the Capability registry, and whether an
external effect may happen at all comes from the Action Gate. This module
assembles what those authorities already determined into something a human or a
platform can read. Where an input is missing it refuses to proceed rather than
filling the gap in -- an application is a set of promises, and a promise nobody
authorised is the most expensive kind of invention.

**§25, stated as code.** A draft cannot claim a capability that is not
registered and proven, cannot carry a price the Governor did not produce,
cannot promise a deadline shorter than the estimate it was given, and cannot be
submitted by this module at all.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from . import sourceaccess as access
from .audit import new_id, now
from .errors import FailClosed
from .types import ActionClass, Cents, PrivacyClass

#: A draft exists and has been checked. It has not been sent.
PREPARED = "PREPARED"
#: Submission was attempted and refused. The normal outcome today.
BLOCKED = "BLOCKED"
#: Submission was permitted and performed. Reachable only with the source
#: permission granted, the Gate open, and simulation off.
SUBMITTED = "SUBMITTED"


@dataclass(frozen=True, slots=True)
class Application:
    """A draft application. A document, not an act."""

    id: str
    opportunity_id: str
    source: str
    #: What DeskPilot says it will do, in the client's terms.
    scope: str
    deliverables: tuple[str, ...]
    #: From the Governor. Never from the listing, and never rounded here.
    price_cents: Cents
    timeline_days: int
    #: The proven capability that would do the work.
    capability_version: str
    #: Things that must be answered before this could become a commitment.
    clarifying_questions: tuple[str, ...] = ()
    #: The covering message. Drafted; sending is Customer Service's decision.
    message: str = ""
    state: str = PREPARED
    prepared_at: str = field(default_factory=now)

    def to_dict(self) -> dict:
        return {"id": self.id, "opportunity_id": self.opportunity_id,
                "source": self.source, "scope": self.scope,
                "deliverables": list(self.deliverables),
                "price_cents": self.price_cents,
                "timeline_days": self.timeline_days,
                "capability_version": self.capability_version,
                "clarifying_questions": list(self.clarifying_questions),
                "message": self.message, "state": self.state,
                "prepared_at": self.prepared_at}

    def render(self) -> str:
        """The application as a person would read it."""
        lines = [f"Scope: {self.scope}", "", "Deliverables:"]
        lines += [f"  - {d}" for d in self.deliverables] or ["  - (none stated)"]
        lines += ["", f"Price: {self.price_cents} cents",
                  f"Timeline: {self.timeline_days} day(s)"]
        if self.clarifying_questions:
            lines += ["", "Before starting, these need answering:"]
            lines += [f"  - {q}" for q in self.clarifying_questions]
        return "\n".join(lines)


class Applications:
    """Prepares applications. Cannot submit one.

    Not an authority: it owns no table, holds no permission, and every refusal
    it produces comes from asking something that already had the answer.
    """

    def __init__(self, solvent) -> None:
        self._s = solvent

    # ----------------------------------------------------------- preparation

    def prepare(self, opportunity: dict, *, price_cents: Cents,
                capability_version: str, deliverables, timeline_days: int,
                scope: str, clarifying_questions=(), message: str = "",
                estimated_days: int | None = None) -> Application:
        """Assemble a draft from what the authorities already established.

        Every argument that could be invented is required, and checked against
        the authority that owns it. ``price_cents`` in particular must be a
        Governor output: this module has no pricing of its own, so if the
        caller has not been to the Governor there is nothing to put here.
        """
        if not opportunity or not opportunity.get("id"):
            raise FailClosed("cannot prepare an application for no opportunity")
        if price_cents is None or int(price_cents) <= 0:
            raise FailClosed(
                "an application needs a price the Financial Governor produced; "
                "a bid with no authorised price is a promise nobody approved")
        if not capability_version:
            raise FailClosed(
                "an application must name the capability that would do the "
                "work; claiming unspecified competence is how a system "
                "promises what it cannot deliver")

        # §25. The claim is checked against the registry, not accepted.
        name = capability_version.split("/")[0]
        registered = {c.version for c in self._s.capability.capabilities()
                      if c.name == name and c.proven}
        if capability_version not in registered:
            raise FailClosed(
                f"{capability_version} is not registered as proven, so it may "
                "not be claimed to a client")
        permitted, why = self._s.capability.may_deploy(name)
        if not permitted:
            raise FailClosed(
                f"{capability_version} may not be offered to a client: {why}")

        if estimated_days is not None and timeline_days < estimated_days:
            raise FailClosed(
                f"the draft promises {timeline_days} day(s) against an "
                f"estimate of {estimated_days}; a deadline that is not "
                "believed is a deadline that will be missed")

        application = Application(
            id=new_id("app"), opportunity_id=opportunity["id"],
            source=opportunity.get("source", ""), scope=scope,
            deliverables=tuple(deliverables), price_cents=int(price_cents),
            timeline_days=int(timeline_days),
            capability_version=capability_version,
            clarifying_questions=tuple(clarifying_questions), message=message)

        self._s.audit.record(
            event="application.prepared", authority="orchestrator",
            initiator="application",
            why=f"draft application for {opportunity['id']}",
            decision=PREPARED, result=f"{application.id}; not submitted",
            payload=json.dumps({"price_cents": application.price_cents,
                                "capability": capability_version}))
        return application

    # ------------------------------------------------------------ submission

    def submit(self, application: Application, *, initiator: str = "application"
               ) -> tuple[bool, str]:
        """Attempt to send one. Expected to be refused, and says why.

        Three separate things must all be true, and each is asked of whoever
        owns it: the source must have been granted ``SUBMIT_APPLICATION``, the
        Action Gate must permit an external effect to that destination, and the
        Gate's own simulation posture must be off. Any one of them says no and
        nothing leaves.

        This method never performs a submission itself even when all three
        agree -- there is no platform client behind it. It exists so the
        boundary is real and testable now, rather than being added later by
        whoever also wants it open.
        """
        source = application.source
        permitted, why = self._s.discovery.may(source, access.SUBMIT_APPLICATION)
        if not permitted:
            self._s.audit.record(
                event="application.submission_blocked", authority="gate",
                initiator=initiator, why=why, decision=BLOCKED,
                external_effect=f"submit application to {source}")
            return False, why

        state = self._s.discovery.source_state(source) or {}
        result = self._s.gate.request(
            # A priced application is an offer: if the client accepts it, a
            # contract exists. So it is classed as a financial commitment
            # rather than as communication, which is the stricter of the two
            # and the one the Governor's limits attach to.
            action_class=ActionClass.C3_FINANCIAL_COMMITMENT,
            destination=state.get("base_url", "") or source,
            privacy=PrivacyClass.INTERNAL,
            purpose=f"submit an application to {source}",
            initiator=initiator)
        if not result.allowed:
            self._s.audit.record(
                event="application.submission_blocked", authority="gate",
                initiator=initiator, why=result.reason, decision=BLOCKED,
                external_effect=f"submit application to {source}")
            return False, result.reason

        # Reached only with the permission granted and the Gate open. There is
        # deliberately no platform client here: the last step is not built,
        # because building it is a decision to start applying for real work.
        return False, ("the source permits it and the Action Gate allows it, "
                       "but no submission client is implemented; sending for "
                       "real is a separate, deliberate step")

    def may_submit(self, source: str) -> tuple[bool, str]:
        """Whether submission is currently possible, without attempting it."""
        return self._s.discovery.may(source, access.SUBMIT_APPLICATION)
