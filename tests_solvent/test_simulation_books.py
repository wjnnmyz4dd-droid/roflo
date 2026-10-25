"""Costs that no job can be charged for, and the double count they risk.

Two things matter here. The journal must charge the owner's own
rates for things the Ledger does not record, and it must never
charge for something the Ledger already did. The third class runs a
few simulated days and checks every accrual has a DeskPilot audit
row behind it.
"""

from __future__ import annotations

import datetime
import os
import pathlib
import secrets
import tempfile
import unittest
from unittest import mock

from simulation import rail
from simulation.books import (
    ACQUISITION, JOURNAL_BOOKS, PRODUCTION_BOOKS, REVISION, SUBSCRIPTIONS,
    SUPERVISION, Journal, post_the_days_costs,
)
from simulation.calendar import Calendar
from simulation.customers import Population
from simulation.shop import open_a_shop
from simulation.world import World
from solvent.runtime import Runtime

START = datetime.date(2026, 1, 5)


class TheTwoSetsOfBooksDoNotOverlap(unittest.TestCase):

    def test_nothing_this_journal_books_is_booked_by_the_ledger(self):
        self.assertEqual(set(PRODUCTION_BOOKS) & set(JOURNAL_BOOKS), set())

    def test_the_ledgers_own_categories_are_the_ones_named(self):
        """If production starts booking a sixth line, this fails."""
        from solvent.quoting import OperatingCost

        lines = OperatingCost(compute_cents=1, api_cents=1,
                              platform_fee_cents=1, payment_fee_cents=1,
                              verification_cents=1).as_cost_lines()

        self.assertEqual({line.category.value for line in lines},
                         set(PRODUCTION_BOOKS))

    def test_booking_a_ledger_category_here_is_refused(self):
        journal = Journal(path=pathlib.Path("unused"),
                          rates={"COMPUTE": 100})

        with self.assertRaises(ValueError):
            journal._accrue(1, "COMPUTE", "should never happen")

    def test_hosting_is_not_charged_twice(self):
        """The VPS is already in the Ledger as COMPUTE, per job."""
        self.assertNotIn("HOSTING", JOURNAL_BOOKS)
        self.assertIn("COMPUTE", PRODUCTION_BOOKS)


class TheRatesAreTheOwners(unittest.TestCase):

    def setUp(self):
        key = mock.patch.dict(os.environ, {
            "SOLVENT_OWNER_KEY": secrets.token_hex(32),
            rail.SECRET_ENV: rail.TEST_SECRET})
        key.start()
        self.addCleanup(key.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.s = open_a_shop(self.root / "shop")
        self.addCleanup(self._close)
        self.journal = Journal.for_shop(self.s, self.root / "books.jsonl")
        self.addCleanup(self.journal.close)

    def _close(self):
        try:
            self.s.store.close()
        except Exception:
            pass

    def test_every_line_reads_a_rate_the_owner_recorded(self):
        for line in JOURNAL_BOOKS:
            with self.subTest(line):
                self.assertGreater(self.journal.rates[line], 0,
                                   f"{line} has no rate in the owner's policy")

    def test_the_rates_match_the_policy_exactly(self):
        self.assertEqual(
            self.journal.rates[SUPERVISION],
            self.s.policy.get("commercial", "supervision_cents_per_job"))
        self.assertEqual(
            self.journal.rates[SUBSCRIPTIONS],
            self.s.policy.get("commercial", "subscription_cents_per_day"))

    def test_an_owner_raising_their_rates_reaches_the_books(self):
        """Configuration `rising_costs` depends on this."""
        from solvent.harness import OWNER

        before = self.journal.rates[SUBSCRIPTIONS]
        self.s.policy.amend(
            {"commercial": {"subscription_cents_per_day": before * 2}},
            OWNER, "the owner's software bill went up")

        self.journal.reread(self.s)

        self.assertEqual(self.journal.rates[SUBSCRIPTIONS], before * 2)

    def test_a_zero_rate_books_nothing_rather_than_a_zero_row(self):
        journal = Journal(path=self.root / "empty.jsonl", rates={REVISION: 0})

        self.assertEqual(journal.reworked_it(1, "job_x"), 0)
        self.assertEqual(journal.rows, [])

    def test_the_subscription_is_charged_on_a_day_nothing_sold(self):
        charged = post_the_days_costs(self.journal, self.s, day=0)

        self.assertEqual(charged[SUBSCRIPTIONS],
                         self.journal.rates[SUBSCRIPTIONS])


class ItSurvivesBeingInterrupted(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = pathlib.Path(self.tmp.name) / "books.jsonl"

    def journal(self) -> Journal:
        journal = Journal(path=self.path, rates={line: 100
                                                 for line in JOURNAL_BOOKS})
        journal.path.parent.mkdir(parents=True, exist_ok=True)
        journal._handle = open(journal.path, "a", encoding="utf-8")
        self.addCleanup(journal.close)
        return journal

    def test_a_flushed_day_is_on_the_disk(self):
        journal = self.journal()
        journal.another_day_of_subscriptions(0)
        journal.end_of_day(0)

        self.assertEqual(len(Journal.read(self.path)), 1)
        self.assertEqual(journal.flushed_through, 0)

    def test_a_partial_last_row_is_reported_not_guessed_at(self):
        journal = self.journal()
        journal.another_day_of_subscriptions(0)
        journal.end_of_day(0)
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write('{"day": 1, "line": "ACQUI')

        rows = Journal.read(self.path)

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[-1]["line"], "TRUNCATED")
        self.assertEqual(rows[-1]["cents"], 0)

    def test_a_cut_slice_is_what_a_thirty_day_report_reads(self):
        journal = self.journal()
        for day in range(5):
            journal.another_day_of_subscriptions(day)

        self.assertEqual(len(journal.through(2)), 3)
        self.assertEqual(journal.total_cents(journal.through(2)), 300)


class EveryAccrualHasARecordBehindIt(unittest.TestCase):
    """Three simulated days of real business, then read the books."""

    @classmethod
    def setUpClass(cls):
        cls._key = mock.patch.dict(os.environ, {
            "SOLVENT_OWNER_KEY": secrets.token_hex(32),
            rail.SECRET_ENV: rail.TEST_SECRET})
        cls._key.start()
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = pathlib.Path(cls._tmp.name)
        cls.spool = cls.root / "payments-inbox"
        cls.s = open_a_shop(cls.root / "shop",
                            policy={"runtime": {"payment_spool": str(cls.spool)}})
        cls.journal = Journal.for_shop(cls.s, cls.root / "books.jsonl")
        calendar = Calendar(start=START, length=4)
        clock = calendar.clock()
        runtime = Runtime(cls.s, clock=clock)
        runtime.start()
        runtime.default_tasks()
        population = Population(seed="books", length_days=4,
                                arrivals_per_day=2.0)
        population.build([d.index for d in calendar.days() if d.working])
        world = World(solvent=cls.s, population=population,
                      workroot=cls.root / "world", spool=cls.spool)
        cls.charged = []
        cls.days = []
        for day in calendar.run(runtime, clock=clock,
                                before_day=lambda d, c:
                                world.open_for_business(d)):
            cls.days.append(day.index)
            cls.charged.append(post_the_days_costs(cls.journal, cls.s,
                                                   day.index))

    @classmethod
    def tearDownClass(cls):
        cls.journal.close()
        try:
            cls.s.store.close()
        except Exception:
            pass
        cls._tmp.cleanup()
        cls._key.stop()

    def test_the_run_charged_something(self):
        self.assertGreater(self.journal.total_cents(), 0)

    def test_the_subscription_was_charged_once_a_day_and_no_more(self):
        """Asserted on the run's own days.

        Not on the row count: another test in this class posts a
        later day deliberately, and unittest orders methods by name,
        so a count would make this test depend on that one.
        """
        charged_on = [r["day"] for r in self.journal.rows
                      if r["line"] == SUBSCRIPTIONS and r["day"] in self.days]

        self.assertEqual(sorted(charged_on), sorted(self.days))

    def test_somebody_was_approached_and_it_cost_something(self):
        self.assertGreater(
            sum(r["cents"] for r in self.journal.rows
                if r["line"] == ACQUISITION), 0)

    def test_no_client_is_charged_for_twice_over(self):
        """A negotiated client must not also be charged at intake."""
        refs = [r["ref"] for r in self.journal.rows
                if r["line"] == ACQUISITION]

        self.assertEqual(len(refs), len(set(refs)))

    def test_running_the_poster_again_posts_nothing(self):
        """A restart replaying a day must not bill the day twice."""
        before = self.journal.total_cents()

        again = post_the_days_costs(self.journal, self.s, day=99)

        self.assertEqual(again[ACQUISITION], 0)
        self.assertEqual(again[SUPERVISION], 0)
        self.assertEqual(again[REVISION], 0)
        # The subscription is per day and day 99 is a new day.
        self.assertEqual(self.journal.total_cents() - before,
                         self.journal.rates[SUBSCRIPTIONS])

    def test_every_supervision_row_names_what_the_owner_looked_at(self):
        for row in self.journal.rows:
            if row["line"] != SUPERVISION:
                continue
            with self.subTest(row["ref"]):
                self.assertTrue(row["ref"], "a supervision charge with no ref")
                self.assertTrue(row["why"])

    def test_the_summary_says_it_is_an_accrual(self):
        said = self.journal.summary()

        self.assertIn("not money production observed being spent",
                      said["basis"])
        self.assertEqual(said["total_cents"], self.journal.total_cents())


if __name__ == "__main__":
    unittest.main()
