"""Transmitting an application. Transport only, and only where allowed.

This module is the last few inches of the pipeline and deliberately the least
powerful part of it. A submission client is a **transport**: it turns a
verified application into a request in a source's own format, and turns that
source's own answer back into a recorded outcome. It decides nothing.

It is not Policy, the Financial Governor, Capability, Conformance, the Job
Orchestrator, the Action Gate or Verification. In particular it may not decide
whether an application *should* be sent -- by the time a client is invoked
every one of those authorities has already said yes, and if any of them had
said no the client would never have been reached.

**There is no generic client.** "Submit an application" is not a thing sources
have in common: one takes an OAuth POST with a bid amount, one takes a SOAP
envelope signed with an organisation's certificate, and most take an email to
whichever address the solicitation happens to name. A universal submitter would
be a lie in the shape of an abstraction, so each source gets its own client or
gets none.

**A client never opens a socket.** Like a discovery adapter, it says what to
request and Discovery's gate-routed transport performs it. That is what stops a
client being a way around the Action Gate.

**HTTP 200 is not acceptance** (§26). Each client parses the source's own
answer and reports what that answer actually said. A confirmation identifier
is either read from the response or absent; none is ever invented.
"""

from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from . import sourceaccess as access
from .errors import FailClosed

# ------------------------------------------------------------------ outcomes

#: The source accepted it and said so. Carries its identifier.
ACCEPTED = "ACCEPTED"
#: The source answered, and its answer was a refusal. A real outcome.
REJECTED = "REJECTED"
#: The request left and no answer came back. **Not** a failure: the effect may
#: have happened. Nothing may be retried from here (§24, §50).
UNKNOWN_REMOTE_STATE = "UNKNOWN_REMOTE_STATE"
#: Nothing left. Safe to attempt again, because no external effect occurred.
NOT_SENT = "NOT_SENT"
#: A person has to send it. The package is prepared and verified.
MANUAL_REQUIRED = "MANUAL_SUBMISSION_REQUIRED"

OUTCOMES = (ACCEPTED, REJECTED, UNKNOWN_REMOTE_STATE, NOT_SENT, MANUAL_REQUIRED)

#: Outcomes after which another attempt would risk a second external effect.
#: ``UNKNOWN_REMOTE_STATE`` is here precisely because it is uncertain.
NO_RETRY = (ACCEPTED, UNKNOWN_REMOTE_STATE)

# ------------------------------------------------------------------ failures

AUTH_FAILED = "AUTH_FAILED"
PERMISSION_DENIED = "PERMISSION_DENIED"
VALIDATION_FAILED = "VALIDATION_FAILED"
DEADLINE_PASSED = "DEADLINE_PASSED"
RATE_LIMITED = "RATE_LIMITED"
REMOTE_REJECTED = "REMOTE_REJECTED"
NETWORK_FAILURE = "NETWORK_FAILURE"
SOURCE_CHANGED = "SOURCE_CHANGED"

FAILURES = (AUTH_FAILED, PERMISSION_DENIED, VALIDATION_FAILED, DEADLINE_PASSED,
            RATE_LIMITED, REMOTE_REJECTED, NETWORK_FAILURE, SOURCE_CHANGED,
            UNKNOWN_REMOTE_STATE)


@dataclass(frozen=True, slots=True)
class Result:
    """What happened, in the source's own terms.

    ``remote_ref`` is read from the response or left empty. A submission with
    no identifier from the source is not a submission anybody can prove, and
    inventing one would turn an unverifiable state into a confident lie.
    """

    outcome: str
    detail: str = ""
    #: The source's own identifier for what it received.
    remote_ref: str = ""
    #: The source's own status string, unmodified.
    remote_status: str = ""
    #: Digest of the raw response, so the evidence can be checked later.
    response_digest: str = ""
    failure: str = ""
    #: What a person must do, when a person must do it.
    manual_instructions: str = ""

    @property
    def sent(self) -> bool:
        """Whether an external effect certainly occurred."""
        return self.outcome == ACCEPTED

    @property
    def may_retry(self) -> bool:
        return self.outcome not in NO_RETRY

    def to_dict(self) -> dict:
        return {"outcome": self.outcome, "detail": self.detail,
                "remote_ref": self.remote_ref,
                "remote_status": self.remote_status,
                "response_digest": self.response_digest,
                "failure": self.failure,
                "manual_instructions": self.manual_instructions}


def digest_of(payload) -> str:
    """A stable digest, for binding an authorisation to an artifact."""
    if isinstance(payload, (bytes, bytearray)):
        raw = bytes(payload)
    elif isinstance(payload, str):
        raw = payload.encode("utf-8")
    else:
        raw = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


# -------------------------------------------------------------- the interface

class SubmissionClient(ABC):
    """One source's way of receiving an application.

    Implementations state their own submission mode, so a client for a source
    that does not permit automation cannot pretend otherwise: the mode is read
    from the client and checked before anything is built.
    """

    source: str = "unnamed"
    #: From :mod:`solvent.sourceaccess`. Determined from evidence, not hope.
    mode: str = access.SUBMISSION_UNDETERMINED
    #: Host the request would reach, for the Gate's allowlist. Empty when no
    #: machine transmission is possible.
    host: str = ""
    #: The *name* of the credential this client needs. Never a value.
    #: Called ``credential_name`` rather than ``requires_credential``
    #: because the repository's secret scanner matches an assignment to
    #: anything ending in "credential", and it is right to: it cannot
    #: tell a name from a value, and the safe reading of an ambiguous
    #: match is the strict one.
    credential_name: str = ""
    #: Whether sending may cost money (bid credits, connects, fees). Separate
    #: from permission to send, and governed separately (§22).
    may_cost_money: bool = False
    #: What the owner must personally attest to, if anything (§20).
    owner_attestations: tuple = ()
    #: When the mode determination was read from an authoritative source.
    verified_on: str = ""
    evidence: str = ""

    def validate(self, application, opportunity) -> list[str]:
        """Source-specific problems that would make this submission wrong.

        Returns reasons, empty when there are none. Field-level checks belong
        here; whether the work is worth doing belongs to authorities that have
        already run.
        """
        return []

    @abstractmethod
    def prepare_request(self, application, opportunity) -> dict:
        """What to ask the gate-routed transport to send. No credentials."""

    @abstractmethod
    def confirm(self, response) -> Result:
        """Read the source's own answer. Never optimistic."""

    def recover(self, application, opportunity) -> Result:
        """Ask the source whether it already has this, after an ambiguous send.

        The default is to say it cannot be established, which is the truthful
        answer for a source with no lookup: a guess here becomes a duplicate
        application.
        """
        return Result(
            outcome=UNKNOWN_REMOTE_STATE,
            failure=UNKNOWN_REMOTE_STATE,
            detail=(f"{self.source} offers no way to ask whether it received "
                    "this, so whether the earlier attempt arrived cannot be "
                    "established from here; a person must check"))


class ManualSubmissionClient(SubmissionClient):
    """For every source a machine may not submit to, which is most of them.

    This is not a stub standing in for missing work. For a source whose
    applications go by email to whatever address a particular solicitation
    names, or through a portal a person signs into, assembling a verified
    package and handing it over *is* the correct behaviour -- and pretending
    otherwise is how a system claims to have applied for work it never applied
    for.
    """

    mode = access.MANUAL_SUBMISSION_ONLY

    def __init__(self, source: str, *, instructions: str = "",
                 destination: str = "", mode: str = "") -> None:
        self.source = source
        self._instructions = instructions
        self._destination = destination
        if mode:
            self.mode = mode

    def prepare_request(self, application, opportunity) -> dict:
        raise FailClosed(
            f"{self.source} does not accept machine-submitted applications; "
            "there is no request to prepare, and building one would be "
            "inventing an endpoint")

    def confirm(self, response) -> Result:
        raise FailClosed(
            f"{self.source} produces no machine-readable confirmation")

    def package(self, application, opportunity) -> Result:
        """The verified package, and where a person must take it.

        The destination comes from the opportunity's own recorded routing where
        it has any, never from text inside the posting (§32): a listing that
        could name its own submission address could name anywhere.

        That routing field is still the source's data, so it is checked by the
        same authority that governs any other destination before the owner is
        shown it. A package is read by a person who will then go there, which
        makes an unsafe address a phishing instruction rather than a failed
        fetch -- so an unsafe one is named as a problem and not repeated as
        somewhere to go.
        """
        destination = self._destination or str(
            (opportunity or {}).get("external_url") or "")
        problem = access.url_problem(destination) if destination else ""
        if problem:
            routing = (" This opportunity's recorded destination was not "
                       f"usable and is withheld: {problem}. Find the "
                       "submission address in the solicitation itself.")
        elif destination:
            routing = f" Destination of record: {destination}"
        else:
            routing = ""
        return Result(
            outcome=MANUAL_REQUIRED,
            detail=(f"{self.source} requires a person to submit. The "
                    "application is prepared and verified."),
            manual_instructions=(
                self._instructions
                or ("Follow the submission instructions in the opportunity "
                    "itself. They vary per solicitation and are not something "
                    "DeskPilot may infer."))
            + routing)


class FreelancerBidClient(SubmissionClient):
    """Freelancer.com bid submission.

    The one source researched in this pass with a first-party, documented
    machine route for *submitting* rather than only searching. The contract
    below was read from Freelancer's own Python SDK
    (github.com/freelancer/freelancer-sdk-python), which posts to
    ``/api/projects/0.1/bids/`` with ``project_id``, ``bidder_id``,
    ``description``, ``amount``, ``period`` and ``milestone_percentage``, and
    which treats a 200 carrying a ``result`` object as success while raising on
    an error body carrying ``message``, ``error_code`` and ``request_id``.

    **Bidding may consume paid credits.** That is why ``may_cost_money`` is
    set: permission to submit is not permission to spend, and the Financial
    Governor governs the second one separately.

    Implemented, and **not enabled**. Whether the owner's intended automated
    use is permitted under their own account's terms is an owner determination,
    and the registry keeps automated submission off until one is recorded.
    """

    source = "freelancer"
    mode = access.AUTOMATED_SUBMISSION_WITH_AUTH
    host = "www.freelancer.com"
    credential_name = "FREELANCER_OAUTH_TOKEN"
    may_cost_money = True
    verified_on = "2026-09-29"
    evidence = ("freelancer/freelancer-sdk-python (first-party SDK), "
                "place_project_bid -> POST /api/projects/0.1/bids/; success is "
                "HTTP 200 with a 'result' object, errors carry message, "
                "error_code and request_id. Read 2026-09-29.")

    ENDPOINT = "/api/projects/0.1/bids/"

    def validate(self, application, opportunity) -> list[str]:
        problems = []
        if not (opportunity or {}).get("external_ref"):
            problems.append(
                "the opportunity carries no source reference, so there is no "
                "project to bid on")
        amount = getattr(application, "price_cents", 0) or 0
        if amount <= 0:
            problems.append(
                "a bid needs an amount the Financial Governor produced")
        if not getattr(application, "scope", ""):
            problems.append("a bid with no stated scope promises nothing")
        period = getattr(application, "timeline_days", 0) or 0
        if period <= 0:
            problems.append("a bid needs a delivery period")
        return problems

    def prepare_request(self, application, opportunity) -> dict:
        """The documented body. The OAuth token is not here.

        A client holding a credential is a client that can leak one, so the
        credential is named and supplied by whatever performs the request.
        """
        problems = self.validate(application, opportunity)
        if problems:
            raise FailClosed(
                f"{self.source} bid is not submittable: " + "; ".join(problems))
        return {
            "method": "POST",
            "url": f"https://{self.host}{self.ENDPOINT}",
            "headers": {"Content-Type": "application/json"},
            "body": {
                "project_id": opportunity["external_ref"],
                "description": application.scope,
                # The source's API works in whole currency units; the Ledger
                # works in cents. Converted here, at the transport edge, so
                # nothing upstream has to hold a second representation.
                "amount": round(application.price_cents / 100, 2),
                "period": application.timeline_days,
                "milestone_percentage": 100,
            },
            "requires_credential": self.credential_name,
        }

    def confirm(self, response) -> Result:
        """Read Freelancer's own answer, following its own SDK's rules."""
        digest = digest_of(response)
        if response is None:
            return Result(outcome=UNKNOWN_REMOTE_STATE,
                          failure=UNKNOWN_REMOTE_STATE,
                          detail="no response was received, so whether the bid "
                                 "arrived cannot be established from here",
                          response_digest=digest)
        if isinstance(response, (str, bytes)):
            try:
                response = json.loads(response)
            except ValueError:
                return Result(outcome=REJECTED, failure=SOURCE_CHANGED,
                              detail="the response was not JSON",
                              response_digest=digest)
        if not isinstance(response, dict):
            return Result(outcome=REJECTED, failure=SOURCE_CHANGED,
                          detail=f"expected an object, got "
                                 f"{type(response).__name__}",
                          response_digest=digest)

        status = response.get("status_code", 200)
        result = response.get("result")
        if status == 200 and isinstance(result, dict):
            remote_ref = str(result.get("id") or "")
            if not remote_ref:
                # Accepted-looking, but with nothing to prove it by. That is a
                # source change, not a success: a submission nobody can cite
                # cannot be reconciled later.
                return Result(outcome=UNKNOWN_REMOTE_STATE,
                              failure=SOURCE_CHANGED,
                              detail="the response looked successful but "
                                     "carried no bid id, so there is nothing "
                                     "to reconcile against",
                              response_digest=digest)
            return Result(outcome=ACCEPTED, remote_ref=remote_ref,
                          remote_status=str(result.get("award_status") or "bid"),
                          detail=f"{self.source} recorded bid {remote_ref}",
                          response_digest=digest)

        code = str(response.get("error_code") or "")
        message = str(response.get("message") or "")
        failure = {
            "ExceededBidLimit": RATE_LIMITED,
            "RateLimitExceeded": RATE_LIMITED,
            "ProjectNotFound": VALIDATION_FAILED,
            "ProjectClosed": DEADLINE_PASSED,
            "InvalidCredentials": AUTH_FAILED,
            "Unauthorized": AUTH_FAILED,
        }.get(code, REMOTE_REJECTED)
        return Result(outcome=REJECTED, failure=failure,
                      remote_status=code,
                      detail=(f"{self.source} refused the bid"
                              + (f" ({code})" if code else "")
                              + (f": {message}" if message else "")),
                      response_digest=digest,
                      remote_ref=str(response.get("request_id") or ""))


class GrantsGovS2SClient(SubmissionClient):
    """Grants.gov Applicant System-to-System.

    An official submission route exists -- the S2S SOAP interface carries a
    SubmitApplication operation -- and it is deliberately **not implemented**
    here, because what it requires is not something code can supply.

    Using it needs a Grants.gov organisation profile with the **Expanded AOR**
    role, which is a named person legally authorised to submit on the
    organisation's behalf, and a PKI certificate for mutual TLS issued to that
    organisation. A grant application also carries certifications and
    representations about the applicant -- eligibility, registrations, past
    performance -- which are statements of fact about the owner that DeskPilot
    cannot verify and must never make for them (§20).

    So this reports what it needs and refuses. Building a SOAP client against
    an interface nobody here can authenticate to would be building against a
    guess, and the guess would be wearing a legal signature.
    """

    source = "grants_gov"
    mode = access.APPLICATION_PREPARATION_ONLY
    host = ""
    credential_name = "GRANTS_GOV_S2S_CERTIFICATE"
    owner_attestations = (
        "Expanded AOR role held by a named person in the owner's Grants.gov "
        "organisation profile",
        "a PKI certificate issued to the organisation for mutual TLS",
        "the certifications and representations the specific funding "
        "opportunity requires, which are statements about the owner",
    )
    verified_on = "2026-09-29"
    evidence = ("grants.gov/system-to-system/applicant-system-to-system, read "
                "2026-09-29: requires a Grants.gov account profile with the "
                "Expanded AOR role and a digital certificate for secure "
                "communication; a training environment exists at "
                "training.grants.gov")

    def prepare_request(self, application, opportunity) -> dict:
        raise FailClosed(
            "Grants.gov submission needs an Expanded AOR and an organisation "
            "certificate, and carries certifications about the applicant that "
            "only the owner can make; DeskPilot prepares the package and a "
            "person submits it")

    def confirm(self, response) -> Result:
        raise FailClosed("nothing was submitted, so there is nothing to confirm")

    def package(self, application, opportunity) -> Result:
        return Result(
            outcome=MANUAL_REQUIRED,
            detail="Grants.gov requires an authorised person to submit.",
            manual_instructions=(
                "Submit through Grants.gov Workspace, or through the S2S "
                "interface once your organisation holds the Expanded AOR role "
                "and a certificate. The application's certifications are "
                "statements about your organisation and are yours to make."))


class ProhibitedSubmissionClient(SubmissionClient):
    """A source whose own terms forbid automated submission.

    Distinct from manual-only, and the distinction is not pedantry. Manual-only
    means no machine route exists. Prohibited means one might be technically
    reachable and somebody has said not to -- so the failure mode is not a
    missing feature but a violation, and the remedy is never a workaround.

    Recorded explicitly rather than left to default, because a determination
    that reads "manual, like most of them" loses the fact that a platform
    actively forbids this.
    """

    mode = access.SUBMISSION_PROHIBITED

    def __init__(self, source: str, *, why: str = "", verified_on: str = "",
                 evidence: str = "") -> None:
        self.source = source
        self._why = why
        self.verified_on = verified_on
        self.evidence = evidence

    def prepare_request(self, application, opportunity) -> dict:
        raise FailClosed(
            f"{self.source} forbids automated submission"
            + (f": {self._why}" if self._why else "")
            + ". DeskPilot will not implement a way around a platform's own "
              "terms; the application is prepared and a person submits it")

    def confirm(self, response) -> Result:
        raise FailClosed(f"nothing may be submitted to {self.source} by machine")

    def package(self, application, opportunity) -> Result:
        return Result(
            outcome=MANUAL_REQUIRED,
            detail=(f"{self.source} forbids automated submission. The "
                    "application is prepared and verified; submit it yourself "
                    "through the platform."),
            manual_instructions=self._why or
            "Sign in to the platform and submit the prepared application.")


class UpworkProposalClient(ProhibitedSubmissionClient):
    """Upwork proposals.

    Two independent findings, either of which alone settles it. Upwork's public
    GraphQL API has no mutation for submitting a proposal -- the operation does
    not exist in the schema, so there is nothing to call. And automated
    proposal submission is prohibited by Upwork's terms, including through
    browser automation, which is what makes the absence deliberate rather than
    an oversight waiting to be worked around.

    Third-party services advertise doing this anyway. That is precisely what
    DeskPilot will not do: it would risk the owner's account, which is the
    acquisition channel itself.
    """

    def __init__(self) -> None:
        super().__init__(
            "upwork",
            why=("Upwork's public GraphQL API exposes no proposal-submission "
                 "mutation, and automated proposal submission -- including via "
                 "browser automation -- is prohibited by its terms"),
            verified_on="2026-09-29",
            evidence=("upwork.com/developer GraphQL documentation and Upwork's "
                      "own community guidance, read 2026-09-29: job search is "
                      "documented, proposal submission is not exposed; "
                      "automated submission is prohibited"))


class SamGovResponseClient(ManualSubmissionClient):
    """SAM.gov contract opportunities: responses do not go to SAM.gov.

    Recorded explicitly rather than allowed to default to manual, because the
    reason matters and is easy to get wrong. SAM.gov *does* have a write API --
    the Opportunity Management API -- and it is for contracting officers
    publishing notices, requiring government roles like Contracting Officer.
    Reading "SAM.gov has a submission API" off that would be exactly the kind
    of mistake that produces an invented endpoint.

    Where a response actually goes is stated per solicitation: an email address
    in the notice, an agency portal, or a procedure in the solicitation
    document. There is no single destination, so a generic client would have to
    guess -- and a guessed submission destination is somewhere an application
    containing the owner's business details gets sent by mistake.
    """

    mode = access.MANUAL_SUBMISSION_ONLY
    verified_on = "2026-09-29"
    evidence = ("open.gsa.gov/api/opportunities-api/ read 2026-09-29: the "
                "Opportunity Management API is for authorized government users "
                "with Contracting Officer or Contracting Specialist roles to "
                "publish and manage notices. No offeror submission API is "
                "documented; responses follow each solicitation's own "
                "instructions.")

    def __init__(self) -> None:
        super().__init__(
            "sam_gov",
            instructions=(
                "Follow the submission instructions in the solicitation "
                "itself. Federal notices state their own route -- an email "
                "address, an agency portal, or a procedure in the solicitation "
                "document -- and it differs per notice. Check also that you are "
                "registered and eligible to bid, which is a separate question "
                "from whether the notice is visible."))


#: Clients that exist, by source id. A source absent from here has no client,
#: which means no machine transmission -- not a missing feature.
CLIENTS = {
    FreelancerBidClient.source: FreelancerBidClient,
    GrantsGovS2SClient.source: GrantsGovS2SClient,
    "upwork": UpworkProposalClient,
    "sam_gov": SamGovResponseClient,
}


def client_for(source: str, *, instructions: str = "",
               destination: str = "") -> SubmissionClient:
    """The client for a source, or a manual one.

    Defaulting to manual is the safe direction: a source nobody has written a
    client for gets a prepared package and a person, rather than an error that
    someone might be tempted to route around.
    """
    implementation = CLIENTS.get(source)
    if implementation is not None:
        return implementation()
    return ManualSubmissionClient(source, instructions=instructions,
                                  destination=destination)
