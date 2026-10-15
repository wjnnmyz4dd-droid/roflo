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
        briefing.payment_received(self.s, job_id="j1", gross_cents=25_000,
                                  simulated=True, provider="fixture")
        body = self.sent(briefing.PAYMENT_RECEIVED)[0]["summary"]
        self.assertIn("SIMULATED", body)
        self.assertIn("not revenue", body)

    def test_a_real_payment_is_not_marked_simulated(self):
        briefing.payment_received(self.s, job_id="j2", gross_cents=25_000,
                                  evidence_ref="txn_test_1",
                                  simulated=False, provider="stripe")
        body = self.sent(briefing.PAYMENT_RECEIVED)[0]["summary"]
        self.assertNotIn("SIMULATED", body)
        self.assertIn("Payment received", body)


class TheFourMessagesAreSent(Base):

    def test_job_obtained_names_the_job_and_the_money(self):
        briefing.job_obtained(self.s, job_id="j1", capability="csv-cleanup",
                              amount_cents=25_000, source="owner_entered",
                              contract_ref="award-1")
        row = self.sent(briefing.JOB_OBTAINED)[0]
        self.assertIn("csv-cleanup", row["summary"])
        self.assertIn("$250.00", row["summary"])
        self.assertEqual(row["job_id"], "j1")

    def test_the_same_job_is_announced_once(self):
        for _ in range(3):
            briefing.job_obtained(self.s, job_id="j1", amount_cents=100,
                                  contract_ref="award-1")
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


class ALongSummaryIsShortenedNotCut(Base):
    """The notifier stores 400 characters. Where the cut falls matters.

    The first version let the column do it, and the column cut
    mid-word: the stored summary ended "no real payment has bee".
    """

    def long_summary(self):
        s = briefing.Summary(slot=briefing.MORNING)
        s.needs_you.append("something genuinely needs you")
        s.lines.extend(f"line {n} with enough words in it to take up "
                       "a fair amount of room" for n in range(20))
        return s

    def test_it_fits_what_the_notifier_stores(self):
        self.assertLessEqual(len(self.long_summary().text()), briefing.LIMIT)

    def test_every_line_it_keeps_is_whole(self):
        text = self.long_summary().text()
        whole = {"- something genuinely needs you"}
        whole.update(f"- line {n} with enough words in it to take up "
                     "a fair amount of room" for n in range(20))
        for line in text.splitlines()[1:-1]:
            with self.subTest(line=line):
                self.assertIn(line, whole)

    def test_it_says_how_many_it_left_out(self):
        self.assertIn("more line(s)", self.long_summary().text())

    def test_what_needs_you_is_never_the_part_dropped(self):
        self.assertIn("something genuinely needs you",
                      self.long_summary().text())

    def test_a_short_summary_is_not_annotated(self):
        s = briefing.Summary(slot=briefing.MORNING)
        s.lines.append("one short line")
        self.assertNotIn("more line(s)", s.text())

    def five_skills(self):
        """All five pipelines, so the real summary actually overflows.

        With two skills it fits under the limit and nothing is
        shortened, which would make the two tests below pass without
        the shortening working at all.
        """
        budget = dict(BUDGET)
        budget["skills"] = ["csv-cleanup", "spreadsheet-automation",
                            "document-processing", "data-entry",
                            "report-builder"]
        self.s.policy.amend({"capacity": budget}, OWNER, "test: five")
        # Something unacknowledged, so the summary also carries a
        # "needs you" line. Five pipelines alone come to 322
        # characters, which fits, and a case that fits is not a case
        # about shortening.
        briefing.job_obtained(self.s, job_id="j1", capability="csv-cleanup",
                              amount_cents=4_000, source="owner_entered",
                              contract_ref="award-1")

    def test_the_stored_summary_is_what_was_composed(self):
        """Nothing between compose and the column may shorten it.

        Asserting a length under the limit would pass on a summary the
        column had cut, because a cut summary is also under the limit.
        The only check that depends on the shortening is that what was
        stored is character for character what was composed.
        """
        self.five_skills()
        composed = briefing.compose(self.s, slot=briefing.MORNING).text()
        self.assertGreater(len(composed), briefing.LIMIT - 60,
                           "this case no longer overflows, so it is not "
                           "testing shortening")
        briefing.send_summary(self.s, slot=briefing.MORNING,
                              today="2026-10-15")
        row = self.sent(briefing.MORNING_SUMMARY)[0]
        self.assertEqual(row["summary"], composed)

    def test_the_money_lines_survive_shortening(self):
        """Shortening must not flatter by omission."""
        self.five_skills()
        briefing.send_summary(self.s, slot=briefing.MORNING,
                              today="2026-10-15")
        row = self.sent(briefing.MORNING_SUMMARY)[0]
        self.assertIn("more line(s)", row["summary"],
                      "nothing was dropped, so this case is not testing "
                      "what survives dropping")
        self.assertIn("collected revenue", row["summary"])
        self.assertIn("no real payment has been received", row["summary"])


class SomethingHasToCallIt(Base):
    """The summaries are on the runtime's timer, not their own.

    briefing.py deliberately has no scheduler in it -- a module with
    its own timer is a second daemon -- which leaves the question of
    what calls it. For a while nothing did, and a summary function
    nobody calls is the same as no summary at all.
    """

    def runtime(self):
        from solvent import runtime as rt

        return rt.Runtime(self.s, sleep=lambda _s: None)

    def test_the_runtime_registers_a_briefing_task(self):
        r = self.runtime()
        r.default_tasks()
        self.assertIn("owner_briefing", [t.name for t in r.tasks])

    def test_it_has_a_configured_cadence(self):
        """An unscheduled task falls back to a rate nobody chose."""
        from solvent import runtime as rt

        self.assertIn("owner_briefing", rt.DEFAULT_INTERVALS)

    def test_it_asks_more_often_than_the_grace_window(self):
        """Asking less often than the grace allows would miss slots."""
        from solvent import runtime as rt

        self.assertLess(rt.DEFAULT_INTERVALS["owner_briefing"],
                        briefing.GRACE_MINUTES * 60)

    def at(self, hhmm):
        """The tick, run at a pinned local time.

        Pinned because a tick run at the real clock passes or fails
        depending on what time of day the suite is run at, which tests
        the hour rather than the rule.
        """
        from datetime import datetime

        when = datetime.strptime(f"2026-10-15 {hhmm}", "%Y-%m-%d %H:%M")
        return self.runtime()._tick_owner_briefing(now=when)

    def test_outside_both_windows_it_sends_nothing(self):
        said = self.at("12:00")
        self.assertIn("no summary due", said)
        self.assertEqual(self.sent(briefing.MORNING_SUMMARY), [])
        self.assertEqual(self.sent(briefing.EVENING_SUMMARY), [])

    def test_at_the_morning_slot_it_sends_the_morning_summary(self):
        self.at("06:50")
        self.assertEqual(len(self.sent(briefing.MORNING_SUMMARY)), 1)
        self.assertEqual(self.sent(briefing.EVENING_SUMMARY), [])

    def test_at_the_evening_slot_it_sends_the_evening_summary(self):
        self.at("16:30")
        self.assertEqual(len(self.sent(briefing.EVENING_SUMMARY)), 1)

    def test_five_minutes_of_ticks_inside_a_window_send_one_message(self):
        """The cadence is inside the grace, so a slot gets many ticks."""
        for hhmm in ("06:50", "06:55", "07:00", "07:05", "07:10"):
            self.at(hhmm)
        self.assertEqual(len(self.sent(briefing.MORNING_SUMMARY)), 1)

    def test_a_failing_summary_does_not_stop_the_loop(self):
        original = briefing.send_summary
        briefing.send_summary = lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("the notifier is down"))
        self.addCleanup(setattr, briefing, "send_summary", original)
        try:
            said = self.at("06:50")
        except Exception as exc:                       # pragma: no cover
            self.fail(f"the tick raised instead of reporting: {exc!r}")
        self.assertIn("could not be sent", said)


if __name__ == "__main__":
    unittest.main()
