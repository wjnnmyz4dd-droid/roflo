"""What the installed service actually does on a timer.

Before a Windows installation it is worth being precise about this,
because the failure mode is quiet: DeskPilot installs, starts, writes
its heartbeat, passes every health check -- and never does a job.

``Runtime`` schedules eight tasks. None of them runs the business
cycle. ``run_business_cycle`` is reachable from exactly two places: the
one-shot ``solvent cycle`` CLI command, and ``solvent/operation.py``,
the bounded operating loop that nothing imports.

That is not asserted here as a bug to be fixed in passing. Turning it
on is an **activation decision**: it is the difference between a
machine that waits to be told and one that goes looking for work, and
the standing instruction for this engagement is not to deploy or go
live. What these tests do is make the current state explicit, so that
it cannot change in either direction without somebody saying so.
"""

from __future__ import annotations

import ast
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent


def default_task_names() -> list[str]:
    """The names `Runtime.default_tasks` registers, read from the source.

    Read rather than run: constructing a Runtime needs a database, a
    policy and a clock, and the question here is only what the method
    registers.
    """
    tree = ast.parse((ROOT / "solvent" / "runtime.py").read_text(
        encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "default_tasks":
            return [call.args[0].value
                    for call in ast.walk(node)
                    if isinstance(call, ast.Call)
                    and getattr(call.func, "attr", "") == "add_task"
                    and call.args
                    and isinstance(call.args[0], ast.Constant)]
    raise AssertionError("Runtime.default_tasks has gone")


class TheServiceRunsExactlyTheseTasks(unittest.TestCase):

    DECLARED = ["heartbeat", "health", "reconciliation", "discovery",
                "diagnostics", "owner_intents", "owner_briefing",
                "supervision"]

    def test_the_task_list_is_the_declared_one(self):
        """Adding or removing a timer task is a visible change."""
        self.assertEqual(default_task_names(), self.DECLARED)

    def test_none_of_them_executes_client_work(self):
        """The finding, pinned.

        If a task that runs the business cycle is ever added, this fails
        and whoever added it has to come here and say so -- which is the
        point. An installed DeskPilot that starts taking work on a timer
        is a different product from one that waits to be asked, and the
        owner should be the one who decides which they have.
        """
        source = (ROOT / "solvent" / "runtime.py").read_text(encoding="utf-8")
        self.assertNotIn("run_business_cycle", source,
                         "the service now runs the business cycle on a "
                         "timer; that is an activation decision and this "
                         "test is where it gets recorded")


class TheBoundedLoopExistsButIsNotWired(unittest.TestCase):
    """`solvent/operation.py`, and the fact that nothing imports it."""

    def test_the_operator_and_its_bounds_exist(self):
        from solvent.operation import Bounds, Operator, RunReport, StopReason

        self.assertTrue(all((Bounds, Operator, RunReport, StopReason)))

    def test_it_is_the_thing_that_would_call_the_cycle(self):
        source = (ROOT / "solvent" / "operation.py").read_text(
            encoding="utf-8")
        self.assertIn("run_business_cycle", source)

    def test_no_production_module_imports_it(self):
        """The gap, stated as a fact rather than as a worry."""
        importers = []
        for path in sorted((ROOT / "solvent").rglob("*.py")):
            if path.name == "operation.py":
                continue
            text = path.read_text(encoding="utf-8")
            if ("from .operation import" in text
                    or "from ..operation import" in text
                    or "from solvent.operation import" in text
                    or "import solvent.operation" in text):
                importers.append(path.relative_to(ROOT).as_posix())
        self.assertEqual(
            importers, [],
            "solvent/operation.py is now imported by production code. If "
            "the bounded loop has been wired in, update this test and "
            "docs/deskpilot-preinstall-system-audit.md together")

    def test_the_cycle_is_reachable_by_hand(self):
        """It is not that DeskPilot cannot work -- it waits to be asked."""
        source = (ROOT / "solvent" / "cli.py").read_text(encoding="utf-8")
        self.assertIn("run_business_cycle", source)


class TheOtherTwoDormantModulesAreDormantOnPurpose(unittest.TestCase):
    """Reachability found three; two of them say so themselves."""

    def test_the_marketplace_adapter_declares_that_it_does_not_run(self):
        source = (ROOT / "solvent" / "marketplaces.py").read_text(
            encoding="utf-8")
        self.assertIn("does not run yet", source)

    def test_inference_is_advisory_only_and_no_skill_calls_a_model(self):
        source = (ROOT / "solvent" / "inference.py").read_text(
            encoding="utf-8")
        self.assertIn("advisory", source.lower())
        self.assertIn("call no model at all", source)


if __name__ == "__main__":
    unittest.main()
