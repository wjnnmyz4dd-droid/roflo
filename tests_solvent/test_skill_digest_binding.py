"""§39. An approval authorises an artifact, not a name.

The owner is not technical QC. They are shown evidence about a specific piece
of code and they authorise *that*. So the thing their approval travels with has
to be the thing that was tested -- otherwise "approved pdf-extract/1.0" means
"approved whatever is behind that number when the promotion runs", including
code written after the certification they were shown.

This was a real gap: record_promotion took a project and an owner and no
artifact digest at all, so nothing compared the two.
"""

from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from solvent import skillslab as lab
from solvent.capability import Capability
from solvent.errors import FailClosed
from solvent.harness import OWNER, Solvent
from test_skills_lab import verified_project

CERTIFIED = "sha256:0000certified"
TAMPERED = "sha256:9999different"


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.s = Solvent(str(pathlib.Path(self.dir.name) / "solvent.db"))
        self.addCleanup(self.close)
        self.covers = frozenset({"extract_pdf_text"})
        self.project = self.offered(CERTIFIED)

    def close(self):
        try:
            self.s.store.close()
        except Exception:
            pass

    def offered(self, fingerprint: str) -> str:
        project_id = verified_project(self.s, skill="pdf-extract",
                                      covers=self.covers)
        self.s.skillslab.mark_specified(project_id=project_id,
                                        target_covers=self.covers,
                                        target_version="pdf-extract/1.0")
        self.s.skillslab.mark_built(project_id=project_id,
                                    fingerprint=fingerprint)
        self.s.skillslab.record_certification(
            project_id=project_id, state="CERTIFIED",
            certified_checks=self.covers, fingerprint=fingerprint)
        self.s.skillslab.offer_to_owner(project_id)
        self.s.capability.register(
            Capability(name="pdf-extract", covers=self.covers, proven=True,
                       version="pdf-extract/1.0",
                       verifiable_by=tuple(self.covers),
                       proven_levels=("L1", "L2", "L3"), fixtures_passed=25,
                       fixtures_total=25, false_completions=0,
                       evidence_ref="tests_solvent/test_skill_digest_binding.py"),
            owner_identity=OWNER)
        return project_id


class ApprovalBindsToTheArtifact(Base):

    def test_the_certified_artifact_may_be_promoted(self):
        """The positive canary. Without it every refusal below could hold on a
        project that was never promotable for some unrelated reason."""
        self.s.skillslab.record_promotion(
            project_id=self.project, owner_identity=OWNER,
            why="the owner approved what they were shown",
            fingerprint=CERTIFIED)
        self.assertEqual(self.s.skillslab.project(self.project).stage,
                         lab.PROMOTED)

    def test_a_different_artifact_may_not(self):
        with self.assertRaises(FailClosed) as caught:
            self.s.skillslab.record_promotion(
                project_id=self.project, owner_identity=OWNER,
                why="promoting code the owner never saw",
                fingerprint=TAMPERED)
        message = str(caught.exception)
        self.assertIn(CERTIFIED, message)
        self.assertIn(TAMPERED, message)
        self.assertIn("certification no longer describes it", message)

    def test_the_refusal_is_the_digest_check_and_not_an_earlier_one(self):
        """§35. A mutant that deleted the digest comparison would survive if
        some earlier check were doing the refusing, so the reason is asserted
        rather than the mere fact of a refusal."""
        with self.assertRaises(FailClosed) as caught:
            self.s.skillslab.record_promotion(
                project_id=self.project, owner_identity=OWNER, why="x",
                fingerprint=TAMPERED)
        for wrong_reason in ("not a registered owner", "nothing was offered",
                             "not registered as proven"):
            self.assertNotIn(wrong_reason, str(caught.exception))

    def test_no_digest_at_all_is_refused(self):
        with self.assertRaises(FailClosed) as caught:
            self.s.skillslab.record_promotion(
                project_id=self.project, owner_identity=OWNER, why="x",
                fingerprint="")
        self.assertIn("authorises any artifact", str(caught.exception))

    def test_whitespace_is_not_a_digest(self):
        with self.assertRaises(FailClosed):
            self.s.skillslab.record_promotion(
                project_id=self.project, owner_identity=OWNER, why="x",
                fingerprint="   ")

    def test_a_refused_promotion_leaves_the_project_unpromoted(self):
        """Fail closed means nothing moved."""
        with self.assertRaises(FailClosed):
            self.s.skillslab.record_promotion(
                project_id=self.project, owner_identity=OWNER, why="x",
                fingerprint=TAMPERED)
        self.assertEqual(self.s.skillslab.project(self.project).stage,
                         lab.AWAITING_OWNER)

    def test_it_can_still_be_promoted_afterwards_with_the_right_digest(self):
        with self.assertRaises(FailClosed):
            self.s.skillslab.record_promotion(
                project_id=self.project, owner_identity=OWNER, why="x",
                fingerprint=TAMPERED)
        self.s.skillslab.record_promotion(
            project_id=self.project, owner_identity=OWNER, why="correct one",
            fingerprint=CERTIFIED)
        self.assertEqual(self.s.skillslab.project(self.project).stage,
                         lab.PROMOTED)

    def test_the_binding_survives_a_restart(self):
        path = self.s.store  # keep the reference before closing
        db = str(pathlib.Path(self.dir.name) / "solvent.db")
        self.s.store.close()
        self.s = Solvent(db)
        with self.assertRaises(FailClosed):
            self.s.skillslab.record_promotion(
                project_id=self.project, owner_identity=OWNER, why="x",
                fingerprint=TAMPERED)


class TheOwnerIntentCarriesTheDigest(Base):
    """The path the dashboard actually uses. An intent that names no artifact
    must not promote one."""

    def apply(self, params):
        from solvent.harness import _apply_owner_intent

        class _Approval:
            owner_identity = OWNER

        return _apply_owner_intent(
            self.s, "promote_capability",
            {"subject": self.project, "why": "approve", "params": params},
            approval=_Approval())

    def test_an_intent_without_a_fingerprint_is_refused(self):
        with self.assertRaises(FailClosed):
            self.apply({})
        self.assertNotEqual(self.s.skillslab.project(self.project).stage,
                            lab.PROMOTED)

    def test_an_intent_with_the_wrong_fingerprint_is_refused(self):
        with self.assertRaises(FailClosed):
            self.apply({"fingerprint": TAMPERED})
        self.assertNotEqual(self.s.skillslab.project(self.project).stage,
                            lab.PROMOTED)

    def test_an_intent_with_the_right_fingerprint_promotes(self):
        self.apply({"fingerprint": CERTIFIED})
        self.assertEqual(self.s.skillslab.project(self.project).stage,
                         lab.PROMOTED)


if __name__ == "__main__":
    unittest.main()
