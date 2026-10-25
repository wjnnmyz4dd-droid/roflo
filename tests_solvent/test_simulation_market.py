"""The weather is weather: it changes conditions, never decisions.

A market model is the easiest place in a business simulation to
smuggle in the answer. Quietly let it set a price, refuse a client or
read a ledger and the run stops measuring DeskPilot and starts
measuring the model. The first three classes here are about that
boundary; the rest are about the model behaving like a market.
"""

from __future__ import annotations

import ast
import pathlib
import unittest

from simulation.market import CONDITIONS, Conditions, Market

HERE = pathlib.Path(__file__).resolve().parent.parent / "simulation"


class ItDecidesNothingAboutTheShop(unittest.TestCase):

    def source(self) -> str:
        return (HERE / "market.py").read_text(encoding="utf-8")

    def test_it_imports_nothing_from_production(self):
        tree = ast.parse(self.source())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add((node.module or "").split(".")[0])

        self.assertNotIn("solvent", imported)
        self.assertNotIn("simulation", imported)

    def test_it_never_names_a_solvent(self):
        names = {node.id for node in ast.walk(ast.parse(self.source()))
                 if isinstance(node, ast.Name)}

        self.assertNotIn("solvent", names)
        self.assertNotIn("Solvent", names)

    def test_it_never_names_the_grader(self):
        self.assertNotIn("audit", self.source().replace("# ", ""))


class ItIsTheSameMarketEveryTime(unittest.TestCase):

    def test_an_unseeded_market_is_refused(self):
        with self.assertRaises(ValueError):
            Market(CONDITIONS["normal"], seed="")

    def test_a_market_with_no_days_is_refused(self):
        with self.assertRaises(ValueError):
            Market(CONDITIONS["normal"], seed="s", length_days=0)

    def test_one_seed_gives_one_sequence_of_weather(self):
        def run(seed):
            market = Market(CONDITIONS["outages"], seed=seed, length_days=60)
            return [market.halted_on(d) for d in range(60)]

        self.assertEqual(run("same"), run("same"))
        self.assertNotEqual(run("same"), run("different"))

    def test_a_draw_is_keyed_by_the_day_not_the_call_order(self):
        """Asking about day 9 first must not change what day 3 says."""
        forwards = Market(CONDITIONS["outages"], seed="k")
        backwards = Market(CONDITIONS["outages"], seed="k")
        ordered = [forwards.halted_on(d) for d in range(10)]
        shuffled = {d: backwards.halted_on(d) for d in (9, 3, 7, 0, 5)}

        for day, answer in shuffled.items():
            with self.subTest(day=day):
                self.assertEqual(answer, ordered[day])


class TheRivalIsAttachedToTheJob(unittest.TestCase):
    """The defect this class exists for, found while writing it.

    The rival first quoted a fraction of *DeskPilot's asking price*,
    so it undercut whatever DeskPilot said and no price was ever
    acceptable. ``strong_competition`` was unwinnable by
    construction, which measures the model rather than the shop.
    """

    def test_a_rival_quotes_against_the_clients_reference_not_the_ask(self):
        market = Market(CONDITIONS["strong_competition"], seed="s")

        self.assertEqual(market.competitor_quote(50_000), 35_000)
        self.assertFalse(market.would_switch(30_000, 50_000),
                         "a price under the rival's lost the client anyway")
        self.assertTrue(market.would_switch(40_000, 50_000))

    def test_there_is_a_price_that_wins_in_every_competitive_market(self):
        """The property the first version broke."""
        for name, conditions in CONDITIONS.items():
            if conditions.competitor_at is None:
                continue
            with self.subTest(name):
                market = Market(conditions, seed="s")
                rival = market.competitor_quote(50_000)
                self.assertFalse(market.would_switch(rival, 50_000),
                                 f"{name} cannot be won at any price")

    def test_no_rival_means_the_budget_is_the_only_limit(self):
        market = Market(CONDITIONS["weak_competition"], seed="s")

        self.assertIsNone(market.competitor_quote(50_000))
        self.assertFalse(market.would_switch(10_000_000, 50_000))

    def test_nobody_switches_for_a_cent(self):
        market = Market(CONDITIONS["normal"], seed="s")
        rival = market.competitor_quote(50_000)

        self.assertFalse(market.would_switch(rival + 1, 50_000))


class DemandAndDisruption(unittest.TestCase):

    def test_low_demand_brings_fewer_people_than_high(self):
        low = Market(CONDITIONS["low_demand"], seed="s")
        high = Market(CONDITIONS["high_demand"], seed="s")

        self.assertLess(sum(low.arrivals_on(d, 1.0) for d in range(30)),
                        sum(high.arrivals_on(d, 1.0) for d in range(30)))

    def test_a_flat_market_never_spikes_and_never_goes_down(self):
        market = Market(CONDITIONS["low_demand"], seed="s")
        for day in range(90):
            self.assertEqual(market.arrivals_on(day, 1.0),
                             CONDITIONS["low_demand"].demand)
            self.assertFalse(market.halted_on(day))

        self.assertEqual(market.log, [])

    def test_an_outage_market_goes_down_sometimes_and_says_so(self):
        market = Market(CONDITIONS["outages"], seed="s", length_days=90)
        down = [d for d in range(90) if market.halted_on(d)]

        self.assertTrue(down, "a one-in-ten outage never happened in 90 days")
        self.assertEqual(len(down), market.summary()["outages"])

    def test_costs_rise_across_the_quarter_and_not_all_at_once(self):
        market = Market(CONDITIONS["rising_costs"], seed="s")

        self.assertEqual(market.cost_multiplier(0), 1.0)
        self.assertAlmostEqual(market.cost_multiplier(45), 1.5)
        self.assertAlmostEqual(market.cost_multiplier(90), 2.0)
        self.assertAlmostEqual(market.cost_multiplier(180), 2.0,
                               msg="a longer run kept inflating for ever")

    def test_a_flat_market_does_not_inflate(self):
        self.assertEqual(
            Market(CONDITIONS["low_demand"], seed="s").cost_multiplier(90), 1.0)

    def test_budgets_move_with_the_market(self):
        poor = Market(CONDITIONS["price_sensitive"], seed="s")
        rich = Market(CONDITIONS["high_value"], seed="s")

        self.assertLess(poor.budget_for(50_000), 50_000)
        self.assertGreater(rich.budget_for(50_000), 50_000)

    def test_a_budget_never_rounds_away_to_nothing(self):
        self.assertGreaterEqual(
            Market(Conditions("tiny", "x", budgets=0.0001),
                   seed="s").budget_for(1), 1)


class EveryNamedConditionIsUsable(unittest.TestCase):

    def test_the_assignments_conditions_all_exist(self):
        for wanted in ("low_demand", "normal", "high_demand",
                       "strong_competition", "weak_competition",
                       "price_sensitive", "high_value", "rising_costs",
                       "outages"):
            with self.subTest(wanted):
                self.assertIn(wanted, CONDITIONS)

    def test_each_one_describes_itself_for_a_report(self):
        for name, conditions in CONDITIONS.items():
            with self.subTest(name):
                self.assertEqual(conditions.name, name)
                self.assertTrue(conditions.description)

    def test_a_summary_says_what_the_weather_actually_did(self):
        market = Market(CONDITIONS["normal"], seed="s", length_days=90)
        for day in range(90):
            market.arrivals_on(day, 1.0)
            market.halted_on(day)

        said = market.summary()

        self.assertEqual(said["conditions"], "normal")
        self.assertGreaterEqual(said["spikes"], 0)
        self.assertGreater(said["cost_multiplier_at_end"], 1.0)


if __name__ == "__main__":
    unittest.main()
