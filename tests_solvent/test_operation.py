"""Running continuously, inside bounds somebody set in advance.

A loop that stops only when something breaks is not autonomous, it is
unattended. The difference is whether a person decided beforehand how far
it may go -- so every limit here is checked *before* each job, and every
stop has a recorded reason.
"""

from __future__ import annotations

import tempfile
import unittest

from solvent.errors import FailClosed
from solvent.harness import OWNER, Solvent
from solvent.operation import Bounds, Operator, StopReason


class FakeCycle:
    """Stands in for run_business_cycle. The loop does not choose work."""

    def __init__(self, completed=True, invoiced_cents=10_000,
                 stopped_at="", why_stopped=""):
        self.completed = completed
        self.invoiced_cents = invoiced_cents
        self.stopped_at = stopped_at
        self.why_stopped = why_stopped


class Running(unittest.TestCase):

    def setUp(self):
        self.box = tempfile.TemporaryDirectory()
        self.addCleanup(self.box.cleanup)
        self.s = Solvent(f"{self.box.name}/ops.db")
        self.addCleanup(self.close)
        self.calls = 0

    def close(self):
        try:
            self.s.store.close()
        except Exception:
            pass

    def enable(self, **over):
        settings = {"continuous_enabled": True, "max_jobs_per_run": 10,
                    "max_committed_cents": 1_000_000,
                    "max_run_seconds": 60, "max_consecutive_failures": 3}
        settings.update(over)
        self.s.policy.amend({"operations": settings}, OWNER, "test")

    def cycle(self, **kwargs):
        def run():
            self.calls += 1
            return FakeCycle(**kwargs)
        return run


class ItIsOffUntilTheOwnerTurnsItOn(Running):

    def test_a_fresh_installation_will_not_run_unattended(self):
        report = Operator(self.s).run(self.cycle())
        self.assertIs(report.stopped, StopReason.NOT_PERMITTED)
        self.assertIn("not enabled", report.why)
        self.assertEqual(self.calls, 0, "it ran a job without permission")

    def test_the_halt_stops_it_before_any_job(self):
        self.enable(halted=True)
        report = Operator(self.s).run(self.cycle())
        self.assertIs(report.stopped, StopReason.NOT_PERMITTED)
        self.assertEqual(self.calls, 0)

    def test_the_halt_is_checked_every_iteration_not_only_at_the_start(self):
        """Stopping it must not require killing a process."""
        self.enable()
        engaged = {"yet": False}

        def halted():
            return engaged["yet"]

        def one_then_halt():
            self.calls += 1
            engaged["yet"] = True
            return FakeCycle()

        report = Operator(self.s, halted=halted).run(one_then_halt)
        self.assertIs(report.stopped, StopReason.HALTED)
        self.assertEqual(self.calls, 1)


class TheOwnersCeilingCannotBeWidened(Running):

    def test_a_caller_asking_for_more_gets_the_owners_number(self):
        self.enable(max_jobs_per_run=2)
        operator = Operator(self.s)
        effective = operator.effective_bounds(Bounds(max_jobs=50,
                                                     max_seconds=9_999))
        self.assertEqual(effective.max_jobs, 2)
        self.assertLessEqual(effective.max_seconds, 60)

    def test_a_caller_asking_for_less_gets_their_own_number(self):
        self.enable(max_jobs_per_run=10)
        effective = Operator(self.s).effective_bounds(Bounds(max_jobs=3))
        self.assertEqual(effective.max_jobs, 3)

    def test_the_default_is_the_most_restrictive_thing_that_still_runs(self):
        bounds = Bounds()
        self.assertEqual(bounds.max_jobs, 1)
        self.assertEqual(bounds.max_committed_cents, 0)
        self.assertEqual(bounds.max_consecutive_failures, 1)

    def test_a_run_that_may_do_no_jobs_is_refused(self):
        with self.assertRaises(FailClosed):
            Bounds(max_jobs=0)

    def test_a_run_needs_a_positive_time_limit(self):
        with self.assertRaises(FailClosed):
            Bounds(max_seconds=0)


class EveryStopHasAReason(Running):

    def test_the_job_limit_stops_it(self):
        self.enable(max_jobs_per_run=3)
        report = Operator(self.s).run(self.cycle())
        self.assertIs(report.stopped, StopReason.JOB_LIMIT)
        self.assertEqual(self.calls, 3)
        self.assertIn("3 job(s)", report.why)

    def test_the_spend_ceiling_stops_it(self):
        self.enable(max_jobs_per_run=100, max_committed_cents=25_000)
        report = Operator(self.s).run(self.cycle(invoiced_cents=10_000))
        self.assertIs(report.stopped, StopReason.SPEND_LIMIT)
        self.assertEqual(self.calls, 3, "it should stop on reaching 30,000")
        self.assertIn("250.00", report.why)

    def test_consecutive_failures_stop_it(self):
        self.enable(max_consecutive_failures=2)
        report = Operator(self.s).run(
            self.cycle(completed=False, stopped_at="budget",
                       why_stopped="unprofitable"))
        self.assertIs(report.stopped, StopReason.CONSECUTIVE_FAILURES)
        self.assertEqual(self.calls, 2)
        self.assertIn("unprofitable", report.why)

    def test_a_success_resets_the_failure_count(self):
        """One bad job in a stream of good ones is not a crisis."""
        self.enable(max_jobs_per_run=5, max_consecutive_failures=2)
        pattern = [False, True, False, True, True]

        def alternating():
            result = FakeCycle(completed=pattern[self.calls])
            self.calls += 1
            return result

        report = Operator(self.s).run(alternating)
        self.assertIs(report.stopped, StopReason.JOB_LIMIT)
        self.assertEqual(self.calls, 5)

    def test_nothing_to_do_is_a_reason_not_a_silence(self):
        self.enable()
        report = Operator(self.s).run(lambda: None)
        self.assertIs(report.stopped, StopReason.NOTHING_TO_DO)
        self.assertTrue(report.why)

    def test_no_run_ever_ends_without_a_recorded_reason(self):
        """A loop that ends silently is indistinguishable from one still
        going."""
        self.enable(max_jobs_per_run=2)
        for cycle in (self.cycle(), self.cycle(completed=False),
                      lambda: None):
            with self.subTest(cycle=cycle):
                self.calls = 0
                report = Operator(self.s).run(cycle)
                self.assertIsNotNone(report.stopped)
                self.assertTrue(report.why)


class ItOwnsNoNewAuthority(unittest.TestCase):

    def test_it_does_not_choose_the_work(self):
        import ast
        import pathlib as _p

        # Parsed, not grepped. The module's own docstring names
        # run_business_cycle to say what it calls, and a text search
        # cannot tell an explanation from a call.
        tree = ast.parse(_p.Path("solvent/operation.py").read_text(encoding="utf-8"))
        called = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                target = node.func
                if isinstance(target, ast.Name):
                    called.add(target.id)
                elif isinstance(target, ast.Attribute):
                    called.add(target.attr)
        for forbidden in ("Finder", "run_business_cycle", "request",
                          "may_deploy", "record_cost", "grant_budget"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, called,
                                 "the operating loop called an authority "
                                 "directly instead of calling the cycle")

    def test_it_imports_no_authority(self):
        import ast
        import pathlib as _p

        tree = ast.parse(_p.Path("solvent/operation.py").read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        self.assertEqual(
            imported - {"__future__", "dataclasses", "enum"},
            {"errors", "types"},
            "the operating loop imports something it should be calling "
            "through the cycle instead")

    def test_it_only_decides_whether_to_go_round_again(self):
        from solvent.operation import Operator

        public = [n for n in dir(Operator) if not n.startswith("_")]
        self.assertEqual(
            sorted(public),
            ["effective_bounds", "owner_bounds", "permitted", "run"])


class TheWorkDirectoryNameIsSafeAnywhere(unittest.TestCase):
    """Found by Windows CI, which is the only place it could be found.

    ``next_work_order`` joined the order id onto the work root raw.
    An engagement-backed order is ``eng-client:northstar``; on NTFS the
    colon opens an alternate data stream, so ``os.makedirs`` raises
    ``NotADirectoryError`` [WinError 267] and the job never runs.
    Thirteen tests failed on Windows and passed on Linux.
    """

    def test_a_colon_in_the_order_id_leaves_the_name(self):
        from solvent.workorder import workdir_name

        self.assertNotIn(":", workdir_name("eng-client:northstar"))

    def test_every_character_windows_refuses_is_gone(self):
        from solvent.workorder import workdir_name

        for bad in ':*?"<>|/\\' + "\t\n\0":
            with self.subTest(character=repr(bad)):
                self.assertNotIn(bad, workdir_name(f"ord-a{bad}b"))

    def test_a_name_cannot_climb_out_of_the_work_root(self):
        """An order id can carry a client's own reference."""
        import os.path

        from solvent.workorder import workdir_name

        for attempt in ("../../etc", "..", "/etc/passwd", "C:\\Windows",
                        "....//....//x"):
            with self.subTest(attempt):
                joined = os.path.normpath(
                    os.path.join("/work", workdir_name(attempt)))
                self.assertTrue(joined.startswith("/work" + os.sep), joined)

    def test_the_name_does_not_begin_with_a_dot_or_a_dash(self):
        """A leading dot hides the directory; a leading dash reads as a flag.

        The *trailing* dot and space Windows strips silently are
        covered by the digest rather than by the strip -- the name
        never ends in one because it ends in twelve hex characters --
        so they are not asserted here as though the strip were doing
        it.
        """
        from solvent.workorder import workdir_name

        for start in (".hidden", "-rf", "..", " x"):
            with self.subTest(start):
                self.assertFalse(workdir_name(start).startswith((".", "-")),
                                 workdir_name(start))

    def test_the_same_order_always_gets_the_same_directory(self):
        """A resumed job has to find the work it already did."""
        from solvent.workorder import workdir_name

        self.assertEqual(workdir_name("eng-client:northstar"),
                         workdir_name("eng-client:northstar"))

    def test_two_orders_that_clean_up_alike_stay_apart(self):
        """`a:b` and `a/b` both become `a-b` before the digest."""
        from solvent.workorder import workdir_name

        self.assertNotEqual(workdir_name("ord-a:b"), workdir_name("ord-a/b"))

    def test_an_order_id_of_nothing_but_punctuation_still_names_something(self):
        from solvent.workorder import workdir_name

        self.assertTrue(workdir_name("::::").strip("-"))


if __name__ == "__main__":
    unittest.main()
