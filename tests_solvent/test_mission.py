"""The mission, end to end: find good work, refuse the rest, do one job, get paid."""

import unittest

from solvent.harness import run_acquisition_cycle, run_first_job
from solvent.qualification import RejectReason
from solvent.types import JobState


class AcquisitionCycle(unittest.TestCase):
    """Discovery finds a board of work; most of it is correctly refused."""

    @classmethod
    def setUpClass(cls):
        cls.report = run_acquisition_cycle()

    def test_multiple_opportunities_are_discovered(self):
        self.assertGreater(self.report.discovered, 1)

    def test_most_of_the_board_is_refused(self):
        """Solvent optimises for good jobs, not for the number of jobs."""
        selected = len(self.report.selected)
        self.assertLess(selected, self.report.discovered)

    def test_each_refusal_has_a_distinct_recorded_reason(self):
        reasons = self.report.metrics["rejected_by_reason"]
        for expected in (RejectReason.BELOW_MINIMUM_VALUE.value,
                         RejectReason.MISSING_CAPABILITY.value,
                         RejectReason.EXCESSIVE_RISK.value,
                         RejectReason.BETTER_OPPORTUNITY_AVAILABLE.value):
            with self.subTest(reason=expected):
                self.assertIn(expected, reasons)

    def test_margin_is_no_longer_what_rejects_small_jobs(self):
        """A consequence of costing honestly, recorded rather than hidden.

        MARGIN_TOO_LOW used to appear on this board because every job was
        costed at two hours of human labour, so a $150 job "cost" more than it
        paid. DeskPilot's real costs are a marketplace cut, a card fee and some
        compute, and against those almost any job clears a 30% floor. The
        margin check has not stopped working -- it stops a job whose fees
        genuinely eat it -- but it is no longer the control that keeps DeskPilot
        away from work not worth doing.

        That job belongs to the **minimum project fee**, which is a different
        question: not "does this pay more than it costs" but "is this worth the
        overhead of taking at all". The two were conflated for as long as the
        cost model was wrong.
        """
        reasons = self.report.metrics["rejected_by_reason"]
        self.assertNotIn(RejectReason.MARGIN_TOO_LOW.value, reasons,
                         "a job on this board is margin-negative against "
                         "compute and fees, which would be a surprise worth "
                         "looking at")
        self.assertIn(RejectReason.BELOW_MINIMUM_VALUE.value, reasons,
                      "nothing was turned away for being too small, so the "
                      "floor that replaced margin is not doing its job")

    def test_a_job_needing_an_unheld_capability_is_refused(self):
        self.assertTrue(any("MISSING_CAPABILITY" in d for d in self.report.decisions))

    def test_a_job_with_no_location_is_now_priceable(self):
        """OD-10's other half, and the same cause.

        A posting that would not say where the work happened could not be
        priced, because the price was a human wage and wages are geographic.
        Compute and card fees are not, so the client refusing to state a
        location no longer stops a quote -- and, in the other direction, no
        longer lets them move DeskPilot's cost by misstating one.
        """
        vague = [d for d in self.report.decisions if d.startswith("A-5")]
        self.assertTrue(vague, "the location-unstated posting vanished")
        self.assertNotIn("UNSUPPORTED_JURISDICTION", vague[0])
        self.assertIn("ACCEPT", vague[0])

    def test_a_high_value_job_waits_for_the_owner(self):
        self.assertTrue(any("NEEDS_OWNER_APPROVAL" in d
                            for d in self.report.decisions))

    def test_work_is_ranked_best_first(self):
        self.assertTrue(self.report.ranked)
        self.assertTrue(self.report.ranked[0].startswith("#1"))

    def test_opportunity_cost_defers_weaker_work(self):
        self.assertTrue(self.report.deferred)
        self.assertTrue(any("better-scoring" in d for d in self.report.deferred))

    def test_the_headline_metric_is_verified_profitable_work(self):
        self.assertIn("verified profitable client work",
                      self.report.metrics["headline"])

    def test_nothing_in_the_cycle_is_presented_as_real(self):
        self.assertTrue(any("invented fixture" in w for w in self.report.warnings))


class DeliveryLoopStillWorks(unittest.TestCase):
    """The manual first-job proof is preserved: acquisition did not break it."""

    @classmethod
    def setUpClass(cls):
        cls.report = run_first_job()

    def test_the_job_completes(self):
        self.assertEqual(self.report.final_state, JobState.COMPLETE.value)

    def test_the_audit_chain_is_intact(self):
        self.assertIn("intact", self.report.audit_chain)

    def test_simulated_money_is_still_not_revenue(self):
        self.assertEqual(self.report.real_revenue, "$0.00")


class MissionIsStatedWhereItCannotBeMissed(unittest.TestCase):
    def test_the_package_docstring_leads_with_the_business_mission(self):
        """A future reader must not mistake Solvent for a generic adaptive AI."""
        import solvent
        doc = solvent.__doc__ or ""
        head = doc[:400]
        self.assertIn("find, qualify, complete, deliver and profit", head)
        self.assertIn("not", doc)
        self.assertIn("generic adaptive AI", doc)

    def test_a_canonical_mission_document_exists_and_leads_with_the_mission(self):
        import pathlib
        mission = pathlib.Path("SOLVENT.md")
        self.assertTrue(mission.exists(), "SOLVENT.md is the canonical mission doc")
        head = mission.read_text()[:600].upper()
        self.assertIn("FIND", head)
        self.assertIn("PROFIT", head)


if __name__ == "__main__":
    unittest.main()
