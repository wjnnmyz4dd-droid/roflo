"""What the installed service does on a timer, now that it does something.

Until this round `Runtime` scheduled eight tasks and none ran the
business cycle: DeskPilot installed, started, wrote its heartbeat,
passed every health check and never did a job. `run_business_cycle` was
reachable only from the one-shot CLI and from `solvent/operation.py`,
the bounded loop that nothing imported.

The earlier version of this module pinned that state, and it fired the
moment the loop was wired in -- which is what sent whoever wired it
here to say so. It now pins the opposite: that the business cycle runs
automatically, and that each of the four things that may stop it still
does.

The four refusals matter more than the connection. A loop that runs is
easy; a loop that stops when simulation is turned off, when the owner
has not opted in, when the halt is engaged, or when another executor
holds the lease is the part that makes running it safe.
"""

from __future__ import annotations

import ast
import pathlib
import unittest

from solvent import runtime as runtime_module
from solvent.operation import Bounds, Operator, RunReport, StopReason
from tests_solvent.fixtures import OWNER, Rig

ROOT = pathlib.Path(__file__).resolve().parent.parent


def default_task_names() -> list[str]:
    """The names `Runtime.default_tasks` registers, read from the source."""
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


class TheServiceSchedulesTheBusinessCycle(unittest.TestCase):

    DECLARED = ["heartbeat", "health", "reconciliation", "discovery",
                "diagnostics", "owner_intents", "owner_briefing",
                "supervision", "business"]

    def test_the_task_list_is_the_declared_one(self):
        """Adding or removing a timer task stays a visible change."""
        self.assertEqual(default_task_names(), self.DECLARED)

    def test_business_is_scheduled(self):
        self.assertIn("business", default_task_names())

    def test_it_has_a_cadence_of_its_own(self):
        self.assertIn("business", runtime_module.DEFAULT_INTERVALS)
        self.assertGreater(runtime_module.DEFAULT_INTERVALS["business"], 0)

    def test_every_task_has_a_cadence(self):
        """The bug that once killed the runtime before its first tick."""
        self.assertEqual(
            sorted(default_task_names()),
            sorted(set(default_task_names())
                   & set(runtime_module.DEFAULT_INTERVALS)))

    def test_it_runs_behind_supervision(self):
        """Noticing stuck work comes before taking more on."""
        names = default_task_names()
        self.assertLess(names.index("supervision"), names.index("business"))


class ItReusesTheOneBusinessEngine(unittest.TestCase):
    """No second Finder, Governor, Action Gate or admission rule."""

    def test_the_tick_calls_the_existing_cycle(self):
        import inspect

        source = inspect.getsource(runtime_module.Runtime._one_business_job)
        self.assertIn("run_business_cycle", source)

    def test_the_tick_drives_the_existing_operator(self):
        import inspect

        source = inspect.getsource(runtime_module.Runtime._tick_business)
        self.assertIn("Operator", source)

    def test_the_runtime_decides_no_business_question_itself(self):
        """It owns no authority; it calls the ones that already exist."""
        import inspect

        source = inspect.getsource(runtime_module.Runtime._tick_business)
        for authority in ("Finder(", "FinancialGovernor(", "Qualification(",
                          "ActionGate("):
            with self.subTest(authority=authority):
                self.assertNotIn(authority, source)


class TheFourRefusals(unittest.TestCase):
    """Each one alone must stop the loop."""

    def setUp(self):
        self.rig = Rig()
        self.rig.policy.amend(
            {"operations": {"continuous_enabled": True},
             "egress": {"simulation_only": True}},
            OWNER, "test: a service that may do business in simulation")

    def _operator(self, halted=None):
        return Operator(self.rig, halted=halted or (lambda: False))

    def test_opt_in_is_off_by_default(self):
        """Wiring the loop in changes nothing until the owner says so."""
        fresh = Rig()
        allowed, why = Operator(fresh, halted=lambda: False).permitted()
        self.assertFalse(allowed)
        self.assertIn("continuous_enabled", why)

    def test_the_halt_stops_it(self):
        allowed, why = self._operator(halted=lambda: True).permitted()
        self.assertFalse(allowed)
        self.assertIn("halt", why)

    def test_simulation_off_stops_the_tick(self):
        """The authorisation for this loop is simulation-only."""
        import inspect

        source = inspect.getsource(runtime_module.Runtime._tick_business)
        self.assertIn("simulation_only", source)
        self.assertIn("simulation only", source)

    def test_a_zero_job_limit_does_no_jobs_rather_than_one(self):
        self.rig.policy.amend({"operations": {"max_jobs_per_run": 0}},
                              OWNER, "test: the owner wrote zero")
        report = self._operator().run(lambda: None)
        self.assertIs(report.stopped, StopReason.JOB_LIMIT)
        self.assertIn("below one", report.why)

    def test_a_caller_cannot_widen_what_the_owner_set(self):
        self.rig.policy.amend({"operations": {"max_jobs_per_run": 2}},
                              OWNER, "test: the owner's ceiling")
        asked = Bounds(max_jobs=999, max_seconds=99_999.0)
        self.assertEqual(self._operator().effective_bounds(asked).max_jobs, 2)


class OneExecutorAtATime(unittest.TestCase):
    """`Task` never overlaps with itself, but that is one process."""

    def test_the_lease_is_recorded_in_the_append_only_log(self):
        self.assertTrue(runtime_module.BUSINESS_LEASE_TAKEN)
        self.assertTrue(runtime_module.BUSINESS_LEASE_RELEASED)
        self.assertNotEqual(runtime_module.BUSINESS_LEASE_TAKEN,
                            runtime_module.BUSINESS_LEASE_RELEASED)

    def test_a_lease_has_a_staleness_horizon(self):
        """A process killed mid-run must not lock the business out."""
        self.assertGreater(runtime_module.Runtime.BUSINESS_LEASE_SECONDS, 300)

    def test_an_unreadable_timestamp_is_not_treated_as_just_now(self):
        """Otherwise every lease looks fresh and the check does nothing."""
        self.assertIsNone(runtime_module._age_seconds("not-a-date"))
        self.assertIsNone(runtime_module._age_seconds(None))

    def test_a_real_timestamp_reads_as_an_age(self):
        from solvent.audit import now

        age = runtime_module._age_seconds(now())
        self.assertIsNotNone(age)
        self.assertLess(abs(age), 60)


class TheOperatorStopsForAReason(unittest.TestCase):
    """`RunReport` always says why, so a silent end is impossible."""

    def setUp(self):
        self.rig = Rig()
        self.rig.policy.amend({"operations": {"continuous_enabled": True}},
                              OWNER, "test")

    def test_a_refused_run_names_the_reason(self):
        report = Operator(Rig(), halted=lambda: False).run(lambda: None)
        self.assertIs(report.stopped, StopReason.NOT_PERMITTED)
        self.assertTrue(report.why)

    def test_every_stop_reason_is_a_reason(self):
        for reason in StopReason:
            with self.subTest(reason=reason):
                self.assertTrue(reason.value)

    def test_a_report_summarises_without_a_run(self):
        summary = RunReport().summary()
        self.assertEqual(summary["attempted"], 0)
        self.assertEqual(summary["stopped"], "")


class TheTickActuallyRuns(unittest.TestCase):
    """Executed, not inspected.

    This class exists because of a specific failure. The first version
    of this module had twenty-two tests and every one of them read the
    tick's *source*: that it mentioned Operator, that it mentioned
    run_business_cycle, that it mentioned simulation_only. All twenty-two
    passed against a tick containing `name 'fmt' is not defined`, which
    raised on every single run. It was the hundred-job acceptance that
    caught it, not the suite.

    A test that greps a function is a test that cannot tell whether the
    function works.
    """

    def setUp(self):
        self.rig = Rig()
        self.rig.policy.amend(
            {"egress": {"simulation_only": True},
             "operations": {"continuous_enabled": True,
                            "max_jobs_per_run": 2}},
            OWNER, "test: a service that may do business in simulation")
        self.runtime = runtime_module.Runtime(self.rig)
        self.ran = []

        def one_job():
            self.ran.append(1)

            class Done:
                completed = True
                invoiced_cents = 0
            return Done()

        self.runtime._one_business_job = one_job

    def test_the_tick_runs_without_raising_and_does_work(self):
        line = self.runtime._tick_business()
        self.assertTrue(self.ran, "the tick did no job")
        self.assertIn("completed", line)

    def test_it_records_what_it_did_in_the_audit_log(self):
        self.runtime._tick_business()
        events = [e for e in self.rig.audit.events()
                  if e.get("event") == "runtime.business_ran"]
        self.assertTrue(events, "a bounded run must leave a record")
        self.assertTrue(events[-1].get("result"))

    def test_simulation_off_refuses_before_doing_anything(self):
        self.rig.policy.amend({"egress": {"simulation_only": False}},
                              OWNER, "test: simulation turned off")
        line = self.runtime._tick_business()
        self.assertIn("simulation only", line)
        self.assertEqual(self.ran, [], "it did work with simulation off")

    def test_opt_in_off_refuses_before_doing_anything(self):
        self.rig.policy.amend({"operations": {"continuous_enabled": False}},
                              OWNER, "test: not enabled")
        line = self.runtime._tick_business()
        self.assertIn("not run", line)
        self.assertEqual(self.ran, [])

    def test_a_second_executor_is_refused_while_the_lease_is_held(self):
        """The whole point of the lease: one process, then another."""
        held = self.runtime._take_business_lease()
        self.assertIsNotNone(held)
        other = runtime_module.Runtime(self.rig)
        other._one_business_job = lambda: None
        line = other._tick_business()
        self.assertIn("another executor", line)

    def test_the_lease_is_released_so_the_next_tick_can_run(self):
        self.runtime._tick_business()
        self.assertEqual(self.rig.audit and
                         self.runtime._business_lease_holder(), "")
        self.runtime._tick_business()
        self.assertEqual(len(self.ran), 4, "two ticks of two jobs each")

    def test_a_stale_lease_does_not_lock_the_business_out_for_ever(self):
        other = runtime_module.Runtime(self.rig)
        other._take_business_lease()
        self.runtime.BUSINESS_LEASE_SECONDS = -1.0
        try:
            self.assertEqual(self.runtime._business_lease_holder(), "")
        finally:
            self.runtime.BUSINESS_LEASE_SECONDS = \
                runtime_module.Runtime.BUSINESS_LEASE_SECONDS

    def test_the_last_run_is_exposed_for_the_control_centre(self):
        self.runtime._tick_business()
        self.assertTrue(self.runtime._business)
        self.assertIn("completed", self.runtime._business)


class TheStatusReportDistinguishesTheStates(unittest.TestCase):
    """A heartbeat says the process is alive. That is all it says.

    A process can be alive, its scheduler running, and its business
    cycle switched off -- which is exactly the state DeskPilot shipped
    in, unnoticed, because nothing distinguished it from working.
    """

    def setUp(self):
        self.rig = Rig()
        self.runtime = runtime_module.Runtime(self.rig)
        self.runtime.started = object()        # start() needs an owner key
        self.runtime.default_tasks()

    def test_every_declared_state_is_reported(self):
        states = self.runtime.business_status()["states"]
        for name in runtime_module.Runtime.BUSINESS_STATES:
            with self.subTest(state=name):
                self.assertIn(name, states)

    def test_connected_but_disabled_is_not_running(self):
        """The distinction a single boolean cannot carry."""
        status = self.runtime.business_status()
        self.assertTrue(status["business_cycle_connected"])
        self.assertFalse(status["business_cycle_enabled"])
        self.assertFalse(status["may_run_now"])
        self.assertTrue(status["states"]["SCHEDULER_PAUSED"])
        self.assertIn("continuous_enabled", status["why_not"])

    def test_enabling_it_clears_the_pause(self):
        self.rig.policy.amend({"operations": {"continuous_enabled": True}},
                              OWNER, "test: on")
        status = self.runtime.business_status()
        self.assertTrue(status["may_run_now"])
        self.assertFalse(status["states"]["SCHEDULER_PAUSED"])

    def test_the_halt_shows_as_halted_and_stops_it_running(self):
        self.rig.policy.amend(
            {"operations": {"continuous_enabled": True, "halted": True}},
            OWNER, "test: halted")
        status = self.runtime.business_status()
        self.assertTrue(status["states"]["HALTED"])
        self.assertFalse(status["may_run_now"])

    def test_simulation_is_reported_and_external_actions_are_disabled(self):
        status = self.runtime.business_status()
        self.assertTrue(status["states"]["SIMULATION"])
        self.assertTrue(status["states"]["EXTERNAL_DISABLED"])

    def test_it_reports_the_seven_figures(self):
        status = self.runtime.business_status()
        for field in ("last_cycle_started", "last_cycle_completed",
                      "last_successful_cycle", "last_failure",
                      "queue_depth", "active_workers", "executor_lease"):
            with self.subTest(field=field):
                self.assertIn(field, status)

    def test_the_lease_shows_free_before_any_run(self):
        self.assertEqual(self.runtime.business_status()["executor_lease"],
                         "free")

    def test_it_never_claims_to_be_operational(self):
        """It reports parts; whoever renders them says what they total."""
        status = self.runtime.business_status()
        self.assertNotIn("operational", status)
        self.assertNotIn("healthy", status)

    def test_a_read_failure_does_not_break_the_page(self):
        """A status call is a page render, not a transaction."""
        broken = runtime_module.Runtime(self.rig)
        broken.started = object()
        broken.default_tasks()
        broken.solvent = type("Gone", (), {
            "policy": self.rig.policy,
            "orchestrator": property(lambda _: (_ for _ in ()).throw(
                RuntimeError("database gone"))),
            "audit": self.rig.audit})()
        self.assertEqual(broken._queue_depth(), 0)
        self.assertEqual(broken._active_workers(), 0)
        self.assertEqual(broken._stranded_job_ids(), [])


class StartupSaysWhichFormatsItCanVerify(unittest.TestCase):
    """W3: the T2 prerequisite, surfaced at boot instead of per job.

    A fresh Windows install has no differential reader, because the
    installer runs no pip by design. DeskPilot already fails closed --
    a job needing a second opinion nobody can give is declined while it
    is still a quote. What was missing is that the owner found that out
    one job at a time.

    Not fatal, deliberately. Refusing to start over a missing optional
    reader would make a usable T1 installation impossible to bring up.
    """

    def _check(self, startup):
        found = [c for c in startup.checks if "T2" in c.name]
        self.assertEqual(len(found), 1, "exactly one T2 readiness check")
        return found[0]

    def test_the_check_exists_and_names_the_formats(self):
        import inspect

        source = inspect.getsource(runtime_module.startup_checks)
        self.assertIn("independent (T2) readers", source)
        self.assertIn("_DIFFERENTIAL_READERS", source)

    def test_it_is_not_fatal(self):
        import inspect

        source = inspect.getsource(runtime_module.startup_checks)
        block = source[source.index("independent (T2) readers"):]
        self.assertIn("fatal=False", block[:900])

    def test_a_missing_reader_names_the_remedy_and_the_consequence(self):
        """The owner must be able to tell a limitation from a fault."""
        import inspect

        source = inspect.getsource(runtime_module.startup_checks)
        self.assertIn("declined while it is still a quote", source)
        self.assertIn("verification", source)

    def test_it_never_downgrades_t2_to_t1(self):
        """It reports what can produce T2. It does not lower the bar."""
        import inspect

        source = inspect.getsource(runtime_module.startup_checks)
        block = source[source.index("independent (T2) readers"):]
        for forbidden in ("REQUIRED_TIER", "T1", "downgrade"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, block[:900])

    def test_a_person_signing_by_hand_does_not_count_as_a_reader(self):
        """Owner review is a T2 producer, but it is not a reader.

        Counting it would report every format as covered on a machine
        with no reader at all, which is the opposite of the point.
        """
        import inspect

        source = inspect.getsource(runtime_module.startup_checks)
        self.assertIn("not p.needs_a_person", source)


class TheOtherTwoDormantModulesAreStillDormantOnPurpose(unittest.TestCase):

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
