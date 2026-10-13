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

    def write(self):
        from solvent.harness import _write_invoice
        from solvent.quoting import BillingBasis

        class Cycle:
            job_id = "job_1"
            client_ref = "client:acme"
            opportunity_ref = "ref-1"
            invoiced_cents = 40_000
            billable_hours = 1.0
            execution_wall_seconds = 12.0
            verification_rounds = 1
            human_equivalent_hours = 0.0

        return _write_invoice(self.s, Cycle(), workdir=self.box.name,
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
