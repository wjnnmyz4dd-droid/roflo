"""Owner notifications: reaching a person who is not looking at the website.

**A notification is not an approval.** Nothing that arrives by text or phone
grants permission, and nothing here can be replied to in order to authorise
anything. Consequential actions are approved at a terminal holding the owner
key, and that is the whole reason taking over a phone number does not take over
the business. There is deliberately no reply-to-approve path: a text message is
an unauthenticated channel that anyone who can spoof a sender can write to.

**Provider-neutral.** The business logic here knows about *channels* — a text,
a phone call — and nothing about which company carries them. A provider is an
adapter satisfying :class:`Provider`, chosen by configuration. Wiring a
particular vendor's semantics into the escalation rules is how a company that
raises its prices becomes an architecture problem.

**It says as little as possible.** A text is delivered to a lock screen, relayed
through carriers, and backed up to whatever the phone syncs with. So a message
names what happened and where to look, never the details: no keys, no client
data, no artifact contents, no figures. The website is where the details live,
behind a password.

**Delivery is reported honestly.** ``SENT`` means a provider accepted it.
``DELIVERED`` is only ever recorded when a provider actually confirms delivery,
because a control centre that shows DELIVERED for "we handed it to an API" is
telling the owner something nobody established.
"""

from __future__ import annotations

from dataclasses import dataclass

from .audit import AuditLog, new_id, now
from .errors import FailClosed
from .resilience import redact
from .store import Store

AUTHORITY = "notify"

#: How much attention something deserves. Severity drives escalation, and
#: escalation costs the owner's attention — which is why routine events are
#: deliberately not allowed to reach a phone.
INFO = "INFO"
ACTION_REQUIRED = "ACTION_REQUIRED"
URGENT = "URGENT"
CRITICAL = "CRITICAL"
SEVERITIES = (INFO, ACTION_REQUIRED, URGENT, CRITICAL)
_RANK = {INFO: 0, ACTION_REQUIRED: 1, URGENT: 2, CRITICAL: 3}

#: The channels this knows about. Adding one is a channel, not a vendor.
WEBSITE = "WEBSITE"
SMS = "SMS"
VOICE = "VOICE"
CHANNELS = (WEBSITE, SMS, VOICE)

#: Where a notification has got to. ``DELIVERED`` is separate from ``SENT`` on
#: purpose: most providers confirm only that they accepted it for sending.
QUEUED = "QUEUED"
SENT = "SENT"
DELIVERED = "DELIVERED"
FAILED = "FAILED"
ACKNOWLEDGED = "ACKNOWLEDGED"
ESCALATED = "ESCALATED"
EXPIRED = "EXPIRED"
STATES = (QUEUED, SENT, DELIVERED, FAILED, ACKNOWLEDGED, ESCALATED, EXPIRED)

#: The default routing. Policy owns the real one; these are what applies when
#: the owner has recorded nothing, and they err towards the website.
DEFAULT_ROUTING = {
    INFO: (WEBSITE,),
    ACTION_REQUIRED: (WEBSITE, SMS),
    URGENT: (WEBSITE, SMS),
    CRITICAL: (WEBSITE, SMS, VOICE),
}

#: How long an URGENT notification may sit unacknowledged before a phone call.
#: Policy owns it; this is the fallback.
DEFAULT_ESCALATE_AFTER_SECONDS = 900

#: Environment variables carrying the things that must never be in Git, in the
#: policy document, in the audit log or on a page.
PHONE_ENV = "SOLVENT_OWNER_PHONE"
SMS_CREDENTIAL_ENV = "SOLVENT_SMS_CREDENTIAL"
VOICE_CREDENTIAL_ENV = "SOLVENT_VOICE_CREDENTIAL"


def mask(number: str) -> str:
    """``***-***-1234``. The only form of a phone number that may be displayed.

    The last four digits are what lets an owner confirm Solvent has the right
    number without the number itself existing in the policy document, the audit
    log, a backup or a page. The full value lives in the process environment and
    is read at dispatch, by the one component that has to have it.
    """
    digits = [c for c in (number or "") if c.isdigit()]
    if len(digits) < 4:
        return ""
    return "***-***-" + "".join(digits[-4:])


def configuration(policy) -> dict:
    """What is configured, as the website is allowed to see it."""
    return {
        "owner_phone": policy.get("notifications", "owner_phone_mask", default=""),
        "sms_provider": policy.get("notifications", "sms_provider", default=""),
        "voice_provider": policy.get("notifications", "voice_provider", default=""),
        "escalate_after_seconds": int(policy.get(
            "notifications", "escalate_after_seconds",
            default=DEFAULT_ESCALATE_AFTER_SECONDS)),
    }


class Provider:
    """What a channel adapter must do. Deliberately tiny.

    ``send`` returns ``(accepted, reference, confirms_delivery)``.
    ``confirms_delivery`` is the honest half: a provider that only acknowledges
    receipt of the request says ``False``, and this module then never reports
    DELIVERED for it.
    """

    name = "provider"
    channel = SMS
    confirms_delivery = False

    def send(self, *, to: str, body: str) -> tuple[bool, str, bool]:
        raise NotImplementedError


class RecordingProvider(Provider):
    """The default. Records what *would* be sent and sends nothing.

    This is what is configured everywhere until an owner deliberately wires a
    real one, and it is what the tests use. A test suite that can place a phone
    call is a test suite that will, at three in the morning, during a rerun
    nobody is watching.
    """

    name = "recording"

    def __init__(self, channel: str = SMS, *, accept: bool = True,
                 confirms_delivery: bool = False) -> None:
        self.channel = channel
        self.accept = accept
        self.confirms_delivery = confirms_delivery
        self.sent: list = []

    def send(self, *, to: str, body: str) -> tuple[bool, str, bool]:
        self.sent.append({"to": to, "body": body})
        if not self.accept:
            return False, "", False
        return True, f"rec_{len(self.sent)}", self.confirms_delivery


@dataclass(frozen=True, slots=True)
class Dispatch:
    """One attempt on one channel."""

    notification_id: str
    channel: str
    state: str
    detail: str = ""


class Notifier:
    """Decides which channel, records what happened, and holds no authority.

    It cannot approve anything, change a rule, or act on the incident it is
    telling the owner about. Its only power is to consume the owner's attention,
    which is exactly why deduplication and severity are enforced here rather
    than left to callers.
    """

    def __init__(self, store: Store, audit: AuditLog, policy, *,
                 providers=None, environ=None) -> None:
        self._db = store.for_authority(AUTHORITY)
        self._audit = audit
        self._policy = policy
        #: ``{channel: Provider}``. Empty means nothing can be sent, which is
        #: reported rather than hidden.
        self._providers = dict(providers or {})
        self._environ = environ

    # ------------------------------------------------------------ dispatch

    def notify(self, *, event: str, severity: str, summary: str,
               dedupe_key: str = "", incident_id: str = "", job_id: str = "",
               clock=None) -> str:
        """Record a notification and send it on whichever channels apply.

        ``dedupe_key`` is what stops a health check that runs every minute from
        sending a thousand identical texts. Deduplication is by *thing*, not by
        message: the same incident escalating is one conversation, and it
        remains able to escalate.
        """
        import time as _time

        if severity not in SEVERITIES:
            raise FailClosed(f"{severity!r} is not a severity; one of {SEVERITIES}")
        at = (clock or _time.time)()
        key = dedupe_key or f"{event}:{incident_id or job_id}"

        existing = self._db.query_one(
            "SELECT * FROM notifications WHERE dedupe_key = ? "
            "AND state NOT IN (?, ?) ORDER BY ts DESC LIMIT 1",
            (key, ACKNOWLEDGED, EXPIRED))
        if existing is not None and _RANK[severity] <= _RANK[existing["severity"]]:
            # Already told them, at least this loudly. Saying it again is noise,
            # and noise is how an owner learns to ignore the channel that
            # matters. An *escalation* is not deduplicated: the branch above
            # only fires when the new one is no more severe.
            return existing["id"]

        notification_id = new_id("ntf")
        body = self._body(event, summary)
        # ``raised_at`` is the escalation clock, and it is stored rather than
        # derived from ``ts``. Deriving it meant comparing a wall-clock ISO
        # timestamp against whatever clock the caller passed, so the two were
        # on different time bases and escalation could never be driven — it
        # simply never came due.
        self._db.execute(
            "INSERT INTO notifications(id,ts,raised_at,event,severity,summary,"
            "body,dedupe_key,incident_id,job_id,state,channels,provider_ref,"
            "acknowledged_at,escalated_at,detail) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'','','','')",
            (notification_id, now(), at, event, severity, redact(summary)[:400],
             body, key, incident_id, job_id, QUEUED, ""))
        self._db.commit()

        channels = self._routing()[severity]
        for channel in channels:
            if channel == WEBSITE:
                continue
            self._dispatch(notification_id, channel, body, at=at)
        self._audit.record(
            event="notify.raised", authority=AUTHORITY, initiator=AUTHORITY,
            why=redact(summary)[:200], job_id=job_id or None,
            decision=f"{severity}:{event}",
            result=f"channels {', '.join(channels)}",
            external_effect="owner notification")
        return notification_id

    def _routing(self) -> dict:
        configured = self._policy.get("notifications", "routing", default=None)
        if not configured:
            return DEFAULT_ROUTING
        return {level: tuple(configured.get(level, DEFAULT_ROUTING[level]))
                for level in SEVERITIES}

    def _body(self, event: str, summary: str) -> str:
        """What actually goes to a phone. Short, and deliberately incomplete.

        It names what happened and where to look. It carries no key, no client
        data, no figures and no artifact contents, because a text message is
        read off a lock screen and stored by whatever the phone syncs with.
        """
        return redact(f"Solvent: {event}. {summary} — open the control centre "
                      f"for details.")[:280]

    def _dispatch(self, notification_id: str, channel: str, body: str,
                  *, at: float) -> None:
        provider = self._providers.get(channel)
        if provider is None:
            self._record_state(notification_id, FAILED,
                               f"no {channel} provider is configured")
            return
        number = self._number()
        if not number:
            self._record_state(notification_id, FAILED,
                               "no owner phone number is configured")
            return
        accepted, reference, confirms = provider.send(to=number, body=body)
        if not accepted:
            self._record_state(notification_id, FAILED,
                               f"{provider.name} did not accept it")
            return
        # SENT, not DELIVERED. A provider that only acknowledges the request has
        # established that it received it, and nothing about whether a phone
        # ever did.
        self._record_state(notification_id, SENT,
                           f"accepted by {provider.name}", reference=reference,
                           channel=channel)
        if confirms:
            self._record_state(notification_id, DELIVERED,
                               f"{provider.name} confirmed delivery",
                               reference=reference, channel=channel)

    def _number(self) -> str:
        import os

        env = os.environ if self._environ is None else self._environ
        return env.get(PHONE_ENV, "")

    def _record_state(self, notification_id: str, state: str, detail: str,
                      *, reference: str = "", channel: str = "") -> None:
        row = self._db.query_one("SELECT channels FROM notifications WHERE id = ?",
                                 (notification_id,))
        channels = {c for c in (row["channels"] if row else "").split(",") if c}
        if channel:
            channels.add(channel)
        self._db.execute(
            "UPDATE notifications SET state = ?, detail = ?, provider_ref = ?, "
            "channels = ? WHERE id = ?",
            (state, redact(detail)[:300], reference, ",".join(sorted(channels)),
             notification_id))
        self._db.commit()

    # --------------------------------------------------------- the owner

    def acknowledge(self, notification_id: str, *, by: str = "owner") -> None:
        """The owner saw it. This is what stops an escalation, and nothing else.

        Acknowledging is not approving. It records that a person is aware; any
        action that follows still goes through whichever authority owns it.
        """
        self._row(notification_id)
        self._db.execute(
            "UPDATE notifications SET state = ?, acknowledged_at = ? WHERE id = ?",
            (ACKNOWLEDGED, now(), notification_id))
        self._db.commit()
        self._audit.record(
            event="notify.acknowledged", authority=AUTHORITY, initiator=by,
            why="the owner acknowledged a notification",
            input_ref=notification_id, result="escalation stopped",
            decision="ACKNOWLEDGED")

    def due_for_escalation(self, *, clock=None) -> list[dict]:
        """URGENT and CRITICAL notifications nobody has acknowledged in time."""
        import time as _time

        at = (clock or _time.time)()
        window = self._escalate_after()
        return [dict(row) for row in self._db.query(
            "SELECT * FROM notifications WHERE state IN (?, ?) "
            "AND severity IN (?, ?) AND ? - raised_at >= ? ORDER BY ts",
            (SENT, QUEUED, URGENT, CRITICAL, at, window))]

    def escalate(self, notification_id: str, *, clock=None) -> str:
        """Nobody answered the text, so try the phone. Governed, not hard-coded.

        The interval comes from Policy. Calling somebody is the loudest thing
        Solvent can do to a person, and how long it waits first is a business
        decision, not a constant in a module.
        """
        import time as _time

        row = self._row(notification_id)
        if row["state"] == ACKNOWLEDGED:
            raise FailClosed("it was acknowledged; escalating would be noise")
        at = (clock or _time.time)()
        self._dispatch(notification_id, VOICE, row["body"], at=at)
        after = self._row(notification_id)
        self._db.execute(
            "UPDATE notifications SET state = ?, escalated_at = ? WHERE id = ?",
            (ESCALATED if after["state"] != FAILED else FAILED, now(),
             notification_id))
        self._db.commit()
        self._audit.record(
            event="notify.escalated", authority=AUTHORITY, initiator=AUTHORITY,
            why="no acknowledgement within the policy interval",
            input_ref=notification_id, decision=row["severity"],
            result=self._row(notification_id)["state"])
        return self._row(notification_id)["state"]

    def _escalate_after(self) -> int:
        return int(self._policy.get("notifications", "escalate_after_seconds",
                                    default=DEFAULT_ESCALATE_AFTER_SECONDS))

    # --------------------------------------------------------------- reading

    def _row(self, notification_id: str) -> dict:
        row = self._db.query_one("SELECT * FROM notifications WHERE id = ?",
                                 (notification_id,))
        if row is None:
            raise FailClosed(f"no notification {notification_id!r}")
        return dict(row)

    def notification(self, notification_id: str) -> dict:
        return self._row(notification_id)

    def notifications(self, *, state: str = "", severity: str = "") -> list[dict]:
        sql, params, clauses = "SELECT * FROM notifications", [], []
        if state:
            clauses.append("state = ?")
            params.append(state)
        if severity:
            clauses.append("severity = ?")
            params.append(severity)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        return [dict(r) for r in self._db.query(sql + " ORDER BY ts DESC",
                                                tuple(params))]

    def unresolved(self) -> list[dict]:
        return [dict(r) for r in self._db.query(
            "SELECT * FROM notifications WHERE state NOT IN (?, ?) "
            "ORDER BY ts DESC", (ACKNOWLEDGED, EXPIRED))]
