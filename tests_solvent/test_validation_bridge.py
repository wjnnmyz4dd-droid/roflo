"""§22-§26, §75-§78. A candidate is tested by somebody other than its author.

The rule this file exists to hold: **DeskPilot may not reason "I created it,
therefore it is safe."** Everything else here is a consequence of that. The
creator cannot validate, the validator cannot promote, the owner promotes but
is not asked to be the evidence, and the artifact that was tested is the
artifact that gets approved.

The external validator is deliberately a *role*, not a named product. Claude is
the validator today; the contract does not assume Claude is available tomorrow,
and nothing here pretends a validation ran that did not.
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

BUILT_AT = "sha256:1111candidate"
CORRECTED_AT = "sha256:2222corrected"
VALIDATOR = "external:validator-under-test"


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.s = Solvent(str(pathlib.Path(self.dir.name) / "solvent.db"))
        self.addCleanup(self.close)
        self.covers = frozenset({"extract_pdf_text"})

    def close(self):
        try:
            self.s.store.close()
        except Exception:
            pass

    def built(self, *, fingerprint=BUILT_AT, skill="pdf-extract") -> str:
        project_id = verified_project(self.s, skill=skill, covers=self.covers)
        self.s.skillslab.mark_specified(
            project_id=project_id, target_covers=self.covers,
            target_version=f"{skill}/1.0")
        self.s.skillslab.mark_built(project_id=project_id,
                                    fingerprint=fingerprint)
        return project_id

    def owner_opened(self, skill="pdf-extract") -> str:
        """A built candidate whose project was opened by the owner, so the
        creator check cannot be the one that refuses."""
        project_id = self.s.skillslab.record_need(
            skill=skill, need="the owner asked for this",
            observed_on="job_owner", requested_by=OWNER)
        self.s.skillslab.check_overlap(project_id=project_id,
                                       target_covers=self.covers)
        self.s.capability.propose(name=skill, covers=self.covers,
                                  why="test", requested_by=OWNER)
        self.s.capability.decide(name=skill, decision="APPROVED",
                                 owner_identity=OWNER, why="test")
        claim_id = self.s.skillslab.record_claim(
            project_id=project_id, claim="measured behaviour",
            source="ran it against fixtures", trust=lab.MEASURED)
        self.s.skillslab.verify_claim(claim_id=claim_id, status=lab.VERIFIED,
                                      checked_by="test harness",
                                      detail="reproduced")
        self.s.skillslab.mark_specified(project_id=project_id,
                                        target_covers=self.covers,
                                        target_version=f"{skill}/1.0")
        self.s.skillslab.mark_built(project_id=project_id,
                                    fingerprint=BUILT_AT)
        return project_id

    def register(self, skill="pdf-extract", version="pdf-extract/1.0"):
        self.s.capability.register(
            Capability(name=skill, covers=self.covers, proven=True,
                       version=version, verifiable_by=tuple(self.covers),
                       proven_levels=("L1", "L2", "L3"), fixtures_passed=25,
                       fixtures_total=25, false_completions=0,
                       evidence_ref="tests_solvent/test_validation_bridge.py"),
            owner_identity=OWNER)


class ACandidateCannotValidateItself(Base):

    def test_the_creator_may_not_validate(self):
        project_id = self.built()
        created_by = self.s.skillslab.project(project_id).opened_by
        self.s.skillslab.request_validation(project_id=project_id,
                                            fingerprint=BUILT_AT)
        with self.assertRaises(FailClosed) as caught:
            self.s.skillslab.record_validation(
                project_id=project_id, validator=created_by, passed=True,
                fingerprint=BUILT_AT)
        self.assertIn("may not also validate", str(caught.exception))

    def test_the_lab_may_not_validate(self):
        project_id = self.built()
        self.s.skillslab.request_validation(project_id=project_id,
                                            fingerprint=BUILT_AT)
        with self.assertRaises(FailClosed):
            self.s.skillslab.record_validation(
                project_id=project_id, validator=lab.AUTHORITY, passed=True,
                fingerprint=BUILT_AT)

    def test_the_lab_may_not_validate_a_project_it_did_not_open(self):
        """The case that makes the Lab check load-bearing rather than a
        duplicate of the creator check.

        A mutation deleting "the Lab may not validate" survived the suite,
        because every project in the tests was opened by the Lab and so the
        *creator* check was doing the refusing. But record_need takes
        requested_by, so a project opened by the owner can exist -- and for
        that project the creator check does not fire and only this one stands
        between the Lab and certifying its own work.
        """
        project_id = self.owner_opened()
        self.s.skillslab.request_validation(project_id=project_id,
                                            fingerprint=BUILT_AT)
        with self.assertRaises(FailClosed) as caught:
            self.s.skillslab.record_validation(
                project_id=project_id, validator=lab.AUTHORITY, passed=True,
                fingerprint=BUILT_AT)
        message = str(caught.exception)
        self.assertIn("Skills Lab may not validate", message)
        # Specifically not the creator check, which cannot fire here.
        self.assertNotIn("may not also validate", message)

    def test_an_owner_opened_project_can_still_be_validated_by_somebody_else(self):
        """The positive canary. Without it the refusal above could hold on a
        project that was never validatable at all."""
        project_id = self.owner_opened()
        self.s.skillslab.request_validation(project_id=project_id,
                                            fingerprint=BUILT_AT)
        ok, why = self.s.skillslab.record_validation(
            project_id=project_id, validator=VALIDATOR, passed=True,
            fingerprint=BUILT_AT)
        self.assertTrue(ok, why)

    def test_an_unnamed_validator_is_refused(self):
        project_id = self.built()
        self.s.skillslab.request_validation(project_id=project_id,
                                            fingerprint=BUILT_AT)
        with self.assertRaises(FailClosed):
            self.s.skillslab.record_validation(
                project_id=project_id, validator="", passed=True,
                fingerprint=BUILT_AT)

    def test_a_refused_validation_leaves_the_candidate_where_it_was(self):
        project_id = self.built()
        self.s.skillslab.request_validation(project_id=project_id,
                                            fingerprint=BUILT_AT)
        with self.assertRaises(FailClosed):
            self.s.skillslab.record_validation(
                project_id=project_id, validator=lab.AUTHORITY, passed=True,
                fingerprint=BUILT_AT)
        self.assertEqual(self.s.skillslab.project(project_id).stage,
                         lab.VALIDATION_REQUESTED)


class TheHandoffCarriesFactsAndNoSecrets(Base):

    def setUp(self):
        super().setUp()
        self.project_id = self.built()
        self.package = self.s.skillslab.request_validation(
            project_id=self.project_id, fingerprint=BUILT_AT)

    def test_it_names_the_candidate_and_the_artifact(self):
        self.assertEqual(self.package["project_id"], self.project_id)
        self.assertEqual(self.package["fingerprint"], BUILT_AT)
        self.assertEqual(self.package["target_version"], "pdf-extract/1.0")

    def test_it_states_what_the_candidate_must_do(self):
        self.assertEqual(self.package["required_checks"], sorted(self.covers))

    def test_it_carries_the_evidence_gathered_so_far(self):
        self.assertTrue(self.package["evidence"])

    def test_it_names_the_creator_so_the_separation_is_checkable(self):
        self.assertTrue(self.package["created_by"])

    def test_it_states_the_testing_standard_expected(self):
        for expected in ("mutation", "holdout", "adversarial", "regression"):
            self.assertIn(expected, self.package["test_standard"])

    def test_it_contains_no_secret(self):
        """§25. A validator tests a candidate; it does not operate the
        business, and a package carrying production secrets would make every
        validator a production risk."""
        import json

        blob = json.dumps(self.package, default=str).lower()
        for secret in ("owner_key", "solvent_owner_key", "api_key", "stripe",
                       "webhook_secret", "password", "private_key"):
            self.assertNotIn(secret, blob)

    def test_it_is_deterministic(self):
        again = self.s.skillslab.handoff(self.project_id, fingerprint=BUILT_AT)
        self.assertEqual(self.package["required_checks"],
                         again["required_checks"])
        self.assertEqual(self.package["target_version"],
                         again["target_version"])


class AFailedValidationIsRecordedTruthfully(Base):
    """§76. The broken-candidate lifecycle."""

    def setUp(self):
        super().setUp()
        self.project_id = self.built()
        self.s.skillslab.request_validation(project_id=self.project_id,
                                            fingerprint=BUILT_AT)
        self.s.skillslab.record_validation(
            project_id=self.project_id, validator=VALIDATOR, passed=False,
            fingerprint=BUILT_AT, detail="extract_pdf_text returned empty text")

    def test_the_candidate_is_marked_failed(self):
        self.assertEqual(self.s.skillslab.project(self.project_id).stage,
                         lab.VALIDATION_FAILED)

    def test_it_is_not_certified(self):
        versions = self.s.skillslab.versions("pdf-extract")
        self.assertEqual(versions, [])

    def test_it_cannot_be_offered_to_the_owner(self):
        with self.assertRaises(FailClosed):
            self.s.skillslab.offer_to_owner(self.project_id)

    def test_the_failure_reason_is_recorded(self):
        timeline = self.s.skillslab.timeline(self.project_id)
        failures = [e for e in timeline if e["outcome"] == "VALIDATION_FAILED"]
        self.assertEqual(len(failures), 1)
        self.assertIn("empty text", failures[0]["detail"])

    def test_the_prior_failure_reaches_the_next_handoff(self):
        """A validator picking this up again is told what went wrong before."""
        package = self.s.skillslab.request_validation(
            project_id=self.project_id, fingerprint=CORRECTED_AT)
        self.assertTrue(package["prior_failures"])

    def test_certification_only_follows_a_genuine_correction(self):
        self.s.skillslab.request_validation(project_id=self.project_id,
                                            fingerprint=CORRECTED_AT)
        ok, why = self.s.skillslab.record_validation(
            project_id=self.project_id, validator=VALIDATOR, passed=True,
            fingerprint=CORRECTED_AT, detail="corrected and re-run")
        self.assertTrue(ok, why)
        self.assertEqual(self.s.skillslab.project(self.project_id).stage,
                         lab.CERTIFIED)

    def test_the_corrected_artifact_is_what_gets_certified(self):
        self.s.skillslab.request_validation(project_id=self.project_id,
                                            fingerprint=CORRECTED_AT)
        self.s.skillslab.record_validation(
            project_id=self.project_id, validator=VALIDATOR, passed=True,
            fingerprint=CORRECTED_AT)
        recorded = self.s.skillslab.versions("pdf-extract")[0]["fingerprint"]
        self.assertEqual(recorded, CORRECTED_AT)
        self.assertNotEqual(recorded, BUILT_AT)


class APassedValidationReachesTheOwnerAndStops(Base):
    """§75. The good-candidate lifecycle, ending exactly where it should."""

    def setUp(self):
        super().setUp()
        self.project_id = self.built()
        self.s.skillslab.request_validation(project_id=self.project_id,
                                            fingerprint=BUILT_AT)
        self.ok, self.why = self.s.skillslab.record_validation(
            project_id=self.project_id, validator=VALIDATOR, passed=True,
            fingerprint=BUILT_AT, evidence_ref="battery: 41 tests, 8/8 mutants")

    def test_it_certifies(self):
        self.assertTrue(self.ok, self.why)
        self.assertEqual(self.s.skillslab.project(self.project_id).stage,
                         lab.CERTIFIED)

    def test_the_validator_is_recorded_as_the_one_who_tested_it(self):
        events = [e for e in self.s.audit.events()
                  if e["event"] == "skillslab.validated"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["initiator"], VALIDATOR)

    def test_certification_does_not_promote(self):
        """The validator tests. It does not decide what runs in production."""
        self.assertNotEqual(self.s.skillslab.project(self.project_id).stage,
                            lab.PROMOTED)
        self.assertEqual(
            [c.name for c in self.s.capability.capabilities()], [])

    def test_it_goes_to_the_owner_and_waits(self):
        self.s.skillslab.offer_to_owner(self.project_id)
        self.assertEqual(self.s.skillslab.project(self.project_id).stage,
                         lab.AWAITING_OWNER)

    def test_the_validator_cannot_promote_it(self):
        self.s.skillslab.offer_to_owner(self.project_id)
        self.register()
        with self.assertRaises(FailClosed) as caught:
            self.s.skillslab.record_promotion(
                project_id=self.project_id, owner_identity=VALIDATOR,
                why="I tested it so I am promoting it",
                fingerprint=BUILT_AT)
        self.assertIn("not a registered owner", str(caught.exception))

    def test_the_owner_can(self):
        self.s.skillslab.offer_to_owner(self.project_id)
        self.register()
        self.s.skillslab.record_promotion(
            project_id=self.project_id, owner_identity=OWNER,
            why="certified by an independent validator", fingerprint=BUILT_AT)
        self.assertEqual(self.s.skillslab.project(self.project_id).stage,
                         lab.PROMOTED)

    def test_the_owner_approval_still_binds_to_the_tested_artifact(self):
        """§39 and §77 together: a validation pass does not loosen the digest
        binding. Code that changed after the validator saw it is not what the
        owner is being asked about."""
        self.s.skillslab.offer_to_owner(self.project_id)
        self.register()
        with self.assertRaises(FailClosed):
            self.s.skillslab.record_promotion(
                project_id=self.project_id, owner_identity=OWNER,
                why="approving a moving target", fingerprint=CORRECTED_AT)


class ValidationCannotBeSkipped(Base):

    def test_a_result_with_no_request_outstanding_is_refused(self):
        project_id = self.built()
        with self.assertRaises(FailClosed) as caught:
            self.s.skillslab.record_validation(
                project_id=project_id, validator=VALIDATOR, passed=True,
                fingerprint=BUILT_AT)
        self.assertIn("there is none outstanding", str(caught.exception))

    def test_an_unbuilt_candidate_cannot_be_handed_out(self):
        project_id = verified_project(self.s, skill="pdf-extract",
                                      covers=self.covers)
        with self.assertRaises(FailClosed):
            self.s.skillslab.request_validation(project_id=project_id,
                                                fingerprint=BUILT_AT)

    def test_a_handoff_must_name_an_artifact(self):
        project_id = self.built()
        with self.assertRaises(FailClosed):
            self.s.skillslab.request_validation(project_id=project_id,
                                                fingerprint="")

    def test_a_result_must_name_the_artifact_tested(self):
        project_id = self.built()
        self.s.skillslab.request_validation(project_id=project_id,
                                            fingerprint=BUILT_AT)
        with self.assertRaises(FailClosed):
            self.s.skillslab.record_validation(
                project_id=project_id, validator=VALIDATOR, passed=True,
                fingerprint="")

    def test_state_survives_a_restart(self):
        project_id = self.built()
        self.s.skillslab.request_validation(project_id=project_id,
                                            fingerprint=BUILT_AT)
        db = str(pathlib.Path(self.dir.name) / "solvent.db")
        self.s.store.close()
        self.s = Solvent(db)
        self.assertEqual(self.s.skillslab.project(project_id).stage,
                         lab.VALIDATION_REQUESTED)


if __name__ == "__main__":
    unittest.main()
