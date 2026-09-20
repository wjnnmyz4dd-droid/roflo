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
from enum import Enum
from typing import Callable

from .audit import new_id
from .errors import FailClosed
from .types import BlockedOn, JobState, OperatingMode

#: Where a production database lives when nothing says otherwise. A 24/7 service
#: whose store defaults to ``:memory:`` would lose the ledger on every restart.
DEFAULT_DB = "/var/lib/solvent/solvent.db"

RUNTIME = "runtime"


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

    solvent.audit.record(
        event="runtime.reconciled", authority="orchestrator", initiator=initiator,
        why="restart reconciliation", decision="RECONCILED",
        result="; ".join(notes) if notes else "nothing outstanding")
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
    "discovery": 900.0,        # look for work every 15 minutes
    "reconciliation": 300.0,   # re-check payment and job state every 5
    "health": 60.0,            # notice trouble within a minute
}

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

    # ------------------------------------------------------------- lifecycle
    def start(self) -> Startup:
        """Run startup checks and reconcile. Raises rather than run unsafely."""
        self.started = startup_checks(self.solvent, mode=self.mode)
        if not self.started.safe_to_run:
            problems = "; ".join(f"{c.name}: {c.detail}" for c in self.started.fatal)
            raise FailClosed(f"refusing to start: {problems}")

        notes = reconcile(self.solvent)
        for note in notes:
            self._say(f"recovery: {note}")

        self.solvent.audit.record(
            event="runtime.started", authority="orchestrator", initiator=RUNTIME,
            why=f"{self.mode.value} runtime start", decision="STARTED",
            result=f"health={health(self.solvent, started=self.started)[0].value}")
        return self.started

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
        return float(configured) if configured else DEFAULT_INTERVALS[name]

    def add_task(self, name: str, run: Callable[[], str]) -> Task:
        task = Task(name=name, interval_s=self.interval(name), run=run)
        self.tasks.append(task)
        return task

    def default_tasks(self) -> None:
        """The three things a first-revenue Solvent needs to do on a timer."""
        self.add_task("health", self._tick_health)
        self.add_task("reconciliation", self._tick_reconcile)
        self.add_task("discovery", self._tick_discovery)

    def _tick_health(self) -> str:
        state, why = health(self.solvent, started=self.started)
        return f"{state.value}: {why}"

    def _tick_reconcile(self) -> str:
        notes = reconcile(self.solvent)
        return f"{len(notes)} item(s) reconciled" if notes else "nothing outstanding"

    def _tick_discovery(self) -> str:
        """Look for work — but only if the authorities currently permit it."""
        state, why = health(self.solvent, started=self.started)
        if not state.may_start_work:
            return f"skipped: {state.value} ({why})"
        if self.solvent.policy.operating_mode.blocks_new_work:
            return "skipped: operating mode blocks new work"
        sources = self.solvent.discovery.sources()
        permitted = [s for s in sources if s.get("readiness") == "PERMITTED"]
        if not permitted:
            return "skipped: no permitted work source"
        return f"{len(permitted)} permitted source(s) available"

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
