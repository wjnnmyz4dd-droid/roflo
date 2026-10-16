"""A deliverable that opens, established without asking the writer.

``sheetverify`` reported ``opens_as_workbook`` as a PASS on the evidence
"read back through solvent.xlsx". That is not evidence that the client
can open the file. It is evidence that our reader reads our writer, and
the two share every assumption, so the one check whose job was "this
file opens" could not fail for any reason the writer was wrong about.

Measured before the fix: both ``xlsx.read_workbook`` and
``docx.read_document`` accept a package with ``[Content_Types].xml``
removed and one with ``_rels/.rels`` removed. Both are mandatory in the
Open Packaging Conventions; Excel and Word refuse a file without them.
"""

from __future__ import annotations

import io
import pathlib
import tempfile
import unittest
import zipfile

from solvent import docx, opcpackage, sheetverify, xlsx
from solvent.types import CheckResult


def a_workbook() -> bytes:
    return xlsx.write_workbook([xlsx.SheetOut(
        name="Sheet1", rows=[["Item", "Amount"], ["a", "1"], ["b", "2"]])])


def a_document() -> bytes:
    return docx.write_document(
        [docx.Out(kind="paragraph", text="Hello", style="Normal")])


def without(blob: bytes, part: str) -> bytes:
    source = zipfile.ZipFile(io.BytesIO(blob))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as out:
        for name in source.namelist():
            if name != part:
                out.writestr(name, source.read(name))
    return buffer.getvalue()


class WhatThePackageCheckAccepts(unittest.TestCase):
    def test_a_workbook_this_system_wrote_is_valid(self):
        self.assertEqual(opcpackage.problem(a_workbook()), "")

    def test_a_document_this_system_wrote_is_valid(self):
        self.assertEqual(opcpackage.problem(a_document()), "")

    def test_an_unused_relationships_part_is_not_required(self):
        """Correctness cuts both ways: a stricter check that is wrong is worse.

        The conventions require a relationships part only for a part that
        has relationships. Our document body references nothing by id, so a
        package without ``word/_rels/document.xml.rels`` still opens, and
        failing it would be inventing a rule.
        """
        self.assertEqual(
            opcpackage.problem(
                without(a_document(), "word/_rels/document.xml.rels")),
            "")


class WhatItRefuses(unittest.TestCase):
    def test_a_file_that_is_not_an_archive(self):
        self.assertIn("not a readable archive",
                      opcpackage.problem(b"%PDF-1.7 this is not a zip"))

    def test_a_package_with_no_content_types(self):
        for blob in (a_workbook(), a_document()):
            with self.subTest(kind=blob[:2]):
                self.assertIn(
                    "[Content_Types].xml",
                    opcpackage.problem(without(blob, "[Content_Types].xml")))

    def test_a_package_with_no_root_relationships(self):
        for blob in (a_workbook(), a_document()):
            with self.subTest(kind=blob[:2]):
                self.assertIn("_rels/.rels",
                              opcpackage.problem(without(blob, "_rels/.rels")))

    def test_a_workbook_whose_sheet_relationships_are_gone(self):
        """The workbook names its sheets by id; without the rels, by nothing."""
        trouble = opcpackage.problem(
            without(a_workbook(), "xl/_rels/workbook.xml.rels"))
        self.assertIn("relationship id", trouble)

    def test_a_part_the_main_document_references_is_missing(self):
        trouble = opcpackage.problem(without(a_workbook(), "xl/styles.xml"))
        self.assertIn("xl/styles.xml", trouble)

    def test_a_part_that_is_not_well_formed_xml(self):
        blob = a_workbook()
        source = zipfile.ZipFile(io.BytesIO(blob))
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as out:
            for name in source.namelist():
                out.writestr(name, b"<truncated"
                             if name == "xl/workbook.xml" else
                             source.read(name))
        self.assertIn("not well-formed XML",
                      opcpackage.problem(buffer.getvalue()))


class TheVerifierUsesIt(unittest.TestCase):
    def setUp(self):
        self.directory = pathlib.Path(tempfile.mkdtemp())
        self.source = self.directory / "in.xlsx"
        self.output = self.directory / "out.xlsx"
        self.source.write_bytes(a_workbook())

    def opens(self, report):
        return next(f for f in report.findings
                    if f.check == "opens_as_workbook")

    def test_an_intact_workbook_passes(self):
        self.output.write_bytes(a_workbook())
        finding = self.opens(sheetverify.verify(
            source=self.source, output=self.output, requirements=[]))
        self.assertEqual(finding.result, CheckResult.PASS)

    def test_a_workbook_excel_would_refuse_no_longer_passes(self):
        """The case the old evidence could not see."""
        self.output.write_bytes(without(a_workbook(), "[Content_Types].xml"))
        finding = self.opens(sheetverify.verify(
            source=self.source, output=self.output, requirements=[]))
        self.assertEqual(finding.result, CheckResult.FAIL)
        self.assertIn("Excel will open", finding.detail)

    def test_the_old_reader_still_accepts_it_which_is_the_whole_point(self):
        """Not a test of opcpackage: a record of why it had to exist.

        If this ever starts raising, the writer's reader has become strict
        on its own and this module's reason for existing should be
        re-examined -- but it still would not be an independent opinion.
        """
        book = xlsx.read_workbook(without(a_workbook(), "[Content_Types].xml"))
        self.assertEqual(len(book.sheets), 1)


class NoWorkerMayUseIt(unittest.TestCase):
    """A second opinion that the writer shares is one opinion."""

    WORKERS = ("csvwork", "entrywork", "reportwork", "sheetwork", "docwork",
               "xlsx", "docx")

    def test_no_producing_module_imports_the_package_check(self):
        import ast

        for name in self.WORKERS:
            with self.subTest(module=name):
                tree = ast.parse(
                    pathlib.Path(f"solvent/{name}.py").read_text(
                        encoding="utf-8"))
                imported = set()
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        imported |= {a.name for a in node.names}
                    elif isinstance(node, ast.ImportFrom):
                        imported.add(node.module or "")
                        imported |= {a.name for a in node.names}
                self.assertNotIn("opcpackage", imported)


if __name__ == "__main__":
    unittest.main()
