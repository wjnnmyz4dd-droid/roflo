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
from .types import BlockedOn, JobState, OperatingMode

#: Where a production database lives when nothing says otherwise. A 24/7 service
#: whose store defaults to ``:memory:`` would lose the ledger on every restart.
DEFAULT_DB = "/var/lib/solvent/solvent.db"

RUNTIME = "runtime"


#: Where the liveness heartbeat is written. Read by deploy/solvent-watchdog.sh
#: from outside this process, which is the entire point of it being a file.
HEARTBEAT_PATH = "/var/lib/solvent/heartbeat"


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
    notes.extend(_reconcile_capacity(solvent, initiator=initiator))

    solvent.audit.record(
        event="runtime.reconciled", authority="orchestrator", initiator=initiator,
        why="restart reconciliation", decision="RECONCILED",
        result="; ".join(notes) if notes else "nothing outstanding")
    return notes


#: States a crash can strand a job in: work was under way and the process
#: went away before anything moved it on. None of them is a resting place,
#: so a job found here after a restart was interrupted.
_INTERRUPTED = (JobState.EXECUTING, JobState.VERIFYING,
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
        why = (f"found in {job.state.value} after a restart, so it was "
               "interrupted; the owner decides whether the work resumes")
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
        if job.state.is_terminal or job.state is JobState.COMPLETE:
            released = capacity.release_job(
                job_id, why=f"job is {job.state.value} at reconciliation")
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

    intact, detail = solvent.audit.verify_chain()
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
        self.add_task("owner_briefing", self._tick_owner_briefing)
        self.add_task("supervision", self._tick_supervision)

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
        sources = self.solvent.discovery.sources()
        permitted = [s for s in sources if s.get("readiness") == "PERMITTED"]
        if not permitted:
            return "skipped: no permitted work source"
        return f"{len(permitted)} permitted source(s) available"

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
