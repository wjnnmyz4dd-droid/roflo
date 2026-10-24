"""The simulated calendar, and what it must not do to the runtime.

Two claims. The calendar is deterministic -- same start, same days,
same clock readings, every run -- and it drives the **production**
scheduler rather than standing in for one. The second is the easier
of the two to lose: a harness that decides for itself which tasks are
due is a second scheduler, and a ninety-day run on a second scheduler
measures the harness.
"""

from __future__ import annotations

import datetime
import pathlib
import unittest

from solvent.harness import OWNER
from solvent.runtime import DEFAULT_INTERVALS, Runtime

from simulation.calendar import (
    SECONDS_PER_DAY, STEP_SECONDS, Calendar, Clock,
    nondeterminism_in, nondeterminism_in_this_module,
)
from tests_solvent.test_production_path import ProductionPath

NEW_YEAR = datetime.date(2026, 1, 1)


def a_fortnight(**kw) -> Calendar:
    return Calendar(start=NEW_YEAR, length=14, **kw)


class ItIsDeterministic(unittest.TestCase):

    def test_the_module_reaches_for_nothing_that_reads_the_world(self):
        self.assertEqual(nondeterminism_in_this_module(), set())

    def test_the_guard_catches_a_module_that_does(self):
        """The control. A guard only ever run against code that passes
        it is a guard nobody has seen work, and it reads in a report
        as a check that ran.
        """
        import tempfile

        cases = {
            "import time\nx = 1\n": {"time"},
            "import datetime\nx = datetime.datetime.now()\n": {"now"},
            "from random import choice\nx = choice([1])\n":
                {"random", "choice"},
            "import datetime\nx = datetime.time()\n": set(),
        }
        with tempfile.TemporaryDirectory() as box:
            for source, expected in cases.items():
                with self.subTest(source.splitlines()[0]):
                    path = pathlib.Path(box) / "probe.py"
                    path.write_text(source, encoding="utf-8")
                    self.assertEqual(nondeterminism_in(path), expected)

    def test_two_runs_of_one_calendar_are_the_same_run(self):
        first = [(d.index, d.date, d.working, d.why) for d in a_fortnight().days()]
        second = [(d.index, d.date, d.working, d.why) for d in a_fortnight().days()]
        self.assertEqual(first, second)

    def test_the_clock_starts_where_it_is_told_to(self):
        clock = a_fortnight().clock()
        self.assertEqual(clock.seconds, 0.0)
        self.assertEqual(clock.date, NEW_YEAR)
        self.assertEqual(clock.isoformat(), "2026-01-01T00:00:00")

    def test_the_date_is_computed_from_the_seconds_not_tracked_beside_them(self):
        """So the two cannot drift. The whole reason they share an object."""
        clock = a_fortnight().clock()
        clock.advance(SECONDS_PER_DAY * 3 + 3_600)
        self.assertEqual(clock.date, datetime.date(2026, 1, 4))
        self.assertEqual(clock.day_index, 3)
        self.assertEqual(clock.isoformat(), "2026-01-04T01:00:00")

    def test_a_clock_does_not_run_backwards(self):
        with self.assertRaises(ValueError):
            a_fortnight().clock().advance(-1)


class WhatCountsAsAWorkingDay(unittest.TestCase):

    def test_weekends_are_not_working_days_and_say_so(self):
        days = {d.date: d for d in a_fortnight().days()}
        saturday = days[datetime.date(2026, 1, 3)]
        self.assertFalse(saturday.working)
        self.assertEqual(saturday.why, "Saturday")
        self.assertTrue(days[datetime.date(2026, 1, 5)].working)

    def test_a_holiday_carries_its_name_rather_than_a_date_to_decode(self):
        christmas = datetime.date(2026, 1, 7)
        calendar = a_fortnight(holidays={christmas: "a public holiday"})
        day = {d.date: d for d in calendar.days()}[christmas]
        self.assertFalse(day.working)
        self.assertEqual(day.why, "a public holiday")

    def test_a_fortnight_has_ten_working_days(self):
        self.assertEqual(a_fortnight().working_days, 10)

    def test_a_shop_whose_clients_answer_at_weekends_can_say_so(self):
        every_day = a_fortnight(working_weekdays=(0, 1, 2, 3, 4, 5, 6))
        self.assertEqual(every_day.working_days, 14)

    def test_a_week_with_no_working_day_is_refused(self):
        with self.assertRaises(ValueError):
            a_fortnight(working_weekdays=())

    def test_a_calendar_of_no_days_is_refused(self):
        with self.assertRaises(ValueError):
            Calendar(start=NEW_YEAR, length=0)

    def test_a_step_longer_than_a_day_is_refused(self):
        """Whole days would pass without the scheduler being asked."""
        with self.assertRaises(ValueError):
            a_fortnight(step_seconds=SECONDS_PER_DAY + 1)


class ItDrivesTheProductionScheduler(ProductionPath):
    """Against a real ``Runtime``, not a stand-in for one.

    The default step here is two hours rather than the ten minutes a
    measured run uses. An idle day at the real step costs about eleven
    seconds of wall clock -- measured, in :class:`WhatItCostsToRun` --
    and these tests are about the walk rather than the cadence, so
    paying that eleven seconds a dozen times over would buy nothing.
    The two tests that *are* about cadence set the step themselves and
    say why.
    """

    STEP = 7_200.0

    def setUp(self):
        super().setUp()
        self.calendar = Calendar(start=NEW_YEAR, length=3,
                                 step_seconds=self.STEP)
        self.clock = self.calendar.clock()
        self.runtime = Runtime(self.s, clock=self.clock)
        self.runtime.start()
        self.runtime.default_tasks()

    def walk(self, **hooks) -> list:
        return list(self.calendar.run(self.runtime, clock=self.clock, **hooks))

    def test_it_yields_each_day_once_in_order(self):
        walked = self.walk()
        self.assertEqual([d.index for d in walked], [0, 1, 2])
        self.assertEqual([d.date for d in walked],
                         [NEW_YEAR + datetime.timedelta(days=i)
                          for i in range(3)])

    def test_each_day_begins_and_ends_on_the_hour_it_should(self):
        """A step that does not divide the day must not drift the calendar."""
        opened: list[float] = []
        self.calendar.step_seconds = 7_000.0        # 12 steps, 84,000s
        self.assertNotEqual(SECONDS_PER_DAY % 7_000.0, 0.0,
                            "a step that divides the day proves nothing here")
        self.walk(before_day=lambda day, clock: opened.append(clock.seconds))

        self.assertEqual(opened, [0.0, SECONDS_PER_DAY, 2 * SECONDS_PER_DAY])
        self.assertEqual(self.clock.seconds, 3 * SECONDS_PER_DAY)

    def test_the_hooks_see_the_day_and_the_clock_they_are_in(self):
        seen: list[tuple] = []
        self.walk(before_day=lambda day, clock: seen.append(
            ("open", day.index, clock.date)),
            after_day=lambda day, clock: seen.append(
            ("close", day.index, clock.date)))

        self.assertEqual(seen[0], ("open", 0, NEW_YEAR))
        self.assertEqual(seen[1], ("close", 0, datetime.date(2026, 1, 2)),
                         "a day closes at the midnight that starts the next")

    def test_it_asks_the_production_scheduler_and_decides_nothing_itself(self):
        """Every line back comes from a task the runtime registered."""
        lines: list[str] = []
        self.walk(on_step=lambda day, clock, done: lines.extend(done))

        self.assertTrue(lines)
        names = {line.split(":", 1)[0] for line in lines}
        self.assertTrue(names <= {t.name for t in self.runtime.tasks},
                        f"something that is not a registered task ran: "
                        f"{names - {t.name for t in self.runtime.tasks}}")

    def test_the_business_task_runs_at_production_cadence_at_the_real_step(self):
        """The default step is chosen to match the slow tasks exactly.

        ``business`` is on a ten-minute cadence and ``STEP_SECONDS``
        is ten minutes, so at the step a measured run uses it fires
        144 times a day -- production's own rate, not once a day and
        not once a step of some other size.
        """
        self.assertEqual(DEFAULT_INTERVALS["business"], STEP_SECONDS)
        self.calendar = Calendar(start=NEW_YEAR, length=1)
        self.clock = self.calendar.clock()
        self.runtime = Runtime(self.s, clock=self.clock)
        self.runtime.start()
        self.runtime.default_tasks()

        ran: list[str] = []
        self.walk(on_step=lambda day, clock, done: ran.extend(
            line for line in done if line.startswith("business:")))

        self.assertEqual(len(ran), 144)
        self.assertEqual(len(ran),
                         int(SECONDS_PER_DAY / DEFAULT_INTERVALS["business"]))

    def test_the_heartbeat_is_sampled_not_reproduced(self):
        """Said plainly, because it is a limit of the harness.

        ``heartbeat`` has a sixty-second cadence, shorter than any
        step worth running ninety days at, so it fires once per step
        rather than once a minute. Nothing in the business depends on
        that rate -- the watchdog it feeds is a real-time concern and
        there is no real time here -- but a report implying this run
        reproduced production's heartbeat would be wrong.
        """
        self.assertLess(DEFAULT_INTERVALS["heartbeat"], STEP_SECONDS)
        ran: list[str] = []
        self.walk(on_step=lambda day, clock, done: ran.extend(
            line for line in done if line.startswith("heartbeat:")))

        self.assertEqual(len(ran), 3 * self.calendar.steps_per_day())
        self.assertLess(len(ran),
                        3 * SECONDS_PER_DAY / DEFAULT_INTERVALS["heartbeat"])

    def test_a_finer_step_asks_more_often(self):
        self.calendar.step_seconds = self.STEP / 4
        ran: list[str] = []
        self.walk(on_step=lambda day, clock, done: ran.extend(
            line for line in done if line.startswith("heartbeat:")))

        self.assertEqual(len(ran), 3 * 48)

    def test_the_runtime_sees_the_clock_the_calendar_moves(self):
        """Otherwise the scheduler would find nothing due, for ever."""
        before = [t.name for t in self.runtime.tasks if t.failures]
        self.walk()

        self.assertEqual([t.name for t in self.runtime.tasks if t.failures],
                         before, "a task failed while the calendar ran")
        self.assertEqual(self.clock.seconds, 3 * SECONDS_PER_DAY)

    def test_the_same_calendar_twice_gives_the_same_scheduler_decisions(self):
        """The claim that makes a measured run worth anything."""
        first: list[str] = []
        self.walk(on_step=lambda day, clock, done: first.extend(done))

        self.setUp()
        second: list[str] = []
        self.walk(on_step=lambda day, clock, done: second.extend(done))

        self.assertEqual(len(first), len(second))
        # Compared on the task names and verdicts rather than whole
        # lines: a line may carry a job id, and job ids are random by
        # design. Two runs taking the same decisions is the claim; two
        # runs minting the same identifiers would be a defect.
        self.assertEqual([line.split(":", 1)[0] for line in first],
                         [line.split(":", 1)[0] for line in second])


class WhatItCostsToRun(ProductionPath):
    """A ninety-day run has to finish, so the price of a day is measured.

    Measured rather than assumed, because the answer decides whether
    the default step is honest. At ten minutes an idle day is about
    eleven seconds here -- call it a quarter of an hour of overhead
    for ninety days, before any job runs -- and the alternative, a
    coarser step, would make the ten-minute tasks fire at a rate
    production never uses.

    The ceiling below is deliberately loose. It is here to catch a
    change that makes a day cost minutes, not to pin a number on a
    shared machine.
    """

    #: Generous against the ~11s measured, so a loaded CI host does
    #: not fail a test about orders of magnitude.
    A_DAY_MUST_COST_UNDER_SECONDS = 120.0

    def test_an_idle_day_is_cheap_enough_for_ninety_of_them(self):
        import time as real_time        # the test may read the real clock

        calendar = Calendar(start=NEW_YEAR, length=1)
        clock = calendar.clock()
        runtime = Runtime(self.s, clock=clock)
        runtime.start()
        runtime.default_tasks()
        self.s.policy.amend({"operations": {"continuous_enabled": False}},
                            OWNER, "measuring an idle day")

        lines: list[str] = []
        began = real_time.perf_counter()
        for _ in calendar.run(runtime, clock=clock,
                              on_step=lambda d, c, done: lines.extend(done)):
            pass
        cost = real_time.perf_counter() - began

        self.assertEqual(clock.seconds, SECONDS_PER_DAY)
        self.assertGreater(len(lines), calendar.steps_per_day(),
                           "fewer lines than steps means tasks were not run")
        self.assertLess(cost, self.A_DAY_MUST_COST_UNDER_SECONDS,
                        f"a simulated day cost {cost:.1f}s, so ninety would "
                        f"cost {cost * 90 / 60:.0f} minutes before any job "
                        "runs")


if __name__ == "__main__":
    unittest.main()
