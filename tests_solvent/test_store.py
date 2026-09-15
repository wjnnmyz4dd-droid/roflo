"""Storage-level enforcement: table ownership and append-only records.

These tests assert the *database* refuses, not that the application remembers to.
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from solvent.errors import AuthorityError, ImmutableRecord
from solvent.store import APPEND_ONLY, TABLE_OWNER, Store


class TableOwnership(unittest.TestCase):
    def setUp(self):
        self.store = Store()

    def test_every_table_has_exactly_one_owner(self):
        """The one-authority law, stated as data and checked as a test."""
        self.assertEqual(len(TABLE_OWNER), len(set(TABLE_OWNER)))
        for table, owner in TABLE_OWNER.items():
            self.assertTrue(owner, f"{table} has no owning authority")

    def test_authority_cannot_write_another_authoritys_table(self):
        ledger = self.store.for_authority("ledger")
        with self.assertRaises(AuthorityError) as ctx:
            ledger.execute("INSERT INTO policy_current(id,version) VALUES(1,1)")
        self.assertIn("owned by 'policy'", str(ctx.exception))

    def test_authority_may_write_its_own_table(self):
        ledger = self.store.for_authority("ledger")
        ledger.execute(
            "INSERT INTO payments(id,job_id,state,amount_cents,rail,updated_at) "
            "VALUES('p1','j1','ESTIMATED',100,'test','now')")
        self.assertEqual(len(ledger.query("SELECT * FROM payments")), 1)

    def test_reads_are_unrestricted_writes_are_not(self):
        """Every authority may read; that is not the boundary being defended."""
        gate = self.store.for_authority("gate")
        self.assertEqual(gate.query("SELECT * FROM payments"), [])
        with self.assertRaises(AuthorityError):
            gate.execute("INSERT INTO payments(id,job_id,state,amount_cents,rail,"
                         "updated_at) VALUES('p','j','ESTIMATED',1,'r','t')")

    def test_unknown_authority_rejected(self):
        with self.assertRaises(AuthorityError):
            self.store.for_authority("shadow_governor")


class Immutability(unittest.TestCase):
    def setUp(self):
        # File-backed on purpose: the point of these tests is that a *second,
        # independent* connection is also refused. ":memory:" would give each
        # connection its own empty database and prove nothing.
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = Store(Path(self._tmp.name) / "solvent.db")
        self.audit = self.store.for_authority("audit")
        self.audit.execute(
            "INSERT INTO audit_log(ts,event,authority,initiator,why,payload,prev_hash,"
            "hash) VALUES('t','e','audit','me','why','{}','0','h')")
        self.audit.commit()  # release the write lock so a second connection can open

    def test_update_and_delete_are_refused(self):
        for sql in ("UPDATE audit_log SET event='tampered'", "DELETE FROM audit_log"):
            with self.subTest(sql=sql), self.assertRaises(ImmutableRecord):
                self.audit.execute(sql)

    def test_immutability_survives_bypassing_the_application_layer(self):
        """The guarantee is in the database, so raw SQL cannot undo it.

        This is the test that distinguishes an enforced control from a convention.
        """
        raw = sqlite3.connect(self.store.path)
        raw.execute("INSERT INTO audit_log(ts,event,authority,initiator,why,payload,"
                    "prev_hash,hash) VALUES('t','e2','a','i','w','{}','h','h2')")
        for sql in ("UPDATE audit_log SET event='x'", "DELETE FROM audit_log"):
            with self.subTest(sql=sql), self.assertRaises(sqlite3.IntegrityError):
                raw.execute(sql)
        raw.close()

    def test_all_declared_append_only_tables_have_triggers(self):
        names = {r["name"] for r in self.store.raw_readonly(
            "SELECT name FROM sqlite_master WHERE type='trigger'")}
        for table in APPEND_ONLY:
            for op in ("update", "delete"):
                self.assertIn(f"{table}_no_{op}", names)


if __name__ == "__main__":
    unittest.main()
