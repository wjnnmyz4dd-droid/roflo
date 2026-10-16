"""Adversarial scenarios for the file a client sends.

The existing adversarial suites attack the business surface: forged
estimates, laundered grants, instructions hidden in a posting, a client
claiming a capability Solvent lacks. None of them attacks the *file*,
and widening what this system is willing to read widened exactly that
surface. A client controls every byte of their own source.

These scenarios were written from measurements, not from imagination.
Two of them record defects that were real when the probe was first run:
``xlsx.read_workbook`` allocated 1.23 GB over 5.2 seconds on a 410 KB
workbook and then accepted it, and ``opcpackage.problem`` allocated
815 MB on a 206 KB package and called it valid. This process shares a
machine with the owner's own trading session, so those are not
abstract: they are an outage the owner would feel, caused by a file
anybody could send.
"""

from __future__ import annotations

import io
import pathlib
import tempfile
import unittest
import zipfile

from solvent import docx, opcpackage, xlsx
from solvent.harness import run_csv_job
from tests_solvent import fixtures_csv as fx


def a_workbook() -> bytes:
    return xlsx.write_workbook([xlsx.SheetOut(
        name="S", rows=[["Item", "Amount"], ["a", "1"]])])


def a_document() -> bytes:
    return docx.write_document(
        [docx.Out(kind="paragraph", text="Hello", style="Normal")])


def repack(blob: bytes, replacements=None, extra=None) -> bytes:
    source = zipfile.ZipFile(io.BytesIO(blob))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as out:
        for name in source.namelist():
            out.writestr(name, (replacements or {}).get(name,
                                                        source.read(name)))
        for name, payload in (extra or {}).items():
            out.writestr(name, payload)
    return buffer.getvalue()


class ASmallArchiveThatExpandsWithoutLimit(unittest.TestCase):
    """A zip is compressed, so reading a part decompresses hostile data.

    ``max_cells`` and ``max_blocks`` cannot help: they count things the
    parser has already built, which is after the memory is spent.
    """

    def tighten(self, module, limit=4096):
        """Shrink the limit rather than build a real bomb in a test."""
        original = module.MAX_PART_BYTES
        module.MAX_PART_BYTES = limit
        self.addCleanup(setattr, module, "MAX_PART_BYTES", original)

    def test_a_workbook_part_that_expands_past_the_limit_is_refused(self):
        self.tighten(xlsx)
        sheet = zipfile.ZipFile(io.BytesIO(a_workbook())).read(
            "xl/worksheets/sheet1.xml").decode()
        padded = sheet.replace("</worksheet>",
                               "<!--" + " " * 20000 + "--></worksheet>")
        blob = repack(a_workbook(),
                      {"xl/worksheets/sheet1.xml": padded.encode()})
        with self.assertRaises(xlsx.UnsupportedWorkbook) as caught:
            xlsx.read_workbook(blob)
        self.assertIn("refused rather than decompressed", str(caught.exception))

    def test_a_document_part_that_expands_past_the_limit_is_refused(self):
        self.tighten(docx)
        body = zipfile.ZipFile(io.BytesIO(a_document())).read(
            "word/document.xml").decode()
        padded = body.replace("</w:document>",
                              "<!--" + " " * 20000 + "--></w:document>")
        blob = repack(a_document(), {"word/document.xml": padded.encode()})
        with self.assertRaises(docx.UnsupportedDocument) as caught:
            docx.read_document(blob)
        self.assertIn("refused rather than decompressed", str(caught.exception))

    def test_the_package_check_does_not_decompress_without_limit(self):
        self.tighten(opcpackage)
        blob = repack(a_workbook(),
                      extra={"xl/bomb.xml": b"<a>" + b" " * 20000 + b"</a>"})
        self.assertIn("refused rather than decompressed",
                      opcpackage.problem(blob))

    def test_an_ordinary_file_is_unaffected_by_the_limit(self):
        """A guard that refuses real work is not a guard, it is an outage."""
        self.assertEqual(opcpackage.problem(a_workbook()), "")
        self.assertEqual(len(xlsx.read_workbook(a_workbook()).sheets), 1)


class AnXmlPartThatAttacksTheParser(unittest.TestCase):
    def test_entity_expansion_is_refused(self):
        """Billion laughs: 614 bytes nominally expanding to 10^10 characters.

        Refused by the amplification limit in CPython's expat rather than
        by anything written here, which is worth stating plainly: this
        protection comes from the runtime, so it is a property of the
        Python the product ships on and not of this code.
        """
        entities = "".join(
            f'<!ENTITY e{i} "' + f"&e{i - 1};" * 10 + '">' for i in range(1, 10))
        evil = ('<?xml version="1.0"?><!DOCTYPE worksheet ['
                '<!ENTITY e0 "AAAAAAAAAA">' + entities + ']>'
                "<worksheet><sheetData>&e9;</sheetData></worksheet>")
        blob = repack(a_workbook(),
                      {"xl/worksheets/sheet1.xml": evil.encode()})
        with self.assertRaises(xlsx.UnsupportedWorkbook):
            xlsx.read_workbook(blob)


class APartNamedToEscapeTheArchive(unittest.TestCase):
    def test_a_traversing_part_name_writes_nothing_to_disk(self):
        """Zip slip only bites a reader that extracts. Neither of these do."""
        marker = pathlib.Path(tempfile.gettempdir()) / "solvent-zip-slip-probe"
        marker.unlink(missing_ok=True)
        blob = repack(a_workbook(),
                      extra={"../../../" + marker.name: b"<a/>"})
        opcpackage.problem(blob)
        xlsx.read_workbook(blob)
        self.assertFalse(marker.exists(),
                         "a part name escaped the archive and reached disk")


class ASourceCraftedAgainstTheSniffer(unittest.TestCase):
    """The delimiter is settled from a sample, so the file can lie to it."""

    def run_bytes(self, payload):
        directory = pathlib.Path(tempfile.mkdtemp())
        source = directory / "in.csv"
        source.write_bytes(payload)
        return run_csv_job(source=str(source), requirements=fx.SIMPLE,
                           workdir=str(directory))

    def test_a_file_that_changes_delimiter_after_the_sample_is_not_delivered(self):
        """Semicolons for sixty lines, then commas.

        The sniffer reads the first fifty lines and answers semicolon,
        which is the honest answer to what it was shown. What matters is
        that the wrong reading does not survive to a client: the checks
        recompute from the same file and refuse it.
        """
        head = "Order ID;Customer;Total\n" + "".join(
            f"{1000 + i};Acme;10.00\n" for i in range(60))
        tail = "".join(f"{2000 + i},Beta,20.00\n" for i in range(10))
        report = self.run_bytes((head + tail).encode("utf-8"))
        self.assertFalse(report.delivered, report.escalated)


if __name__ == "__main__":
    unittest.main()
