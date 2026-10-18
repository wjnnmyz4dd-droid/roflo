"""``grant_for``: did the Governor authorise this job's work, and is it still?

Recovery uses this to tell an interrupted job from one that never started.
Getting it wrong in the permissive direction restarts work nobody budgeted,
which is the one thing the Governor exists to prevent.
"""

from __future__ import annotations

import pathlib
import tempfile
import unittest

from solvent.harness import Solvent


class GrantLookup(unittest.TestCase):

    def setUp(self):
        self.box = tempfile.TemporaryDirectory()
        self.addCleanup(self.box.cleanup)
        self.s = Solvent(str(pathlib.Path(self.box.name) / "g.db"))
        self.addCleanup(self.close)

    def close(self):
        try:
            self.s.store.close()
        except Exception:
            pass

    def test_an_unfunded_job_has_no_grant(self):
        self.assertEqual(self.s.governor.grant_for("job_nothing"), "")

    def test_a_funded_job_reports_its_grant(self):
        grant = self.s.governor.grant_budget(job_id="job_a", decision_id="d",
                                             authorized_cents=1_000)
        self.assertEqual(self.s.governor.grant_for("job_a"), grant)

    def test_one_job_s_grant_is_not_another_s(self):
        self.s.governor.grant_budget(job_id="job_a", decision_id="d",
                                     authorized_cents=1_000)
        self.assertEqual(self.s.governor.grant_for("job_b"), "")

    def test_the_newest_grant_wins(self):
        self.s.governor.grant_budget(job_id="job_a", decision_id="d1",
                                     authorized_cents=1_000)
        second = self.s.governor.grant_budget(job_id="job_a", decision_id="d2",
                                              authorized_cents=2_000)
        self.assertEqual(self.s.governor.grant_for("job_a"), second)

    def test_a_revoked_grant_is_not_authorisation(self):
        """Nothing sets ``revoked`` yet, so this pins the guard rather than
        a behaviour: the day revocation is wired, withdrawn funding must
        not read as permission to restart the work after a crash."""
        grant = self.s.governor.grant_budget(job_id="job_a", decision_id="d",
                                             authorized_cents=1_000)
        connection = self.s.store.for_authority("governor")
        connection.execute("UPDATE budget_grants SET revoked = 1 WHERE id = ?",
                           (grant,))
        connection.commit()
        self.assertEqual(self.s.governor.grant_for("job_a"), "",
                         "a revoked grant still reads as authorisation")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
