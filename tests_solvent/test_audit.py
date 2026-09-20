"""Audit Log: tamper evidence and the rules that make verification real."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from solvent.audit import AuditLog
from solvent.errors import FailClosed
from solvent.store import Store
from solvent.types import ConsequenceTier, VerificationTier

from tests_solvent import fixtures as fx


class Chain(unittest.TestCase):
    def setUp(self):
        # File-backed: the tamper test must reach the same database from a
        # second connection, which ":memory:" would not allow.
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = Store(Path(self._tmp.name) / "solvent.db")
        self.log = AuditLog(self.store)

    def test_chain_verifies_when_intact(self):
        for i in range(5):
            self.log.record(event=f"e{i}", authority="orchestrator",
                            initiator="test", why="because", job_id="J1")
        ok, detail = self.log.verify_chain()
        self.assertTrue(ok, detail)

    def test_tampering_is_detectable_even_via_raw_sql(self):
        """Triggers stop edits; the hash chain catches anything that gets past them."""
        self.log.record(event="a", authority="x", initiator="t", why="w")
        self.log.record(event="b", authority="x", initiator="t", why="w")
        raw = sqlite3.connect(self.store.path)
        raw.execute("PRAGMA writable_schema=ON")
        raw.execute("DROP TRIGGER audit_log_no_update")
        raw.commit()
        raw.execute("UPDATE audit_log SET why='rewritten' WHERE seq=1")
        raw.commit()
        raw.close()
        ok, detail = self.log.verify_chain()
        self.assertFalse(ok)
        self.assertIn("hash mismatch", detail)


class VerificationRules(unittest.TestCase):
    def setUp(self):
        # A genuinely certified verifier, earned against the real battery. These
        # tests are about the controls *underneath* the certification gate, so
        # they have to get past it — with real evidence, not a permissive stub.
        self.log, self.store = fx.certified_audit()
        self.certified = fx.certified_evidence_fields()

    def test_self_attestation_refused_for_high_consequence(self):
        """The component that benefits from claiming success cannot be its own witness."""
        with self.assertRaises(FailClosed):
            self.log.record_verification(
            requirement_id="r-legacy", artifact_digest="d" * 64,
                subject_ref="J1", tier=VerificationTier.T1_DETERMINISTIC,
                method="ran tests", executor_identity="worker",
                verifier_identity="worker", verdict=True, raw_output="ok",
                consequence=ConsequenceTier.C_HIGH, **self.certified)

    def test_self_attestation_allowed_for_low_consequence(self):
        """Bounded downside is why cheap work does not need an independent verifier."""
        evidence = self.log.record_verification(
            requirement_id="r-legacy", artifact_digest="d" * 64,
            subject_ref="J2", tier=VerificationTier.T1_DETERMINISTIC, method="schema",
            executor_identity="worker", verifier_identity="worker", verdict=True,
            raw_output="valid", consequence=ConsequenceTier.C_LOW, **self.certified)
        self.assertTrue(evidence)

    def test_t0_self_report_is_never_verification_above_low(self):
        with self.assertRaises(FailClosed):
            self.log.record_verification(
            requirement_id="r-legacy", artifact_digest="d" * 64,
                subject_ref="J3", tier=VerificationTier.T0_SELF_REPORT,
                method="agent said done", executor_identity="w", verifier_identity="v",
                verdict=True, raw_output="done", consequence=ConsequenceTier.C_MED)

    def test_t0_never_feeds_learning_and_only_t3_feeds_economics(self):
        self.assertFalse(VerificationTier.T0_SELF_REPORT.may_feed_learning)
        self.assertTrue(VerificationTier.T1_DETERMINISTIC.may_feed_learning)
        self.assertFalse(VerificationTier.T1_DETERMINISTIC.may_feed_economic_learning)
        self.assertTrue(VerificationTier.T3_EXTERNAL_FACT.may_feed_economic_learning)

    def test_anonymous_verifier_refused(self):
        with self.assertRaises(FailClosed):
            self.log.record_verification(
            requirement_id="r-legacy", artifact_digest="d" * 64,
                subject_ref="J4", tier=VerificationTier.T1_DETERMINISTIC, method="m",
                executor_identity="w", verifier_identity="", verdict=True,
                raw_output="", consequence=ConsequenceTier.C_LOW, **self.certified)

    def test_best_tier_reports_highest_passing_evidence(self):
        for tier in (VerificationTier.T1_DETERMINISTIC, VerificationTier.T3_EXTERNAL_FACT):
            self.log.record_verification(
            requirement_id="r-legacy", artifact_digest="d" * 64,
                subject_ref="J5", tier=tier, method="m", executor_identity="w",
                verifier_identity="v", verdict=True, raw_output="",
                consequence=ConsequenceTier.C_LOW, **self.certified)
        self.assertIs(self.log.best_tier("J5"), VerificationTier.T3_EXTERNAL_FACT)


if __name__ == "__main__":
    unittest.main()
