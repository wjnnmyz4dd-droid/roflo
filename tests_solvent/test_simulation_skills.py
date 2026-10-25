"""Turning a skill on and off is an owner act, and it really happens.

The thing worth testing is not the list of names. It is that
``sell`` changes what a client can actually buy -- read back from the
registry and from the storefront's own refusal -- rather than
changing a variable the simulator then consults.
"""

from __future__ import annotations

import os
import pathlib
import secrets
import tempfile
import unittest
from unittest import mock

from simulation import rail
from simulation.shop import OWNER_IDENTITY, open_a_shop
from simulation.skills import (
    CONFIGURATIONS, EVERY_SKILL, FIRST_TWO, MIDPOINT, PER_SKILL_COMMITTED,
    Configuration, active_in, applied_to, policy_for, sell,
)


class TheConfigurations(unittest.TestCase):

    def test_the_assignment_asks_for_six_and_there_are_six(self):
        self.assertEqual(sorted(CONFIGURATIONS), list("ABCDEF"))

    def test_they_count_up_two_three_four_five(self):
        for name, expected in (("A", 2), ("B", 3), ("C", 4), ("D", 5)):
            with self.subTest(name):
                self.assertEqual(len(CONFIGURATIONS[name].start), expected)

    def test_the_two_are_the_two_the_owner_named(self):
        self.assertEqual(set(CONFIGURATIONS["A"].start), set(FIRST_TWO))
        self.assertIn("report-builder", FIRST_TWO)
        self.assertIn("document-processing", FIRST_TWO)

    def test_growing_keeps_the_first_two(self):
        for name in ("B", "C", "D"):
            with self.subTest(name):
                self.assertTrue(
                    set(FIRST_TWO) <= set(CONFIGURATIONS[name].start),
                    "a bigger shop dropped one of the owner's first skills")

    def test_e_contracts_and_f_expands_at_the_midpoint(self):
        contracting = CONFIGURATIONS["E"]
        expanding = CONFIGURATIONS["F"]

        self.assertEqual(len(contracting.skills_on(MIDPOINT - 1)), 5)
        self.assertEqual(len(contracting.skills_on(MIDPOINT)), 2)
        self.assertEqual(len(expanding.skills_on(MIDPOINT - 1)), 2)
        self.assertEqual(len(expanding.skills_on(MIDPOINT)), 5)

    def test_a_configuration_that_never_changes_says_so(self):
        self.assertEqual(CONFIGURATIONS["A"].changes, ())
        self.assertEqual(CONFIGURATIONS["E"].changes, (MIDPOINT,))

    def test_an_unknown_skill_is_refused(self):
        with self.assertRaises(ValueError):
            policy_for(["telepathy"])

    def test_a_shop_selling_nothing_is_refused(self):
        with self.assertRaises(ValueError):
            policy_for([])

    def test_the_owed_allowance_scales_with_the_skills(self):
        two = policy_for(FIRST_TWO)["capacity"]["max_owed_seconds"]
        five = policy_for(EVERY_SKILL)["capacity"]["max_owed_seconds"]

        self.assertAlmostEqual(five / two, 2.5)

    def test_a_pipeline_holds_one_active_and_three_queued(self):
        self.assertEqual(PER_SKILL_COMMITTED, 4)
        self.assertEqual(
            policy_for(FIRST_TWO)["capacity"]["max_concurrent_jobs"], 4)


class ItReallyChangesWhatIsForSale(unittest.TestCase):

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

    def _close(self):
        try:
            self.s.store.close()
        except Exception:
            pass

    def test_a_fresh_shop_sells_everything(self):
        self.assertEqual(set(active_in(self.s)), set(EVERY_SKILL))

    def test_selling_two_withdraws_the_other_three(self):
        said = sell(self.s, FIRST_TWO)

        self.assertEqual(set(active_in(self.s)), set(FIRST_TWO))
        self.assertEqual({k for k, v in said.items() if v}, set(FIRST_TWO))

    def test_the_registry_is_the_one_being_read_not_a_variable(self):
        sell(self.s, FIRST_TWO)
        withdrawn = next(n for n in EVERY_SKILL if n not in FIRST_TWO)

        permitted, why = self.s.capability.may_deploy(withdrawn)

        self.assertFalse(permitted)
        self.assertIn("development and testing", why)

    def test_the_storefront_declines_an_order_for_a_withdrawn_skill(self):
        """The thing a client would actually run into."""
        from solvent.storefront import OFFERS

        sell(self.s, FIRST_TWO)
        withdrawn = next(o for o in OFFERS
                         if o.capability not in FIRST_TWO)

        order = self.s.storefront.place(
            order_id="ord-withdrawn", offer_id=withdrawn.offer_id,
            client_ref="client:someone", answers={}, offered_cents=50_000)

        self.assertEqual(order.state.value, "DECLINED")
        self.assertIn("not cleared for delivery", (order.why or ""))

    def test_a_withdrawn_skill_can_be_brought_back(self):
        """Configuration F does this on day 45."""
        sell(self.s, FIRST_TWO)
        self.assertEqual(len(active_in(self.s)), 2)

        sell(self.s, EVERY_SKILL)

        self.assertEqual(set(active_in(self.s)), set(EVERY_SKILL))

    def test_applying_the_same_set_twice_changes_nothing(self):
        first = sell(self.s, FIRST_TWO)
        second = sell(self.s, FIRST_TWO)

        self.assertEqual(first, second)

    def test_the_capacity_allowance_moves_with_the_skills(self):
        sell(self.s, FIRST_TWO)
        two = self.s.capacity.budget().max_owed_seconds

        sell(self.s, EVERY_SKILL)

        self.assertGreater(self.s.capacity.budget().max_owed_seconds, two)

    def test_capacity_counts_the_pipelines_that_are_switched_on(self):
        sell(self.s, FIRST_TWO)

        self.assertEqual(set(self.s.capacity.pipelines()), set(FIRST_TWO))

    def test_only_an_owner_may_change_what_is_sold(self):
        with self.assertRaises(Exception):
            sell(self.s, FIRST_TWO, owner_identity="owner:not-the-owner")

    def test_every_configuration_applies_cleanly(self):
        for name, configuration in CONFIGURATIONS.items():
            for day in (0, MIDPOINT):
                wanted = configuration.skills_on(day)
                with self.subTest(configuration=name, day=day):
                    sell(self.s, wanted)
                    self.assertEqual(set(active_in(self.s)), set(wanted))


class WhatProductionDoesNotEnforce(unittest.TestCase):
    """The honest half of §11, pinned so the report cannot overstate it."""

    def test_the_execution_worker_bound_is_global_not_per_skill(self):
        from solvent.capacity import Budget

        doc = Budget.workers.__doc__ or ""

        self.assertIn("across the whole shop", doc,
                      "if this became per-skill the report's caveat is stale")


if __name__ == "__main__":
    unittest.main()
