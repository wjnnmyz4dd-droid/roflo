"""The price floor must see every cost, including the two it did not.

An evidence-first audit priced the worked example this architecture is
built around and found the floor short by a third of the cost stack.

    a $200 job
      execution      $35        service_cents
      verification   $20        verification_cents
      revisions      $40        expected_revision_cents
      acquisition    $30        -- nowhere to go
      payment fees    $7        payment_processing_cents
      supervision    $25        -- nowhere to go
      ------------------
      true cost     $157  ->  true contribution $43

``Costs`` could represent $102 of that $157. So the floor came out at
$145.71 and believed a $200 price left $98 of contribution when it left
$43 -- an overstatement of $55, which is 128% high. Worse than the
number being wrong: **a job priced at $146 cleared the owner's
authorised profit floor while losing $11.**

Nothing was miscalculated. ``floor_cents`` is correct arithmetic over
the lines it is given, and ``Ledger.actual_profit`` sums every category
it is handed. The defect was that two of the six lines in the worked
example had no field, and ``Costs`` says in its own docstring that it
lists them individually "so none is forgotten".

These tests hold the whole stack, not the two fields, so the next
omission is caught by the same rule rather than by another audit.
"""

from __future__ import annotations

import unittest

from solvent.negotiation import Costs, NegotiationPolicy, floor_cents

#: The audit's worked example, in cents.
EXECUTION, VERIFICATION, REVISIONS = 3_500, 2_000, 4_000
ACQUISITION, FEES, SUPERVISION = 3_000, 700, 2_500
TRUE_COST = (EXECUTION + VERIFICATION + REVISIONS
             + ACQUISITION + FEES + SUPERVISION)


def worked_example() -> Costs:
    return Costs(service_cents=EXECUTION, verification_cents=VERIFICATION,
                 expected_revision_cents=REVISIONS,
                 acquisition_cents=ACQUISITION,
                 payment_processing_cents=FEES,
                 supervision_cents=SUPERVISION)


class TheFloorSeesTheWholeCostStack(unittest.TestCase):

    POLICY = NegotiationPolicy(minimum_margin_fraction=0.30,
                               minimum_contribution_cents=0,
                               max_discount_fraction=0.25, max_concessions=2,
                               owner_signs_above_cents=10_000_000,
                               max_revisions_without_owner=2)

    def test_every_line_of_the_worked_example_is_counted(self):
        self.assertEqual(worked_example().total_cents, TRUE_COST)

    def test_the_breakdown_names_every_line(self):
        """A total nobody can decompose is a total nobody can check."""
        named = worked_example().breakdown()
        for line, amount in (("service", EXECUTION),
                             ("verification", VERIFICATION),
                             ("expected_revisions", REVISIONS),
                             ("acquisition", ACQUISITION),
                             ("payment_processing", FEES),
                             ("supervision", SUPERVISION)):
            with self.subTest(line):
                self.assertEqual(named[line], amount)
        self.assertEqual(named["total"], TRUE_COST)
        self.assertEqual(sum(v for k, v in named.items() if k != "total"),
                         TRUE_COST, "the breakdown does not sum to the total")

    def test_the_floor_refuses_the_price_that_used_to_clear_it(self):
        """$146 cleared the old floor and loses $11. It must not clear now."""
        floor = floor_cents(worked_example(), self.POLICY)
        self.assertGreater(floor, 14_600)
        self.assertGreater(floor, TRUE_COST,
                           "a floor at or below true cost authorises a "
                           "job that cannot make money")

    def test_no_price_clearing_the_floor_loses_money(self):
        """The property, swept rather than sampled at one price."""
        floor = floor_cents(worked_example(), self.POLICY)
        for price in range(floor, floor + 10_000, 250):
            with self.subTest(price=price):
                self.assertGreaterEqual(
                    price - TRUE_COST, 0,
                    f"${price / 100:.2f} clears the floor and loses money")

    def test_the_margin_the_owner_asked_for_is_the_margin_they_get(self):
        """30% of the price, measured against the true cost."""
        floor = floor_cents(worked_example(), self.POLICY)
        self.assertGreaterEqual((floor - TRUE_COST) / floor, 0.30 - 0.001)

    def test_dropping_either_new_line_moves_the_floor(self):
        """A field that changes no number is a field that guards nothing."""
        full = floor_cents(worked_example(), self.POLICY)
        for missing in ("acquisition_cents", "supervision_cents"):
            with self.subTest(missing):
                from dataclasses import replace
                without = floor_cents(
                    replace(worked_example(), **{missing: 0}), self.POLICY)
                self.assertLess(without, full)

    def test_the_audit_figures_are_reproduced_exactly(self):
        """The before and after, so the finding stays legible."""
        before = Costs(service_cents=EXECUTION, verification_cents=VERIFICATION,
                       expected_revision_cents=REVISIONS,
                       payment_processing_cents=FEES)
        self.assertEqual(before.total_cents, 10_200)
        self.assertEqual(floor_cents(before, self.POLICY), 14_571)
        self.assertEqual(20_000 - before.total_cents, 9_800)   # believed
        self.assertEqual(20_000 - TRUE_COST, 4_300)            # true
        self.assertEqual(9_800 - 4_300, 5_500)                 # overstatement


class EveryCostFieldReachesTheTotal(unittest.TestCase):
    """Generalised past the two that were missing.

    Counted from the dataclass rather than from a list written here,
    so a field added later is covered without anyone remembering to
    add it twice.
    """

    def test_each_field_on_its_own_raises_the_total(self):
        import dataclasses

        for field in dataclasses.fields(Costs):
            with self.subTest(field.name):
                one = Costs(**{field.name: 1_000})
                self.assertEqual(
                    one.total_cents, 1_000,
                    f"{field.name} does not reach total_cents")

    def test_each_field_appears_in_the_breakdown(self):
        import dataclasses

        named = set(Costs().breakdown()) - {"total"}
        self.assertEqual(
            len(named), len(dataclasses.fields(Costs)),
            "a cost field is missing from breakdown(), so a total that "
            "includes it cannot be decomposed")


class EveryCostFieldSurvivesARestart(unittest.TestCase):
    """The third place the hand-written list fell behind the type.

    ``Negotiation.as_dict`` wrote the cost lines out field by field,
    and the two the audit added were not among them. That is not a
    cosmetic omission. ``floor`` is recomputed from the restored costs
    rather than restored -- deliberately, so that no stored number can
    be moved by an edit or a migration -- so a cost line that did not
    survive the round trip came back as a *lower floor* than the one
    the deal was opened under. A crash discounted the job by exactly
    the lines that were dropped, and the deal looked perfectly healthy
    afterwards.

    Found by the first test that opened a deal through production with
    every line populated and then reopened the database. Nothing before
    it could have found it, because nothing before it put a non-zero
    number in those two fields and restarted.
    """

    def setUp(self):
        from solvent.negotiation import Negotiation

        self.deal = Negotiation(
            opportunity_id="o1", service="csv-cleanup",
            costs=worked_example(),
            policy=NegotiationPolicy(minimum_margin_fraction=0.30),
            scope_units=1_000)

    def test_the_round_trip_preserves_every_cost_field(self):
        import dataclasses

        from solvent.negotiation import negotiation_from_dict

        back = negotiation_from_dict(self.deal.as_dict())
        for field in dataclasses.fields(Costs):
            with self.subTest(field.name):
                self.assertEqual(
                    getattr(back.costs, field.name),
                    getattr(self.deal.costs, field.name),
                    f"{field.name} did not survive the round trip, so a "
                    "restart moves the floor")

    def test_the_round_trip_preserves_every_limit(self):
        import dataclasses

        from solvent.negotiation import negotiation_from_dict

        back = negotiation_from_dict(self.deal.as_dict())
        for field in dataclasses.fields(NegotiationPolicy):
            with self.subTest(field.name):
                self.assertEqual(getattr(back.policy, field.name),
                                 getattr(self.deal.policy, field.name))

    def test_the_floor_is_identical_after_a_round_trip(self):
        """The property that actually matters to the money."""
        from solvent.negotiation import negotiation_from_dict

        back = negotiation_from_dict(self.deal.as_dict())
        self.assertEqual(back.floor, self.deal.floor)
        self.assertEqual(back.costs.total_cents, TRUE_COST)

    def test_the_stored_shape_names_every_field_of_both_types(self):
        """Pins the mechanism, not just today's outcome.

        The three tests above would all pass again if somebody replaced
        the derivation with a hand-written list that happens to be
        complete today. This one fails the moment the list stops being
        derived from the type.
        """
        import dataclasses

        stored = self.deal.as_dict()
        self.assertEqual(
            set(stored["costs"]),
            {f.name for f in dataclasses.fields(Costs)})
        self.assertEqual(
            set(stored["policy"]),
            {f.name for f in dataclasses.fields(NegotiationPolicy)})


if __name__ == "__main__":
    unittest.main()
