"""`solvent setup probation` — granting and ending it, visibly.

The command exists because the obvious way to end probation does not
work. Policy patches deep-merge, so amending an empty capabilities
map merges nothing into the existing one: the owner reads "policy
amended" and the capability is still on probation. A control that
lets DeskPilot quote work it has never done is the wrong one to have
a silent no-op in.
"""

from __future__ import annotations

import contextlib
import io
import tempfile
import unittest

from solvent.cli import main
from solvent.errors import FailClosed
from solvent.finder import Finder
from solvent.harness import OWNER, Solvent


class Base(unittest.TestCase):

    def setUp(self):
        self.box = tempfile.TemporaryDirectory()
        self.addCleanup(self.box.cleanup)
        self.db = f"{self.box.name}/s.db"

    def run_cli(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(["setup", "probation", "--db", self.db, *args])
        return code, out.getvalue()

    def solvent(self):
        s = Solvent(self.db)
        self.addCleanup(lambda: s.store.close())
        return s

    def grant_of(self, capability="csv-cleanup"):
        s = self.solvent()
        finder = Finder(s.discovery, s.policy, s.capability, s.orchestrator,
                        s.store)
        return finder.probation(capability)


class GrantingIsBounded(Base):

    def test_a_grant_is_recorded(self):
        code, said = self.run_cli("--capability", "csv-cleanup",
                                  "--max-jobs", "2",
                                  "--max-value-cents", "7500",
                                  "--min-deadline-hours", "48")
        self.assertEqual(code, 0)
        self.assertIn("may not send them", said)
        self.assertTrue(self.grant_of().granted)

    def test_a_grant_without_a_value_ceiling_is_refused(self):
        code, said = self.run_cli("--capability", "csv-cleanup",
                                  "--max-jobs", "2",
                                  "--min-deadline-hours", "48")
        self.assertEqual(code, 1)
        self.assertIn("value ceiling", said)

    def test_a_grant_without_a_delivery_window_is_refused(self):
        code, said = self.run_cli("--capability", "csv-cleanup",
                                  "--max-jobs", "2",
                                  "--max-value-cents", "7500")
        self.assertEqual(code, 1)
        self.assertIn("delivery window", said)

    def test_a_grant_of_zero_jobs_is_refused(self):
        code, said = self.run_cli("--capability", "csv-cleanup",
                                  "--max-jobs", "0",
                                  "--max-value-cents", "7500",
                                  "--min-deadline-hours", "48")
        self.assertEqual(code, 1)
        self.assertIn("revoke_probation", said)

    def test_nothing_is_on_probation_to_begin_with(self):
        code, said = self.run_cli()
        self.assertEqual(code, 0)
        self.assertIn("no capability is on probation", said)

    def test_what_is_granted_can_be_listed(self):
        self.run_cli("--capability", "csv-cleanup", "--max-jobs", "2",
                     "--max-value-cents", "7500",
                     "--min-deadline-hours", "48")
        _, said = self.run_cli()
        self.assertIn("csv-cleanup", said)
        self.assertIn("$75.00", said)


class RevokingSaysSoInTheRecord(Base):

    def granted(self):
        self.run_cli("--capability", "csv-cleanup", "--max-jobs", "2",
                     "--max-value-cents", "7500",
                     "--min-deadline-hours", "48")

    def test_a_grant_can_be_ended(self):
        self.granted()
        code, said = self.run_cli("--revoke", "csv-cleanup")
        self.assertEqual(code, 0)
        self.assertFalse(self.grant_of().granted)

    def test_the_refusal_afterwards_says_revoked(self):
        self.granted()
        self.run_cli("--revoke", "csv-cleanup")
        self.assertIn("revoked", self.grant_of().why)

    def test_a_revoked_grant_is_listed_as_revoked_not_absent(self):
        self.granted()
        self.run_cli("--revoke", "csv-cleanup")
        _, said = self.run_cli()
        self.assertIn("revoked", said)

    def test_revoking_what_was_never_granted_is_refused(self):
        code, said = self.run_cli("--revoke", "csv-cleanup")
        self.assertEqual(code, 1)
        self.assertIn("nothing", said)

    def test_revoking_needs_a_capability(self):
        s = self.solvent()
        with self.assertRaises(FailClosed):
            s.policy.revoke_probation(owner_identity=OWNER, capability="")


if __name__ == "__main__":
    unittest.main()
