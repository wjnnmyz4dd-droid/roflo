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

#: When the owner wants a summary, in their own local time.
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


@dataclass
class Summary:
    """What the owner is told, and the parts it was built from."""

    slot: str
    needs_you: list = field(default_factory=list)
    lines: list = field(default_factory=list)

    @property
    def event(self) -> str:
        return MORNING_SUMMARY if self.slot == MORNING else EVENING_SUMMARY

    def text(self) -> str:
        head = (f"{self.slot} summary. "
                + (f"{len(self.needs_you)} thing(s) need you."
                   if self.needs_you else "Nothing needs you."))
        body = "\n".join(f"- {line}" for line in
                         (self.needs_you + self.lines))
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

    # --- money, carefully ---------------------------------------------
    try:
        real = solvent.ledger.real_revenue_cents()
        summary.lines.append(f"collected revenue: {_money(real)}")
        if not real:
            summary.lines.append(
                "no real payment has been received; any money in the "
                "ledger is simulated and is not counted here")
    except Exception:
        summary.lines.append("the ledger could not be read")

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
                 clock=None) -> str:
    """Tell the owner a job was taken on."""
    from . import notify as _notify

    where = f" from {source}" if source else ""
    what = f" for {capability}" if capability else ""
    return solvent.notifier.notify(
        event=JOB_OBTAINED, severity=_notify.INFO,
        summary=(f"Job obtained{what}{where}: {_money(amount_cents)} "
                 f"agreed. It is in the pipeline and a worker will take "
                 "it when one is free."),
        job_id=job_id, dedupe_key=f"{JOB_OBTAINED}:{job_id}", clock=clock)


def payment_received(solvent, *, job_id: str, amount_cents: int = 0,
                     simulated: bool = False, method: str = "",
                     clock=None) -> str:
    """Tell the owner money arrived -- saying plainly when it did not.

    A simulated payment is announced as simulated. This is the one
    message where a comfortable wording would do real harm: an owner
    who reads "payment received" and believes it has a different
    picture of their business from the one the ledger has.
    """
    from . import notify as _notify

    if simulated:
        body = (f"SIMULATED payment of {_money(amount_cents)} recorded for "
                f"{job_id}. No money moved and this is not revenue.")
    else:
        body = (f"Payment received: {_money(amount_cents)} for {job_id}"
                + (f" via {method}" if method else "") + ".")
    return solvent.notifier.notify(
        event=PAYMENT_RECEIVED, severity=_notify.INFO, summary=body,
        job_id=job_id, dedupe_key=f"{PAYMENT_RECEIVED}:{job_id}",
        clock=clock)
