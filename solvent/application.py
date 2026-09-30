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
from . import submission as tx
from .audit import new_id, now
from .errors import FailClosed
from .types import ActionClass, Cents, PrivacyClass

#: A draft exists and has been checked. It has not been sent.
PREPARED = "PREPARED"
#: The owner authorised this exact artifact to be transmitted.
APPROVED = "APPROVED"
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
        self._db = solvent.store.for_authority("orchestrator")

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
        # Recorded as part of being prepared, not as a second step a caller
        # has to remember. A submission is consequential, and an application
        # that exists only in memory cannot be reconciled after a crash -- so
        # there is deliberately no state where a prepared application is
        # unrecorded, rather than a rule saying there should not be.
        self.record(application, opportunity)
        return application

    # ---------------------------------------------------- verify and bind

    @staticmethod
    def digest(application: Application, opportunity: dict) -> str:
        """A digest over everything that makes this *this* application.

        Deliberately covers the material terms rather than the whole object:
        the opportunity, the source, the capability claimed, the price, the
        timeline, the scope and the deliverables. Change any of those and the
        authorisation no longer describes what would be sent, which is the
        whole point (§16). Change a cosmetic field and it does.

        The opportunity and source are in here because an application is a
        promise *to somebody about something* -- an authorisation that survived
        being re-pointed at a different client would authorise a different act.
        """
        return tx.digest_of({
            "opportunity_id": application.opportunity_id,
            "source": application.source,
            "external_ref": (opportunity or {}).get("external_ref", ""),
            "capability_version": application.capability_version,
            "price_cents": application.price_cents,
            "timeline_days": application.timeline_days,
            "scope": application.scope,
            "deliverables": list(application.deliverables),
        })

    def verify(self, application: Application, opportunity: dict, *,
               now: str = "") -> tuple[bool, list[str]]:
        """Everything that must be true before the Gate is even asked (§15).

        This is the technical verification the owner should never have to do.
        By the time they see an approval card, "is the package complete, is it
        pointed at the right opportunity, is the capability real, is it still
        in time" are all settled -- so their decision is about authority, not
        about whether somebody did their job.

        Returns every problem rather than the first, because an owner told one
        reason at a time cannot tell how far off it is.
        """
        problems: list[str] = []

        if not opportunity or not opportunity.get("id"):
            return False, ["there is no opportunity to apply for"]
        if opportunity["id"] != application.opportunity_id:
            problems.append(
                f"the application is for {application.opportunity_id} and the "
                f"opportunity is {opportunity['id']}; an application aimed at "
                "the wrong opportunity is a promise to the wrong client")
        if application.source != opportunity.get("source"):
            problems.append(
                f"the application names source {application.source!r} and the "
                f"opportunity came from {opportunity.get('source')!r}")

        # The capability must be real, proven and cleared to deploy -- a
        # candidate is not a capability and a recommendation is not either.
        name = (application.capability_version or "").split("/")[0]
        registered = {c.version for c in self._s.capability.capabilities()
                      if c.name == name and c.proven}
        if application.capability_version not in registered:
            problems.append(
                f"{application.capability_version} is not registered as "
                "proven, so it may not be claimed to a client")
        else:
            permitted, why = self._s.capability.may_deploy(name)
            if not permitted:
                problems.append(f"{name} may not be offered: {why}")

        if application.price_cents <= 0:
            problems.append("there is no authorised price")
        if application.timeline_days <= 0:
            problems.append("there is no delivery period")
        if not application.scope.strip():
            problems.append("the scope is empty, so the application promises "
                            "nothing specific")

        # §29. A deadline that cannot be read is not a deadline that has
        # passed, and it is not one that has not -- so it fails closed.
        in_time, deadline_reason = self._deadline_ok(opportunity, now=now)
        if not in_time:
            problems.append(deadline_reason)

        # Source-specific field checks, from the client that knows the source.
        client = tx.client_for(application.source)
        problems.extend(client.validate(application, opportunity))

        return (not problems), problems

    @staticmethod
    def _deadline_ok(opportunity: dict, *, now: str = "") -> tuple[bool, str]:
        """Whether there is still time, erring towards refusing.

        Three states, and the third is the one that matters: in time, out of
        time, and *unreadable*. A deadline string this cannot parse is not
        treated as absent -- an unparseable deadline near the wire is exactly
        when a timezone assumption becomes a late submission, so it refuses and
        says a person must read it.
        """
        import datetime as dt

        raw = str((opportunity or {}).get("deadline") or "").strip()
        if not raw:
            return True, ""
        reference = (dt.datetime.fromisoformat(now)
                     if now else dt.datetime.now(dt.timezone.utc))
        if reference.tzinfo is None:
            reference = reference.replace(tzinfo=dt.timezone.utc)

        parsed = None
        for shape in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S%z",
                      "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%m/%d/%Y"):
            try:
                parsed = dt.datetime.strptime(raw, shape)
                break
            except ValueError:
                continue
        if parsed is None:
            try:
                parsed = dt.datetime.fromisoformat(raw)
            except ValueError:
                return False, (
                    f"the deadline {raw!r} cannot be read, so whether there is "
                    "still time cannot be established; a person must check it "
                    "rather than have a guess made on their behalf")
        if parsed.tzinfo is None:
            # No zone stated. Within a day of the boundary that ambiguity is
            # worth up to a day either way, so it refuses rather than assume
            # the one that happens to be convenient.
            midnight = parsed.replace(tzinfo=dt.timezone.utc)
            remaining = (midnight - reference).total_seconds()
            if remaining < 0:
                return False, f"the deadline {raw} has passed"
            if remaining < 86_400:
                return False, (
                    f"the deadline {raw} states no timezone and is less than a "
                    "day away; the ambiguity is larger than the margin, so "
                    "this needs a person")
            return True, ""
        if parsed <= reference:
            return False, f"the deadline {raw} has passed"
        return True, ""

    # ------------------------------------------------------------ submission

    def record(self, application: Application, opportunity: dict) -> str:
        """Persist a prepared application with its digest and verdict.

        Persisted because a submission is consequential and a consequential
        effect that exists only in memory cannot be reconciled after a crash.
        """
        from . import intake

        verified, problems = self.verify(application, opportunity)
        digest = self.digest(application, opportunity)
        state = self._s.discovery.source_state(application.source) or {}
        # The registry's determination, not the client's own opinion of itself.
        # A client that *can* submit does not make a source submittable: the
        # owner may have recorded it as prohibited or undetermined, and reading
        # the client here would have offered such an application for
        # authorisation on the approvals page.
        client = tx.client_for(application.source)
        mode = state.get("submission_mode") or client.mode
        self._db.execute(
            "INSERT OR REPLACE INTO applications(id,ts,opportunity_id,source,"
            "capability_version,price_cents,timeline_days,scope,deliverables,"
            "state,digest,verified,problems,submission_mode,intake_role) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (application.id, now(), application.opportunity_id,
             application.source, application.capability_version,
             application.price_cents, application.timeline_days,
             application.scope, json.dumps(list(application.deliverables)),
             PREPARED, digest, 1 if verified else 0, json.dumps(problems),
             mode, intake.role_of(state)))
        self._db.commit()
        self._s.audit.record(
            event="application.recorded", authority="orchestrator",
            initiator="application", input_ref=application.id,
            why=f"application for {application.opportunity_id}",
            decision=PREPARED,
            result=f"{digest}; verified={verified}")
        return digest

    def stored(self, application_id: str) -> dict:
        row = self._db.query_one("SELECT * FROM applications WHERE id = ?",
                                 (application_id,))
        return dict(row) if row else {}

    def applications(self) -> list[dict]:
        return [dict(r) for r in self._db.query(
            "SELECT * FROM applications ORDER BY ts DESC")]

    def attempts(self, application_id: str = "") -> list[dict]:
        if application_id:
            return [dict(r) for r in self._db.query(
                "SELECT * FROM submission_attempts WHERE application_id = ? "
                "ORDER BY ts", (application_id,))]
        return [dict(r) for r in self._db.query(
            "SELECT * FROM submission_attempts ORDER BY ts")]

    # -------------------------------------------------------- owner approval

    def approve(self, application_id: str, *, owner_identity: str, why: str,
                digest: str) -> None:
        """The owner authorises *this* application to be sent.

        The digest is required and is compared, so an approval cannot outlive
        the thing it approved (§16, §19). Approving one application authorises
        that application: there is deliberately no way to express "and any
        future ones", because the difference between a $400 bid and a $40,000
        one is one field.
        """
        if not self._s.policy.is_owner(owner_identity):
            raise FailClosed(
                f"authorising a submission is an owner decision (got "
                f"{owner_identity!r})")
        if not why:
            raise FailClosed("authorising a submission needs a recorded reason")
        stored = self.stored(application_id)
        if not stored:
            raise FailClosed(f"unknown application {application_id!r}")
        if not digest:
            raise FailClosed(
                "an approval must name the application digest it authorises; "
                "one that names none authorises anything")
        if digest != stored["digest"]:
            raise FailClosed(
                f"the approval names {digest} and the application is now "
                f"{stored['digest']}; it changed after you were shown it, so "
                "this approval no longer describes what would be sent")
        if not int(stored["verified"]):
            raise FailClosed(
                "this application has not passed verification, and asking you "
                "to approve an unverified package would be asking you to be "
                "the verification")
        self._db.execute(
            "UPDATE applications SET state = ?, approved_digest = ?, "
            "approved_by = ?, approved_at = ? WHERE id = ?",
            (APPROVED, digest, owner_identity, now(), application_id))
        self._db.commit()
        self._s.audit.record(
            event="application.approved", authority="owner",
            initiator=owner_identity, input_ref=application_id,
            why=why, decision=APPROVED, permission="SUBMIT_APPLICATION",
            result=digest)

    # ------------------------------------------------------------ submission

    def submit(self, application: Application, *, initiator: str = "application",
               opportunity: dict | None = None, transport=None,
               now_iso: str = "", approval=None,
               submission_cost_cents: int = 0, grant_id: str | None = None
               ) -> tuple[bool, str]:
        """Attempt to send one. Every gate is asked, in order, and named.

        The order matters and is not arbitrary. Cheap, local, certain refusals
        come first so that an application that was never going anywhere does
        not consume an approval or leave an intent record; the Action Gate --
        the one authority that can permit an external effect -- comes last, so
        that reaching it means everything else already agreed.

        1. the source's submission mode permits machine transmission at all
        2. the source holds ``SUBMIT_APPLICATION``
        3. the application verifies, and its digest still matches
        4. the owner has approved *this* digest
        5. no earlier attempt may already have arrived
        6. the Action Gate permits the effect, on the owner's *signed*
           approval -- which is a different thing from step 4 and deliberately
           both: step 4 binds the decision to an exact artifact, and this
           proves the decision was the owner's and not the website's
        7. a write-ahead intent is recorded, **then** the transport runs

        ``submission_cost_cents`` is what *sending* costs -- a bid credit, a
        connect, a listing fee -- and is zero for most sources. It is not the
        bid amount. A source that charges to apply needs a Financial Governor
        budget grant for that charge, which is a separate decision from
        permission to apply at all.

        Returns ``(sent, reason)``. ``sent`` is only ever true when a source
        confirmed receipt in its own words.
        """
        source = application.source
        stored = self.stored(application.id)
        if not stored:
            raise FailClosed(
                f"application {application.id} was never recorded; an "
                "unrecorded consequential act cannot be reconciled after a "
                "crash, so it may not be attempted")
        opportunity = opportunity or {}
        client = tx.client_for(source)

        # 1. Does this source accept machine submissions at all? Read from the
        #    registry, which is where the owner's recorded determination lives;
        #    the client's own mode is the fallback for a source registered
        #    before the determination existed.
        registry = self._s.discovery.source_state(source) or {}
        mode = registry.get("submission_mode") or client.mode
        allowed, why = access.may_transmit(mode)
        if not allowed:
            return self._refuse(application, tx.NOT_SENT, tx.PERMISSION_DENIED,
                                why, initiator, event="mode")

        # 2. Source permission. Independent of the Gate, and checked first so
        #    its refusal cannot be mistaken for the Gate's.
        permitted, why = self._s.discovery.may(source, access.SUBMIT_APPLICATION)
        if not permitted:
            return self._refuse(application, tx.NOT_SENT, tx.PERMISSION_DENIED,
                                why, initiator, event="permission")

        # 3. Verification, and the digest must still describe the application.
        verified, problems = self.verify(application, opportunity,
                                        now=now_iso)
        if not verified:
            return self._refuse(application, tx.NOT_SENT, tx.VALIDATION_FAILED,
                                "; ".join(problems), initiator,
                                event="verification")
        digest = self.digest(application, opportunity)
        if digest != stored["digest"]:
            return self._refuse(
                application, tx.NOT_SENT, tx.VALIDATION_FAILED,
                f"the application changed since it was recorded "
                f"({stored['digest']} -> {digest})", initiator, event="digest")

        # 4. Owner approval, bound to this digest.
        if stored["approved_digest"] != digest:
            return self._refuse(
                application, tx.NOT_SENT, tx.PERMISSION_DENIED,
                ("this application has not been approved for submission"
                 if not stored["approved_digest"] else
                 f"the approval covers {stored['approved_digest']} and this is "
                 f"{digest}; the application changed after it was approved"),
                initiator, event="approval")

        # 5. At-most-once. An earlier attempt that may have arrived is never
        #    followed by another (§24): uncertainty is not permission to retry.
        for attempt in self.attempts(application.id):
            if attempt["outcome"] in tx.NO_RETRY:
                return self._refuse(
                    application, attempt["outcome"], attempt["failure"],
                    f"an earlier attempt is recorded as {attempt['outcome']}"
                    + (f" ({attempt['remote_ref']})" if attempt["remote_ref"]
                       else "")
                    + "; sending again risks a second application for the same "
                      "work", initiator, event="duplicate")

        # 6. The Action Gate. Last, and the only thing that can say yes.
        state = self._s.discovery.source_state(source) or {}
        result = self._s.gate.request(
            action_class=ActionClass.C3_FINANCIAL_COMMITMENT,
            destination=client.host or state.get("base_url", "") or source,
            privacy=PrivacyClass.INTERNAL,
            purpose=f"submit an application to {source}",
            initiator=initiator, approval=approval,
            # §22. What the Gate meters is money *leaving* -- a bid credit, a
            # connect, a listing fee. Not the bid amount: that is what the
            # client would pay DeskPilot, and passing it here would ask the
            # Financial Governor to authorise spending the revenue. Submitting
            # usually costs nothing, and when it does the cost is a separate
            # decision from permission to submit.
            amount_cents=submission_cost_cents,
            grant_id=grant_id)
        if not result.allowed:
            return self._refuse(application, tx.NOT_SENT, tx.PERMISSION_DENIED,
                                result.reason, initiator, event="gate")

        # 7. Write-ahead, then transmit. If the process dies between these two
        #    lines the intent is on disk, so recovery knows an attempt may have
        #    left rather than having to guess.
        self._attempt(application, digest, phase="INTENT", outcome="",
                     detail=f"about to transmit to {source}")
        request = client.prepare_request(application, opportunity)
        if transport is None:
            outcome = tx.Result(
                outcome=tx.NOT_SENT, failure=tx.NETWORK_FAILURE,
                detail=("no transport was supplied, so nothing was sent; the "
                        "gate-routed transport is configured by deployment"))
        else:
            try:
                response = transport(request)
            except Exception as exc:
                # The request may or may not have left. That is the honest
                # answer and it is not a retryable failure.
                outcome = tx.Result(
                    outcome=tx.UNKNOWN_REMOTE_STATE,
                    failure=tx.UNKNOWN_REMOTE_STATE,
                    detail=f"{type(exc).__name__} while transmitting: {exc}; "
                           "whether it arrived cannot be established here")
            else:
                outcome = client.confirm(response)

        self._attempt(application, digest, phase="OUTCOME",
                      outcome=outcome.outcome, failure=outcome.failure,
                      remote_ref=outcome.remote_ref,
                      remote_status=outcome.remote_status,
                      response_digest=outcome.response_digest,
                      detail=outcome.detail)
        self._db.execute("UPDATE applications SET state = ? WHERE id = ?",
                         (SUBMITTED if outcome.sent else outcome.outcome,
                          application.id))
        self._db.commit()
        self._s.audit.record(
            event="application.submitted" if outcome.sent
            else "application.submission_failed",
            authority="gate", initiator=initiator, input_ref=application.id,
            why=outcome.detail[:200], decision=outcome.outcome,
            external_effect=f"submit application to {source}",
            result=outcome.remote_ref or outcome.failure)
        return outcome.sent, outcome.detail

    def package_for_manual_submission(self, application: Application,
                                      opportunity: dict) -> tx.Result:
        """The verified package, for a source a machine may not submit to.

        This is the ordinary outcome, not a fallback. It is separated from
        :meth:`submit` so nothing can report a prepared package as a
        transmission (§39, §40).
        """
        verified, problems = self.verify(application, opportunity)
        if not verified:
            raise FailClosed(
                "the package is not verified, so handing it over would hand "
                "over a problem: " + "; ".join(problems))
        client = tx.client_for(application.source)
        if not hasattr(client, "package"):
            # A source with an automated client can still be recorded
            # manual-only -- the commonest reason being that the owner has not
            # accepted that API's terms yet. Handing over a package with no
            # instructions would be the least useful possible outcome, so a
            # manual client for the same source supplies them.
            client = tx.ManualSubmissionClient(
                application.source,
                destination=str((opportunity or {}).get("external_url") or ""))
        return client.package(application, opportunity)

    # ------------------------------------------------------------- recording

    def _attempt(self, application, digest, *, phase, outcome="", failure="",
                 remote_ref="", remote_status="", response_digest="",
                 detail="") -> None:
        self._db.execute(
            "INSERT INTO submission_attempts(id,ts,application_id,digest,phase,"
            "outcome,failure,remote_ref,remote_status,response_digest,detail) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (new_id("sub"), now(), application.id, digest, phase, outcome,
             failure, remote_ref, remote_status, response_digest, detail[:400]))
        self._db.commit()

    def _refuse(self, application, outcome, failure, why, initiator, *,
                event) -> tuple[bool, str]:
        """Record a refusal and return it. Nothing left, so nothing to undo."""
        self._s.audit.record(
            event="application.submission_blocked", authority="gate",
            initiator=initiator, input_ref=application.id, why=why[:200],
            decision=f"BLOCKED:{event}",
            external_effect=f"submit application to {application.source}")
        return False, why

    def unresolved(self) -> list[dict]:
        """Attempts whose outcome nobody can establish (§24, §50).

        These need a person to check the source. They are listed separately
        because the one thing that must not happen to them is another send.
        """
        return [a for a in self.attempts()
                if a["outcome"] == tx.UNKNOWN_REMOTE_STATE]

    def may_submit(self, source: str) -> tuple[bool, str]:
        """Whether submission is currently possible, without attempting it."""
        return self._s.discovery.may(source, access.SUBMIT_APPLICATION)
