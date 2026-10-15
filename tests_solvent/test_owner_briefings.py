"""The four things the owner is told.

Job obtained, payment received, and a summary at 06:50 and 16:30.
Together they are the whole of "manage by exception": if none of them
arrives, nothing needed the owner.

The one these tests are strictest about is payment, because a summary
is exactly where simulated money would quietly become reported
revenue.
"""

from __future__ import annotations

import tempfile
import unittest

from solvent import briefing
from solvent import notify as _notify
from solvent.harness import OWNER, Solvent

SKILLS = ("csv-cleanup", "data-entry")
BUDGET = {"max_owed_seconds": 100_000.0, "max_concurrent_jobs": 4,
          "max_execution_workers": 5, "max_verification_workers": 5,
          "max_open_bids": 2, "cpu_share": 0.5, "min_free_disk_bytes": 0,
          "skills": list(SKILLS), "pipeline_target": 4, "replenish_at": 2,
          "halted": False}


class Base(unittest.TestCase):

    def setUp(self):
        self.box = tempfile.TemporaryDirectory()
        self.addCleanup(self.box.cleanup)
        self.s = Solvent(f"{self.box.name}/brief.db")
        self.addCleanup(self.close)
        self.s.policy.amend({"capacity": BUDGET}, OWNER, "test")

    def close(self):
        try:
            self.s.store.close()
        except Exception:
            pass

    def sent(self, event=""):
        rows = self.s.notifier.notifications()
        return [r for r in rows if not event or r["event"] == event]


class TheClockDecidesWhichSummary(unittest.TestCase):

    def test_the_two_times_are_the_owners(self):
        self.assertEqual(briefing.SUMMARY_TIMES, ("06:50", "16:30"))

    def test_each_time_names_its_own_slot(self):
        self.assertEqual(briefing.due("06:50"), "06:50")
        self.assertEqual(briefing.due("16:30"), "16:30")

    def test_a_late_run_inside_the_grace_still_counts(self):
        self.assertEqual(briefing.due("07:15"), "06:50")
        self.assertEqual(briefing.due("16:55"), "16:30")

    def test_a_run_long_past_the_slot_sends_nothing(self):
        """A summary at noon is not the morning summary."""
        for late in ("07:21", "12:00", "18:00", "23:59", "00:00"):
            with self.subTest(at=late):
                self.assertEqual(briefing.due(late), "")

    def test_rubbish_asks_for_nothing(self):
        for bad in ("", "nonsense", None, "25:00", "6.50"):
            with self.subTest(at=bad):
                self.assertEqual(briefing.due(bad), "")

    def test_nothing_here_schedules_itself(self):
        """A module with its own timer would be another daemon."""
        from tests_solvent.fixtures import source_names

        names = source_names("solvent/briefing.py")
        for forbidden in ("sleep", "Timer", "Thread", "cron", "schedule",
                          "asyncio"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, names)


class TheSummaryLeadsWithWhatNeedsYou(Base):

    def test_an_unconfigured_capacity_is_the_first_thing_said(self):
        bare = Solvent(f"{self.box.name}/bare.db")
        self.addCleanup(lambda: bare.store.close())
        summary = briefing.compose(bare, slot=briefing.MORNING)
        self.assertTrue(summary.needs_you)
        self.assertIn("capacity is not configured",
                      " ".join(summary.needs_you))

    def test_a_quiet_shop_says_nothing_needs_you(self):
        summary = briefing.compose(self.s, slot=briefing.MORNING)
        self.assertEqual(summary.needs_you, [])
        self.assertIn("Nothing needs you", summary.text())

    def test_unresolved_submissions_are_raised(self):
        self.s.applications.unresolved = lambda: [{"id": "a"}, {"id": "b"}]
        summary = briefing.compose(self.s, slot=briefing.MORNING)
        self.assertIn("2 submission(s)", " ".join(summary.needs_you))
        self.assertIn("block new commitments", " ".join(summary.needs_you))

    def test_the_pipelines_are_reported_with_their_target(self):
        summary = briefing.compose(self.s, slot=briefing.MORNING)
        text = summary.text()
        self.assertIn("csv-cleanup 0/4", text)
        self.assertIn("below the top-up line", text)

    def test_a_full_pipeline_is_not_listed_as_short(self):
        for n in range(4):
            self.s.capacity.admit(f"csv-{n}", execute_seconds=10,
                                  idempotency_key=f"c{n}",
                                  capability="csv-cleanup")
        summary = briefing.compose(self.s, slot=briefing.MORNING)
        short = [line for line in summary.lines
                 if "below the top-up line" in line]
        self.assertTrue(short)
        self.assertNotIn("csv-cleanup", short[0])

    def test_a_broken_ledger_is_said_rather_than_skipped(self):
        def explode():
            raise RuntimeError("unreadable")

        self.s.ledger.real_revenue_cents = explode
        summary = briefing.compose(self.s, slot=briefing.MORNING)
        self.assertIn("ledger could not be read", summary.text())


class MoneyIsNeverFlattered(Base):

    def test_collected_revenue_is_zero_and_says_why(self):
        text = briefing.compose(self.s, slot=briefing.EVENING).text()
        self.assertIn("collected revenue: $0.00", text)
        self.assertIn("any money in the ledger is simulated", text)

    def test_a_simulated_payment_is_announced_as_simulated(self):
        briefing.payment_received(self.s, job_id="j1", amount_cents=25_000,
                                  simulated=True, method="fixture")
        body = self.sent(briefing.PAYMENT_RECEIVED)[0]["summary"]
        self.assertIn("SIMULATED", body)
        self.assertIn("not revenue", body)

    def test_a_real_payment_is_not_marked_simulated(self):
        briefing.payment_received(self.s, job_id="j2", amount_cents=25_000,
                                  simulated=False, method="stripe")
        body = self.sent(briefing.PAYMENT_RECEIVED)[0]["summary"]
        self.assertNotIn("SIMULATED", body)
        self.assertIn("Payment received", body)


class TheFourMessagesAreSent(Base):

    def test_job_obtained_names_the_job_and_the_money(self):
        briefing.job_obtained(self.s, job_id="j1", capability="csv-cleanup",
                              amount_cents=25_000, source="owner_entered")
        row = self.sent(briefing.JOB_OBTAINED)[0]
        self.assertIn("csv-cleanup", row["summary"])
        self.assertIn("$250.00", row["summary"])
        self.assertEqual(row["job_id"], "j1")

    def test_the_same_job_is_announced_once(self):
        for _ in range(3):
            briefing.job_obtained(self.s, job_id="j1", amount_cents=100)
        self.assertEqual(len(self.sent(briefing.JOB_OBTAINED)), 1)

    def test_a_summary_is_sent_once_per_slot_per_day(self):
        for _ in range(4):
            briefing.send_summary(self.s, slot=briefing.MORNING,
                                  today="2026-10-15")
        self.assertEqual(len(self.sent(briefing.MORNING_SUMMARY)), 1)

    def test_the_evening_summary_is_a_different_message(self):
        briefing.send_summary(self.s, slot=briefing.MORNING,
                              today="2026-10-15")
        briefing.send_summary(self.s, slot=briefing.EVENING,
                              today="2026-10-15")
        self.assertEqual(len(self.sent(briefing.MORNING_SUMMARY)), 1)
        self.assertEqual(len(self.sent(briefing.EVENING_SUMMARY)), 1)

    def test_tomorrows_summary_is_not_suppressed_as_a_duplicate(self):
        briefing.send_summary(self.s, slot=briefing.MORNING,
                              today="2026-10-15")
        briefing.send_summary(self.s, slot=briefing.MORNING,
                              today="2026-10-16")
        self.assertEqual(len(self.sent(briefing.MORNING_SUMMARY)), 2)

    def test_all_four_events_are_declared(self):
        self.assertEqual(len(briefing.EVENTS), 4)
        for event in briefing.EVENTS:
            with self.subTest(event=event):
                self.assertTrue(event.startswith("business."))


class TheCycleTellsTheOwner(unittest.TestCase):
    """The two event messages, fired by a real business cycle."""

    def test_a_completed_cycle_announces_both(self):
        from tests_solvent.test_first_customer_workflow import (
            TheJobRunsAllTheWayThrough)

        case = TheJobRunsAllTheWayThrough("test_it_completed")
        case.setUp()
        try:
            self.assertTrue(case.result.completed, case.result.why_stopped)
            events = {r["event"] for r in case.s.notifier.notifications()}
            self.assertIn(briefing.JOB_OBTAINED, events)
            self.assertIn(briefing.PAYMENT_RECEIVED, events)
        finally:
            case.doCleanups()

    def test_the_payment_it_announces_is_marked_simulated(self):
        from tests_solvent.test_first_customer_workflow import (
            TheJobRunsAllTheWayThrough)

        case = TheJobRunsAllTheWayThrough("test_it_completed")
        case.setUp()
        try:
            paid = [r for r in case.s.notifier.notifications()
                    if r["event"] == briefing.PAYMENT_RECEIVED]
            self.assertTrue(paid)
            self.assertIn("SIMULATED", paid[0]["summary"])
            self.assertEqual(case.s.ledger.real_revenue_cents(), 0)
        finally:
            case.doCleanups()


if __name__ == "__main__":
    unittest.main()
