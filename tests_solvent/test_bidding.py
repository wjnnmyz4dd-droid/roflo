"""Bidding: what we would charge, and how fast we could deliver.

A method on the Job Finder rather than a bidding agent. Bidding is the
Finder answering one more question about an opportunity it has already
assessed, and the answer is assembled from authorities that already
exist -- measured execution history for the time, `quoting` for the
cost and margin, the owner's recorded terms for the floor. A separate
agent would need its own copy of all three and would disagree with them
the first time one changed.

The two things that must never happen: a delivery time that was guessed,
and a price below what the work costs.
"""

from __future__ import annotations

import tempfile
import unittest

from solvent.discovery import Compliance, FixtureSource, Opportunity, Readiness
from solvent.finder import Confidence, ExecutionHistory, Finder
from solvent.harness import OWNER, Solvent, owner_promotions, promote_capability
from tests_solvent.fixtures import set_commercial_terms

BOARD = "board"
SKILL = "csv-cleanup"


class Base(unittest.TestCase):

    def setUp(self):
        self.box = tempfile.TemporaryDirectory()
        self.addCleanup(self.box.cleanup)
        self.s = Solvent(f"{self.box.name}/bid.db")
        self.addCleanup(self.close)
        for name in owner_promotions():
            promote_capability(self.s, name, owner_identity=OWNER, why="test")
        self.s.discovery.register_source(
            FixtureSource(BOARD, []), owner_identity=OWNER,
            readiness=Readiness.PERMITTED_AUTOMATION,
            compliance=Compliance.PERMITTED, determination="test fixture")
        set_commercial_terms(self.s, minimum_margin_fraction=0.30)
        self.finder = Finder(self.s.discovery, self.s.policy,
                             self.s.capability, self.s.orchestrator,
                             self.s.store)

    def close(self):
        try:
            self.s.store.close()
        except Exception:
            pass

    def history(self, *seconds, capability=SKILL):
        return ExecutionHistory({capability: list(seconds)})

    def posting(self, cents=60_000, needs=(SKILL,), **over):
        from solvent.content import quarantine
        from solvent.types import PrivacyClass

        fields = {"id": "o1", "source": BOARD, "external_ref": "r1",
                  "title": "Tidy a spreadsheet", "quoted_cents": cents,
                  "needs": tuple(needs), "payment_structure": "FIXED",
                  "posting": quarantine("A straightforward file.",
                                        source=BOARD,
                                        privacy=PrivacyClass.PUBLIC)}
        fields.update(over)
        return Opportunity(**fields)


class CompletionTimeComesFromMeasurement(Base):

    def test_an_unmeasured_capability_has_no_estimate(self):
        estimate = self.finder.completion_estimate(SKILL,
                                                   history=ExecutionHistory())
        self.assertFalse(estimate.known)
        self.assertIs(estimate.confidence, Confidence.UNKNOWN)
        self.assertIn("not known", estimate.basis)

    def test_an_unmeasured_capability_is_not_a_fast_one(self):
        """The failure mode worth naming: absence reading as zero."""
        estimate = self.finder.completion_estimate(SKILL,
                                                   history=ExecutionHistory())
        self.assertIsNone(estimate.seconds)
        self.assertIsNone(estimate.hours)

    def test_the_estimate_is_the_median_of_observed_runs(self):
        estimate = self.finder.completion_estimate(
            SKILL, history=self.history(10.0, 20.0, 30.0))
        self.assertEqual(estimate.seconds, 20.0)
        self.assertEqual(estimate.samples, 3)

    def test_one_run_is_provisional_not_measured(self):
        estimate = self.finder.completion_estimate(
            SKILL, history=self.history(12.0))
        self.assertIs(estimate.confidence, Confidence.PROVISIONAL)

    def test_enough_runs_earn_measured(self):
        estimate = self.finder.completion_estimate(
            SKILL, history=self.history(*[12.0] * 5))
        self.assertIs(estimate.confidence, Confidence.MEASURED)

    def test_an_outlier_does_not_drag_the_estimate(self):
        """Median, not mean: one pathological run is not the new promise."""
        estimate = self.finder.completion_estimate(
            SKILL, history=self.history(10.0, 11.0, 12.0, 13.0, 9_000.0))
        self.assertEqual(estimate.seconds, 12.0)


class NoBidWithoutTheEvidenceToMakeOne(Base):

    def test_no_measured_history_means_no_bid(self):
        bid = self.finder.bid(self.posting(), history=ExecutionHistory())
        self.assertFalse(bid.submittable)
        self.assertIsNone(bid.amount_cents)
        self.assertIn("not known", bid.why)

    def test_a_posting_naming_no_price_gets_no_bid(self):
        bid = self.finder.bid(self.posting(cents=0),
                              history=self.history(12.0))
        self.assertFalse(bid.submittable)
        self.assertIn("states no price", bid.why)

    def test_an_uncertified_capability_gets_no_bid(self):
        bid = self.finder.bid(self.posting(needs=("neurosurgery",)),
                              history=self.history(12.0,
                                                   capability="neurosurgery"))
        self.assertFalse(bid.submittable)

    def test_an_unconfigured_business_cannot_bid(self):
        from solvent.harness import Solvent as S

        box = tempfile.TemporaryDirectory()
        self.addCleanup(box.cleanup)
        bare = S(f"{box.name}/bare.db")
        self.addCleanup(lambda: bare.store.close())
        finder = Finder(bare.discovery, bare.policy, bare.capability,
                        bare.orchestrator, bare.store)
        bid = finder.bid(self.posting(), history=self.history(12.0))
        self.assertFalse(bid.submittable)
        self.assertTrue(bid.blockers)

    def test_a_refusal_still_explains_itself(self):
        bid = self.finder.bid(self.posting(), history=ExecutionHistory())
        self.assertTrue(bid.why)
        self.assertIn("no bid", bid.summary())


class ThePriceNeverGoesBelowWhatTheWorkCosts(Base):

    def test_a_budget_under_the_floor_is_refused(self):
        bid = self.finder.bid(self.posting(cents=100),
                              history=self.history(12.0))
        self.assertFalse(bid.submittable)
        self.assertIn("below", bid.why)

    def test_the_floor_respects_the_owners_margin(self):
        """A 30% margin means cost may be at most 70% of the price."""
        floor, why = self.finder.bid_floor_cents(70_000)
        self.assertGreaterEqual(floor, 100_000)
        self.assertIn("30%", why)

    def test_the_floor_is_never_below_cost(self):
        """Whatever else the rules produce, cost is the hard bottom.

        The margin has to be *zero* for this to mean anything, which is
        the whole point and what the first version of this test missed.
        For any margin between 0 and 1, ``cost / (1 - margin)`` is
        already at or above cost, so the margin branch satisfies the
        assertion on its own and the clamp underneath it never runs. A
        mutant deleting the clamp survived, which is how I found out.
        """
        self.s.policy.amend({"commercial": {"minimum_margin_fraction": 0.0,
                                            "minimum_project_fee_cents": 1}},
                            OWNER, "test: no margin rule, a 1-cent floor")
        floor, why = self.finder.bid_floor_cents(50_000)
        self.assertEqual(floor, 50_000)
        self.assertIn("no price may sit below", why)

    def test_the_margin_branch_cannot_hide_the_clamp(self):
        """Why the test above sets the margin to zero, pinned.

        If this ever fails, the margin rule has started producing a
        floor below cost and the clamp has become load-bearing in a
        different way than it is here.
        """
        for margin in (0.01, 0.1, 0.3, 0.5, 0.9):
            with self.subTest(margin=margin):
                self.s.policy.amend(
                    {"commercial": {"minimum_margin_fraction": margin,
                                    "minimum_project_fee_cents": 1}},
                    OWNER, "test")
                floor, _ = self.finder.bid_floor_cents(50_000)
                self.assertGreaterEqual(floor, 50_000)

    def test_a_bid_that_is_made_clears_the_margin(self):
        bid = self.finder.bid(self.posting(cents=60_000),
                              history=self.history(12.0))
        self.assertTrue(bid.submittable, bid.why)
        self.assertGreaterEqual(bid.margin, 0.30)

    def test_the_bid_never_exceeds_what_the_client_offered(self):
        bid = self.finder.bid(self.posting(cents=60_000),
                              history=self.history(12.0))
        self.assertLessEqual(bid.amount_cents, 60_000)

    def test_every_bid_shows_its_working(self):
        bid = self.finder.bid(self.posting(), history=self.history(12.0))
        joined = " | ".join(bid.basis)
        self.assertIn("compute", joined)
        self.assertIn("floor:", joined)
        self.assertIn("completion:", joined)


class WithRealPlatformFees(Base):
    """The fixture above has no fees, so cost is almost nothing and the
    margin check cannot bite. These set the fees the owner records for a
    real marketplace, where the platform's cut is most of the cost."""

    def setUp(self):
        super().setUp()
        set_commercial_terms(
            self.s, minimum_margin_fraction=0.30,
            platform_fee_fraction=0.10, payment_fee_fraction=0.029,
            payment_fee_flat_cents=30)

    def test_the_fees_are_most_of_the_cost(self):
        bid = self.finder.bid(self.posting(cents=60_000),
                              history=self.history(*[22.0] * 5))
        self.assertTrue(bid.submittable, bid.why)
        self.assertGreater(bid.cost_cents, 7_000)
        self.assertIn("platform fee", " | ".join(bid.basis))

    def test_a_job_whose_fees_eat_the_margin_is_refused(self):
        """At a 95% platform cut there is no price that works."""
        set_commercial_terms(self.s, minimum_margin_fraction=0.30,
                             platform_fee_fraction=0.95)
        bid = self.finder.bid(self.posting(cents=10_000),
                              history=self.history(*[22.0] * 5))
        self.assertFalse(bid.submittable)
        self.assertIn("below", bid.why)

    def test_the_floor_is_computed_at_the_budget_not_at_the_floor(self):
        """Documented conservatism: fees scale with price, so costing at
        the client's budget overstates the floor rather than under."""
        bid = self.finder.bid(self.posting(cents=60_000),
                              history=self.history(*[22.0] * 5))
        self.assertGreater(bid.floor_cents, bid.cost_cents)


class BiddingSendsNothing(unittest.TestCase):
    """The architectural constraint, checked structurally."""

    def test_the_bidder_is_a_method_on_the_finder(self):
        self.assertTrue(hasattr(Finder, "bid"))
        self.assertTrue(hasattr(Finder, "completion_estimate"))

    def test_there_is_no_separate_bidding_component(self):
        import pathlib
        import re

        offenders = []
        for path in sorted(pathlib.Path("solvent").glob("*.py")):
            for match in re.finditer(r"^class (\w*(Bidder|BidAgent)\w*)",
                                     path.read_text(encoding="utf-8"),
                                     re.M):
                offenders.append(f"{path.name}:{match.group(1)}")
        self.assertEqual(offenders, [])

    def test_the_finder_cannot_transmit(self):
        """It reads and recommends. Sending belongs to Applications."""
        import pathlib

        source = pathlib.Path("solvent/finder.py").read_text(encoding="utf-8")
        for forbidden in ("transport", "requests.", "urlopen", "gate.request",
                          "def submit"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


class TheFortyAnHourMinimumIsAFloorNotAPriceListEntry(unittest.TestCase):
    """A rate is only a minimum if something enforces it.

    Before this, a job that would hold DeskPilot for three hours could
    be sold at the $40 minimum project fee -- thirteen dollars an hour
    -- because the floor knew about cash and margin and not about
    time.
    """

    def setUp(self):
        self.box = tempfile.TemporaryDirectory()
        self.addCleanup(self.box.cleanup)
        self.s = Solvent(f"{self.box.name}/rates.db")
        self.addCleanup(self.close)
        self.s.policy.amend(
            {"commercial": {"hourly_rate_cents": 4_000,
                            "minimum_project_fee_cents": 4_000}},
            OWNER, "test: the owner's stated rates")
        self.finder = Finder(self.s.discovery, self.s.policy,
                             self.s.capability, self.s.orchestrator,
                             self.s.store)

    def close(self):
        try:
            self.s.store.close()
        except Exception:
            pass

    def floor(self, hours, cost=0):
        return self.finder.bid_floor_cents(cost,
                                           occupied_seconds=hours * 3600)

    def test_a_long_job_may_not_be_sold_at_the_minimum_fee(self):
        amount, why = self.floor(3.0)
        self.assertEqual(amount, 12_000)
        self.assertIn("hour", why)

    def test_the_floor_scales_with_the_hours_occupied(self):
        self.assertEqual(self.floor(10.0)[0], 40_000)
        self.assertEqual(self.floor(2.5)[0], 10_000)

    def test_a_fast_job_keeps_the_minimum_project_fee(self):
        """Speed is the advantage; the hourly floor must not tax it."""
        amount, why = self.floor(0.03)
        self.assertEqual(amount, 4_000)
        self.assertIn("minimum project fee", why)

    def test_an_unmeasured_job_occupies_no_hours_and_floors_at_the_fee(self):
        self.assertEqual(self.floor(0.0)[0], 4_000)

    def test_no_hourly_rate_means_no_hourly_floor(self):
        self.s.policy.amend({"commercial": {"hourly_rate_cents": 0}},
                            OWNER, "test: no rate recorded")
        self.assertEqual(self.floor(10.0)[0], 4_000)

    def test_the_binding_floor_is_the_highest_of_them(self):
        """Cost still sits under everything."""
        self.assertEqual(self.floor(1.0, cost=90_000)[0], 90_000)
        self.assertEqual(self.floor(50.0, cost=1_000)[0], 200_000)

    def test_the_floor_is_whole_cents(self):
        for hours in (0.7, 1.3, 2.9, 7.77):
            with self.subTest(hours=hours):
                self.assertIsInstance(self.floor(hours)[0], int)


class ABidPricesAgainstTheOwnersFloor(Base):
    """End to end through bid(), not just the floor on its own."""

    def test_the_floor_counts_the_hours_the_job_will_occupy(self):
        """A sixteen-hour job cannot be sold for six hundred dollars.

        Each measured run is eight hours and the safety factor doubles
        it, so accepting occupies sixteen -- $640 at the owner's rate.
        The client offers $600, which clears the minimum fee and the
        margin easily and is still too little for the time.
        """
        long_job = self.history(*[28_800.0] * 12)
        bid = self.finder.bid(self.posting(cents=60_000), history=long_job)
        self.assertGreater(bid.floor_cents, 60_000)
        self.assertFalse(bid.submittable)
        self.assertIn("hour", bid.why)

    def test_a_fast_job_at_the_same_price_is_biddable(self):
        """The same money, a job that takes a minute: a good deal."""
        quick = self.history(*[60.0] * 12)
        bid = self.finder.bid(self.posting(cents=60_000), history=quick)
        self.assertTrue(bid.submittable, bid.why)
        self.assertEqual(bid.amount_cents, 60_000)

    def test_the_bid_states_how_long_the_client_will_wait(self):
        quick = self.history(*[60.0] * 12)
        bid = self.finder.bid(self.posting(cents=60_000), history=quick)
        self.assertGreater(bid.promised_seconds, 0)
        self.assertIn("delivered within", bid.why)


class ABidStatesItsDeliveryTime(unittest.TestCase):
    """Speed is the competitive case, so it belongs on the offer."""

    def test_the_bid_carries_the_promise_in_hours(self):
        from solvent.finder import Bid, CompletionEstimate, Confidence

        bid = Bid("o1", "csv-cleanup", 4_000,
                  CompletionEstimate("csv-cleanup", 60.0, 12,
                                     Confidence.MEASURED, "t", 90.0),
                  4_000, 100, 0.5, True, "why", promised_seconds=7_200)
        self.assertEqual(bid.promised_hours, 2.0)

    def test_a_bid_with_no_promise_reports_no_hours(self):
        from solvent.finder import Bid, CompletionEstimate, Confidence

        bid = Bid("o1", "csv-cleanup", None,
                  CompletionEstimate("csv-cleanup", None, 0,
                                     Confidence.UNKNOWN, "t"),
                  0, None, None, False, "why")
        self.assertEqual(bid.promised_hours, 0.0)


if __name__ == "__main__":
    unittest.main()
