"""The revenue-capacity model, and the things it refuses to say.

The model's value is in its refusals. DeskPilot has never submitted a
bid, won a contract or been paid, so a monthly revenue figure requires
inputs nobody has. These tests pin that the model says so instead of
filling the gaps with plausible numbers, and that where it does produce
figures they are marked as modelled rather than earned.
"""

from __future__ import annotations

import tempfile
import unittest

from solvent import revenuemodel as rm
from solvent.finder import ExecutionHistory
from solvent.harness import OWNER, Solvent

CAPACITY = {"max_owed_seconds": 3600.0, "max_concurrent_jobs": 4,
            "max_open_bids": 4, "cpu_share": 0.5,
            "max_execution_workers": 2, "revision_headroom_fraction": 0.4}

#: The owner's own figures, as supplied: $40/hour, a $40 minimum
#: project fee and roughly $98 a month of VPS.
COMMERCIAL = {"hourly_rate_cents": 4_000, "minimum_project_fee_cents": 4_000,
              "platform_fee_fraction": 0.10, "payment_fee_fraction": 0.029,
              "payment_fee_flat_cents": 30,
              "fixed_monthly_overhead_cents": 9_800}

WHAT_IFS = {"bid_win_rate": 0.15, "collection_rate": 0.95}


class Base(unittest.TestCase):

    def setUp(self):
        self.box = tempfile.TemporaryDirectory()
        self.addCleanup(self.box.cleanup)
        self.s = Solvent(f"{self.box.name}/revenue.db")
        self.addCleanup(self.close)
        self.s.policy.amend({"capacity": CAPACITY}, OWNER, "test")
        self.s.policy.amend({"commercial": COMMERCIAL}, OWNER, "test")
        self.history = ExecutionHistory({"csv-cleanup": [1800.0] * 12})

    def close(self):
        try:
            self.s.store.close()
        except Exception:
            pass

    def build(self, *, history=True, **assumptions):
        return rm.build(
            policy=self.s.policy, capacity=self.s.capacity,
            ledger=self.s.ledger,
            history=self.history if history else None,
            capability="csv-cleanup" if history else "",
            assumptions=dict(assumptions) if assumptions else None)


class WhatItRefusesToSay(Base):

    def test_with_no_measured_runs_it_produces_no_figures(self):
        model = self.build(history=False)
        for name, scenario in model.scenarios.items():
            with self.subTest(scenario=name):
                self.assertIsNone(scenario["gross_revenue_cents"])
                self.assertIn("seconds_per_job", model.unknowns)

    def test_with_no_win_rate_it_produces_no_figures(self):
        model = self.build(collection_rate=0.95)
        self.assertIn("bid_win_rate", model.unknowns)
        self.assertIsNone(
            model.scenarios["NORMAL_DEMAND"]["net_profit_cents"])

    def test_it_says_which_input_is_missing(self):
        why = self.build()["why"] if False else (
            self.build().scenarios["NORMAL_DEMAND"]["why_no_figure"])
        self.assertIn("bid_win_rate", why)
        self.assertIn("invented", why)

    def test_a_win_rate_is_never_defaulted(self):
        """No bid has been sent, so no default could be anything but made up."""
        model = self.build()
        self.assertIsNone(model.inputs["bid_win_rate"].value)
        self.assertEqual(model.inputs["bid_win_rate"].basis, rm.UNKNOWN)

    def test_supplying_a_what_if_marks_it_assumed_not_observed(self):
        model = self.build(**WHAT_IFS)
        self.assertEqual(model.inputs["bid_win_rate"].basis, rm.ASSUMED)
        self.assertNotEqual(model.inputs["bid_win_rate"].basis, rm.OBSERVED)


class ModelledIsNotEarned(Base):

    def test_collected_revenue_comes_from_the_ledger_and_is_nothing(self):
        model = self.build(**WHAT_IFS)
        self.assertEqual(model.collected_revenue_cents, 0)

    def test_the_output_says_it_is_modelled(self):
        payload = self.build(**WHAT_IFS).to_dict()
        self.assertTrue(payload["modelled"])
        self.assertIn("not earnings", payload["warning"])

    def test_a_modelled_figure_never_enters_the_collected_total(self):
        model = self.build(**WHAT_IFS)
        modelled = model.scenarios["HIGH_DEMAND"][
            "collected_revenue_cents_modelled"]
        self.assertGreater(modelled, 0)
        self.assertEqual(model.collected_revenue_cents, 0)

    def test_every_input_carries_a_basis(self):
        for name, value in self.build(**WHAT_IFS).to_dict()["inputs"].items():
            with self.subTest(input=name):
                self.assertIn(value["basis"],
                              (rm.OBSERVED, rm.SIMULATED, rm.ASSUMED,
                               rm.UNKNOWN))

    def test_the_simulated_completion_rate_is_labelled_simulated(self):
        """It came from a simulation. Calling it observed would be a lie."""
        basis = self.build(**WHAT_IFS).inputs[
            "completion_rate_of_accepted"].basis
        self.assertEqual(basis, rm.SIMULATED)

    def test_the_owners_rate_is_not_presented_as_a_market_rate(self):
        note = self.build(**WHAT_IFS).inputs["price_per_job_cents"].note
        self.assertIn("not", note)
        self.assertIn("market", note)


class UnpaidIsNotCollected(Base):

    def test_the_uncollected_share_is_reported_separately(self):
        scenario = self.build(**WHAT_IFS).scenarios["HIGH_DEMAND"]
        self.assertGreater(scenario["uncollected_cents"], 0)
        self.assertEqual(
            scenario["gross_revenue_cents"] - scenario["uncollected_cents"],
            scenario["collected_revenue_cents_modelled"])

    def test_fees_are_taken_from_what_is_collected_not_from_what_is_billed(self):
        full = self.build(bid_win_rate=0.15, collection_rate=1.0)
        half = self.build(bid_win_rate=0.15, collection_rate=0.5)
        self.assertLess(
            half.scenarios["HIGH_DEMAND"]["marketplace_fees_cents"],
            full.scenarios["HIGH_DEMAND"]["marketplace_fees_cents"])

    def test_nothing_collected_means_nothing_contributed(self):
        model = self.build(bid_win_rate=0.15, collection_rate=0.0)
        scenario = model.scenarios["HIGH_DEMAND"]
        self.assertEqual(scenario["collected_revenue_cents_modelled"], 0)
        self.assertLessEqual(scenario["contribution_profit_cents"], 0)


class ContributionAndNetAreSeparate(Base):

    def test_both_are_reported(self):
        scenario = self.build(**WHAT_IFS).scenarios["NORMAL_DEMAND"]
        self.assertIn("contribution_profit_cents", scenario)
        self.assertIn("net_profit_cents", scenario)

    def test_net_is_contribution_less_the_fixed_overhead(self):
        scenario = self.build(**WHAT_IFS).scenarios["NORMAL_DEMAND"]
        self.assertEqual(
            scenario["net_profit_cents"],
            scenario["contribution_profit_cents"]
            - scenario["fixed_overhead_cents"])

    def test_an_unrecorded_overhead_does_not_silently_flatter_the_net(self):
        self.s.policy.amend({"commercial": {"fixed_monthly_overhead_cents": 0}},
                            OWNER, "test: nothing recorded")
        scenario = self.build(**WHAT_IFS).scenarios["NORMAL_DEMAND"]
        self.assertEqual(scenario["fixed_overhead_cents"], 0)
        self.assertEqual(scenario["net_profit_cents"],
                         scenario["contribution_profit_cents"])
        note = self.build(**WHAT_IFS).inputs[
            "fixed_monthly_overhead_cents"].note
        self.assertIn("whether or not", note)

    def test_every_cost_line_is_itemised(self):
        scenario = self.build(**WHAT_IFS).scenarios["HIGH_DEMAND"]
        for line in ("marketplace_fees_cents", "payment_fees_cents",
                     "incremental_compute_cents", "other_direct_costs_cents"):
            with self.subTest(line=line):
                self.assertIn(line, scenario)

    def test_every_money_line_is_whole_cents(self):
        """Fractions of a cent in a statement are a rounding bug waiting."""
        s = self.build(**WHAT_IFS).scenarios["HIGH_DEMAND"]
        for line, value in s.items():
            if line.endswith("_cents"):
                with self.subTest(line=line):
                    self.assertIsInstance(value, int)

    def test_the_cost_lines_add_up_to_the_contribution(self):
        s = self.build(**WHAT_IFS).scenarios["HIGH_DEMAND"]
        costs = (s["marketplace_fees_cents"] + s["payment_fees_cents"]
                 + s["incremental_compute_cents"]
                 + s["other_direct_costs_cents"])
        self.assertEqual(s["collected_revenue_cents_modelled"] - costs,
                         s["contribution_profit_cents"],
                         "the statement must reconcile to the cent")


class DemandBeyondCapacityEarnsNothingExtra(Base):

    def test_overload_is_clamped_at_capacity(self):
        """The first version let overload run past the gate.

        With a 15% win rate, "three times the offers" was half the
        capacity, so the overload scenario never overloaded and the
        model showed revenue rising with demand for ever.
        """
        scenario = self.build(**WHAT_IFS).scenarios["OVERLOAD"]
        self.assertTrue(scenario["capacity_bound"])
        self.assertAlmostEqual(scenario["accepted_jobs"],
                               scenario["capacity_jobs_per_month"], delta=0.5)

    def test_overload_turns_work_away_and_says_how_much(self):
        scenario = self.build(**WHAT_IFS).scenarios["OVERLOAD"]
        self.assertGreater(scenario["turned_away_jobs"], 0)

    def test_profit_stops_growing_once_capacity_is_the_limit(self):
        model = self.build(**WHAT_IFS)
        self.assertEqual(model.scenarios["OVERLOAD"]["net_profit_cents"],
                         model.scenarios["HIGH_DEMAND"]["net_profit_cents"])

    def test_more_demand_below_capacity_does_earn_more(self):
        model = self.build(**WHAT_IFS)
        self.assertGreater(model.scenarios["NORMAL_DEMAND"]["net_profit_cents"],
                           model.scenarios["LOW_DEMAND"]["net_profit_cents"])

    def test_capacity_follows_the_owners_worker_limit(self):
        before = self.build(**WHAT_IFS).scenarios["HIGH_DEMAND"][
            "capacity_jobs_per_month"]
        self.s.policy.amend({"capacity": {"max_execution_workers": 4}},
                            OWNER, "test: twice the workers")
        after = self.build(**WHAT_IFS).scenarios["HIGH_DEMAND"][
            "capacity_jobs_per_month"]
        self.assertAlmostEqual(after, before * 2, delta=1.0)

    def test_revision_headroom_reduces_the_jobs_that_fit(self):
        with_headroom = self.build(**WHAT_IFS).scenarios["HIGH_DEMAND"][
            "capacity_jobs_per_month"]
        self.s.policy.amend({"capacity": {"revision_headroom_fraction": 0.0}},
                            OWNER, "test: no headroom")
        without = self.build(**WHAT_IFS).scenarios["HIGH_DEMAND"][
            "capacity_jobs_per_month"]
        self.assertGreater(without, with_headroom)


class ItIsNotAnAuthority(Base):

    def test_no_gate_imports_the_model(self):
        from tests_solvent.fixtures import source_names

        for module in ("solvent/gate.py", "solvent/governor.py",
                       "solvent/capacity.py", "solvent/application.py",
                       "solvent/quoting.py", "solvent/pricing.py"):
            with self.subTest(module=module):
                self.assertNotIn("revenuemodel", source_names(module))

    def test_building_the_model_writes_nothing(self):
        before = len(self.s.audit.events())
        for _ in range(3):
            self.build(**WHAT_IFS)
        self.assertEqual(len(self.s.audit.events()), before)


if __name__ == "__main__":
    unittest.main()
