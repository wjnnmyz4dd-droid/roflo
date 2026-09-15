"""Financial Governor: the one money gate, and the calibration that guards it."""

import unittest

from solvent.errors import GovernorRefused
from solvent.types import (
    CostCategory, CostLine, GovernorVerdict, Jurisdiction, LocationSignals,
    OperatingMode, PaymentState, money,
)
from tests_solvent.fixtures import OWNER, Rig


def synthetic_job(rig, job_id, *, estimated_cents, actual_cents, revenue_cents,
                  job_class="general", verified=True):
    """Run one job to completion so it can enter the calibration sample."""
    estimate = rig.governor.estimate(
        job_id=job_id, revenue_cents=revenue_cents, signals=LocationSignals(),
        quantities={}, job_class=job_class,
        direct_lines=[CostLine(category=CostCategory.VENDOR, amount=estimated_cents)])
    rig.ledger.record_cost(job_id=job_id, category=CostCategory.VENDOR,
                           amount_cents=actual_cents, source="RECEIPT")
    payment = rig.ledger.open_payment(job_id=job_id, amount_cents=revenue_cents,
                                      rail="test")
    if verified:
        rig.ledger.set_payment_state(payment, PaymentState.PAID,
                                     collected_cents=revenue_cents,
                                     verification_method="test_rail_webhook")
    else:
        rig.ledger.set_payment_state(payment, PaymentState.INVOICED)
    return estimate


class DecisionOrder(unittest.TestCase):
    """The order of checks encodes the laws, so it is part of the contract."""

    def setUp(self):
        self.rig = Rig()
        self.rig.load_labor_fixtures()
        self.rig.relax_caps()

    def _estimate(self, job_id="J1", state="CA", hours=2, revenue=money(600)):
        return self.rig.governor.estimate(
            job_id=job_id, revenue_cents=revenue,
            signals=LocationSignals(project=Jurisdiction(state=state)),
            quantities={CostCategory.ON_SITE_LABOR: hours})

    def test_conformance_before_price(self):
        """A non-conforming option is not a cheap option. Price is never reached."""
        estimate = self._estimate()
        verdict, reason = self.rig.governor.decide(
            job_id="J1", revenue_cents=money(600), estimate=estimate,
            conformance_ok=False)
        self.assertIs(verdict, GovernorVerdict.REJECT)
        self.assertIn("price is not evaluated", reason)

    def test_location_before_binding_price(self):
        estimate = self.rig.governor.estimate(
            job_id="J2", revenue_cents=money(600), signals=LocationSignals(),
            quantities={CostCategory.ON_SITE_LABOR: 2})
        verdict, reason = self.rig.governor.decide(
            job_id="J2", revenue_cents=money(600), estimate=estimate,
            conformance_ok=True)
        self.assertIs(verdict, GovernorVerdict.PRICING_LOCATION_UNKNOWN)
        self.assertIn("ON_SITE_LABOR", reason)

    def test_margin_floor_is_enforced(self):
        estimate = self._estimate(hours=6, revenue=money(700))
        verdict, _ = self.rig.governor.decide(
            job_id="J1", revenue_cents=money(700), estimate=estimate,
            conformance_ok=True)
        self.assertIs(verdict, GovernorVerdict.MARGIN_TOO_LOW)

    def test_profitable_when_margin_clears_the_floor(self):
        estimate = self._estimate(hours=1, revenue=money(900))
        verdict, reason = self.rig.governor.decide(
            job_id="J1", revenue_cents=money(900), estimate=estimate,
            conformance_ok=True)
        self.assertIs(verdict, GovernorVerdict.PROFITABLE, reason)

    def test_high_value_requires_owner_approval(self):
        self.rig.policy.amend({"financial": {"owner_approval_above_cents": money(500)}},
                              OWNER, "test threshold")
        estimate = self._estimate(hours=1, revenue=money(900))
        verdict, _ = self.rig.governor.decide(
            job_id="J1", revenue_cents=money(900), estimate=estimate,
            conformance_ok=True)
        self.assertIs(verdict, GovernorVerdict.REQUIRES_APPROVAL)

    def test_decision_is_reproducible_from_the_record(self):
        """The audit trail must let the owner re-derive the verdict without the code."""
        estimate = self._estimate(hours=1, revenue=money(900))
        self.rig.governor.decide(job_id="J1", revenue_cents=money(900),
                                 estimate=estimate, conformance_ok=True)
        record = self.rig.governor.decisions_for("J1")[0]
        for field in ("verdict", "revenue_cents", "effective_cost_cents", "margin",
                      "floor", "policy_version", "calibration", "estimate_id"):
            self.assertIsNotNone(record[field], field)


class CalibrationMath(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()
        self.rig.relax_caps()

    def test_bootstrap_holds_until_minimum_sample_reached(self):
        """With no track record Solvent must find clearly profitable work."""
        self.assertEqual(self.rig.governor.calibration_for(), 1.5)
        for i in range(3):
            synthetic_job(self.rig, f"J{i}", estimated_cents=10_000,
                          actual_cents=10_000, revenue_cents=money(500))
        result = self.rig.governor.recalibrate()
        self.assertFalse(result["applied"])
        self.assertEqual(result["k"], 1.5)

    def test_accurate_estimating_lowers_k_but_only_at_the_permitted_rate(self):
        for i in range(8):
            synthetic_job(self.rig, f"J{i}", estimated_cents=10_000,
                          actual_cents=10_000, revenue_cents=money(500))
        result = self.rig.governor.recalibrate()
        self.assertTrue(result["applied"])
        self.assertAlmostEqual(result["raw"], 1.0, places=3)
        # Loosening is rate-limited: one batch of good news cannot drop K to 1.0.
        self.assertAlmostEqual(result["k"], 1.5 * 0.9, places=4)
        self.assertIn("rate-limited", result["reason"])

    def test_optimistic_estimating_tightens_the_gate_automatically(self):
        """The central property: drift makes the gate stricter with no owner action."""
        for i in range(8):
            synthetic_job(self.rig, f"J{i}", estimated_cents=10_000,
                          actual_cents=18_000, revenue_cents=money(500))
        result = self.rig.governor.recalibrate()
        self.assertGreaterEqual(result["k"], 1.5)
        self.assertAlmostEqual(result["raw"], 1.8, places=3)
        self.assertIn("immediately", result["reason"])

    def test_inflating_estimates_is_not_a_profitable_exploit(self):
        """Lowering K by over-estimating costs acceptance immediately."""
        rig = self.rig
        for i in range(8):
            synthetic_job(rig, f"J{i}", estimated_cents=40_000, actual_cents=10_000,
                          revenue_cents=money(500))
        result = rig.governor.recalibrate()
        self.assertLess(result["raw"], 1.0)
        # K never drops below 1.0 in effect: apply_factor refuses to discount costs.
        inflated = rig.governor.estimate(
            job_id="X", revenue_cents=money(500), signals=LocationSignals(),
            quantities={}, direct_lines=[CostLine(category=CostCategory.VENDOR,
                                                  amount=40_000)])
        self.assertGreaterEqual(inflated.effective_cost_cents, 40_000,
                                "calibration must never make a cost cheaper")

    def test_calibration_is_stratified_by_job_class(self):
        """An improving easy stratum must not loosen the gate for hard work."""
        for i in range(8):
            synthetic_job(self.rig, f"easy{i}", estimated_cents=10_000,
                          actual_cents=10_000, revenue_cents=money(500),
                          job_class="easy")
        for i in range(8):
            synthetic_job(self.rig, f"hard{i}", estimated_cents=10_000,
                          actual_cents=20_000, revenue_cents=money(500),
                          job_class="hard")
        self.rig.governor.recalibrate("easy")
        self.rig.governor.recalibrate("hard")
        self.assertLess(self.rig.governor.calibration_for("easy"),
                        self.rig.governor.calibration_for("hard"))

    def test_unverified_jobs_are_not_evidence(self):
        """Verify before learn, applied to the Governor's own self-measurement."""
        for i in range(8):
            synthetic_job(self.rig, f"J{i}", estimated_cents=10_000,
                          actual_cents=10_000, revenue_cents=money(500),
                          verified=False)
        result = self.rig.governor.recalibrate()
        self.assertEqual(result["n"], 0)
        self.assertFalse(result["applied"])

    def test_severe_degradation_triggers_rollback_and_escalation(self):
        for i in range(8):
            synthetic_job(self.rig, f"J{i}", estimated_cents=10_000,
                          actual_cents=40_000, revenue_cents=money(500))
        result = self.rig.governor.recalibrate()
        self.assertIn("degraded", result["reason"])
        events = [e["event"] for e in self.rig.audit.events()]
        self.assertIn("governor.calibration_degraded", events)


class KnownVersusEstimatedCost(unittest.TestCase):
    def test_calibration_does_not_inflate_known_costs(self):
        """Applying an uncertainty factor to a published fee is nonsense."""
        rig = Rig()
        rig.relax_caps()
        estimate = rig.governor.estimate(
            job_id="J1", revenue_cents=money(1000), signals=LocationSignals(),
            quantities={},
            direct_lines=[
                CostLine(category=CostCategory.PLATFORM_FEES, amount=5_000),
                CostLine(category=CostCategory.VENDOR, amount=10_000),
            ])
        self.assertEqual(estimate.known_cents, 5_000)
        # Known fee passes at face value; only the uncertain line is scaled.
        self.assertEqual(
            estimate.effective_cost_cents,
            5_000 + int((10_000 + rig.policy.verification_budget(money(1000))) * 1.5))


class SpendControl(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()
        self.rig.relax_caps()
        self.grant = self.rig.governor.grant_budget(
            job_id="J1", decision_id="d1", authorized_cents=10_000)

    def test_spend_within_grant_is_authorized(self):
        ok, _ = self.rig.governor.authorize_spend(
            grant_id=self.grant, amount_cents=4_000, what="inference")
        self.assertTrue(ok)

    def test_spend_beyond_grant_is_refused(self):
        self.rig.governor.authorize_spend(grant_id=self.grant, amount_cents=9_000,
                                          what="inference")
        ok, reason = self.rig.governor.authorize_spend(
            grant_id=self.grant, amount_cents=2_000, what="more inference")
        self.assertFalse(ok)
        self.assertIn("exceed grant", reason)

    def test_spend_without_a_grant_is_refused(self):
        ok, reason = self.rig.governor.authorize_spend(
            grant_id="grant_does_not_exist", amount_cents=1, what="sneaky")
        self.assertFalse(ok)

    def test_anomalous_single_spend_is_refused(self):
        """A runaway loop shows up as one call that dwarfs the calls before it.

        The spend is well inside the grant, so this is genuinely the anomaly
        check firing and not the ceiling check in disguise.
        """
        grant = self.rig.governor.grant_budget(job_id="J2", decision_id="d2",
                                               authorized_cents=1_000_000)
        for _ in range(4):
            ok, _ = self.rig.governor.authorize_spend(
                grant_id=grant, amount_cents=1_000, what="normal inference call")
            self.assertTrue(ok)
        ok, reason = self.rig.governor.authorize_spend(
            grant_id=grant, amount_cents=50_000, what="runaway loop")
        self.assertFalse(ok)
        self.assertIn("anomalous", reason)

    def test_normal_variation_is_not_flagged_as_anomalous(self):
        """The detector must not cry wolf, or it will be turned off."""
        grant = self.rig.governor.grant_budget(job_id="J3", decision_id="d3",
                                               authorized_cents=1_000_000)
        for amount in (1_000, 1_200, 900, 1_500):
            ok, reason = self.rig.governor.authorize_spend(
                grant_id=grant, amount_cents=amount, what="ordinary call")
            self.assertTrue(ok, reason)

    def test_kill_switch_stops_spending_already_in_flight(self):
        """Checked before every spend, not at job start."""
        self.rig.policy.set_operating_mode(OperatingMode.PAUSE_SPEND, OWNER, "test")
        ok, reason = self.rig.governor.authorize_spend(
            grant_id=self.grant, amount_cents=1, what="mid-flight")
        self.assertFalse(ok)
        self.assertIn("PAUSE_SPEND", reason)

    def test_require_spend_raises_so_callers_cannot_proceed_anyway(self):
        with self.assertRaises(GovernorRefused):
            self.rig.governor.require_spend(grant_id=self.grant, amount_cents=999_999,
                                            what="too much")

    def test_committed_unverified_exposure_is_capped(self):
        """Bounds the damage of a calibration drift nobody has noticed yet."""
        rig = Rig()
        rig.policy.amend({"financial": {"max_committed_unverified_cents": 20_000,
                                        "max_job_spend_cents": 10_000_000,
                                        "owner_approval_above_cents": 10_000_000}},
                         OWNER, "test exposure cap")
        rig.governor.grant_budget(job_id="JA", decision_id="d", authorized_cents=19_000)
        estimate = rig.governor.estimate(
            job_id="JB", revenue_cents=money(1000), signals=LocationSignals(),
            quantities={}, direct_lines=[CostLine(category=CostCategory.VENDOR,
                                                  amount=5_000)])
        verdict, reason = rig.governor.decide(job_id="JB", revenue_cents=money(1000),
                                              estimate=estimate, conformance_ok=True)
        self.assertIs(verdict, GovernorVerdict.HIGH_RISK)
        self.assertIn("exposure", reason)


class VerificationEconomics(unittest.TestCase):
    def test_verification_cost_is_priced_before_acceptance(self):
        """A job that cannot afford to be verified is rejected, not discovered later."""
        rig = Rig()
        rig.relax_caps()
        estimate = rig.governor.estimate(
            job_id="J1", revenue_cents=money(1000), signals=LocationSignals(),
            quantities={}, direct_lines=[CostLine(category=CostCategory.VENDOR,
                                                  amount=1_000)])
        categories = [line.category for line in estimate.lines]
        self.assertIn(CostCategory.VERIFICATION, categories)


if __name__ == "__main__":
    unittest.main()
