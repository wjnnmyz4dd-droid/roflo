"""The four things the owner is told, and nothing else.

Two of them are events -- a job was obtained, a payment arrived --
and two are a clock: a morning summary and an evening one. Together
they are the whole of "manage the business by exception": if none of
these arrives, nothing needed the owner.

This is a projection and a message. It is not an authority, it stores
no state of its own, and it decides nothing: every number below is
read from whichever authority owns it, and the summary is rebuilt
each time rather than accumulated. The one thing it is careful about
is money, because a summary is exactly where simulated revenue would
quietly become reported revenue. Collected money comes from
``Ledger.real_revenue_cents``, which excludes anything simulated, and
simulated money is named as simulated or not shown at all.

The times are the owner's: 06:50 and 16:30. Nothing here schedules
itself -- ``due`` says which slot a given local time falls in, and
the caller (a cron entry, or the operating loop) decides when to ask.
A module that started its own timer would be a daemon, and the one
thing this system does not need is another process with opinions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

#: The owner's timezone, named rather than assumed. Two summaries a
#: day at a fixed wall-clock time is a daylight-saving question
#: whether or not anybody wants it to be: a server on UTC that sends
#: at 11:50 is sending at 06:50 for half the year and 07:50 for the
#: other half. ZoneInfo carries the transition table, so 06:50 is
#: 06:50 in March and in July.
OWNER_TIMEZONE = "America/New_York"

#: When the owner wants a summary, in the timezone above.
MORNING = "06:50"
EVENING = "16:30"
SUMMARY_TIMES = (MORNING, EVENING)

#: How late a summary may be sent and still be that summary. A cron
#: that fires at 06:52 should send the 06:50 one; a process that wakes
#: at 09:00 having missed it should not pretend it is morning.
GRACE_MINUTES = 30

JOB_OBTAINED = "business.job_obtained"
PAYMENT_RECEIVED = "business.payment_received"
MORNING_SUMMARY = "business.morning_summary"
EVENING_SUMMARY = "business.evening_summary"

EVENTS = (JOB_OBTAINED, PAYMENT_RECEIVED, MORNING_SUMMARY, EVENING_SUMMARY)


def _minutes(hhmm: str) -> int:
    hours, _, minutes = hhmm.partition(":")
    return int(hours) * 60 + int(minutes)


def zone():
    """The owner's timezone, or UTC with the reason it is not theirs.

    A missing tz database is a real possibility on a stripped
    Windows host, and the wrong answer to it is to carry on in local
    time as though nothing happened -- that sends the morning
    summary at whatever hour the server happens to think it is.
    Falling back to UTC is wrong too, but it is wrong *visibly*: the
    summary then arrives at a consistent hour the owner can see is
    not theirs, and :func:`zone_problem` says so in the summary
    itself.
    """
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(OWNER_TIMEZONE)
    except Exception:
        return timezone.utc


def zone_problem() -> str:
    """Empty when the owner's timezone is actually available."""
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo(OWNER_TIMEZONE)
    except Exception as exc:
        return (f"this host has no timezone data for {OWNER_TIMEZONE} "
                f"({type(exc).__name__}), so summary times are UTC and "
                "will not be the owner's 06:50 and 16:30")
    return ""


def owner_now(at=None):
    """The current moment in the owner's timezone.

    ``at`` is for tests and for a caller that already has a time.
    A naive datetime is read as already being the owner's local
    time; an aware one is converted, which is the direction that
    matters -- a UTC-clocked daemon asking "what time is it for the
    owner" must not get its own hour back.
    """
    if at is None:
        return datetime.now(zone())
    if at.tzinfo is None:
        return at.replace(tzinfo=zone())
    return at.astimezone(zone())


def slot_for(at=None, *, grace_minutes: int = GRACE_MINUTES) -> str:
    """Which summary is due at this moment, in the owner's timezone.

    The dedupe key downstream is keyed on the owner's *date*, not the
    server's, so a run just after midnight UTC does not send
    yesterday evening's summary a second time under today's date.
    """
    moment = owner_now(at)
    return due(moment.strftime("%H:%M"), grace_minutes=grace_minutes)


def owner_date(at=None) -> str:
    """Today's date where the owner is. The dedupe key's other half."""
    return owner_now(at).strftime("%Y-%m-%d")


def due(local_time: str, *, grace_minutes: int = GRACE_MINUTES) -> str:
    """Which summary a local ``HH:MM`` falls in, or empty.

    Returns the slot rather than a boolean so the caller can key its
    dedupe on it. A time well past a slot returns nothing: a summary
    sent at noon is not the morning summary, it is a confusing
    message about a morning that has gone.
    """
    try:
        asked = _minutes(local_time)
    except (AttributeError, TypeError, ValueError):
        # AttributeError too: a None clock raised rather than
        # returning "no summary is due", and a scheduler that crashes
        # on a missing time sends nothing for the rest of the day.
        return ""
    for slot in SUMMARY_TIMES:
        start = _minutes(slot)
        if start <= asked <= start + max(0, grace_minutes):
            return slot
    return ""


#: The notifier stores a summary at 400 characters. Shortening is done
#: here against the same number rather than left to the column, because
#: a column cuts mid-word and this can drop a whole line instead.
LIMIT = 400

#: What a shortened summary says about what it left out. A summary that
#: silently dropped lines would be worse than a long one.
MORE = "- and {n} more line(s); open the control centre"


@dataclass
class Summary:
    """What the owner is told, and the parts it was built from."""

    slot: str
    needs_you: list = field(default_factory=list)
    lines: list = field(default_factory=list)

    @property
    def event(self) -> str:
        return MORNING_SUMMARY if self.slot == MORNING else EVENING_SUMMARY

    def text(self, *, limit: int = LIMIT) -> str:
        """The summary, whole or honestly shortened.

        The notifier stores a summary at 400 characters and composes a
        280-character message from it, both of which are deliberate:
        one is a database column and the other is a lock screen. What
        was not deliberate was what that did to this. A full summary
        ran past 400 and was cut mid-word -- the first version of this
        ended "no real payment has bee", which is the single sentence
        in the whole system that most needs to arrive intact.

        So it is shortened here, where the meaning of the lines is
        known, rather than at the column, where it is not. Lines are
        dropped whole, from the back, so what the owner reads is true
        and complete as far as it goes; what needs them is never
        dropped, because that is what the message is for; and the last
        line says how many were left out, so a shortened summary
        announces that it is one.
        """
        head = (f"{self.slot} summary. "
                + (f"{len(self.needs_you)} thing(s) need you."
                   if self.needs_you else "Nothing needs you."))
        wanted = [f"- {line}" for line in (self.needs_you + self.lines)]
        kept, dropped = [], 0
        used = len(head)
        for n, line in enumerate(wanted):
            # Leave room for the "and N more" line, which has to fit
            # too -- a truncation notice that is itself truncated
            # tells the owner nothing.
            tail = len(MORE.format(n=len(wanted) - n)) + 1
            if used + len(line) + 1 + (tail if n < len(wanted) - 1 else 0
                                       ) > limit:
                dropped = len(wanted) - n
                break
            kept.append(line)
            used += len(line) + 1
        if dropped:
            kept.append(MORE.format(n=dropped))
        body = "\n".join(kept)
        return f"{head}\n{body}" if body else head


def _money(cents) -> str:
    return f"${(cents or 0) / 100:,.2f}"


def compose(solvent, *, slot: str = MORNING) -> Summary:
    """Build the summary from the authorities that own each part."""
    summary = Summary(slot=slot)

    # --- what needs the owner, first, because that is the point ------
    try:
        pending = [n for n in solvent.notifier.unresolved()]
        if pending:
            summary.needs_you.append(
                f"{len(pending)} notification(s) not acknowledged")
    except Exception:
        summary.needs_you.append("the notification log could not be read")

    try:
        unresolved = solvent.applications.unresolved()
        if unresolved:
            summary.needs_you.append(
                f"{len(unresolved)} submission(s) whose outcome nobody has "
                "established -- these block new commitments")
    except Exception:
        pass

    problem = solvent.capacity.budget().problem()
    if problem:
        summary.needs_you.append(f"capacity is not configured: {problem}")

    # --- money, before the detail -------------------------------------
    # First of the ordinary lines, because the summary is shortened
    # from the back and this is the line that must never be the one
    # dropped. A summary that lost "no real payment has been received"
    # while keeping a pipeline census would be flattering by omission.
    try:
        real = solvent.ledger.real_revenue_cents()
        summary.lines.append(f"collected revenue: {_money(real)}")
        if not real:
            summary.lines.append(
                "no real payment has been received; any money in the "
                "ledger is simulated and is not counted here")
    except Exception:
        summary.lines.append("the ledger could not be read")

    # --- the pipelines -----------------------------------------------
    try:
        pipelines = solvent.capacity.pipelines()
    except Exception:
        pipelines = {}
    if pipelines:
        short = [name for name, row in pipelines.items()
                 if row["needs_replenishment"]]
        summary.lines.append(
            f"pipelines: "
            + ", ".join(f"{name} {row['committed']}/{row['target']}"
                        for name, row in pipelines.items()))
        if short:
            summary.lines.append(
                f"below the top-up line: {', '.join(short)}")
    else:
        summary.lines.append("no skill pipelines are configured")

    # --- capacity -----------------------------------------------------
    try:
        report = solvent.capacity.report()
        summary.lines.append(
            f"owed {report['owed_seconds']:.0f}s of "
            f"{report['budget_seconds']:.0f}s allowed, "
            f"{solvent.capacity.executing()} worker(s) busy")
    except Exception:
        summary.lines.append("capacity could not be read")

    return summary


def send_summary(solvent, *, slot: str, today: str, clock=None) -> str:
    """Compose and send one summary. Once per slot per day.

    The dedupe key carries the date and the slot, so a loop that asks
    every minute inside the grace window sends one message, and
    tomorrow's is a different message rather than a suppressed
    duplicate.
    """
    from . import notify as _notify

    summary = compose(solvent, slot=slot)
    return solvent.notifier.notify(
        event=summary.event, severity=_notify.INFO, summary=summary.text(),
        dedupe_key=f"summary:{today}:{slot}", clock=clock)


def job_obtained(solvent, *, job_id: str, capability: str = "",
                 amount_cents: int = 0, source: str = "",
                 contract_ref: str = "", deadline: str = "",
                 worker: str = "", queue_position=None,
                 client_ref: str = "", clock=None) -> str:
    """Tell the owner a job was won. Only a job, never a bid.

    The owner asked for exactly two routine notifications per job,
    and was specific about when this one fires: *after a real job or
    contract has been confirmed*, and not merely because a bid went
    out. Those are different events with different consequences --
    one is an offer DeskPilot made, the other is work it now owes --
    and a system that announced both would be telling the owner it
    had won something four times for every time it had.

    So ``contract_ref`` is required. It is whatever the award is
    identified by: a contract id, an accepted-bid reference, the
    owner's own confirmation. Without one this refuses to send and
    says why, rather than announcing a win nobody can point at.

    Everything else the owner listed is carried: where it came from,
    which skill, the client and job identifiers, what it pays, when
    it is due, which worker has it, and where it sits in that
    skill's queue.
    """
    from . import notify as _notify

    if not contract_ref:
        raise ValueError(
            "job_obtained needs a contract reference: this message means "
            "work has been won, and a submitted bid is not a won job. "
            "Pass the award identifier, or send nothing")

    lines = [f"job {job_id}"]
    if client_ref:
        lines.append(f"client {client_ref}")
    lines.append(f"contract {contract_ref}")
    if source:
        lines.append(f"via {source}")
    if capability:
        lines.append(f"skill {capability}")
    lines.append(f"value {_money(amount_cents)}")
    lines.append(f"due {deadline}" if deadline
                 else "no deadline stated by the client")
    lines.append(f"worker {worker}" if worker
                 else "no worker assigned yet")
    if queue_position is not None:
        lines.append(f"queue position {queue_position}")

    return solvent.notifier.notify(
        event=JOB_OBTAINED, severity=_notify.INFO,
        summary="Job obtained. " + "; ".join(lines) + ".",
        job_id=job_id, dedupe_key=f"{JOB_OBTAINED}:{job_id}", clock=clock)


def payment_received(solvent, *, job_id: str, gross_cents: int = 0,
                     fees_cents: int = 0, net_cents=None,
                     provider: str = "", confirmed_at: str = "",
                     evidence_ref: str = "", simulated: bool = False,
                     clock=None) -> str:
    """Tell the owner money arrived -- and never that it did otherwise.

    The owner's fields: job, gross, fees, net, provider, and the
    timestamp the receipt was confirmed at. ``evidence_ref`` is the
    seventh and is not optional for a real payment: this message may
    only follow authoritative provider evidence, so it carries the
    reference to that evidence or it is not sent.

    That rule is the reason this function can be trusted at all. An
    email saying "paid" and a customer saying "paid" are both
    things an attacker can write. A provider's own transaction
    record is not, and :mod:`solvent.paymentmonitor` is what reads
    it. This only announces what that established.

    A simulated payment is announced as simulated, in those words,
    with no money moved and this is not revenue in the body. It is
    the one message where a comfortable wording would do real harm.
    """
    from . import notify as _notify

    if net_cents is None:
        net_cents = max(0, int(gross_cents) - int(fees_cents))
    if not simulated and not evidence_ref:
        raise ValueError(
            "payment_received needs a reference to the provider evidence "
            "that confirmed it. An email or a customer message is not "
            "confirmation, and this message is what the owner will "
            "believe")

    if simulated:
        body = (f"SIMULATED payment for {job_id}: gross "
                f"{_money(gross_cents)}, fees {_money(fees_cents)}, net "
                f"{_money(net_cents)}. No money moved and this is not "
                "revenue.")
    else:
        body = (f"Payment received for {job_id}: gross "
                f"{_money(gross_cents)}, fees {_money(fees_cents)}, net "
                f"{_money(net_cents)}"
                + (f", via {provider}" if provider else "")
                + (f", confirmed {confirmed_at}" if confirmed_at else "")
                + f", evidence {evidence_ref}.")
    return solvent.notifier.notify(
        event=PAYMENT_RECEIVED, severity=_notify.INFO, summary=body,
        job_id=job_id, dedupe_key=f"{PAYMENT_RECEIVED}:{job_id}",
        clock=clock)
