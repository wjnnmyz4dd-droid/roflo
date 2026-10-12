"""Three numbers that must never become one, and the rule that is not a trade-off.

A human would take five hours. DeskPilot takes five minutes. Both facts are
true and they belong in different columns:

* the five hours is what the *client* is comparing against, so it is the basis
  of a fixed price;
* the five minutes is what *happened*, so it is the only thing an hourly
  contract may be billed for;
* neither is what the job *cost*, which is compute and fees.

Most of this file is the refusals, because the failure mode here is not a
crash. It is an invoice that looks reasonable and is for work nobody did.
"""

from __future__ import annotations

import unittest

from solvent.errors import FailClosed
from solvent.quoting import (
    BillingBasis, CostRates, ExecutionMeasurement, WorkStandard,
    hours_billable, human_equivalent, operating_cost, profitability,
    quote_fixed_price, recommend_fixed_price, refuse_fabricated_hours,
)
from solvent.types import PricingTier

#: A cited standard. The citation is test-only and says so; the point under
#: test is that the field is *required*, not what is in it.
STANDARD = WorkStandard(
    unit="row", minutes_per_unit=0.15, setup_minutes=10.0,
    source="TEST-ONLY", citation="fixture standard, not a real benchmark",
    retrieved_date="2026-10-12")

#: $65/hour. TEST-ONLY, and the suite never asserts it is a real market rate.
MARKET_HOURLY = 6_500


class AHumanEquivalentIsMeasuredAndCited(unittest.TestCase):

    def test_it_scales_on_a_counted_unit_plus_setup(self):
        human = human_equivalent(2_000, STANDARD)
        self.assertAlmostEqual(human.hours, (10.0 + 2_000 * 0.15) / 60.0)
        self.assertEqual(human.units, 2_000)

    def test_the_basis_names_the_source_and_when_it_was_retrieved(self):
        basis = human_equivalent(100, STANDARD).basis
        self.assertIn("100 row(s)", basis)
        self.assertIn("TEST-ONLY", basis)
        self.assertIn("2026-10-12", basis)

    def test_a_standard_with_no_citation_is_refused(self):
        for missing in ("source", "citation", "retrieved_date", "unit"):
            with self.subTest(missing=missing):
                fields = dict(unit="row", minutes_per_unit=0.1, source="S",
                              citation="C", retrieved_date="2026-01-01")
                fields[missing] = "  "
                with self.assertRaises(FailClosed) as caught:
                    WorkStandard(**fields)
                self.assertIn("opinion", str(caught.exception))

    def test_a_negative_standard_is_refused(self):
        with self.assertRaises(FailClosed):
            WorkStandard(unit="row", minutes_per_unit=-1, source="S",
                         citation="C", retrieved_date="2026-01-01")

    def test_a_negative_unit_count_is_refused(self):
        with self.assertRaises(FailClosed):
            human_equivalent(-1, STANDARD)

    def test_an_empty_job_still_costs_a_human_the_setup(self):
        self.assertAlmostEqual(human_equivalent(0, STANDARD).hours, 10.0 / 60.0)


class AFixedPricePricesTheOutcome(unittest.TestCase):

    def setUp(self):
        self.human = human_equivalent(2_000, STANDARD)  # 5.167 hours

    def test_the_price_is_the_human_cost_less_the_owners_discount(self):
        quote = quote_fixed_price(human=self.human,
                                  market_hourly_cents=MARKET_HOURLY,
                                  discount_fraction=0.40)
        self.assertEqual(quote.human_cost_cents,
                         round(self.human.hours * MARKET_HOURLY))
        self.assertEqual(quote.price_cents,
                         round(quote.human_cost_cents * 0.60))
        self.assertEqual(quote.basis, BillingBasis.FIXED_PRICE)

    def test_how_fast_deskpilot_is_does_not_appear_in_the_price(self):
        """The whole point of a fixed price, stated as a test."""
        slow = quote_fixed_price(human=self.human,
                                 market_hourly_cents=MARKET_HOURLY)
        # Nothing about execution is an input to `quote_fixed_price` at all,
        # which is the strongest form this assertion can take: there is no
        # argument to vary.
        self.assertNotIn("measurement",
                         quote_fixed_price.__code__.co_varnames)
        self.assertEqual(slow.price_cents, slow.human_cost_cents)

    def test_the_client_saving_is_what_they_keep(self):
        quote = quote_fixed_price(human=self.human,
                                  market_hourly_cents=MARKET_HOURLY,
                                  discount_fraction=0.40)
        self.assertEqual(quote.client_saving_cents,
                         quote.human_cost_cents - quote.price_cents)
        self.assertGreater(quote.client_saving_cents, 0)

    def test_a_quote_with_no_market_rate_is_refused(self):
        with self.assertRaises(FailClosed) as caught:
            quote_fixed_price(human=self.human, market_hourly_cents=0)
        self.assertIn("what the work is worth", str(caught.exception))

    def test_a_discount_outside_zero_to_one_is_refused(self):
        for bad in (-0.1, 1.0, 1.5):
            with self.subTest(discount=bad), self.assertRaises(FailClosed):
                quote_fixed_price(human=self.human,
                                  market_hourly_cents=MARKET_HOURLY,
                                  discount_fraction=bad)

    def test_a_quote_on_a_non_binding_standard_is_marked_indicative(self):
        """A FALLBACK standard may inform the owner and may not bind a client."""
        indicative = WorkStandard(
            unit="row", minutes_per_unit=0.15, source="GUESS",
            citation="no benchmark found", retrieved_date="2026-10-12",
            tier=PricingTier.FALLBACK)
        quote = quote_fixed_price(human=human_equivalent(100, indicative),
                                  market_hourly_cents=MARKET_HOURLY)
        self.assertFalse(quote.binding)
        self.assertIn("INDICATIVE", quote.summary())

    def test_a_market_data_standard_binds(self):
        quote = quote_fixed_price(human=self.human,
                                  market_hourly_cents=MARKET_HOURLY)
        self.assertTrue(quote.binding)
        self.assertNotIn("INDICATIVE", quote.summary())

    def test_the_rationale_shows_every_step(self):
        quote = quote_fixed_price(human=self.human,
                                  market_hourly_cents=MARKET_HOURLY,
                                  discount_fraction=0.40)
        joined = " ".join(quote.rationale)
        self.assertIn("human-equivalent", joined)
        self.assertIn("40%", joined)
        self.assertIn("TEST-ONLY", joined)


class TheMinimumProjectFee(unittest.TestCase):

    def small(self):
        return human_equivalent(2, STANDARD)  # ~10.3 minutes

    def test_a_job_below_the_floor_is_quoted_at_the_floor(self):
        quote = quote_fixed_price(human=self.small(),
                                  market_hourly_cents=MARKET_HOURLY,
                                  minimum_fee_cents=5_000)
        self.assertEqual(quote.price_cents, 5_000)
        self.assertTrue(quote.minimum_fee_applied)
        self.assertIn("minimum project fee", " ".join(quote.rationale))

    def test_a_job_above_the_floor_is_untouched(self):
        quote = quote_fixed_price(human=human_equivalent(2_000, STANDARD),
                                  market_hourly_cents=MARKET_HOURLY,
                                  minimum_fee_cents=5_000)
        self.assertFalse(quote.minimum_fee_applied)
        self.assertGreater(quote.price_cents, 5_000)

    def test_the_owner_can_waive_it_and_the_waiver_is_recorded(self):
        quote = quote_fixed_price(human=self.small(),
                                  market_hourly_cents=MARKET_HOURLY,
                                  minimum_fee_cents=5_000, waive_minimum=True)
        self.assertLess(quote.price_cents, 5_000)
        self.assertFalse(quote.minimum_fee_applied)
        self.assertIn("waived", " ".join(quote.rationale))

    def test_no_configured_minimum_is_not_a_minimum_of_zero_by_accident(self):
        """An unset floor leaves the price alone rather than flooring at 0."""
        quote = quote_fixed_price(human=self.small(),
                                  market_hourly_cents=MARKET_HOURLY)
        self.assertGreater(quote.price_cents, 0)
        self.assertFalse(quote.minimum_fee_applied)


class HourlyBillingIsWhatHappened(unittest.TestCase):
    """The one rule in this system that is not a trade-off."""

    def setUp(self):
        # Five minutes of work against a human's five hours.
        self.measurement = ExecutionMeasurement(wall_seconds=300.0,
                                                cpu_seconds=240.0)
        self.human = human_equivalent(2_000, STANDARD)

    def test_billable_hours_are_the_elapsed_hours(self):
        self.assertAlmostEqual(hours_billable(self.measurement), 5.0 / 60.0)

    def test_there_is_no_way_to_pass_the_human_figure_in(self):
        """Asserted against the signature, not against behaviour.

        A function that *accepts* the human-equivalent figure is one refactor
        away from returning it. The protection is that there is no parameter.
        """
        self.assertEqual(
            set(hours_billable.__code__.co_varnames[
                :hours_billable.__code__.co_argcount
                + hours_billable.__code__.co_kwonlyargcount]),
            {"measurement", "round_to_minutes"})

    def test_billing_the_human_equivalent_is_refused_by_name(self):
        with self.assertRaises(FailClosed) as caught:
            refuse_fabricated_hours(self.human.hours, self.measurement,
                                    self.human)
        message = str(caught.exception)
        self.assertIn("human-equivalent figure", message)
        self.assertIn("never billed as time", message)

    def test_any_padded_figure_is_refused_not_just_that_one(self):
        with self.assertRaises(FailClosed) as caught:
            refuse_fabricated_hours(2.0, self.measurement)
        self.assertIn("work that took", str(caught.exception))

    def test_the_honest_figure_passes(self):
        refuse_fabricated_hours(hours_billable(self.measurement),
                                self.measurement, self.human)

    def test_rounding_to_a_billing_increment_is_allowed(self):
        self.assertAlmostEqual(
            hours_billable(self.measurement, round_to_minutes=15), 0.25)
        refuse_fabricated_hours(0.25, self.measurement, self.human)

    def test_rounding_beyond_a_quarter_hour_is_refused(self):
        for bad in (0, 16, 60):
            with self.subTest(increment=bad), self.assertRaises(FailClosed) as c:
                hours_billable(self.measurement, round_to_minutes=bad)
            self.assertIn("honest rounding", str(c.exception))

    def test_a_tiny_job_rounds_up_to_one_increment_and_no_further(self):
        tiny = ExecutionMeasurement(wall_seconds=6.0, cpu_seconds=5.0)
        self.assertAlmostEqual(hours_billable(tiny), 1.0 / 60.0)
        with self.assertRaises(FailClosed):
            refuse_fabricated_hours(1.0, tiny)


class OperatingCostIsMeasuredNotImagined(unittest.TestCase):

    def setUp(self):
        self.rates = CostRates(compute_cents_per_cpu_hour=50,
                               platform_fee_fraction=0.10,
                               payment_fee_fraction=0.029,
                               payment_fee_flat_cents=30)
        self.measurement = ExecutionMeasurement(wall_seconds=300.0,
                                                cpu_seconds=3600.0)

    def test_compute_is_cpu_seconds_at_the_recorded_rate(self):
        cost = operating_cost(self.measurement, price_cents=40_000,
                              rates=self.rates)
        self.assertEqual(cost.compute_cents, 50)

    def test_fees_are_proportions_of_the_contract_price(self):
        cost = operating_cost(self.measurement, price_cents=40_000,
                              rates=self.rates)
        self.assertEqual(cost.platform_fee_cents, 4_000)
        self.assertEqual(cost.payment_fee_cents, round(40_000 * 0.029) + 30)

    def test_no_labour_line_appears_anywhere(self):
        """The defect this module exists to fix, asserted directly."""
        from solvent.types import CostCategory

        cost = operating_cost(self.measurement, price_cents=40_000,
                              rates=self.rates)
        categories = {line.category for line in cost.as_cost_lines()}
        for labour in (CostCategory.ON_SITE_LABOR, CostCategory.REMOTE_LABOR,
                       CostCategory.PREVAILING_WAGE, CostCategory.MARKET_RATE):
            self.assertNotIn(labour, categories,
                             "DeskPilot is costing its own work at a human "
                             "wage it does not pay")

    def test_every_line_says_where_it_came_from(self):
        cost = operating_cost(self.measurement, price_cents=40_000,
                              rates=self.rates)
        joined = " ".join(cost.basis)
        self.assertIn("CPU-seconds", joined)
        self.assertIn("platform fee", joined)
        self.assertIn("payment fee", joined)

    def test_a_fee_fraction_outside_zero_to_one_is_refused(self):
        for bad in ({"platform_fee_fraction": 1.0},
                    {"payment_fee_fraction": -0.1}):
            with self.subTest(bad=bad), self.assertRaises(FailClosed):
                CostRates(**bad)

    def test_an_unpaid_job_carries_no_payment_fee(self):
        cost = operating_cost(self.measurement, price_cents=0,
                              rates=self.rates)
        self.assertEqual(cost.payment_fee_cents, 0)

    def test_negative_measurements_are_refused(self):
        for bad in ({"wall_seconds": -1.0, "cpu_seconds": 0.0},
                    {"wall_seconds": 0.0, "cpu_seconds": -1.0}):
            with self.subTest(bad=bad), self.assertRaises(FailClosed):
                ExecutionMeasurement(**bad)


class ProfitIsPriceLessWhatWasSpent(unittest.TestCase):

    def test_the_margin_is_computed_against_measured_cost(self):
        rates = CostRates(compute_cents_per_cpu_hour=50,
                          platform_fee_fraction=0.10,
                          payment_fee_fraction=0.029,
                          payment_fee_flat_cents=30)
        cost = operating_cost(
            ExecutionMeasurement(wall_seconds=300.0, cpu_seconds=120.0),
            price_cents=40_000, rates=rates)
        book = profitability(price_cents=40_000, cost=cost,
                             human_cost_cents=33_583)
        self.assertEqual(book.profit_cents, 40_000 - cost.total_cents)
        self.assertGreater(book.margin, 0.85,
                           "the margin collapsed, which means a labour cost "
                           "is being counted again somewhere")

    def test_the_human_cost_is_reported_but_never_deducted(self):
        """It is the client's alternative, not DeskPilot's expense."""
        cost = operating_cost(
            ExecutionMeasurement(wall_seconds=1.0, cpu_seconds=1.0),
            price_cents=40_000, rates=CostRates())
        book = profitability(price_cents=40_000, cost=cost,
                             human_cost_cents=33_583)
        self.assertEqual(book.profit_cents, 40_000)
        self.assertEqual(book.client_saving_cents, 0,
                         "the client pays more than a human here, so there is "
                         "no saving to claim")

    def test_a_job_priced_below_its_own_fees_shows_a_loss(self):
        rates = CostRates(platform_fee_fraction=0.20,
                          payment_fee_fraction=0.029,
                          payment_fee_flat_cents=30)
        cost = operating_cost(
            ExecutionMeasurement(wall_seconds=1.0, cpu_seconds=1.0),
            price_cents=30, rates=rates)
        book = profitability(price_cents=30, cost=cost)
        self.assertLess(book.profit_cents, 0)


class WhenHourlyIsTheWrongInstrument(unittest.TestCase):

    def setUp(self):
        self.measurement = ExecutionMeasurement(wall_seconds=300.0,
                                                cpu_seconds=240.0)
        self.quote = quote_fixed_price(
            human=human_equivalent(2_000, STANDARD),
            market_hourly_cents=MARKET_HOURLY, discount_fraction=0.40)

    def test_it_recommends_converting_and_quantifies_both_sides(self):
        advice = recommend_fixed_price(measurement=self.measurement,
                                       agreed_hourly_cents=MARKET_HOURLY,
                                       fixed_quote=self.quote)
        self.assertTrue(advice.recommend)
        self.assertGreater(advice.uplift_cents, 0)
        self.assertIn("the client still keeps", advice.reason)

    def test_it_does_not_recommend_when_the_two_are_comparable(self):
        slow = ExecutionMeasurement(wall_seconds=4.0 * 3600, cpu_seconds=100.0)
        advice = recommend_fixed_price(measurement=slow,
                                       agreed_hourly_cents=MARKET_HOURLY,
                                       fixed_quote=self.quote)
        self.assertFalse(advice.recommend)
        self.assertIn("no case for renegotiating", advice.reason)

    def test_a_job_shorter_than_one_increment_is_named_as_rounding(self):
        instant = ExecutionMeasurement(wall_seconds=0.4, cpu_seconds=0.3)
        advice = recommend_fixed_price(measurement=instant,
                                       agreed_hourly_cents=MARKET_HOURLY,
                                       fixed_quote=self.quote)
        self.assertTrue(advice.recommend)
        self.assertIn("rounding rather than time", advice.reason)

    def test_the_comparison_uses_billable_hours_not_raw_seconds(self):
        """A 90-second job on a 15-minute increment bills a quarter hour.

        Comparing against raw elapsed time would understate what hourly
        actually pays and flatter the recommendation.
        """
        short = ExecutionMeasurement(wall_seconds=90.0, cpu_seconds=80.0)
        advice = recommend_fixed_price(measurement=short,
                                       agreed_hourly_cents=MARKET_HOURLY,
                                       fixed_quote=self.quote,
                                       round_to_minutes=15)
        self.assertEqual(advice.hourly_revenue_cents,
                         round(0.25 * MARKET_HOURLY))

    def test_it_is_advice_and_changes_no_agreement(self):
        """It returns a recommendation. It has no path to a price in force."""
        advice = recommend_fixed_price(measurement=self.measurement,
                                       agreed_hourly_cents=MARKET_HOURLY,
                                       fixed_quote=self.quote)
        self.assertIsInstance(advice.recommend, bool)
        self.assertEqual(self.quote.price_cents,
                         advice.fixed_revenue_cents,
                         "the advice altered the quote it was given")


if __name__ == "__main__":
    unittest.main()


class ReadinessSaysWhenThePriceCannotBeTrusted(unittest.TestCase):
    """Two checks that must stop a real job and must not stop a trial."""

    def setUp(self):
        import tempfile, pathlib
        from solvent.harness import Solvent

        self.box = tempfile.TemporaryDirectory()
        self.addCleanup(self.box.cleanup)
        self.s = Solvent(str(pathlib.Path(self.box.name) / "ready.db"))
        self.addCleanup(self.close)

    def close(self):
        try:
            self.s.store.close()
        except Exception:
            pass

    def named(self, report, name):
        return next(c for c in report.checks if c.name == name)

    def test_unrecorded_operating_costs_are_reported_as_not_ready(self):
        """Zero cost is an unconfigured deployment, not a cheap one.

        It also silently disables a control: the Governor's margin floor
        compares revenue against cost, so with every cost at zero every job
        shows a 100% margin and the floor cannot reject anything.
        """
        check = self.named(self.s.readiness(), "operating costs recorded")
        self.assertFalse(check.ready)
        self.assertIn("cannot reject a job", check.detail)
        self.assertIn("OD-16", check.owner_decision)

    def test_recording_one_cost_satisfies_it(self):
        from solvent.harness import OWNER

        self.s.policy.amend({"commercial": {"platform_fee_fraction": 0.1}},
                            OWNER, "test")
        self.assertTrue(
            self.named(self.s.readiness(), "operating costs recorded").ready)

    def test_no_minimum_project_fee_is_reported_as_not_ready(self):
        check = self.named(self.s.readiness(), "a minimum project fee")
        self.assertFalse(check.ready)
        self.assertIn("OD-17", check.owner_decision)

    def test_neither_blocks_a_supervised_trial(self):
        """A trial quotes nobody and collects nothing."""
        from solvent.readiness import assert_may_attempt_supervised_trial

        try:
            assert_may_attempt_supervised_trial(self.s.readiness())
        except Exception as refusal:
            for name in ("operating costs recorded", "a minimum project fee"):
                self.assertNotIn(name, str(refusal),
                                 f"{name!r} blocked a supervised trial, which "
                                 "sets no price and takes no money")

    def test_both_block_a_real_job(self):
        from solvent.errors import FailClosed
        from solvent.readiness import assert_may_attempt_first_real_job

        with self.assertRaises(FailClosed) as caught:
            assert_may_attempt_first_real_job(self.s.readiness())
        message = str(caught.exception)
        self.assertIn("OD-16", message)
        self.assertIn("OD-17", message)
