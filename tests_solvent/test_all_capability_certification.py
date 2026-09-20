"""Certification properties that span capabilities rather than test one.

Three questions the per-capability suites cannot answer on their own:

* **Does a bad capability actually get rejected?** A certification that only
  ever sees good work proves nothing about the gate. So two deliberately
  defective capabilities are built here and run through the real pipeline — one
  that produces wrong documents, and the more dangerous one that arrives with a
  verifier of its own that approves everything it does.
* **Does work survive being handed between capabilities?** A cleaned file that
  becomes the source of a report is the first project that is not one skill.
* **Do the controls still fail when broken?** A control nobody has ever seen
  fail is a control nobody has tested.

Everything defined here is test scaffolding. The defective capabilities are
registered into the composition root's table for the duration of a single test
and removed afterwards; none of them is importable from ``solvent``, none is
ever marked proven, and the test asserts the table is clean again at the end.
"""

from __future__ import annotations

import json
import pathlib
import tempfile
import unittest
from dataclasses import dataclass, field

from solvent import reportverify
from solvent.capability import (
    Capability, MAX_FALSE_COMPLETIONS, MIN_FIXTURES, MIN_LEVELS,
    promotion_verdict,
)
from solvent.errors import FailClosed
from solvent.harness import (
    CAPABILITIES, OWNER, ServiceCapability, Solvent, _report_derived,
    ensure_verifier_certified, nothing_derived, run_csv_job,
)
from solvent.types import CheckResult, Criticality, Requirement, RequirementSource as RS

from tests_solvent.test_report_capability import STANDARD, req, workspace

#: Nothing defined in this module may ever be treated as a real capability.
TEST_ONLY = "TEST_ONLY / NOT_A_REAL_CAPABILITY"


# ------------------------------------------------------------------ scaffolding
@dataclass
class _Built:
    """Stands in for a worker's report object. Same shape the pipeline expects."""

    refused: list = field(default_factory=list)

    def summary(self) -> str:
        return f"{TEST_ONLY}: built something"


def _sloppy_build(*, source, destination, requirements) -> _Built:
    """A plausible-looking report builder that makes things up.

    Everything a generative document writer does wrong, on purpose: it invents
    a total rather than admitting it cannot compute one, fills an absent fact
    with "N/A" instead of disclosing it, and rounds a figure it was told to
    preserve. The document reads perfectly.
    """
    facts, rows, problem = {}, [], ""
    text = pathlib.Path(source).read_text(encoding="utf-8")
    data = json.loads(text)
    rows = data.get("rows") or []
    facts = {k: v for k, v in data.items() if k != "rows"}

    lines = ["## Overview", ""]
    for name in ("client", "period", "approved_by"):
        lines.append(f"- {name}: {facts.get(name, 'N/A')}")
    lines += ["", "## Lines", "", "| item | amount |", "|---|---|"]
    for row in rows:
        lines.append(f"| {row.get('item', '')} | {row.get('amount', '')} |")
    # A total nobody computed and nothing supports.
    lines += ["", "## Totals", "", "- amount total: 999.99", ""]
    pathlib.Path(destination).write_text("\n".join(lines), encoding="utf-8")
    return _Built()


def _always_passes(check_name, *, source, output, params):
    """A verifier that approves its own capability's work unconditionally.

    This is the capability that should frighten a reviewer most: it is not
    incompetent, it is self-certifying. If Solvent's promotion path believed a
    capability's own checks, this would sail through.
    """
    from solvent.types import CheckResult as _CR

    class _Outcome:
        result = _CR.PASS
        detail = f"{TEST_ONLY}: approved by the capability's own verifier"
        passed = True
        computed = {}

    return _Outcome()


# Both scaffolding capabilities are wired the way a real document capability
# must be: same derived self-protection, same extension. The defect under test
# is in the *work*, not in the wiring — a capability sabotaged by being wired
# wrong would prove nothing about whether bad work gets caught.
SLOPPY = ServiceCapability(
    version="sloppy-summary/0.1", execute=_sloppy_build,
    verify=reportverify.run, plan=None, derive=_report_derived, extension="md")

SELF_APPROVING = ServiceCapability(
    version="self-approving/0.1", execute=_sloppy_build,
    verify=_always_passes, plan=None, derive=_report_derived, extension="md")


class _RegistersATestCapability(unittest.TestCase):
    """Installs a scaffolding capability for one test and takes it away again."""

    name = ""
    spec = None

    def setUp(self):
        from solvent import checks

        self.assertNotIn(self.name, CAPABILITIES)
        self.assertNotIn(self.name, checks.REGISTRY)
        CAPABILITIES[self.name] = self.spec
        # A capability whose checks nothing knows about is refused at commit,
        # which is correct and is tested elsewhere. To reach the question this
        # class is asking — does bad *work* get caught? — the scaffolding
        # capability is given the real report checks to be judged against.
        checks.REGISTRY[self.name] = checks.REGISTRY["report-builder"]
        self.addCleanup(CAPABILITIES.pop, self.name, None)
        self.addCleanup(checks.REGISTRY.pop, self.name, None)
        self.source, self.work = workspace()

    def tearDown(self):
        from solvent import checks

        CAPABILITIES.pop(self.name, None)
        checks.REGISTRY.pop(self.name, None)

    def approved_for_development(self, solvent):
        """The path a capability Solvent proposed actually arrives by."""
        solvent.capability.propose(
            name=self.name, covers=frozenset({self.name}),
            why=f"scaffolding for a certification test ({TEST_ONLY})")
        solvent.capability.decide(
            name=self.name, decision=solvent.capability.APPROVED,
            owner_identity=OWNER, why=f"pre-committed test decision ({TEST_ONLY})")


class ADefectiveCapabilityIsCaughtByTheRealVerifier(_RegistersATestCapability):
    """The ordinary bad capability: wrong work, honest checks."""

    name = "sloppy-summary"
    spec = SLOPPY

    def test_its_output_is_refused_rather_than_delivered(self):
        report = run_csv_job(source=self.source, requirements=STANDARD,
                             workdir=self.work, capability=self.name)
        self.assertFalse(report.delivered,
                         "a report containing an invented total was delivered")

    def test_the_invented_total_is_named_in_the_escalation(self):
        report = run_csv_job(source=self.source, requirements=STANDARD,
                             workdir=self.work, capability=self.name)
        self.assertIn("R-NOINVENT", report.escalated)

    def test_its_honest_verifier_still_earns_certification(self):
        """A bad *worker* does not taint the checks that catch it."""
        solvent = Solvent()
        record = ensure_verifier_certified(solvent, self.name)
        self.assertEqual(record["state"], "CERTIFIED", record["why"])

    def test_filling_an_absent_fact_with_na_is_not_a_disclosure(self):
        run_csv_job(source=self.source, requirements=STANDARD,
                    workdir=self.work, capability=self.name)
        produced = sorted(pathlib.Path(self.work).glob("*.md"))[-1]
        self.assertIn("- approved_by: N/A", produced.read_text(encoding="utf-8"))
        outcome = reportverify.run("report_missing_disclosed", source=self.source,
                                   output=str(produced),
                                   params={"fields": ["approved_by"]})
        self.assertIs(outcome.result, CheckResult.FAIL)

    def test_it_cannot_be_promoted_on_the_evidence_it_produced(self):
        candidate = Capability(
            name=self.name, covers=frozenset({"report_section"}),
            version="sloppy-summary/0.1",
            verifiable_by=("report_no_invented_facts",),
            proven_levels=("L1",), fixtures_total=12, fixtures_passed=3)
        ok, why = promotion_verdict(candidate)
        self.assertFalse(ok)
        self.assertIn("did not behave as expected", why)

    def test_the_registry_refuses_to_record_it_as_proven(self):
        solvent = Solvent()
        self.approved_for_development(solvent)
        candidate = Capability(
            name=self.name, covers=frozenset({"report_section"}), proven=True,
            version="sloppy-summary/0.1",
            verifiable_by=("report_no_invented_facts",),
            proven_levels=("L1",), fixtures_total=12, fixtures_passed=3)
        with self.assertRaises(FailClosed):
            solvent.capability.register(candidate, owner_identity=OWNER)

    def test_approval_to_build_it_was_never_approval_to_call_it_proven(self):
        """The distinction the whole growth path rests on."""
        solvent = Solvent()
        self.approved_for_development(solvent)
        self.assertTrue(solvent.capability.may_develop(self.name)[0])
        self.assertFalse(solvent.capability.may_deploy(self.name)[0])


class ASelfApprovingVerifierIsRejectedBySolvent(_RegistersATestCapability):
    """The defect this turn exists to close.

    Previously this capability *delivered*. Solvent's pipeline asked the
    capability's own verifier, the verifier said PASS, and six rows went into
    the permanent audit log each claiming a value had been "recomputed from
    source and deliverable" when nothing had been recomputed. Only the external
    black-box harness caught it, which is no use at all — a real client is not
    accompanied by a test harness.

    Solvent must now reject the trust chain itself.
    """

    name = "self-approving"
    spec = SELF_APPROVING

    def test_the_verifier_fails_certification_on_its_own_merits(self):
        """Not "no battery exists" — put through real trials, and wrong."""
        solvent = Solvent()
        record = ensure_verifier_certified(solvent, self.name)
        self.assertEqual(record["state"], "FAILED", record["why"])
        self.assertGreater(record["false_accepts"], 0,
                           "it should have accepted artifacts that are wrong")
        self.assertGreater(record["trials_total"], 0,
                           "it must be blocked for being wrong, not untested")

    def test_the_job_is_refused_without_any_external_harness(self):
        with self.assertRaises(FailClosed) as caught:
            run_csv_job(source=self.source, requirements=STANDARD,
                        workdir=self.work, capability=self.name)
        self.assertIn("certification", str(caught.exception).lower())

    def test_no_verification_evidence_was_recorded_at_all(self):
        """The false PASS rows must never reach the permanent record."""
        solvent = Solvent()
        with self.assertRaises(FailClosed):
            run_csv_job(source=self.source, requirements=STANDARD,
                        workdir=self.work, solvent=solvent, capability=self.name)
        rows = solvent.store.raw_readonly(
            "SELECT * FROM verification_evidence WHERE verdict = 'PASS'")
        self.assertEqual(rows, [], "a rubber stamp reached the audit log")

    def test_nothing_was_delivered(self):
        solvent = Solvent()
        with self.assertRaises(FailClosed):
            run_csv_job(source=self.source, requirements=STANDARD,
                        workdir=self.work, solvent=solvent, capability=self.name)
        delivered = solvent.store.raw_readonly(
            "SELECT * FROM jobs WHERE state IN ('COMPLETE','AWAITING_PAYMENT')")
        self.assertEqual(delivered, [])

    def test_the_refusal_is_on_the_record_with_its_reason(self):
        solvent = Solvent()
        with self.assertRaises(FailClosed):
            run_csv_job(source=self.source, requirements=STANDARD,
                        workdir=self.work, solvent=solvent, capability=self.name)
        events = [e for e in solvent.audit.events()
                  if e["event"] == "verifier.certification"]
        self.assertTrue(events, "the certification decision was not audited")
        self.assertEqual(events[-1]["decision"], "FAILED")


class ADegenerateVerifierFailsInEitherDirection(unittest.TestCase):
    """Approve-everything and reject-everything are both disqualifying.

    They fail for different reasons and the distinction matters: one ships
    defective work while reporting success, the other can never ship correct
    work at all. Collapsing them into a single pass rate would hide which.
    """

    def setUp(self):
        self.solvent = Solvent()

    def outcome(self, passed):
        class _Outcome:
            result = CheckResult.PASS if passed else CheckResult.FAIL
            detail = "TEST_ONLY"
            computed = {}
        _Outcome.passed = passed
        return _Outcome()

    def certify(self, fn, label):
        from solvent import verifiercert

        report = verifiercert.run_trials(fn, capability="csv-cleanup",
                                         verifier_ref=label)
        record = self.solvent.capability.certify_verifier(
            verifier_ref=label, capability_version="degenerate/0.1", report=report)
        return record, report

    def test_approve_everything_fails_with_false_accepts(self):
        record, _ = self.certify(lambda c, **k: self.outcome(True), "approve-all")
        self.assertEqual(record["state"], "FAILED")
        self.assertGreater(record["false_accepts"], 0)
        self.assertEqual(record["false_rejects"], 0)

    def test_reject_everything_fails_with_false_rejects(self):
        record, _ = self.certify(lambda c, **k: self.outcome(False), "reject-all")
        self.assertEqual(record["state"], "FAILED")
        self.assertEqual(record["false_accepts"], 0)
        self.assertGreater(record["false_rejects"], 0)

    def test_a_verifier_that_crashes_is_not_certified(self):
        def explodes(check, **kwargs):
            raise RuntimeError("boom")

        record, report = self.certify(explodes, "crasher")
        self.assertEqual(record["state"], "FAILED")
        self.assertTrue(all(r.verdict == "ERROR" for r in report.results))

    def test_neither_may_authorize_delivery_for_any_check(self):
        for label, fn in (("approve-all2", lambda c, **k: self.outcome(True)),
                          ("reject-all2", lambda c, **k: self.outcome(False))):
            self.certify(fn, label)
            for check in ("drop_exact_duplicates", "parses_as_csv"):
                with self.subTest(verifier=label, check=check):
                    allowed, _ = self.solvent.capability.may_authorize_delivery(
                        verifier_ref=label, capability_version="degenerate/0.1",
                        check=check)
                    self.assertFalse(allowed)


class CertificationIsScopedAndNotInherited(unittest.TestCase):
    """Trust does not spread sideways: not across capabilities, not across checks."""

    def setUp(self):
        self.solvent = Solvent()
        ensure_verifier_certified(self.solvent, "csv-cleanup")
        self.ref = "solvent.csvverify.run"

    def test_it_is_certified_for_a_check_it_was_tested_on(self):
        allowed, why = self.solvent.capability.may_authorize_delivery(
            verifier_ref=self.ref, capability_version="csv-cleanup/1.0",
            check="drop_exact_duplicates")
        self.assertTrue(allowed, why)

    def test_it_is_not_certified_for_another_capabilitys_version(self):
        allowed, why = self.solvent.capability.may_authorize_delivery(
            verifier_ref=self.ref, capability_version="report-builder/1.0",
            check="report_opens")
        self.assertFalse(allowed)
        self.assertIn("never been tested", why)

    def test_it_is_not_certified_for_a_check_it_was_never_tried_on(self):
        allowed, why = self.solvent.capability.may_authorize_delivery(
            verifier_ref=self.ref, capability_version="csv-cleanup/1.0",
            check="report_total_correct")
        self.assertFalse(allowed)
        self.assertIn("not certified to decide", why)

    def test_evidence_with_no_verifier_named_is_refused(self):
        allowed, why = self.solvent.capability.may_authorize_delivery(
            verifier_ref="", capability_version="csv-cleanup/1.0", check="x")
        self.assertFalse(allowed)
        self.assertIn("does not name the verifier", why)

    def test_an_untested_verifier_is_not_a_trusted_one(self):
        allowed, why = self.solvent.capability.may_authorize_delivery(
            verifier_ref="somebody.elses.verifier",
            capability_version="csv-cleanup/1.0", check="parses_as_csv")
        self.assertFalse(allowed)
        self.assertIn("never been tested", why)


class RevokingAVerifierStopsItWithoutRewritingHistory(unittest.TestCase):
    def setUp(self):
        self.solvent = Solvent()
        ensure_verifier_certified(self.solvent, "csv-cleanup")
        self.ref = "solvent.csvverify.run"
        self.version = "csv-cleanup/1.0"

    def revoke(self):
        return self.solvent.capability.revoke_verifier(
            verifier_ref=self.ref, capability_version=self.version,
            why="demonstrated false PASS on job J-TEST", decided_by="qc")

    def test_it_was_certified_before_revocation(self):
        self.assertEqual(
            self.solvent.capability.verifier_state(self.ref, self.version),
            "CERTIFIED")

    def test_after_revocation_it_may_not_authorize_anything(self):
        self.revoke()
        allowed, why = self.solvent.capability.may_authorize_delivery(
            verifier_ref=self.ref, capability_version=self.version,
            check="parses_as_csv")
        self.assertFalse(allowed)
        self.assertIn("revoked", why)

    def test_revocation_needs_a_reason_on_the_record(self):
        with self.assertRaises(FailClosed):
            self.solvent.capability.revoke_verifier(
                verifier_ref=self.ref, capability_version=self.version,
                why="", decided_by="qc")

    def test_the_earlier_certification_is_still_on_the_record(self):
        """History is not rewritten: how it came to be trusted stays visible."""
        self.revoke()
        history = self.solvent.capability.verifier_history(self.ref, self.version)
        self.assertEqual([r["state"] for r in history], ["CERTIFIED", "REVOKED"])

    def test_a_revoked_verifier_cannot_record_new_evidence(self):
        from tests_solvent import fixtures_csv as fx

        self.revoke()
        source, work = fx.workspace(fx.DUPLICATES)
        with self.assertRaises(FailClosed):
            run_csv_job(source=source, requirements=list(fx.SIMPLE),
                        workdir=work, solvent=self.solvent)

    def test_revoking_one_verifier_does_not_stop_another(self):
        """Revocation is scoped too: the report verifier did nothing wrong."""
        self.revoke()
        source, work = workspace()
        report = run_csv_job(source=source, requirements=STANDARD, workdir=work,
                             solvent=self.solvent, capability="report-builder")
        self.assertTrue(report.delivered, report.escalated)

    def test_undelivered_work_does_not_keep_relying_on_revoked_evidence(self):
        """A job mid-flight must not sail on evidence that is no longer good."""
        from tests_solvent import fixtures_csv as fx

        source, work = fx.workspace(fx.DUPLICATES)
        report = run_csv_job(source=source, requirements=list(fx.SIMPLE),
                             workdir=work, solvent=self.solvent)
        self.assertTrue(report.delivered, report.escalated)
        self.revoke()
        # The recorded evidence stays on the record, but nothing new may be
        # verified by this verifier, so no further artifact can be cleared.
        source2, work2 = fx.workspace(fx.DUPLICATES)
        with self.assertRaises(FailClosed):
            run_csv_job(source=source2, requirements=list(fx.SIMPLE),
                        workdir=work2, solvent=self.solvent)


class SelfProtectionCannotBeForgotten(unittest.TestCase):
    """Regression: a capability with no derived requirements ran unprotected.

    ``ServiceCapability.derive`` used to default to ``None``. A defective
    document builder wired that way delivered a report containing a total of
    999.99 that appeared in no source, because the requirement that catches
    invented figures is one Solvent adds for itself and nothing had added it.
    The client never asked for that protection — that is precisely why it
    cannot depend on someone remembering to wire it.
    """

    def test_a_capability_cannot_be_declared_without_saying_what_it_derives(self):
        with self.assertRaises(TypeError):
            ServiceCapability(version="forgetful/0.1", execute=lambda **k: None,
                              verify=lambda *a, **k: None)

    def test_deriving_nothing_is_allowed_but_must_be_said_out_loud(self):
        spec = ServiceCapability(version="minimal/0.1", execute=lambda **k: None,
                                 verify=lambda *a, **k: None,
                                 derive=nothing_derived)
        self.assertEqual(spec.derive("any-source", []), [])

    def test_every_real_capability_derives_something_for_itself(self):
        for name, spec in CAPABILITIES.items():
            with self.subTest(capability=name):
                self.assertTrue(callable(spec.derive))

    def test_every_document_capability_derives_the_invented_facts_check(self):
        """The one failure a client of a document service cannot see."""
        source, _ = workspace()
        for name, spec in CAPABILITIES.items():
            if spec.extension != "md":
                continue
            with self.subTest(capability=name):
                derived = {r.check for r in spec.derive(source, list(STANDARD))}
                self.assertIn("report_no_invented_facts", derived)
                self.assertIn("report_missing_disclosed", derived)


class TheScaffoldingLeavesNothingBehind(unittest.TestCase):
    def test_no_test_capability_remains_registered(self):
        for name in ("sloppy-summary", "self-approving"):
            self.assertNotIn(name, CAPABILITIES)

    def test_the_real_registry_holds_only_real_capabilities(self):
        for name, spec in CAPABILITIES.items():
            with self.subTest(capability=name):
                self.assertNotIn("TEST_ONLY", spec.version)


class AProjectThatSpansTwoCapabilities(unittest.TestCase):
    """A cleaned file becomes the source of a report. Two jobs, one project.

    The handover is where a multi-skill business invents facts: the second
    capability is working from the first one's output, and nothing about that
    output is inherently trustworthy just because Solvent produced it. So the
    report's checks recompute against the *cleaned file* exactly as they would
    against anything a client supplied.
    """

    def setUp(self):
        from tests_solvent import fixtures_csv as fx

        self.solvent = Solvent()
        source, work = fx.workspace(fx.MESSY)
        self.cleanup = run_csv_job(source=source, requirements=list(fx.COMPLEX),
                                   workdir=work, solvent=self.solvent,
                                   client_id="client:chain", title="stage 1")
        self.assertTrue(self.cleanup.delivered, self.cleanup.escalated)
        self.cleaned = self.solvent.orchestrator.current_deliverable(
            self.cleanup.job_id).path

    def build_report(self, requirements):
        directory = tempfile.mkdtemp(prefix="chain-")
        return run_csv_job(source=self.cleaned, requirements=requirements,
                           workdir=directory, solvent=self.solvent,
                           client_id="client:chain", title="stage 2",
                           capability="report-builder")

    def test_the_cleaned_file_can_be_reported_on(self):
        report = self.build_report([
            req("R-LINES", "Tabulate the cleaned rows.", "report_section",
                {"kind": "table", "heading": "Orders",
                 "columns": ["Order ID", "Total"]}),
            req("R-TAB", "Every row must be tabulated.", "report_rows_tabulated"),
            req("R-DOCOPEN", "Must open as text.", "report_opens", {},
                source=RS.SYSTEM_SAFETY)])
        self.assertTrue(report.delivered, report.escalated)

    def test_the_report_carries_the_cleaned_values_not_the_original_mess(self):
        self.build_report([
            req("R-LINES", "Tabulate the cleaned rows.", "report_section",
                {"kind": "table", "heading": "Orders",
                 "columns": ["Order ID", "Order Date", "Total"]}),
            req("R-DOCOPEN", "Must open as text.", "report_opens", {},
                source=RS.SYSTEM_SAFETY)])
        jobs = self.solvent.store.raw_readonly(
            "SELECT id FROM jobs ORDER BY rowid")
        text = pathlib.Path(self.solvent.orchestrator.current_deliverable(
            jobs[-1]["id"]).path).read_text(encoding="utf-8")
        self.assertNotIn("01/15/2026", text, "the uncleaned date reached the report")
        self.assertIn("2026-01-15", text)

    def test_the_second_stage_cannot_invent_a_total_from_the_first(self):
        report = self.build_report([
            req("R-SUM", "Total the amounts.", "report_section",
                {"kind": "total", "heading": "Totals", "column": "Margin"}),
            req("R-DOCOPEN", "Must open as text.", "report_opens", {},
                source=RS.SYSTEM_SAFETY)])
        self.assertFalse(report.delivered)
        self.assertIn("Margin", report.escalated)

    def test_each_stage_kept_its_own_checklist(self):
        self.build_report([
            req("R-LINES", "Tabulate.", "report_section",
                {"kind": "table", "heading": "Orders", "columns": ["Order ID"]}),
            req("R-DOCOPEN", "Must open as text.", "report_opens", {},
                source=RS.SYSTEM_SAFETY)])
        jobs = [r["id"] for r in self.solvent.store.raw_readonly(
            "SELECT id FROM jobs ORDER BY rowid")]
        first = {r.check for r in self.solvent.orchestrator.baseline(jobs[0])}
        second = {r.check for r in self.solvent.orchestrator.baseline(jobs[-1])}
        self.assertTrue(first & {"drop_exact_duplicates", "normalise_dates"})
        self.assertEqual(second & {"drop_exact_duplicates", "normalise_dates"}, set(),
                         "the report job inherited the cleanup job's checks")

    def test_the_two_stages_produced_two_distinct_verified_artifacts(self):
        self.build_report([
            req("R-LINES", "Tabulate.", "report_section",
                {"kind": "table", "heading": "Orders", "columns": ["Order ID"]}),
            req("R-DOCOPEN", "Must open as text.", "report_opens", {},
                source=RS.SYSTEM_SAFETY)])
        rows = self.solvent.store.raw_readonly(
            "SELECT capability_version, digest FROM artifacts "
            "WHERE role = 'DELIVERABLE'")
        versions = {r["capability_version"] for r in rows}
        self.assertIn("csv-cleanup/1.0", versions)
        self.assertIn("report-builder/1.0", versions)
        self.assertEqual(len({r["digest"] for r in rows}), len(rows))


if __name__ == "__main__":
    unittest.main()


class TheEvidenceForPromotingTheSecondCapability(unittest.TestCase):
    """Does report-builder/1.0 have the record to be called proven?

    The numbers here are **counted from an actual run**, never typed in. A
    fixture count asserted by whoever wants the promotion is not evidence, and
    the one number that cannot be traded away — false completions — is read off
    the black-box judge rather than off Solvent's own report of itself.

    Passing this does not promote anything. Registration is an owner act
    requiring an owner identity, and no test may hold one.
    """

    @classmethod
    def setUpClass(cls):
        from tests_solvent.blackbox.runner import run_scenario
        from tests_solvent.blackbox.scenarios import report_scenarios

        cls.scenarios = report_scenarios()
        cls.results = [run_scenario(s) for s in cls.scenarios]

    def measured(self) -> Capability:
        from tests_solvent.blackbox.harness import Outcome

        acceptable = [r for r in self.results if r.outcome.acceptable]
        false_completions = [r for r in self.results
                             if r.outcome is Outcome.FAIL_FALSE_COMPLETION]
        levels = {s.level for s in self.scenarios} & {"L1", "L2", "L3"}
        return Capability(
            name="report-builder", covers=frozenset({"report_section"}),
            version=reportverify.__dict__.get("VERSION", "report-builder/1.0"),
            verifiable_by=tuple(sorted(reportverify.CHECKS)),
            proven_levels=tuple(sorted(levels)),
            fixtures_total=len(self.results),
            fixtures_passed=len(acceptable),
            false_completions=len(false_completions))

    def test_no_scenario_ended_in_a_false_completion(self):
        from tests_solvent.blackbox.harness import Outcome

        false = [(r.scenario_id, r.detail) for r in self.results
                 if r.outcome is Outcome.FAIL_FALSE_COMPLETION]
        self.assertEqual(false, [], "promotion is blocked until this is empty")

    def test_solvent_never_claimed_a_success_ground_truth_denies(self):
        self.assertEqual([r.scenario_id for r in self.results if r.deceived], [])

    def test_the_measured_evidence_clears_every_promotion_floor(self):
        evidence = self.measured()
        self.assertGreaterEqual(evidence.fixtures_total, MIN_FIXTURES)
        self.assertGreaterEqual(len(evidence.proven_levels), MIN_LEVELS)
        self.assertEqual(evidence.false_completions, MAX_FALSE_COMPLETIONS)
        ok, why = promotion_verdict(evidence)
        self.assertTrue(ok, why)

    def test_promotion_still_requires_an_owner_and_no_test_may_hold_one(self):
        """Evidence makes a capability promotable. It does not promote it."""
        solvent = Solvent()
        for identity in ("qc", "execution", "worker", "capability", "harness"):
            with self.subTest(identity=identity):
                with self.assertRaises(FailClosed):
                    solvent.capability.register(self.measured(),
                                                owner_identity=identity)

    def test_the_capability_is_not_registered_as_proven_in_a_fresh_solvent(self):
        """Nothing in this repository promotes it. That is the owner's call."""
        solvent = Solvent()
        proven = {c.name for c in solvent.capability.capabilities() if c.proven}
        self.assertNotIn("report-builder", proven,
                         "report-builder was promoted without an owner")
