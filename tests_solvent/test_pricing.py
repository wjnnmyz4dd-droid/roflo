"""Location-aware pricing: the CA/CT/NC requirement, and refusing to guess."""

import unittest
from datetime import date, timedelta

from solvent.pricing import SIGNAL_PRECEDENCE, resolve_context
from solvent.errors import FailClosed
from solvent.types import CostCategory, Jurisdiction, LocationSignals, PricingTier
from tests_solvent.fixtures import OWNER, STATE_LABOR_RATES, Rig


class JurisdictionBinding(unittest.TestCase):
    """A job is not one jurisdiction. It is one jurisdiction *per cost category*."""

    def test_client_project_and_worker_stay_three_separate_bindings(self):
        signals = LocationSignals(project=Jurisdiction(state="CA"),
                                  client=Jurisdiction(state="CT"),
                                  worker=Jurisdiction(state="NC"))
        context = resolve_context(signals, [
            CostCategory.ON_SITE_LABOR, CostCategory.REMOTE_LABOR,
            CostCategory.MARKET_RATE])
        self.assertEqual(context.jurisdiction_for(CostCategory.ON_SITE_LABOR).state, "CA")
        self.assertEqual(context.jurisdiction_for(CostCategory.REMOTE_LABOR).state, "NC")
        self.assertEqual(context.jurisdiction_for(CostCategory.MARKET_RATE).state, "CT")

    def test_contract_clause_outranks_physical_location_for_onsite_labor(self):
        signals = LocationSignals(project=Jurisdiction(state="CA"),
                                  contract_clause=Jurisdiction(state="NC"))
        context = resolve_context(signals, [CostCategory.ON_SITE_LABOR])
        self.assertEqual(context.jurisdiction_for(CostCategory.ON_SITE_LABOR).state, "NC")

    def test_materials_follow_the_delivery_destination(self):
        signals = LocationSignals(project=Jurisdiction(state="CA"),
                                  material_destination=Jurisdiction(state="CT"))
        context = resolve_context(signals, [CostCategory.MATERIALS])
        self.assertEqual(context.jurisdiction_for(CostCategory.MATERIALS).state, "CT")

    def test_owner_location_is_never_a_pricing_signal(self):
        """Pricing comes from the job, not from where the business happens to sit."""
        signal_names = {name for names in SIGNAL_PRECEDENCE.values() for name in names}
        self.assertNotIn("owner", signal_names)
        self.assertFalse(hasattr(LocationSignals(), "owner"))

    def test_missing_signal_is_reported_unresolved_not_defaulted(self):
        context = resolve_context(LocationSignals(), [CostCategory.ON_SITE_LABOR])
        self.assertIn(CostCategory.ON_SITE_LABOR, context.unresolved)
        self.assertIsNone(context.jurisdiction_for(CostCategory.ON_SITE_LABOR))

    def test_non_geographic_categories_need_no_jurisdiction(self):
        """Demanding a state for an API bill would block quotes for no reason."""
        context = resolve_context(LocationSignals(), [CostCategory.AI_API,
                                                     CostCategory.PLATFORM_FEES])
        self.assertEqual(context.unresolved, [])


class StatePricing(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()
        self.rig.load_labor_fixtures()

    def test_three_states_produce_three_materially_different_prices(self):
        prices = {}
        for state in ("CA", "CT", "NC"):
            line, err = self.rig.pricing.price_line(
                CostCategory.ON_SITE_LABOR, Jurisdiction(state=state), 10)
            self.assertEqual(err, "")
            prices[state] = line.amount
        self.assertEqual(len(set(prices.values())), 3, prices)
        self.assertEqual(prices["CA"], STATE_LABOR_RATES["CA"] * 10)
        self.assertGreater(prices["CA"], prices["CT"])
        self.assertGreater(prices["CT"], prices["NC"])

    def test_wrong_state_rate_is_detectable_from_the_recorded_reference(self):
        """Every line names the jurisdiction it was priced from, so a mismatch shows."""
        line, _ = self.rig.pricing.price_line(
            CostCategory.ON_SITE_LABOR, Jurisdiction(state="CT"), 1)
        self.assertEqual(line.jurisdiction.state, "CT")
        self.assertIn("CT", line.reference_id)
        self.assertNotEqual(line.amount, STATE_LABOR_RATES["CA"])

    def test_lookup_falls_back_up_the_hierarchy_and_records_the_level_used(self):
        line, err = self.rig.pricing.price_line(
            CostCategory.ON_SITE_LABOR, Jurisdiction(state="CA", city="Fresno"), 1)
        self.assertEqual(err, "")
        self.assertEqual(line.jurisdiction.path(), "US>CA",
                         "should generalise to the state record it actually has")

    def test_city_record_outranks_state_record_when_present(self):
        self.rig.pricing.ingest(
            category=CostCategory.ON_SITE_LABOR,
            jurisdiction=Jurisdiction(state="CA", city="Fresno"),
            tier=PricingTier.MARKET_DATA, unit_cents=12_000, unit="hour",
            source="FIXTURE", effective_date="2026-06-01", retrieved_date="2026-09-01",
            confidence=0.9, owner_identity=OWNER, is_fixture=True)
        line, _ = self.rig.pricing.price_line(
            CostCategory.ON_SITE_LABOR, Jurisdiction(state="CA", city="Fresno"), 1)
        self.assertEqual(line.amount, 12_000)


class DataQuality(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()

    def test_stale_data_is_treated_as_absent_not_as_weak_evidence(self):
        old = (date.today() - timedelta(days=400)).isoformat()
        self.rig.load_labor_fixtures(effective=old)
        line, err = self.rig.pricing.price_line(
            CostCategory.ON_SITE_LABOR, Jurisdiction(state="CA"), 1)
        self.assertIsNone(line)
        self.assertIn("no in-date approved reference", err)

    def test_conflicting_sources_resolve_to_the_higher_cost_and_say_so(self):
        """Wrong in the expensive direction loses a job; the other way loses money."""
        self.rig.load_labor_fixtures()
        self.rig.pricing.ingest(
            category=CostCategory.ON_SITE_LABOR, jurisdiction=Jurisdiction(state="CA"),
            tier=PricingTier.MARKET_DATA, unit_cents=11_000, unit="hour",
            source="BLS", effective_date="2026-06-01", retrieved_date="2026-09-01",
            confidence=0.7, owner_identity=OWNER, reference_id="px_alt_CA")
        line, _ = self.rig.pricing.price_line(
            CostCategory.ON_SITE_LABOR, Jurisdiction(state="CA"), 1)
        self.assertEqual(line.amount, 11_000)
        self.assertIn("conflict", line.note)
        self.assertGreater(self.rig.pricing.conflict_spread(
            CostCategory.ON_SITE_LABOR, Jurisdiction(state="CA")), 0)

    def test_unapproved_source_is_refused(self):
        with self.assertRaises(FailClosed):
            self.rig.pricing.ingest(
                category=CostCategory.ON_SITE_LABOR, jurisdiction=Jurisdiction(state="CA"),
                tier=PricingTier.MARKET_DATA, unit_cents=1, unit="hour",
                source="SOME_BLOG", effective_date="2026-06-01",
                retrieved_date="2026-09-01", confidence=0.9, owner_identity=OWNER)

    def test_reference_data_is_owner_governed(self):
        with self.assertRaises(FailClosed):
            self.rig.pricing.ingest(
                category=CostCategory.ON_SITE_LABOR, jurisdiction=Jurisdiction(state="CA"),
                tier=PricingTier.MARKET_DATA, unit_cents=1, unit="hour",
                source="FIXTURE", effective_date="2026-06-01",
                retrieved_date="2026-09-01", confidence=0.9, owner_identity="learning")

    def test_a_price_with_no_reference_record_cannot_be_produced(self):
        """A model may propose a lookup key; it may never supply a value."""
        line, err = self.rig.pricing.price_line(
            CostCategory.MATERIALS, Jurisdiction(state="WY"), 1)
        self.assertIsNone(line)
        self.assertTrue(err)

    def test_every_produced_line_cites_its_evidence(self):
        self.rig.load_labor_fixtures()
        line, _ = self.rig.pricing.price_line(
            CostCategory.ON_SITE_LABOR, Jurisdiction(state="NC"), 1)
        self.assertTrue(line.reference_id)
        self.assertIsNotNone(line.pricing_tier)
        self.assertIsNotNone(line.jurisdiction)


class LegalFloor(unittest.TestCase):
    """Tier 1 is a floor, not merely a priority."""

    def test_rate_below_a_legally_required_rate_is_a_violation(self):
        rig = Rig()
        rig.pricing.ingest(
            category=CostCategory.PREVAILING_WAGE, jurisdiction=Jurisdiction(state="CA"),
            tier=PricingTier.LAW_CONTRACT, unit_cents=9_000, unit="hour",
            source="OWNER", effective_date="2026-01-01", retrieved_date="2026-09-01",
            confidence=1.0, owner_identity=OWNER)
        from solvent.types import CostLine
        cheap = CostLine(category=CostCategory.PREVAILING_WAGE, amount=5_000,
                         jurisdiction=Jurisdiction(state="CA"),
                         pricing_tier=PricingTier.OWNER_POLICY)
        self.assertIn("below the legally required",
                      rig.pricing.legal_floor_violation(cheap))

    def test_fallback_tier_may_never_bind_a_quote(self):
        self.assertFalse(PricingTier.FALLBACK.binding_quote_allowed)
        for tier in (PricingTier.LAW_CONTRACT, PricingTier.OWNER_POLICY,
                     PricingTier.MARKET_DATA, PricingTier.HISTORICAL_ACTUALS):
            self.assertTrue(tier.binding_quote_allowed)


if __name__ == "__main__":
    unittest.main()
