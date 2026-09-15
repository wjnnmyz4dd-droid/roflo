"""Business Memory learns only from evidence; Project State stays a projection."""

import unittest

from solvent.errors import AuthorityError, FailClosed
from solvent.harness import Solvent
from solvent.memory import BusinessMemory
from solvent.types import VerificationTier
from tests_solvent.fixtures import Rig


class VerifyBeforeLearn(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()
        self.memory = BusinessMemory(self.rig.store, self.rig.audit)

    def test_self_report_is_never_evidence(self):
        """'The agent said it finished' is not a finding."""
        with self.assertRaises(FailClosed):
            self.memory.learn(kind="job_outcome", subject="C1", payload={},
                              evidence_ref="e1",
                              tier=VerificationTier.T0_SELF_REPORT)

    def test_economic_claims_require_an_external_fact(self):
        """A passing test proves the file parses, not that the job made money."""
        for tier in (VerificationTier.T1_DETERMINISTIC,
                     VerificationTier.T2_INDEPENDENT_REVIEW):
            with self.subTest(tier=tier), self.assertRaises(FailClosed):
                self.memory.learn(kind="estimate_accuracy", subject="C1",
                                  payload={"ratio": 1.1}, evidence_ref="e1", tier=tier)
        self.assertTrue(self.memory.learn(
            kind="estimate_accuracy", subject="C1", payload={"ratio": 1.1},
            evidence_ref="e1", tier=VerificationTier.T3_EXTERNAL_FACT))

    def test_technical_learning_accepts_a_deterministic_check(self):
        self.assertTrue(self.memory.learn(
            kind="tool_performance", subject="xlsx", payload={"ok": True},
            evidence_ref="e1", tier=VerificationTier.T1_DETERMINISTIC))

    def test_a_lesson_must_cite_its_evidence(self):
        with self.assertRaises(FailClosed):
            self.memory.learn(kind="tool_performance", subject="x", payload={},
                              evidence_ref="", tier=VerificationTier.T1_DETERMINISTIC)

    def test_memory_has_no_write_path_to_money_or_evidence(self):
        """Not policed at runtime — the connection physically cannot."""
        for table, values in (
            ("ledger_entries", "'x','t','j','AI_API',1,'COST','RECEIPT',''"),
            ("audit_log", "1,2,3,4,5,6,7,8"),
            ("policy_current", "1,1"),
            ("governor_decisions", "1,2,3,4,5,6,7,8,9,10,11,12"),
        ):
            with self.subTest(table=table), self.assertRaises(AuthorityError):
                self.memory._db.execute(f"INSERT INTO {table} VALUES({values})")


class ProjectionStaysAProjection(unittest.TestCase):
    def setUp(self):
        self.solvent = Solvent()

    def test_projection_stores_nothing(self):
        """It is rebuilt from the authorities on every call."""
        from solvent.store import TABLE_OWNER
        self.assertNotIn("projection", TABLE_OWNER.values())
        self.assertFalse(any("projection" in t for t in TABLE_OWNER))

    def test_projection_names_the_authority_behind_each_field(self):
        job_id = self.solvent.orchestrator.intake(
            title="T", client_id="C1", quoted_cents=1000,
            signals=__import__("solvent.types", fromlist=["LocationSignals"])
            .LocationSignals(), initiator="owner:test")
        view = self.solvent.project(job_id)
        self.assertEqual(view.sources["state"], "orchestrator")
        self.assertEqual(view.sources["money"], "ledger")
        self.assertEqual(view.sources["evidence"], "audit")

    def test_projection_reflects_authoritative_state_not_its_own(self):
        from solvent.types import BlockedOn, LocationSignals
        job_id = self.solvent.orchestrator.intake(
            title="T", client_id="C1", quoted_cents=1000,
            signals=LocationSignals(), initiator="owner:test")
        self.solvent.orchestrator.block(job_id=job_id, blocked_on=BlockedOn.CLIENT,
                                        why="waiting on files", initiator="x")
        view = self.solvent.project(job_id)
        self.assertEqual(view.state, "BLOCKED")
        self.assertEqual(view.blocked_on, "CLIENT")


if __name__ == "__main__":
    unittest.main()
