"""Verifier certification, tested where mutation probes found it untested.

Every test in this module exists because a deliberately broken control went
unnoticed by the rest of the suite. Several of those controls turned out to be
*behaviourally* redundant — a FAILED verifier is also certified for no checks,
so removing the FAILED branch changed no outcome — but redundancy that nobody
has verified is indistinguishable from a control that does nothing. These pin
the branches themselves, so the next person to simplify one finds out.
"""

from __future__ import annotations

import unittest

from solvent import certgen, verifiercert as vc
from solvent.audit import AuditLog
from solvent.capability import CapabilityRegistry
from solvent.errors import FailClosed
from solvent.harness import OWNER, Solvent, ensure_verifier_certified
from solvent.policy import PolicyStore
from solvent.store import Store
from solvent.types import (
    CheckResult, ConsequenceTier, VerificationTier,
)


def outcome(passed: bool):
    class _Outcome:
        result = CheckResult.PASS if passed else CheckResult.FAIL
        detail = "TEST_ONLY"
        computed: dict = {}
    _Outcome.passed = passed
    return _Outcome()


class TheRefusalNamesWhyTheVerifierIsNotTrusted(unittest.TestCase):
    """A FAILED verifier must be refused *as failed*, not merely as unlisted."""

    def setUp(self):
        self.s = Solvent()
        report = vc.run_generated(lambda c, **k: outcome(True),
                                  capability="csv-cleanup",
                                  verifier_ref="approve-all",
                                  seed=certgen.CERTIFICATION_SEED, cases=14)
        self.s.capability.certify_verifier(
            verifier_ref="approve-all", capability_version="bad/1.0", report=report)

    def test_the_state_is_failed(self):
        self.assertEqual(
            self.s.capability.verifier_state("approve-all", "bad/1.0"), "FAILED")

    def test_the_reason_says_it_failed_certification(self):
        allowed, why = self.s.capability.may_authorize_delivery(
            verifier_ref="approve-all", capability_version="bad/1.0",
            check="drop_exact_duplicates")
        self.assertFalse(allowed)
        self.assertIn("failed certification", why)

    def test_a_revoked_verifier_is_refused_as_revoked(self):
        ensure_verifier_certified(self.s, "csv-cleanup")
        self.s.capability.revoke_verifier(
            verifier_ref="solvent.csvverify.run",
            capability_version="csv-cleanup/1.0",
            why="demonstrated false PASS", decided_by="qc")
        allowed, why = self.s.capability.may_authorize_delivery(
            verifier_ref="solvent.csvverify.run",
            capability_version="csv-cleanup/1.0", check="parses_as_csv")
        self.assertFalse(allowed)
        self.assertIn("revoked", why)

    def test_a_real_verifier_clears_the_evidence_floor(self):
        """Guards every test above: a floor nothing can clear proves nothing."""
        from solvent import csvverify

        report = vc.run_generated(csvverify.run, capability="csv-cleanup",
                                  verifier_ref="real",
                                  seed=certgen.CERTIFICATION_SEED, cases=14)
        certified, short = self.s.capability._checks_meeting_the_floor(report)
        self.assertEqual(short, [])
        self.assertGreaterEqual(len(certified), 9)


class APartiallyWrongVerifierIsTrustedOnlyWhereItWasRight(unittest.TestCase):
    """The interesting middle: competent at some checks, wrong at others.

    An all-or-nothing verdict would either throw away a verifier that is right
    about nine checks and wrong about one, or trust it on the one. Neither is
    acceptable, so certification is per check — and this is the case that
    proves the per-check path is real rather than decorative.
    """

    def setUp(self):
        self.s = Solvent()
        # Right about everything except duplicates, where it accepts anything.
        def partial(check, *, source, output, params):
            from solvent import csvverify
            if check == "drop_exact_duplicates":
                return outcome(True)
            return csvverify.run(check, source=source, output=output, params=params)

        self.report = vc.run_generated(partial, capability="csv-cleanup",
                                       verifier_ref="partial",
                                       seed=certgen.CERTIFICATION_SEED, cases=14)
        self.record = self.s.capability.certify_verifier(
            verifier_ref="partial", capability_version="partial/1.0",
            report=self.report)

    def test_it_is_certified_with_limits_not_outright(self):
        self.assertEqual(self.record["state"], "CERTIFIED_WITH_LIMITS",
                         self.record["why"])

    def test_the_reason_names_the_defective_artifact_it_accepted(self):
        self.assertIn("accepted", self.record["why"])
        self.assertGreater(self.record["false_accepts"], 0)

    def test_it_may_not_decide_the_check_it_got_wrong(self):
        allowed, why = self.s.capability.may_authorize_delivery(
            verifier_ref="partial", capability_version="partial/1.0",
            check="drop_exact_duplicates")
        self.assertFalse(allowed, why)

    def test_it_may_still_decide_the_checks_it_got_right(self):
        allowed, why = self.s.capability.may_authorize_delivery(
            verifier_ref="partial", capability_version="partial/1.0",
            check="preserve_columns")
        self.assertTrue(allowed, why)

    def test_a_verifier_wrong_in_both_directions_is_limited_too(self):
        def erratic(check, *, source, output, params):
            from solvent import csvverify
            if check == "drop_exact_duplicates":
                return outcome(True)          # accepts defects
            if check == "preserve_columns":
                return outcome(False)         # refuses correct work
            return csvverify.run(check, source=source, output=output, params=params)

        report = vc.run_generated(erratic, capability="csv-cleanup",
                                  verifier_ref="erratic",
                                  seed=certgen.CERTIFICATION_SEED, cases=14)
        record = self.s.capability.certify_verifier(
            verifier_ref="erratic", capability_version="erratic/1.0", report=report)
        self.assertEqual(record["state"], "CERTIFIED_WITH_LIMITS")
        self.assertGreater(record["false_accepts"], 0)
        self.assertGreater(record["false_rejects"], 0)
        for check in ("drop_exact_duplicates", "preserve_columns"):
            with self.subTest(check=check):
                self.assertFalse(self.s.capability.may_authorize_delivery(
                    verifier_ref="erratic", capability_version="erratic/1.0",
                    check=check)[0])


class AOneSidedBatteryCertifiesNothing(unittest.TestCase):
    """A check tried in only one direction cannot certify anything.

    Tested only on defective artifacts, a reject-everything stub scores
    perfectly. Tested only on correct ones, so does a rubber stamp. The real
    batteries happen to cover both directions everywhere, which is exactly why
    this needs a deliberately one-sided battery to test at all.
    """

    def setUp(self):
        self.s = Solvent()

    def trials(self, expectations):
        """Build a report by hand from named expectations."""
        results = []
        for name, check, expect, verdict in expectations:
            trial = vc.Trial(name=name, check=check, expect=expect)
            results.append(vc.TrialResult(trial, verdict))
        report = vc.TrialReport(capability="one-sided", verifier_ref="stub")
        report.results = results
        return report

    def test_a_check_tried_only_on_bad_artifacts_is_not_certified(self):
        report = self.trials([
            ("bad-1", "some_check", vc.BAD, "REJECT"),
            ("bad-2", "some_check", vc.BAD, "REJECT")])
        self.assertEqual(report.checks_passed(), set())
        self.assertEqual(report.one_sided_checks, {"some_check"})

    def test_a_check_tried_only_on_good_artifacts_is_not_certified(self):
        report = self.trials([
            ("good-1", "some_check", vc.GOOD, "ACCEPT"),
            ("good-2", "some_check", vc.GOOD, "ACCEPT")])
        self.assertEqual(report.checks_passed(), set())
        self.assertEqual(report.one_sided_checks, {"some_check"})

    def test_a_check_tried_in_both_directions_is_certified(self):
        report = self.trials([
            ("good-1", "some_check", vc.GOOD, "ACCEPT"),
            ("bad-1", "some_check", vc.BAD, "REJECT")])
        self.assertEqual(report.checks_passed(), {"some_check"})
        self.assertEqual(report.one_sided_checks, set())

    def test_a_one_sided_report_does_not_certify_the_verifier(self):
        report = self.trials([
            ("bad-1", "some_check", vc.BAD, "REJECT"),
            ("bad-2", "other_check", vc.BAD, "REJECT")])
        record = self.s.capability.certify_verifier(
            verifier_ref="stub", capability_version="one-sided/1.0", report=report)
        self.assertEqual(record["state"], "FAILED", record["why"])

    def test_the_real_batteries_are_not_one_sided(self):
        """Guards the test above: it proves nothing if the batteries are lopsided."""
        from solvent import csvverify, reportverify

        for capability, run in (("csv-cleanup", csvverify.run),
                                ("report-builder", reportverify.run)):
            report = vc.run_trials(run, capability=capability, verifier_ref="real")
            with self.subTest(capability=capability):
                self.assertEqual(report.one_sided_checks, set())


class TheAuditChokepointRefusesWhatItCannotVouchFor(unittest.TestCase):
    """The gate itself, not the registry behind it."""

    def setUp(self):
        self.store = Store()
        self.audit = AuditLog(self.store)

    def record(self, **overrides):
        fields = dict(
            subject_ref="J1", tier=VerificationTier.T1_DETERMINISTIC,
            method="m", executor_identity="worker", verifier_identity="qc",
            verdict=True, raw_output="ok", consequence=ConsequenceTier.C_LOW,
            requirement_id="R-1", artifact_digest="d" * 64,
            verifier_ref="solvent.csvverify.run",
            capability_version="csv-cleanup/1.0", check="parses_as_csv")
        fields.update(overrides)
        return self.audit.record_verification(**fields)

    def test_with_no_trust_oracle_wired_evidence_above_t0_is_refused(self):
        """An audit log that cannot ask about the verifier does not guess."""
        with self.assertRaises(FailClosed) as caught:
            self.record()
        self.assertIn("certification", str(caught.exception))

    def test_evidence_that_names_no_verifier_implementation_is_refused(self):
        self.wire_trust()
        with self.assertRaises(FailClosed) as caught:
            self.record(verifier_ref="")
        self.assertIn("verifier implementation", str(caught.exception))

    def test_a_t0_self_report_still_needs_no_verifier_certification(self):
        """T0 is not evidence and authorises nothing, so it is not gated here."""
        self.assertTrue(self.record(tier=VerificationTier.T0_SELF_REPORT,
                                    verifier_ref="", requirement_id="",
                                    artifact_digest=""))

    def test_certified_evidence_records_the_implementation_and_its_state(self):
        self.wire_trust()
        self.record()
        row = self.store.raw_readonly("SELECT * FROM verification_evidence")[0]
        self.assertEqual(row["verifier_ref"], "solvent.csvverify.run")
        self.assertEqual(row["verifier_state"], "CERTIFIED")

    def wire_trust(self):
        from solvent import csvverify

        policy = PolicyStore(self.store, self.audit, owner_identity=OWNER)
        registry = CapabilityRegistry(self.store, self.audit, policy)
        report = vc.run_generated(csvverify.run, capability="csv-cleanup",
                                  verifier_ref="solvent.csvverify.run",
                                  seed=certgen.CERTIFICATION_SEED, cases=14)
        registry.certify_verifier(
            verifier_ref="solvent.csvverify.run",
            capability_version="csv-cleanup/1.0", report=report,
            fingerprint=vc.implementation_fingerprint(csvverify.run))
        self.audit.trust_verifiers_via(registry)


class EvidenceIsBoundToTheArtifactAtTheDeliveryGate(unittest.TestCase):
    """Digest scoping, tested *through* the gate rather than on the helper.

    ``passes_for_artifact`` was already tested directly. What was not tested is
    that ``verification_satisfied`` uses it — so a change that read every pass
    for the job, regardless of which artifact earned it, went unnoticed. That
    is the difference between a correction inheriting its predecessor's passes
    and not.
    """

    def setUp(self):
        from tests_solvent import fixtures_csv as fx

        self.s = Solvent()
        source, work = fx.workspace(fx.DUPLICATES)
        self.report = run_job(self.s, source, work, fx)
        self.job_id = self.report.job_id

    def test_the_job_delivered_on_its_current_artifact(self):
        self.assertTrue(self.report.delivered, self.report.escalated)
        ok, why = self.s.orchestrator.verification_satisfied(self.job_id)
        self.assertTrue(ok, why)

    def test_a_different_artifact_does_not_inherit_those_passes(self):
        """Replace the file's bytes: every pass was earned against other bytes."""
        import pathlib

        deliverable = self.s.orchestrator.current_deliverable(self.job_id)
        path = pathlib.Path(deliverable.path)
        path.write_text(path.read_text(encoding="utf-8") + "9999,X,2026-01-01,North,1,1.00\n",
                        encoding="utf-8")
        ok, why = self.s.orchestrator.verification_satisfied(self.job_id)
        self.assertFalse(ok)
        self.assertIn("changed since it was registered", why)

    def test_passes_are_looked_up_by_digest_not_by_job(self):
        deliverable = self.s.orchestrator.current_deliverable(self.job_id)
        for_this = self.s.audit.passes_for_artifact(self.job_id, deliverable.digest)
        for_other = self.s.audit.passes_for_artifact(self.job_id, "f" * 64)
        self.assertTrue(for_this)
        self.assertEqual(for_other, {},
                         "evidence was returned for an artifact that earned none")

    def test_a_correction_does_not_inherit_the_previous_attempts_passes(self):
        """The hole a mutation probe found: passes read by job, not by artifact.

        Attempt one earns passes. Attempt two replaces the deliverable and earns
        none. Reading every pass recorded against the *job* would clear attempt
        two on attempt one's evidence — a correction inheriting the verification
        of the thing it was correcting, which is precisely backwards.
        """
        import pathlib as _p

        first = self.s.orchestrator.current_deliverable(self.job_id)
        passes_for_first = self.s.audit.passes_for_artifact(self.job_id,
                                                            first.digest)
        self.assertTrue(passes_for_first, "attempt one earned no evidence")

        # A genuinely different artifact, registered as the new deliverable.
        replacement = _p.Path(first.path).with_name("deliverable.attempt2.csv")
        replacement.write_text(
            "Order ID,Customer,Order Date,Region,Units,Total\n"
            "2001,Replacement,2026-02-01,North,1,10.00\n", encoding="utf-8")
        second = self.s.orchestrator.register_artifact(
            job_id=self.job_id, role="DELIVERABLE", path=str(replacement),
            produced_by="worker", capability_version="csv-cleanup/1.0",
            initiator="execution", media_type="text/csv")

        self.assertNotEqual(second.digest, first.digest)
        self.assertEqual(
            self.s.audit.passes_for_artifact(self.job_id, second.digest), {},
            "the new artifact somehow already has evidence")

        ok, why = self.s.orchestrator.verification_satisfied(self.job_id)
        self.assertFalse(
            ok, "a replacement artifact was cleared on the previous one's passes")
        self.assertIn("no pass", why)

    def test_every_recorded_pass_names_the_artifact_it_inspected(self):
        rows = self.s.store.raw_readonly(
            "SELECT * FROM verification_evidence WHERE subject_ref = ?",
            (self.job_id,))
        self.assertTrue(rows)
        for row in rows:
            with self.subTest(requirement=row["requirement_id"]):
                self.assertTrue(row["artifact_digest"])
                self.assertTrue(row["requirement_id"])


def run_job(solvent, source, work, fx):
    from solvent.harness import run_csv_job

    return run_csv_job(source=source, requirements=list(fx.SIMPLE), workdir=work,
                       solvent=solvent, client_id="client:bind", title="bind")


if __name__ == "__main__":
    unittest.main()
