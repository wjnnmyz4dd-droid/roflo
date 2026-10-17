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


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
