"""Writing to people, and the much harder job of not writing to them.

Preparing a message is easy. The parts that matter are the ones that
stop a message: a suppression that outlives every restart, a contact
that was written to yesterday, an address nobody can say where they
got. Those are what separate outreach from spam, and in the US they
are also what the law is about.

Three rules shape everything here.

**Suppression wins, always, and it is checked last.** :func:`may_write`
evaluates the cheap reasons first and the suppression list last, so
that no earlier condition can short-circuit past it. A refusal names
which rule stopped it.

**Resubscription needs affirmative consent, recorded.** Not an absence
of complaint, not a new form submission that nobody can trace, and
certainly not an ordinary record update. :meth:`Suppressions.restore`
takes a :class:`Consent` with a source and a date and refuses without
one, because the failure mode -- a routine update quietly reactivating
somebody who asked to be left alone -- is both silent and the single
worst thing this module could do.

**Nothing here sends.** :class:`Draft` is text and a recipient.
Transmission goes through the Action Gate like every other external
effect, which means simulation mode, the egress allowlist and the
owner's approval all apply, and none of them is this module's to
bypass.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from enum import Enum

from .errors import FailClosed

#: Never write to the same person twice inside this window, whatever
#: the campaign thinks. A follow-up cadence is a plan; this is a floor.
MINIMUM_DAYS_BETWEEN_MESSAGES = 3

#: How many times to follow up before stopping. Silence is not consent
#: to keep writing.
MAX_FOLLOW_UPS = 2


class Reply(Enum):
    """What a reply meant. Classified conservatively, on purpose.

    ``UNCLEAR`` exists so that an ambiguous message becomes a question
    for a person rather than an optimistic reading. The expensive
    mistake is reading a brush-off as interest; the cheap one is asking.
    """

    INTERESTED = "INTERESTED"
    NOT_NOW = "NOT_NOW"
    NOT_INTERESTED = "NOT_INTERESTED"
    #: Anything that reads as "stop". Treated as an opt-out, not a mood.
    OPT_OUT = "OPT_OUT"
    UNCLEAR = "UNCLEAR"
    BOUNCED = "BOUNCED"


#: Phrases that mean stop. Matched generously: a false positive costs
#: one prospect, a false negative costs somebody their peace.
_STOP = (
    "unsubscribe", "opt out", "opt-out", "remove me", "take me off",
    "stop emailing", "stop contacting", "do not contact", "don't contact",
    "no longer wish", "leave me alone", "stop messaging",
)
_NEGATIVE = ("not interested", "no thanks", "no thank you", "we're all set",
             "we are all set", "not a fit", "no need")
_LATER = ("next quarter", "next year", "later", "circle back", "not right now",
          "revisit", "in a few months")
_POSITIVE = ("interested", "tell me more", "send details", "how much",
             "pricing", "let's talk", "lets talk", "call me", "sounds good")
_BOUNCE = ("mailer-daemon", "delivery status notification", "undeliverable",
           "address not found", "550 5.1.1", "recipient rejected")


def classify(text: str) -> Reply:
    """Read a reply. Stop beats everything, and unclear beats guessing.

    Order is the whole design. A message saying "this sounds
    interesting but please take me off your list" contains a positive
    phrase and a stop phrase; reading it as interest would be a
    catastrophe and reading it as stop merely loses a sale.
    """
    body = (text or "").lower()
    if any(p in body for p in _BOUNCE):
        return Reply.BOUNCED
    if any(p in body for p in _STOP):
        return Reply.OPT_OUT
    if any(p in body for p in _NEGATIVE):
        return Reply.NOT_INTERESTED
    if any(p in body for p in _LATER):
        return Reply.NOT_NOW
    if any(p in body for p in _POSITIVE):
        return Reply.INTERESTED
    return Reply.UNCLEAR


@dataclass(frozen=True, slots=True)
class Consent:
    """Affirmative, attributable permission to write to someone again.

    Every field is required. A consent record with no source is the
    same thing as no consent, and the whole point of this class is
    that restoring a suppressed address has to leave a trace somebody
    could audit.
    """

    email: str
    #: What the person actually did. "They filled in the contact form
    #: on 3 March asking for a quote", not "they seemed fine with it".
    affirmative_action: str
    source: str
    given_on: str

    def __post_init__(self) -> None:
        for name in ("affirmative_action", "source", "given_on"):
            if not str(getattr(self, name)).strip():
                raise FailClosed(
                    f"consent for {self.email!r} has no {name}; consent that "
                    "cannot be attributed is not consent, and an absence of "
                    "complaint is not an affirmative action")


@dataclass(frozen=True, slots=True)
class Suppressed:
    email: str
    why: str
    since: str
    #: Set only when a suppression was lifted, and by what.
    restored_by: Consent | None = None

    @property
    def active(self) -> bool:
        return self.restored_by is None


class Suppressions:
    """Addresses that must not be written to, and why.

    Normalised on the way in, so that a differently-cased or
    whitespace-padded copy of the same address is the same address.
    That is not tidiness: it is the difference between a suppression
    that holds and one that a later record quietly routes around.
    """

    def __init__(self, memory=None, audit=None) -> None:
        self._memory = memory
        self._audit = audit
        self._rows: dict[str, Suppressed] = {}
        if memory is not None:
            self._rehydrate()

    @property
    def durable(self) -> bool:
        return self._memory is not None

    @staticmethod
    def key(email: str) -> str:
        return (email or "").strip().lower()

    def _rehydrate(self) -> None:
        try:
            facts = self._memory.recall(kind=_KIND)
        except Exception:
            return
        # Newest first from `recall`. Reversed so that later events
        # win, which is the opposite of what the CRM's first restore
        # did and the reason that bug erased an opt-out.
        for fact in reversed(list(facts)):
            payload = fact.get("payload") or {}
            if not isinstance(payload, dict) or not payload.get("email"):
                continue
            consent = payload.get("restored_by")
            self._rows[self.key(payload["email"])] = Suppressed(
                email=payload["email"], why=payload.get("why", ""),
                since=payload.get("since", ""),
                restored_by=Consent(**consent) if consent else None)

    def _persist(self, row: Suppressed) -> None:
        if self._memory is None:
            return
        from .types import VerificationTier

        # Spelled out rather than taken from __dict__: Consent uses
        # slots and has none, and a rename on either side should fail
        # here at one place instead of writing a half-empty record.
        payload = {"email": row.email, "why": row.why, "since": row.since,
                   "restored_by": None}
        if row.restored_by is not None:
            payload["restored_by"] = {
                "email": row.restored_by.email,
                "affirmative_action": row.restored_by.affirmative_action,
                "source": row.restored_by.source,
                "given_on": row.restored_by.given_on}
        self._memory.learn(kind=_KIND, subject=self.key(row.email),
                           payload=payload,
                           evidence_ref=f"suppression:{self.key(row.email)}",
                           tier=VerificationTier.T1_DETERMINISTIC)

    def suppress(self, email: str, why: str) -> Suppressed:
        """Add an address. Idempotent, and never silently a no-op.

        Re-suppressing an address that was previously restored takes
        effect: somebody can consent once and change their mind, and
        the later decision is the one that counts.
        """
        if not why.strip():
            raise FailClosed("a suppression with no reason cannot be reviewed")
        row = Suppressed(email=email, why=why, since=_today())
        self._rows[self.key(email)] = row
        self._persist(row)
        self._note("suppressed", email, why)
        return row

    def restore(self, consent: Consent) -> Suppressed:
        """Lift a suppression, on recorded affirmative consent only."""
        if not isinstance(consent, Consent):
            raise FailClosed(
                "restoring a suppressed address requires a Consent record; "
                "an ordinary update must never reactivate somebody who "
                "asked to be left alone")
        key = self.key(consent.email)
        existing = self._rows.get(key)
        if existing is None:
            raise FailClosed(f"{consent.email!r} is not suppressed")
        row = Suppressed(email=existing.email, why=existing.why,
                         since=existing.since, restored_by=consent)
        self._rows[key] = row
        self._persist(row)
        self._note("restored", consent.email, consent.affirmative_action)
        return row

    def active(self, email: str) -> Suppressed | None:
        row = self._rows.get(self.key(email))
        return row if row is not None and row.active else None

    def all(self) -> list[Suppressed]:
        return list(self._rows.values())

    def _note(self, what: str, email: str, detail: str) -> None:
        if self._audit is None:
            return
        try:
            self._audit.record(
                event=f"outreach.{what}", authority="orchestrator",
                initiator="chief-of-staff", why=detail[:200],
                decision="RECORDED", result=self.key(email))
        except Exception:
            pass


_KIND = "outreach_suppression"


@dataclass(frozen=True, slots=True)
class Draft:
    """A message that has not been sent and cannot send itself."""

    to: str
    subject: str
    body: str
    prospect_id: str
    kind: str = "first_contact"
    #: Every commercial message has to carry one. Checked, not assumed.
    opt_out_instruction: str = (
        "If you would rather not hear from us, reply with STOP and we "
        "will not contact you again.")

    def __post_init__(self) -> None:
        if not self.opt_out_instruction.strip():
            raise FailClosed(
                "a commercial message with no way to opt out is not one we "
                "may send")
        if not self.to or "@" not in self.to:
            raise FailClosed(f"{self.to!r} is not an address")

    def rendered(self) -> str:
        return f"{self.body}\n\n--\n{self.opt_out_instruction}"


@dataclass
class Campaign:
    """What was written to whom, and when. The record outreach needs."""

    sent: list[dict] = field(default_factory=list)

    def record(self, email: str, kind: str, on: str = "") -> None:
        self.sent.append({"email": Suppressions.key(email), "kind": kind,
                          "on": on or _today()})

    def messages_to(self, email: str) -> list[dict]:
        key = Suppressions.key(email)
        return [m for m in self.sent if m["email"] == key]

    def last_message_to(self, email: str) -> str:
        messages = self.messages_to(email)
        return max((m["on"] for m in messages), default="")

    def follow_ups_to(self, email: str) -> int:
        return sum(1 for m in self.messages_to(email)
                   if m["kind"].startswith("follow_up"))


def may_write(email: str, *, suppressions: Suppressions,
              campaign: Campaign | None = None, today: str = "",
              prospect=None) -> str:
    """``""`` when it is allowed, otherwise why not.

    The suppression check is deliberately **last**. Putting it first
    would read as caution and would in fact be the risk: every earlier
    condition becomes a path that returns before it is consulted, and
    the day somebody reorders this file for tidiness, the suppression
    stops being checked at all. Last means unconditional.
    """
    if not email or "@" not in email:
        return f"{email!r} is not an address"
    if prospect is not None:
        if getattr(prospect.standing, "terminal", False):
            return (f"{prospect.company} is {prospect.standing.value}")
        if not prospect.contacts:
            return f"{prospect.company} has no contact with a recorded source"
    campaign = campaign or Campaign()
    if campaign.follow_ups_to(email) >= MAX_FOLLOW_UPS:
        return (f"already followed up {MAX_FOLLOW_UPS} time(s); silence is "
                "not consent to keep writing")
    last = campaign.last_message_to(email)
    if last:
        gap = _days_between(last, today or _today())
        if gap is None:
            return f"the last message to {email} has an unreadable date"
        if gap < MINIMUM_DAYS_BETWEEN_MESSAGES:
            return (f"written to {gap} day(s) ago; the floor is "
                    f"{MINIMUM_DAYS_BETWEEN_MESSAGES}")
    held = suppressions.active(email)
    if held is not None:
        return f"{email} is suppressed: {held.why}"
    return ""


def schedule_follow_up(email: str, *, after_days: int = 5,
                       today: str = "") -> str:
    base = date.fromisoformat(today) if today else datetime.now(timezone.utc).date()
    return (base + timedelta(days=after_days)).isoformat()


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _days_between(earlier: str, later: str) -> int | None:
    try:
        return (date.fromisoformat(later) - date.fromisoformat(earlier)).days
    except (TypeError, ValueError):
        return None
