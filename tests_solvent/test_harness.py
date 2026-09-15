"""The P0 proof: one complete job, with every gate actually exercised."""

import unittest

from solvent.harness import run_first_job
from solvent.types import JobState


class CompleteJob(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = run_first_job()

    def test_the_job_reaches_completion(self):
        self.assertEqual(self.report.final_state, JobState.COMPLETE.value)

    def test_the_audit_chain_is_intact_end_to_end(self):
        self.assertIn("intact", self.report.audit_chain)

    def test_every_gate_was_exercised_not_stepped_around(self):
        exercised = " ".join(self.report.gates_exercised)
        for expected in ("injection", "location", "owner approval", "egress",
                         "verification", "payment"):
            with self.subTest(gate=expected):
                self.assertIn(expected, exercised)

    def test_injected_instructions_could_not_establish_conformance(self):
        self.assertIn("UNKNOWN", " ".join(self.report.gates_exercised))

    def test_an_unpriced_jurisdiction_blocked_a_binding_quote(self):
        self.assertIn("PRICING_LOCATION_UNKNOWN",
                      " ".join(self.report.gates_exercised))

    def test_an_unallowlisted_destination_was_refused_by_the_allowlist(self):
        """Refused for the right reason, not incidentally by an earlier check."""
        egress = [g for g in self.report.gates_exercised if g.startswith("egress")]
        self.assertTrue(egress)
        self.assertIn("allowlist", egress[0])

    def test_delivery_was_refused_before_verification_evidence_existed(self):
        self.assertIn("delivery refused", " ".join(self.report.gates_exercised))

    def test_invoiced_contributed_nothing_to_revenue(self):
        self.assertIn("INVOICED contributes $0.00",
                      " ".join(self.report.gates_exercised))


class SimulationIsNeverRevenue(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = run_first_job()

    def test_the_run_is_marked_simulated(self):
        self.assertTrue(self.report.simulated)

    def test_real_revenue_is_zero_however_large_the_fixture_profit(self):
        """The whole point: fixture money must never be reportable as earnings."""
        self.assertGreater(self.report.profit["actual_profit_cents"], 0)
        self.assertEqual(self.report.real_revenue, "$0.00")

    def test_the_report_says_so_in_words(self):
        self.assertTrue(any("no figure here is revenue" in w.lower()
                            for w in self.report.warnings))


class LocationChangesThePrice(unittest.TestCase):
    def test_the_same_job_prices_differently_in_three_states(self):
        estimates = {}
        for state in ("CA", "CT", "NC"):
            report = run_first_job(state=state)
            line = [s for s in report.steps if s.startswith("estimate:")][0]
            estimates[state] = line
        self.assertEqual(len(set(estimates.values())), 3, estimates)

    def test_the_jurisdiction_used_is_recorded_in_the_estimate(self):
        report = run_first_job(state="NC")
        estimate_step = [s for s in report.steps if s.startswith("estimate:")][0]
        self.assertIn("US>NC", estimate_step)


if __name__ == "__main__":
    unittest.main()
