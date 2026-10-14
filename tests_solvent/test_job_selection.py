"""Choosing the best combination of jobs, not the earliest one.

The capacity gate answers "is there room". That alone makes DeskPilot
take the first thing that fits, which maximises nothing. These tests
pin the other half: when offers compete for scarce room, the ones
worth more per second win.
"""

from __future__ import annotations

import tempfile
import unittest

from solvent.finder import Bid, CompletionEstimate, Confidence, Finder
from solvent.harness import OWNER, Solvent


def estimate(seconds, *, known=True):
    return CompletionEstimate(
        "csv-cleanup", seconds if known else None, 12,
        Confidence.MEASURED if known else Confidence.UNKNOWN,
        "test", seconds)


def bid(opportunity_id, amount, cost, seconds, *, submittable=True,
        known=True):
    return Bid(opportunity_id, "csv-cleanup", amount, estimate(seconds,
                                                               known=known),
               0, cost, None, submittable, "test")


class Base(unittest.TestCase):

    def setUp(self):
        self.box = tempfile.TemporaryDirectory()
        self.addCleanup(self.box.cleanup)
        self.s = Solvent(f"{self.box.name}/choose.db")
        self.addCleanup(self.close)
        self.finder = Finder(self.s.discovery, self.s.policy,
                             self.s.capability, self.s.orchestrator,
                             self.s.store)

    def close(self):
        try:
            self.s.store.close()
        except Exception:
            pass


class ValuePerSecond(Base):

    def test_profit_is_divided_by_the_time_it_takes(self):
        self.assertAlmostEqual(
            self.finder.value_density(bid("a", 10_000, 4_000, 60)), 100.0)

    def test_a_bigger_job_can_be_the_worse_job(self):
        """Twice the money for three times the work is a worse job."""
        small = self.finder.value_density(bid("a", 10_000, 2_000, 100))
        large = self.finder.value_density(bid("b", 20_000, 4_000, 300))
        self.assertGreater(small, large)

    def test_an_unmeasured_job_has_no_density_rather_than_a_low_one(self):
        self.assertIsNone(
            self.finder.value_density(bid("a", 10_000, 2_000, 0,
                                          known=False)))

    def test_an_unsubmittable_bid_has_no_density(self):
        self.assertIsNone(
            self.finder.value_density(bid("a", 10_000, 2_000, 60,
                                          submittable=False)))

    def test_a_job_with_no_price_has_no_density(self):
        self.assertIsNone(self.finder.value_density(
            bid("a", None, 2_000, 60)))


class ChoosingWithinTheRoom(Base):

    def offers(self):
        """Deliberately worst-first.

        The first version listed the big job first, and it did not fit
        in any of the test's budgets -- so taking offers in arrival
        order produced the same answer as ranking them, and a mutant
        that removed the ranking survived. The poor job now arrives
        first and does fit, so only ranking can pass these.
        """
        return [
            bid("quick-poor", 4_000, 3_000, 100),    # 10.0/s
            bid("slow-rich", 30_000, 5_000, 600),    # 41.7/s
            bid("medium", 12_000, 2_000, 200),       # 50.0/s
            bid("quick-good", 9_000, 1_000, 100),    # 80.0/s
        ]

    def test_the_best_value_per_second_is_taken_first(self):
        chosen, _ = self.finder.choose(self.offers(), seconds_available=100)
        self.assertEqual([b.opportunity_id for b in chosen], ["quick-good"])

    def test_it_fills_the_room_in_value_order(self):
        chosen, _ = self.finder.choose(self.offers(), seconds_available=300)
        self.assertEqual([b.opportunity_id for b in chosen],
                         ["quick-good", "medium"])

    def test_arrival_order_does_not_decide(self):
        forwards, _ = self.finder.choose(self.offers(), seconds_available=300)
        backwards, _ = self.finder.choose(list(reversed(self.offers())),
                                          seconds_available=300)
        self.assertEqual([b.opportunity_id for b in forwards],
                         [b.opportunity_id for b in backwards])

    def test_nothing_fits_in_no_room(self):
        chosen, passed = self.finder.choose(self.offers(),
                                            seconds_available=0)
        self.assertEqual(chosen, [])
        self.assertEqual(len(passed), 4)

    def test_everything_fits_in_enough_room(self):
        chosen, passed = self.finder.choose(self.offers(),
                                            seconds_available=10_000)
        self.assertEqual(len(chosen), 4)
        self.assertEqual(passed, [])

    def test_what_was_passed_over_says_why(self):
        _, passed = self.finder.choose(self.offers(), seconds_available=100)
        for _, why in passed:
            self.assertTrue(why.strip())
            self.assertIn("s", why)

    def test_an_unpriceable_offer_is_passed_over_not_guessed_at(self):
        offers = self.offers() + [bid("unknown", 50_000, 100, 0, known=False)]
        chosen, passed = self.finder.choose(offers, seconds_available=10_000)
        self.assertNotIn("unknown", [b.opportunity_id for b in chosen])
        reasons = {b.opportunity_id: why for b, why in passed}
        self.assertIn("no value per second", reasons["unknown"])

    def test_the_same_offers_always_give_the_same_choice(self):
        """Reproducibility: ties break on id, not on dictionary order."""
        tied = [bid("b", 10_000, 0, 100), bid("a", 10_000, 0, 100),
                bid("c", 10_000, 0, 100)]
        for _ in range(10):
            chosen, _ = self.finder.choose(tied, seconds_available=200)
            self.assertEqual([b.opportunity_id for b in chosen], ["a", "b"])

    def test_choosing_commits_nothing(self):
        before = len(self.s.audit.events())
        self.finder.choose(self.offers(), seconds_available=10_000)
        self.assertEqual(len(self.s.audit.events()), before)
        self.assertEqual(self.s.capacity.owed_seconds(), 0.0)


class ItIsAnApproximationAndSaysSo(Base):

    def test_greedy_can_be_beaten_by_the_exact_answer(self):
        """Stated rather than hidden: this is not the optimum.

        Room for 100s. Greedy takes the densest job (60s at 100/s =
        6000) and then cannot fit the 50s one, totalling 6000. Taking
        the two 50s jobs instead would make 9000. The exact answer
        needs every offer in advance, which a marketplace does not
        give; this test exists so nobody reads `choose` as optimal.
        """
        offers = [bid("dense", 6_000, 0, 60),
                  bid("pair-a", 4_500, 0, 50),
                  bid("pair-b", 4_500, 0, 50)]
        chosen, _ = self.finder.choose(offers, seconds_available=100)
        taken = sum(int(b.amount_cents) for b in chosen)
        self.assertEqual([b.opportunity_id for b in chosen], ["dense"])
        self.assertEqual(taken, 6_000)
        self.assertLess(taken, 9_000)

    def test_it_never_takes_more_than_the_room_allows(self):
        offers = [bid(f"j{n}", 10_000, 0, 100) for n in range(20)]
        chosen, _ = self.finder.choose(offers, seconds_available=450)
        self.assertLessEqual(
            sum(b.estimate.seconds for b in chosen), 450)


if __name__ == "__main__":
    unittest.main()
