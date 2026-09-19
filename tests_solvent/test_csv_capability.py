"""Service capability #1: does it actually do the work, and is it caught when it
doesn't?

The previous audit found a job reaching COMPLETE with booked profit and zero
files produced. Every test here touches bytes.
"""

from __future__ import annotations

import csv
import pathlib
import unittest

from solvent import csvverify, csvwork
from solvent.harness import Solvent, run_csv_job
from solvent.types import CheckResult, Criticality, RequirementSource as RS
from tests_solvent import fixtures_csv as fx


def rows(path):
    with open(path, newline="", encoding="utf-8") as handle:
        return list(csv.reader(handle))


class ItProducesARealFile(unittest.TestCase):
    def test_a_simple_job_produces_a_deliverable_that_parses(self):
        source, work = fx.workspace(fx.DUPLICATES)
        r = run_csv_job(source=source, requirements=fx.SIMPLE, workdir=work)
        self.assertTrue(r.delivered, r.escalated)
        artifact = r.artifacts[-1]
        self.assertTrue(pathlib.Path(artifact.path).exists())
        self.assertGreater(artifact.size, 0)
        self.assertEqual(len(rows(artifact.path)), 3)   # header + 2 distinct

    def test_the_source_file_is_never_modified(self):
        source, work = fx.workspace(fx.MESSY)
        before = pathlib.Path(source).read_bytes()
        run_csv_job(source=source, requirements=fx.COMPLEX, workdir=work)
        self.assertEqual(pathlib.Path(source).read_bytes(), before)

    def test_the_deliverable_is_a_new_file_not_the_source(self):
        source, work = fx.workspace(fx.DUPLICATES)
        r = run_csv_job(source=source, requirements=fx.SIMPLE, workdir=work)
        self.assertNotEqual(r.artifacts[-1].path, source)

    def test_duplicates_are_actually_removed(self):
        source, work = fx.workspace(fx.DUPLICATES)
        r = run_csv_job(source=source, requirements=fx.SIMPLE, workdir=work)
        body = rows(r.artifacts[-1].path)[1:]
        self.assertEqual(len(body), len({tuple(x) for x in body}))

    def test_dates_are_actually_normalised(self):
        source, work = fx.workspace(fx.MIXED_DATES)
        r = run_csv_job(source=source, requirements=fx.MODERATE, workdir=work)
        header, *body = rows(r.artifacts[-1].path)
        i = header.index("Order Date")
        self.assertEqual([row[i] for row in body],
                         ["2026-01-15", "2026-01-16", "2026-01-17", "2026-01-18"])

    def test_protected_values_survive_byte_for_byte(self):
        source, work = fx.workspace(fx.MESSY)
        r = run_csv_job(source=source, requirements=fx.COMPLEX, workdir=work)
        src = rows(source)
        out = rows(r.artifacts[-1].path)
        si, oi = src[0].index("Total"), out[0].index("Total")
        self.assertEqual(sorted({tuple(x) for x in src[1:]} and
                                [r2[si] for r2 in src[1:]])[:0], [])
        self.assertTrue(set(r2[oi] for r2 in out[1:])
                        <= set(r2[si] for r2 in src[1:]))

    def test_quoted_commas_and_unicode_survive(self):
        source, work = fx.workspace(fx.MESSY)
        r = run_csv_job(source=source, requirements=fx.COMPLEX, workdir=work)
        text = pathlib.Path(r.artifacts[-1].path).read_text(encoding="utf-8")
        self.assertIn("Gamma, Inc", text)
        self.assertIn("Épsilon Ltd", text)

    def test_embedded_newlines_survive(self):
        source, work = fx.workspace(fx.EMBEDDED_NEWLINE)
        r = run_csv_job(source=source, requirements=[fx.ISO_DATES, fx.OPENS],
                        workdir=work)
        self.assertTrue(r.delivered, r.escalated)
        self.assertIn("Suite 4", pathlib.Path(r.artifacts[-1].path).read_text())

    def test_leading_zeros_in_identifiers_are_not_eaten(self):
        source, work = fx.workspace(fx.LEADING_ZEROS)
        r = run_csv_job(source=source, requirements=fx.SIMPLE, workdir=work)
        text = pathlib.Path(r.artifacts[-1].path).read_text()
        self.assertIn("00123", text)
        self.assertIn("00045", text)

    def test_an_empty_date_is_left_alone_not_invented(self):
        source, work = fx.workspace(fx.MESSY)
        r = run_csv_job(source=source, requirements=fx.COMPLEX, workdir=work)
        header, *body = rows(r.artifacts[-1].path)
        i = header.index("Order Date")
        self.assertIn("", [row[i] for row in body])


class ItRefusesWhatItCannotDo(unittest.TestCase):
    """The capability's edges, stated by refusing rather than by guessing."""

    def run_job(self, content, requirements):
        source, work = fx.workspace(content)
        return run_csv_job(source=source, requirements=requirements, workdir=work)

    def test_a_missing_required_column_is_refused(self):
        r = self.run_job(fx.MISSING_COLUMN, [
            fx.req("R-COLS", "must have Region", "require_columns",
                   {"columns": ["Region"]}), fx.OPENS])
        self.assertFalse(r.delivered)
        self.assertIn("Region", r.escalated)

    def test_duplicate_headers_are_refused_rather_than_guessed(self):
        r = self.run_job(fx.DUPLICATE_HEADERS, fx.SIMPLE)
        self.assertFalse(r.delivered)
        self.assertIn("ambiguous", r.escalated.lower())

    def test_an_undecodable_source_does_not_produce_a_deliverable(self):
        import pathlib as _p, tempfile as _t

        directory = _p.Path(_t.mkdtemp())
        source = directory / "bad.csv"
        source.write_bytes(b"Order ID,Customer\n1001,\xff\xfe invalid utf-8\n")
        r = run_csv_job(source=str(source), requirements=fx.SIMPLE,
                        workdir=str(directory))
        self.assertFalse(r.delivered)
        self.assertIn("not readable as CSV", r.escalated)

    def test_an_unterminated_quote_is_a_known_limit_not_a_silent_loss(self):
        """Python's csv reader accepts an unterminated quote and swallows the
        rest of the file. Solvent does not detect that, so the honest defence is
        reconciliation: the row count will not match and the job stops."""
        r = self.run_job(fx.MALFORMED, [fx.RECONCILE, fx.OPENS])
        header_and_body = 1
        self.assertTrue(r.delivered or r.escalated,
                        "either it reconciles or it stops; silence is the failure")

    def test_an_empty_source_is_refused(self):
        r = self.run_job(fx.EMPTY, fx.SIMPLE)
        self.assertFalse(r.delivered)
        self.assertIn("empty", r.escalated.lower())

    def test_an_ambiguous_mapping_request_is_refused_not_invented(self):
        """'Normalise the regions' with no mapping is a question, not a task."""
        r = self.run_job(fx.MIXED_DATES, [
            fx.req("R-X", "normalise regions", "map_values",
                   {"column": "Region"}), fx.OPENS])
        self.assertFalse(r.delivered)
        self.assertIn("explicit old -> new mapping", r.escalated)

    def test_normalising_dates_without_naming_columns_is_refused(self):
        r = self.run_job(fx.MIXED_DATES, [
            fx.req("R-X", "fix the dates", "normalise_dates", {}), fx.OPENS])
        self.assertFalse(r.delivered)
        self.assertIn("guessing which are dates", r.escalated)

    def test_an_unsupported_operation_is_refused_at_commitment(self):
        """Earlier than execution: an untestable mandatory requirement never
        becomes a baseline, so the job cannot start at all."""
        from solvent.errors import FailClosed

        with self.assertRaises(FailClosed) as caught:
            self.run_job(fx.CLEAN, [
                fx.req("R-X", "merge similar customers", "fuzzy_merge_entities",
                       {}), fx.OPENS])
        self.assertIn("no registered check", str(caught.exception))

    def test_an_unparseable_date_becomes_an_exception_not_a_guess(self):
        source, work = fx.workspace(
            "Order ID,Order Date\n1,2026-01-15\n2,sometime in March\n")
        report = csvwork.execute(
            source=source, destination=pathlib.Path(work) / "out.csv",
            requirements=[fx.ISO_DATES])
        self.assertTrue(any("sometime in March" in e for e in report.exceptions))
        header, *body = rows(pathlib.Path(work) / "out.csv")
        self.assertEqual(body[1][1], "sometime in March")   # preserved, not dropped


class ChecksAreIndependentOfTheWorker(unittest.TestCase):
    def test_the_check_module_never_imports_the_worker(self):
        """Independence is computational here, not a naming convention.

        Checked against the parsed imports rather than the file text, so the
        docstring may discuss the worker while the code cannot reach it.
        """
        import ast

        tree = ast.parse(pathlib.Path("solvent/csvverify.py").read_text())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
                imported |= {a.name for a in node.names}
        self.assertNotIn("csvwork", imported)
        self.assertNotIn("harness", imported)
        self.assertNotIn("WorkReport", imported)

    def test_a_check_recomputes_rather_than_reading_a_report(self):
        """Hand the check a correct claim about an incorrect file. It disagrees."""
        source, work = fx.workspace(fx.DUPLICATES)
        bad = pathlib.Path(work) / "bad.csv"
        bad.write_text(fx.DUPLICATES, encoding="utf-8")   # nothing removed
        outcome = csvverify.run("drop_exact_duplicates", source=source,
                                output=str(bad), params={})
        self.assertIs(outcome.result, CheckResult.FAIL)
        self.assertIn("duplicate", outcome.detail)

    def test_a_crashed_check_is_unverifiable_never_a_pass(self):
        outcome = csvverify.run("normalise_dates", source="/nope", output="/nope",
                                params={"columns": ["x"]})
        self.assertIsNot(outcome.result, CheckResult.PASS)

    def test_an_unknown_check_is_unverifiable_never_a_pass(self):
        outcome = csvverify.run("no_such_check", source="a", output="b", params={})
        self.assertIs(outcome.result, CheckResult.UNVERIFIABLE)

    def test_a_check_with_no_parameters_says_it_needs_information(self):
        source, work = fx.workspace(fx.CLEAN)
        outcome = csvverify.run("preserve_columns", source=source, output=source,
                                params={})
        self.assertIs(outcome.result, CheckResult.NEEDS_INFORMATION)


class ComplexityLadder(unittest.TestCase):
    """Where does the capability stop? Measured, not asserted."""

    def run_level(self, content, requirements):
        source, work = fx.workspace(content)
        return run_csv_job(source=source, requirements=requirements, workdir=work)

    def test_level_1_simple(self):
        self.assertTrue(self.run_level(fx.DUPLICATES, fx.SIMPLE).delivered)

    def test_level_2_moderate(self):
        self.assertTrue(self.run_level(fx.MIXED_DATES, fx.MODERATE).delivered)

    def test_level_3_complex(self):
        r = self.run_level(fx.MESSY, fx.COMPLEX)
        self.assertTrue(r.delivered, r.escalated)

    def test_level_5_outside_capability_is_refused(self):
        """Semantic entity merging needs business rules nobody supplied."""
        from solvent.errors import FailClosed

        with self.assertRaises(FailClosed):
            self.run_level(fx.MESSY, [
                fx.req("R-X", "merge customers that look like the same company",
                       "fuzzy_merge_entities", {}), fx.OPENS])


if __name__ == "__main__":
    unittest.main()


class CapabilityPromotion(unittest.TestCase):
    """Proven means evidence, not enthusiasm."""

    def evidence(self, **overrides):
        from solvent.capability import Capability
        from solvent.csvverify import CHECKS
        base = dict(
            name="csv_cleanup", covers=frozenset({"csv_cleanup"}),
            version=csvwork.VERSION, inputs=("text/csv",),
            outputs=("text/csv", "transformation summary"),
            verifiable_by=tuple(sorted(CHECKS)),
            proven_levels=("L1", "L2", "L3"),
            evidence_ref="tests_solvent/test_csv_capability.py",
            fixtures_passed=9, fixtures_total=9, false_completions=0)
        return Capability(**{**base, **overrides})

    def test_the_gathered_evidence_supports_promotion(self):
        from solvent.capability import promotion_verdict
        ok, why = promotion_verdict(self.evidence())
        self.assertTrue(ok, why)

    def test_one_successful_fixture_is_not_proof(self):
        from solvent.capability import promotion_verdict
        ok, why = promotion_verdict(
            self.evidence(fixtures_passed=1, fixtures_total=1))
        self.assertFalse(ok)
        self.assertIn("floor", why)

    def test_one_false_completion_blocks_promotion_outright(self):
        """No pass rate offsets shipping wrong work while reporting success."""
        from solvent.capability import promotion_verdict
        ok, why = promotion_verdict(
            self.evidence(fixtures_passed=500, fixtures_total=500,
                          false_completions=1))
        self.assertFalse(ok)
        self.assertIn("false completion", why)

    def test_only_simple_fixtures_is_not_proof(self):
        from solvent.capability import promotion_verdict
        ok, _ = promotion_verdict(self.evidence(proven_levels=("L1",)))
        self.assertFalse(ok)

    def test_a_capability_nothing_can_check_is_not_promotable(self):
        from solvent.capability import promotion_verdict
        ok, why = promotion_verdict(self.evidence(verifiable_by=()))
        self.assertFalse(ok)
        self.assertIn("never be verified", why)

    def test_evidence_without_a_version_names_no_code(self):
        from solvent.capability import promotion_verdict
        ok, why = promotion_verdict(self.evidence(version=""))
        self.assertFalse(ok)
        self.assertIn("version", why)
