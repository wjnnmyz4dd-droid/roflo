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
        return sum(1 for c in self.cycles if getattr(c, "completed", False))

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started_at

    def summary(self) -> dict:
        return {
            "attempted": len(self.cycles),
            "completed": self.completed,
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
        return bool(self._s.policy.get("operations", "halted", default=False))

    def owner_bounds(self) -> Bounds:
        """The owner's own ceiling, from policy."""
        get = lambda k, d: self._s.policy.get("operations", k, default=d)  # noqa: E731
        return Bounds(
            max_jobs=max(1, int(get("max_jobs_per_run", 1) or 1)),
            max_committed_cents=int(get("max_committed_cents", 0) or 0),
            max_seconds=float(get("max_run_seconds", 300) or 300),
            max_consecutive_failures=max(
                1, int(get("max_consecutive_failures", 1) or 1)))

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
            if getattr(result, "completed", False):
                consecutive = 0
                # Counted after the job, because what was committed is only
                # known once the job ran -- but the *next* iteration is
                # refused if this pushed it over.
                report.committed_cents += invoiced
                if (limits.max_committed_cents
                        and report.committed_cents >= limits.max_committed_cents):
                    report.stopped = StopReason.SPEND_LIMIT
                    report.why = (
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
