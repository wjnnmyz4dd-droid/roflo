"""The four commercial terms, and what happens while they are unset.

Each one was a default that read as permission. No floor meant no floor
was applied. Compute at zero meant every margin shown was better than the
real one. A minimum margin was read by the list of gaps that complains it
is missing, and by nothing else in the system -- the number existed to be
reported absent. And the gap for market rates said "no *cited* rates" and
checked only that some rate existed, so a number typed in with no source
closed it.

The policy comments beside these keys already said the right thing:
"Zero means the owner has not set one, not that any price is acceptable."
That was true of the comment and not of the code.
"""

from __future__ import annotations

import tempfile
import unittest

from solvent.discovery import Compliance, FixtureSource, Opportunity, Readiness
from solvent.finder import Finder, Verdict
from solvent.harness import OWNER, Solvent, owner_promotions, promote_capability
from tests_solvent.test_business_cycle import Cycle as BusinessCycleBase

BOARD = "board"

#: Everything the owner has to decide before DeskPilot may recommend work.
COMPLETE = {
    "minimum_project_fee_cents": 4_000,
    "market_hourly_cents": {"csv-cleanup": 3_000},
    "market_rate_citations": {"csv-cleanup": "test fixture, not a citation"},
    "compute_cents_per_cpu_hour": 12,
    "minimum_margin_fraction": 0.05,
}


class Base(unittest.TestCase):

    def setUp(self):
        self.box = tempfile.TemporaryDirectory()
        self.addCleanup(self.box.cleanup)
        self.s = Solvent(f"{self.box.name}/commercial.db")
        self.addCleanup(self.close)
        for name in owner_promotions():
            promote_capability(self.s, name, owner_identity=OWNER, why="test")
        self.s.discovery.register_source(
            FixtureSource(BOARD, []), owner_identity=OWNER,
            readiness=Readiness.PERMITTED_AUTOMATION,
            compliance=Compliance.PERMITTED, determination="test fixture")
        self.finder = Finder(self.s.discovery, self.s.policy,
                             self.s.capability, self.s.orchestrator,
                             self.s.store)

    def close(self):
        try:
            self.s.store.close()
        except Exception:
            pass

    def configure(self, **overrides):
        terms = dict(COMPLETE)
        terms.update(overrides)
        self.s.policy.amend({"commercial": terms}, OWNER, "test: configure")

    def posting(self, cents=60_000, **over):
        from solvent.content import quarantine
        from solvent.types import PrivacyClass

        fields = {"id": "o1", "source": BOARD, "external_ref": "r1",
                  "title": "Tidy a spreadsheet", "quoted_cents": cents,
                  "needs": ("csv-cleanup",), "payment_structure": "FIXED",
                  "posting": quarantine("A straightforward file.",
                                        source=BOARD,
                                        privacy=PrivacyClass.PUBLIC)}
        fields.update(over)
        return Opportunity(**fields)

    def verdict(self, **over):
        return self.finder.assess(self.posting(**over), extension="xlsx")


class NothingIsRecommendedUntilAllFourAreSet(Base):

    def test_an_unconfigured_installation_recommends_nothing(self):
        assessment = self.verdict()
        self.assertIs(assessment.verdict, Verdict.HOLD)
        self.assertIn("commercial terms", assessment.why)

    def test_a_fully_configured_one_does(self):
        self.configure()
        self.assertIs(self.verdict().verdict, Verdict.PURSUE)

    def test_each_missing_term_on_its_own_is_enough_to_stop_it(self):
        """One at a time, so no gap is load-bearing for another."""
        for term, cleared in (("minimum_project_fee_cents", 0),
                              ("compute_cents_per_cpu_hour", 0),
                              ("minimum_margin_fraction", 0.0)):
            with self.subTest(missing=term):
                self.setUp()
                self.configure(**{term: cleared})
                assessment = self.verdict()
                self.assertIsNot(
                    assessment.verdict, Verdict.PURSUE,
                    f"{term} is unset and work was still recommended")

    def test_the_assessment_still_explains_itself(self):
        """Refusing to recommend is not refusing to answer."""
        assessment = self.verdict()
        self.assertTrue(assessment.why)
        self.assertIsNotNone(assessment.economics)


class TheOwnersMarginIsApplied(Base):

    def test_clearing_cost_by_a_hair_is_not_worth_taking(self):
        self.configure(minimum_margin_fraction=0.20)
        assessment = self.verdict(cents=40_000, upfront_cost_cents=39_000)
        self.assertIs(assessment.verdict, Verdict.DECLINE)
        self.assertIn("margin", assessment.why)
        self.assertIn("20%", assessment.why)

    def test_a_healthy_margin_passes(self):
        self.configure(minimum_margin_fraction=0.20)
        self.assertIs(self.verdict(cents=60_000).verdict, Verdict.PURSUE)

    def test_the_margin_is_a_share_not_a_floor(self):
        """A big job can fail the margin while clearing the cash floor."""
        self.configure(minimum_margin_fraction=0.50)
        assessment = self.verdict(cents=100_000, upfront_cost_cents=60_000)
        self.assertIs(assessment.verdict, Verdict.DECLINE)
        self.assertIn("margin", assessment.why)


class AnUncitedRateIsNotEvidence(Base):

    def test_a_rate_without_a_citation_leaves_the_gap_open(self):
        self.configure(market_hourly_cents={"csv-cleanup": 3_000},
                       market_rate_citations={})
        gaps = " | ".join(self.finder.configuration_gaps())
        self.assertIn("no recorded citation", gaps)

    def test_an_uncited_rate_cannot_be_read_for_a_quote(self):
        self.configure(market_hourly_cents={"csv-cleanup": 3_000},
                       market_rate_citations={})
        cents, why = self.finder.market_rate_cents("csv-cleanup")
        self.assertEqual(cents, 0)
        self.assertIn("uncited", why)

    def test_a_cited_rate_is_returned_with_its_citation(self):
        self.configure(
            market_hourly_cents={"csv-cleanup": 3_000},
            market_rate_citations={"csv-cleanup": "BLS OEWS 43-9021, 2025"})
        cents, citation = self.finder.market_rate_cents("csv-cleanup")
        self.assertEqual(cents, 3_000)
        self.assertIn("BLS", citation)

    def test_a_capability_with_no_rate_says_so(self):
        self.configure()
        cents, why = self.finder.market_rate_cents("report-builder")
        self.assertEqual(cents, 0)
        self.assertIn("no market rate", why)


class TheGapsAreTheGate(Base):

    def test_configured_reports_both_the_answer_and_the_reasons(self):
        ok, gaps = self.finder.commercially_configured()
        self.assertFalse(ok)
        self.assertTrue(gaps)
        self.configure()
        ok, gaps = self.finder.commercially_configured()
        self.assertTrue(ok, gaps)
        self.assertEqual(gaps, [])

    def test_a_posting_that_should_be_declined_still_is(self):
        """The gate must not turn every answer into HOLD. A posting that
        is wrong on its own terms is still declined while the business
        is unconfigured -- the gate is the last question asked, not the
        first."""
        assessment = self.verdict(needs=("neurosurgery",))
        self.assertIs(assessment.verdict, Verdict.DECLINE)


if __name__ == "__main__":
    unittest.main()


class AnInvoiceNamesWhoIsAskingToBePaid(unittest.TestCase):
    """The payment-readiness defect found in this pass.

    `_write_invoice` printed "(contracting identity not recorded)" into
    the From line and carried on. That produces a document that looks
    like a bill, names nobody to pay, cannot be sent, and -- worse --
    lets the pipeline report an invoice stage it completed.

    Policy already knew which fields an invoice needs and already had
    the question `may_contract_for("INVOICE")`. Nothing asked it.
    """

    def setUp(self):
        self.box = tempfile.TemporaryDirectory()
        self.addCleanup(self.box.cleanup)
        self.s = Solvent(f"{self.box.name}/invoice.db")
        self.addCleanup(self.close)

    def close(self):
        try:
            self.s.store.close()
        except Exception:
            pass

    def write(self, **fields):
        """A real ``BusinessCycle``, not a stand-in for one.

        This used to be a hand-written class with the seven
        attributes the invoice happened to read. Adding a field to
        ``BusinessCycle`` then broke it with an AttributeError from
        inside production code -- the same "hand-written mirror falls
        behind the type" shape the rest of this suite keeps finding.
        """
        from solvent.harness import BusinessCycle, _write_invoice
        from solvent.quoting import BillingBasis

        cycle = BusinessCycle()
        cycle.job_id = "job_1"
        cycle.client_ref = "client:acme"
        cycle.opportunity_ref = "ref-1"
        cycle.invoiced_cents = 40_000
        cycle.due_cents = 40_000
        cycle.billable_hours = 1.0
        cycle.execution_wall_seconds = 12.0
        cycle.verification_rounds = 1
        for name, value in fields.items():
            setattr(cycle, name, value)
        return _write_invoice(self.s, cycle, workdir=self.box.name,
                              basis=BillingBasis.FIXED_PRICE,
                              agreed_hourly_cents=4_000)

    def identify(self, **over):
        fields = {"legal_name": "TEST-ONLY Owner",
                  "email": "test-only@example.invalid",
                  "address": "1 Test Street, Testville"}
        fields.update(over)
        self.s.policy.record_contracting_structure(
            owner_identity=OWNER, structure="INDIVIDUAL",
            reason="test", **fields)

    def test_no_identity_means_no_invoice(self):
        from solvent.errors import FailClosed

        with self.assertRaises(FailClosed) as caught:
            self.write()
        self.assertIn("who is asking to be paid", str(caught.exception))

    def test_a_partial_identity_is_still_no_invoice(self):
        """An address is one of the three fields an invoice needs."""
        from solvent.errors import FailClosed

        self.s.policy.record_contracting_structure(
            owner_identity=OWNER, structure="INDIVIDUAL", reason="test",
            legal_name="TEST-ONLY Owner", email="test-only@example.invalid")
        with self.assertRaises(FailClosed):
            self.write()

    def test_a_complete_identity_issues_one_that_names_the_issuer(self):
        import pathlib

        self.identify()
        text = pathlib.Path(self.write()).read_text(encoding="utf-8")
        self.assertIn("TEST-ONLY Owner", text)
        self.assertNotIn("not recorded", text)


class TheCycleStopsAtInvoiceRatherThanExploding(BusinessCycleBase):
    """A cycle that cannot invoice has still delivered and verified the
    work. That is worth recording, and it is not a crash.

    Driven through the real cycle, because the first version of this
    test asserted that ``run_business_cycle`` was callable, which is
    true of every function in the file and proves nothing about any of
    them.
    """

    def setUp(self):
        super().setUp()
        # The shared rig records an issuer now. Take it away again: an
        # empty structure is what an installation looks like before the
        # owner has answered.
        self.s.policy.amend({"governance": {"contracting": {"structure": ""}}},
                            OWNER, "test: the owner has not answered yet")

    def test_it_stops_at_invoice_and_says_why(self):
        cycle = self.runCycle()
        self.assertFalse(cycle.completed)
        self.assertEqual(cycle.stopped_at, "invoice")
        self.assertIn("who is asking to be paid", cycle.why_stopped)

    def test_the_work_was_still_done_and_verified(self):
        cycle = self.runCycle()
        self.assertIn("execute", cycle.stages)
        self.assertIn("verify", cycle.stages)
        self.assertNotIn("invoice", cycle.stages)

    def test_no_invoice_file_was_written(self):
        cycle = self.runCycle()
        self.assertFalse(cycle.invoice_path)

    def test_the_audit_chain_still_verifies(self):
        cycle = self.runCycle()
        self.assertTrue(cycle.audit_chain_ok)


class ThePlatformFeeHasAFloor(unittest.TestCase):
    """Freelancer publishes "10% or $5.00 USD, whichever is greater".

    A fee model that knows only the percentage charges a $40 job $4
    and is wrong by a dollar in the direction that makes the job look
    worth taking. Verified against the platform's own fees page on
    2026-10-14.
    """

    def setUp(self):
        self.box = tempfile.TemporaryDirectory()
        self.addCleanup(self.box.cleanup)
        self.s = Solvent(f"{self.box.name}/fees.db")
        self.addCleanup(self.close)
        self.s.policy.amend(
            {"commercial": {"platform_fee_fraction": 0.10,
                            "platform_fee_minimum_cents": 500}},
            OWNER, "test: the platform's published terms")

    def close(self):
        try:
            self.s.store.close()
        except Exception:
            pass

    def cost(self, price):
        from solvent import quoting

        return quoting.operating_cost(
            quoting.ExecutionMeasurement(wall_seconds=60, cpu_seconds=60),
            price_cents=price,
            rates=quoting.rates_from_policy(self.s.policy))

    def test_a_small_job_pays_the_floor_not_the_percentage(self):
        self.assertEqual(self.cost(4_000).platform_fee_cents, 500)

    def test_a_large_job_pays_the_percentage(self):
        self.assertEqual(self.cost(25_000).platform_fee_cents, 2_500)

    def test_the_crossover_is_where_the_two_meet(self):
        self.assertEqual(self.cost(5_000).platform_fee_cents, 500)
        self.assertEqual(self.cost(6_000).platform_fee_cents, 600)

    def test_the_basis_says_which_rule_applied(self):
        lines = " ".join(self.cost(4_000).basis)
        self.assertIn("the floor the platform takes", lines)
        self.assertIn("the floor the platform takes",
                      " ".join(self.cost(4_000).basis))

    def test_no_floor_configured_means_the_percentage_alone(self):
        self.s.policy.amend({"commercial":
                             {"platform_fee_minimum_cents": 0}},
                            OWNER, "test: no floor recorded")
        self.assertEqual(self.cost(4_000).platform_fee_cents, 400)

    def test_a_free_job_pays_no_fee_at_all(self):
        """The floor is per job charged, not a charge for existing."""
        self.assertEqual(self.cost(0).platform_fee_cents, 0)

    def test_understating_the_fee_would_overstate_the_margin(self):
        """Why this matters: it is the direction that accepts bad work."""
        with_floor = self.cost(4_000).total_cents
        self.s.policy.amend({"commercial":
                             {"platform_fee_minimum_cents": 0}},
                            OWNER, "test: without the floor")
        self.assertLess(self.cost(4_000).total_cents, with_floor)


class TheFeesCanActuallyBeRecorded(unittest.TestCase):
    """`solvent setup commercial` could not record a single fee.

    The revenue model reads five cost inputs -- the platform's
    percentage and its floor, the payment provider's percentage and
    its flat charge, and the fixed monthly overhead -- and treats each
    unset one as UNKNOWN rather than as zero, which is right. But no
    command set any of them, so every net figure the model produced
    was the gross figure wearing a different label, and the only
    remedy documented anywhere was editing the policy by hand.

    A report that told the owner to configure these with a command
    that could not configure them would have been the second time in
    this engagement that a checklist named a command that did not do
    what it said.
    """

    def setUp(self):
        import tempfile

        self.box = tempfile.TemporaryDirectory()
        self.addCleanup(self.box.cleanup)
        self.db = f"{self.box.name}/fees.db"

    def run_cli(self, *args):
        import contextlib
        import io

        from solvent.cli import main

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(["setup", "commercial", "--db", self.db,
                         "--owner", OWNER, *args])
        return code, out.getvalue()

    def recorded(self, name):
        solvent = Solvent(self.db)
        self.addCleanup(lambda: solvent.store.close())
        return solvent.policy.get("commercial", name, default=None)

    def test_the_platform_percentage_is_recorded(self):
        code, said = self.run_cli("--platform-fee-fraction", "0.1")
        self.assertEqual(code, 0, said)
        self.assertEqual(self.recorded("platform_fee_fraction"), 0.1)

    def test_the_platform_floor_is_recorded(self):
        """Freelancer takes 10% or $5, whichever is greater."""
        code, said = self.run_cli("--platform-fee-minimum-cents", "500")
        self.assertEqual(code, 0, said)
        self.assertEqual(self.recorded("platform_fee_minimum_cents"), 500)

    def test_the_payment_fees_are_recorded(self):
        code, said = self.run_cli("--payment-fee-fraction", "0.029",
                                  "--payment-fee-flat-cents", "30")
        self.assertEqual(code, 0, said)
        self.assertEqual(self.recorded("payment_fee_fraction"), 0.029)
        self.assertEqual(self.recorded("payment_fee_flat_cents"), 30)

    def test_the_monthly_overhead_is_recorded(self):
        code, said = self.run_cli("--fixed-monthly-overhead-cents", "9800")
        self.assertEqual(code, 0, said)
        self.assertEqual(self.recorded("fixed_monthly_overhead_cents"), 9800)

    def test_the_hourly_rate_is_recorded(self):
        code, said = self.run_cli("--hourly-rate-cents", "4000")
        self.assertEqual(code, 0, said)
        self.assertEqual(self.recorded("hourly_rate_cents"), 4000)

    def test_a_fraction_above_one_is_refused(self):
        code, said = self.run_cli("--platform-fee-fraction", "1.5")
        self.assertEqual(code, 1, said)
        self.assertIn("not a fraction below one", said)

    def test_a_negative_charge_is_refused(self):
        code, said = self.run_cli("--payment-fee-flat-cents", "-1")
        self.assertEqual(code, 1, said)
        self.assertIn("cannot be negative", said)

    def test_a_refusal_records_nothing(self):
        """The shipped default is 0.0, so "unchanged" is 0.0, not None."""
        self.run_cli("--platform-fee-fraction", "1.5")
        self.assertEqual(self.recorded("platform_fee_fraction"), 0.0)

    def test_unrecorded_fees_are_said_out_loud(self):
        """Unrecorded is not zero, and silence would read as zero."""
        code, said = self.run_cli("--minimum-fee-cents", "4000")
        self.assertEqual(code, 0, said)
        self.assertIn("unrecorded is not zero", said)
        for name in ("platform_fee_fraction", "platform_fee_minimum_cents",
                     "payment_fee_fraction", "payment_fee_flat_cents",
                     "fixed_monthly_overhead_cents"):
            with self.subTest(name=name):
                self.assertIn(name, said)

    def test_once_recorded_they_stop_being_listed(self):
        code, said = self.run_cli(
            "--platform-fee-fraction", "0.1",
            "--platform-fee-minimum-cents", "500",
            "--payment-fee-fraction", "0.029",
            "--payment-fee-flat-cents", "30",
            "--fixed-monthly-overhead-cents", "9800")
        self.assertEqual(code, 0, said)
        self.assertNotIn("unrecorded is not zero", said)
