"""Running continuously, inside bounds the owner set.

DeskPilot can already do one job end to end. This is what it means to do
that repeatedly without a person watching each step -- and the whole
difficulty is in the word "bounded". An operating loop that stops only when
something breaks is not autonomous, it is unattended, and the difference is
whether somebody decided in advance how far it may go.

So this owns no new capability. It calls ``run_business_cycle``, which
already asks every authority in order, and adds exactly three things around
it:

* **Limits the owner set**, checked before each job rather than after: how
  many jobs per run, how much may be committed in total, how long the run
  may last, and how many consecutive failures end it.
* **A reason for stopping**, always recorded. A loop that ends silently is
  indistinguishable from one that is still going.
* **A halt that is checked every iteration**, so stopping it does not
  require killing a process.

It does not decide what work to take -- the Finder triages, qualification
ranks, the Governor prices and the owner approves. It decides only
*whether to go round again*.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum

from .errors import FailClosed
from .types import Cents


class StopReason(Enum):
    """Why a run ended. There is no "it just finished" without a reason."""

    JOB_LIMIT = "JOB_LIMIT"
    SPEND_LIMIT = "SPEND_LIMIT"
    TIME_LIMIT = "TIME_LIMIT"
    CONSECUTIVE_FAILURES = "CONSECUTIVE_FAILURES"
    NOTHING_TO_DO = "NOTHING_TO_DO"
    #: The owner asked it to stop, via the emergency halt.
    HALTED = "HALTED"
    #: A gate refused in a way that is not this loop's to resolve.
    BLOCKED = "BLOCKED"
    NOT_PERMITTED = "NOT_PERMITTED"


def productive(result) -> bool:
    """Did this iteration do the business any good?

    Not the same question as ``BusinessCycle.completed``, which means the
    cycle went all the way through *payment*. A job that was delivered to
    the client and invoiced has done everything this loop can do; whether
    the money has arrived is the payment rail's business and the
    reconciliation task's, and it happens on the client's schedule rather
    than this run's.

    Reading ``completed`` here was a real defect. An unattended loop must
    not fabricate a payment to make its own jobs look finished, so every
    job it ran correctly stopped at AWAITING_PAYMENT, counted as a
    failure, and three of them in a row ended the run -- a business that
    shut itself down for the offence of billing its customers.

    Unpaid exposure is still bounded, and not by this function: the
    Governor's ``max_committed_unverified_cents`` is the ceiling on work
    delivered but not yet paid for, and it is checked before each job is
    priced. Counting an invoice as progress here does not widen it.
    """
    if getattr(result, "completed", False):
        return True
    # Delivered *and* billed, not merely carrying a figure. A cycle that
    # stopped early can still hold a quoted amount in `invoiced_cents`,
    # and reading that alone counted an unprofitable job that stopped at
    # the budget stage as progress -- which would have disarmed the
    # consecutive-failure brake.
    return (bool(getattr(result, "delivered", False))
            and int(getattr(result, "invoiced_cents", 0) or 0) > 0)


@dataclass(frozen=True, slots=True)
class Bounds:
    """How far the owner has said it may go without being asked again.

    Every one defaults to the most restrictive value that still does
    something: one job, no spending, five minutes. A caller that wants more
    says so, and the owner's policy can only narrow it further.
    """

    max_jobs: int = 1
    max_committed_cents: Cents = 0
    max_seconds: float = 300.0
    max_consecutive_failures: int = 1

    def __post_init__(self) -> None:
        if self.max_jobs < 1:
            raise FailClosed("a run that may do no jobs is not a run")
        if self.max_seconds <= 0:
            raise FailClosed("a run needs a positive time limit")


@dataclass
class RunReport:
    cycles: list = field(default_factory=list)
    committed_cents: Cents = 0
    stopped: StopReason | None = None
    why: str = ""
    started_at: float = field(default_factory=time.monotonic)

    @property
    def completed(self) -> int:
        """Cycles that went all the way through *payment*.

        Kept with its original meaning, which several evidence tools
        rely on. It is deliberately **not** this loop's measure of a
        good job: see :func:`productive`, and read :attr:`delivered`
        alongside it. An unattended loop does not fabricate payments,
        so on a shop that bills its customers honestly this is zero
        on every run and says nothing about the work done.
        """
        return sum(1 for c in self.cycles if getattr(c, "completed", False))

    @property
    def delivered(self) -> int:
        """Cycles that did everything this loop can do: delivered and billed.

        The count the loop itself already used, through
        :func:`productive`, and the one that was missing from the
        report. ``summary()`` published ``completed`` alone, so a run
        that delivered a job, invoiced 27,409c for it and counted it
        productive -- adding that figure to ``committed_cents`` --
        was reported by the runtime as ``0/1 completed``. A ninety-day
        run read off that line shows no deliveries against a ledger
        full of revenue.
        """
        return sum(1 for c in self.cycles if productive(c))

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started_at

    def summary(self) -> dict:
        return {
            "attempted": len(self.cycles),
            "delivered": self.delivered,
            "completed": self.completed,
            # Quoted, not collected. This is the sum of `invoiced_cents`
            # over the productive cycles, which is what DeskPilot asked
            # to be paid; the Ledger is the only record of what arrived.
            "committed_cents": self.committed_cents,
            "stopped": self.stopped.value if self.stopped else "",
            "why": self.why,
            "elapsed_seconds": round(self.elapsed, 3),
        }


class Operator:
    """Goes round the loop, and stops when it was told to.

    Reads policy for the owner's ceiling and takes the stricter of that and
    whatever the caller asked for. A caller cannot widen what the owner set.
    """

    def __init__(self, solvent, *, halted=None):
        self._s = solvent
        #: Injected so a halt can be tested without a real signal source.
        #: Defaults to the emergency halt the rest of the system already has.
        self._halted = halted or self._policy_halt

    def _policy_halt(self) -> bool:
        """Is the owner's stop engaged?

        Two switches existed and this loop honoured the wrong one. It
        read ``operations.halted``, a boolean nothing else consults,
        while the documented kill switch is ``operating_mode`` -- the
        enum the Action Gate checks through ``blocks_external`` and the
        orchestrator through ``blocks_new_work``, and the one
        ``PolicyStore.set_operating_mode`` writes.

        The consequence was precise and bad: an owner who pulled the
        halt stopped the Gate and the orchestrator but not this loop,
        which went on picking up work and discovering the halt as a
        ``FailClosed`` thrown from inside each job -- counted as a
        failure, three in a row, and the run ended looking like a
        malfunction instead of a stop the owner asked for.

        Both are read, the mode first. The boolean stays honoured so an
        owner or a script that set it is not quietly ignored, which is
        the same mistake in the other direction.
        """
        if self._s.policy.operating_mode.blocks_new_work:
            return True
        return bool(self._s.policy.get("operations", "halted", default=False))

    def owner_bounds(self) -> Bounds:
        """The owner's own ceiling, from policy.

        ``max_jobs_per_run`` of zero is not read here. ``Bounds`` refuses to
        exist with no jobs, so this used to coerce a zero up to one -- which
        meant an owner who wrote 0 got one job per run and was never told.
        Widening an owner's own limit is the one thing this class exists to
        prevent, so the zero is handled in ``run``, which can stop instead
        of rounding. Found by the sealed exam.
        """
        get = lambda k, d: self._s.policy.get("operations", k, default=d)  # noqa: E731
        return Bounds(
            max_jobs=max(1, self._configured_jobs()),
            max_committed_cents=int(get("max_committed_cents", 0) or 0),
            max_seconds=float(get("max_run_seconds", 300) or 300),
            max_consecutive_failures=max(
                1, int(get("max_consecutive_failures", 1) or 1)))

    def _configured_jobs(self) -> int:
        """What the owner actually wrote, zero included."""
        value = self._s.policy.get("operations", "max_jobs_per_run", default=1)
        try:
            return int(value)
        except (TypeError, ValueError):
            return 1

    def effective_bounds(self, asked: Bounds | None = None) -> Bounds:
        """The stricter of what the caller asked for and what the owner set.

        Deliberately not "whatever the caller passed". A caller is code;
        the owner is the person who carries the consequences.
        """
        owner = self.owner_bounds()
        if asked is None:
            return owner
        return Bounds(
            max_jobs=min(asked.max_jobs, owner.max_jobs),
            max_committed_cents=min(asked.max_committed_cents,
                                    owner.max_committed_cents),
            max_seconds=min(asked.max_seconds, owner.max_seconds),
            max_consecutive_failures=min(asked.max_consecutive_failures,
                                         owner.max_consecutive_failures))

    def permitted(self) -> tuple[bool, str]:
        """May it run unattended at all? Off unless the owner turned it on."""
        if not bool(self._s.policy.get("operations", "continuous_enabled",
                                       default=False)):
            return False, ("continuous operation is not enabled "
                           "(operations.continuous_enabled)")
        if self._halted():
            return False, "the emergency halt is engaged"
        return True, "enabled and not halted"

    def run(self, cycle, *, bounds: Bounds | None = None) -> RunReport:
        """Go round until a bound says stop. ``cycle`` does one job.

        ``cycle`` is a callable returning something with ``completed`` and
        ``invoiced_cents`` -- in practice ``run_business_cycle`` with its
        arguments already bound. This loop does not choose the work.
        """
        report = RunReport()
        allowed, why = self.permitted()
        if not allowed:
            report.stopped, report.why = StopReason.NOT_PERMITTED, why
            return report

        if self._configured_jobs() < 1:
            report.stopped = StopReason.JOB_LIMIT
            report.why = ("the owner set max_jobs_per_run below one, so this "
                          "run may do no jobs")
            return report

        limits = self.effective_bounds(bounds)
        consecutive = 0

        while True:
            # Checked before each job, never after: a limit tested on the
            # way out has already been exceeded once.
            if self._halted():
                report.stopped = StopReason.HALTED
                report.why = "the owner engaged the emergency halt"
                return report
            if len(report.cycles) >= limits.max_jobs:
                report.stopped = StopReason.JOB_LIMIT
                report.why = f"reached the limit of {limits.max_jobs} job(s)"
                return report
            if report.elapsed >= limits.max_seconds:
                report.stopped = StopReason.TIME_LIMIT
                report.why = (f"reached the {limits.max_seconds:g}s limit "
                              f"for one run")
                return report

            result = cycle()
            if result is None:
                report.stopped = StopReason.NOTHING_TO_DO
                report.why = "no further work was available"
                return report
            report.cycles.append(result)

            invoiced = int(getattr(result, "invoiced_cents", 0) or 0)
            if productive(result):
                consecutive = 0
                # Counted after the job, because what was committed is only
                # known once the job ran -- but the *next* iteration is
                # refused if this pushed it over.
                report.committed_cents += invoiced
                # A ceiling of zero means nothing may be committed, not "no
                # ceiling". Read as a sentinel it did the opposite of what
                # ``Bounds`` documents -- the default says "no spending" and
                # imposed no limit at all -- and because ``effective_bounds``
                # takes the *minimum*, an owner who had set no ceiling erased
                # the caller's. The left-hand test is what keeps a run that
                # commits nothing from stopping against a zero ceiling.
                if (report.committed_cents > 0
                        and report.committed_cents >= limits.max_committed_cents):
                    report.stopped = StopReason.SPEND_LIMIT
                    report.why = (
                        f"committed {report.committed_cents / 100:,.2f} "
                        "against a ceiling of nothing; this run was not "
                        "permitted to commit anything"
                        if not limits.max_committed_cents else
                        f"committed {report.committed_cents / 100:,.2f}, "
                        f"reaching the "
                        f"{limits.max_committed_cents / 100:,.2f} ceiling")
                    return report
            else:
                consecutive += 1
                if consecutive >= limits.max_consecutive_failures:
                    report.stopped = StopReason.CONSECUTIVE_FAILURES
                    where = getattr(result, "stopped_at", "?")
                    detail = getattr(result, "why_stopped", "")
                    report.why = (
                        f"{consecutive} job(s) in a row did not complete; "
                        f"the last stopped at {where}: {detail}")
                    return report
