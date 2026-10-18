"""Regression tests for the six engineering defects, written to fail first.

Each class reproduces a defect found by measurement in the audit reports
at 91987f5 and 3c7d6d0, and states the behaviour the repair must produce.
They are written against the defect, not against the fix, so that a fix
which merely moves the problem does not pass them.
"""

from __future__ import annotations

import pathlib
import tempfile
import unittest

from solvent import independent, xlsx
from solvent.types import (Criticality, JobState, Requirement,
                           RequirementSource)


def req(rid, check, params=None):
    return Requirement(
        id=rid, text=check, source=RequirementSource.CLIENT_CONFIRMED_STRUCTURED,
        acceptance=check, check=check, params=params or {},
        criticality=Criticality.MANDATORY)


def workbook(path: pathlib.Path, sheets: dict) -> pathlib.Path:
    """Write a real .xlsx with the named sheets, via openpyxl."""
    import openpyxl
    book = openpyxl.Workbook()
    book.remove(book.active)
    for name, rows in sheets.items():
        sheet = book.create_sheet(title=name)
        for row in rows:
            sheet.append(list(row))
    book.save(path)
    return path


HEADER = ("Item", "Client ref", "Units", "Rate")


class ConsolidatedSheetsAreNotFabrication(unittest.TestCase):
    """5A. The reviewer read only the first sheet of a consolidated job.

    Measured at 3c7d6d0: three Client ref values present in the second
    source sheet were reported as "value(s) the source never had".
    """

    def setUp(self):
        self.box = tempfile.TemporaryDirectory()
        self.addCleanup(self.box.cleanup)
        self.dir = pathlib.Path(self.box.name)
        self.source = workbook(self.dir / "source.xlsx", {
            "Q1 data": [HEADER,
                        ("Audit 01", "CR-4243", 343, 277.24),
                        ("Audit 03", "CR-2266", 25, 493.70)],
            "Q2 data": [HEADER,
                        ("Hosting 06", "CR-5486", 105, 412.03),
                        ("Audit 02", "CR-6484", 42, 425.27)],
        })
        # A correctly consolidated deliverable: every row from both sheets.
        self.delivered = [
            ["Item", "Client ref"],
            ["Audit 01", "CR-4243"],
            ["Audit 03", "CR-2266"],
            ["Hosting 06", "CR-5486"],
            ["Audit 02", "CR-6484"],
        ]
        self.requirements = [
            req("r1", "consolidate_sheets", {"sheets": ["Q1 data", "Q2 data"]}),
            req("r2", "preserve_columns", {"columns": ["Client ref"]}),
        ]

    def findings(self, delivered=None):
        import openpyxl
        return independent._recompute_independently(
            openpyxl, "xlsx", str(self.source),
            delivered if delivered is not None else self.delivered,
            self.requirements)

    def provenance(self, delivered=None):
        return [f for f in self.findings(delivered)
                if "came from the source" in f[0]]

    def test_values_from_the_second_sheet_are_not_invented(self):
        found = self.provenance()
        self.assertTrue(found, "the provenance check did not run at all")
        what, agreed, detail = found[0]
        self.assertTrue(
            agreed,
            "a correctly consolidated deliverable was reported as inventing "
            f"values: {detail}. Every value came from a declared source "
            "sheet.")

    def test_a_genuinely_invented_value_is_still_caught(self):
        """The repair must not become 'accept anything'."""
        tampered = [list(r) for r in self.delivered]
        tampered.append(["Ghost 99", "CR-0000"])
        found = self.provenance(tampered)
        self.assertTrue(found)
        what, agreed, detail = found[0]
        self.assertFalse(
            agreed,
            "a value present in no source sheet was accepted; the repair "
            "has turned the provenance check off rather than widening it")
        self.assertIn("1 value", detail)

    def test_a_value_from_an_undeclared_sheet_is_still_invented(self):
        """Only the sheets the checklist authorises count as the source."""
        self.source = workbook(self.dir / "extra.xlsx", {
            "Q1 data": [HEADER, ("Audit 01", "CR-4243", 343, 277.24)],
            "Q2 data": [HEADER, ("Hosting 06", "CR-5486", 105, 412.03)],
            "Scratch": [HEADER, ("Private 00", "CR-9999", 1, 1.0)],
        })
        sneaky = [["Item", "Client ref"], ["Audit 01", "CR-4243"],
                  ["Private 00", "CR-9999"]]
        found = self.provenance(sneaky)
        self.assertTrue(found)
        self.assertFalse(
            found[0][1],
            "a value from a sheet the checklist never authorised was "
            "accepted as supplied")


class CompletedJobsReleaseTheirSlot(unittest.TestCase):
    """1. COMPLETE was not terminal, so finished jobs held capacity for good.

    Measured at b08d52e: three COMPLETE jobs held all three slots and every
    later opportunity was refused.
    """

    def test_complete_does_not_occupy_capacity(self):
        from solvent import qualification
        occupying = qualification.OCCUPIES_CAPACITY
        self.assertNotIn(
            JobState.COMPLETE, occupying,
            "a finished job still counts against the commitment ceiling, so "
            "the shop seizes after max_concurrent_jobs lifetime successes")

    def test_work_in_flight_still_occupies_capacity(self):
        """The inverse, so the fix cannot pass by releasing everything."""
        from solvent import qualification
        occupying = qualification.OCCUPIES_CAPACITY
        for state in (JobState.EXECUTING, JobState.VERIFYING,
                      JobState.BLOCKED, JobState.READY_FOR_DELIVERY,
                      JobState.REVISION_REQUESTED):
            self.assertIn(
                state, occupying,
                f"{state.value} stopped occupying capacity; work still owed "
                "must keep its slot")

    def test_an_unpaid_invoice_does_not_hold_a_processing_slot(self):
        """A deliberate call, recorded here rather than left implicit.

        I first wrote this test asserting the opposite. AWAITING_PAYMENT
        means the work has shipped and the money has not arrived: labour is
        not owed, so a *processing* slot buys nothing. Holding one would
        re-seize the shop after 20 unpaid invoices, which is the same defect
        in a new place, while the unpaid-exposure limit already governs the
        money and does it better by reasoning about amounts.

        The debt is untouched: the payment row, the invoice and the audit
        trail all outlive the slot.
        """
        from solvent import qualification
        self.assertNotIn(JobState.AWAITING_PAYMENT,
                         qualification.OCCUPIES_CAPACITY)


class PerSkillPoolsAreEnforced(unittest.TestCase):
    """Repair 1: one skill must not consume another skill's inventory.

    Measured before this: offering a backlog grouped by skill let
    csv-cleanup take every global slot and four of five skills delivered
    nothing. The capacity controller already counted reservations per
    capability and enforced a per-pipeline limit; nothing called it.
    """

    def setUp(self):
        from solvent.harness import Solvent
        self.s = Solvent(":memory:")
        self.addCleanup(self.s.store.close)

    def budget(self, **overrides):
        base = {"max_owed_seconds": 20 * 3600, "max_concurrent_jobs": 4,
                "pipeline_target": 4, "replenish_at": 2,
                "max_committed_jobs": 20,
                # Required: the validator refuses a budget that claims the
                # whole machine, because MetaTrader 5 shares it.
                "cpu_share": 0.5,
                "max_execution_workers": 5,
                "max_verification_workers": 5}
        base.update(overrides)
        self.s.policy.amend({"capacity": base}, "owner:demo", "test")

    def reserve(self, job_id, capability):
        return self.s.capacity.reserve(
            3600.0, kind="EXECUTE", job_id=job_id, capability=capability,
            idempotency_key=f"commit:{job_id}", why="test")

    def test_a_pipeline_fills_at_its_own_ceiling(self):
        self.budget()
        for n in range(4):
            self.assertTrue(self.reserve(f"job_a{n}", "csv-cleanup").allowed)
        self.assertFalse(
            self.reserve("job_a4", "csv-cleanup").allowed,
            "a fifth job entered a pipeline whose ceiling is four")

    def test_a_full_pipeline_does_not_block_another_skill(self):
        """The starvation the repair exists to prevent."""
        self.budget()
        for n in range(4):
            self.reserve(f"job_a{n}", "csv-cleanup")
        self.assertTrue(
            self.reserve("job_b0", "data-entry").allowed,
            "a full csv-cleanup pipeline blocked data-entry, which is the "
            "cross-skill starvation this repair removes")

    def test_the_same_acceptance_twice_takes_one_slot(self):
        self.budget()
        self.reserve("job_dup", "data-entry")
        self.reserve("job_dup", "data-entry")
        self.assertEqual(self.s.capacity.jobs_in_flight("data-entry"), 1,
                         "a replayed acceptance took a second slot")

    def test_a_settled_job_gives_its_slot_back(self):
        self.budget()
        self.reserve("job_done", "report-builder")
        self.assertEqual(self.s.capacity.jobs_in_flight("report-builder"), 1)
        self.assertEqual(self.s.capacity.release_job("job_done"), 1)
        self.assertEqual(self.s.capacity.jobs_in_flight("report-builder"), 0)

    def test_releasing_twice_is_harmless(self):
        """Both the cycle and startup reconciliation may try."""
        self.budget()
        self.reserve("job_twice", "data-entry")
        self.assertEqual(self.s.capacity.release_job("job_twice"), 1)
        self.assertEqual(self.s.capacity.release_job("job_twice"), 0)

    def test_an_unconfigured_budget_does_not_stop_the_shop(self):
        """No budget set is not the same sentence as 'this pipeline is full'."""
        verdict = self.reserve("job_nobudget", "data-entry")
        self.assertFalse(verdict.allowed)
        self.assertEqual(verdict.refusal.name, "NOT_CONFIGURED",
                         "an unset budget must be distinguishable from a full "
                         "pipeline, or qualification cannot tell them apart")


class IndependentCsvReview(unittest.TestCase):
    """Repair 2: a second opinion on a .csv that is genuinely second.

    The worker parses with the standard library's ``csv``. A checker
    using ``csv`` would be Solvent reading its own output twice, which
    is why the reviewer refused .csv outright and 34 of 100 contracts
    stopped at verification-eligibility. CleverCSV parses with its own
    C parser.
    """

    HEADER = ("Item", "Client ref", "Units")

    def setUp(self):
        self.box = tempfile.TemporaryDirectory()
        self.addCleanup(self.box.cleanup)
        self.dir = pathlib.Path(self.box.name)

    def csv_file(self, name: str, text: str) -> pathlib.Path:
        path = self.dir / name
        path.write_text(text, encoding="utf-8")
        return path

    def read(self, path):
        import clevercsv
        return independent._read_independently(clevercsv, "csv", str(path))

    def findings(self, source, delivered, requirements):
        import clevercsv
        return independent._recompute_independently(
            clevercsv, "csv", str(source), delivered, requirements)

    # ---------------------------------------------------------- the reader
    def test_a_plain_file_is_read(self):
        path = self.csv_file("plain.csv",
                             "Item,Client ref,Units\nAudit,CR-1,3\n")
        rows, problem = self.read(path)
        self.assertEqual(problem, "")
        self.assertEqual(rows[0], ["Item", "Client ref", "Units"])
        self.assertEqual(rows[1], ["Audit", "CR-1", "3"])

    def test_a_quoted_delimiter_does_not_split_a_field(self):
        path = self.csv_file(
            "quoted.csv",
            'Item,Client ref,Units\n"Audit, phase two",CR-1,3\n')
        rows, problem = self.read(path)
        self.assertEqual(problem, "")
        self.assertEqual(rows[1][0], "Audit, phase two")
        self.assertEqual(len(rows[1]), 3, f"the row split wrongly: {rows[1]}")

    def test_an_embedded_newline_stays_in_its_field(self):
        path = self.csv_file(
            "newline.csv",
            'Item,Client ref,Units\n"Audit\nphase two",CR-1,3\n')
        rows, problem = self.read(path)
        self.assertEqual(problem, "")
        self.assertEqual(len(rows), 2, f"the record split across lines: {rows}")
        self.assertIn("\n", rows[1][0])

    def test_a_semicolon_dialect_is_worked_out_not_assumed(self):
        path = self.csv_file("semi.csv",
                             "Item;Client ref;Units\nAudit;CR-1;3\n")
        rows, problem = self.read(path)
        self.assertEqual(problem, "")
        self.assertEqual(len(rows[0]), 3,
                         "the reader was not told the delimiter and did not "
                         f"work it out: {rows[0]}")

    def test_empty_fields_survive(self):
        path = self.csv_file("empty.csv",
                             "Item,Client ref,Units\nAudit,,3\n")
        rows, _ = self.read(path)
        self.assertEqual(rows[1], ["Audit", "", "3"])

    # ------------------------------------------------- what it must catch
    def requirement(self):
        return [req("r1", "preserve_columns", {"columns": ["Client ref"]})]

    def test_a_correct_deliverable_passes(self):
        source = self.csv_file(
            "src.csv", "Item,Client ref,Units\nA,CR-1,3\nB,CR-2,4\n")
        delivered = [["Item", "Client ref"], ["A", "CR-1"], ["B", "CR-2"]]
        found = [f for f in self.findings(source, delivered, self.requirement())
                 if "came from the source" in f[0]]
        self.assertTrue(found, "the provenance check did not run on a .csv")
        self.assertTrue(found[0][1], found[0][2])

    def test_a_fabricated_value_is_caught(self):
        source = self.csv_file(
            "src2.csv", "Item,Client ref,Units\nA,CR-1,3\n")
        delivered = [["Item", "Client ref"], ["A", "CR-1"], ["Ghost", "CR-999"]]
        found = [f for f in self.findings(source, delivered, self.requirement())
                 if "came from the source" in f[0]]
        self.assertTrue(found)
        self.assertFalse(found[0][1],
                         "a value the source never had was accepted")

    def test_an_altered_value_is_caught(self):
        """A changed reference is a value the source never had."""
        source = self.csv_file(
            "src3.csv", "Item,Client ref,Units\nA,CR-1,3\n")
        delivered = [["Item", "Client ref"], ["A", "CR-7"]]
        found = [f for f in self.findings(source, delivered, self.requirement())
                 if "came from the source" in f[0]]
        self.assertTrue(found)
        self.assertFalse(found[0][1], "an altered value was accepted")

    def test_an_unreadable_file_refuses_rather_than_passing(self):
        path = self.dir / "gone.csv"
        rows, problem = self.read(path)
        self.assertEqual(rows, [])
        self.assertTrue(problem, "an unreadable file produced no complaint, "
                                 "which would read as agreement")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
