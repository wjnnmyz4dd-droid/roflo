"""From an agreed price to authorised work: proposal, contract, deposit.

The sequence this enforces is the one a service business gets wrong in
exactly one direction. Work starts, then the invoice goes out, then
the client goes quiet. So the deposit is a **gate, not a milestone**:
:meth:`Engagement.work_authorised` is false until a verified payment
event clears it, and nothing here can set it any other way.

Three distinctions the module refuses to blur, each because collapsing
it is a specific way to lose money or lie to the owner:

* **Invoiced is not paid.** An invoice is a request. Only a payment
  event from a rail that verified it moves anything.
* **A client saying they paid is not a payment.** :meth:`record_payment`
  takes a verified event; a message is evidence of a message.
* **Accepted is not authorised.** A client accepting a contract binds
  them, not DeskPilot. The Governor still prices the job, the Action
  Gate still authorises delivery, and above the owner's threshold the
  owner still signs.

This holds no money and moves none. The Ledger remains the only
record of actuals; what lives here is the state of the agreement.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from .errors import FailClosed
from .types import Cents, fmt

#: Phase 11's default for direct clients. A fraction, not a constant
#: scattered through the code, because an owner changing it should
#: change it once.
DEFAULT_DEPOSIT_FRACTION = 0.20


class Stage(Enum):
    """Where an engagement has got to. Forward only, with two exits."""

    DRAFTED = "DRAFTED"
    SENT = "SENT"
    NEGOTIATING = "NEGOTIATING"
    ACCEPTED = "ACCEPTED"
    DEPOSIT_REQUESTED = "DEPOSIT_REQUESTED"
    DEPOSIT_RECEIVED = "DEPOSIT_RECEIVED"
    WORK_AUTHORISED = "WORK_AUTHORISED"
    DELIVERED = "DELIVERED"
    INVOICED = "INVOICED"
    PAID = "PAID"
    DECLINED = "DECLINED"
    ABANDONED = "ABANDONED"

    @property
    def terminal(self) -> bool:
        return self in (Stage.DECLINED, Stage.ABANDONED, Stage.PAID)


@dataclass(frozen=True, slots=True)
class Deliverable:
    """One thing the client will receive, and how it will be checked.

    ``check`` names a registered check, so what the proposal promises
    and what the checker recomputes are the same sentence. A
    deliverable with no check is a promise nobody can verify, and
    :class:`Proposal` refuses one.
    """

    description: str
    check: str
    acceptance: str


@dataclass(frozen=True, slots=True)
class Proposal:
    """What we will do, for how much, by when, and how often we'll fix it.

    Every field here exists because leaving it out causes a specific
    argument later: what was included, what it cost, when it arrives,
    and how many rounds of corrections are free.
    """

    proposal_id: str
    prospect_id: str
    service: str
    price_cents: Cents
    deliverables: tuple[Deliverable, ...]
    turnaround_days: int
    revisions_included: int
    scope_excluded: tuple[str, ...] = ()
    #: What the client must supply. Work that cannot start because the
    #: client never sent the file is a scheduling problem invented at
    #: proposal time.
    client_must_supply: tuple[str, ...] = ()
    notes: str = ""

    def __post_init__(self) -> None:
        if self.price_cents <= 0:
            raise FailClosed("a proposal with no price is not a proposal")
        if not self.deliverables:
            raise FailClosed(
                f"proposal {self.proposal_id!r} promises nothing; a proposal "
                "with no deliverable cannot be delivered against")
        for item in self.deliverables:
            if not item.check.strip():
                raise FailClosed(
                    f"deliverable {item.description!r} names no check, so "
                    "nothing could verify it was done")
        if self.turnaround_days <= 0:
            raise FailClosed("a turnaround of nothing is not a commitment")
        if self.revisions_included < 0:
            raise FailClosed("a negative revision allowance is not a number")

    def deposit_cents(self, fraction: float = DEFAULT_DEPOSIT_FRACTION) -> Cents:
        """Rounded up. A deposit that rounds down leaves us short."""
        if not 0 < fraction < 1:
            raise FailClosed(
                f"a deposit fraction of {fraction!r} is not a deposit; it is "
                "either nothing or the whole price")
        return -(-self.price_cents * int(fraction * 10_000) // 10_000)

    def balance_cents(self, fraction: float = DEFAULT_DEPOSIT_FRACTION) -> Cents:
        return self.price_cents - self.deposit_cents(fraction)

    def render(self, fraction: float = DEFAULT_DEPOSIT_FRACTION) -> str:
        """What the client reads. Plain, and complete about the terms."""
        lines = [f"Proposal {self.proposal_id}: {self.service}", ""]
        lines.append("What you get:")
        lines += [f"  - {d.description} ({d.acceptance})"
                  for d in self.deliverables]
        if self.scope_excluded:
            lines += ["", "Not included:"] + [f"  - {s}"
                                              for s in self.scope_excluded]
        if self.client_must_supply:
            lines += ["", "What we need from you:"] + [
                f"  - {s}" for s in self.client_must_supply]
        lines += [
            "", f"Price: {fmt(self.price_cents)}",
            f"Deposit to begin ({fraction:.0%}): "
            f"{fmt(self.deposit_cents(fraction))}",
            f"Balance on delivery: {fmt(self.balance_cents(fraction))}",
            f"Turnaround: {self.turnaround_days} day(s) from the deposit "
            "and your files",
            f"Revisions included: {self.revisions_included}",
        ]
        if self.notes:
            lines += ["", self.notes]
        return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class Acceptance:
    """Evidence that the client agreed, and to what exactly.

    ``terms_digest`` binds the acceptance to the proposal as it stood.
    Without it, "they accepted" is a claim about an unspecified
    document, and a later edit to the proposal silently rewrites what
    was agreed.
    """

    accepted_by: str
    accepted_on: str
    method: str
    terms_digest: str

    def __post_init__(self) -> None:
        for name in ("accepted_by", "accepted_on", "method", "terms_digest"):
            if not str(getattr(self, name)).strip():
                raise FailClosed(
                    f"acceptance is missing {name}; an acceptance nobody can "
                    "attribute to a person, a date, a method and a specific "
                    "set of terms is not evidence of agreement")


@dataclass
class Engagement:
    """One agreement, from draft to paid. Holds state, not money."""

    engagement_id: str
    proposal: Proposal
    deposit_fraction: float = DEFAULT_DEPOSIT_FRACTION
    stage: Stage = Stage.DRAFTED
    acceptance: Acceptance | None = None
    #: Verified payment events only. Never a client's say-so.
    payments: list[dict] = field(default_factory=list)
    history: list[dict] = field(default_factory=list)
    #: Called with this engagement after every recorded change.
    #: :meth:`_note` is the one place every transition and every
    #: payment passes through, so hooking it there means durability
    #: cannot be lost by somebody adding a transition later and
    #: forgetting to save -- which is how a signed contract and a
    #: cleared deposit would come back from a restart as an
    #: unanswered proposal.
    on_change: Callable[["Engagement"], None] | None = field(
        default=None, repr=False, compare=False)

    @property
    def deposit_due_cents(self) -> Cents:
        return self.proposal.deposit_cents(self.deposit_fraction)

    @property
    def collected_cents(self) -> Cents:
        return sum(int(p["cents"]) for p in self.payments)

    @property
    def outstanding_cents(self) -> Cents:
        return max(0, self.proposal.price_cents - self.collected_cents)

    @property
    def deposit_satisfied(self) -> bool:
        return self.collected_cents >= self.deposit_due_cents

    @property
    def work_authorised(self) -> bool:
        """The gate. Verified money, or no work.

        A property rather than a stored flag on purpose: there is no
        field for a bug, an optimistic update or a client's assurance
        to set.
        """
        return (self.acceptance is not None and self.deposit_satisfied
                and self.stage not in (Stage.DECLINED, Stage.ABANDONED))

    # ---- moving it along ------------------------------------------------

    def send(self) -> "Engagement":
        self._require(Stage.DRAFTED)
        return self._move(Stage.SENT, "proposal sent")

    def accept(self, acceptance: Acceptance) -> "Engagement":
        if self.stage in (Stage.DECLINED, Stage.ABANDONED):
            raise FailClosed(
                f"engagement {self.engagement_id!r} is {self.stage.value}")
        if self.acceptance is not None:
            raise FailClosed(
                f"engagement {self.engagement_id!r} was already accepted on "
                f"{self.acceptance.accepted_on}; a second acceptance is two "
                "agreements for one job")
        self.acceptance = acceptance
        return self._move(Stage.ACCEPTED,
                          f"accepted by {acceptance.accepted_by} "
                          f"({acceptance.method})")

    def request_deposit(self) -> dict:
        if self.acceptance is None:
            raise FailClosed(
                "asking for a deposit before the terms are accepted invites "
                "a payment against terms nobody agreed")
        self._move(Stage.DEPOSIT_REQUESTED,
                   f"deposit of {fmt(self.deposit_due_cents)} requested")
        return {"engagement_id": self.engagement_id,
                "amount_cents": self.deposit_due_cents,
                "of_total_cents": self.proposal.price_cents,
                "fraction": self.deposit_fraction,
                "balance_cents": self.outstanding_cents - self.deposit_due_cents}

    def record_payment(self, event) -> "Engagement":
        """Record money that a rail verified. Idempotent by reference.

        ``event`` must carry ``verified`` truthy, a positive
        ``cents`` and an ``external_ref``. A duplicate reference is
        ignored rather than added, because a webhook delivered twice
        is the ordinary case and counting it twice is how a job looks
        paid when it is half paid.
        """
        verified = bool(getattr(event, "verified", None)
                        or (isinstance(event, dict) and event.get("verified")))
        cents = int(getattr(event, "cents", 0)
                    or (isinstance(event, dict) and event.get("cents")) or 0)
        ref = str(getattr(event, "external_ref", "")
                  or (isinstance(event, dict) and event.get("external_ref"))
                  or "")
        if not verified:
            raise FailClosed(
                "an unverified payment event is a claim that money arrived, "
                "not evidence that it did")
        if cents <= 0:
            raise FailClosed("a payment of nothing is not a payment")
        if not ref:
            raise FailClosed(
                "a payment event with no external reference cannot be "
                "deduplicated, so a repeated delivery would be counted twice")
        if any(p["external_ref"] == ref for p in self.payments):
            return self
        self.payments.append({"cents": cents, "external_ref": ref,
                              "at": _now()})
        if self.deposit_satisfied and self.stage is Stage.DEPOSIT_REQUESTED:
            self._move(Stage.DEPOSIT_RECEIVED,
                       f"deposit satisfied: {fmt(self.collected_cents)}")
        elif self.outstanding_cents == 0 and self.stage in (
                Stage.INVOICED, Stage.DELIVERED):
            self._move(Stage.PAID, "balance settled")
        else:
            self._note("payment", f"{fmt(cents)} received ({ref})")
        return self

    def authorise_work(self) -> "Engagement":
        """Only ever a consequence of the gate, never a way past it."""
        if not self.work_authorised:
            raise FailClosed(
                f"work is not authorised for {self.engagement_id!r}: "
                f"accepted={self.acceptance is not None}, deposit "
                f"{fmt(self.collected_cents)} of "
                f"{fmt(self.deposit_due_cents)}")
        return self._move(Stage.WORK_AUTHORISED, "deposit cleared")

    def delivered(self) -> "Engagement":
        if not self.work_authorised:
            raise FailClosed("delivering unauthorised work")
        return self._move(Stage.DELIVERED, "delivered")

    def invoiced(self) -> "Engagement":
        self._require(Stage.DELIVERED)
        return self._move(Stage.INVOICED,
                          f"balance of {fmt(self.outstanding_cents)} invoiced")

    def declined(self, why: str) -> "Engagement":
        return self._move(Stage.DECLINED, why or "declined")

    def abandoned(self, why: str) -> "Engagement":
        return self._move(Stage.ABANDONED, why or "abandoned")

    # ---- plumbing --------------------------------------------------------

    def _require(self, stage: Stage) -> None:
        if self.stage is not stage:
            raise FailClosed(
                f"engagement {self.engagement_id!r} is {self.stage.value}, "
                f"not {stage.value}")

    def _move(self, stage: Stage, why: str) -> "Engagement":
        if self.stage.terminal:
            raise FailClosed(
                f"engagement {self.engagement_id!r} is {self.stage.value}, "
                "which is terminal")
        self.stage = stage
        self._note("stage", f"{stage.value}: {why}")
        return self

    def _note(self, what: str, detail: str) -> None:
        self.history.append({"at": _now(), "what": what, "detail": detail})
        if self.on_change is not None:
            self.on_change(self)

    def as_dict(self) -> dict:
        """Everything needed to rebuild it, terms included.

        Distinct from :meth:`summary`, which is derived figures for a
        reader. The proposal and the acceptance are stored whole
        because they are the contract: an engagement restored with its
        stage but without the terms it was accepted against is a
        record that something was agreed and no record of what.
        """
        return {
            "engagement_id": self.engagement_id,
            "deposit_fraction": self.deposit_fraction,
            "stage": self.stage.value,
            "proposal": {
                "proposal_id": self.proposal.proposal_id,
                "prospect_id": self.proposal.prospect_id,
                "service": self.proposal.service,
                "price_cents": self.proposal.price_cents,
                "deliverables": [{"description": d.description,
                                  "check": d.check,
                                  "acceptance": d.acceptance}
                                 for d in self.proposal.deliverables],
                "turnaround_days": self.proposal.turnaround_days,
                "revisions_included": self.proposal.revisions_included,
                "scope_excluded": list(self.proposal.scope_excluded),
                "client_must_supply": list(self.proposal.client_must_supply),
                "notes": self.proposal.notes,
            },
            "acceptance": None if self.acceptance is None else {
                "accepted_by": self.acceptance.accepted_by,
                "accepted_on": self.acceptance.accepted_on,
                "method": self.acceptance.method,
                "terms_digest": self.acceptance.terms_digest,
            },
            "payments": [dict(p) for p in self.payments],
            "history": [dict(h) for h in self.history],
        }

    def summary(self) -> dict:
        return {
            "engagement_id": self.engagement_id,
            "prospect_id": self.proposal.prospect_id,
            "service": self.proposal.service,
            "stage": self.stage.value,
            "price_cents": self.proposal.price_cents,
            "deposit_due_cents": self.deposit_due_cents,
            "collected_cents": self.collected_cents,
            "outstanding_cents": self.outstanding_cents,
            "deposit_satisfied": self.deposit_satisfied,
            "work_authorised": self.work_authorised,
            "accepted": self.acceptance is not None,
            "payments": len(self.payments),
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def engagement_from_dict(doc: dict) -> Engagement:
    """Rebuild an engagement exactly as it stood.

    Nothing derived is read back: ``collected_cents``,
    ``deposit_satisfied`` and ``work_authorised`` are recomputed from
    the restored payments by the properties that always compute them.
    A stored ``work_authorised`` flag would be a field for a bug to
    set, which is the whole reason it is a property.
    """
    terms = doc["proposal"]
    proposal = Proposal(
        proposal_id=terms["proposal_id"], prospect_id=terms["prospect_id"],
        service=terms["service"], price_cents=int(terms["price_cents"]),
        deliverables=tuple(
            Deliverable(d["description"], d["check"], d["acceptance"])
            for d in terms["deliverables"]),
        turnaround_days=int(terms["turnaround_days"]),
        revisions_included=int(terms["revisions_included"]),
        scope_excluded=tuple(terms.get("scope_excluded", ())),
        client_must_supply=tuple(terms.get("client_must_supply", ())),
        notes=terms.get("notes", ""))
    accepted = doc.get("acceptance")
    return Engagement(
        engagement_id=doc["engagement_id"], proposal=proposal,
        deposit_fraction=float(doc.get("deposit_fraction",
                                       DEFAULT_DEPOSIT_FRACTION)),
        stage=Stage(doc["stage"]),
        acceptance=None if not accepted else Acceptance(**accepted),
        payments=[dict(p) for p in doc.get("payments", [])],
        history=[dict(h) for h in doc.get("history", [])])
