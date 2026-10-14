"""The owner's own numbers, applied, and what they leave open.

Four commercial terms decide whether DeskPilot may recommend work. The
owner has supplied three of them: $40/hour (already recorded), a $40
minimum project fee, a 30% minimum margin, and a VPS costing about
$98/month from which the compute rate is derived.

The fourth -- a cited market rate -- is deliberately not supplied here.
OD-7 records that the owner approves the rate source before anything is
loaded from it, and a rate this file invented would be exactly the
fabrication the citation requirement exists to prevent.
"""

from __future__ import annotations

import tempfile
import unittest

from solvent.cli import main as cli_main
from solvent.discovery import Compliance, FixtureSource, Opportunity, Readiness
from solvent.finder import ExecutionHistory, Finder, Verdict
from solvent.harness import OWNER, Solvent, owner_promotions, promote_capability

#: $98/month over an average month of 730.5 hours is $0.134155/hour.
#: Rounded up to 14 cents and charged per CPU-hour, which overstates
#: the rate on a machine with more than one core -- the safe direction,
#: because an understated cost produces a bid that loses money.
#:
#: It is also an amortisation, not a marginal cost: the $98 is owed
#: whether or not a job runs, so this only recovers it if the machine
#: is busy. At low utilisation the true cost of a job is higher than
#: any per-CPU-hour figure can express, which is an argument for taking
#: more work rather than for charging less.
VPS_MONTHLY_CENTS = 9_800
HOURS_PER_MONTH = 365.25 / 12 * 24
COMPUTE_CENTS_PER_CPU_HOUR = 14

OWNER_TERMS = {
    "minimum_project_fee_cents": 4_000,
    "minimum_margin_fraction": 0.30,
    "compute_cents_per_cpu_hour": COMPUTE_CENTS_PER_CPU_HOUR,
}


class Base(unittest.TestCase):

    def setUp(self):
        self.box = tempfile.TemporaryDirectory()
        self.addCleanup(self.box.cleanup)
        self.db = f"{self.box.name}/activation.db"
        self.s = Solvent(self.db)
        self.addCleanup(self.close)
        for name in owner_promotions():
            promote_capability(self.s, name, owner_identity=OWNER, why="test")
        self.s.discovery.register_source(
            FixtureSource("board", []), owner_identity=OWNER,
            readiness=Readiness.PERMITTED_AUTOMATION,
            compliance=Compliance.PERMITTED, determination="test fixture")

    def close(self):
        try:
            self.s.store.close()
        except Exception:
            pass

    def finder(self):
        return Finder(self.s.discovery, self.s.policy, self.s.capability,
                      self.s.orchestrator, self.s.store)

    def apply_owner_terms(self):
        self.s.policy.amend({"commercial": OWNER_TERMS}, OWNER,
                            "the owner's recorded commercial terms")

    def posting(self, cents=60_000):
        from solvent.content import quarantine
        from solvent.types import PrivacyClass

        return Opportunity(
            id="o1", source="board", external_ref="r1",
            title="Tidy a spreadsheet", quoted_cents=cents,
            needs=("csv-cleanup",), payment_structure="FIXED",
            posting=quarantine("A straightforward file.", source="board",
                               privacy=PrivacyClass.PUBLIC))


class TheComputeRateIsDerivedNotGuessed(unittest.TestCase):

    def test_the_arithmetic_is_what_it_claims(self):
        per_hour_cents = VPS_MONTHLY_CENTS / HOURS_PER_MONTH
        self.assertAlmostEqual(per_hour_cents, 13.4155, places=3)

    def test_it_rounds_up_rather_than_down(self):
        """Rounding a cost down flatters every margin computed from it."""
        per_hour_cents = VPS_MONTHLY_CENTS / HOURS_PER_MONTH
        self.assertGreaterEqual(COMPUTE_CENTS_PER_CPU_HOUR, per_hour_cents)


class WhatTheOwnersNumbersClose(Base):

    def test_three_of_the_four_blockers_close(self):
        self.apply_owner_terms()
        _, gaps = self.finder().commercially_configured()
        self.assertEqual(len(gaps), 1, gaps)
        self.assertIn("market rate", gaps[0])

    def test_the_last_one_is_the_cited_rate(self):
        self.apply_owner_terms()
        configured, gaps = self.finder().commercially_configured()
        self.assertFalse(configured)
        self.assertIn("cited", gaps[0] + " ".join(gaps))

    def test_with_all_four_the_finder_recommends_work(self):
        self.apply_owner_terms()
        self.s.policy.amend({"commercial": {
            "market_hourly_cents": {"csv-cleanup": 1_988},
            "market_rate_citations": {
                "csv-cleanup": "TEST-ONLY placeholder, not a real citation"},
        }}, OWNER, "test: a rate, so the gate can be seen to open")
        assessment = self.finder().assess(self.posting(), extension="xlsx")
        self.assertIs(assessment.verdict, Verdict.PURSUE)

    def test_the_margin_the_owner_set_is_the_one_applied(self):
        self.apply_owner_terms()
        floor, why = self.finder().bid_floor_cents(70_000)
        self.assertEqual(floor, 100_000)
        self.assertIn("30%", why)


class TheSetupCommandRecordsThem(Base):
    """Through the command an owner would actually run."""

    def run_cli(self, *argv):
        import contextlib
        import io

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli_main([*argv, "--db", self.db])
        return code, out.getvalue()

    def test_the_documented_command_records_all_three(self):
        self.s.store.close()
        code, out = self.run_cli(
            "setup", "commercial",
            "--minimum-fee-cents", "4000",
            "--minimum-margin-fraction", "0.30",
            "--compute-cents-per-cpu-hour", "14")
        self.assertEqual(code, 0, out)
        self.s = Solvent(self.db)
        _, gaps = self.finder().commercially_configured()
        self.assertEqual(len(gaps), 1, gaps)

    def test_it_refuses_a_zero_the_owner_did_not_mean(self):
        self.s.store.close()
        code, out = self.run_cli("setup", "commercial",
                                 "--minimum-fee-cents", "0")
        self.assertEqual(code, 1)
        self.assertIn("not a floor", out)
        self.s = Solvent(self.db)


class BiddingUnderTheOwnersTerms(Base):

    def test_a_bid_clears_the_owners_thirty_percent(self):
        self.apply_owner_terms()
        self.s.policy.amend({"commercial": {
            "platform_fee_fraction": 0.10,
            "payment_fee_fraction": 0.029,
            "payment_fee_flat_cents": 30,
            "market_hourly_cents": {"csv-cleanup": 1_988},
            "market_rate_citations": {"csv-cleanup": "TEST-ONLY placeholder"},
        }}, OWNER, "test: marketplace fees")
        bid = self.finder().bid(
            self.posting(60_000),
            history=ExecutionHistory({"csv-cleanup": [22.0] * 5}))
        self.assertTrue(bid.submittable, bid.why)
        self.assertGreaterEqual(bid.margin, 0.30)

    def test_a_forty_dollar_job_is_exactly_at_the_floor(self):
        """The owner's $40 minimum and their $40/hour rate are the same
        number on purpose: one hour's work is the smallest job."""
        self.apply_owner_terms()
        self.assertEqual(self.finder().minimum_fee_cents, 4_000)


if __name__ == "__main__":
    unittest.main()
