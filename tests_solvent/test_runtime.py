"""The 24/7 runtime: start safely, survive, stop honestly.

The theme running through these is that a restart must make Solvent *more*
cautious, never less. A process that comes back confident is a process that
sends the invoice twice.
"""

from __future__ import annotations

import pathlib
import tempfile
import unittest

from solvent import runtime as rt
from solvent.errors import FailClosed
from solvent.gate import ATTEMPTING, SETTLED
from solvent.harness import OWNER, Solvent
from solvent.types import (
    ActionClass, BlockedOn, JobState, Jurisdiction, LocationSignals,
    OperatingMode, PrivacyClass,
)

REPO = pathlib.Path(__file__).resolve().parent.parent


def db_path() -> str:
    return str(pathlib.Path(tempfile.mkdtemp()) / "solvent.db")


def enable_real_execution(solvent, destination="client.example"):
    """Put Solvent in the one posture where an external effect can really land."""
    solvent.policy.set_operating_mode(OperatingMode.NORMAL, OWNER, "test")
    solvent.policy.amend({
        "egress": {"simulation_only": False, "allowlist": [destination]},
        "permissions": {ActionClass.C2_EXTERNAL_COMMUNICATION.value: "AUTONOMOUS"},
    }, OWNER, "test: allow one destination")


class StartupChecks(unittest.TestCase):
    def setUp(self):
        self.solvent = Solvent()

    def test_local_mode_tolerates_an_in_memory_store(self):
        report = rt.startup_checks(self.solvent, mode=rt.Mode.LOCAL)
        self.assertTrue(report.safe_to_run, report.render())

    def test_always_on_mode_refuses_an_in_memory_store(self):
        """A 24/7 service whose ledger dies on restart is not a 24/7 service."""
        report = rt.startup_checks(self.solvent, mode=rt.Mode.ALWAYS_ON)
        self.assertFalse(report.safe_to_run)
        self.assertIn("durable storage", [c.name for c in report.fatal])

    def test_the_runtime_refuses_to_start_when_a_check_is_fatal(self):
        with self.assertRaises(FailClosed) as caught:
            rt.Runtime(self.solvent, mode=rt.Mode.ALWAYS_ON).start()
        self.assertIn("durable storage", str(caught.exception))

    def test_a_broken_audit_chain_is_fatal(self):
        """Evidence you cannot trust is worse than no evidence, so do not run."""
        import sqlite3

        solvent = Solvent(db_path())
        solvent.audit.record(event="e", authority="policy", initiator=OWNER,
                             why="w", decision="d", result="r")
        # Append a row whose prev_hash does not follow. The append-only triggers
        # permit INSERT, which is precisely why the chain, not the trigger, is
        # what detects a forged history.
        conn = sqlite3.connect(solvent.store.path)
        conn.execute(
            "INSERT INTO audit_log(ts,event,authority,initiator,why,payload,"
            "prev_hash,hash) VALUES(?,?,?,?,?,?,?,?)",
            ("2026-01-01T00:00:00+00:00", "forged", "policy", "attacker",
             "inserted by hand", "{}", "not-the-previous-hash", "made-up"))
        conn.commit()
        conn.close()

        report = rt.startup_checks(Solvent(solvent.store.path), mode=rt.Mode.LOCAL)
        self.assertFalse(report.safe_to_run)
        self.assertIn("audit chain", [c.name for c in report.fatal])

    def test_a_missing_owner_key_warns_rather_than_refusing_to_boot(self):
        """Refusing to start would leave the owner unable to inspect the gap."""
        report = rt.startup_checks(self.solvent, mode=rt.Mode.LOCAL)
        names = [c.name for c in report.warnings]
        self.assertIn("owner channel", names)
        self.assertTrue(report.safe_to_run)

    def test_startup_reports_the_egress_posture_it_actually_finds(self):
        report = rt.startup_checks(self.solvent, mode=rt.Mode.LOCAL)
        egress = next(c for c in report.checks if c.name == "egress posture")
        self.assertIn("fail-closed", egress.detail)


class HealthStates(unittest.TestCase):
    def setUp(self):
        self.solvent = Solvent()

    def test_a_governed_idle_solvent_is_healthy(self):
        state, _ = rt.health(self.solvent)
        self.assertIs(state, rt.Health.HEALTHY)

    def test_halt_reads_as_halted_not_healthy(self):
        self.solvent.policy.set_operating_mode(OperatingMode.HALT, OWNER, "test")
        state, _ = rt.health(self.solvent)
        self.assertIs(state, rt.Health.HALTED)
        self.assertFalse(state.may_start_work)

    def test_a_paused_mode_is_degraded(self):
        self.solvent.policy.set_operating_mode(
            OperatingMode.PAUSE_NEW_WORK, OWNER, "test")
        self.assertIs(rt.health(self.solvent)[0], rt.Health.DEGRADED)

    def test_a_blocked_job_is_blocked_not_broken(self):
        job = self.solvent.orchestrator.intake(
            title="t", client_id="c", quoted_cents=1000, initiator="test",
            signals=LocationSignals(project=Jurisdiction(state="NC")))
        self.solvent.orchestrator.block(job_id=job, blocked_on=BlockedOn.CLIENT,
                                        why="waiting on the client", initiator="test")
        state, why = rt.health(self.solvent)
        self.assertIs(state, rt.Health.BLOCKED)
        self.assertIn("CLIENT", why)
        # Blocked is a correct state to be in, so work may still start.
        self.assertTrue(state.may_start_work)

    def test_a_failed_startup_reads_as_not_ready(self):
        started = rt.startup_checks(self.solvent, mode=rt.Mode.ALWAYS_ON)
        self.assertIs(rt.health(self.solvent, started=started)[0], rt.Health.NOT_READY)

    def test_health_is_derived_and_never_stored(self):
        """A stored health value could disagree with the authorities."""
        source = (REPO / "solvent" / "runtime.py").read_text()
        self.assertNotIn("INSERT INTO", source)


class WriteAheadIntent(unittest.TestCase):
    """An effect that may have happened must leave a trace before it happens."""

    def setUp(self):
        self.solvent = Solvent(db_path())
        enable_real_execution(self.solvent)

    def rows(self):
        return self.solvent.store.raw_readonly(
            "SELECT * FROM action_requests ORDER BY ts")

    def act(self, perform):
        return self.solvent.gate.request(
            action_class=ActionClass.C2_EXTERNAL_COMMUNICATION,
            destination="client.example", privacy=PrivacyClass.CLIENT_CONFIDENTIAL,
            purpose="deliver", job_id="J1", perform=perform)

    def test_an_intent_is_written_before_the_effect_runs(self):
        seen = {}

        def perform():
            seen["rows_during"] = len(self.rows())
            return "sent"

        self.act(perform)
        self.assertEqual(seen["rows_during"], 1,
                         "the intent must be durable before perform() can run")

    def test_a_completed_action_settles(self):
        result = self.act(lambda: "sent")
        phases = [r["phase"] for r in self.rows()]
        self.assertEqual(phases, [ATTEMPTING, SETTLED])
        self.assertTrue(result.allowed)
        self.assertEqual(self.solvent.gate.unsettled(), [])

    def test_a_transport_failure_still_settles(self):
        """A failure we saw is knowledge. It is not the unknown case."""
        with self.assertRaises(RuntimeError):
            self.act(lambda: (_ for _ in ()).throw(RuntimeError("connection reset")))
        self.assertEqual(self.solvent.gate.unsettled(), [],
                         "a seen failure is settled, not unknown")

    def test_a_simulated_action_writes_no_intent(self):
        """Nothing left the process, so there is nothing to be uncertain about."""
        self.solvent.policy.amend({"egress": {"simulation_only": True}},
                                  OWNER, "back to simulation")
        self.act(lambda: "should not run")
        self.assertEqual([r["phase"] for r in self.rows()], [SETTLED])

    def test_a_denied_action_writes_no_intent(self):
        self.solvent.gate.request(
            action_class=ActionClass.C2_EXTERNAL_COMMUNICATION,
            destination="not-allowlisted.example",
            privacy=PrivacyClass.CLIENT_CONFIDENTIAL, purpose="deliver",
            job_id="J1", perform=lambda: "should not run")
        self.assertEqual([r["phase"] for r in self.rows()], [SETTLED])


class CrashDuringAnExternalAction(unittest.TestCase):
    """The case that makes 24/7 dangerous: the process dies mid-effect.

    The crash is modelled with ``KeyboardInterrupt``, which the Gate's
    ``except Exception`` deliberately does not catch, so nothing settles — the
    exact durable signature a SIGKILL leaves. A real forked SIGKILL cannot be used
    here because the egress guard denies ``subprocess.Popen`` once installed, and
    that refusal is a control worth keeping rather than working around. The true
    process-death case was reproduced separately against this same code path.
    """

    def setUp(self):
        self.db = db_path()
        self.solvent = Solvent(self.db)
        enable_real_execution(self.solvent)
        self.job = self.solvent.orchestrator.intake(
            title="cleanup", client_id="c", quoted_cents=45_000, initiator="test",
            signals=LocationSignals(project=Jurisdiction(state="NC")))

    def crash_mid_action(self) -> None:
        def dies_mid_effect():
            raise KeyboardInterrupt("the process is killed here")

        with self.assertRaises(KeyboardInterrupt):
            self.solvent.gate.request(
                action_class=ActionClass.C2_EXTERNAL_COMMUNICATION,
                destination="client.example",
                privacy=PrivacyClass.CLIENT_CONFIDENTIAL, purpose="deliver",
                job_id=self.job, perform=dies_mid_effect)

    def restart(self) -> Solvent:
        self.solvent.store.close()
        return Solvent(self.db)

    def test_the_attempt_survives_the_crash(self):
        self.crash_mid_action()
        unsettled = self.restart().gate.unsettled()
        self.assertEqual(len(unsettled), 1)
        self.assertEqual(unsettled[0]["destination"], "client.example")

    def test_nothing_settles_so_the_outcome_stays_unknown(self):
        self.crash_mid_action()
        phases = [r["phase"] for r in self.solvent.store.raw_readonly(
            "SELECT phase FROM action_requests")]
        self.assertEqual(phases, [ATTEMPTING])

    def test_recovery_blocks_the_job_on_the_owner_and_does_not_retry(self):
        self.crash_mid_action()
        restarted = self.restart()
        notes = rt.reconcile(restarted)

        self.assertEqual(len(notes), 1)
        self.assertIn("unknown", notes[0])
        job = restarted.orchestrator.job(self.job)
        self.assertIs(job.state, JobState.BLOCKED)
        self.assertIs(job.blocked_on, BlockedOn.OWNER)

    def test_recovery_performs_no_external_action_of_its_own(self):
        self.crash_mid_action()
        restarted = self.restart()
        before = len(restarted.store.raw_readonly("SELECT 1 FROM action_requests"))
        rt.reconcile(restarted)
        after = len(restarted.store.raw_readonly("SELECT 1 FROM action_requests"))
        self.assertEqual(before, after, "recovery attempted an external action")

    def test_recovery_is_idempotent(self):
        self.crash_mid_action()
        restarted = self.restart()
        rt.reconcile(restarted)
        second = rt.reconcile(restarted)
        self.assertTrue(all("already BLOCKED" in n for n in second), second)

    def test_health_reports_recovering_until_it_is_reconciled(self):
        self.crash_mid_action()
        self.assertIs(rt.health(self.restart())[0], rt.Health.RECOVERING)

    def test_the_restarted_runtime_reconciles_on_start(self):
        self.crash_mid_action()
        restarted = self.restart()
        runtime = rt.Runtime(restarted, mode=rt.Mode.ALWAYS_ON)
        runtime.start()
        self.assertTrue(any("recovery" in line for line in runtime.log), runtime.log)

    def test_the_unsettled_action_is_visible_in_the_audit_log(self):
        self.crash_mid_action()
        restarted = self.restart()
        gate_events = [e for e in restarted.audit.events()
                       if e["authority"] == "gate"]
        self.assertTrue(gate_events, "the attempt left no audit trace")


class StatePersistsAcrossRestarts(unittest.TestCase):
    def setUp(self):
        self.db = db_path()

    def test_halt_survives_a_restart(self):
        first = Solvent(self.db)
        first.policy.set_operating_mode(OperatingMode.HALT, OWNER, "owner halted")
        first.store.close()

        restarted = Solvent(self.db)
        self.assertIs(restarted.policy.operating_mode, OperatingMode.HALT)
        self.assertIs(rt.health(restarted)[0], rt.Health.HALTED)

    def test_a_restart_cannot_clear_halt_by_starting_the_runtime(self):
        first = Solvent(self.db)
        first.policy.set_operating_mode(OperatingMode.HALT, OWNER, "owner halted")
        first.store.close()

        restarted = Solvent(self.db)
        rt.Runtime(restarted, mode=rt.Mode.ALWAYS_ON).start()
        self.assertIs(restarted.policy.operating_mode, OperatingMode.HALT)

    def test_a_consumed_owner_approval_stays_consumed(self):
        """Restarting must not hand the owner's single-use approval back."""
        import os
        os.environ["SOLVENT_OWNER_KEY"] = "a" * 64
        try:
            first = Solvent(self.db)
            approval = first.owner.issue(
                owner_identity=OWNER, subject="deliver",
                action_class=ActionClass.C2_EXTERNAL_COMMUNICATION,
                job_id="J1", max_cents=0, ttl_minutes=10)
            first.owner.consume(approval)
            first.store.close()

            restarted = Solvent(self.db)
            problem = restarted.owner.verify(
                approval, action_class=ActionClass.C2_EXTERNAL_COMMUNICATION,
                job_id="J1", amount_cents=0)
            self.assertTrue(problem, "a used approval came back usable")
        finally:
            os.environ.pop("SOLVENT_OWNER_KEY", None)

    def test_ledger_audit_and_policy_all_survive(self):
        first = Solvent(self.db)
        first.ledger.open_payment(job_id="J1", amount_cents=45_000,
                                  rail="stripe", currency="usd")
        version = first.policy.version
        first.store.close()

        restarted = Solvent(self.db)
        self.assertIsNotNone(restarted.ledger.payment_for("J1"))
        self.assertEqual(restarted.policy.version, version)
        self.assertTrue(restarted.audit.verify_chain()[0])

    def test_a_processed_payment_event_is_not_reprocessed_after_a_restart(self):
        from tests_solvent.test_payments import SECRET, body, headers
        from solvent.payments import StripeRail

        first = Solvent(self.db)
        first.ledger.open_payment(job_id="J1", amount_cents=45_000,
                                  rail="stripe", currency="usd")
        raw = body()
        first.ledger.apply_payment_event(
            StripeRail().verify_event(raw, headers(raw), SECRET))
        collected = first.ledger.collected_for("J1")
        first.store.close()

        restarted = Solvent(self.db)
        outcome = restarted.ledger.apply_payment_event(
            StripeRail().verify_event(raw, headers(raw), SECRET))
        self.assertIn("duplicate", outcome)
        self.assertEqual(restarted.ledger.collected_for("J1"), collected)


class Scheduling(unittest.TestCase):
    def setUp(self):
        self.solvent = Solvent()
        self.now = 0.0
        self.runtime = rt.Runtime(self.solvent, mode=rt.Mode.LOCAL,
                                  clock=lambda: self.now, sleep=lambda _s: None)

    def test_a_task_does_not_run_before_it_is_due(self):
        runs = []
        self.runtime.add_task("health", lambda: runs.append(1) or "ok")
        self.runtime.run_due()                      # t=0: first run
        self.runtime.run_due()                      # t=0: not due again
        self.assertEqual(len(runs), 1)

    def test_a_task_runs_again_once_its_interval_has_passed(self):
        runs = []
        task = self.runtime.add_task("health", lambda: runs.append(1) or "ok")
        self.runtime.run_due()
        self.now += task.interval_s
        self.runtime.run_due()
        self.assertEqual(len(runs), 2)

    def test_intervals_come_from_policy_when_set(self):
        self.solvent.policy.amend({"runtime": {"intervals": {"discovery": 42}}},
                                  OWNER, "faster discovery")
        runtime = rt.Runtime(self.solvent, mode=rt.Mode.LOCAL)
        self.assertEqual(runtime.add_task("discovery", lambda: "x").interval_s, 42)

    def test_one_failing_task_does_not_stop_the_loop(self):
        def boom():
            raise RuntimeError("transient")

        self.runtime.add_task("reconciliation", boom)
        self.runtime.add_task("health", lambda: "fine")
        results = self.runtime.run_due()
        self.assertTrue(any("FAILED" in r for r in results))
        self.assertTrue(any("fine" in r for r in results))

    def test_repeated_failure_is_recorded_rather_than_silently_retried(self):
        def boom():
            raise RuntimeError("persistent")

        task = self.runtime.add_task("health", boom)
        for _ in range(rt.FAILURE_THRESHOLD):
            self.runtime.run_due()
            self.now += task.interval_s
        events = [e for e in self.solvent.audit.events()
                  if e["event"] == "runtime.task_failing"]
        self.assertTrue(events)

    def test_the_log_is_bounded(self):
        """A process that runs for months must not accumulate its own history."""
        for i in range(500):
            self.runtime._say(f"line {i}")
        self.assertLessEqual(len(self.runtime.log), 200)

    def test_discovery_does_not_run_while_halted(self):
        self.solvent.policy.set_operating_mode(OperatingMode.HALT, OWNER, "test")
        self.assertIn("skipped", self.runtime._tick_discovery())

    def test_discovery_skips_when_no_source_is_permitted(self):
        self.assertIn("skipped", self.runtime._tick_discovery())


class Shutdown(unittest.TestCase):
    def setUp(self):
        self.db = db_path()
        self.solvent = Solvent(self.db)

    def test_the_loop_stops_when_asked(self):
        runtime = rt.Runtime(self.solvent, mode=rt.Mode.LOCAL, sleep=lambda _s: None)
        runtime.stop("SIGTERM")
        self.assertEqual(runtime.run_forever(max_ticks=100), "SIGTERM")

    def test_shutdown_never_completes_a_job(self):
        """A service stopping is not evidence that work got done."""
        job = self.solvent.orchestrator.intake(
            title="t", client_id="c", quoted_cents=1000, initiator="test",
            signals=LocationSignals(project=Jurisdiction(state="NC")))
        runtime = rt.Runtime(self.solvent, mode=rt.Mode.LOCAL)
        runtime.stop("SIGTERM")
        runtime.shutdown()

        reopened = Solvent(self.db)
        self.assertIsNot(reopened.orchestrator.job(job).state, JobState.COMPLETE)

    def test_shutdown_is_recorded(self):
        runtime = rt.Runtime(self.solvent, mode=rt.Mode.LOCAL)
        runtime.stop("SIGTERM")
        runtime.shutdown()
        reopened = Solvent(self.db)
        self.assertTrue(any(e["event"] == "runtime.stopped"
                            for e in reopened.audit.events()))


class TheRuntimeIsNotAnAuthority(unittest.TestCase):
    """It schedules. It must never decide."""

    def setUp(self):
        self.source = (REPO / "solvent" / "runtime.py").read_text()

    def test_it_never_amends_policy(self):
        self.assertNotIn(".amend(", self.source)

    def test_it_never_sets_the_operating_mode(self):
        self.assertNotIn("set_operating_mode", self.source)

    def test_it_never_authorises_spending(self):
        for forbidden in ("authorize_spend", "decide(", "approve"):
            self.assertNotIn(forbidden, self.source, forbidden)

    def test_it_never_performs_an_external_action(self):
        """Every external effect goes through the Gate, and the Gate is called
        by business code, not by the scheduler."""
        self.assertNotIn("perform=", self.source)

    def test_it_never_completes_or_delivers_a_job(self):
        for forbidden in ("record_delivery", ".complete(", "mark_verified"):
            self.assertNotIn(forbidden, self.source, forbidden)

    def test_it_only_blocks_jobs_which_is_the_cautious_direction(self):
        self.assertIn("orchestrator.block(", self.source)
        self.assertNotIn("orchestrator.unblock(", self.source)


if __name__ == "__main__":
    unittest.main()
