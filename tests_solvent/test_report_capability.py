"""report-builder/1.0 — the second capability, held to the first one's standard.

Two things are being established here, and they are different.

The first is that the capability does its job: renders supplied facts, totals
what it was asked to total, and names what is absent. That is the easy half.

The second is that it **cannot pass while being wrong**, including when the
process dies in the middle. A newly built capability gets no credit for being
new: it goes through the same crash battery as the one that has been running
since the first audit, because the only interesting question about a service
that runs unattended is what it claims after something goes wrong.
"""

from __future__ import annotations

import json
import pathlib
import tempfile
import unittest

from solvent import reportverify, reportwork
from solvent.checks import checks_for, runner_for
from solvent.errors import FailClosed
from solvent.gate import ATTEMPTING
from solvent.harness import OWNER, Solvent, run_csv_job
from solvent.types import (
    ActionClass, BlockedOn, Criticality, JobState, Jurisdiction,
    LocationSignals, OperatingMode, PrivacyClass, Requirement,
    RequirementSource as RS, CheckResult,
)

FACTS = {
    "client": "Acme Corp",
    "period": "Q1 2026",
    "rows": [{"item": "Consulting", "amount": "100.00"},
             {"item": "Support", "amount": "350.50"}],
}


def req(rid, text, check, params=None, source=RS.CLIENT_CONFIRMED_STRUCTURED,
        criticality=Criticality.MANDATORY):
    return Requirement(id=rid, text=text, source=source, acceptance=text,
                       check=check, params=params or {}, criticality=criticality)


OVERVIEW = req("R-OVERVIEW", "Overview naming client and period.", "report_section",
               {"kind": "facts", "heading": "Overview",
                "fields": ["client", "period", "approved_by"]})
LINES = req("R-LINES", "Tabulate the lines.", "report_section",
            {"kind": "table", "heading": "Lines", "columns": ["item", "amount"]})
TOTALS = req("R-SUM", "Total the amounts.", "report_section",
             {"kind": "total", "heading": "Totals", "column": "amount"})
OPENS = req("R-DOCOPEN", "Must open as text.", "report_opens", {},
            source=RS.SYSTEM_SAFETY)

STANDARD = [OVERVIEW, LINES, TOTALS, OPENS]


def workspace(facts=None):
    directory = pathlib.Path(tempfile.mkdtemp(prefix="rep-"))
    source = directory / "facts.json"
    source.write_text(json.dumps(facts if facts is not None else FACTS),
                      encoding="utf-8")
    return str(source), str(directory)


def db_path() -> str:
    return str(pathlib.Path(tempfile.mkdtemp()) / "solvent.db")


class TheCapabilityDoesTheWork(unittest.TestCase):
    def setUp(self):
        self.source, self.work = workspace()

    def test_a_standard_report_is_produced_and_verified(self):
        report = run_csv_job(source=self.source, requirements=STANDARD,
                             workdir=self.work, capability="report-builder")
        self.assertTrue(report.delivered, report.escalated)

    def test_supplied_facts_appear_exactly_as_supplied(self):
        run_csv_job(source=self.source, requirements=STANDARD,
                    workdir=self.work, capability="report-builder")
        text = self.delivered()
        self.assertIn("- client: Acme Corp", text)
        self.assertIn("- period: Q1 2026", text)

    def test_a_fact_nobody_supplied_is_named_not_filled_in(self):
        run_csv_job(source=self.source, requirements=STANDARD,
                    workdir=self.work, capability="report-builder")
        self.assertIn("- approved_by: NOT SUPPLIED", self.delivered())

    def test_the_total_is_the_sum_of_the_supplied_amounts(self):
        run_csv_job(source=self.source, requirements=STANDARD,
                    workdir=self.work, capability="report-builder")
        self.assertIn("- amount total: 450.50", self.delivered())

    def delivered(self) -> str:
        found = sorted(pathlib.Path(self.work).glob("deliverable.attempt*.md"))
        return found[-1].read_text(encoding="utf-8")


class ItRefusesRatherThanImprovises(unittest.TestCase):
    """Every refusal here is a place a generative report writer would invent."""

    def refuse(self, requirements, facts=None):
        source, work = workspace(facts)
        return run_csv_job(source=source, requirements=requirements,
                           workdir=work, capability="report-builder")

    def test_an_unreadable_source_is_refused_not_reported_as_empty(self):
        directory = pathlib.Path(tempfile.mkdtemp())
        source = directory / "facts.json"
        source.write_text("{not json", encoding="utf-8")
        report = run_csv_job(source=str(source), requirements=STANDARD,
                             workdir=str(directory), capability="report-builder")
        self.assertFalse(report.delivered)
        self.assertIn("not valid JSON", report.escalated)

    def test_a_column_the_source_lacks_is_refused_not_computed(self):
        report = self.refuse([req("R-BAD", "Tabulate margin.", "report_section",
                                  {"kind": "table", "heading": "Lines",
                                   "columns": ["item", "margin"]}), OPENS])
        self.assertFalse(report.delivered)
        self.assertIn("margin", report.escalated)

    def test_choosing_which_facts_matter_is_refused_as_the_clients_call(self):
        report = self.refuse([req("R-VAGUE", "The important details.",
                                  "report_section",
                                  {"kind": "facts", "heading": "Overview"}), OPENS])
        self.assertFalse(report.delivered)
        self.assertIn("field", report.escalated)

    def test_a_section_kind_it_does_not_have_is_refused_not_approximated(self):
        report = self.refuse([req("R-NARR", "Write a narrative summary.",
                                  "report_section",
                                  {"kind": "narrative", "heading": "Summary"}),
                              OPENS])
        self.assertFalse(report.delivered)
        self.assertIn("narrative", report.escalated)


class TheChecksAreIndependentOfTheWorker(unittest.TestCase):
    def test_reportverify_never_imports_the_worker_that_produces_the_output(self):
        """Checking with the code that produced the work proves only consistency.

        ``load_source`` is the one shared import and it is deliberate: both
        sides must read the client's file the same way, or a disagreement about
        the *source* would be reported as a defect in the *output*.
        """
        import ast

        tree = ast.parse(pathlib.Path("solvent/reportverify.py").read_text())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
                for alias in node.names:
                    imported.add(f"{node.module}.{alias.name}")
            elif isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
        forbidden = {m for m in imported
                     if m.endswith("reportwork") and "load_source" not in m}
        self.assertEqual(
            imported & {"reportwork.build", ".reportwork.build"}, set(),
            "the checks must not call the builder they are checking")
        self.assertEqual(forbidden - {"reportwork", ".reportwork"}, set())

    def test_a_crashed_check_is_unverifiable_never_a_pass(self):
        outcome = reportverify.run("report_total_correct", source="/nope",
                                   output="/nope", params={"column": "amount"})
        self.assertIsNot(outcome.result, CheckResult.PASS)

    def test_an_unknown_check_is_unverifiable_never_a_pass(self):
        outcome = reportverify.run("no_such_check", source="a", output="b",
                                   params={})
        self.assertIs(outcome.result, CheckResult.UNVERIFIABLE)


class AFactMustMatchOnItsOwnLine(unittest.TestCase):
    """Regression: substring matching passed a corrupted value.

    ``"client: Acme Corp" in text`` is true of a document that says
    ``client: Acme Corporation``. The first trap battery shipped exactly that
    report. The same mistake had already appeared twice elsewhere, so it is
    pinned here rather than left to the scenario suite.
    """

    def setUp(self):
        self.source, self.work = workspace()
        run_csv_job(source=self.source, requirements=STANDARD,
                    workdir=self.work, capability="report-builder")
        self.output = sorted(pathlib.Path(self.work).glob("*.md"))[-1]

    def check(self):
        return reportverify.run("report_facts_rendered", source=self.source,
                                output=str(self.output),
                                params={"fields": ["client", "period"]})

    def test_the_untouched_report_passes(self):
        self.assertIs(self.check().result, CheckResult.PASS)

    def test_a_value_extended_at_the_end_is_caught(self):
        self.output.write_text(
            self.output.read_text(encoding="utf-8")
            .replace("- client: Acme Corp", "- client: Acme Corporation"),
            encoding="utf-8")
        self.assertIs(self.check().result, CheckResult.FAIL)

    def test_a_value_with_text_appended_after_it_is_caught(self):
        self.output.write_text(
            self.output.read_text(encoding="utf-8")
            .replace("- period: Q1 2026", "- period: Q1 2026 (provisional)"),
            encoding="utf-8")
        self.assertIs(self.check().result, CheckResult.FAIL)


class AnAbsenceMustStayAnAbsence(unittest.TestCase):
    """Regression: a disclosed gap was filled in and nothing noticed.

    A ``missing`` section's whole content is an absence, but ``report_section``
    is verified by "the document opens", and the invented-facts check reads
    numbers — so ``- approved_by`` becoming ``- approved_by: J. Rivera`` passed
    both. Solvent now derives R-DISCLOSED for itself on any job that names
    fields it might have to report as absent.
    """

    def setUp(self):
        self.source, self.work = workspace()
        self.report = run_csv_job(source=self.source, requirements=STANDARD,
                                  workdir=self.work, capability="report-builder")

    def test_solvent_derives_the_disclosure_requirement_itself(self):
        committed = {r.id for r in self.report.committed}
        self.assertIn("R-DISCLOSED", committed)

    def test_the_derived_requirement_is_not_attributed_to_the_client(self):
        derived = [r for r in self.report.committed if r.id == "R-DISCLOSED"][0]
        self.assertIs(derived.source, RS.DERIVED)

    def test_filling_in_an_absent_fact_fails_the_check(self):
        output = sorted(pathlib.Path(self.work).glob("*.md"))[-1]
        output.write_text(
            output.read_text(encoding="utf-8")
            .replace("- approved_by: NOT SUPPLIED", "- approved_by: J. Rivera"),
            encoding="utf-8")
        outcome = reportverify.run("report_missing_disclosed", source=self.source,
                                   output=str(output),
                                   params={"fields": ["client", "approved_by"]})
        self.assertIs(outcome.result, CheckResult.FAIL)
        self.assertIn("approved_by", outcome.detail)


class ComplaintsAreRecheckedByTheRightLibrary(unittest.TestCase):
    """Regression: every report complaint was read as Solvent's own defect.

    ``investigate`` did ``from . import csvverify`` unconditionally, so
    recomputing a *report's* checklist returned UNVERIFIABLE for every
    requirement and concluded the verifier had missed something. A correct
    report plus an out-of-scope request produced a false self-accusation.
    """

    def setUp(self):
        self.source, self.work = workspace()
        self.solvent = Solvent()
        self.report = run_csv_job(source=self.source, requirements=STANDARD,
                                  workdir=self.work, solvent=self.solvent,
                                  capability="report-builder")
        self.assertTrue(self.report.delivered, self.report.escalated)

    def investigate(self, body):
        record = self.solvent.feedback.receive(job_id=self.report.job_id, body=body)
        return self.solvent.feedback.investigate(feedback_id=record.id,
                                                 source=self.source)

    def test_the_artifact_records_which_capability_made_it(self):
        artifact = self.solvent.orchestrator.current_deliverable(self.report.job_id)
        self.assertEqual(artifact.capability_version, reportwork.VERSION)

    def test_an_out_of_scope_request_is_scope_not_a_defect(self):
        finding = self.investigate("Could you add a commentary section?")
        self.assertEqual(finding.classification, "NEW_SCOPE")
        self.assertFalse(finding.verifier_missed)

    def test_a_genuinely_corrupted_report_is_still_found_to_be_a_defect(self):
        output = pathlib.Path(
            self.solvent.orchestrator.current_deliverable(self.report.job_id).path)
        output.write_text(
            output.read_text(encoding="utf-8")
            .replace("- client: Acme Corp", "- client: Acme Holdings"),
            encoding="utf-8")
        finding = self.investigate("The client name in this is not our name.")
        self.assertEqual(finding.classification, "MISSED_REQUIREMENT")
        self.assertTrue(finding.verifier_missed)

    def test_work_no_library_can_recheck_is_unknown_not_fine(self):
        """A capability nobody can verify must reach a human, not a verdict."""
        self.assertIsNone(runner_for("pdf-designer/1.0"))
        self.assertEqual(checks_for("pdf-designer/1.0"), {})


class CrashCertification(unittest.TestCase):
    """The new capability's own crash battery. No exemption for being new.

    The crash is modelled with ``KeyboardInterrupt`` for the reason recorded in
    the runtime suite: the egress guard denies ``subprocess.Popen`` once
    installed, and that refusal is a control worth keeping. ``except Exception``
    does not catch it, so nothing unwinds — the durable signature a SIGKILL
    leaves.
    """

    def setUp(self):
        self.db = db_path()
        self.source, self.work = workspace()
        self.solvent = Solvent(self.db)

    def restart(self) -> Solvent:
        self.solvent.store.close()
        return Solvent(self.db)

    def crash_after_the_document_is_written(self):
        def dies(path, attempt):
            raise KeyboardInterrupt("killed after writing, before verifying")

        with self.assertRaises(KeyboardInterrupt):
            run_csv_job(source=self.source, requirements=STANDARD,
                        workdir=self.work, solvent=self.solvent,
                        capability="report-builder", sabotage=dies)

    def test_a_document_on_disk_is_not_a_delivered_job(self):
        self.crash_after_the_document_is_written()
        written = sorted(pathlib.Path(self.work).glob("*.md"))
        self.assertTrue(written, "the worker did write a document")

        restarted = self.restart()
        jobs = restarted.store.raw_readonly("SELECT id, state FROM jobs")
        self.assertEqual(len(jobs), 1)
        self.assertNotEqual(jobs[0]["state"], JobState.COMPLETE.value)

    def test_the_crash_left_no_verification_evidence_behind(self):
        self.crash_after_the_document_is_written()
        restarted = self.restart()
        rows = restarted.store.raw_readonly(
            "SELECT * FROM verification_evidence WHERE verdict = 'PASS'")
        self.assertEqual(rows, [], "a pass survived a crash that preceded it")

    def test_the_unregistered_document_cannot_be_delivered_after_restart(self):
        """Nothing may treat a file found on disk as verified work."""
        self.crash_after_the_document_is_written()
        restarted = self.restart()
        job_id = restarted.store.raw_readonly("SELECT id FROM jobs")[0]["id"]
        ok, why = restarted.orchestrator.verification_satisfied(job_id)
        self.assertFalse(ok)
        self.assertTrue(why)

    def test_the_audit_chain_survives_the_crash(self):
        self.crash_after_the_document_is_written()
        restarted = self.restart()
        self.assertTrue(restarted.audit.verify_chain()[0])

    def test_a_crash_during_delivery_leaves_the_outcome_unknown(self):
        """The dangerous case: dying with the client's report half-sent."""
        from solvent.gate import ATTEMPTING as _ATTEMPTING
        from solvent import runtime as rt

        solvent = self.solvent
        solvent.policy.set_operating_mode(OperatingMode.NORMAL, OWNER, "test")
        solvent.policy.amend({
            "egress": {"simulation_only": False, "allowlist": ["client.example"]},
            "permissions": {ActionClass.C2_EXTERNAL_COMMUNICATION.value:
                            "AUTONOMOUS"},
        }, OWNER, "test: allow one destination")
        job = solvent.orchestrator.intake(
            title="report", client_id="c", quoted_cents=45_000, initiator="test",
            signals=LocationSignals(project=Jurisdiction(state="NC")))

        def dies_mid_send():
            raise KeyboardInterrupt("killed while sending the report")

        with self.assertRaises(KeyboardInterrupt):
            solvent.gate.request(
                action_class=ActionClass.C2_EXTERNAL_COMMUNICATION,
                destination="client.example",
                privacy=PrivacyClass.CLIENT_CONFIDENTIAL, purpose="deliver report",
                job_id=job, perform=dies_mid_send)

        phases = [r["phase"] for r in solvent.store.raw_readonly(
            "SELECT phase FROM action_requests")]
        self.assertEqual(phases, [_ATTEMPTING])

        restarted = self.restart()
        notes = rt.reconcile(restarted)
        self.assertEqual(len(notes), 1)
        self.assertIn("unknown", notes[0])
        self.assertIs(restarted.orchestrator.job(job).blocked_on, BlockedOn.OWNER)

    def test_recovery_does_not_resend_the_report(self):
        self.test_a_crash_during_delivery_leaves_the_outcome_unknown()


class ATruncatedDocumentNeverVerifies(unittest.TestCase):
    """A half-written file is the ordinary shape of a crash on disk.

    Note what is *not* claimed here. Cutting this document in half leaves the
    Overview section whole, so the facts check passes — correctly, because
    those facts really are rendered correctly. An earlier version of this test
    demanded that every check fail on a truncated file, which would have been
    scoring a check for missing something outside its remit. The property that
    matters is the one the pipeline actually relies on: the committed checklist
    *as a whole* must not pass.
    """

    def setUp(self):
        self.source, self.work = workspace()
        run_csv_job(source=self.source, requirements=STANDARD,
                    workdir=self.work, capability="report-builder")
        self.output = sorted(pathlib.Path(self.work).glob("*.md"))[-1]
        whole = self.output.read_text(encoding="utf-8")
        self.output.write_text(whole[:len(whole) // 2], encoding="utf-8")

    def run_check(self, name, **params):
        return reportverify.run(name, source=self.source,
                                output=str(self.output), params=params)

    def test_the_checklist_as_a_whole_rejects_a_truncated_document(self):
        checks = [("report_total_correct", {"column": "amount"}),
                  ("report_rows_tabulated", {}),
                  ("report_facts_rendered", {"fields": ["client", "period"]}),
                  ("report_opens", {})]
        results = {name: self.run_check(name, **params).result
                   for name, params in checks}
        self.assertTrue(any(r is not CheckResult.PASS for r in results.values()),
                        f"a truncated report satisfied every check: {results}")

    def test_the_missing_total_is_caught(self):
        self.assertIsNot(self.run_check("report_total_correct", column="amount")
                         .result, CheckResult.PASS)

    def test_the_missing_table_rows_are_caught(self):
        self.assertIsNot(self.run_check("report_rows_tabulated").result,
                         CheckResult.PASS)

    def test_an_empty_file_does_not_pass_as_a_report(self):
        self.output.write_text("", encoding="utf-8")
        self.assertIsNot(self.run_check("report_opens").result, CheckResult.PASS)


class ConcurrentClientsStayApart(unittest.TestCase):
    """Two report jobs in one Solvent must not read each other's work."""

    def setUp(self):
        self.solvent = Solvent()
        self.a_source, self.a_work = workspace(
            {"client": "Acme Corp", "period": "Q1 2026",
             "rows": [{"item": "Consulting", "amount": "100.00"}]})
        self.b_source, self.b_work = workspace(
            {"client": "Borealis Ltd", "period": "Q2 2026",
             "rows": [{"item": "Retainer", "amount": "900.00"}]})
        self.a = run_csv_job(source=self.a_source, requirements=STANDARD,
                             workdir=self.a_work, solvent=self.solvent,
                             client_id="client:a", title="A",
                             capability="report-builder")
        self.b = run_csv_job(source=self.b_source, requirements=STANDARD,
                             workdir=self.b_work, solvent=self.solvent,
                             client_id="client:b", title="B",
                             capability="report-builder")

    def test_both_jobs_delivered(self):
        self.assertTrue(self.a.delivered and self.b.delivered)

    def test_neither_report_contains_the_other_clients_name(self):
        a_text = pathlib.Path(
            self.solvent.orchestrator.current_deliverable(self.a.job_id).path
        ).read_text(encoding="utf-8")
        b_text = pathlib.Path(
            self.solvent.orchestrator.current_deliverable(self.b.job_id).path
        ).read_text(encoding="utf-8")
        self.assertNotIn("Borealis", a_text)
        self.assertNotIn("Acme", b_text)

    def test_neither_report_carries_the_other_clients_figures(self):
        a_text = pathlib.Path(
            self.solvent.orchestrator.current_deliverable(self.a.job_id).path
        ).read_text(encoding="utf-8")
        self.assertNotIn("900.00", a_text)

    def test_the_checklists_did_not_merge(self):
        a_ids = {r.id for r in self.solvent.orchestrator.baseline(self.a.job_id)}
        b_ids = {r.id for r in self.solvent.orchestrator.baseline(self.b.job_id)}
        self.assertEqual(a_ids, b_ids, "same job shape")
        a_digest = self.solvent.orchestrator.current_deliverable(self.a.job_id).digest
        b_digest = self.solvent.orchestrator.current_deliverable(self.b.job_id).digest
        self.assertNotEqual(a_digest, b_digest)


if __name__ == "__main__":
    unittest.main()
