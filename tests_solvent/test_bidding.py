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
        """Whatever the margin rule produces, cost is the hard bottom."""
        self.s.policy.amend({"commercial": {"minimum_margin_fraction": 0.01,
                                            "minimum_project_fee_cents": 1}},
                            OWNER, "test: almost no floor at all")
        floor, why = self.finder.bid_floor_cents(50_000)
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


if __name__ == "__main__":
    unittest.main()
