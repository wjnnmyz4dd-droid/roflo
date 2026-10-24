"""A deterministic calendar, so ninety days take minutes rather than months.

Nothing here is a production component and nothing in :mod:`solvent`
imports it. It supplies the one thing a simulated business cannot get
from the real world: time, advanced on demand and identically on every
run.

**Determinism is the whole point.** There is no ``datetime.now``, no
``time.monotonic`` and no ``random`` in this module -- a test asserts
that, by walking the parse tree rather than grepping, because a guard
that reads prose as code passes whatever is written near it. Two runs
of the same calendar produce the same sequence of days, the same clock
readings and therefore the same scheduler decisions.

**It is not a second scheduler.** :class:`Calendar` advances a clock
and calls ``Runtime.run_due``, which is the production scheduler
deciding for itself which tasks are due. The cadences in
``runtime.DEFAULT_INTERVALS`` are the ones that apply; this chooses
only how finely the clock is sampled between them.

**A working day is about the client, not about DeskPilot.** An
unattended system does not take Saturdays off, so the runtime ticks on
every day here. :attr:`Day.working` is published for the customer
model to read, because the people who answer emails do take Saturdays
off, and a simulation where they reply at the same rate all week
overstates the week.
"""

from __future__ import annotations

import ast
import datetime
import pathlib
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field

#: Seconds in a day. Named rather than inlined so the one place that
#: converts between the two is greppable.
SECONDS_PER_DAY = 86_400.0

#: Monday to Friday, as :meth:`datetime.date.weekday` numbers them.
WEEKDAYS = (0, 1, 2, 3, 4)

#: The default sampling step, in seconds. Ten minutes, which is the
#: cadence of the business and contracting tasks -- the slowest things
#: that need to run often -- so a day is 144 scheduler passes. A finer
#: step costs time and changes nothing for them; a coarser one makes
#: the ten-minute tasks fire less often than production would.
STEP_SECONDS = 600.0


@dataclass(frozen=True, slots=True)
class Day:
    """One simulated day. ``index`` is zero-based; ``date`` is real."""

    index: int
    date: datetime.date
    working: bool
    #: Why it is not a working day, or ``""``. Carried so a report can
    #: say "Christmas Day" rather than leaving a reader to count back
    #: through the dates and work it out.
    why: str = ""

    @property
    def weekday(self) -> str:
        return self.date.strftime("%A")


class Clock:
    """Simulated seconds, advanced only by being asked.

    Satisfies what ``Runtime`` wants of a clock -- a zero-argument
    callable returning a float -- while also knowing the date, which
    the runtime neither asks for nor needs. The two are kept in one
    object so they cannot drift: the date is computed from the
    seconds, never tracked alongside them.
    """

    __slots__ = ("start", "seconds")

    def __init__(self, start: datetime.date, seconds: float = 0.0) -> None:
        self.start = start
        self.seconds = float(seconds)

    def __call__(self) -> float:
        return self.seconds

    def advance(self, seconds: float) -> float:
        if seconds < 0:
            raise ValueError("a simulated clock does not run backwards")
        self.seconds += float(seconds)
        return self.seconds

    @property
    def moment(self) -> datetime.datetime:
        return (datetime.datetime.combine(self.start, datetime.time())
                + datetime.timedelta(seconds=self.seconds))

    @property
    def date(self) -> datetime.date:
        return self.moment.date()

    @property
    def day_index(self) -> int:
        return int(self.seconds // SECONDS_PER_DAY)

    def isoformat(self) -> str:
        return self.moment.isoformat(timespec="seconds")


@dataclass
class Calendar:
    """A fixed run of days, and the clock that walks them.

    ``holidays`` are dates the clients do not work; the value is the
    reason, which is carried into :attr:`Day.why`. Weekends come from
    ``working_weekdays`` so that a run can model a shop whose clients
    answer at weekends by passing all seven.
    """

    start: datetime.date
    length: int
    holidays: dict = field(default_factory=dict)
    working_weekdays: Sequence[int] = WEEKDAYS
    step_seconds: float = STEP_SECONDS

    def __post_init__(self) -> None:
        if self.length <= 0:
            raise ValueError("a calendar of no days simulates nothing")
        if self.step_seconds <= 0:
            raise ValueError("a step of nothing never reaches tomorrow")
        if self.step_seconds > SECONDS_PER_DAY:
            raise ValueError(
                f"a step of {self.step_seconds:g}s is longer than a day, so "
                "whole days would pass without the scheduler being asked")
        if not self.working_weekdays:
            raise ValueError(
                "a week with no working day in it is not a slower business, "
                "it is a business where nothing can ever happen")

    def clock(self) -> Clock:
        """A fresh clock at the first moment of the first day."""
        return Clock(self.start)

    def day(self, index: int) -> Day:
        date = self.start + datetime.timedelta(days=index)
        reason = self.holidays.get(date, "")
        if reason:
            return Day(index=index, date=date, working=False, why=reason)
        if date.weekday() not in self.working_weekdays:
            return Day(index=index, date=date, working=False,
                       why=date.strftime("%A"))
        return Day(index=index, date=date, working=True)

    def days(self) -> list[Day]:
        return [self.day(i) for i in range(self.length)]

    @property
    def working_days(self) -> int:
        return sum(1 for d in self.days() if d.working)

    def steps_per_day(self) -> int:
        """How many scheduler passes each day gets.

        Rounded **down**, and the remainder is spent as one final jump
        to midnight, so a step that does not divide the day cannot
        drift the calendar: day ``n`` always begins at exactly
        ``n * SECONDS_PER_DAY``.
        """
        return int(SECONDS_PER_DAY // self.step_seconds)

    # ---- running it ---------------------------------------------------

    def run(self, runtime, *, clock: Clock,
            before_day: Callable[[Day, Clock], None] | None = None,
            after_day: Callable[[Day, Clock], None] | None = None,
            on_step: Callable[[Day, Clock, list], None] | None = None,
            ) -> Iterator[Day]:
        """Walk the calendar, ticking the production scheduler.

        Yields each day once it is over, so a caller can measure as it
        goes rather than only at the end. The hooks are where the
        simulated world acts: ``before_day`` is the clients doing
        whatever they do that morning, ``after_day`` is the audit
        taking its reading.

        The runtime's clock must be the ``clock`` passed here. It is
        taken as an argument rather than read off the runtime because
        ``Runtime`` keeps its clock private, and reaching into it to
        check would be this module asserting about production
        internals it does not own.
        """
        per_day = self.steps_per_day()
        for day in self.days():
            opened_at = day.index * SECONDS_PER_DAY
            if clock.seconds < opened_at:
                clock.advance(opened_at - clock.seconds)
            if before_day is not None:
                before_day(day, clock)
            for _ in range(per_day):
                done = runtime.run_due()
                if on_step is not None:
                    on_step(day, clock, done)
                clock.advance(self.step_seconds)
            # Land exactly on midnight however the step divides.
            closes_at = opened_at + SECONDS_PER_DAY
            if clock.seconds < closes_at:
                clock.advance(closes_at - clock.seconds)
            if after_day is not None:
                after_day(day, clock)
            yield day


# ---------------------------------------------------------------- guards


#: Attribute and bare-name references that read the world instead of
#: the arguments they were given. ``time`` is deliberately absent:
#: ``datetime.time()`` is a time-of-day constructor and perfectly
#: deterministic, while the ``time`` *module* is caught as an import
#: below. A guard that cannot tell those apart either passes nothing
#: or gets switched off, and a guard that is switched off is worse
#: than none, because it reads in a report as a check that ran.
FORBIDDEN_CALLS = frozenset({
    "now", "utcnow", "today", "monotonic", "perf_counter", "time_ns",
    "random", "randint", "choice", "shuffle", "sample", "uuid4", "urandom",
})

#: Modules whose whole purpose is to be nondeterministic.
FORBIDDEN_IMPORTS = frozenset({"time", "random", "uuid", "secrets", "os"})


def _references(path: pathlib.Path) -> tuple[set[str], set[str]]:
    """``(names and attributes, imported modules)`` used by this code.

    An AST walk, because the module docstring above contains the words
    ``datetime.now``, ``time.monotonic`` and ``random``, and a textual
    search would flag the sentence explaining that they are absent.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    used: set[str] = set()
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            used.add(node.id)
        elif isinstance(node, ast.Attribute):
            used.add(node.attr)
        elif isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    return used, imported


def nondeterminism_in(path: pathlib.Path) -> set[str]:
    """Sources of nondeterminism the code at ``path`` reaches for.

    Takes a path rather than only checking this file, so a test can
    point it at something that *does* read the world and watch it say
    so. A guard only ever run against code that passes it is a guard
    nobody has seen work.
    """
    used, imported = _references(path)
    return (FORBIDDEN_CALLS & used) | (FORBIDDEN_IMPORTS & imported)


def nondeterminism_in_this_module() -> set[str]:
    """Sources of nondeterminism this module reaches for. Must be empty."""
    return nondeterminism_in(pathlib.Path(__file__))
