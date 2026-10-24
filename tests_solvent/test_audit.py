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


class TheTailCheck(Chain):
    """Verifying only what is new, and what that does and does not catch.

    The full derivation is linear in the age of the installation --
    12ms at a thousand rows, 719ms at fifty thousand -- and
    ``runtime.health`` asked for it on every call, with four
    scheduled tasks calling ``health``. A shop was charged for
    staying in business. These pin the replacement's behaviour,
    including the part of it that is a genuine reduction in cover.
    """

    def rows(self, n: int) -> None:
        for i in range(n):
            self.log.record(event=f"e{i}", authority="orchestrator",
                            initiator="test", why="because", job_id="J1")

    def tamper(self, seq: int) -> None:
        raw = sqlite3.connect(self.store.path)
        raw.execute("PRAGMA writable_schema=ON")
        raw.execute("DROP TRIGGER audit_log_no_update")
        raw.commit()
        raw.execute("UPDATE audit_log SET why='rewritten' WHERE seq=?", (seq,))
        raw.commit()
        raw.close()

    def test_the_first_call_in_a_process_derives_everything(self):
        """So a log tampered with before startup is caught at startup."""
        self.rows(5)
        self.tamper(2)

        ok, detail = AuditLog(self.store).verify_tail()

        self.assertFalse(ok, "a fresh process must re-derive the whole chain")
        self.assertIn("hash mismatch", detail)

    def test_a_tampered_tail_is_caught(self):
        self.rows(5)
        self.assertTrue(self.log.verify_tail()[0])
        self.rows(3)
        self.tamper(7)

        ok, detail = self.log.verify_tail()

        self.assertFalse(ok)
        self.assertIn("hash mismatch", detail)

    def test_rows_disappearing_is_caught(self):
        self.rows(5)
        self.assertTrue(self.log.verify_tail()[0])
        raw = sqlite3.connect(self.store.path)
        raw.execute("PRAGMA writable_schema=ON")
        raw.execute("DROP TRIGGER audit_log_no_delete")
        raw.commit()
        raw.execute("DELETE FROM audit_log WHERE seq > 3")
        raw.commit()
        raw.close()

        ok, detail = self.log.verify_tail()

        self.assertFalse(ok)
        self.assertIn("no longer in the log", detail)

    def test_what_it_does_not_catch_is_said_out_loud(self):
        """An edit to an already-verified row, within one process.

        Asserted rather than left in prose, because this is the cost
        of the change and a reader is owed it as a fact. The full
        check still catches it, which is why ``diagnostics`` keeps
        running one.
        """
        self.rows(5)
        self.assertTrue(self.log.verify_tail()[0])
        self.tamper(2)

        self.assertTrue(self.log.verify_tail()[0],
                        "the tail check is not expected to catch this")
        self.assertFalse(self.log.verify_chain()[0],
                         "the full check must still catch it")

    def test_it_does_not_get_slower_as_the_log_grows(self):
        """The whole point, measured rather than asserted from the code.

        Compares work done, not seconds: a wall-clock assertion on a
        shared machine fails for reasons that have nothing to do with
        this. The tail check reads a bounded number of rows however
        long the log is.
        """
        self.rows(50)
        self.log.verify_tail()
        reads = []
        inner = self.log._db.query

        def counted(sql, params=()):
            rows = inner(sql, params)
            if "FROM audit_log" in sql and "MAX" not in sql:
                reads.append(len(rows))
            return rows

        self.log._db.query = counted
        self.log.verify_tail()
        after_none = sum(reads)
        self.rows(3)
        reads.clear()
        self.log.verify_tail()

        self.assertEqual(after_none, 0, "it re-read rows it had checked")
        self.assertEqual(sum(reads), 3, "it read more than the new rows")


class TheNarrowReaders(Chain):
    """Asking the database for the rows wanted, not for all of them."""

    def some(self) -> None:
        for name in ("alpha", "beta", "alpha", "gamma", "beta", "alpha"):
            self.log.record(event=name, authority="x", initiator="t", why="w")

    def test_named_returns_only_those_events_oldest_first(self):
        self.some()

        got = self.log.named(("alpha", "gamma"))

        self.assertEqual([r["event"] for r in got],
                         ["alpha", "alpha", "gamma", "alpha"])

    def test_latest_returns_the_newest_first(self):
        self.some()

        got = self.log.latest(("alpha", "beta"), limit=2)

        self.assertEqual([r["event"] for r in got], ["alpha", "beta"])

    def test_count_counts_in_the_database(self):
        self.some()

        self.assertEqual(self.log.count(), 6)
        self.assertEqual(self.log.count(("alpha",)), 3)
        self.assertEqual(self.log.count(("nothing-like-this",)), 0)

    def test_an_empty_ask_is_an_empty_answer(self):
        """Pinned, because the guard that makes it true looks removable.

        A mutant that deleted the early return survived: SQLite reads
        a generated ``IN ()`` as matching nothing, so the guard
        protects nothing *here*. It stays because it says what the
        method means and because another database reads ``IN ()`` as
        a syntax error -- but the mutant was withdrawn rather than
        left as a claim the guard was load-bearing when it is not.
        """
        self.some()

        self.assertEqual(self.log.named(()), [])
        self.assertEqual(self.log.latest(()), [])
        self.assertEqual(self.log.count(()), 0)

    def test_a_negative_limit_does_not_mean_no_limit(self):
        """SQLite reads ``LIMIT -1`` as unbounded. That guard is real."""
        self.some()

        self.assertEqual(self.log.latest(("alpha",), limit=-1), [])
        self.assertEqual(len(self.log.latest(("alpha",), limit=2)), 2)

    def test_top_seq_is_the_highest_row(self):
        self.assertEqual(self.log.top_seq(), 0)
        self.some()
        self.assertEqual(self.log.top_seq(), 6)


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
