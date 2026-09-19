"""Attacks on the checklist, the artifact binding and the delivery gate.

The previous audit's finding was blunt: three client requirements recorded, none
met, none checked, delivered. Everything here is an attempt to do that again.
"""

from __future__ import annotations

import pathlib
import unittest

from solvent.errors import FailClosed
from solvent.harness import OWNER, Solvent, run_csv_job, verify_against_baseline
from solvent.types import (
    ConsequenceTier, Criticality, JobState, Jurisdiction, LocationSignals,
    Requirement, RequirementSource as RS, RequirementStatus, VerificationTier,
)
from tests_solvent import fixtures_csv as fx


def delivered_job():
    """A genuine job, delivered. The starting point for most attacks."""
    source, work = fx.workspace(fx.MESSY)
    s = Solvent()
    report = run_csv_job(source=source, requirements=fx.COMPLEX, workdir=work,
                         solvent=s)
    assert report.delivered, report.escalated
    return s, report, source


class RequirementAttacks(unittest.TestCase):
    def setUp(self):
        self.s = Solvent()
        self.job = self.s.orchestrator.intake(
            title="t", client_id="c", quoted_cents=18_000, initiator=OWNER,
            signals=LocationSignals(project=Jurisdiction(state="NC")))
        self.s.orchestrator.add_requirement(self.job, fx.DEDUPE)
        self.s.orchestrator.add_requirement(self.job, fx.OPENS)
        self.s.orchestrator.commit_requirements(job_id=self.job, initiator=OWNER)

    def test_a_committed_requirement_cannot_be_overwritten(self):
        weaker = Requirement(id="R-DEDUPE", text="never mind the duplicates",
                             source=RS.CLIENT_CONFIRMED_STRUCTURED,
                             check="parses_as_csv", params={})
        with self.assertRaises(FailClosed) as caught:
            self.s.orchestrator.add_requirement(self.job, weaker)
        self.assertIn("committed", str(caught.exception))

    def test_a_non_owner_cannot_amend_a_committed_requirement(self):
        with self.assertRaises(FailClosed):
            self.s.orchestrator.amend_requirement(
                job_id=self.job, replacing="R-DEDUPE",
                replacement=fx.req("R-NEW", "weaker", "parses_as_csv"),
                owner_identity="worker:csv", why="easier")

    def test_a_non_owner_cannot_withdraw_a_requirement(self):
        with self.assertRaises(FailClosed):
            self.s.orchestrator.withdraw_requirement(
                job_id=self.job, requirement_id="R-DEDUPE",
                owner_identity="client:acme", why="I changed my mind")
        self.assertIn("R-DEDUPE", [r.id for r in self.s.orchestrator.baseline(self.job)])

    def test_an_owner_amendment_preserves_the_original(self):
        self.s.orchestrator.amend_requirement(
            job_id=self.job, replacing="R-DEDUPE",
            replacement=fx.req("R-DEDUPE-2", "dedupe, case-insensitively",
                               "drop_exact_duplicates"),
            owner_identity=OWNER, why="client clarified")
        all_records = {r.id: r for r in self.s.orchestrator.requirement_records(self.job)}
        self.assertIs(all_records["R-DEDUPE"].status, RequirementStatus.AMENDED)
        self.assertEqual(all_records["R-DEDUPE-2"].supersedes, "R-DEDUPE")

    def test_an_amendment_may_not_reuse_the_original_id(self):
        with self.assertRaises(FailClosed):
            self.s.orchestrator.amend_requirement(
                job_id=self.job, replacing="R-DEDUPE",
                replacement=fx.req("R-DEDUPE", "same id", "drop_exact_duplicates"),
                owner_identity=OWNER, why="overwrite attempt")

    def test_a_mandatory_requirement_with_no_check_cannot_be_committed(self):
        job = self.s.orchestrator.intake(
            title="t2", client_id="c", quoted_cents=1000, initiator=OWNER,
            signals=LocationSignals(project=Jurisdiction(state="NC")))
        self.s.orchestrator.add_requirement(job, Requirement(
            id="R-VAGUE", text="make it nice", source=RS.CLIENT_CONFIRMED_STRUCTURED))
        with self.assertRaises(FailClosed) as caught:
            self.s.orchestrator.commit_requirements(job_id=job, initiator=OWNER)
        self.assertIn("no registered check", str(caught.exception))

    def test_a_model_extracted_requirement_cannot_gate_a_commitment(self):
        job = self.s.orchestrator.intake(
            title="t3", client_id="c", quoted_cents=1000, initiator=OWNER,
            signals=LocationSignals(project=Jurisdiction(state="NC")))
        self.s.orchestrator.add_requirement(job, fx.req(
            "R-GUESS", "probably dedupe", "drop_exact_duplicates",
            source=RS.MODEL_EXTRACTED_UNCONFIRMED))
        with self.assertRaises(FailClosed) as caught:
            self.s.orchestrator.commit_requirements(job_id=job, initiator=OWNER)
        self.assertIn("unconfirmed", str(caught.exception))

    def test_a_job_with_no_requirements_cannot_commit(self):
        job = self.s.orchestrator.intake(
            title="t4", client_id="c", quoted_cents=1000, initiator=OWNER,
            signals=LocationSignals(project=Jurisdiction(state="NC")))
        with self.assertRaises(FailClosed):
            self.s.orchestrator.commit_requirements(job_id=job, initiator=OWNER)


class ArtifactAttacks(unittest.TestCase):
    def test_verifying_one_file_and_delivering_another_is_refused(self):
        s, report, source = delivered_job()
        job = report.job_id
        # Swap in a different file at the recorded path.
        path = pathlib.Path(report.artifacts[-1].path)
        path.write_text(fx.CLEAN, encoding="utf-8")
        ok, why = s.orchestrator.verification_satisfied(job)
        self.assertFalse(ok)
        self.assertIn("changed since it was registered", why)

    def test_changing_one_byte_invalidates_every_pass(self):
        s, report, source = delivered_job()
        path = pathlib.Path(report.artifacts[-1].path)
        path.write_text(path.read_text() + " ", encoding="utf-8")
        ok, _ = s.orchestrator.verification_satisfied(report.job_id)
        self.assertFalse(ok)

    def test_a_caller_cannot_supply_its_own_digest(self):
        """register_artifact computes the digest; it never takes one."""
        import inspect

        sig = inspect.signature(Solvent().orchestrator.register_artifact)
        self.assertNotIn("digest", sig.parameters)

    def test_registering_an_artifact_that_does_not_exist_is_refused(self):
        s = Solvent()
        job = s.orchestrator.intake(
            title="t", client_id="c", quoted_cents=1000, initiator=OWNER,
            signals=LocationSignals(project=Jurisdiction(state="NC")))
        with self.assertRaises(FailClosed):
            s.orchestrator.register_artifact(job_id=job, role="DELIVERABLE",
                                             path="/no/such/file.csv")

    def test_a_deleted_deliverable_cannot_be_delivered(self):
        s, report, source = delivered_job()
        pathlib.Path(report.artifacts[-1].path).unlink()
        ok, why = s.orchestrator.verification_satisfied(report.job_id)
        self.assertFalse(ok)
        self.assertIn("missing", why)

    def test_evidence_from_another_job_does_not_count(self):
        s, report, source = delivered_job()
        other = Solvent()
        job = other.orchestrator.intake(
            title="other", client_id="c", quoted_cents=1000, initiator=OWNER,
            signals=LocationSignals(project=Jurisdiction(state="NC")))
        passes = other.audit.passes_for_artifact(job, report.artifacts[-1].digest)
        self.assertEqual(passes, {})

    def test_a_correction_cannot_inherit_the_previous_passes(self):
        """A new artifact has a new digest, so old evidence simply does not apply."""
        s, report, source = delivered_job()
        first = report.artifacts[-1]
        second = pathlib.Path(first.path).with_name("v2.csv")
        second.write_text(pathlib.Path(first.path).read_text() + "9999,X,2026-01-01,North,1,1.00\n",
                          encoding="utf-8")
        artifact = s.orchestrator.register_artifact(
            job_id=report.job_id, role="DELIVERABLE", path=str(second),
            produced_by="worker")
        self.assertEqual(
            s.audit.passes_for_artifact(report.job_id, artifact.digest), {})


class GuardianAttacks(unittest.TestCase):
    def setUp(self):
        self.s = Solvent()
        self.job = self.s.orchestrator.intake(
            title="t", client_id="c", quoted_cents=18_000, initiator=OWNER,
            signals=LocationSignals(project=Jurisdiction(state="NC")))

    def test_evidence_above_t0_must_name_a_requirement(self):
        with self.assertRaises(FailClosed) as caught:
            self.s.audit.record_verification(
                subject_ref=self.job, tier=VerificationTier.T1_DETERMINISTIC,
                method="m", executor_identity="w", verifier_identity="v",
                verdict=True, raw_output="ok",
                consequence=ConsequenceTier.C_LOW, artifact_digest="d" * 64)
        self.assertIn("must name the requirement", str(caught.exception))

    def test_evidence_above_t0_must_name_an_artifact_digest(self):
        with self.assertRaises(FailClosed) as caught:
            self.s.audit.record_verification(
                subject_ref=self.job, tier=VerificationTier.T1_DETERMINISTIC,
                method="m", executor_identity="w", verifier_identity="v",
                verdict=True, raw_output="ok",
                consequence=ConsequenceTier.C_LOW, requirement_id="R-1")
        self.assertIn("artifact digest", str(caught.exception))

    def test_the_executor_may_not_verify_its_own_high_consequence_work(self):
        with self.assertRaises(FailClosed):
            self.s.audit.record_verification(
                subject_ref=self.job, tier=VerificationTier.T1_DETERMINISTIC,
                method="m", executor_identity="worker", verifier_identity="worker",
                verdict=True, raw_output="ok", consequence=ConsequenceTier.C_HIGH,
                requirement_id="R-1", artifact_digest="d" * 64)

    def test_a_self_report_cannot_verify_above_low_consequence(self):
        with self.assertRaises(FailClosed):
            self.s.audit.record_verification(
                subject_ref=self.job, tier=VerificationTier.T0_SELF_REPORT,
                method="m", executor_identity="w", verifier_identity="v",
                verdict=True, raw_output="ok", consequence=ConsequenceTier.C_HIGH)

    def test_verification_with_no_verifier_is_refused(self):
        with self.assertRaises(FailClosed):
            self.s.audit.record_verification(
                subject_ref=self.job, tier=VerificationTier.T1_DETERMINISTIC,
                method="m", executor_identity="w", verifier_identity="",
                verdict=True, raw_output="ok", consequence=ConsequenceTier.C_LOW,
                requirement_id="R-1", artifact_digest="d" * 64)

    def test_passing_only_some_requirements_does_not_open_the_gate(self):
        source, work = fx.workspace(fx.MESSY)
        s = Solvent()
        report = run_csv_job(source=source, requirements=fx.COMPLEX, workdir=work,
                             solvent=s)
        # Add a new mandatory requirement after the fact; it has no evidence.
        s.orchestrator.add_requirement(report.job_id, fx.req(
            "R-EXTRA", "sort by Order ID", "sort_rows",
            {"columns": ["Order ID"]}))
        s.orchestrator.commit_requirements(job_id=report.job_id, initiator=OWNER)
        ok, why = s.orchestrator.verification_satisfied(report.job_id)
        self.assertFalse(ok)
        self.assertIn("R-EXTRA", why)

    def test_a_job_with_no_baseline_cannot_be_delivered(self):
        s = Solvent()
        job = s.orchestrator.intake(
            title="t", client_id="c", quoted_cents=1000, initiator=OWNER,
            signals=LocationSignals(project=Jurisdiction(state="NC")))
        ok, why = s.orchestrator.verification_satisfied(job)
        self.assertFalse(ok)
        self.assertIn("no committed requirement baseline", why)

    def test_an_advisory_failure_does_not_block_delivery_but_is_visible(self):
        source, work = fx.workspace(fx.MESSY)
        s = Solvent()
        # Advisory *and* genuinely unsatisfiable: the column does not exist, so
        # nothing can make it pass. A mandatory version would block delivery.
        advisory = fx.req("R-NICE", "ideally include a Salesperson column",
                          "require_columns", {"columns": ["Salesperson"]},
                          criticality=Criticality.ADVISORY)
        report = run_csv_job(source=source, requirements=fx.COMPLEX + [advisory],
                             workdir=work, solvent=s)
        self.assertTrue(report.delivered, report.escalated)
        self.assertIn("R-NICE", report.rounds[-1].advisory_failures)


class FalseCompletionBattery(unittest.TestCase):
    """Plausible but wrong artifacts. Every one must be stopped."""

    def attempt(self, mutate):
        import csv as _csv

        def sabotage(path, attempt):
            with open(path, newline="", encoding="utf-8") as handle:
                data = list(_csv.reader(handle))
            data = mutate(data)
            with open(path, "w", newline="", encoding="utf-8") as handle:
                _csv.writer(handle).writerows(data)

        source, work = fx.workspace(fx.MESSY)
        return run_csv_job(source=source, requirements=fx.COMPLEX, workdir=work,
                           sabotage=sabotage)

    def test_a_left_behind_duplicate_is_caught(self):
        r = self.attempt(lambda rows: rows + [rows[2][:]])
        self.assertFalse(r.delivered)

    def test_a_changed_protected_total_is_caught(self):
        r = self.attempt(
            lambda rows: [rows[0]] + [[*rows[1][:5], "999.00"]] + rows[2:])
        self.assertFalse(r.delivered)
        self.assertIn("R-TOTAL", r.rounds[-1].failures)

    def test_a_malformed_date_is_caught(self):
        r = self.attempt(
            lambda rows: [rows[0]] + [[*rows[1][:2], "15/01/26", *rows[1][3:]]]
            + rows[2:])
        self.assertFalse(r.delivered)
        self.assertIn("R-DATES", r.rounds[-1].failures)

    def test_a_silently_dropped_row_is_caught(self):
        r = self.attempt(lambda rows: rows[:3] + rows[4:])
        self.assertFalse(r.delivered)
        self.assertIn("R-ROWS", r.rounds[-1].failures)

    def test_a_deleted_column_is_caught(self):
        r = self.attempt(lambda rows: [row[:2] + row[3:] for row in rows])
        self.assertFalse(r.delivered)

    def test_an_unauthorised_extra_transformation_is_caught(self):
        """The one that escaped the first battery: helpful, unrequested, wrong."""
        r = self.attempt(
            lambda rows: [rows[0]] + [[r[0], r[1].upper(), *r[2:]] for r in rows[1:]])
        self.assertFalse(r.delivered)
        self.assertIn("R-NOCHANGE", r.rounds[-1].failures)

    def test_a_ragged_row_is_caught(self):
        r = self.attempt(lambda rows: rows + [["9999", "Zeta"]])
        self.assertFalse(r.delivered)


if __name__ == "__main__":
    unittest.main()
