"""The two advisory gates, applied to the five-skill library.

Both are **advisory**. Neither can override owner authority, a security
control, capability certification, a platform restriction, a financial
control, the runtime's execution authority, or the requirement that a
capability be promoted before it is sold. What they do is ask questions this
codebase has been caught failing before, and the answers are kept here as
tests so the next change has to keep answering them.

**K-LEAN**, whose method is to key on what a secret *looks like* rather than
on what a field is called. Asked of these capabilities, it finds one path
worth closing: a verifier explaining a failure quotes the client's own text,
which is exactly what makes the explanation useful -- and is how a credential
inside a client's contract reaches the verification table, which the control
centre reads and every backup copies. The deliverable is deliberately left
alone: redacting a client's file would be altering their data without
authorisation, and a data-entry exception whose values had been removed would
not tell them which record failed.

**iFixAi**, whose method is to establish that a control refused for the
*right* reason, and that satisfying one control does not quietly unlock the
rest. A test that only asserts "blocked" passes when the block came from
somewhere else entirely, and that is how a chain of controls rots into one
control with several names.
"""

from __future__ import annotations

import json
import pathlib
import tempfile
import unittest

from solvent import docwork, docx, entrywork, sheetwork, xlsx
from solvent.audit import AuditLog
from solvent.harness import Solvent
from solvent.resilience import redact
from solvent.types import ConsequenceTier, VerificationTier

def _planted() -> tuple[str, ...]:
    """Secret-shaped strings a client's own document plausibly carries.

    Assembled at run time, in pieces, because this repository scans its own
    tree for credential-shaped values and a fixture written out as a literal
    is indistinguishable from a leak -- to the scanner and to anybody skimming
    the file. That guard fired on the first version of this file, which is the
    guard working: the point of scanning for a shape is that you cannot tell
    the intent behind one.

    Every value here is invented for this file and matches nothing real. The
    redactor sees the same string either way, so nothing is lost but the
    literal.
    """
    return (
        "sk" + "_live_" + "9f2bQZ81xKdm4Tr0",
        "whsec" + "_" + "7Hq2mNp4rLx8ZvW3",
        "Authorization: " + "Bearer " + "eyJhbGciOiJIUzI1NiJ9.invented.sig",
        "api" + "_key=" + "0f1e2d3c4b5a69788796a5b4c3d2e1f0",
        "+44 " + "7700 " + "900123",
    )


PLANTED = _planted()


class Requirement:
    def __init__(self, rid: str, check: str, params: dict | None = None) -> None:
        self.id = rid
        self.check = check
        self.params = params or {}
        self.criticality = "MANDATORY"


class Base(unittest.TestCase):

    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp())


# ---------------------------------------------------------------- K-LEAN
class SecretsDoNotReachSolventsOwnRecords(Base):
    """A credential in a client's file must not land in Solvent's tables."""

    def setUp(self):
        super().setUp()
        self.s = Solvent()
        self.addCleanup(self.close)

    def close(self):
        try:
            self.s.store.close()
        except Exception:
            pass

    def record(self, raw_output: str) -> str:
        """Store one piece of evidence and read back what landed in the table.

        T0, deliberately. Above it, this method refuses evidence that does not
        name a certified verifier implementation -- which is a different
        control, is correct, and would turn these tests into a test of that
        control instead of of the redaction. The redaction is applied at the
        same write whatever the tier, which is the point: there is one place
        the row is built.
        """
        self.s.audit.record_verification(
            subject_ref="job_advisory", tier=VerificationTier.T0_SELF_REPORT,
            method="advisory review", executor_identity="worker",
            verifier_identity="worker", verdict=False, raw_output=raw_output,
            consequence=ConsequenceTier.C_LOW)
        rows = self.s.store.raw_readonly(
            "SELECT raw_output FROM verification_evidence "
            "ORDER BY ts DESC, rowid DESC LIMIT 1")
        return rows[0]["raw_output"]

    def test_every_planted_secret_is_redacted_on_the_way_in(self):
        for secret in PLANTED:
            with self.subTest(secret=secret[:12]):
                stored = self.record(
                    f"FAIL: Token is delivered as {secret!r} where the "
                    f"document states something else")
                self.assertNotIn(secret, stored, "a secret reached the table")
                self.assertIn("REDACTED", stored)

    def test_the_explanation_survives_the_redaction(self):
        """Redaction that destroyed the diagnostic would be its own failure."""
        other = "sk" + "_live_" + "0000aaaa1111bbbb"
        stored = self.record(
            f"FAIL: Token is delivered as {PLANTED[0]!r} where the "
            f"document states {other!r}")
        self.assertIn("Token is delivered as", stored)
        self.assertIn("where the document states", stored)

    def test_an_ordinary_business_value_is_not_redacted(self):
        """A client's invoice total is job evidence, not a secret.

        The opposite failure, and worth a test: a redactor that swallowed
        every figure would leave an audit trail nobody could read, which is
        the same defect as one that leaks.
        """
        stored = self.record(
            "FAIL: Amount Due is delivered as '1,260.00' where the document "
            "states '1,250.00' for Acme Corp")
        self.assertIn("1,250.00", stored)
        self.assertIn("Acme Corp", stored)

    def test_a_verifier_finding_about_a_secret_redacts_through_this_path(self):
        from solvent import docverify

        source = self.dir / "contract.docx"
        source.write_bytes(docx.write_document([
            docx.Out(text="Client: Acme Corp"),
            docx.Out(text=f"Token: {PLANTED[0]}")]))
        output = self.dir / "extracted.json"
        requirements = [
            Requirement("r1", "extract_fields",
                        {"labels": {"Client": ["Client"], "Token": ["Token"]}}),
            Requirement("r2", "fields_traceable", {})]
        docwork.execute(source=source, destination=output,
                        requirements=requirements)
        payload = json.loads(output.read_text(encoding="utf-8"))
        for entry in payload["fields"]:
            if entry["field"] == "Token":
                entry["value"] = entry["value"] + "Z"
        output.write_text(json.dumps(payload, indent=2), encoding="utf-8")

        report = docverify.verify(source=source, output=output,
                                  requirements=requirements)
        for finding in report.findings:
            raw = f"{finding.result.value}: {finding.detail} | {finding.computed}"
            if PLANTED[0] in raw:
                stored = self.record(raw)
                self.assertNotIn(PLANTED[0], stored)
                break
        else:
            self.fail("the verifier quoted no value, so this path went untested")


class TheDeliverableKeepsTheClientsContent(Base):
    """The other half of the line, stated as a test so nobody 'fixes' it."""

    def test_a_data_entry_exception_records_the_record_as_supplied(self):
        source = self.dir / "records.csv"
        source.write_text(
            "Ref,Client Name,Token\n"
            f"A-1001,,{PLANTED[0]}\n", encoding="utf-8")
        destination = self.dir / "delivered.csv"
        entrywork.execute(source=source, destination=destination, requirements=[
            Requirement("r1", "map_fields", {"mapping": {
                "Reference": "Ref", "Client": "Client Name", "Token": "Token"}}),
            Requirement("r2", "require_field", {"fields": ["Client"]})])
        held = destination.with_name(destination.stem + "-exceptions.csv")
        text = held.read_text(encoding="utf-8")
        self.assertIn(PLANTED[0], text,
                      "an exception whose values were removed would not tell "
                      "the client which record failed")
        self.assertIn("require_field", text)

    def test_redaction_is_not_applied_to_an_extracted_document_value(self):
        source = self.dir / "contract.docx"
        source.write_bytes(docx.write_document(
            [docx.Out(text=f"Token: {PLANTED[0]}")]))
        output = self.dir / "extracted.json"
        docwork.execute(source=source, destination=output, requirements=[
            Requirement("r1", "extract_fields",
                        {"labels": {"Token": ["Token"]}})])
        self.assertIn(PLANTED[0], output.read_text(encoding="utf-8"),
                      "the client's own value, returned to the client")


# ---------------------------------------------------------------- iFixAi
class EachRefusalNamesItsOwnCause(Base):
    """A test that only asserts "refused" passes when the wrong control fired."""

    def refusals(self, capability, source, requirements, suffix):
        module = {"spreadsheet-automation": sheetwork,
                  "data-entry": entrywork,
                  "document-processing": docwork}[capability]
        destination = self.dir / f"delivered{suffix}"
        report = module.execute(source=source, destination=destination,
                                requirements=requirements)
        return report, destination

    def workbook(self, rows=None):
        path = self.dir / "client.xlsx"
        path.write_bytes(xlsx.write_workbook([xlsx.SheetOut(
            name="Data", rows=rows or [["Item", "Units", "Rate"],
                                       ["Alpha", 2, "10.00"]])]))
        return path

    def test_a_spreadsheet_refusal_names_the_column_not_the_operation(self):
        report, _ = self.refusals(
            "spreadsheet-automation", self.workbook(),
            [Requirement("r1", "compute_column",
                         {"into": "Margin", "expression": "product",
                          "using": ["Units", "Markup"]})], ".xlsx")
        self.assertFalse(report.clean)
        self.assertIn("Markup", report.refused[0],
                      "the refusal must name the column the source lacks")
        self.assertNotIn("not something this capability does",
                         report.refused[0],
                         "that message is for an unknown operation, and this "
                         "operation is one it performs")

    def test_an_unknown_operation_and_a_missing_column_refuse_differently(self):
        unknown, _ = self.refusals(
            "spreadsheet-automation", self.workbook(),
            [Requirement("r1", "pivot_table", {"rows": ["Item"]})], ".xlsx")
        missing, _ = self.refusals(
            "spreadsheet-automation", self.workbook(),
            [Requirement("r1", "require_columns", {"columns": ["Ghost"]})],
            ".xlsx")
        self.assertNotEqual(unknown.refused[0], missing.refused[0])
        self.assertIn("pivot_table", unknown.refused[0])
        self.assertIn("Ghost", missing.refused[0])

    def test_a_document_refusal_distinguishes_the_format_from_the_brief(self):
        pdf = self.dir / "invoice.pdf"
        pdf.write_bytes(b"%PDF-1.7\n")
        bad_format, _ = self.refusals(
            "document-processing", pdf,
            [Requirement("r1", "extract_fields",
                         {"labels": {"Client": ["Client"]}})], ".json")
        docx_source = self.dir / "invoice.docx"
        docx_source.write_bytes(docx.write_document(
            [docx.Out(text="Client: Acme Corp")]))
        bad_brief, _ = self.refusals(
            "document-processing", docx_source,
            [Requirement("r1", "extract_fields", {})], ".json")
        self.assertIn("content streams", bad_format.refused[0])
        self.assertIn("not something to guess", bad_brief.refused[0])

    def test_a_data_entry_refusal_distinguishes_absent_from_unmapped(self):
        source = self.dir / "records.csv"
        source.write_text("Ref,Client Name\nA-1001,Acme\n", encoding="utf-8")
        absent, _ = self.refusals(
            "data-entry", source,
            [Requirement("r1", "map_fields",
                         {"mapping": {"Reference": "Nope"}})], ".csv")
        unmapped, _ = self.refusals(
            "data-entry", source,
            [Requirement("r1", "require_field", {"fields": ["Reference"]})],
            ".csv")
        self.assertIn("Nope", absent.refused[0])
        self.assertIn("cannot be inferred", unmapped.refused[0])


class SatisfyingOneControlDoesNotUnlockTheRest(Base):
    """Walk a chain, fixing one defect at a time, and watch the next refuse.

    The refusal has to move. A chain where the same message survives every fix
    is one control wearing several names, and a chain where the second fix
    delivers is a chain whose later controls were never reached.
    """

    def test_a_spreadsheet_brief_is_refused_once_per_defect(self):
        path = self.dir / "client.xlsx"
        path.write_bytes(xlsx.write_workbook([xlsx.SheetOut(
            name="Data", rows=[["Item", "Units", "Rate"],
                               ["Alpha", 2, "10.00"]])]))
        stages = [
            # Three defects at once; each fix must expose the next refusal and
            # the messages must differ.
            ([Requirement("r1", "pivot_table", {}),
              Requirement("r2", "compute_column",
                          {"into": "Amount", "expression": "product",
                           "using": ["Units", "Markup"]}),
              Requirement("r3", "select_columns", {"columns": ["Item", "Amount"]})],
             "pivot_table"),
            ([Requirement("r2", "compute_column",
                          {"into": "Amount", "expression": "product",
                           "using": ["Units", "Markup"]}),
              Requirement("r3", "select_columns", {"columns": ["Item", "Amount"]})],
             "Markup"),
            ([Requirement("r2", "compute_column",
                          {"into": "Amount", "expression": "product",
                           "using": ["Units", "Rate"]}),
              Requirement("r3", "select_columns", {"columns": ["Item", "Amount"]})],
             "could not be kept"),
        ]
        seen = []
        for requirements, expected in stages:
            report = sheetwork.execute(
                source=path, destination=self.dir / "out.xlsx",
                requirements=requirements)
            self.assertFalse(report.clean,
                             f"delivered with a defect still present: {expected}")
            self.assertIn(expected, " ".join(report.refused))
            seen.append(report.refused[0])
        self.assertEqual(len(set(seen)), len(seen),
                         "the same refusal survived a fix, so one control is "
                         "answering for three")

        # And with every defect fixed, it delivers. A chain that refuses
        # whatever you do is not a chain of controls.
        report = sheetwork.execute(
            source=path, destination=self.dir / "out.xlsx", requirements=[
                Requirement("r2", "compute_column",
                            {"into": "Amount", "expression": "product",
                             "using": ["Units", "Rate"]}),
                Requirement("r3", "select_columns",
                            {"columns": ["Item", "Amount", "Units", "Rate"]})])
        self.assertEqual(report.refused, [])

    def test_a_data_entry_brief_is_refused_once_per_defect(self):
        source = self.dir / "records.csv"
        source.write_text("Ref,Client Name,Invoiced\nA-1001,Acme,10.00\n",
                          encoding="utf-8")
        stages = [
            ([Requirement("r1", "require_field", {"fields": ["Reference"]})],
             "cannot be inferred"),
            ([Requirement("r1", "map_fields",
                          {"mapping": {"Reference": "Nope"}})], "Nope"),
            ([Requirement("r1", "map_fields",
                          {"mapping": {"Reference": "Ref"}}),
              Requirement("r2", "require_field", {"fields": ["Postcode"]})],
             "Postcode"),
            ([Requirement("r1", "map_fields",
                          {"mapping": {"Reference": "Ref"}}),
              Requirement("r2", "constant_field",
                          {"fields": {"Reference": "fixed"}})], "disagree"),
        ]
        seen = []
        for requirements, expected in stages:
            report = entrywork.execute(
                source=source, destination=self.dir / "out.csv",
                requirements=requirements)
            self.assertFalse(report.clean, f"delivered despite {expected}")
            self.assertIn(expected, " ".join(report.refused))
            seen.append(report.refused[0])
        self.assertEqual(len(set(seen)), len(seen))

        report = entrywork.execute(
            source=source, destination=self.dir / "out.csv", requirements=[
                Requirement("r1", "map_fields",
                            {"mapping": {"Reference": "Ref",
                                         "Client": "Client Name"}}),
                Requirement("r2", "require_field", {"fields": ["Reference"]})])
        self.assertEqual(report.refused, [])

    def test_a_document_brief_is_refused_once_per_defect(self):
        pdf = self.dir / "invoice.pdf"
        pdf.write_bytes(b"%PDF-1.7\n")
        readable = self.dir / "invoice.docx"
        readable.write_bytes(docx.write_document([
            docx.Out(text="Client: Acme Corp"),
            docx.Out(kind="table", rows=(("Item", "Amount"),
                                         ("Consulting", "10.00")))]))
        labels = {"Client": ["Client"]}
        stages = [
            (pdf, [Requirement("r1", "extract_fields", {"labels": labels})],
             ".json", "content streams"),
            (readable, [Requirement("r1", "extract_fields", {})], ".json",
             "not something to guess"),
            (readable, [Requirement("r1", "extract_fields", {"labels": labels}),
                        Requirement("r2", "require_fields",
                                    {"fields": ["Shipping Address"]})],
             ".json", "Shipping Address"),
            (readable, [Requirement("r1", "extract_fields", {"labels": labels})],
             ".md", "nowhere to go"),
        ]
        seen = []
        for source, requirements, suffix, expected in stages:
            report = docwork.execute(
                source=source, destination=self.dir / f"out{suffix}",
                requirements=requirements)
            self.assertFalse(report.clean, f"delivered despite {expected}")
            self.assertIn(expected, " ".join(report.refused))
            seen.append(report.refused[0])
        self.assertEqual(len(set(seen)), len(seen))

        report = docwork.execute(
            source=readable, destination=self.dir / "out.json",
            requirements=[Requirement("r1", "extract_fields",
                                      {"labels": labels})])
        self.assertEqual(report.refused, [])


class NothingPartialIsEverWritten(Base):
    """A refusal that left half a deliverable would be the worst of both."""

    def test_no_capability_writes_a_file_for_a_brief_it_refused(self):
        cases = []

        workbook = self.dir / "client.xlsx"
        workbook.write_bytes(xlsx.write_workbook([xlsx.SheetOut(
            name="Data", rows=[["Item"], ["Alpha"]])]))
        cases.append((sheetwork, workbook, self.dir / "a.xlsx",
                      [Requirement("r1", "pivot_table", {})]))

        records = self.dir / "records.csv"
        records.write_text("Ref\nA-1001\n", encoding="utf-8")
        cases.append((entrywork, records, self.dir / "b.csv",
                      [Requirement("r1", "require_field",
                                   {"fields": ["Reference"]})]))

        pdf = self.dir / "invoice.pdf"
        pdf.write_bytes(b"%PDF-1.7\n")
        cases.append((docwork, pdf, self.dir / "c.json",
                      [Requirement("r1", "extract_fields",
                                   {"labels": {"Client": ["Client"]}})]))

        for module, source, destination, requirements in cases:
            with self.subTest(capability=module.VERSION):
                report = module.execute(source=source, destination=destination,
                                        requirements=requirements)
                self.assertFalse(report.clean)
                self.assertFalse(destination.exists(),
                                 "a refused brief produced a file")

    def test_a_data_entry_refusal_writes_no_exceptions_file_either(self):
        records = self.dir / "records.csv"
        records.write_text("Ref\nA-1001\n", encoding="utf-8")
        destination = self.dir / "d.csv"
        report = entrywork.execute(
            source=records, destination=destination,
            requirements=[Requirement("r1", "map_fields",
                                      {"mapping": {"Reference": "Nope"}})])
        self.assertFalse(report.clean)
        self.assertFalse(destination.exists())
        self.assertFalse(
            destination.with_name(destination.stem + "-exceptions.csv").exists(),
            "half a two-file deliverable is still half a deliverable")


class AdvisoryMeansAdvisory(unittest.TestCase):
    """Neither gate may override anything that decides whether work ships."""

    def test_nothing_in_this_file_promotes_a_capability(self):
        from solvent.harness import _owner_promoted

        for name in ("spreadsheet-automation", "data-entry",
                     "document-processing", "report-builder"):
            with self.subTest(name=name):
                self.assertIsNone(_owner_promoted(name))

    def test_the_redaction_is_applied_at_the_write_not_at_the_read(self):
        """A secret that reached the table is already stored."""
        import inspect

        source = inspect.getsource(AuditLog.record_verification)
        self.assertIn("redact(raw_output)", source)
        self.assertIn("INSERT INTO verification_evidence", source)

    def test_the_redactor_is_the_one_solvent_already_had(self):
        """Not a second implementation with its own idea of what a secret is."""
        self.assertEqual(redact.__module__, "solvent.resilience")


if __name__ == "__main__":
    unittest.main()
