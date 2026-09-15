"""First-Revenue Mode, the manual bridge, and the readiness projection."""

import unittest

from solvent.discovery import Compliance, ManualSource, Readiness as SourceReadiness
from solvent.errors import FailClosed
from solvent.harness import Solvent, run_manual_opportunity
from solvent.owner import OwnerChannel
from solvent.readiness import first_revenue_readiness
from solvent.types import PaymentState, money
from tests_solvent.fixtures import OWNER, Rig


class FirstRevenueProfile(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()

    def enter(self, **kwargs):
        defaults = dict(owner_identity=OWNER, max_job_spend_cents=money("200"),
                        max_committed_unverified_cents=money("400"),
                        margin_floor=0.35, source="owner_entered",
                        capabilities=["spreadsheet"], reason="first revenue attempt")
        return self.rig.policy.enter_first_revenue_mode(**{**defaults, **kwargs})

    def test_the_mode_constrains_solvent_to_one_job_and_one_source(self):
        self.enter()
        self.assertTrue(self.rig.policy.first_revenue_mode)
        self.assertEqual(
            self.rig.policy.get("qualification", "max_concurrent_jobs"), 1)
        self.assertEqual(
            self.rig.policy.get("discovery", "approved_sources"), ["owner_entered"])

    def test_spend_limits_must_come_from_the_owner(self):
        """An unset ceiling is an undecided ceiling, not an unlimited one."""
        for bad in ({"max_job_spend_cents": 0},
                    {"max_committed_unverified_cents": 0},
                    {"max_job_spend_cents": -1}):
            with self.subTest(bad=bad), self.assertRaises(FailClosed):
                self.enter(**bad)

    def test_a_nonsensical_margin_floor_is_refused(self):
        for floor in (0.0, 1.0, 1.5, -0.2):
            with self.subTest(floor=floor), self.assertRaises(FailClosed):
                self.enter(margin_floor=floor)

    def test_the_mode_forbids_autonomous_growth(self):
        self.enter()
        for knob in ("autonomous_capability_install", "autonomous_contract_changes",
                     "autonomous_source_registration"):
            with self.subTest(knob=knob):
                self.assertFalse(self.rig.policy.get("growth", knob))

    def test_only_a_registered_owner_may_enter_the_mode(self):
        with self.assertRaises(FailClosed):
            self.enter(owner_identity="owner:impostor")

    def test_entering_the_mode_is_audited(self):
        self.enter()
        events = [e["event"] for e in self.rig.audit.events()]
        self.assertIn("policy.first_revenue_mode", events)


class ManualBridge(unittest.TestCase):
    """The acquisition path blocked by nothing external."""

    def test_an_owner_entered_opportunity_is_qualified_normally(self):
        result = run_manual_opportunity(
            title="Expense spreadsheet", client_ref="client_real",
            quote_dollars="450", needs=["spreadsheet"], project_state="CT")
        self.assertEqual(result["verdict"], "ACCEPT")
        self.assertEqual(result["governor"], "PROFITABLE")
        self.assertEqual(result["job_state"], "ACCEPTED")

    def test_owner_entered_requirements_are_owner_confirmed(self):
        """Nothing is more confirmed than the owner typing it in."""
        result = run_manual_opportunity(
            title="Expense spreadsheet", client_ref="c", quote_dollars="450",
            needs=["spreadsheet"], project_state="CT")
        self.assertEqual(result["requirements_source"], ["OWNER_CONFIRMED"])

    def test_the_manual_path_still_obeys_every_authority(self):
        """A manual job is not a privileged job."""
        result = run_manual_opportunity(
            title="Unprofitable job", client_ref="c", quote_dollars="60",
            needs=["spreadsheet"], project_state="CA")
        self.assertEqual(result["verdict"], "REJECT")
        self.assertEqual(result["reject_reason"], "MARGIN_TOO_LOW")

    def test_the_manual_source_is_not_a_fixture(self):
        source = ManualSource("owner_entered", [])
        self.assertFalse(source.is_fixture)
        self.assertTrue(source.owner_entered_source)

    def test_a_manual_source_still_needs_owner_registration(self):
        rig = Rig()
        from solvent.discovery import Discovery
        discovery = Discovery(rig.store, rig.audit, rig.policy)
        with self.assertRaises(FailClosed):
            discovery.register_source(
                ManualSource("m", []), owner_identity="qualification",
                readiness=SourceReadiness.PERMITTED_AUTOMATION,
                compliance=Compliance.PERMITTED, determination="x")


class ReadinessProjection(unittest.TestCase):
    def setUp(self):
        self.solvent = Solvent()

    def report(self, owner_channel=None):
        return first_revenue_readiness(
            policy=self.solvent.policy, ledger=self.solvent.ledger,
            capability=self.solvent.capability, discovery=self.solvent.discovery,
            owner_channel=owner_channel)

    def test_a_fresh_instance_is_not_ready_and_says_why(self):
        report = self.report()
        self.assertFalse(report.ready)
        self.assertTrue(report.blocking)

    def test_the_owner_decisions_are_named_explicitly(self):
        blocking = " ".join(self.report().blocking)
        for decision in ("OD-1", "OD-2", "OD-3", "OD-5"):
            with self.subTest(decision=decision):
                self.assertIn(decision, blocking)

    def test_engineering_work_is_reported_ready(self):
        ready = {c.name for c in self.report().checks if c.ready}
        self.assertIn("acquisition pipeline", ready)
        self.assertIn("execution and verification", ready)
        self.assertIn("location-aware pricing", ready)

    def test_an_owner_key_clears_the_authentication_check(self):
        channel = OwnerChannel(self.solvent.store, self.solvent.audit,
                               self.solvent.policy, key=b"k")
        checks = {c.name: c.ready for c in self.report(channel).checks}
        self.assertTrue(checks["authenticated owner approval"])

    def test_a_fixture_source_does_not_count_as_an_approved_work_source(self):
        """Proving the pipeline is not the same as having somewhere to sell."""
        from solvent.discovery import FixtureSource
        self.solvent.discovery.register_source(
            FixtureSource("board", []), owner_identity="owner:demo",
            readiness=SourceReadiness.PERMITTED_AUTOMATION,
            compliance=Compliance.PERMITTED, determination="fixture")
        checks = {c.name: c.ready for c in self.report().checks}
        self.assertFalse(checks["an approved work source"])

    def test_the_projection_stores_nothing(self):
        from solvent.store import TABLE_OWNER
        self.assertNotIn("readiness", TABLE_OWNER.values())


class PaymentLifecycle(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()
        self.payment = self.rig.ledger.open_payment(
            job_id="J1", amount_cents=money("500"), rail="test")

    def test_payment_pending_is_not_collected_revenue(self):
        """'The client says they paid' is an expectation, not money."""
        self.rig.ledger.set_payment_state(self.payment, PaymentState.PAYMENT_PENDING)
        self.assertFalse(PaymentState.PAYMENT_PENDING.is_collected)
        self.assertEqual(self.rig.ledger.collected_for("J1"), 0)

    def test_the_full_lifecycle_is_representable(self):
        for state in (PaymentState.ESTIMATED, PaymentState.AUTHORIZED,
                      PaymentState.INCURRED, PaymentState.INVOICED,
                      PaymentState.PAYMENT_PENDING, PaymentState.PAID,
                      PaymentState.PARTIALLY_PAID, PaymentState.OVERDUE,
                      PaymentState.REFUNDED, PaymentState.DISPUTED):
            with self.subTest(state=state):
                self.assertIsInstance(state.value, str)

    def test_only_verified_states_count_as_collected(self):
        collected = {s for s in PaymentState if s.is_collected}
        self.assertEqual(collected, {PaymentState.PAID, PaymentState.PARTIALLY_PAID})


if __name__ == "__main__":
    unittest.main()
