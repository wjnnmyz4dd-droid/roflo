"""Regressions from the external adversarial audit.

Each class below is a defect that was found by attacking the system rather than
by testing it — and each would have shipped without these.
"""

from __future__ import annotations

import pathlib
import tempfile
import unittest

from solvent.errors import FailClosed
from solvent.harness import OWNER, Solvent, run_csv_job
from solvent.services import SPREADSHEET_CLEANUP
from solvent.types import (
    JobState, Jurisdiction, LocationSignals, Requirement, RequirementSource as RS,
)
from tests_solvent import fixtures_csv as fx


def job_on(solvent, title, client):
    return solvent.orchestrator.intake(
        title=title, client_id=client, quoted_cents=18_000, initiator=OWNER,
        signals=LocationSignals(project=Jurisdiction(state="NC")))


class CrossJobRequirementIsolation(unittest.TestCase):
    """CRITICAL. Requirement ids were a global namespace.

    Two clients using the same checklist template — the normal case for a
    repeatable service — collided on the primary key. With INSERT OR REPLACE,
    the second job silently took over the first job's row: client A's committed
    requirement changed owner and vanished from A's baseline. A requirement
    disappearing from a committed baseline is the exact failure the checklist
    exists to prevent, arriving by a different door.
    """

    def setUp(self):
        self.s = Solvent()                      # one Solvent, two clients
        self.a = job_on(self.s, "A", "client:alpha")
        self.b = job_on(self.s, "B", "client:beta")

    def test_two_jobs_may_use_the_same_requirement_id(self):
        self.s.orchestrator.add_requirement(self.a, fx.DEDUPE)
        self.s.orchestrator.add_requirement(self.b, fx.DEDUPE)
        self.assertEqual([r.id for r in self.s.orchestrator.requirement_records(self.a)],
                         ["R-DEDUPE"])
        self.assertEqual([r.id for r in self.s.orchestrator.requirement_records(self.b)],
                         ["R-DEDUPE"])

    def test_a_second_job_cannot_steal_a_committed_requirement(self):
        self.s.orchestrator.add_requirement(self.a, fx.DEDUPE)
        self.s.orchestrator.commit_requirements(job_id=self.a, initiator=OWNER)
        self.s.orchestrator.add_requirement(self.b, fx.DEDUPE)
        self.s.orchestrator.commit_requirements(job_id=self.b, initiator=OWNER)

        self.assertEqual([r.id for r in self.s.orchestrator.baseline(self.a)],
                         ["R-DEDUPE"])
        rows = self.s.store.raw_readonly(
            "SELECT job_id FROM requirements WHERE id = 'R-DEDUPE'")
        self.assertEqual({r["job_id"] for r in rows}, {self.a, self.b})

    def test_the_commitment_guard_is_scoped_to_its_own_job(self):
        """A's commitment must not stop B from drafting the same id."""
        self.s.orchestrator.add_requirement(self.a, fx.DEDUPE)
        self.s.orchestrator.commit_requirements(job_id=self.a, initiator=OWNER)
        self.s.orchestrator.add_requirement(self.b, fx.DEDUPE)   # must not raise
        with self.assertRaises(FailClosed):
            self.s.orchestrator.add_requirement(self.a, fx.DEDUPE)

    def test_two_full_jobs_run_side_by_side_without_interfering(self):
        s = Solvent()
        srcA, workA = fx.workspace(fx.MESSY)
        srcB, workB = fx.workspace(fx.DUPLICATES)
        a = run_csv_job(source=srcA, requirements=fx.COMPLEX, workdir=workA,
                        solvent=s, client_id="client:alpha")
        b = run_csv_job(source=srcB, requirements=fx.SIMPLE, workdir=workB,
                        solvent=s, client_id="client:beta")
        self.assertTrue(a.delivered, a.escalated)
        self.assertTrue(b.delivered, b.escalated)
        self.assertNotEqual(a.artifacts[-1].digest, b.artifacts[-1].digest)
        self.assertEqual(len(s.orchestrator.artifacts(a.job_id, role="DELIVERABLE")), 1)

    def test_evidence_does_not_cross_between_jobs(self):
        s = Solvent()
        srcA, workA = fx.workspace(fx.MESSY)
        a = run_csv_job(source=srcA, requirements=fx.COMPLEX, workdir=workA,
                        solvent=s, client_id="client:alpha")
        b = job_on(s, "B", "client:beta")
        # Register A's exact file as B's deliverable: same bytes, same digest.
        artifact = s.orchestrator.register_artifact(
            job_id=b, role="DELIVERABLE", path=a.artifacts[-1].path)
        self.assertEqual(artifact.digest, a.artifacts[-1].digest)
        self.assertEqual(s.audit.passes_for_artifact(b, artifact.digest), {},
                         "A's evidence was visible to B")

    def test_a_legacy_database_migrates_without_losing_a_requirement(self):
        import sqlite3

        db = str(pathlib.Path(tempfile.mkdtemp()) / "old.db")
        conn = sqlite3.connect(db)
        conn.executescript("""
            CREATE TABLE requirements (
              id TEXT PRIMARY KEY, job_id TEXT NOT NULL, text TEXT NOT NULL,
              source TEXT NOT NULL, confirmed_by TEXT NOT NULL DEFAULT '',
              acceptance TEXT NOT NULL DEFAULT '',
              check_name TEXT NOT NULL DEFAULT '', params TEXT NOT NULL DEFAULT '{}',
              criticality TEXT NOT NULL DEFAULT 'MANDATORY',
              status TEXT NOT NULL DEFAULT 'DRAFT',
              supersedes TEXT NOT NULL DEFAULT '', committed_at TEXT);
            INSERT INTO requirements(id,job_id,text,source,check_name,status)
              VALUES('R-OLD','job_legacy','legacy','OWNER_CONFIRMED',
                     'parses_as_csv','COMMITTED');
        """)
        conn.commit()
        conn.close()

        from solvent.store import Store
        store = Store(db)
        rows = store.raw_readonly("SELECT id, job_id, status FROM requirements")
        keys = {r["name"] for r in store.raw_readonly("PRAGMA table_info(requirements)")
                if r["pk"]}
        store.close()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["job_id"], "job_legacy")
        self.assertEqual(keys, {"job_id", "id"})


class BinarySourceIsNotASpreadsheet(unittest.TestCase):
    """CRITICAL. A real .xlsx was accepted, 'cleaned', verified and delivered.

    A zip header decodes as UTF-8, so "it decoded" is not "it is a CSV". The file
    was read as one enormous text row, transformed, and passed every check —
    because the checks compare the source to the output and both were the same
    garbage. A false completion: wrong work shipped while reporting success.
    """

    def run_bytes(self, payload):
        directory = pathlib.Path(tempfile.mkdtemp())
        source = directory / "in.dat"
        source.write_bytes(payload)
        return run_csv_job(source=str(source),
                           requirements=[fx.DEDUPE, fx.RECONCILE, fx.OPENS],
                           workdir=str(directory))

    def test_a_real_xlsx_is_refused(self):
        report = self.run_bytes(b"PK\x03\x04" + b"\x00" * 200)
        self.assertFalse(report.delivered)
        self.assertIn("not a CSV", report.escalated)

    def test_a_pdf_is_refused(self):
        self.assertFalse(self.run_bytes(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n").delivered)

    def test_a_png_is_refused(self):
        self.assertFalse(self.run_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64).delivered)

    def test_a_headerless_file_is_refused(self):
        report = self.run_bytes(b",,,\n1,2,3\n")
        self.assertFalse(report.delivered)
        self.assertIn("no column names", report.escalated)

    def test_a_wrongly_delimited_file_is_refused_rather_than_guessed(self):
        report = self.run_bytes(b"Order ID\tTotal\n1\t250.00\n1\t250.00\n")
        self.assertFalse(report.delivered)
        self.assertIn("comma-separated", report.escalated)

    def test_an_ordinary_csv_still_passes(self):
        report = self.run_bytes(fx.DUPLICATES.encode())
        self.assertTrue(report.delivered, report.escalated)


class TheContractPromisesOnlyWhatExists(unittest.TestCase):
    def test_only_csv_is_advertised(self):
        self.assertEqual(SPREADSHEET_CLEANUP.supported_formats, ("csv",))

    def test_proving_csv_does_not_imply_adjacent_formats(self):
        for fmt in ("xlsx", "tsv", "pdf", "docx", "pptx", "json"):
            with self.subTest(fmt=fmt):
                ok, why = SPREADSHEET_CLEANUP.conforms(
                    requested_formats=[fmt], requested=["remove duplicate rows"])
                self.assertFalse(ok, f"{fmt} was accepted")


class BlankLinesAreNotRows(unittest.TestCase):
    """HIGH. A trailing newline made a job unprocessable.

    csv.reader yields [] for a blank line, and a file ending in a newline is the
    normal shape. Treating that as a data row let it survive deduplication,
    appear in the output as a blank line, then fail the 'parses' check as ragged
    — burning all three correction attempts on a file that was never malformed.
    """

    def run_text(self, text, requirements=None):
        directory = pathlib.Path(tempfile.mkdtemp())
        source = directory / "s.csv"
        source.write_text(text, encoding="utf-8")
        return run_csv_job(source=str(source),
                           requirements=requirements or [fx.DEDUPE, fx.RECONCILE,
                                                         fx.OPENS],
                           workdir=str(directory))

    def test_a_trailing_blank_line_does_not_break_the_job(self):
        report = self.run_text("Order ID,Total\n1,250.00\n1,250.00\n\n")
        self.assertTrue(report.delivered, report.escalated)
        self.assertEqual(report.attempts, 1)

    def test_blank_lines_do_not_appear_in_the_deliverable(self):
        report = self.run_text("Order ID,Total\n1,250.00\n\n\n1,250.00\n")
        text = pathlib.Path(report.artifacts[-1].path).read_text()
        self.assertNotIn("\n\n", text)

    def test_blank_lines_are_not_counted_as_rows(self):
        report = self.run_text("Order ID,Total\n1,250.00\n2,100.00\n\n")
        self.assertIn("2 rows in", report.work_summary)

    def test_a_row_of_empty_values_is_still_a_row(self):
        """Zero fields is a blank line. Correct width with empty values is data."""
        report = self.run_text("Order ID,Total\n1,\n2,\n1,\n")
        self.assertTrue(report.delivered, report.escalated)
        text = pathlib.Path(report.artifacts[-1].path).read_text()
        self.assertIn("1,\n", text)
        self.assertIn("2,\n", text)


class TheDeliveryGateRefusesOnItsOwn(unittest.TestCase):
    """TEST WEAKNESS. Every record_delivery test used a verified job.

    A mutation probe removed the refusal from record_delivery entirely and the
    whole suite stayed green: the last enforcement point before work leaves
    Solvent had no test asserting it enforces anything. The pipeline checked
    first, so nothing noticed — until a future caller skips the pipeline.
    """

    def test_delivery_is_refused_without_a_checklist_or_an_artifact(self):
        s = Solvent()
        job = job_on(s, "t", "c")
        for target in (JobState.QUALIFYING, JobState.ACCEPTED, JobState.EXECUTING,
                       JobState.VERIFYING, JobState.READY_FOR_DELIVERY):
            s.orchestrator._transition(job, target, why="setup", initiator="test")
        with self.assertRaises(FailClosed) as caught:
            s.orchestrator.record_delivery(job_id=job, initiator="x",
                                           gate_request_id="act")
        self.assertIn("refusing to deliver unverified work", str(caught.exception))
        self.assertIs(s.orchestrator.job(job).state, JobState.READY_FOR_DELIVERY)

    def test_delivery_is_refused_when_the_artifact_changed_after_verification(self):
        src, work = fx.workspace(fx.MESSY)
        s = Solvent()
        report = run_csv_job(source=src, requirements=fx.COMPLEX, workdir=work,
                             solvent=s)
        path = pathlib.Path(report.artifacts[-1].path)
        path.write_text(path.read_text() + " ", encoding="utf-8")
        with self.assertRaises(FailClosed) as caught:
            s.orchestrator.record_delivery(job_id=report.job_id, initiator="x",
                                           gate_request_id="act")
        self.assertIn("changed since it was registered", str(caught.exception))


if __name__ == "__main__":
    unittest.main()


class UnrecognisedWorkIsRefused(unittest.TestCase):
    """TEST WEAKNESS. Default-deny had no test for its central case.

    Two turns ago the scope matcher was inverted: a request is in scope only
    when it names work this service performs, so anything unrecognised is
    refused rather than accepted by omission. Every test written for it covered
    *barred* concepts — tax work, valuations, macros — and none covered the
    merely unknown. A mutation probe deleted the unrecognised branch entirely
    and the whole suite stayed green, which means the half of the fix that
    actually inverted the default was never asserted.
    """

    def refuse(self, ask):
        return SPREADSHEET_CLEANUP.conforms(requested_formats=["csv"],
                                            requested=[ask])

    def test_work_this_service_does_not_perform_is_refused(self):
        for ask in ("translate the file into French",
                    "fill in the missing values",
                    "merge duplicate customers who look similar",
                    "build me a dashboard from this data",
                    "make it look nicer",
                    "add a column with whatever seems useful"):
            with self.subTest(ask=ask):
                ok, why = self.refuse(ask)
                self.assertFalse(ok, f"{ask!r} was accepted")
                self.assertIn("not recognised", why)

    def test_an_unrecognised_ask_is_refused_even_beside_a_recognised_one(self):
        """Half a job in scope is not a job in scope."""
        ok, why = SPREADSHEET_CLEANUP.conforms(
            requested_formats=["csv"],
            requested=["remove duplicate rows", "and translate it into French"])
        self.assertFalse(ok)
        self.assertIn("not recognised", why)

    def test_recognised_work_still_conforms(self):
        """The guard must refuse the unknown without refusing the ordinary."""
        for ask in ("remove duplicate records", "normalise the date fields",
                    "make the region names consistent", "sort the rows by order date",
                    "do not change the invoice amounts"):
            with self.subTest(ask=ask):
                self.assertTrue(self.refuse(ask)[0], f"{ask!r} was refused")
