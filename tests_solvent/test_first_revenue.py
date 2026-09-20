"""First-Revenue Mode, the manual bridge, and the readiness projection."""

import unittest

from solvent.discovery import Compliance, ManualSource, Readiness as SourceReadiness
from solvent.errors import FailClosed
from solvent.harness import Solvent, run_manual_opportunity
from solvent.owner import OwnerChannel
from solvent.readiness import first_revenue_readiness
from solvent.types import PaymentState, money
from tests_solvent.fixtures import OWNER, Rig, TEST_ONLY_OWNER_KEY


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
                               self.solvent.policy,
                               key=TEST_ONLY_OWNER_KEY)
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


class BootstrapIsAPreferenceNotAVeto(unittest.TestCase):
    """Bootstrap nudges ranking. It can never reject a job."""

    def _rig(self, board, **policy):
        from solvent.capability import Capability
        from solvent.discovery import Compliance, FixtureSource
        from solvent.discovery import Readiness as R
        from solvent.types import CostCategory, Jurisdiction, PricingTier
        from solvent.harness import OWNER as HOWNER, Solvent
        s = Solvent()
        for state, rate in (("CA", 9_500), ("NC", 5_200)):
            s.pricing.ingest(
                category=CostCategory.ON_SITE_LABOR,
                jurisdiction=Jurisdiction(state=state), tier=PricingTier.MARKET_DATA,
                unit_cents=rate, unit="hour", source="FIXTURE",
                effective_date="2026-06-01", retrieved_date="2026-09-01",
                confidence=0.9, owner_identity=HOWNER, is_fixture=True)
        s.capability.register(
            Capability(name="spreadsheet", covers=frozenset({"spreadsheet"}),
                       proven=True), owner_identity=HOWNER)
        s.discovery.register_source(
            FixtureSource("board", board), owner_identity=HOWNER,
            readiness=R.PERMITTED_AUTOMATION, compliance=Compliance.PERMITTED,
            determination="fixture")
        s.policy.amend({
            "discovery": {"approved_sources": ["board"]},
            "qualification": {"minimum_value_cents": money("50"),
                              "max_concurrent_jobs": 3, "default_hours": 2.0},
            "financial": {"max_job_spend_cents": money("5000"),
                          "max_committed_unverified_cents": money("50000"),
                          "owner_approval_above_cents": money("10000")},
            **policy}, HOWNER, "bootstrap test")
        return s

    @staticmethod
    def _posting(ref, dollars, upfront=0):
        return {"ref": ref, "title": f"job {ref}", "quoted_cents": money(dollars),
                "needs": ["spreadsheet"], "project_state": "NC",
                "client_ref": "c", "body": "x", "upfront_cost_cents": money(upfront)}

    def _run(self, s):
        candidates = s.discovery.poll("board")
        survivors, _ = s.qualification.triage(candidates)
        return [s.qualification.qualify(c) for c in survivors]

    def test_a_job_needing_money_up_front_still_gets_accepted(self):
        """The preference must not become a disguised rejection."""
        from solvent.qualification import Verdict
        s = self._rig([self._posting("upfront", "900", upfront="50")])
        decisions = self._run(s)
        self.assertIs(decisions[0].verdict, Verdict.ACCEPT)

    def test_between_equal_jobs_the_zero_upfront_one_ranks_higher(self):
        s = self._rig([self._posting("needs_cash", "900", upfront="50"),
                       self._posting("no_cash", "900")])
        ranked = s.qualification.rank(self._run(s))
        self.assertEqual(ranked[0].opportunity.external_ref, "no_cash")

    def test_a_clearly_better_job_still_wins_despite_needing_money_up_front(self):
        """Bootstrap must not block an obviously superior opportunity."""
        s = self._rig([self._posting("small_free", "600"),
                       self._posting("big_upfront", "2400", upfront="50")])
        ranked = s.qualification.rank(self._run(s))
        self.assertEqual(ranked[0].opportunity.external_ref, "big_upfront")

    def test_the_penalty_is_bounded_so_it_cannot_become_a_veto(self):
        s = self._rig([self._posting("upfront", "900", upfront="50")],
                      bootstrap={"prefer_low_upfront": True, "upfront_penalty": 5.0})
        ranked = s.qualification.rank(self._run(s))
        self.assertGreater(ranked[0].score, 0.0,
                           "an unbounded penalty would zero the score")

    def test_bootstrap_can_be_switched_off(self):
        s = self._rig([self._posting("a", "900", upfront="50"),
                       self._posting("b", "900")],
                      bootstrap={"prefer_low_upfront": False})
        ranked = s.qualification.rank(self._run(s))
        self.assertAlmostEqual(ranked[0].score, ranked[1].score, places=2)


class FirstRealJobAcceptanceContract(unittest.TestCase):
    """The preconditions are an executable check, not a checklist to remember."""

    def test_a_fresh_instance_refuses_and_lists_every_blocker(self):
        from solvent.harness import Solvent
        solvent = Solvent()
        with self.assertRaises(FailClosed) as ctx:
            solvent.assert_ready_for_real_job()
        message = str(ctx.exception)
        for expected in ("OD-1", "OD-2", "OD-3", "OD-12"):
            with self.subTest(blocker=expected):
                self.assertIn(expected, message)

    def test_fail_closed_external_execution_alone_blocks_a_real_job(self):
        """A job that cannot be delivered or paid is not a job worth starting."""
        from solvent.harness import Solvent
        solvent = Solvent()
        with self.assertRaises(FailClosed) as ctx:
            solvent.assert_ready_for_real_job()
        self.assertIn("fail-closed", str(ctx.exception))

    def test_the_contract_names_the_category_of_each_blocker(self):
        from solvent.harness import Solvent
        report = Solvent().readiness()
        for item in report.blocking:
            with self.subTest(item=item):
                self.assertTrue(item.startswith("["), item)
