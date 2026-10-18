"""A second opinion on a Markdown deliverable, from somebody else's parser.

Measured in the 100-opportunity acceptance run: three document-processing
jobs above T1 blocked on the owner because no independently authored reader
for ``.md`` existed. The worker renders Markdown and the capability's own
checker reads it back by matching line prefixes — ``line.startswith("## ")``
— which is one project's idea of Markdown consulted twice.

``markdown-it-py`` is a CommonMark implementation ported from the
JavaScript ``markdown-it``, written by different people from a published
specification. It disagrees with a line-prefix scan in exactly the places
that matter, and those disagreements are tested here rather than asserted.

A second parser is not on its own evidence that the document is *right*, so
the structural tests and the substantive ones are kept apart below.
"""

from __future__ import annotations

import json
import pathlib
import tempfile
import unittest

from solvent import independent
from solvent.errors import FailClosed
from solvent.types import (ConsequenceTier, Requirement,
                           RequirementSource)


def req(check: str, params: dict | None = None) -> Requirement:
    return Requirement(
        id=f"R-{check}-{sorted((params or {}).items())}", text=check,
        source=RequirementSource.CLIENT_CONFIRMED_STRUCTURED,
        acceptance="as agreed", check=check, params=params or {})


FACTS = {"client": "Acme Corp", "period": "Q1 2026",
         "rows": [{"item": "Consulting", "amount": "100.00"},
                  {"item": "Support", "amount": "350.50"}]}

GOOD = """## Overview

- client: Acme Corp
- period: Q1 2026

## Lines

| item | amount |
|---|---|
| Consulting | 100.00 |
| Support | 350.50 |

## Totals

- amount total: 450.50
"""

BRIEF = [req("report_section", {"kind": "facts", "heading": "Overview",
                                "fields": ["client", "period"]}),
         req("report_section", {"kind": "table", "heading": "Lines",
                                "columns": ["item", "amount"]}),
         req("report_section", {"kind": "total", "heading": "Totals",
                                "column": "amount"})]


class TheReaderIsAvailableAndIndependent(unittest.TestCase):

    def test_markdown_has_an_independent_reader(self):
        module, description = independent._reader_for("md")
        self.assertIsNotNone(
            module, "no independently authored Markdown reader is wired")
        self.assertIn("independently", description)

    def test_it_is_not_the_capability_s_own_checker(self):
        module, _ = independent._reader_for("md")
        self.assertNotIn("solvent", (module.__name__ or "").lower())
        self.assertFalse(
            (module.__file__ or "").startswith(
                str(pathlib.Path(independent.__file__).parent)),
            "the 'independent' reader ships inside this project")


class ItReadsMarkdownAsMarkdown(unittest.TestCase):
    """The disagreements with a line-prefix scan, which are the point."""

    def setUp(self):
        self.box = tempfile.TemporaryDirectory()
        self.addCleanup(self.box.cleanup)
        self.dir = pathlib.Path(self.box.name)
        self.module, _ = independent._reader_for("md")

    def read(self, text):
        path = self.dir / "doc.md"
        path.write_text(text, encoding="utf-8")
        blocks, problem = independent._read_independently(
            self.module, "md", str(path))
        self.assertEqual(problem, "")
        return blocks

    def headings(self, text):
        return [b["text"] for b in self.read(text) if b["kind"] == "heading"]

    def test_headings_are_found(self):
        self.assertEqual(self.headings(GOOD),
                         ["Overview", "Lines", "Totals"])

    def test_a_heading_inside_a_code_fence_is_not_a_heading(self):
        """A line-prefix scan counts this one. A parser does not."""
        text = "## Real\n\n```\n## Not a heading\n```\n"
        self.assertEqual(self.headings(text), ["Real"])

    def test_a_setext_heading_is_a_heading(self):
        """A line-prefix scan misses this one."""
        self.assertEqual(self.headings("Overview\n--------\n\nbody\n"),
                         ["Overview"])

    def test_tables_are_read_as_rows(self):
        tables = [b for b in self.read(GOOD) if b["kind"] == "table"]
        self.assertEqual(len(tables), 1)
        self.assertEqual(tables[0]["rows"][0], ["item", "amount"])
        self.assertEqual(tables[0]["rows"][1], ["Consulting", "100.00"])

    def test_an_escaped_pipe_stays_inside_its_cell(self):
        text = "| a | b |\n|---|---|\n| x \\| y | z |\n"
        rows = [b for b in self.read(text) if b["kind"] == "table"][0]["rows"]
        self.assertEqual(rows[1], ["x | y", "z"])

    def test_lists_are_read_as_items(self):
        items = [b["text"] for b in self.read(GOOD) if b["kind"] == "list_item"]
        self.assertIn("client: Acme Corp", items)

    def test_links_keep_their_text(self):
        blocks = self.read("See [the invoice](https://example.test/i) please\n")
        self.assertTrue(any("the invoice" in b["text"] for b in blocks))

    def test_a_large_document_is_read(self):
        rows = "\n".join(f"| item{n} | {n}.00 |" for n in range(2_000))
        text = f"## Big\n\n| item | amount |\n|---|---|\n{rows}\n"
        tables = [b for b in self.read(text) if b["kind"] == "table"]
        self.assertEqual(len(tables[0]["rows"]), 2_001)

    def test_malformed_markdown_still_reads_as_something(self):
        """CommonMark has no parse errors; it must not crash or hang."""
        blocks = self.read("## \n\n|||\n|\n```\nunclosed\n")
        self.assertIsInstance(blocks, list)


class ItChecksTheDocumentNotJustTheSyntax(unittest.TestCase):
    """Independence and correctness are separate claims; this is correctness."""

    def setUp(self):
        self.box = tempfile.TemporaryDirectory()
        self.addCleanup(self.box.cleanup)
        self.dir = pathlib.Path(self.box.name)
        self.source = self.dir / "facts.json"
        self.source.write_text(json.dumps(FACTS), encoding="utf-8")
        self.module, _ = independent._reader_for("md")

    def findings(self, text, requirements=None):
        path = self.dir / "report.md"
        path.write_text(text, encoding="utf-8")
        delivered, problem = independent._read_independently(
            self.module, "md", str(path))
        self.assertEqual(problem, "")
        return independent._recompute_independently(
            self.module, "md", str(self.source), delivered,
            list(BRIEF if requirements is None else requirements))

    def agreed(self, text, **kw):
        found = self.findings(text, **kw)
        self.assertTrue(found, "nothing was independently checked at all")
        return [f for f in found if not f[1]]

    def test_a_correct_report_is_agreed(self):
        self.assertEqual(self.agreed(GOOD), [], "a correct report was faulted")

    def test_a_missing_required_section_is_caught(self):
        bad = GOOD.replace("## Totals\n\n- amount total: 450.50\n", "")
        self.assertTrue(self.agreed(bad), "a missing section passed")

    def test_a_wrong_total_is_caught(self):
        bad = GOOD.replace("450.50", "999.00")
        self.assertTrue(self.agreed(bad), "a fabricated total passed")

    def test_an_omitted_row_is_caught(self):
        bad = GOOD.replace("| Support | 350.50 |\n", "")
        self.assertTrue(self.agreed(bad), "a dropped row passed")

    def test_an_invented_row_is_caught(self):
        bad = GOOD.replace("| Support | 350.50 |",
                           "| Support | 350.50 |\n| Phantom | 1.00 |")
        self.assertTrue(self.agreed(bad), "an invented row passed")

    def test_a_corrupted_fact_is_caught(self):
        bad = GOOD.replace("client: Acme Corp", "client: Acme Corporation")
        self.assertTrue(self.agreed(bad), "a corrupted fact passed")

    def test_a_heading_hidden_in_a_code_fence_does_not_satisfy_a_section(self):
        """The disagreement that matters, as a correctness claim."""
        bad = GOOD.replace("## Totals", "```\n## Totals\n```\n\n## Elsewhere")
        self.assertTrue(self.agreed(bad),
                        "a heading inside a code fence satisfied a section")

    def test_a_brief_with_nothing_checkable_yields_no_findings(self):
        """Refused upstream as 'review by a person is required', not passed."""
        self.assertEqual(
            self.findings(GOOD, requirements=[req("report_opens")]), [])


class TheReviewRefusesRatherThanDegrading(unittest.TestCase):

    def setUp(self):
        self.box = tempfile.TemporaryDirectory()
        self.addCleanup(self.box.cleanup)
        self.dir = pathlib.Path(self.box.name)
        self.source = self.dir / "facts.json"
        self.source.write_text(json.dumps(FACTS), encoding="utf-8")

    class _Artifact:
        def __init__(self, path):
            self.path, self.digest, self.id = str(path), "d" * 16, "art_1"

    def review(self, text, requirements=BRIEF):
        from solvent.audit import AuditLog
        from solvent.store import Store
        store = Store(str(self.dir / "a.db"))
        self.addCleanup(store.close)
        audit = AuditLog(store)
        path = self.dir / "report.md"
        path.write_text(text, encoding="utf-8")
        return independent.differential_review(
            audit, job_id="job_x", source=str(self.source),
            artifact=self._Artifact(path), requirement_ids=["R1"],
            consequence=ConsequenceTier.C_HIGH,
            requirements=requirements)

    def test_a_correct_report_is_reviewed(self):
        self.assertTrue(self.review(GOOD))

    def test_a_wrong_total_refuses(self):
        with self.assertRaises(FailClosed) as caught:
            self.review(GOOD.replace("450.50", "999.00"))
        self.assertIn("disagrees", str(caught.exception))

    def test_an_unverifiable_brief_refuses(self):
        with self.assertRaises(FailClosed) as caught:
            self.review(GOOD, requirements=[req("report_opens")])
        self.assertIn("person", str(caught.exception))


class TheCaseThatWasBlockedInAcceptance(unittest.TestCase):
    """document-processing renders Markdown from a client's .docx.

    Three of the hundred jobs in the acceptance run stopped here for want
    of a reader. Both sides are now read by somebody else's code: the
    source with python-docx, the deliverable with markdown-it-py, and
    neither is the worker that produced it.
    """

    def setUp(self):
        from tests_solvent.skillmatrix import (_generate, _requirements_for,
                                               _source)
        self.box = tempfile.TemporaryDirectory()
        self.addCleanup(self.box.cleanup)
        self.dir = pathlib.Path(self.box.name)
        # Case 2 of the matrix is the apply_template one, whose deliverable
        # is Markdown. The same case the acceptance run could not review.
        case = _generate("document-processing", 20411277, 2)
        self.requirements = _requirements_for("document-processing", case)
        self.source = self.dir / (case.source_name or "source.dat")
        self.source.write_bytes(_source(case))
        self.module, _ = independent._reader_for("md")
        self.deliverable = self.produce(case)

    def produce(self, case):
        from solvent.capability import Capability
        from solvent.checks import checks_for
        from solvent.harness import run_csv_job
        from solvent import docverify
        run_csv_job(
            source=str(self.source), requirements=self.requirements,
            workdir=str(self.dir), capability="document-processing",
            provision=Capability(
                name="document-processing",
                covers=frozenset(checks_for("document-processing")),
                version="document-processing/1.0", proven=True,
                verifiable_by=tuple(sorted(docverify.CHECKS))))
        produced = sorted(self.dir.glob("*.md"))
        self.assertTrue(produced, "the worker produced no Markdown")
        return produced[-1]

    def findings(self, text=None):
        path = self.deliverable
        if text is not None:
            path = self.dir / "tampered.md"
            path.write_text(text, encoding="utf-8")
        blocks, problem = independent._read_independently(
            self.module, "md", str(path))
        self.assertEqual(problem, "")
        return independent._recompute_independently(
            self.module, "md", str(self.source), blocks, self.requirements)

    def test_the_brief_is_now_reviewable_at_all(self):
        found = self.findings()
        self.assertTrue(found, "still nothing a second reader can check")

    def test_the_worker_s_own_output_is_agreed(self):
        self.assertEqual([f for f in self.findings() if not f[1]], [],
                         "correct work was faulted")

    def test_a_fabricated_table_row_is_caught(self):
        text = self.deliverable.read_text(encoding="utf-8")
        tampered = text.rstrip("\n") + "\n| Phantom charge | 9999.00 |\n"
        disputes = [f for f in self.findings(tampered) if not f[1]]
        self.assertTrue(disputes, "an invented line item passed the review")

    def test_a_corrupted_field_value_is_caught(self):
        text = self.deliverable.read_text(encoding="utf-8")
        tampered = text.replace("Kowalski GmbH", "Someone Else Ltd")
        self.assertNotEqual(tampered, text, "the fixture changed shape")
        disputes = [f for f in self.findings(tampered) if not f[1]]
        self.assertTrue(disputes, "a rewritten client name passed the review")

    def test_a_changed_amount_is_caught(self):
        text = self.deliverable.read_text(encoding="utf-8")
        tampered = text.replace("| 93.12 |", "| 9312.00 |")
        self.assertNotEqual(tampered, text, "the fixture changed shape")
        disputes = [f for f in self.findings(tampered) if not f[1]]
        self.assertTrue(disputes, "an inflated amount passed the review")

    def test_the_source_is_read_by_a_different_library(self):
        module, description = independent._reader_for("docx")
        self.assertIsNotNone(module)
        self.assertNotEqual(module.__name__, self.module.__name__)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
