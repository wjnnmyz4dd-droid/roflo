"""The ``.docx`` layer: what it reads, what it refuses, and whether anyone agrees.

A round trip through one implementation proves only that it agrees with itself.
So the cross-validation tests here read this module's output with
``python-docx`` and read ``python-docx``'s output with this module. That
library is a **test-only** dependency: Solvent itself imports the standard
library and nothing else, because the Windows installer builds a virtual
environment with no packages in it. Those tests skip when it is absent.

The tests that matter most are the tracked-change ones. A document with a
struck-out price contains two prices, and a reader that collects every text run
returns the one the author deleted. That is not a formatting detail -- it is an
extraction reporting a figure the document does not say.
"""

from __future__ import annotations

import unittest
import zipfile

from solvent import docx

try:
    import docx as python_docx
except ImportError:  # pragma: no cover - the dependency is test-only
    python_docx = None


W = docx._W


def wrap(body: str) -> bytes:
    """A minimal .docx around this body XML, for testing specific elements."""
    parts = {
        "[Content_Types].xml": docx._CONTENT_TYPES,
        "_rels/.rels": docx._ROOT_RELS,
        "word/_rels/document.xml.rels": docx._DOC_RELS,
        "word/styles.xml": docx._styles_xml(),
        "word/document.xml":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>',
    }
    buffer = bytearray()

    class Sink:
        def write(self, data):
            buffer.extend(data)
            return len(data)

        def flush(self):
            return None

        def tell(self):
            return len(buffer)

        def seekable(self):
            return False

    with zipfile.ZipFile(Sink(), "w") as archive:
        for name, text in parts.items():
            archive.writestr(name, text)
    return bytes(buffer)


def para(runs: str, style: str = "") -> str:
    properties = (f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else "")
    return f"<w:p>{properties}{runs}</w:p>"


def run(text: str) -> str:
    return f'<w:r><w:t xml:space="preserve">{text}</w:t></w:r>'


class Refusals(unittest.TestCase):
    """A document this module cannot read correctly is not read approximately."""

    def refuse(self, data: bytes) -> str:
        with self.assertRaises(docx.UnsupportedDocument) as caught:
            docx.read_document(data)
        return str(caught.exception)

    def test_a_file_that_is_not_a_zip(self):
        self.assertIn("not a ZIP", self.refuse(b"PK not really"))

    def test_an_empty_file(self):
        self.assertIn("not a ZIP", self.refuse(b""))

    def test_a_pdf_offered_as_a_word_document(self):
        self.assertIn("not a ZIP", self.refuse(b"%PDF-1.7\n1 0 obj\n"))

    def test_a_zip_with_no_document_part(self):
        buffer = bytearray()

        class Sink:
            def write(self, data):
                buffer.extend(data)
                return len(data)

            def flush(self):
                return None

            def tell(self):
                return len(buffer)

            def seekable(self):
                return False

        with zipfile.ZipFile(Sink(), "w") as archive:
            archive.writestr("hello.txt", "not a document")
        self.assertIn("word/document.xml", self.refuse(bytes(buffer)))

    def test_a_macro_enabled_document(self):
        data = bytearray(wrap(para(run("x"))))
        buffer = bytearray()

        class Sink:
            def write(self, d):
                buffer.extend(d)
                return len(d)

            def flush(self):
                return None

            def tell(self):
                return len(buffer)

            def seekable(self):
                return False

        source = zipfile.ZipFile(docx._BytesIO(bytes(data)))
        with zipfile.ZipFile(Sink(), "w") as out:
            for name in source.namelist():
                out.writestr(name, source.read(name))
            out.writestr("word/vbaProject.bin", b"\x00\x01")
        self.assertIn("macro", self.refuse(bytes(buffer)))

    def test_a_document_whose_text_lives_somewhere_else(self):
        buffer = bytearray()

        class Sink:
            def write(self, d):
                buffer.extend(d)
                return len(d)

            def flush(self):
                return None

            def tell(self):
                return len(buffer)

            def seekable(self):
                return False

        rels = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<Relationships xmlns="{docx._PKG_REL}">'
            '<Relationship Id="rId9" Target="other.docx" TargetMode="External" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/'
            'relationships/subDocument"/></Relationships>')
        source = zipfile.ZipFile(docx._BytesIO(wrap(para(run("local")))))
        replaced = "word/_rels/document.xml.rels"
        with zipfile.ZipFile(Sink(), "w") as out:
            for name in source.namelist():
                if name != replaced:
                    out.writestr(name, source.read(name))
            out.writestr(replaced, rels)
        message = self.refuse(bytes(buffer))
        self.assertIn("somewhere else", message)
        self.assertIn("subDocument", message)

    def test_malformed_xml_in_the_document_part(self):
        buffer = bytearray()

        class Sink:
            def write(self, d):
                buffer.extend(d)
                return len(d)

            def flush(self):
                return None

            def tell(self):
                return len(buffer)

            def seekable(self):
                return False

        with zipfile.ZipFile(Sink(), "w") as out:
            out.writestr("word/document.xml", "<w:document><unclosed>")
        self.assertIn("well-formed", self.refuse(bytes(buffer)))

    def test_a_document_part_with_no_body(self):
        data = wrap("")
        source = zipfile.ZipFile(docx._BytesIO(data))
        buffer = bytearray()

        class Sink:
            def write(self, d):
                buffer.extend(d)
                return len(d)

            def flush(self):
                return None

            def tell(self):
                return len(buffer)

            def seekable(self):
                return False

        with zipfile.ZipFile(Sink(), "w") as out:
            for name in source.namelist():
                if name == "word/document.xml":
                    out.writestr(name, f'<w:document xmlns:w="{W}"/>')
                else:
                    out.writestr(name, source.read(name))
        self.assertIn("no body", self.refuse(bytes(buffer)))

    def test_more_blocks_than_the_limit_is_refused_not_truncated(self):
        body = para(run("x")) * 12
        with self.assertRaises(docx.UnsupportedDocument) as caught:
            docx.read_document(wrap(body), max_blocks=10)
        self.assertIn("more than 10 blocks", str(caught.exception))

    def test_a_style_the_writer_does_not_define_is_refused(self):
        with self.assertRaises(docx.UnsupportedDocument) as caught:
            docx.write_document([docx.Out(text="x", style="FancyQuote")])
        message = str(caught.exception)
        self.assertIn("FancyQuote", message)
        self.assertIn("Normal", message, "the refusal names what is available")


class TrackedChanges(unittest.TestCase):
    """The document says one thing. A naive reader returns two."""

    def test_deleted_text_is_not_what_the_document_says(self):
        body = para(
            run("The fee is ") +
            '<w:del><w:r><w:delText xml:space="preserve">4,000</w:delText>'
            "</w:r></w:del>" +
            run("1,250") + run(" per month."))
        read = docx.read_document(wrap(body))
        self.assertEqual(read.paragraphs[0].text,
                         "The fee is 1,250 per month.")
        self.assertNotIn("4,000", read.text)
        self.assertTrue(read.has_revisions)
        self.assertTrue(any("deleted" in d for d in read.dropped))

    def test_inserted_text_is_what_the_document_says(self):
        body = para(run("Term: ") +
                    f'<w:ins>{run("36 months")}</w:ins>')
        read = docx.read_document(wrap(body))
        self.assertEqual(read.paragraphs[0].text, "Term: 36 months")
        self.assertTrue(read.has_revisions,
                        "an unaccepted revision is reported, not hidden")

    def test_a_document_with_no_revisions_does_not_claim_any(self):
        read = docx.read_document(wrap(para(run("Plain text."))))
        self.assertFalse(read.has_revisions)
        self.assertEqual(read.dropped, [])

    def test_a_field_instruction_is_not_content(self):
        body = para(run("Page ") +
                    '<w:r><w:instrText>PAGE</w:instrText></w:r>')
        read = docx.read_document(wrap(body))
        self.assertEqual(read.paragraphs[0].text, "Page")
        self.assertTrue(any("field instruction" in d for d in read.dropped))

    def test_a_mergefield_names_itself_as_unreadable_rather_than_as_a_value(self):
        body = para(run("Client: ") +
                    '<w:r><w:instrText> MERGEFIELD Client </w:instrText></w:r>')
        read = docx.read_document(wrap(body))
        self.assertNotIn("MERGEFIELD", read.text)
        self.assertTrue(any("MERGEFIELD" in d for d in read.dropped))

    def test_a_comment_is_a_reviewers_note_not_the_document(self):
        body = para(run("Agreed.") + '<w:commentReference w:id="1"/>')
        read = docx.read_document(wrap(body))
        self.assertEqual(read.paragraphs[0].text, "Agreed.")
        self.assertTrue(any("comment" in d for d in read.dropped))

    def test_an_embedded_image_is_recorded_as_not_text(self):
        body = para(run("See figure.") + "<w:drawing/>")
        read = docx.read_document(wrap(body))
        self.assertTrue(any("image" in d for d in read.dropped))


class WhatItReads(unittest.TestCase):

    def test_heading_levels_come_from_the_style(self):
        body = (para(run("T"), "Title") + para(run("A"), "Heading1")
                + para(run("B"), "Heading 2") + para(run("C"), "Normal"))
        read = docx.read_document(wrap(body))
        self.assertEqual([(p.text, p.level) for p in read.paragraphs],
                         [("T", 0), ("A", 1), ("B", 2), ("C", None)])

    def test_a_paragraph_with_no_explicit_style_has_no_style_name(self):
        read = docx.read_document(wrap(para(run("x"))))
        self.assertEqual(read.paragraphs[0].style, "")
        self.assertIsNone(read.paragraphs[0].level)

    def test_a_line_break_separates_words_rather_than_joining_them(self):
        body = para(run("first") + "<w:r><w:br/></w:r>" + run("second"))
        self.assertEqual(docx.read_document(wrap(body)).paragraphs[0].text,
                         "first second")

    def test_a_tab_is_kept_because_it_separates_a_label_from_its_value(self):
        body = para(run("Invoice") + "<w:r><w:tab/></w:r>" + run("A-1001"))
        self.assertIn("\t", docx.read_document(wrap(body)).paragraphs[0].text)

    def test_runs_split_mid_word_by_a_spellchecker_rejoin(self):
        body = para(run("Bore") + run("alis") + run(" GmbH"))
        self.assertEqual(docx.read_document(wrap(body)).paragraphs[0].text,
                         "Borealis GmbH")

    def test_document_order_is_preserved_across_paragraphs_and_tables(self):
        body = (para(run("before")) +
                "<w:tbl><w:tr><w:tc>" + para(run("cell")) + "</w:tc></w:tr></w:tbl>" +
                para(run("after")))
        read = docx.read_document(wrap(body))
        kinds = [type(b).__name__ for b in read.blocks]
        self.assertEqual(kinds, ["Paragraph", "Table", "Paragraph"])

    def test_a_table_row_of_two_cells_reads_as_a_labelled_value(self):
        body = ("<w:tbl><w:tr>"
                "<w:tc>" + para(run("Invoice Number")) + "</w:tc>"
                "<w:tc>" + para(run("A-1001")) + "</w:tc>"
                "</w:tr></w:tbl>")
        read = docx.read_document(wrap(body))
        self.assertIn("Invoice Number: A-1001", read.lines())

    def test_merged_cells_are_reported_rather_than_silently_shifted(self):
        body = ("<w:tbl><w:tr><w:tc>"
                '<w:tcPr><w:gridSpan w:val="2"/></w:tcPr>'
                + para(run("spans two")) + "</w:tc></w:tr></w:tbl>")
        read = docx.read_document(wrap(body))
        self.assertTrue(any("merged" in d for d in read.dropped))

    def test_text_inside_a_content_control_is_read_and_the_binding_is_not(self):
        body = ("<w:sdt><w:sdtContent>" + para(run("Signed by: A. Nakamura")) +
                "</w:sdtContent></w:sdt>")
        read = docx.read_document(wrap(body))
        self.assertEqual(read.paragraphs[0].text, "Signed by: A. Nakamura")
        self.assertTrue(any("content control" in d for d in read.dropped))

    def test_an_unmodelled_element_is_named_rather_than_passed_over(self):
        read = docx.read_document(wrap("<w:customXml/>"))
        self.assertTrue(any("customXml" in d for d in read.dropped))

    def test_tables_are_not_paragraph_text(self):
        body = "<w:tbl><w:tr><w:tc>" + para(run("cell")) + "</w:tc></w:tr></w:tbl>"
        read = docx.read_document(wrap(body))
        self.assertEqual(read.text, "")
        self.assertEqual(read.tables[0].rows, (("cell",),))


class WhatItWrites(unittest.TestCase):

    def test_the_same_content_produces_the_same_bytes(self):
        blocks = [docx.Out(text="Report", style="Title"),
                  docx.Out(text="Body text.")]
        self.assertEqual(docx.write_document(blocks),
                         docx.write_document(list(blocks)))

    def test_xml_special_characters_survive_a_round_trip(self):
        text = 'Fee & "terms" <per clause 4> 5 > 3'
        data = docx.write_document([docx.Out(text=text)])
        self.assertEqual(docx.read_document(data).paragraphs[0].text, text)

    def test_a_newline_in_a_paragraph_becomes_a_line_break(self):
        data = docx.write_document([docx.Out(text="first\nsecond")])
        self.assertEqual(docx.read_document(data).paragraphs[0].text,
                         "first second")

    def test_an_empty_paragraph_is_written_and_read_back_as_empty(self):
        data = docx.write_document([docx.Out(text="")])
        self.assertEqual(docx.read_document(data).paragraphs[0].text, "")

    def test_a_table_with_ragged_rows_is_padded_to_a_rectangle(self):
        data = docx.write_document([docx.Out(
            kind="table", rows=(("a", "b", "c"), ("d",)))])
        self.assertEqual(docx.read_document(data).tables[0].rows,
                         (("a", "b", "c"), ("d", "", "")))

    def test_every_style_the_writer_offers_round_trips(self):
        for style in docx._STYLES:
            with self.subTest(style=style):
                data = docx.write_document([docx.Out(text="x", style=style)])
                self.assertEqual(
                    docx.read_document(data).paragraphs[0].style, style)

    def test_unicode_survives(self):
        text = "Szabó & Oyelaran — 1 250,00 €"
        data = docx.write_document([docx.Out(text=text)])
        self.assertEqual(docx.read_document(data).paragraphs[0].text, text)


@unittest.skipIf(python_docx is None, "python-docx is a test-only dependency")
class AnotherLibraryAgrees(unittest.TestCase):
    """A round trip through one implementation proves only self-agreement."""

    def open_mine(self, blocks):
        import pathlib
        import tempfile

        path = pathlib.Path(tempfile.mkdtemp()) / "mine.docx"
        path.write_bytes(docx.write_document(blocks))
        return python_docx.Document(str(path))

    def test_python_docx_reads_the_paragraphs_this_module_writes(self):
        doc = self.open_mine([
            docx.Out(text="Quarterly Statement", style="Title"),
            docx.Out(text="Summary", style="Heading1"),
            docx.Out(text="Invoice Number: A-1001")])
        self.assertEqual([p.text for p in doc.paragraphs],
                         ["Quarterly Statement", "Summary",
                          "Invoice Number: A-1001"])

    def test_python_docx_resolves_the_styles_this_module_declares(self):
        doc = self.open_mine([docx.Out(text="H", style="Heading2")])
        self.assertEqual(doc.paragraphs[0].style.name, "Heading 2")

    def test_python_docx_reads_the_escaped_characters_unescaped(self):
        text = 'Fee & "terms" <clause 4>'
        doc = self.open_mine([docx.Out(text=text)])
        self.assertEqual(doc.paragraphs[0].text, text)

    def test_python_docx_reads_the_table_cells(self):
        doc = self.open_mine([docx.Out(
            kind="table", rows=(("Field", "Value"), ("Client", "Acme Ltd")))])
        self.assertEqual(
            [[c.text for c in row.cells] for row in doc.tables[0].rows],
            [["Field", "Value"], ["Client", "Acme Ltd"]])

    def test_this_module_reads_what_python_docx_writes(self):
        import pathlib
        import tempfile

        path = pathlib.Path(tempfile.mkdtemp()) / "theirs.docx"
        out = python_docx.Document()
        out.add_heading("Contract", level=0)
        out.add_heading("Parties", level=2)
        out.add_paragraph("Supplier: Borealis GmbH")
        table = out.add_table(rows=2, cols=2)
        table.cell(0, 0).text = "Term"
        table.cell(0, 1).text = "Value"
        table.cell(1, 0).text = "Fee"
        table.cell(1, 1).text = "480.50"
        out.save(str(path))

        read = docx.read_document(path.read_bytes())
        self.assertEqual([(p.text, p.level) for p in read.paragraphs],
                         [("Contract", 0), ("Parties", 2),
                          ("Supplier: Borealis GmbH", None)])
        self.assertEqual(read.tables[0].rows,
                         (("Term", "Value"), ("Fee", "480.50")))

    def test_a_tracked_deletion_written_by_another_tool_is_still_excluded(self):
        """The deletion is constructed by hand; python-docx reads it either way.

        Worth having because it shows the two readers *disagreeing* for the
        right reason: python-docx's ``paragraph.text`` includes the deleted
        run, and this module's does not. Both are defensible as format
        readers; only one of them is safe to extract a client's figures from.
        """
        import pathlib
        import tempfile

        body = para(docx_run("The fee is ") +
                    '<w:del><w:r><w:delText>4,000</w:delText></w:r></w:del>' +
                    docx_run("1,250"))
        path = pathlib.Path(tempfile.mkdtemp()) / "revised.docx"
        path.write_bytes(wrap(body))
        theirs = python_docx.Document(str(path))
        mine = docx.read_document(path.read_bytes())
        self.assertEqual(mine.paragraphs[0].text, "The fee is 1,250")
        self.assertNotIn("4,000", mine.text)
        self.assertNotIn("4,000", theirs.paragraphs[0].text,
                         "if this ever fails, python-docx changed and the "
                         "disagreement above is the one to re-examine")


def docx_run(text: str) -> str:
    return run(text)


if __name__ == "__main__":
    unittest.main()
