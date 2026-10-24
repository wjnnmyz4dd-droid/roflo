"""The always-on runtime: startup checks, health, recovery, and the tick loop.

**This is a process concern, not a business authority.** It owns nothing a
business decision depends on. It starts the process safely, keeps it running,
notices when it is unhealthy, and stops it cleanly. Every decision it appears to
make is really a read of an authority that already exists:

* whether work may start at all — Policy's operating mode
* whether an action may leave the process — the Action Gate
* what a job's state is — the Job Orchestrator
* whether money may be spent — the Financial Governor

The division that keeps this honest is: **systemd owns process supervision, the
Job Orchestrator owns job supervision, and this module owns neither.** It is the
thing systemd runs, and the thing that asks the Orchestrator to take a turn.

The dangerous instinct in a 24/7 runtime is to make restarting clever. It is
deliberately not: a restart re-reads durable state and *stops* at anything
ambiguous. A process that comes back and resumes confidently is a process that
sends the invoice twice.
"""

from __future__ import annotations

import signal
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from datetime import datetime
from enum import Enum
from typing import Callable

from .audit import new_id
from .errors import FailClosed
from . import resilience as _res
from . import settlement
from .types import (BlockedOn, JobState, OperatingMode, PaymentState,
                    default_state_path, fmt)

#: Where a production database lives when nothing says otherwise. A 24/7 service
#: whose store defaults to ``:memory:`` would lose the ledger on every restart.
DEFAULT_DB = default_state_path("solvent.db")

RUNTIME = "runtime"

#: The business executor's lease, as audit events. Append-only and durable
#: already, which is what a lease that must survive the process holding it
#: needs to be.
BUSINESS_LEASE_TAKEN = "runtime.business_lease_taken"
BUSINESS_LEASE_RELEASED = "runtime.business_lease_released"


def _age_seconds(stamp) -> float | None:
    """How long ago, or ``None`` when the stamp cannot be read.

    ``None`` rather than zero: an unreadable timestamp must not read as
    "just now", which would make every lease look fresh and the staleness
    check do nothing.
    """
    from datetime import datetime, timezone

    try:
        when = datetime.fromisoformat(str(stamp))
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - when).total_seconds()


#: Where the liveness heartbeat is written. Read by deploy/solvent-watchdog.sh
#: from outside this process, which is the entire point of it being a file.
HEARTBEAT_PATH = default_state_path("heartbeat")


class Mode(Enum):
    """How this process is deployed. Same Solvent, different lifetime."""

    LOCAL = "LOCAL"          # a developer's machine; stops when the lid closes
    ALWAYS_ON = "ALWAYS_ON"  # a host that stays awake; survives the owner leaving

    @property
    def expects_durable_storage(self) -> bool:
        return self is Mode.ALWAYS_ON


class Health(Enum):
    """What a running process is actually capable of right now.

    The distinction that matters: **process alive is not Solvent healthy, and
    Solvent healthy is not Solvent ready to transact.** A supervisor that
    conflates them restarts a halted system into doing business.
    """

    HEALTHY = "HEALTHY"        # running, governed, nothing outstanding
    RECOVERING = "RECOVERING"  # restarted with unsettled state to reconcile
    DEGRADED = "DEGRADED"      # running, but something is wrong and visible
    BLOCKED = "BLOCKED"        # waiting on the owner or a client; correct, not broken
    HALTED = "HALTED"          # the kill switch is on; nothing may proceed
    NOT_READY = "NOT_READY"    # started, but startup checks have not passed

    @property
    def may_start_work(self) -> bool:
        return self in (Health.HEALTHY, Health.BLOCKED)


@dataclass(slots=True)
class Check:
    name: str
    ok: bool
    detail: str
    fatal: bool = False


@dataclass(slots=True)
class Startup:
    """The result of asking, before doing anything, whether it is safe to."""

    checks: list[Check] = field(default_factory=list)
    mode: Mode = Mode.LOCAL

    @property
    def fatal(self) -> list[Check]:
        return [c for c in self.checks if not c.ok and c.fatal]

    @property
    def warnings(self) -> list[Check]:
        return [c for c in self.checks if not c.ok and not c.fatal]

    @property
    def safe_to_run(self) -> bool:
        return not self.fatal

    def render(self) -> str:
        lines = [f"Startup checks ({self.mode.value})", "=" * 58]
        for c in self.checks:
            mark = "ok  " if c.ok else ("FATAL" if c.fatal else "warn")
            lines.append(f"  [{mark:<5}] {c.name}: {c.detail}")
        lines.append("")
        lines.append("safe to run" if self.safe_to_run
                     else f"REFUSING TO RUN: {len(self.fatal)} fatal problem(s)")
        return "\n".join(lines)


def startup_checks(solvent, *, mode: Mode = Mode.LOCAL) -> Startup:
    """Verify the things that must hold before the process does anything.

    Fatal means *do not run*: a broken audit chain or an ephemeral store in
    production are not conditions to work around. Everything else is a warning,
    because refusing to start over a missing Stripe secret would make the service
    impossible to bring up and inspect — and inspection is how the owner fixes it.
    """
    report = Startup(mode=mode)
    add = report.checks.append

    intact, detail = solvent.audit.verify_chain()
    add(Check("audit chain", intact, detail, fatal=True))

    durable = solvent.store.path != ":memory:"
    add(Check("durable storage",
              durable or not mode.expects_durable_storage,
              solvent.store.path if durable else "in-memory (nothing survives a restart)",
              fatal=mode.expects_durable_storage))

    try:
        solvent.store.raw_readonly("SELECT count(*) FROM jobs")
        add(Check("database readable", True, "schema present"))
    except Exception as exc:  # noqa: BLE001 - the whole point is to not start
        add(Check("database readable", False, str(exc), fatal=True))

    version = solvent.policy.version
    add(Check("policy", version > 0, f"version {version}", fatal=True))

    # Which deliverable formats this machine can give a second opinion on.
    #
    # Not fatal, and deliberately so: DeskPilot runs perfectly well with no
    # differential reader and declines a job needing one while it is still
    # a quote, which is the correct fail-closed behaviour and is already
    # tested. What was missing is that the owner only discovered it one
    # job at a time. A fresh Windows install has none of these -- the
    # installer runs no pip by design -- so saying which formats are
    # covered at boot is the difference between a known limitation and a
    # surprise.
    #
    # It never downgrades anything. T2 stays T2; this only reports what
    # can produce it.
    from . import independent

    covered, missing = [], []
    for extension in sorted(independent._DIFFERENTIAL_READERS):
        report_for = independent.available(extension=extension)
        usable = any(p.usable and not p.needs_a_person
                     for p in report_for.producers)
        (covered if usable else missing).append(extension)
    add(Check(
        "independent (T2) readers",
        not missing,
        (f"{len(covered)} format(s) covered: {', '.join(covered)}"
         if covered else "none installed")
        + (f"; no reader for {', '.join(missing)} — a job above C_HIGH in "
           "those formats will be declined while it is still a quote, not "
           "delivered unverified. Install the 'verification' extra into "
           "DeskPilot's environment to cover them." if missing else ""),
        fatal=False))

    op = solvent.policy.operating_mode
    add(Check("operating mode", op is OperatingMode.NORMAL,
              f"{op.value}" + ("" if op is OperatingMode.NORMAL
                               else " — no new work will start")))

    simulation = solvent.policy.get("egress", "simulation_only", default=True)
    add(Check("egress posture", True,
              "fail-closed (simulation_only)" if simulation
              else "LIVE — real external effects are enabled"))

    has_key = solvent.owner.available
    add(Check("owner channel", has_key,
              "signing key present" if has_key
              else f"{solvent.owner.key_refusal}: consequential approvals "
                   "fail closed"))

    unsettled = solvent.gate.unsettled()
    add(Check("unsettled external actions", not unsettled,
              "none" if not unsettled
              else f"{len(unsettled)} action(s) of unknown outcome — see `solvent recover`"))

    return report


def reconcile(solvent, *, initiator: str = RUNTIME) -> list[str]:
    """Bring durable state to a safe resting point after a restart.

    The rule this enforces is the whole reason the runtime is cautious: **an
    external action whose outcome was never written down must not be retried and
    must not be assumed to have succeeded.** It is neither. So the job that owns
    it is blocked on the owner, who is the only party who can go and look.

    Returns what it did, so a restart is never silent.
    """
    notes: list[str] = []
    for row in solvent.gate.unsettled():
        job_id = row["job_id"]
        note = (f"action {row['request_id']} ({row['action_class']} -> "
                f"{row['destination']}) was started and never settled; its real-world "
                "outcome is unknown")
        if not job_id:
            notes.append(f"{note}; no job attached")
            continue
        try:
            job = solvent.orchestrator.job(job_id)
        except Exception:  # noqa: BLE001 - a missing job is itself worth saying
            notes.append(f"{note}; job {job_id} not found")
            continue
        if job.state is JobState.BLOCKED or job.state.is_terminal:
            notes.append(f"{note}; {job_id} already {job.state.value}")
            continue
        solvent.orchestrator.block(
            job_id=job_id, blocked_on=BlockedOn.OWNER, initiator=initiator,
            why=f"{note}; the owner must confirm before anything is repeated")
        notes.append(f"{note}; blocked {job_id} on the owner")

    notes.extend(_reconcile_stranded_jobs(solvent, initiator=initiator))
    notes.extend(settlement.settle_paid_jobs(solvent, initiator=initiator))
    notes.extend(_reconcile_invoices(solvent, initiator=initiator))
    notes.extend(_reconcile_capacity(solvent, initiator=initiator))

    solvent.audit.record(
        event="runtime.reconciled", authority="orchestrator", initiator=initiator,
        why="restart reconciliation", decision="RECONCILED",
        result="; ".join(notes) if notes else "nothing outstanding")
    return notes


#: States in which a job has been delivered and the money is still owed.
#: COMPLETE is here too: a crash can leave a job marked complete on a payment
#: that was simulated, and an invoice missing is an invoice missing.
_BILLABLE = (JobState.AWAITING_PAYMENT, JobState.COMPLETE)


#: The rail a recovered invoice is opened on. Named rather than inherited
#: from anywhere, because the cycle that would have chosen one never ran.
_DEFAULT_RAIL = "recovered"


def _agreed_basis(solvent, job_id: str) -> str:
    """What this job was agreed to be billed on, from the audit log.

    Written by the business cycle before the work starts, so it survives a
    crash at any later point. Absent for a job that never got that far,
    and the caller treats absence as "ask the owner" rather than as a
    default.
    """
    for event in reversed(solvent.audit.events(job_id)):
        if event.get("event") == "billing.terms_agreed":
            return str(event.get("decision") or "")
    return ""


def _reconcile_invoices(solvent, *, initiator: str) -> list[str]:
    """Finish the invoices a crash left half-written, and no others.

    Opening a payment row is the second of two writes. The first is the
    billing obligation, which the cycle commits the moment it knows what the
    delivered work is owed for. A process that stops between them leaves the
    work delivered and unbilled, and nothing used to notice.

    Three things have to be true before an invoice is written here, and each
    is a separate durable record written by a different authority:

    * the **Ledger** holds an obligation for the job, so the amount, rail and
      terms are the ones the cycle computed and not a figure invented now;
    * the **orchestrator** has the job in a delivered state;
    * the **audit log** carries this job's ``delivery.completed`` event, which
      is the Action Gate's own record that the work actually went out.

    The state alone is not enough. A job can be moved by a repair, a replay or
    a test; the delivery event is written once, by the code that performed the
    delivery, and it is append-only. Requiring both is what keeps this from
    becoming a way to invoice work nobody delivered.

    Nothing here collects money. The recovered row is INVOICED -- billed, not
    paid -- and ``real_revenue_cents`` is untouched, because nothing in this
    process is entitled to say a client paid.
    """
    notes: list[str] = []
    ledger = getattr(solvent, "ledger", None)
    if ledger is None:
        return notes

    delivered = {event.get("job_id")
                 for event in solvent.audit.named(("delivery.completed",))
                 if event.get("job_id")}

    for obligation in ledger.unbilled_obligations():
        job_id = obligation["job_id"]
        try:
            job = solvent.orchestrator.job(job_id)
        except Exception:  # noqa: BLE001 - an obligation with no job is a fault
            notes.append(f"billing obligation for unknown job {job_id}; "
                         "left for the owner")
            continue
        if job.state not in _BILLABLE or job_id not in delivered:
            # Not a missing invoice: an obligation for work that has not been
            # delivered. Invoicing it would be billing for nothing.
            continue
        payment_id = ledger.open_payment(
            job_id=job_id, amount_cents=int(obligation["amount_cents"]),
            rail=obligation["rail"], currency=obligation["currency"],
            external_ref=obligation["external_ref"],
            client_ref=obligation["client_ref"])
        ledger.set_payment_state(payment_id, PaymentState.INVOICED)
        notes.append(f"{job_id} was delivered and unbilled; invoice "
                     f"{payment_id} written from the recorded obligation")

    # A delivered job with no obligation at all is the other half of the same
    # crash window, and it is the half that cannot be repaired automatically:
    # nobody wrote down what the work was worth, and this is not the place to
    # decide. It is escalated rather than guessed at or left quiet.
    for job in solvent.orchestrator.jobs():
        if job.state not in _BILLABLE or job.id not in delivered:
            continue
        if ledger.payment_for(job.id) or ledger.obligation_for(job.id):
            continue
        # A job delivered by recovery rather than by the business cycle
        # never reached the step that prices it. Where the agreed terms are
        # on file and they are a fixed price, the amount is not in doubt --
        # it is the quote the job was accepted on -- so the obligation is
        # written from that and the invoice follows on the next pass.
        # An hourly job is a different matter: its figure depends on hours
        # this process cannot reconstruct, and inventing one would be the
        # fabrication this whole exercise is about.
        basis = _agreed_basis(solvent, job.id)
        if basis == "FIXED_PRICE" and int(getattr(job, "quoted_cents", 0)) > 0:
            # Less whatever the client has already paid toward it. The
            # obligation the cycle writes is already net of a deposit;
            # this branch billed the gross, so the same client was
            # charged 28,515 by one recovery route and 22,812 by the
            # other. Found by comparing two crash traces.
            from .contracting import prepaid_for_job

            prepaid = prepaid_for_job(solvent, job.id)
            due = max(0, int(job.quoted_cents) - prepaid)
            ledger.record_billing_obligation(
                job_id=job.id, amount_cents=due,
                rail=_DEFAULT_RAIL, basis="FIXED_PRICE",
                client_ref=getattr(job, "client_id", "") or "")
            payment_id = ledger.open_payment(
                job_id=job.id, amount_cents=due, rail=_DEFAULT_RAIL,
                client_ref=getattr(job, "client_id", "") or "")
            ledger.set_payment_state(payment_id, PaymentState.INVOICED)
            notes.append(
                f"{job.id} was delivered by recovery and never priced; "
                f"invoiced {payment_id} at the agreed fixed price"
                + (f" less {prepaid} already paid" if prepaid else ""))
            continue
        why = ("delivered, but no billing obligation and no invoice were "
               "recorded, so what the client owes is not on file and only "
               "the owner can say what it should be"
               + (f" (agreed basis {basis}, which cannot be recomputed here)"
                  if basis else " (no agreed basis is on file either)"))
        try:
            solvent.resilience.record_incident(
                component="runtime", operation="invoice-reconciliation",
                error_class="UnbilledDelivery", signature=why,
                severity=_res.S_SERIOUS, job_id=job.id,
                trigger="a restart found a delivery with nothing owed on file",
                symptoms=why)
        except Exception:  # pragma: no cover - the recorder is best effort
            pass
        solvent.orchestrator.block(job_id=job.id, blocked_on=BlockedOn.OWNER,
                                   initiator=initiator, why=why)
        notes.append(f"{job.id} was delivered with nothing owed on file; "
                     "blocked on the owner")
    return notes


#: Work admitted and not yet finished. Queue depth is a figure the owner
#: reads to decide whether the shop is keeping up, so it counts what is
#: waiting rather than what exists.
_QUEUED = (JobState.ACCEPTED, JobState.QUALIFYING,
           JobState.AWAITING_OWNER_APPROVAL)

#: States a crash can strand a job in: work was under way and the process
#: went away before anything moved it on. None of them is a resting place,
#: so a job found here after a restart was interrupted.
#:
#: The first three were the original list and cover a crash during the
#: work. The last three cover a crash *before* it, which the list
#: missed: INTAKE, QUALIFYING and ACCEPTED are all passed through
#: inside a single ``run_business_cycle`` call, so nothing resumes a
#: job left in one of them either.
#:
#: Measured, by killing a process inside ``Capacity.reserve``: the job
#: stayed in QUALIFYING for ever, its 3,600-second EXECUTE reservation
#: survived, and ``reconcile`` did nothing at all -- so every crash
#: during qualification leaked an execution slot and stranded the job
#: where no watchdog looked. Repeat it a few times and the shop stops
#: taking work.
#:
#: ``AWAITING_OWNER_APPROVAL`` is deliberately **not** here. A job
#: waiting for a person is waiting, not interrupted, and blocking it
#: on the owner would be telling them their own decision had crashed.
_INTERRUPTED = (JobState.INTAKE, JobState.QUALIFYING, JobState.ACCEPTED,
                JobState.EXECUTING, JobState.VERIFYING,
                JobState.READY_FOR_DELIVERY)


def _reconcile_stranded_jobs(solvent, *, initiator: str) -> list[str]:
    """Nothing stays silently mid-flight across a restart.

    Measured before this existed: a job killed during verification sat in
    VERIFYING for ever. Nothing resumed it, nothing timed it out, nothing
    escalated it, and it kept its pipeline slot, so every crash
    permanently shrank the shop.

    It is **not** resumed here, and that is deliberate. Re-running a
    checker is safe in itself, but a job interrupted mid-flight may also
    have had an external effect nobody wrote down -- the loop above is
    what knows about those -- and deciding to carry on is a judgement
    about work already paid for. So this surfaces rather than guesses:
    the job is blocked on the owner with the state it was found in, which
    authorises nothing and cannot be mistaken for progress.

    Idempotent: a job already BLOCKED is left alone, so running this
    twice after a double restart changes nothing the second time.
    """
    notes: list[str] = []
    for job in solvent.orchestrator.jobs():
        if job.state not in _INTERRUPTED:
            continue
        why = (f"found in {job.state.value} with nothing running it, so it "
               "was interrupted; the owner decides whether the work resumes")
        try:
            solvent.resilience.record_incident(
                component="runtime", operation="restart-reconciliation",
                error_class="InterruptedJob", signature=why,
                severity=_res.S_SERIOUS, job_id=job.id,
                trigger="a restart found this job mid-flight", symptoms=why)
        except Exception:  # pragma: no cover - the recorder is best effort
            pass
        solvent.orchestrator.block(
            job_id=job.id, blocked_on=BlockedOn.OWNER, initiator=initiator,
            why=why)
        notes.append(f"{job.id} was stranded in {job.state.value}; "
                     "blocked on the owner")
    return notes


def _reconcile_capacity(solvent, *, initiator: str) -> list[str]:
    """Give back pipeline slots held by jobs that are no longer owed work.

    A crash between settling a job and releasing its reservation leaves
    the slot held by a job nobody is working on. Releasing by job id is
    idempotent, so this and the business cycle may both decide the same
    job has settled without the slot being released twice.

    Only settled jobs are touched. A job still owed work keeps its slot,
    and this never looks at an invoice: the debt outlives the slot.

    **The rule is "the work is finished", not "the money arrived".**
    A slot comes back when the job is terminal, or when it has been
    delivered and not reopened -- COMPLETE or AWAITING_PAYMENT.
    REVISION_REQUESTED is delivered too and deliberately keeps its
    slot, because a client asking for changes owes labour again.

    Note what is *not* used here: ``OCCUPIES_CAPACITY``. That set
    answers a different question -- which jobs count against the
    shop-wide committed total -- and it excludes INTAKE and QUALIFYING
    as well as the delivered states. Releasing on "not in that set"
    reads correctly and is wrong: it hands back the slot of a job that
    has only just arrived and is still heading for execution. A test
    that reserves against an INTAKE job caught it immediately.

    This used to release only terminal and COMPLETE jobs, which left
    AWAITING_PAYMENT holding an execution slot until a verified payment
    arrived. Measured consequence: five delivered jobs held five
    reservations and 18,000 owed seconds that reconciliation would not
    give back, and with no invoice issuable the shop seized for good
    after (3 x pipelines) jobs. One client who never paid could stop
    the whole business.

    Releasing the slot is a statement about *capacity* and nothing else.
    The money stays owed: the invoice, the payment row and the audit
    trail are untouched, the budget grant is not revoked, and the
    Governor's ``max_committed_unverified_cents`` still counts the job
    against unpaid exposure -- which is the control that bounds
    unpaid work, and the right one, because it is denominated in money
    rather than in worker-seconds.
    """
    notes: list[str] = []
    capacity = getattr(solvent, "capacity", None)
    if capacity is None:
        return notes
    for reservation in capacity.outstanding():
        job_id = reservation.job_id
        if not job_id:
            continue
        try:
            job = solvent.orchestrator.job(job_id)
        except Exception:  # noqa: BLE001 - a reservation for a job that is
            # not there is itself worth saying, and worth releasing
            capacity.release_job(job_id, why="no such job at reconciliation")
            notes.append(f"released a slot held by unknown job {job_id}")
            continue
        finished = job.state.is_terminal or (
            job.state.is_delivered
            and job.state is not JobState.REVISION_REQUESTED)
        if finished:
            released = capacity.release_job(
                job_id, why=f"job is {job.state.value} and owes no work")
            if released:
                notes.append(f"released {released} slot(s) held by "
                             f"{job_id}, which is {job.state.value}")
    return notes


def health(solvent, *, started: Startup | None = None) -> tuple[Health, str]:
    """What this process can currently do. Derived, never stored.

    Storing it would create a second opinion about the system's state that could
    disagree with the authorities. Every call reads them afresh.
    """
    if started is not None and not started.safe_to_run:
        return Health.NOT_READY, "; ".join(c.name for c in started.fatal)

    # `verify_tail`, not `verify_chain`. The first call in a process
    # re-derives the whole chain, as a startup check should; later
    # ones check only what has been appended since. This function is
    # called by four scheduled tasks, and the full derivation is
    # linear in the age of the installation -- measured at 12ms for a
    # thousand rows and 719ms for fifty thousand -- so asking for it
    # every time meant a three-month-old shop spending the larger
    # part of every minute re-hashing its own history. `diagnostics`
    # still runs the full check on its own cadence; see
    # `AuditLog.verify_tail` for exactly what each one catches.
    intact, detail = solvent.audit.verify_tail()
    if not intact:
        return Health.DEGRADED, f"audit chain: {detail}"

    op = solvent.policy.operating_mode
    if op is OperatingMode.HALT:
        return Health.HALTED, "kill switch is HALT; no work, no spend, no egress"

    unsettled = solvent.gate.unsettled()
    if unsettled:
        return (Health.RECOVERING,
                f"{len(unsettled)} external action(s) of unknown outcome")

    if op is not OperatingMode.NORMAL:
        return Health.DEGRADED, f"operating mode {op.value}"

    blocked = [j for j in solvent.orchestrator.jobs()
               if j.state is JobState.BLOCKED]
    if blocked:
        return (Health.BLOCKED,
                f"{len(blocked)} job(s) waiting: "
                + ", ".join(sorted({j.blocked_on.value for j in blocked
                                    if j.blocked_on})))
    return Health.HEALTHY, "governed, nothing outstanding"


@dataclass(slots=True)
class Task:
    """One periodic job of work. Never overlaps with itself."""

    name: str
    interval_s: float
    run: Callable[[], str]
    last_run: float | None = None
    failures: int = 0

    def due(self, at: float) -> bool:
        # None means never run. A service that has just started should check its
        # own health now, not in sixty seconds -- the first minute after a
        # restart is exactly when something is most likely to be wrong.
        return self.last_run is None or at - self.last_run >= self.interval_s


#: Default cadences, in seconds. Deliberately unhurried: this is a business that
#: takes hours per job, not a trading system, and a tight loop on a $5 host is
#: cost with no benefit. Every one is overridable from Policy.
DEFAULT_INTERVALS = {
    "chief_of_staff": 900.0,
    # Every task :meth:`Runtime.default_tasks` registers needs an entry here.
    # ``heartbeat`` did not have one, and ``interval`` looked names up with a
    # bare subscript, so registering the first task raised KeyError out of
    # ``serve`` -- after ``start`` had already recorded runtime.started with
    # health=HEALTHY. The runtime died before its first tick every time it was
    # launched, which left an owner with an audit log saying everything was
    # fine and a Diagnostics page with nothing on it, because the task that
    # records diagnostics had never been registered, let alone run.
    #
    # ``test_runtime`` now asserts the two sets match.
    "heartbeat": 60.0,         # the watchdog fails it at 180s; stay well inside
    # The owner is sitting in front of the control centre when they press a
    # button, so this is the one cadence chosen for a person waiting rather
    # than for a cheap host.
    "owner_intents": 30.0,
    # Money arriving. Brisker than the business cycle and slower than
    # the owner's buttons: nobody is sitting watching for it, but a
    # payment the books have not heard about is work the shop thinks
    # it is still owed for, and the Governor's unpaid-exposure
    # ceiling is computed from that.
    "payments": 120.0,
    # The two summaries have a half-hour grace window, so asking every
    # five minutes sends them within five minutes of their slot and
    # still sends them if a tick was missed. The dedupe key is the date
    # and the slot, so asking often does not mean telling often.
    "owner_briefing": 300.0,
    # Often enough to catch a stall within the hour, rarely enough
    # that a persistent one is not re-reported every minute. The
    # dedupe key does the rest.
    "supervision": 600.0,
    "discovery": 900.0,        # look for work every 15 minutes
    "reconciliation": 300.0,   # re-check payment and job state every 5
    "health": 60.0,            # notice trouble within a minute
    # Well inside diagnostics.REPORT_STALE_SECONDS, so a running system keeps
    # the control centre's report fresh with margin to spare -- and a system
    # that has stopped crosses into "stale" quickly enough to be noticed.
    "diagnostics": 300.0,
    # The business cycle. Unhurried on purpose: a job takes hours, the
    # Operator's own bounds cap how many it does per run, and a tighter
    # loop on a $5 host next to MetaTrader 5 is cost with no benefit.
    "business": 600.0,
    # Opening deals. A client who has asked for a price is waiting for
    # one, so this is brisker than the business cycle; it is still not
    # urgent, because the tick writes a number down rather than sending
    # it, and the sending has its own cadence rules.
    "dealmaking": 600.0,
    # Carrying an agreed deal forward: proposal, then admission once the
    # deposit has cleared. Same cadence as dealmaking and for the same
    # reason -- a client who has agreed a price and paid a deposit is
    # waiting on work, and the step costs a database write.
    "contracting": 600.0,
}

#: Used when a task is registered with no cadence anywhere. That is a
#: programming mistake and a test catches it, but the runtime meeting one at
#: boot should not be fatal: ``run_due`` already treats a task that *fails* as
#: weather and degrades only after three strikes, while a task that failed to
#: *register* took the whole process down, because registration happens before
#: that protected loop. Four tasks running and one complaint in the log beats a
#: business that will not start.
FALLBACK_INTERVAL = 300.0

#: Consecutive failures of one task before the runtime calls itself degraded.
#: One failure is weather; several in a row is a fault.
FAILURE_THRESHOLD = 3


class Runtime:
    """The long-running process. Ticks tasks, watches health, stops cleanly.

    It does **not** decide what work to do. It asks the authorities on a schedule
    and records what they say. If this class ever grows a judgement about whether
    a job is worth taking, that judgement belongs in the Governor instead.
    """

    def __init__(self, solvent, *, mode: Mode = Mode.LOCAL,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.solvent = solvent
        self.mode = mode
        self._clock = clock
        self._sleep = sleep
        self._stopping = threading.Event()
        self.started: Startup | None = None
        self.tasks: list[Task] = []
        self.log: list[str] = []
        self.stop_reason = ""
        #: Tasks that had no cadence and fell back to one. Empty on a correct
        #: build. Recorded rather than merely survived, so the condition is
        #: visible in ``self.log`` instead of being inferred from a task
        #: ticking at a rate nobody chose.
        self.unscheduled: list[str] = []
        #: This process's claim on being the business executor. Random per
        #: process, so a second process cannot mistake another's lease for
        #: its own and run a second business loop against one database.
        self._lease_id = f"{RUNTIME}:{new_id('lease')}"
        #: The last bounded business run, for the control centre. Empty
        #: until one has happened -- which is itself the answer to "is the
        #: business cycle running?" and must not be confused with zero
        #: jobs completed.
        self._business: dict = {}
        #: Refusals this process has already written down, keyed by
        #: (tick, subject, reason). A connector re-decides on every
        #: pass, and a decision re-made on facts that have not
        #: changed is not a new decision: recording it again fills
        #: the audit log with repetition -- measured at 2,016
        #: identical UNPROFITABLE rows in eighteen simulated days --
        #: and makes the real entries harder to find. In memory, so
        #: a restart records each refusal once more, which is when
        #: an owner is most likely to want to see it.
        self._said_already: set = set()

    # ------------------------------------------------------------- lifecycle
    def start(self) -> Startup:
        """Run startup checks and reconcile. Raises rather than run unsafely."""
        self.started = startup_checks(self.solvent, mode=self.mode)
        if not self.started.safe_to_run:
            problems = "; ".join(f"{c.name}: {c.detail}" for c in self.started.fatal)
            raise FailClosed(f"refusing to start: {problems}")

        notes = self._recover()
        for note in notes:
            self._say(f"recovery: {note}")

        self.solvent.audit.record(
            event="runtime.started", authority="orchestrator", initiator=RUNTIME,
            why=f"{self.mode.value} runtime start", decision="STARTED",
            result=f"health={health(self.solvent, started=self.started)[0].value}")
        return self.started

    def _recover(self) -> list[str]:
        """Resume what is safely recoverable, then reconcile the rest.

        ``reconcile`` on its own is the safety net: it escalates
        everything it finds mid-flight. That is the right floor and the
        wrong ceiling -- work that stopped during a recompute can simply
        be re-run, and a shop that escalates every interrupted job to a
        person after every restart is a shop that stops.

        Resumption re-runs a worker, so it needs somewhere to write the
        deliverable. The owner names that place, as ``recovery.work_root``
        in policy. Until they do, this reconciles only, and says so where
        it can be read: a runtime that invented a directory to write
        client deliverables into would be worse than one that asks.
        """
        from .harness import recover_after_restart

        root = self.solvent.policy.get("recovery", "work_root", default="")
        interrupted = [job for job in self.solvent.orchestrator.jobs()
                       if job.state in _INTERRUPTED]
        if not root:
            if interrupted:
                self._say(
                    f"automatic resumption is not configured, so "
                    f"{len(interrupted)} interrupted job(s) go to the owner "
                    f"instead; set recovery.work_root to have them re-run")
            return reconcile(self.solvent)
        outcome = recover_after_restart(self.solvent, workdir=str(root),
                                        initiator=RUNTIME)
        return ([f"{r['job_id']} found in {r['found_in']}: {r['outcome']}"
                 for r in outcome["resumed"]] + outcome["reconciled"])

    def stop(self, reason: str = "requested") -> None:
        """Ask the loop to finish its current tick and come back."""
        self.stop_reason = reason
        self._stopping.set()

    @property
    def stopping(self) -> bool:
        return self._stopping.is_set()

    def install_signal_handlers(self) -> None:
        """SIGTERM is how systemd says stop. Treat it as a request, not a kill."""
        for sig, name in ((signal.SIGTERM, "SIGTERM"), (signal.SIGINT, "SIGINT")):
            signal.signal(sig, lambda *_args, _n=name: self.stop(_n))

    def shutdown(self) -> None:
        """Leave everything recoverable. Never invent a completion.

        There is deliberately nothing here that finishes a job. A service being
        stopped is not evidence that work was done, and a shutdown path that
        tidied jobs into COMPLETE would be lying in the ledger.
        """
        self.solvent.audit.record(
            event="runtime.stopped", authority="orchestrator", initiator=RUNTIME,
            why=self.stop_reason or "shutdown", decision="STOPPED",
            result=f"health={health(self.solvent)[0].value}; "
                   "in-flight jobs left in their recorded states")
        self.solvent.store.close()

    # ----------------------------------------------------------------- tasks
    def interval(self, name: str) -> float:
        configured = self.solvent.policy.get("runtime", "intervals", name)
        if configured:
            return float(configured)
        if name not in DEFAULT_INTERVALS:
            self.unscheduled.append(name)
            self._say(f"{name} has no configured cadence; running it every "
                      f"{FALLBACK_INTERVAL:.0f}s")
            return FALLBACK_INTERVAL
        return DEFAULT_INTERVALS[name]

    def add_task(self, name: str, run: Callable[[], str]) -> Task:
        task = Task(name=name, interval_s=self.interval(name), run=run)
        self.tasks.append(task)
        return task

    def default_tasks(self) -> None:
        """What a first-revenue Solvent needs to do on a timer."""
        self.add_task("heartbeat", self._tick_heartbeat)
        self.add_task("health", self._tick_health)
        self.add_task("reconciliation", self._tick_reconcile)
        self.add_task("discovery", self._tick_discovery)
        self.add_task("diagnostics", self._tick_diagnostics)
        self.add_task("owner_intents", self._tick_owner_intents)
        self.add_task("payments", self._tick_payments)
        self.add_task("owner_briefing", self._tick_owner_briefing)
        self.add_task("supervision", self._tick_supervision)
        # Before the business cycle and after supervision, for the same
        # reason the business cycle is: opening a deal takes work on, and
        # a tick that takes work on runs behind the one that notices work
        # already stuck.
        self.add_task("dealmaking", self._tick_dealmaking)
        self.add_task("contracting", self._tick_contracting)
        self.add_task("business", self._tick_business)
        self.add_task("chief_of_staff", self._tick_chief_of_staff)

    def _tick_heartbeat(self) -> str:
        """Touch a file so something outside Solvent can tell it is working.

        This is the one health signal Solvent cannot produce while broken and
        cannot fake while healthy: writing it requires getting far enough
        through a tick to write it.

        It exists because ``solvent health`` runs *inside* Solvent. A process
        that is wedged, deadlocked, out of memory or simply not running reports
        nothing — and nothing looks exactly like quiet-because-everything-is-fine.
        A process cannot notice its own absence, so the noticing is done by a
        timer on the host reading this file's age.

        Failing to write it is not fatal to the tick. The watchdog treating a
        missing heartbeat as an outage is the correct outcome, and a Solvent that
        crashed because it could not write a diagnostic file would be turning a
        monitoring problem into an availability problem.
        """
        path = self.solvent.policy.get("runtime", "heartbeat_path",
                                       default=HEARTBEAT_PATH)
        try:
            target = Path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"{time.time():.0f}\n", encoding="utf-8")
        except OSError as exc:
            return f"could not write the heartbeat at {path}: {exc}"
        return f"heartbeat written to {path}"

    def _tick_health(self) -> str:
        state, why = health(self.solvent, started=self.started)
        return f"{state.value}: {why}"

    def _tick_diagnostics(self) -> str:
        """Run the self-checks and record them.

        The control centre cannot run these itself -- it holds no authorities --
        so this is what keeps its Diagnostics page truthful. If this task stops,
        the page says the report is stale rather than showing the last good one
        as though it were current.
        """
        report = self.solvent.diagnostics.record()
        return f"{len(report.findings)} check(s); worst {report.worst}"

    def _tick_payments(self) -> str:
        """Hand any spooled payment deliveries to the rail and the Ledger.

        The ingress was complete and unreachable. ``StripeRail``
        verifies a signature with a replay window and a constant-time
        compare, ``Ledger.apply_payment_event`` decides what an
        authentic event means for the books, and
        ``harness.ingest_payment_spool`` moves the bytes between
        them. The only caller of the last one was ``solvent payments
        ingest``, typed at a terminal.

        So ``PaymentState.PAID`` was reachable in a running shop only
        through the business cycle's ``simulate_payment`` branch. A
        real client's money arrived in a real account and the books
        never heard: every delivered job stayed AWAITING_PAYMENT, the
        Governor went on counting it against
        ``max_committed_unverified_cents``, and the shop eventually
        refused new work because it believed nobody had ever paid it.

        This is the same gap ``_tick_owner_intents`` was written to
        close, and it is closed the same way. **It is plumbing, not
        an authority.** It verifies nothing and decides nothing: an
        unverifiable delivery is moved aside by the ingestor and
        reported, never applied.

        Unlike the business cycle this is not gated on simulation,
        and deliberately. A payment that genuinely arrived is a fact
        about the world whether or not DeskPilot is allowed to act on
        it, and refusing to write down a fact does not make it less
        true. Reading the spool sends nothing and spends nothing.
        """
        from .harness import PAYMENT_SPOOL, ingest_payment_spool

        spool = self.solvent.policy.get("runtime", "payment_spool",
                                        default="") or PAYMENT_SPOOL
        try:
            results = ingest_payment_spool(self.solvent, spool=spool)
        except Exception as problem:
            # A broken spool must not stop the tick, and must not be
            # silent either: "no payments" and "we could not look"
            # are different answers and an owner needs to tell them
            # apart.
            self.solvent.audit.record(
                event="payment.spool_unreadable", authority="ledger",
                initiator=RUNTIME, why=f"{type(problem).__name__}: {problem}",
                decision="NOT_READ", result=str(spool))
            return f"could not read the payment spool: {problem}"
        # Settling runs whether or not a delivery arrived this tick: a
        # payment applied by `solvent payments ingest` at a terminal,
        # or by the run before a crash, is money the job has not been
        # closed against yet.
        closed = settlement.settle_paid_jobs(self.solvent, initiator=RUNTIME)
        if not results:
            return ("no payments waiting" if not closed
                    else f"no payments waiting; {len(closed)} job(s) closed")
        refused = [r for r in results if r.get("outcome") == "REFUSED"]
        applied = len(results) - len(refused)
        for item in refused:
            self._notify_owner(
                event="payment.refused",
                summary=f"{item.get('delivery')}: {item.get('why', '')}"[:400],
                dedupe_key=f"payment:{item.get('delivery')}")
        parts = [f"{applied} applied"]
        if refused:
            parts.append(f"{len(refused)} refused")
        if closed:
            parts.append(f"{len(closed)} job(s) closed")
        return ", ".join(parts)

    def _tick_owner_briefing(self, now=None) -> str:
        """Send the 06:50 and 16:30 summaries when their slot comes round.

        The summaries existed as a function nobody called, which is the
        same as not existing: an owner managing by exception needs the
        exceptions to arrive on their own. This is the timer that makes
        them arrive.

        It asks every five minutes and sends at most one message per
        slot per day, because the dedupe key is the date and the slot.
        Outside both windows it does nothing and says so, which is the
        normal result for twenty-two hours of the day.

        A summary that cannot be sent is recorded and does not stop the
        tick loop: an owner who is not told the news is worse off than
        one who is told it late, and both are better off than one whose
        system fell over trying.

        ``now`` is for tests, which otherwise pass or fail depending on
        what hour of the day they are run at -- and a test whose result
        comes from the wall clock is not testing the slot logic.
        """
        from . import briefing

        # The owner's timezone, not the server's. A host on UTC that
        # asked itself the time would send the morning summary at
        # 06:50 UTC, which is 01:50 or 02:50 in New York depending on
        # the month -- and the month is exactly the part a naive
        # clock gets wrong twice a year.
        moment = briefing.owner_now(now)
        slot = briefing.slot_for(moment)
        if not slot:
            return (f"no summary due at {moment.strftime('%H:%M %Z')}; "
                    f"slots are {', '.join(briefing.SUMMARY_TIMES)} "
                    f"{briefing.OWNER_TIMEZONE}")
        try:
            # Keyed on the owner's date too. A server crossing
            # midnight UTC mid-evening would otherwise file the 16:30
            # summary under tomorrow and send it again.
            outcome = briefing.send_summary(
                self.solvent, slot=slot,
                today=briefing.owner_date(moment))
        except Exception as exc:
            return f"the {slot} summary could not be sent: {exc!r}"[:300]
        return f"{slot} {briefing.OWNER_TIMEZONE} summary: {outcome}"

    def _tick_supervision(self) -> str:
        """Look for work that has stopped moving, and escalate once.

        The supervisor fixes nothing. It notices, names the authority
        that owns the fix, and interrupts the owner only for the
        critical ones -- a WARN belongs in the next summary, which
        wants reading today rather than answering now.
        """
        supervisor = getattr(self.solvent, "supervisor", None)
        if supervisor is None:
            return "no supervisor is wired"
        findings = supervisor.check()
        if not findings:
            return "nothing has stalled"
        sent = supervisor.escalate(findings, notifier=self.solvent.notifier)
        worst = sorted({f.severity for f in findings})
        return (f"{len(findings)} finding(s) ({', '.join(worst)}); "
                f"{len(sent)} escalated")

    def _tick_owner_intents(self) -> str:
        """Apply what the owner asked for from the control centre.

        The control centre cannot change anything itself -- it holds no
        authorities, by construction -- so it writes an intent to a spool and
        something on this side has to pick it up. Nothing did. The spool was
        drained only by ``solvent intents consume``, typed at a terminal, which
        left an owner pressing a button in a browser and watching nothing
        happen, with no indication that anything was waiting.

        :func:`~solvent.harness.consume_owner_intents` decides what may be
        applied, and this does not second-guess it: a SAFE intent executes,
        because its worst outcome is Solvent doing less; a CONSEQUENTIAL one is
        refused without an owner approval minted by the Owner Channel, whose
        key the website does not hold. Draining on a timer therefore does not
        widen what the website can cause -- it only stops a decision the owner
        already made from sitting in a file.
        """
        from .harness import QUEUED, consume_owner_intents

        spool = self.solvent.policy.get("runtime", "owner_intent_spool")
        results = consume_owner_intents(
            self.solvent, **({"spool": spool} if spool else {}))
        if not results:
            return "no owner intents waiting"
        # Three outcomes, counted separately because they mean different
        # things to an owner watching: something happened, something is
        # waiting on their approval, something was turned down.
        refused = [r for r in results if r.get("outcome") == "REFUSED"]
        queued = [r for r in results if r.get("outcome") == QUEUED]
        applied = [r for r in results
                   if r.get("outcome") not in ("REFUSED", QUEUED)]
        parts = [f"{len(applied)} applied"]
        if queued:
            parts.append(f"{len(queued)} waiting for an owner approval")
        if refused:
            verbs = sorted({r.get("verb") or "unreadable" for r in refused})
            parts.append(f"{len(refused)} refused ({', '.join(verbs)})")
        return "; ".join(parts)

    def _tick_reconcile(self) -> str:
        notes = reconcile(self.solvent)
        return f"{len(notes)} item(s) reconciled" if notes else "nothing outstanding"

    def _tick_discovery(self) -> str:
        """Look for work — but only if the authorities currently permit it.

        Discovery is the first thing shed when the machine is under
        pressure, and it is first for a reason the owner's ladder and
        this one agree on: a job nobody has been promised costs
        nothing to not find. Shedding accepted work is breaking a
        promise, so it is last and this tick never does it.

        The capacity gate already computed that ladder. Until now
        nothing read it -- ``pressure()["relief"]`` was advice with
        no audience, which is the same as no advice. This is the
        audience.

        **This tick does not poll, and that is a gap, not a design.**
        It reports which sources are live and stops. Two things make
        the obvious wiring wrong rather than merely missing:

        * ``Discovery.poll`` returns only candidates it has not seen
          before, and ``run_business_cycle`` polls the source itself
          at its ``discover`` stage. A tick that polled first would
          consume the postings and leave every later cycle stopping
          at "discover" with "returned no opportunities".
        * Reaching ACCEPTED means ``qualification.select``, which
          mints a job and applies the Governor's verdict. A job
          created here with nothing to execute it sits in ACCEPTED
          holding a capacity slot -- a stranded job, manufactured on
          a schedule.

        So found work still enters through the business cycle, which
        polls, qualifies and selects as one transaction. What is
        genuinely missing is an unattended route for work found on a
        board rather than ordered through the storefront, and it is
        missing for a real reason: a board posting carries no client
        materials, so there is nothing to run it on. The outbound
        route therefore goes board -> CRM -> negotiation ->
        engagement -> the client sends their file -> order, which is
        what ``solvent.contracting`` now joins up.

        The filter here used to read ``readiness == "PERMITTED"``.
        No member of ``discovery.Readiness`` has that value -- the
        nine are RESEARCH_REQUIRED through PRODUCTION_READY -- so the
        comparison was false for every source that could ever exist,
        and this tick answered "no permitted work source" on every
        run of every deployment, including one whose source was
        PRODUCTION_READY. It now asks ``source_report``, which is the
        authority that already answers "is this a live route to paid
        work", rather than mirroring an enum in a string literal.
        """
        state, why = health(self.solvent, started=self.started)
        if not state.may_start_work:
            return f"skipped: {state.value} ({why})"
        if self.solvent.policy.operating_mode.blocks_new_work:
            return "skipped: operating mode blocks new work"
        shed = self._shed()
        if "stop speculative discovery" in shed:
            # Not an error and not a failure: this is the system
            # protecting a machine it shares with MetaTrader 5, and
            # saying so plainly so a quiet discovery log can be told
            # apart from a broken one.
            return ("skipped: backing off under pressure — "
                    + "; ".join(shed))
        report = self.solvent.discovery.source_report()
        live = [row for row in report if row["suitability"] == "ACTIVE"]
        if not live:
            # Names them and says what each is waiting on. "No
            # permitted work source" was the old answer, and it was
            # the *only* answer this line could ever give -- see the
            # docstring. An owner who has registered a source and
            # cannot see why it is idle is owed the reason.
            blocked = ", ".join(f"{row['name']} {row['suitability']}"
                                for row in report) or "none registered"
            return f"no live work source: {blocked}"
        return (f"{len(live)} live source(s): "
                + ", ".join(row["name"] for row in live))

    def _shed(self) -> list:
        """What the capacity gate says to stop doing, in order.

        A failure to read pressure sheds everything. The alternative
        is to carry on discovering because the thing that would have
        told us to stop could not be consulted, and this system has
        found that shape of bug three times now.
        """
        try:
            return list(self.solvent.capacity.pressure().get("relief") or [])
        except Exception:
            return ["stop speculative discovery",
                    "pressure could not be read, which is not the same as "
                    "no pressure"]

    # ------------------------------------------------------------------ loop
    def run_due(self, now: float | None = None) -> list[str]:
        """Run whatever is due once. Never runs a task concurrently with itself."""
        at = self._clock() if now is None else now
        results = []
        for task in self.tasks:
            if not task.due(at) or self.stopping:
                continue
            task.last_run = at
            try:
                outcome = task.run()
                task.failures = 0
            except Exception as exc:  # noqa: BLE001 - one bad tick is not a crash
                task.failures += 1
                outcome = f"FAILED ({task.failures}): {exc}"
                if task.failures >= FAILURE_THRESHOLD:
                    self.solvent.audit.record(
                        event="runtime.task_failing", authority="orchestrator",
                        initiator=RUNTIME, why=task.name, decision="DEGRADED",
                        result=outcome)
            results.append(f"{task.name}: {outcome}")
            self._say(f"{task.name}: {outcome}")
        return results

    def run_forever(self, *, tick_s: float = 1.0, max_ticks: int | None = None) -> str:
        """The service main loop. Returns why it stopped.

        ``max_ticks`` exists so the loop is testable without waiting on a clock.
        """
        ticks = 0
        while not self.stopping and (max_ticks is None or ticks < max_ticks):
            self.run_due()
            ticks += 1
            if self.stopping:
                break
            self._sleep(tick_s)
        return self.stop_reason or "loop finished"

    # ------------------------------------------------- what is actually true
    #: The states the control centre has to be able to tell apart. A
    #: heartbeat distinguishes none of them: a process can be alive, its
    #: scheduler running, and its business cycle switched off, and
    #: reporting that as "operational" is the specific lie this exists to
    #: prevent.
    BUSINESS_STATES = (
        "SERVICE_RUNNING", "SCHEDULER_RUNNING", "SCHEDULER_PAUSED",
        "CYCLE_EXECUTING", "NO_ELIGIBLE_OPPORTUNITIES", "WORKER_FAILURE",
        "RECOVERY_REQUIRED", "HALTED", "SIMULATION", "EXTERNAL_DISABLED",
    )

    def business_status(self) -> dict:
        """What the business cycle is actually doing, for the dashboard.

        Every field is read from state that already exists -- policy, the
        audit log, the scheduler's own task record. Nothing here decides
        anything, and nothing here is allowed to say "operational": it
        reports the parts, and whoever renders them says what they add up
        to. A single cheerful boolean is how a disconnected business cycle
        went unnoticed in the first place.
        """
        from .operation import Operator

        task = next((t for t in self.tasks if t.name == "business"), None)
        simulation = bool(self.solvent.policy.get(
            "egress", "simulation_only", default=True))
        enabled = bool(self.solvent.policy.get(
            "operations", "continuous_enabled", default=False))
        halted = bool(self.solvent.policy.get(
            "operations", "halted", default=False))
        allowed, why = Operator(self.solvent).permitted()
        last = dict(self._business or {})

        states = {
            "SERVICE_RUNNING": self.started is not None and not self.stopping,
            "SCHEDULER_RUNNING": task is not None and not self.stopping,
            "SCHEDULER_PAUSED": task is None or not enabled or self.stopping,
            "CYCLE_EXECUTING": bool(self._business_lease_holder()),
            "NO_ELIGIBLE_OPPORTUNITIES":
                last.get("stopped") == "NOTHING_TO_DO",
            "WORKER_FAILURE": bool(task and task.failures),
            "RECOVERY_REQUIRED": bool(self._stranded_job_ids()),
            "HALTED": halted,
            "SIMULATION": simulation,
            "EXTERNAL_DISABLED": simulation,
        }
        return {
            "states": states,
            "business_cycle_connected": task is not None,
            "business_cycle_enabled": enabled,
            "may_run_now": allowed,
            "why_not": "" if allowed else why,
            "last_cycle_started": last.get("started_at", ""),
            "last_cycle_completed": last.get("finished_at", ""),
            "last_successful_cycle": last.get("finished_at", "")
                                     if last.get("completed") else "",
            "last_failure": last.get("why", "") if task and task.failures
                            else "",
            "consecutive_task_failures": task.failures if task else 0,
            "queue_depth": self._queue_depth(),
            "active_workers": self._active_workers(),
            "executor_lease": self._business_lease_holder() or "free",
            "interval_seconds": task.interval_s if task else None,
        }

    def _stranded_job_ids(self) -> list:
        """Jobs sitting where only a crash leaves them."""
        try:
            return [j.id for j in self.solvent.orchestrator.jobs()
                    if j.state in _INTERRUPTED]
        except Exception:                       # a read must not break a page
            return []

    def _queue_depth(self) -> int:
        try:
            return sum(1 for j in self.solvent.orchestrator.jobs()
                       if j.state in _QUEUED)
        except Exception:
            return 0

    def _active_workers(self) -> int:
        try:
            return sum(1 for j in self.solvent.orchestrator.jobs()
                       if j.state is JobState.EXECUTING)
        except Exception:
            return 0

    # -------------------------------------------------------- the business
    #: A lease older than this belonged to a process that is gone. Long
    #: enough that a legitimately slow run is never evicted underneath
    #: itself: the Operator's own ``max_run_seconds`` defaults to 300 and
    #: the owner's policy can only narrow it.
    BUSINESS_LEASE_SECONDS = 3600.0

    def _tick_dealmaking(self) -> str:
        """Turn clients who asked for a price into open negotiations.

        The connection this codebase was missing. Everything either side
        of it existed -- the Finder found work, the Qualification judged
        it, the negotiation engine held the arithmetic -- and nothing
        joined them, so every negotiation in the repository had been
        opened by a line of test code. A business whose deals are opened
        by its test harness opens none.

        **It decides no price.** ``dealmaking.open_ready`` asks the CRM
        who replied, the registry whether the skill may reach a client,
        Capacity whether there is room and the owner's recorded costs
        what the floor is, then calls the existing engine. Every refusal
        in the log is some other authority's words.

        **It sends nothing**, which is why it is not gated on
        ``egress.simulation_only`` the way the business cycle is. That
        gate exists because the business cycle commits money and
        executes work; this writes an asking price into the book. Gating
        it on simulation would mean price-setting stopped on the day the
        owner went live, which is backwards. Telling the client is a
        separate act down the outreach path, with its own consent rules
        that this does not touch.

        Three conditions, each refusing on its own: health, the owner's
        continuous-operation opt-in, and the business lease -- the same
        lease the business cycle takes, so that a second process cannot
        open a second set of deals against one database.
        """
        from .dealmaking import open_ready
        from .operation import Operator

        state, why = health(self.solvent, started=self.started)
        if not state.may_start_work:
            return f"skipped: {state.value} ({why})"
        if self.solvent.policy.operating_mode.blocks_new_work:
            return "skipped: operating mode blocks new work"

        allowed, why = Operator(self.solvent).permitted()
        if not allowed:
            return f"not run: {why}"

        limit = int(self.solvent.policy.get(
            "dealmaking", "max_opened_per_tick", default=1) or 1)

        lease = self._take_business_lease()
        if lease is None:
            return "not run: another executor holds the business lease"
        try:
            results = open_ready(self.solvent, limit=limit, initiator=RUNTIME)
        finally:
            self._release_business_lease(lease)

        if not results:
            return "nobody is waiting on a price"
        opened = [r for r in results if r.opened]
        refused = [r for r in results if not r.opened]
        for refusal in refused:
            if not self._first_time("dealmaking", refusal.prospect_id,
                                    refusal.refusal.value):
                continue
            self.solvent.audit.record(
                event="negotiation.not_opened", authority="orchestrator",
                initiator=RUNTIME, why=refusal.why[:200],
                decision=refusal.refusal.value, result=refusal.prospect_id)
        return (f"{len(opened)} opened, {len(refused)} refused: "
                + "; ".join(r.summary() for r in results))

    def _tick_contracting(self) -> str:
        """Carry an agreed deal to a proposal, and a paid one to an order.

        The other half of the join. ``dealmaking`` turned a qualified
        opportunity into a price; this turns an agreed price into a
        contract and a cleared deposit into an order. Before both
        existed, ``Proposal(`` and ``Engagement(`` appeared nowhere in
        ``solvent/`` outside the function that rebuilds a stored one,
        so the Chief of Staff said PROPOSE and ADMIT_WORK into a
        codebase where nothing performed either.

        **It admits nothing itself.** ``contracting.admit`` places an
        order with the storefront and stops. ``next_work_order`` is
        still the only route from an order into the pipeline, and
        Qualification, the Governor, the Action Gate and the owner's
        threshold are all downstream of it and untouched. A second
        route in would be a second way to get two jobs for one payment.

        **It sends nothing and clears no deposit.**
        ``Engagement.work_authorised`` is a property computed from
        verified payment events, read here and never set.

        Not gated on ``egress.simulation_only``, for dealmaking's
        reason: the business cycle is, so with simulation off an order
        placed here simply waits. An order waiting for a client who
        has paid is the correct state; refusing to write it down is
        not.
        """
        from .contracting import carry_forward
        from .operation import Operator

        state, why = health(self.solvent, started=self.started)
        if not state.may_start_work:
            return f"skipped: {state.value} ({why})"
        if self.solvent.policy.operating_mode.blocks_new_work:
            return "skipped: operating mode blocks new work"

        allowed, why = Operator(self.solvent).permitted()
        if not allowed:
            return f"not run: {why}"

        limit = int(self.solvent.policy.get(
            "contracting", "max_steps_per_tick", default=1) or 1)

        lease = self._take_business_lease()
        if lease is None:
            return "not run: another executor holds the business lease"
        try:
            results = carry_forward(self.solvent, limit=limit,
                                    initiator=RUNTIME)
        finally:
            self._release_business_lease(lease)

        if not results:
            return "no deal is waiting on a step"
        done = [r for r in results if r.done]
        for refusal in (r for r in results if not r.done):
            if not self._first_time("contracting", refusal.prospect_id,
                                    refusal.refusal.value):
                continue
            self.solvent.audit.record(
                event="contracting.not_carried", authority="orchestrator",
                initiator=RUNTIME, why=refusal.why[:200],
                decision=refusal.refusal.value, result=refusal.prospect_id)
        return (f"{len(done)} carried, {len(results) - len(done)} refused: "
                + "; ".join(r.summary() for r in results))

    def _tick_business(self) -> str:
        """Do some business, inside the bounds the owner set.

        **This does not decide anything.** It owns no Finder, no Governor,
        no Action Gate and no admission rule; it calls
        ``run_business_cycle``, which asks every existing authority in
        order, through ``operation.Operator``, which is where the owner's
        limits already live. Adding a second engine here is precisely the
        thing that would make "what decides whether DeskPilot takes a
        job?" a question with two answers.

        Four conditions, each refusing on its own:

        * **Simulation.** Refused unless ``egress.simulation_only`` is on.
          The authorisation for this loop is explicitly simulation-only,
          and a loop that keeps running when somebody turns simulation off
          is a loop that went live without being asked to.
        * **Opt-in.** ``Operator.permitted`` is false unless the owner set
          ``operations.continuous_enabled``. Off is the default, so wiring
          this in changes nothing until the owner says so.
        * **The halt.** Checked by the Operator before every job, not
          after.
        * **One executor.** The lease below.

        ``Task`` never overlaps with *itself*, but that is one process.
        The lease is what stops a second process -- a hand-run ``solvent
        serve``, a scheduled task that fired twice, an operator debugging
        beside the service -- from running a second business loop against
        the same database.
        """
        from .operation import Operator, StopReason

        if not self.solvent.policy.get("egress", "simulation_only",
                                       default=True):
            return ("not run: this loop is authorised for simulation only "
                    "and egress.simulation_only is off")

        operator = Operator(self.solvent)
        allowed, why = operator.permitted()
        if not allowed:
            return f"not run: {why}"

        lease = self._take_business_lease()
        if lease is None:
            return "not run: another executor holds the business lease"
        try:
            report = operator.run(self._one_business_job)
        finally:
            self._release_business_lease(lease)

        summary = report.summary()
        self.solvent.audit.record(
            event="runtime.business_ran", authority="orchestrator",
            initiator=RUNTIME,
            why=f"bounded business run: {summary['why']}",
            decision=summary["stopped"],
            # "delivered", not "completed". The loop's own measure of a
            # good job is delivered-and-billed; `completed` means the
            # money also arrived, which happens on the client's
            # schedule and not this run's. Reporting `completed` here
            # told an owner nothing was being done on every run where
            # the shop behaved correctly. Both are carried, so neither
            # question has to be guessed at from the other.
            result=f"{summary['delivered']} of {summary['attempted']} "
                   f"delivered ({summary['completed']} settled), "
                   f"{fmt(summary['committed_cents'])} invoiced")
        self._business = {**summary, "finished_at": self._clock()}
        # Only when it genuinely did nothing. A run that did three jobs
        # and then found the queue empty also stops with NOTHING_TO_DO,
        # and reporting that as "no eligible opportunity" hid the three.
        if report.stopped is StopReason.NOTHING_TO_DO and not summary["attempted"]:
            return "no eligible opportunity"
        return (f"{summary['delivered']}/{summary['attempted']} delivered; "
                f"stopped: {summary['stopped']}")

    def _one_business_job(self):
        """One customer order, through the business cycle that exists.

        Returns ``None`` when the shop is empty, which is what
        :meth:`Operator.run` requires in order to stop with
        NOTHING_TO_DO. Returning a stand-in "nothing happened" object
        instead would look to the loop like a job that failed, and three
        of those in a row end the run as if something were wrong.

        **This function was broken from the day it was written.** It read
        ``self.solvent.policy.owner``, an attribute ``PolicyStore`` does
        not have, and it omitted ``source_file`` and ``workdir``, which
        ``run_business_cycle`` declares without defaults. Either one
        raises on the first call. It was never caught because every test
        that exercised the business tick -- including the acceptance run
        that reported a hundred opportunities and fifty-three deliveries
        -- replaced this method with a substitute. Certifying a function
        by swapping it out certifies the substitute.

        So the three arguments now come from somewhere real:

        ``owner_identity``
            from ``governance.owner_identities``, which is the list
            ``PolicyStore.is_owner`` actually checks. Absent, it raises:
            a defaulted owner identity is a forged one.

        ``source_file``
            the file the customer attached to their order. Checked for
            existence before the job is admitted, not when the worker
            opens it.

        ``workdir``
            a directory per order under the runtime's work root.
        """
        from .harness import run_business_cycle
        from .workorder import next_work_order

        order = next_work_order(self.solvent, workroot=self._workroot())
        if order is None:
            return None
        return run_business_cycle(self.solvent, **order.as_cycle_kwargs())

    def _tick_chief_of_staff(self) -> str:
        """Work out what each relationship needs next. Does none of it.

        A tenth task, and deliberately a toothless one: it reads the
        book, records anything only the owner can answer, and returns.
        It admits no job, sends no message and moves no money, so it
        is not a second business engine however often it runs.

        It exists because a coordination layer nothing calls is the
        defect this codebase keeps producing. The pipeline being empty
        is a real answer and the ordinary one.
        """
        chief = getattr(self.solvent, "chief_of_staff", None)
        if chief is None:
            return "not run: no chief of staff is configured"
        try:
            pipeline = chief.pipeline()
        except Exception as problem:            # a read must not stop a tick
            return f"could not read the pipeline: {problem}"
        escalations = [r for r in pipeline if r.owner_must_decide]
        for item in escalations:
            self.solvent.audit.record(
                event="chief_of_staff.escalation", authority="orchestrator",
                initiator=RUNTIME, why=item.why[:200],
                decision="OWNER_MUST_DECIDE", result=item.prospect_id)
            # And actually tell them. The audit log is a record, not a
            # channel: an escalation that only ever landed there was a
            # question nobody was asked, and this tick runs every
            # fifteen minutes, so it would have been asked ninety-six
            # times a day and answered none of them. The Notifier
            # dedupes by key, which is what makes that safe.
            self._notify_owner(
                event="chief_of_staff.escalation",
                summary=f"{item.prospect_id}: {item.why}"[:400],
                dedupe_key=f"chief_of_staff:{item.prospect_id}")
        self._record_pipeline(chief, pipeline)
        total = len(chief.crm.all())
        if not total:
            return "no prospects"
        return (f"{total} prospect(s); {len(escalations)} need the owner")

    def _first_time(self, tick: str, subject: str, reason: str) -> bool:
        """Is this the first time this process has refused this, this way?

        A refusal that changes reason is new and is recorded again;
        the same reason on the same subject is the same decision
        re-made against facts that have not moved.
        """
        key = (tick, str(subject), str(reason))
        if key in self._said_already:
            return False
        self._said_already.add(key)
        # The subject's *other* reasons stay recorded: a client who
        # was UNPROFITABLE and is now NO_CAPACITY has had two
        # different things said about them and both are worth having.
        return True

    def _notify_owner(self, *, event: str, summary: str,
                      dedupe_key: str) -> None:
        """Ask the Notifier, and never let a failed ask stop the tick.

        Swallowing the exception would be wrong and recording nothing
        would be worse, so a notification that could not be sent is
        written to the audit log as a notification that could not be
        sent. An owner reading the log can tell "nothing was wrong"
        from "we could not tell you".
        """
        from .notify import ACTION_REQUIRED

        notifier = getattr(self.solvent, "notifier", None)
        if notifier is None:
            return
        try:
            notifier.notify(event=event, severity=ACTION_REQUIRED,
                            summary=summary, dedupe_key=dedupe_key)
        except Exception as problem:
            self.solvent.audit.record(
                event="runtime.notification_failed", authority="orchestrator",
                initiator=RUNTIME, why=dedupe_key, decision="NOT_SENT",
                result=str(problem)[:200])

    def _record_pipeline(self, chief, pipeline) -> None:
        """Write down what the coordinator worked out, so it can be seen.

        Without this the whole tick was invisible: the figures were
        computed every fifteen minutes and dropped, the control centre
        had no way to reach a ChiefOfStaff (it holds a read-only
        database connection and no Solvent), and the owner's only
        evidence that any of it ran was an audit row per escalation.

        The read model renders what is recorded here and recomputes
        none of it, the same arrangement as the diagnostics report --
        a projection that re-derived the pipeline would be a second
        answer to the question this layer exists to answer.
        """
        memory = getattr(self.solvent, "memory", None)
        if memory is None:
            return
        from .types import VerificationTier

        try:
            figures = chief.performance()
        except Exception as problem:
            self.solvent.audit.record(
                event="chief_of_staff.unreadable", authority="orchestrator",
                initiator=RUNTIME, why="performance", decision="NOT_RECORDED",
                result=str(problem)[:200])
            return
        # Said once, here, rather than left for the page to infer: money
        # that moved in simulation is not revenue, and a figure whose
        # label depends on a reader checking a different setting is a
        # figure that will eventually be read as real.
        simulated = bool(self.solvent.policy.get(
            "egress", "simulation_only", default=True))
        try:
            memory.learn(
                kind="cos_performance", subject="pipeline",
                # No timestamp of our own. `self._clock` is
                # `time.monotonic` by default, which is seconds since
                # an arbitrary point inside this process: stored and
                # read back by anything else it is not a time at all.
                # `memory_facts.ts` is already a UTC instant written
                # by the store, so the snapshot's age is read from
                # there.
                payload={**figures, "simulated": simulated,
                         "next_steps": [r.as_dict() for r in pipeline]},
                evidence_ref="chief_of_staff:pipeline",
                tier=VerificationTier.T1_DETERMINISTIC)
        except Exception as problem:
            self.solvent.audit.record(
                event="chief_of_staff.unreadable", authority="orchestrator",
                initiator=RUNTIME, why="snapshot", decision="NOT_RECORDED",
                result=str(problem)[:200])

    def _workroot(self) -> str:
        """Where jobs get their scratch directories.

        Beside the database rather than in the system temporary
        directory: a job's working files are the customer's data, and on
        a shared machine the temporary directory is not a place for it.
        """
        import os

        from .types import default_state_path

        configured = self.solvent.policy.get("operations", "work_root",
                                             default="")
        root = str(configured or default_state_path("work"))
        os.makedirs(root, exist_ok=True)
        return root

    def _take_business_lease(self) -> str | None:
        """Claim the right to be the only business executor, or ``None``.

        Recorded in the audit log rather than in a file: the log is
        append-only, already durable, already replicated by whatever backs
        up the database, and a lease that outlives the process holding it
        is exactly the stale-lease case this has to survive.
        """
        held = self._business_lease_holder()
        if held and held != self._lease_id:
            return None
        self.solvent.audit.record(
            event=BUSINESS_LEASE_TAKEN, authority="orchestrator",
            initiator=RUNTIME, why="one business executor at a time",
            decision=self._lease_id, result="held")
        return self._lease_id

    def _release_business_lease(self, lease: str) -> None:
        self.solvent.audit.record(
            event=BUSINESS_LEASE_RELEASED, authority="orchestrator",
            initiator=RUNTIME, why="business run finished",
            decision=lease, result="released")

    def _business_lease_holder(self) -> str:
        """Who holds the lease, or ``""``. A stale lease holds nothing.

        A process killed mid-run leaves a TAKEN with no RELEASED. That
        must not lock the business out for ever, so a lease older than
        :data:`BUSINESS_LEASE_SECONDS` is treated as abandoned -- and
        saying so in the log beats a silent takeover.
        """
        # Asked of the database, not of the whole log. This used to
        # read `audit.events()`, which is every row the installation
        # has ever written, build a dict for each, and scan backwards
        # for the newest lease event. Two scheduled tasks take the
        # lease on every pass, so the cost of finding out who holds
        # it grew with the age of the shop: measured at 13ms a call
        # after four simulated days and still climbing.
        for event in self.solvent.audit.latest(
                (BUSINESS_LEASE_TAKEN, BUSINESS_LEASE_RELEASED), limit=1):
            name = event.get("event")
            if name == BUSINESS_LEASE_RELEASED:
                return ""
            age = _age_seconds(event.get("ts"))
            if age is not None and age > self.BUSINESS_LEASE_SECONDS:
                self.solvent.audit.record(
                    event="runtime.business_lease_expired",
                    authority="orchestrator", initiator=RUNTIME,
                    why=f"held for {age:.0f}s with no release",
                    decision=str(event.get("decision") or ""),
                    result="treated as abandoned")
                return ""
            return str(event.get("decision") or "")
        return ""

    def _say(self, line: str) -> None:
        # Bounded in memory on purpose: a process that runs for months must not
        # accumulate its own history. Durable narrative belongs in the audit log.
        self.log.append(line)
        del self.log[:-200]


def serve(solvent, *, mode: Mode = Mode.ALWAYS_ON, tick_s: float = 1.0,
          max_ticks: int | None = None) -> str:
    """Start, run, and stop one Solvent runtime. What the service unit invokes."""
    runtime = Runtime(solvent, mode=mode)
    runtime.install_signal_handlers()
    runtime.start()
    runtime.default_tasks()
    try:
        return runtime.run_forever(tick_s=tick_s, max_ticks=max_ticks)
    finally:
        runtime.shutdown()
