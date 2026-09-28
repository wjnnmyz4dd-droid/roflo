"""Business metrics: the surface whose whole job is telling the owner the truth.

This module had **no test**, and it is the one an owner reads to decide whether
the business works. It was found by applying two checks derived from K-LEAN's
source — its test-coverage question (which production modules no test imports)
and its complexity thresholds (``business_metrics`` is CCN 16, CRITICAL by
K-LEAN's own cut-off) — and then asking the boundary-condition question its
review checklist asks.

The boundary that mattered: **real revenue and simulated revenue both present.**
Profit and margin were computed on everything collected, so fixture money was
reported as profit under a field called ``actual`` — and the caveat that would
have said so fired only when real revenue was exactly zero. The first real
payment therefore switched the warning *off* while the simulated money stayed
in the total.
"""

from __future__ import annotations

import unittest

from solvent.harness import (CSV_PROMOTION, OWNER, Solvent, provision_capability,
                             run_csv_job)
from solvent.ledger import SIMULATED_PREFIX
from solvent.types import PaymentState

from tests_solvent import fixtures_csv as fx

REAL_RAIL = "stripe_webhook_signed_event"
SIMULATED_RAIL = SIMULATED_PREFIX + "fixture rail"


class Base(unittest.TestCase):
    def setUp(self):
        self.s = Solvent()
        provision_capability(self.s, CSV_PROMOTION, owner_identity=OWNER)

    def job(self, title, cents=10_000):
        source, work = fx.workspace(fx.DUPLICATES)
        return run_csv_job(source=source, requirements=list(fx.SIMPLE),
                           workdir=work, solvent=self.s,
                           client_id=f"client:{title}", title=title,
                           quoted_cents=cents)

    def collect(self, job_id, rail, cents=10_000):
        payment = self.s.ledger.open_payment(job_id=job_id, amount_cents=cents,
                                             rail="stripe")
        self.s.ledger.set_payment_state(payment, PaymentState.PAID,
                                        collected_cents=cents,
                                        verification_method=rail)


class SimulatedMoneyIsNeverProfit(Base):
    def test_mixed_real_and_simulated_does_not_report_simulated_as_profit(self):
        """The reproduction. $100 real beside $100 simulated used to read as
        $200 profit at a 100% margin, with no warning at all."""
        self.collect(self.job("sim").job_id, SIMULATED_RAIL)
        self.collect(self.job("real").job_id, REAL_RAIL)
        metrics = self.s.metrics()
        self.assertEqual(metrics.revenue_collected, "$200.00")
        self.assertEqual(metrics.simulated_revenue_collected, "$100.00")
        self.assertEqual(metrics.real_revenue_collected, "$100.00")
        self.assertEqual(metrics.actual_profit, "$100.00")

    def test_the_caveat_fires_when_any_simulated_money_is_present(self):
        """It used to fire only when real revenue was zero, so the first real
        payment switched off the warning about the fixture money beside it."""
        self.collect(self.job("sim").job_id, SIMULATED_RAIL)
        self.collect(self.job("real").job_id, REAL_RAIL)
        caveats = " ".join(self.s.metrics().caveats)
        self.assertIn("SIMULATED", caveats)
        self.assertIn("$100.00", caveats)

    def test_margin_is_computed_on_real_revenue(self):
        self.collect(self.job("sim").job_id, SIMULATED_RAIL, cents=90_000)
        self.collect(self.job("real").job_id, REAL_RAIL, cents=10_000)
        metrics = self.s.metrics()
        # A margin computed on $100,000 collected would be nine times smaller
        # than the truth, and computed on the right base it is not.
        self.assertEqual(metrics.real_revenue_collected, "$100.00")
        self.assertEqual(metrics.actual_profit, "$100.00")
        self.assertEqual(metrics.actual_margin, 1.0)

    def test_client_concentration_counts_real_clients_only(self):
        """A fixture client is not a client, and concentration is a question
        about which clients the business depends on."""
        self.collect(self.job("sim").job_id, SIMULATED_RAIL)
        self.collect(self.job("real").job_id, REAL_RAIL)
        concentration = self.s.metrics().client_concentration
        self.assertEqual(list(concentration), ["client:real"])

    def test_simulated_only_reports_no_profit_and_says_why(self):
        self.collect(self.job("sim").job_id, SIMULATED_RAIL)
        metrics = self.s.metrics()
        self.assertEqual(metrics.real_revenue_collected, "$0.00")
        self.assertEqual(metrics.actual_margin, 0.0)
        caveats = " ".join(metrics.caveats)
        self.assertIn("All collected revenue is SIMULATED", caveats)

    def test_real_only_carries_no_simulated_caveat(self):
        """The warning must not cry wolf, or it stops being read."""
        self.collect(self.job("real").job_id, REAL_RAIL)
        metrics = self.s.metrics()
        self.assertEqual(metrics.simulated_revenue_collected, "$0.00")
        self.assertNotIn("SIMULATED", " ".join(metrics.caveats))

    def test_the_headline_reports_real_revenue(self):
        self.collect(self.job("sim").job_id, SIMULATED_RAIL)
        self.assertIn("$0.00", self.s.metrics().headline)


class AFreshBusinessReportsNothing(Base):
    def test_everything_is_zero_and_nothing_is_invented(self):
        metrics = self.s.metrics()
        for field in ("revenue_invoiced", "revenue_collected",
                      "simulated_revenue_collected", "real_revenue_collected",
                      "actual_profit"):
            with self.subTest(field=field):
                self.assertEqual(getattr(metrics, field), "$0.00")
        self.assertEqual(metrics.actual_margin, 0.0)
        self.assertEqual(metrics.client_concentration, {})

    def test_an_uncalibrated_estimator_says_so(self):
        self.assertIn("uncalibrated", " ".join(self.s.metrics().caveats))

    def test_it_is_reachable_the_way_the_owner_reaches_it(self):
        """Through Solvent.metrics(), which is what `solvent metrics` and
        `solvent status` call."""
        self.assertTrue(self.s.metrics().to_dict())


class ItReadsAndDecidesNothing(Base):
    def test_computing_metrics_changes_nothing(self):
        before = {
            "policy": self.s.policy.version,
            "ledger": len(self.s.store.raw_readonly("SELECT * FROM ledger_entries")),
            "payments": len(self.s.store.raw_readonly("SELECT * FROM payments")),
            "audit": len(self.s.audit.events()),
        }
        self.s.metrics()
        self.s.metrics()
        self.assertEqual({
            "policy": self.s.policy.version,
            "ledger": len(self.s.store.raw_readonly("SELECT * FROM ledger_entries")),
            "payments": len(self.s.store.raw_readonly("SELECT * FROM payments")),
            "audit": len(self.s.audit.events()),
        }, before)

    def test_it_owns_no_table(self):
        from solvent.store import TABLE_OWNER

        self.assertNotIn("metrics", set(TABLE_OWNER.values()))
