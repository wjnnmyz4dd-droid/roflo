"""§29–§30: inject a fault at each stage, and check the whole matrix each time.

The question is not "does it crash safely" — the earlier suites establish that.
It is whether a crash leaves behind **enough to act on**: a record that it
happened, what Solvent was doing, the last durable state, whether the same thing
has happened before, whether anything outside this machine became uncertain, and
whether a person was told.

Every fault is injected at a seam an authority actually calls, so the failure
arrives where a real one would. A fault injected into a hook added for the
purpose tests the harness.
"""

from __future__ import annotations

import os
import pathlib
import tempfile
import unittest

from solvent import notify
from solvent import resilience as res
from solvent.harness import (CSV_PROMOTION, OWNER, Solvent, provision_capability,
                             run_csv_job, supervised)
from tests_solvent import faults
from tests_solvent import fixtures_csv as fx

TEST_PHONE = "+15550000000"


class Base(unittest.TestCase):
    def setUp(self):
        previous = os.environ.get(notify.PHONE_ENV)
        os.environ[notify.PHONE_ENV] = TEST_PHONE
        self.addCleanup(
            lambda: (os.environ.__setitem__(notify.PHONE_ENV, previous)
                     if previous is not None
                     else os.environ.pop(notify.PHONE_ENV, None)))
        self.s = Solvent()
        provision_capability(self.s, CSV_PROMOTION, owner_identity=OWNER)

    def crash(self, *, component="orchestrator", operation="commit_requirements",
              message="injected fault", external_effect="", severity=""):
        with self.assertRaises(faults.InjectedFault):
            with supervised(self.s, component=component, operation=operation,
                            external_effect=external_effect, severity=severity,
                            checkpoint="BASELINE_COMMITTED") as trace:
                self.trace = trace
                raise faults.InjectedFault(message)
        return self.s.resilience.incidents()[0]


class TheMatrixForOneCrash(Base):
    """Every column of §30's matrix, against one injected fault."""

    def setUp(self):
        super().setUp()
        self.incident = self.crash()

    def test_the_failure_was_detected(self):
        self.assertTrue(self.incident)
        self.assertEqual(self.incident["error_class"], "InjectedFault")

    def test_the_flight_recorder_captured_the_context(self):
        frames = self.s.resilience.frames(self.incident["trace_id"])
        self.assertEqual([f["state"] for f in frames],
                         ["ATTEMPTING", "FAILED"])

    def test_the_last_safe_checkpoint_is_known(self):
        self.assertEqual(self.incident["checkpoint"], "BASELINE_COMMITTED")

    def test_a_fingerprint_was_generated(self):
        self.assertTrue(self.incident["fingerprint"])
        self.assertEqual(len(self.incident["fingerprint"]), 16)

    def test_it_was_recognised_as_a_new_failure_class(self):
        """Asserted on the status Solvent recorded at the time, which is the
        decision made with the right information, and on a correlation that
        excludes the incident being asked about — because a correlation that
        counts itself reports every first occurrence as a recurrence."""
        self.assertEqual(self.incident["status"], res.DETECTED)
        correlation = self.s.resilience.correlate(
            self.incident["fingerprint"], exclude=self.incident["id"])
        self.assertFalse(correlation.seen_before)

    def test_no_external_effect_was_claimed_uncertain(self):
        """Saying an effect might have happened when it could not have is its
        own kind of lie: it sends the owner to check something for nothing."""
        self.assertEqual(self.incident["external_uncertainty"], "")
        self.assertNotEqual(self.incident["status"],
                            res.OWNER_ACTION_REQUIRED)

    def test_the_owner_was_notified(self):
        notifications = self.s.notifier.notifications()
        self.assertEqual(len(notifications), 1)
        self.assertIn("commit_requirements", notifications[0]["event"])

    def test_the_exception_still_propagated(self):
        """Recording a failure is not handling one. A supervisor that swallowed
        the exception would turn a fail-closed system into a fail-quiet one."""
        with self.assertRaises(faults.InjectedFault):
            with supervised(self.s, component="c", operation="o"):
                raise faults.InjectedFault("still raises")

    def test_nothing_governed_moved(self):
        self.assertTrue(self.s.policy.get("egress", "simulation_only",
                                          default=True))
        self.assertEqual(self.s.ledger.real_revenue_cents(), 0)
        self.assertTrue(self.s.audit.verify_chain()[0])

    def test_the_incident_reaches_the_audit_log(self):
        self.assertTrue([e for e in self.s.audit.events()
                         if e["event"] == "resilience.incident"])


class AnUncertainExternalEffect(Base):
    """§17. The one case where retrying is the wrong answer."""

    def setUp(self):
        super().setUp()
        self.incident = self.crash(
            component="gate", operation="deliver",
            external_effect="the client may already have received the file")

    def test_it_is_recorded_as_uncertain(self):
        self.assertIn("may already have received",
                      self.incident["external_uncertainty"])

    def test_it_goes_to_a_person_rather_than_being_retried(self):
        self.assertEqual(self.incident["status"], res.OWNER_ACTION_REQUIRED)
        self.assertIn("must not be retried", self.incident["owner_action"])

    def test_the_owner_is_called_not_merely_texted(self):
        """An effect that may have reached a client is the loudest thing that
        can happen without anybody noticing."""
        notification = self.s.notifier.notifications()[0]
        self.assertEqual(notification["severity"], notify.CRITICAL)

    def test_the_incident_page_asks_the_question_directly(self):
        self.assertTrue(self.incident["external_uncertainty"])


class RecurrenceAndRegression(Base):
    def test_the_same_fault_twice_is_recognised(self):
        first = self.crash(message="connection reset by peer after 30s")
        second = self.crash(message="connection reset by peer after 45s")
        self.assertEqual(first["fingerprint"], second["fingerprint"])
        self.assertEqual(
            self.s.resilience.incident(second["id"])["status"], res.RECURRED)

    def test_a_recurrence_does_not_send_a_second_text(self):
        """One conversation per kind of failure. A component failing every
        minute is an escalating thread, not a thousand texts."""
        for _ in range(20):
            self.crash(message="connection reset")
        self.assertEqual(len(self.s.notifier._providers[notify.SMS].sent), 1)

    def test_the_same_fault_after_a_verified_fix_is_a_regression(self):
        first = self.crash(message="connection reset")
        self.s.resilience.record_root_cause(first["id"],
                                            root_cause="no retry budget")
        self.s.resilience.record_fix(first["id"], fix="add a retry budget",
                                     prevention="a regression test")
        self.s.resilience.verify_fix(first["id"],
                                     verification_ref="tests_solvent/x.py")
        again = self.crash(message="connection reset")
        correlation = self.s.resilience.correlate(again["fingerprint"],
                                                  exclude=again["id"])
        self.assertTrue(correlation.regression)
        self.assertIn("regression", correlation.summary)

    def test_a_regression_is_escalated_louder_than_a_first_occurrence(self):
        first = self.crash(message="connection reset")
        self.s.resilience.record_root_cause(first["id"], root_cause="cause")
        self.s.resilience.record_fix(first["id"], fix="fix")
        self.s.resilience.verify_fix(first["id"], verification_ref="a test")
        self.s.notifier.acknowledge(self.s.notifier.notifications()[0]["id"])
        self.crash(message="connection reset")
        severities = [n["severity"] for n in self.s.notifier.notifications()]
        self.assertIn(notify.URGENT, severities)

    def test_the_prior_fix_is_offered_the_next_time(self):
        first = self.crash(message="connection reset")
        self.s.resilience.record_root_cause(first["id"], root_cause="no budget")
        self.s.resilience.record_fix(first["id"], fix="add a budget")
        self.s.resilience.verify_fix(first["id"], verification_ref="a test")
        again = self.crash(message="connection reset")
        correlation = self.s.resilience.correlate(again["fingerprint"],
                                                  exclude=again["id"])
        self.assertEqual(correlation.prior_fix, "add a budget")


class EveryStageCanFailAndBeRecorded(Base):
    """§29. A fault at each seam, and each one leaves a usable record."""

    def test_each_stage_produces_an_incident_naming_that_stage(self):
        for stage in sorted(faults.STAGES):
            with self.subTest(stage=stage):
                solvent = Solvent()
                provision_capability(solvent, CSV_PROMOTION,
                                     owner_identity=OWNER)
                component, operation = faults.STAGES[stage]
                with self.assertRaises(faults.InjectedFault):
                    with supervised(solvent, component=component,
                                    operation=operation):
                        raise faults.InjectedFault(f"fault at {stage}")
                incident = solvent.resilience.incidents()[0]
                self.assertEqual(incident["component"], component)
                self.assertEqual(incident["operation"], operation)
                self.assertTrue(incident["fingerprint"])

    def test_a_fault_at_a_different_stage_is_a_different_failure_class(self):
        prints = set()
        for stage in sorted(faults.STAGES):
            component, operation = faults.STAGES[stage]
            prints.add(res.fingerprint(component=component, operation=operation,
                                       error_class="InjectedFault",
                                       signature="the same message everywhere"))
        self.assertEqual(len(prints), len(faults.STAGES),
                         "two stages fingerprint identically, so a failure in "
                         "one would be reported as a recurrence of the other")

    def test_an_unknown_stage_is_refused_rather_than_silently_skipped(self):
        with self.assertRaises(KeyError):
            faults.inject(self.s, "a-stage-that-does-not-exist")


class AFaultInTheRealPipeline(Base):
    """Injected into the pipeline itself, not into a supervised block."""

    def test_a_fault_during_requirements_leaves_the_job_undelivered(self):
        source, work = fx.workspace(fx.DUPLICATES)
        with faults.inject(self.s, "requirements"):
            with self.assertRaises(faults.InjectedFault):
                run_csv_job(source=source, requirements=list(fx.SIMPLE),
                            workdir=work, solvent=self.s, client_id="c",
                            title="crashed job")
        self.assertEqual(self.s.ledger.real_revenue_cents(), 0)
        self.assertTrue(self.s.audit.verify_chain()[0])

    def test_a_fault_at_the_delivery_gate_does_not_deliver(self):
        source, work = fx.workspace(fx.DUPLICATES)
        with faults.inject(self.s, "delivery_gate"):
            with self.assertRaises(faults.InjectedFault):
                run_csv_job(source=source, requirements=list(fx.SIMPLE),
                            workdir=work, solvent=self.s, client_id="c",
                            title="crashed at the gate")
        delivered = [j for j in self.s.orchestrator.jobs()
                     if j.state.value in ("AWAITING_PAYMENT", "COMPLETE")]
        self.assertEqual(delivered, [])

    def test_a_transient_fault_that_stops_lets_work_through_afterwards(self):
        """The distinction between a broken dependency and a bad minute."""
        source, work = fx.workspace(fx.DUPLICATES)
        with faults.slow_then_failing(self.s.orchestrator, "intake", failures=1):
            with self.assertRaises(faults.InjectedFault):
                run_csv_job(source=source, requirements=list(fx.SIMPLE),
                            workdir=work, solvent=self.s, client_id="c",
                            title="first attempt")
            source, work = fx.workspace(fx.DUPLICATES)
            report = run_csv_job(source=source, requirements=list(fx.SIMPLE),
                                 workdir=work, solvent=self.s, client_id="c",
                                 title="second attempt")
        self.assertTrue(report.delivered, report.escalated)


class ContainmentAfterRepeatedFailure(Base):
    """§18–§20. A dependency that keeps failing, and bounded recovery."""

    def setUp(self):
        super().setUp()
        self.at = [1000.0]

    def clock(self):
        return self.at[0]

    def test_repeated_failure_contains_the_dependency(self):
        for _ in range(res.DEFAULT_FAILURE_THRESHOLD):
            self.s.resilience.failure("model", why="provider 503",
                                      clock=self.clock)
        allowed, why = self.s.resilience.may_call("model", clock=self.clock)
        self.assertFalse(allowed)
        self.assertIn("contained", why)

    def test_containment_grants_no_alternative(self):
        """A breaker stops Solvent hammering something. It cannot send the work
        to an uncleared provider instead, because that is a permission question
        and containment is not allowed to answer one."""
        for _ in range(res.DEFAULT_FAILURE_THRESHOLD):
            self.s.resilience.failure("model", clock=self.clock)
        before = dict(self.s.policy.approved_models())
        self.assertEqual(dict(self.s.policy.approved_models()), before)
        self.assertNotIn("fallback", self.s.resilience.breaker("model"))

    def test_recovery_is_one_canary_and_not_the_whole_workload(self):
        for _ in range(res.DEFAULT_FAILURE_THRESHOLD):
            self.s.resilience.failure("model", clock=self.clock)
        self.at[0] += res.DEFAULT_COOLDOWN_SECONDS + 1
        allowed, why = self.s.resilience.may_call("model", clock=self.clock)
        self.assertTrue(allowed)
        self.assertIn("one canary call", why)
        self.assertEqual(self.s.resilience.breaker("model")["state"],
                         res.HALF_OPEN)

    def test_a_failed_canary_does_not_restore_the_workload(self):
        for _ in range(res.DEFAULT_FAILURE_THRESHOLD):
            self.s.resilience.failure("model", clock=self.clock)
        self.at[0] += res.DEFAULT_COOLDOWN_SECONDS + 1
        self.s.resilience.may_call("model", clock=self.clock)
        self.s.resilience.record_canary("model", passed=False,
                                        detail="still failing", clock=self.clock)
        self.assertFalse(self.s.resilience.may_call("model",
                                                   clock=self.clock)[0])

    def test_flapping_is_bounded_rather_than_endless(self):
        """Each failed canary restarts the cooldown, so a service flapping
        between working and not working cannot produce a retry storm."""
        for cycle in range(5):
            for _ in range(res.DEFAULT_FAILURE_THRESHOLD):
                self.s.resilience.failure("model", clock=self.clock)
            self.at[0] += res.DEFAULT_COOLDOWN_SECONDS + 1
            self.s.resilience.may_call("model", clock=self.clock)
            self.s.resilience.record_canary("model", passed=False,
                                            clock=self.clock)
            self.assertEqual(self.s.resilience.breaker("model")["state"],
                             res.OPEN)


class RestartAfterACrash(unittest.TestCase):
    def test_the_incident_the_notification_and_the_breaker_all_survive(self):
        previous = os.environ.get(notify.PHONE_ENV)
        os.environ[notify.PHONE_ENV] = TEST_PHONE
        self.addCleanup(
            lambda: (os.environ.__setitem__(notify.PHONE_ENV, previous)
                     if previous is not None
                     else os.environ.pop(notify.PHONE_ENV, None)))
        with tempfile.TemporaryDirectory() as tmp:
            path = str(pathlib.Path(tmp) / "solvent.db")
            before = Solvent(path)
            provision_capability(before, CSV_PROMOTION, owner_identity=OWNER)
            with self.assertRaises(faults.InjectedFault):
                with supervised(before, component="payments",
                                operation="ingest",
                                external_effect="a webhook may be acknowledged",
                                checkpoint="SPOOL_READ"):
                    raise faults.InjectedFault("connection reset")
            for _ in range(res.DEFAULT_FAILURE_THRESHOLD):
                before.resilience.failure("payments")
            incident_id = before.resilience.incidents()[0]["id"]
            before.store.close()

            after = Solvent(path)
            incident = after.resilience.incident(incident_id)
            self.assertEqual(incident["status"], res.OWNER_ACTION_REQUIRED)
            self.assertEqual(incident["checkpoint"], "SPOOL_READ")
            self.assertTrue(incident["external_uncertainty"])
            self.assertEqual(after.resilience.breaker("payments")["state"],
                             res.OPEN)
            self.assertTrue(after.notifier.unresolved())
            self.assertTrue(after.audit.verify_chain()[0])
            self.assertTrue(after.resilience.frames(incident["trace_id"]))

    def test_a_restart_does_not_resolve_an_uncertain_effect(self):
        """Coming back up establishes nothing about what happened before."""
        with tempfile.TemporaryDirectory() as tmp:
            path = str(pathlib.Path(tmp) / "solvent.db")
            before = Solvent(path)
            with self.assertRaises(faults.InjectedFault):
                with supervised(before, component="gate", operation="deliver",
                                external_effect="the client may have the file",
                                notify_owner=False):
                    raise faults.InjectedFault("crash")
            incident_id = before.resilience.incidents()[0]["id"]
            before.store.close()
            after = Solvent(path)
            self.assertEqual(after.resilience.incident(incident_id)["status"],
                             res.OWNER_ACTION_REQUIRED)


class TheHarnessIsTestOnly(unittest.TestCase):
    def test_nothing_in_the_package_imports_the_fault_harness(self):
        """Production code containing a way to inject a fault contains a way to
        inject a fault, however carefully the flag is named."""
        for path in pathlib.Path("solvent").rglob("*.py"):
            with self.subTest(module=str(path)):
                source = path.read_text()
                self.assertNotIn("from tests_solvent", source)
                self.assertNotIn("import faults", source)

    def test_the_stages_all_name_a_real_seam(self):
        """A fault injected into a place nothing uses tests the harness."""
        solvent = Solvent()
        for stage, (owner, attribute) in faults.STAGES.items():
            with self.subTest(stage=stage):
                target = getattr(solvent, owner, None)
                self.assertIsNotNone(target, f"{owner} is not on Solvent")
                self.assertTrue(callable(getattr(target, attribute, None)),
                                f"{owner}.{attribute} is not callable")
