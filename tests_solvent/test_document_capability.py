"""Document processing: the worker, and the verifier that does not trust it.

The tests that matter most are in :class:`TheVerifierCatchesAWrongWorker`.
Each takes a correct delivery, puts one deliberate defect in it, and asserts
which check notices.

Two of them are the reason this capability has the shape it has:

:meth:`a_gap_filled_in_with_something_plausible` -- a field the document does
not state, delivered as a value that reads perfectly well. Nothing about the
deliverable is detectably wrong. Only the document says so.

:meth:`a_contradiction_resolved_quietly` -- a document stating one field twice
with two different values, delivered as one of them. That is a coin flip
presented as an extraction, and the client has no way to know a coin was
flipped.
"""

from __future__ import annotations

import json
import pathlib
import tempfile
import unittest

from solvent import docverify as verifier
from solvent import docwork as worker
from solvent import docx


class Requirement:
    def __init__(self, rid: str, check: str, params: dict,
                 criticality: str = "MANDATORY") -> None:
        self.id = rid
        self.check = check
        self.params = params
        self.criticality = criticality


LABELS = {
    "Invoice Number": ["Invoice Number", "Invoice No"],
    "Client": ["Client", "Client Name"],
    "Amount Due": ["Amount Due", "Total Due"],
    "Purchase Order": ["Purchase Order", "PO Number"],
}


def document_blocks():
    """A plausible client invoice: headings, labelled lines, and a table.

    ``Purchase Order`` is deliberately absent, because the interesting half of
    an extraction is what the document does not say.
    """
    return [
        docx.Out(text="Invoice", style="Title"),
        docx.Out(text="Summary", style="Heading1"),
        docx.Out(text="Invoice Number: A-1001"),
        docx.Out(text="Client: Acme Ltd"),
        docx.Out(text="Amount Due: 1,250.00"),
        docx.Out(text="Lines", style="Heading2"),
        docx.Out(kind="table", rows=(("Item", "Amount"),
                                     ("Consulting", "1,000.00"),
                                     ("Support", "250.00"))),
        docx.Out(text="Payable within 30 days."),
    ]


def extraction_brief():
    return [
        Requirement("r1", "extract_fields", {"labels": LABELS}),
        Requirement("r2", "extract_table", {"columns": ["Item", "Amount"]}),
        Requirement("r3", "document_opens", {}),
        Requirement("r4", "fields_traceable", {}),
        Requirement("r5", "absences_disclosed", {}),
        Requirement("r6", "no_invented_content", {}),
        Requirement("r7", "table_preserved", {}),
        Requirement("r8", "unsupported_declared", {}),
    ]


TEMPLATE = [
    {"style": "Title", "text": "Statement of Account"},
    {"style": "Heading1", "text": "Details"},
    {"text": "Invoice: {Invoice Number}"},
    {"text": "Client: {Client}"},
    {"text": "Due: {Amount Due}"},
    {"text": "Purchase order: {Purchase Order}"},
    {"kind": "table", "rows": "extracted"},
]


class Base(unittest.TestCase):

    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp())
        self.source = self.dir / "invoice.docx"
        self.source.write_bytes(docx.write_document(document_blocks()))

    def extract_to(self, name="extracted.json", requirements=None):
        self.output = self.dir / name
        return worker.execute(source=self.source, destination=self.output,
                              requirements=requirements or extraction_brief())

    def verify(self, requirements=None):
        return verifier.verify(source=self.source, output=self.output,
                               requirements=requirements or extraction_brief())

    def finding(self, report, check):
        matches = [f for f in report.findings if f.check == check]
        self.assertTrue(matches, f"no finding for {check}: {report.summary()}")
        return matches[0]

    def read_json(self) -> dict:
        return json.loads(self.output.read_text(encoding="utf-8"))

    def write_json(self, payload) -> None:
        self.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")


class ThePlan(Base):

    def test_an_unknown_operation_is_refused_by_name(self):
        steps, refusals = worker.plan([Requirement("r1", "summarise_contract", {})])
        self.assertEqual(steps, [])
        self.assertIn("summarise_contract", refusals[0])
        self.assertIn("extract_fields", refusals[0])

    def test_which_text_is_a_field_label_is_not_guessed(self):
        _, refusals = worker.plan([Requirement("r1", "extract_fields", {})])
        self.assertIn("not something to guess", refusals[0])

    def test_a_table_with_no_columns_named_cannot_be_identified(self):
        _, refusals = worker.plan([Requirement("r1", "extract_table", {})])
        self.assertIn("cannot be established", refusals[0])

    def test_a_target_format_outside_the_vocabulary_is_refused(self):
        _, refusals = worker.plan(
            [Requirement("r1", "convert_format", {"to": "pdf"})])
        self.assertIn("pdf", refusals[0])
        self.assertIn(".docx", refusals[0])

    def test_a_template_style_no_output_format_defines_is_refused(self):
        _, refusals = worker.plan([Requirement(
            "r1", "apply_template", {"blocks": [{"style": "Fancy", "text": "x"}]})])
        self.assertIn("Fancy", refusals[0])

    def test_a_verify_only_check_is_not_an_operation(self):
        steps, refusals = worker.plan([Requirement("r1", "fields_traceable", {})])
        self.assertEqual((steps, refusals), ([], []))


class UnsupportedFormats(Base):
    """Named, with the reason, rather than discovered by a client."""

    def test_a_pdf_is_refused_with_the_reason_and_a_way_forward(self):
        source = self.dir / "invoice.pdf"
        source.write_bytes(b"%PDF-1.7\n")
        self.output = self.dir / "out.json"
        report = worker.execute(source=source, destination=self.output,
                                requirements=extraction_brief())
        self.assertFalse(report.clean)
        self.assertIn("content streams", report.refused[0])
        self.assertIn(".docx", report.refused[0], "the refusal says what works")
        self.assertFalse(self.output.exists(),
                         "nothing partial is written for a refused source")

    def test_an_image_is_refused_and_ocr_is_not_approximated(self):
        source = self.dir / "scan.png"
        source.write_bytes(b"\x89PNG\r\n\x1a\n")
        self.output = self.dir / "out.json"
        report = worker.execute(source=source, destination=self.output,
                                requirements=extraction_brief())
        self.assertIn("optical character recognition", report.refused[0])
        self.assertIn("does not approximate", report.refused[0])

    def test_a_legacy_doc_is_a_different_format_and_says_so(self):
        self.assertIn("pre-2007", worker.refusal_for(".doc"))

    def test_a_spreadsheet_is_another_capabilitys_work(self):
        self.assertIn("spreadsheet-automation", worker.refusal_for(".xlsx"))

    def test_every_refused_format_carries_a_reason(self):
        for suffix, reason in worker.REFUSED_FORMATS.items():
            with self.subTest(suffix=suffix):
                self.assertGreater(len(reason), 30,
                                   f"{suffix} is refused without explaining why")

    def test_no_format_is_both_read_and_refused(self):
        self.assertEqual(set(worker.READABLE) & set(worker.REFUSED_FORMATS),
                         set())

    def test_the_verifier_confirms_the_refusal_was_honoured(self):
        source = self.dir / "invoice.pdf"
        source.write_bytes(b"%PDF-1.7\n")
        self.output = self.dir / "out.json"
        worker.execute(source=source, destination=self.output,
                       requirements=extraction_brief())
        report = verifier.verify(source=source, output=self.output,
                                 requirements=extraction_brief())
        self.assertEqual(
            self.finding(report, "unsupported_declared").verdict, verifier.PASS)

    def test_a_deliverable_written_from_a_refused_source_is_caught(self):
        source = self.dir / "invoice.pdf"
        source.write_bytes(b"%PDF-1.7\n")
        self.output = self.dir / "out.json"
        self.output.write_text('{"fields": []}', encoding="utf-8")
        report = verifier.verify(source=source, output=self.output,
                                 requirements=extraction_brief())
        finding = self.finding(report, "unsupported_declared")
        self.assertEqual(finding.verdict, verifier.FAIL)
        self.assertIn("was written", finding.detail)


class ACorrectExtraction(Base):

    def test_the_whole_brief_runs_and_every_check_passes(self):
        report = self.extract_to()
        self.assertEqual(report.refused, [])
        self.assertEqual(len(report.found), 3)
        self.assertEqual(len(report.absent), 1)
        verdict = self.verify()
        self.assertEqual(verdict.verdict, verifier.PASS, verdict.summary())

    def test_an_absent_field_is_marked_rather_than_omitted(self):
        self.extract_to()
        payload = self.read_json()
        absent = [f for f in payload["fields"] if f["field"] == "Purchase Order"]
        self.assertEqual(absent[0]["value"], worker.NOT_FOUND)
        self.assertEqual(payload["not_stated"], ["Purchase Order"])

    def test_every_found_field_carries_the_line_it_came_from(self):
        self.extract_to()
        for entry in self.read_json()["fields"]:
            if entry["found"]:
                self.assertIn(entry["value"], entry["evidence"])

    def test_an_alternative_label_finds_the_same_field(self):
        source = self.dir / "other.docx"
        source.write_bytes(docx.write_document([
            docx.Out(text="Invoice No: B-2002"),
            docx.Out(text="Client Name: Borealis GmbH")]))
        self.output = self.dir / "out.json"
        worker.execute(source=source, destination=self.output, requirements=[
            Requirement("r1", "extract_fields", {"labels": LABELS})])
        values = {f["field"]: f["value"] for f in self.read_json()["fields"]}
        self.assertEqual(values["Invoice Number"], "B-2002")
        self.assertEqual(values["Client"], "Borealis GmbH")

    def test_a_label_in_a_table_row_is_found_too(self):
        source = self.dir / "tabular.docx"
        source.write_bytes(docx.write_document([docx.Out(
            kind="table", rows=(("Invoice Number", "C-3003"),))]))
        self.output = self.dir / "out.json"
        worker.execute(source=source, destination=self.output, requirements=[
            Requirement("r1", "extract_fields",
                        {"labels": {"Invoice Number": ["Invoice Number"]}})])
        self.assertEqual(self.read_json()["fields"][0]["value"], "C-3003")

    def test_label_matching_tolerates_case_and_spacing(self):
        source = self.dir / "loose.docx"
        source.write_bytes(docx.write_document(
            [docx.Out(text="invoice   number  :  D-4004")]))
        self.output = self.dir / "out.json"
        worker.execute(source=source, destination=self.output, requirements=[
            Requirement("r1", "extract_fields",
                        {"labels": {"Invoice Number": ["Invoice Number"]}})])
        self.assertEqual(self.read_json()["fields"][0]["value"], "D-4004")

    def test_a_table_is_carried_through_cell_for_cell(self):
        self.extract_to()
        self.assertEqual(self.read_json()["tables"][0],
                         [["Item", "Amount"], ["Consulting", "1,000.00"],
                          ["Support", "250.00"]])

    def test_two_tables_with_the_same_header_is_a_question_not_a_guess(self):
        source = self.dir / "twice.docx"
        rows = (("Item", "Amount"), ("x", "1"))
        source.write_bytes(docx.write_document([
            docx.Out(kind="table", rows=rows),
            docx.Out(text="and again"),
            docx.Out(kind="table", rows=rows)]))
        self.output = self.dir / "out.json"
        report = worker.execute(source=source, destination=self.output,
                                requirements=[Requirement(
                                    "r1", "extract_table",
                                    {"columns": ["Item", "Amount"]})])
        self.assertIn("question for the client", report.refused[0])

    def test_a_document_stating_a_field_twice_the_same_way_is_not_ambiguous(self):
        source = self.dir / "repeated.docx"
        source.write_bytes(docx.write_document([
            docx.Out(text="Client: Acme Ltd"),
            docx.Out(text="Client: Acme Ltd")]))
        self.output = self.dir / "out.json"
        worker.execute(source=source, destination=self.output, requirements=[
            Requirement("r1", "extract_fields", {"labels": {"Client": ["Client"]}})])
        self.assertEqual(self.read_json()["fields"][0]["value"], "Acme Ltd")

    def test_a_document_contradicting_itself_is_reported_as_contradicting_itself(self):
        source = self.dir / "contradictory.docx"
        source.write_bytes(docx.write_document([
            docx.Out(text="Amount Due: 1,250.00"),
            docx.Out(text="Amount Due: 4,000.00")]))
        self.output = self.dir / "out.json"
        worker.execute(source=source, destination=self.output, requirements=[
            Requirement("r1", "extract_fields",
                        {"labels": {"Amount Due": ["Amount Due"]}})])
        entry = self.read_json()["fields"][0]
        self.assertEqual(entry["value"], worker.AMBIGUOUS)
        self.assertIn("1,250.00", entry["evidence"])
        self.assertIn("4,000.00", entry["evidence"])

    def test_a_struck_out_figure_is_not_what_the_document_says(self):
        """A tracked deletion carries the old price. Extracting it is a defect."""
        from tests_solvent.test_docx_format import para, run, wrap

        source = self.dir / "revised.docx"
        source.write_bytes(wrap(para(
            run("Amount Due: ") +
            "<w:del><w:r><w:delText>4,000.00</w:delText></w:r></w:del>" +
            run("1,250.00"))))
        self.output = self.dir / "out.json"
        report = worker.execute(source=source, destination=self.output,
                                requirements=[Requirement(
                                    "r1", "extract_fields",
                                    {"labels": {"Amount Due": ["Amount Due"]}})])
        self.assertEqual(self.read_json()["fields"][0]["value"], "1,250.00")
        self.assertTrue(report.has_revisions)
        self.assertIn("revisions", report.summary())

    def test_a_required_field_the_document_does_not_state_fails_verification(self):
        self.extract_to(requirements=extraction_brief() + [
            Requirement("r9", "require_fields", {"fields": ["Purchase Order"]})])
        report = self.verify(extraction_brief() + [
            Requirement("r9", "require_fields", {"fields": ["Purchase Order"]})])
        finding = self.finding(report, "require_fields")
        self.assertEqual(finding.verdict, verifier.FAIL)
        self.assertIn("correctly disclosed", finding.detail)
        self.assertEqual(self.finding(report, "absences_disclosed").verdict,
                         verifier.PASS,
                         "disclosing it is right; it is still not the work asked for")

    def test_require_fields_naming_an_unextracted_field_is_refused(self):
        report = self.extract_to(requirements=[
            Requirement("r1", "extract_fields", {"labels": {"Client": ["Client"]}}),
            Requirement("r2", "require_fields", {"fields": ["Shipping Address"]})])
        self.assertIn("Shipping Address", report.refused[0])


class JobLevelRefusals(Base):

    def test_a_job_that_says_nothing_to_do_is_refused(self):
        report = self.extract_to(requirements=[
            Requirement("r1", "document_opens", {})])
        self.assertIn("no requirement says what to do", report.refused[0])

    def test_an_extraction_with_nowhere_to_go_is_refused(self):
        report = self.extract_to("out.md", requirements=[
            Requirement("r1", "extract_fields", {"labels": LABELS})])
        self.assertIn("nowhere to go", report.refused[0])

    def test_a_conversion_and_a_template_are_two_deliverables(self):
        report = self.extract_to("out.md", requirements=[
            Requirement("r1", "convert_format", {"to": "md"}),
            Requirement("r2", "apply_template", {"blocks": TEMPLATE})])
        self.assertIn("two different deliverables", report.refused[0])

    def test_a_conversion_target_that_disagrees_with_the_deliverable(self):
        report = self.extract_to("out.txt", requirements=[
            Requirement("r1", "convert_format", {"to": "md"})])
        self.assertIn("disagree about the format", report.refused[0])

    def test_an_output_format_this_capability_does_not_write(self):
        report = self.extract_to("out.pdf", requirements=[
            Requirement("r1", "convert_format", {"to": "md"})])
        self.assertTrue(any("not a format this capability writes" in r
                            for r in report.refused))


class Conversion(Base):

    def convert(self, suffix: str):
        self.output = self.dir / f"converted{suffix}"
        requirements = [
            Requirement("r1", "convert_format", {"to": suffix.lstrip(".")}),
            Requirement("r2", "document_opens", {}),
            Requirement("r3", "conversion_fidelity", {}),
            Requirement("r4", "headings_applied", {}),
            Requirement("r5", "table_preserved", {}),
            Requirement("r6", "no_invented_content", {}),
        ]
        report = worker.execute(source=self.source, destination=self.output,
                                requirements=requirements)
        self.assertEqual(report.refused, [], report.summary())
        return report, requirements

    def test_a_word_document_converts_to_markdown_and_verifies(self):
        _, requirements = self.convert(".md")
        text = self.output.read_text(encoding="utf-8")
        self.assertIn("# Invoice", text)
        self.assertIn("## Summary", text)
        self.assertIn("| Item | Amount |", text)
        report = self.verify(requirements)
        self.assertEqual(report.verdict, verifier.PASS, report.summary())

    def test_a_word_document_converts_to_plain_text_and_verifies(self):
        self.convert(".txt")
        text = self.output.read_text(encoding="utf-8")
        self.assertIn("Invoice Number: A-1001", text)
        self.assertIn("Item\tAmount", text, "the cells are carried through")
        self.assertNotIn("#", text, "plain text carries no markup")
        # Without the two structure requirements, because plain text cannot
        # satisfy them and the worker says so rather than pretending.
        requirements = [
            Requirement("r1", "convert_format", {"to": "txt"}),
            Requirement("r2", "document_opens", {}),
            Requirement("r3", "conversion_fidelity", {}),
            Requirement("r6", "no_invented_content", {})]
        verdict = self.verify(requirements)
        self.assertEqual(verdict.verdict, verifier.PASS, verdict.summary())

    def test_plain_text_says_what_it_cannot_carry_rather_than_losing_it_quietly(self):
        report = self.convert(".txt")[0]
        self.assertTrue(any("heading levels" in d
                            for d in report.dropped_features))
        self.assertTrue(any("table structure" in d
                            for d in report.dropped_features))
        self.assertIn("not carried through", report.summary())

    def test_asking_plain_text_for_structure_is_unverifiable_not_passed(self):
        _, requirements = self.convert(".txt")
        report = self.verify(requirements)
        for check in ("headings_applied", "table_preserved"):
            finding = self.finding(report, check)
            self.assertEqual(finding.verdict, verifier.UNVERIFIABLE, check)
            self.assertIn("plain text", finding.detail)
        self.assertEqual(report.verdict, verifier.UNVERIFIABLE,
                         "a job half of which could not be checked is not "
                         "a verified job")

    def test_markdown_converts_to_a_word_document_and_verifies(self):
        self.source = self.dir / "notes.md"
        self.source.write_text(
            "# Report\n\n## Findings\n\nThe fee is 1,250.00.\n\n"
            "| Item | Amount |\n|---|---|\n| Consulting | 1,000.00 |\n",
            encoding="utf-8")
        _, requirements = self.convert(".docx")
        read = docx.read_document(self.output.read_bytes())
        self.assertEqual([(p.text, p.level) for p in read.paragraphs],
                         [("Report", 0), ("Findings", 1),
                          ("The fee is 1,250.00.", None)])
        self.assertEqual(read.tables[0].rows,
                         (("Item", "Amount"), ("Consulting", "1,000.00")))
        self.assertEqual(self.verify(requirements).verdict, verifier.PASS)

    def test_a_pipe_inside_a_cell_survives_the_round_trip(self):
        self.source = self.dir / "piped.md"
        self.source.write_text("| Item | Note |\n|---|---|\n| A | x \\| y |\n",
                               encoding="utf-8")
        self.convert(".docx")
        read = docx.read_document(self.output.read_bytes())
        self.assertEqual(read.tables[0].rows[1], ("A", "x | y"))

    def test_heading_levels_survive_both_directions(self):
        for level, marker in ((0, "# "), (1, "## "), (2, "### ")):
            with self.subTest(level=level):
                self.source = self.dir / f"h{level}.docx"
                self.source.write_bytes(docx.write_document([docx.Out(
                    text="Heading text",
                    style=worker._style_for_level(level))]))
                self.convert(".md")
                self.assertTrue(
                    self.output.read_text(encoding="utf-8").startswith(marker))


class Templates(Base):

    def fill(self):
        self.output = self.dir / "statement.docx"
        requirements = [
            Requirement("r1", "extract_fields", {"labels": LABELS}),
            Requirement("r2", "extract_table", {"columns": ["Item", "Amount"]}),
            Requirement("r3", "apply_template", {"blocks": TEMPLATE}),
            Requirement("r4", "document_opens", {}),
            Requirement("r5", "fields_traceable", {}),
            Requirement("r6", "absences_disclosed", {}),
            Requirement("r7", "no_invented_content", {}),
            Requirement("r8", "table_preserved", {}),
        ]
        report = worker.execute(source=self.source, destination=self.output,
                               requirements=requirements)
        self.assertEqual(report.refused, [], report.summary())
        return report, requirements

    def test_a_template_filled_from_the_document_verifies(self):
        _, requirements = self.fill()
        read = docx.read_document(self.output.read_bytes())
        texts = [p.text for p in read.paragraphs]
        self.assertIn("Invoice: A-1001", texts)
        self.assertIn("Client: Acme Ltd", texts)
        report = self.verify(requirements)
        self.assertEqual(report.verdict, verifier.PASS, report.summary())

    def test_an_unfilled_placeholder_is_marked_not_invented(self):
        self.fill()
        read = docx.read_document(self.output.read_bytes())
        texts = [p.text for p in read.paragraphs]
        self.assertIn(f"Purchase order: {worker.NOT_FOUND}", texts)

    def test_the_extracted_table_is_placed_where_the_template_says(self):
        self.fill()
        read = docx.read_document(self.output.read_bytes())
        self.assertEqual(read.tables[0].rows[0], ("Item", "Amount"))

    def test_a_placeholder_for_a_field_nobody_extracted_is_left_visible(self):
        self.output = self.dir / "statement.docx"
        worker.execute(source=self.source, destination=self.output,
                       requirements=[
                           Requirement("r1", "extract_fields",
                                       {"labels": {"Client": ["Client"]}}),
                           Requirement("r2", "apply_template", {"blocks": [
                               {"text": "Ref: {Invoice Number}"}]})])
        read = docx.read_document(self.output.read_bytes())
        self.assertIn("Ref: {Invoice Number}",
                      [p.text for p in read.paragraphs],
                      "a template referring to nothing is a mistake to show, "
                      "not one to delete")


class TwoImplementationsOfOneSpecification(Base):
    """The worker and the verifier match labels separately. They must agree."""

    def test_label_matching_agrees_across_the_shapes_documents_use(self):
        cases = [
            ("Invoice Number", "Invoice Number: A-1001"),
            ("Invoice Number", "invoice number : A-1001"),
            ("Invoice Number", "Invoice Number - A-1001"),
            ("Invoice Number", "Invoice Number\tA-1001"),
            ("Invoice Number", "Invoice Number – A-1001"),
            ("Invoice Number", "The Invoice Number: A-1001"),
            ("Invoice Number", "Invoice Numbers: A-1001"),
            ("Client", "Client: Acme Ltd: trading as Acme"),
            ("Client", "Client:"),
            ("Client", "Acme Ltd"),
            ("Amount", "Total Amount: 10"),
        ]
        for label, line in cases:
            with self.subTest(line=line):
                self.assertEqual(worker._value_after(label, line),
                                 verifier.value_for(label, line),
                                 f"{label!r} against {line!r}")

    def test_the_loose_comparison_is_the_same_comparison_twice(self):
        for text in ("Invoice Number", " invoice  number ", "INVOICE NUMBER:",
                     "Invoice-Number", "", "  ", "Total."):
            with self.subTest(text=text):
                self.assertEqual(worker._loose(text), verifier.loose(text))

    def test_both_sides_extract_the_same_values_from_the_same_document(self):
        self.extract_to()
        lines = worker.read_source(self.source).lines()
        mine = {f.field: f.value for f in worker.extract(LABELS, lines)}
        theirs = verifier.extract(LABELS, verifier.read_any(self.source).lines())
        self.assertEqual(mine, theirs)

    def test_both_sides_read_the_same_markdown_structure(self):
        path = self.dir / "doc.md"
        path.write_text("# A\n\n## B\n\ntext\n\n| h |\n|---|\n| v |\n",
                        encoding="utf-8")
        mine = worker.read_source(path)
        theirs = verifier.read_any(path)
        self.assertEqual([(p.text, p.level) for p in mine.paragraphs],
                         theirs.paragraphs)
        self.assertEqual([list(r) for r in mine.tables[0].rows],
                         [list(r) for r in theirs.tables[0]])


class TheVerifierCatchesAWrongWorker(Base):

    def setUp(self):
        super().setUp()
        self.extract_to()
        self.assertEqual(self.verify().verdict, verifier.PASS,
                         "the baseline must be clean before a defect means anything")

    def test_a_gap_filled_in_with_something_plausible(self):
        """The worst case: a field the document never states, delivered anyway."""
        payload = self.read_json()
        for entry in payload["fields"]:
            if entry["field"] == "Purchase Order":
                entry.update(value="PO-55120", found=True,
                             evidence="Purchase Order: PO-55120")
        payload["not_stated"] = []
        self.write_json(payload)
        report = self.verify()
        self.assertEqual(report.verdict, verifier.FAIL)
        for check in ("extract_fields", "absences_disclosed",
                      "fields_traceable", "no_invented_content"):
            self.assertEqual(self.finding(report, check).verdict, verifier.FAIL,
                             check)

    def test_a_contradiction_resolved_quietly(self):
        source = self.dir / "contradictory.docx"
        source.write_bytes(docx.write_document([
            docx.Out(text="Amount Due: 1,250.00"),
            docx.Out(text="Amount Due: 4,000.00")]))
        self.output = self.dir / "picked.json"
        self.write_json({"fields": [
            {"field": "Amount Due", "value": "1,250.00", "found": True,
             "evidence": "Amount Due: 1,250.00"}], "tables": [],
            "not_stated": []})
        requirements = [Requirement("r1", "extract_fields", {
            "labels": {"Amount Due": ["Amount Due"]}})]
        report = verifier.verify(source=source, output=self.output,
                                 requirements=requirements)
        finding = self.finding(report, "extract_fields")
        self.assertEqual(finding.verdict, verifier.FAIL)
        self.assertIn("coin flip", finding.evidence)

    def test_a_value_altered_by_one_character(self):
        payload = self.read_json()
        for entry in payload["fields"]:
            if entry["field"] == "Amount Due":
                entry["value"] = "1,260.00"
        self.write_json(payload)
        report = self.verify()
        self.assertEqual(self.finding(report, "extract_fields").verdict,
                         verifier.FAIL)
        self.assertEqual(self.finding(report, "fields_traceable").verdict,
                         verifier.FAIL)

    def test_a_value_with_no_evidence_behind_it(self):
        payload = self.read_json()
        for entry in payload["fields"]:
            entry["evidence"] = ""
        self.write_json(payload)
        finding = self.finding(self.verify(), "fields_traceable")
        self.assertEqual(finding.verdict, verifier.FAIL)
        self.assertIn("no evidence", finding.detail)

    def test_an_evidence_line_the_document_does_not_contain(self):
        payload = self.read_json()
        for entry in payload["fields"]:
            if entry["field"] == "Client":
                entry["evidence"] = "Client: Acme Ltd (per our call)"
        self.write_json(payload)
        finding = self.finding(self.verify(), "fields_traceable")
        self.assertEqual(finding.verdict, verifier.FAIL)
        self.assertIn("does not contain", finding.detail)

    def test_an_absent_field_dropped_from_the_deliverable_entirely(self):
        payload = self.read_json()
        payload["fields"] = [f for f in payload["fields"]
                             if f["field"] != "Purchase Order"]
        self.write_json(payload)
        report = self.verify()
        self.assertEqual(self.finding(report, "absences_disclosed").verdict,
                         verifier.FAIL)
        self.assertEqual(self.finding(report, "extract_fields").verdict,
                         verifier.FAIL)

    def test_a_table_row_quietly_dropped(self):
        payload = self.read_json()
        payload["tables"][0] = payload["tables"][0][:-1]
        self.write_json(payload)
        report = self.verify()
        self.assertEqual(self.finding(report, "extract_table").verdict,
                         verifier.FAIL)
        self.assertEqual(self.finding(report, "table_preserved").verdict,
                         verifier.FAIL)

    def test_a_table_cell_quietly_changed(self):
        payload = self.read_json()
        payload["tables"][0][1][1] = "1,100.00"
        self.write_json(payload)
        self.assertEqual(self.finding(self.verify(), "table_preserved").verdict,
                         verifier.FAIL)

    def test_a_deliverable_that_is_not_an_extraction_at_all(self):
        self.output.write_text('{"summary": "all good"}', encoding="utf-8")
        report = self.verify()
        self.assertEqual(report.verdict, verifier.FAIL)
        self.assertIn("no fields list",
                      self.finding(report, "document_opens").detail)

    def test_a_deliverable_that_is_not_readable(self):
        self.output.write_bytes(b"\xff\xfe not json")
        self.assertEqual(self.verify().verdict, verifier.FAIL)


class ConversionDefects(Base):

    def setUp(self):
        super().setUp()
        self.output = self.dir / "converted.md"
        self.requirements = [
            Requirement("r1", "convert_format", {"to": "md"}),
            Requirement("r2", "document_opens", {}),
            Requirement("r3", "conversion_fidelity", {}),
            Requirement("r4", "headings_applied", {}),
            Requirement("r5", "table_preserved", {}),
            Requirement("r6", "no_invented_content", {}),
        ]
        worker.execute(source=self.source, destination=self.output,
                       requirements=self.requirements)
        self.assertEqual(self.verify(self.requirements).verdict, verifier.PASS)

    def test_a_paragraph_lost_in_conversion(self):
        text = self.output.read_text(encoding="utf-8")
        self.output.write_text(
            text.replace("Payable within 30 days.\n", ""), encoding="utf-8")
        finding = self.finding(self.verify(self.requirements),
                               "conversion_fidelity")
        self.assertEqual(finding.verdict, verifier.FAIL)
        self.assertIn("not in", finding.detail)

    def test_a_sentence_added_in_conversion(self):
        with self.output.open("a", encoding="utf-8") as handle:
            handle.write("\nPayment is overdue.\n")
        finding = self.finding(self.verify(self.requirements),
                               "conversion_fidelity")
        self.assertEqual(finding.verdict, verifier.FAIL)
        self.assertIn("nobody wrote", finding.evidence)

    def test_paragraphs_reordered_in_conversion(self):
        """Every line present, and the document no longer says the same thing."""
        lines = self.output.read_text(encoding="utf-8").splitlines()
        body = [line for line in lines if line.strip()]
        body[2], body[4] = body[4], body[2]
        self.output.write_text("\n".join(body) + "\n", encoding="utf-8")
        finding = self.finding(self.verify(self.requirements),
                               "conversion_fidelity")
        self.assertEqual(finding.verdict, verifier.FAIL)
        self.assertIn("different order", finding.detail)

    def test_a_heading_flattened_into_body_text(self):
        text = self.output.read_text(encoding="utf-8")
        self.output.write_text(text.replace("## Summary", "Summary"),
                               encoding="utf-8")
        finding = self.finding(self.verify(self.requirements),
                               "headings_applied")
        self.assertEqual(finding.verdict, verifier.FAIL)
        self.assertIn("not a heading", finding.detail)

    def test_a_heading_promoted_to_the_wrong_level(self):
        text = self.output.read_text(encoding="utf-8")
        self.output.write_text(text.replace("## Summary", "# Summary"),
                               encoding="utf-8")
        finding = self.finding(self.verify(self.requirements),
                               "headings_applied")
        self.assertEqual(finding.verdict, verifier.FAIL)
        self.assertIn("level", finding.detail)

    def test_a_table_dropped_in_conversion(self):
        lines = [line for line in self.output.read_text(encoding="utf-8")
                 .splitlines() if not line.startswith("|")]
        self.output.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.assertEqual(
            self.finding(self.verify(self.requirements),
                         "table_preserved").verdict, verifier.FAIL)


class TheVerifierSaysWhenItCannotTell(Base):

    def test_an_unknown_check_is_unverifiable_rather_than_passed(self):
        self.extract_to()
        report = self.verify(extraction_brief() + [
            Requirement("rX", "tone_is_professional", {})])
        finding = self.finding(report, "tone_is_professional")
        self.assertEqual(finding.verdict, verifier.UNVERIFIABLE)
        self.assertIn("not been checked by anybody", finding.detail)
        self.assertEqual(report.verdict, verifier.UNVERIFIABLE)

    def test_no_requirements_at_all_is_unverifiable(self):
        self.extract_to()
        report = verifier.verify(source=self.source, output=self.output,
                                 requirements=[])
        self.assertEqual(report.verdict, verifier.UNVERIFIABLE)

    def test_a_whole_job_check_through_the_single_entry_point_says_so(self):
        self.extract_to()
        finding = verifier.run("no_invented_content", source=self.source,
                               output=self.output, params={})
        self.assertEqual(finding.verdict, verifier.UNVERIFIABLE)
        self.assertIn("whole job", finding.detail)

    def test_a_field_level_check_works_through_the_single_entry_point(self):
        self.extract_to()
        finding = verifier.run("extract_fields", source=self.source,
                               output=self.output, params={"labels": LABELS})
        self.assertEqual(finding.verdict, verifier.PASS)
        self.assertTrue(finding.passed)

    def test_two_matching_tables_leave_the_verifier_unable_to_tell(self):
        source = self.dir / "twice.docx"
        rows = (("Item", "Amount"), ("x", "1"))
        source.write_bytes(docx.write_document([
            docx.Out(kind="table", rows=rows),
            docx.Out(text="and again"),
            docx.Out(kind="table", rows=rows)]))
        self.output = self.dir / "out.json"
        self.write_json({"fields": [], "tables": [[["Item", "Amount"], ["x", "1"]]],
                         "not_stated": []})
        report = verifier.verify(source=source, output=self.output,
                                 requirements=[Requirement(
                                     "r1", "extract_table",
                                     {"columns": ["Item", "Amount"]})])
        self.assertEqual(self.finding(report, "extract_table").verdict,
                         verifier.UNVERIFIABLE)

    def test_fidelity_on_an_extraction_is_unverifiable_not_passed(self):
        self.extract_to()
        report = self.verify([Requirement("r1", "conversion_fidelity", {})])
        self.assertEqual(self.finding(report, "conversion_fidelity").verdict,
                         verifier.UNVERIFIABLE)


if __name__ == "__main__":
    unittest.main()
