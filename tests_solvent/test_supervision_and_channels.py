"""Requirements 6.7 and 6.10: noticing, without becoming an authority.

Everything else in this system refuses badly-formed work. Nothing
watched for work that was accepted correctly and then stopped moving
-- a worker that died holding a job, a channel answering 401 since
midnight, a ledger that stopped opening. Those do not raise. They sit.

The constraint the owner put on the fix is the interesting part: the
supervisor must not become another autonomous authority capable of
overriding safeguards. So the tests below spend as much effort on
what it cannot do as on what it finds.
"""

from __future__ import annotations

import tempfile
import unittest

from solvent import channelhealth as ch
from solvent import supervision as sup
from solvent.harness import OWNER, Solvent

BUDGET = {"max_owed_seconds": 100_000.0, "max_concurrent_jobs": 4,
          "max_execution_workers": 5, "max_verification_workers": 5,
          "max_open_bids": 4, "cpu_share": 0.5, "min_free_disk_bytes": 0,
          "max_memory_used_percent": 0.0,
          "skills": ["csv-cleanup", "data-entry"],
          "pipeline_target": 4, "replenish_at": 2, "halted": False}


class Base(unittest.TestCase):

    def setUp(self):
        self.box = tempfile.TemporaryDirectory()
        self.addCleanup(self.box.cleanup)
        self.s = Solvent(f"{self.box.name}/sup.db")
        self.addCleanup(self.close)
        self.s.policy.amend({"capacity": BUDGET}, OWNER, "test")

    def close(self):
        try:
            self.s.store.close()
        except Exception:
            pass

    def conditions(self):
        return {f.condition for f in self.s.supervisor.check()}


class AChannelNobodyHasCalledIsNotHealthy(unittest.TestCase):
    """Silence is not health. The default is UNCERTAIN."""

    def setUp(self):
        self.health = ch.ChannelHealth()

    def test_an_untested_channel_is_uncertain(self):
        self.assertEqual(self.health.health("x").state, ch.UNCERTAIN)

    def test_an_untested_channel_is_paused(self):
        paused, why = self.health.paused("x")
        self.assertTrue(paused)
        self.assertIn("untested token is not a working one", why)

    def test_a_successful_call_makes_it_healthy(self):
        self.health.observe("x", ch.OK)
        self.assertEqual(self.health.health("x").state, ch.HEALTHY)
        self.assertFalse(self.health.health("x").paused)

    def test_an_expired_token_is_uncertain_immediately(self):
        self.health.observe("x", ch.OK)
        self.health.observe("x", ch.AUTH_FAILED, detail="401")
        self.assertEqual(self.health.health("x").state, ch.UNCERTAIN)

    def test_a_restriction_blocks_rather_than_degrades(self):
        self.health.observe("x", ch.OK)
        self.health.observe("x", ch.RESTRICTED, detail="account limited")
        self.assertEqual(self.health.health("x").state, ch.BLOCKED)

    def test_one_rate_limit_is_survivable(self):
        self.health.observe("x", ch.OK)
        self.health.observe("x", ch.RATE_LIMITED)
        self.assertEqual(self.health.health("x").state, ch.DEGRADED)

    def test_repeated_rate_limits_stop_being_survivable(self):
        self.health.observe("x", ch.OK)
        for _ in range(3):
            self.health.observe("x", ch.RATE_LIMITED)
        self.assertTrue(self.health.health("x").paused)

    def test_success_clears_the_count(self):
        """Otherwise the system gets permanently more pessimistic."""
        self.health.observe("x", ch.OK)
        for _ in range(3):
            self.health.observe("x", ch.RATE_LIMITED)
        self.health.observe("x", ch.OK)
        self.assertEqual(self.health.health("x").state, ch.HEALTHY)

    def test_a_policy_change_makes_the_status_unclear(self):
        self.health.observe("x", ch.OK)
        self.health.observe("x", ch.POLICY_CHANGED, detail="new terms")
        self.assertTrue(self.health.health("x").paused)

    def test_an_unknown_observation_is_refused_not_guessed(self):
        with self.assertRaises(ValueError):
            self.health.observe("x", "vibes")

    def test_it_holds_no_permission_of_its_own(self):
        surface = {n for n in dir(ch.ChannelHealth)
                   if not n.startswith("_")}
        for forbidden in ("permit", "allow", "authorise", "authorize",
                          "enable", "grant"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, surface)


class TheSupervisorNoticesStillness(Base):

    def test_an_empty_shop_has_starved_queues(self):
        self.assertIn(sup.QUEUE_STARVED, self.conditions())

    def test_a_working_shop_does_not_report_starvation(self):
        self.s.capacity.admit("csv-cleanup-0", execute_seconds=60,
                              idempotency_key="a", capability="csv-cleanup")
        self.s.capacity.admit("data-entry-0", execute_seconds=60,
                              idempotency_key="b", capability="data-entry")
        self.assertNotIn(sup.QUEUE_STARVED, self.conditions())

    def test_committed_work_with_no_worker_running_is_a_stall(self):
        self.s.capacity.admit("csv-cleanup-0", execute_seconds=60,
                              idempotency_key="a", capability="csv-cleanup")
        self.s.capacity.admit("data-entry-0", execute_seconds=60,
                              idempotency_key="b", capability="data-entry")
        self.assertIn(sup.WORKER_STALLED, self.conditions())

    def test_an_unreadable_ledger_is_critical(self):
        def explode(*a, **k):
            raise RuntimeError("unreadable")

        self.s.ledger.real_revenue_cents = explode
        findings = [f for f in self.s.supervisor.check()
                    if f.condition == sup.PAYMENT_UNRECONCILED]
        self.assertTrue(findings)
        self.assertEqual(findings[0].severity, sup.CRITICAL)

    def test_a_paused_channel_is_reported(self):
        self.s.channels.observe("freelancehunt", ch.RESTRICTED,
                                detail="limited")
        self.assertIn(sup.CHANNEL_UNAVAILABLE, self.conditions())

    def test_a_check_that_cannot_run_is_a_finding_not_a_silence(self):
        """All clear from a check that did not run is the worst answer."""
        def explode():
            raise RuntimeError("the gate is unreadable")

        self.s.capacity.pressure = explode
        findings = self.s.supervisor.check()
        self.assertTrue(any("could not run" in f.detail for f in findings))

    def test_every_finding_names_who_owns_the_fix(self):
        self.s.channels.observe("x", ch.RESTRICTED)
        for finding in self.s.supervisor.check():
            with self.subTest(condition=finding.condition):
                self.assertTrue(finding.owner_authority)


class ItFixesNothing(Base):
    """The owner's constraint, asserted rather than described."""

    def test_it_has_no_power_to_act(self):
        surface = {n for n in dir(self.s.supervisor)
                   if not n.startswith("_")}
        for forbidden in ("accept", "deliver", "approve", "pay", "price",
                          "quote", "admit", "reserve", "start", "halt",
                          "resume", "amend", "grant", "promote"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, surface)

    def test_checking_changes_nothing(self):
        self.s.capacity.admit("csv-cleanup-0", execute_seconds=60,
                              idempotency_key="a", capability="csv-cleanup")
        before = self.s.capacity.jobs_in_flight()
        self.s.supervisor.check()
        self.assertEqual(self.s.capacity.jobs_in_flight(), before)

    def test_retries_are_counted_not_performed(self):
        finding = sup.Finding(sup.DELIVERY_FAILED, sup.WARN, "j1",
                              "failed", "delivery")
        self.assertTrue(finding.may_retry)
        for _ in range(sup.MAX_ATTEMPTS):
            self.s.supervisor.record_attempt(finding)
        counted = self.s.supervisor.attempts(finding)
        self.assertEqual(counted, sup.MAX_ATTEMPTS)
        bounded = sup.Finding(sup.DELIVERY_FAILED, sup.WARN, "j1",
                              "failed", "delivery", attempts=counted)
        self.assertFalse(bounded.may_retry)


class TheOwnerIsNotFlooded(Base):

    def sent(self):
        return [r for r in self.s.notifier.notifications()
                if r["event"] == "business.exception"]

    def critical(self):
        def explode(*a, **k):
            raise RuntimeError("unreadable")

        self.s.ledger.real_revenue_cents = explode
        return self.s.supervisor.check()

    def test_only_critical_findings_interrupt(self):
        findings = self.s.supervisor.check()
        self.assertTrue(findings, "nothing to escalate, so this proves "
                                  "nothing about what is escalated")
        self.s.supervisor.escalate(findings, notifier=self.s.notifier)
        self.assertEqual(self.sent(), [],
                         "a WARN interrupted the owner; it belongs in "
                         "the summary")

    def test_a_critical_finding_does_interrupt(self):
        self.s.supervisor.escalate(self.critical(), notifier=self.s.notifier)
        self.assertEqual(len(self.sent()), 1)

    def test_the_same_incident_is_announced_once(self):
        for _ in range(4):
            self.s.supervisor.escalate(self.critical(),
                                       notifier=self.s.notifier)
        self.assertEqual(len(self.sent()), 1)

    def test_escalation_without_a_notifier_sends_nothing(self):
        self.assertEqual(self.s.supervisor.escalate(self.critical()), [])

    def test_the_severity_it_sends_is_one_the_notifier_knows(self):
        """Two vocabularies, and they are not the same list.

        The first version sent "WARN", which this module uses to
        rank findings and the notifier does not accept at all. Every
        escalation raised, the exception was swallowed, and
        escalate() returned as though it had worked -- a
        notification failure reporting success, which is precisely
        the class of fault this module exists to catch elsewhere.
        """
        from solvent import notify

        self.assertIn(sup.OWNER_ACTION_REQUIRED, notify.SEVERITIES)

    def test_a_failed_escalation_is_recorded_not_swallowed(self):
        class Broken:
            def notify(self, **kw):
                raise RuntimeError("the notifier is down")

        findings = self.critical()
        sent = self.s.supervisor.escalate(findings, notifier=Broken())
        self.assertEqual(sent, [])
        self.assertTrue(self.s.supervisor.escalation_failures())


if __name__ == "__main__":
    unittest.main()
