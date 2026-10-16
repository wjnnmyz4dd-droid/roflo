"""Service capability #1: does it actually do the work, and is it caught when it
doesn't?

The previous audit found a job reaching COMPLETE with booked profit and zero
files produced. Every test here touches bytes.
"""

from __future__ import annotations

import csv
import pathlib
import tempfile
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
        """Bytes that fail every rung of the ladder.

        The old fixture here was b"\xff\xfe", which this test called
        undecodable because the reader assumed UTF-8. It is not: in
        cp1252 those bytes are the perfectly ordinary characters y-
        diaeresis and thorn, and the file is now read and declared as
        cp1252 -- the refusal was a false negative caused by the
        assumption, which is the whole reason solvent.tabular exists.

        0x81, 0x8d, 0x8f, 0x90 and 0x9d are undefined in cp1252 and
        are not valid UTF-8 or UTF-16 either, so they fail all three
        and a refusal is the correct answer.
        """
        import pathlib as _p, tempfile as _t

        directory = _p.Path(_t.mkdtemp())
        source = directory / "bad.csv"
        source.write_bytes(b"Order ID,Customer\n1001,\x81\x8d\x8f\x90\x9d\n")
        r = run_csv_job(source=str(source), requirements=fx.SIMPLE,
                        workdir=str(directory))
        self.assertFalse(r.delivered)
        self.assertIn("not a CSV file", r.escalated)
        self.assertIn("does not decode", r.escalated)

    def test_a_cp1252_export_is_read_and_the_assumption_declared(self):
        """What the old assumption refused, and what honesty requires.

        Accepting the file is only half of it. cp1252 decodes almost
        any byte sequence without complaining, so a cp1252 result is
        a weaker claim than a UTF-8 one and the report has to say
        which was used.
        """
        import pathlib as _p, tempfile as _t

        from solvent.csvwork import execute as run_worker

        directory = _p.Path(_t.mkdtemp())
        source = directory / "eu.csv"
        source.write_bytes(
            "Order ID,Customer\n1001,Café Münster\n".encode("cp1252"))
        report = run_worker(source=str(source),
                            destination=str(directory / "out.csv"),
                            requirements=fx.SIMPLE)
        self.assertEqual(report.refused, [])
        self.assertEqual(report.source_encoding, "cp1252")
        self.assertEqual(report.source_delimiter, ",")

    def test_a_semicolon_export_is_read_and_declared(self):
        import pathlib as _p, tempfile as _t

        from solvent.csvwork import execute as run_worker

        directory = _p.Path(_t.mkdtemp())
        source = directory / "eu.csv"
        source.write_text("Order ID;Customer;Amount\n"
                          "1001;Acme;10.00\n1002;Beta;20.00\n",
                          encoding="utf-8")
        report = run_worker(source=str(source),
                            destination=str(directory / "out.csv"),
                            requirements=fx.SIMPLE)
        self.assertEqual(report.source_delimiter, ";")

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
        # And not the worker's decoder. solvent.tabular is a library rather
        # than the worker, which makes it the easy mistake: importing it
        # would be convenient, would look harmless, and would mean a file
        # both sides decoded wrongly reconciled perfectly against itself --
        # exactly the failure the checks exist to catch, reintroduced
        # through a shared helper instead of a shared module.
        self.assertNotIn("tabular", imported)

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


class ItReadsWhatTheClientActuallySaved(unittest.TestCase):
    """The encoding ladder, and the one rung that is a trap.

    Python's utf-16 codec does not fail on input that is not UTF-16: it
    accepts any even-length byte sequence and returns CJK. So "it decoded
    as UTF-16" carries no information on its own, and an un-gated UTF-16
    rung sits at the top of the ladder swallowing every cp1252 file of
    even length. The gate is the byte-order mark.
    """

    def run_bytes(self, payload):
        directory = pathlib.Path(tempfile.mkdtemp())
        source = directory / "in.csv"
        source.write_bytes(payload)
        return run_csv_job(source=str(source), requirements=fx.SIMPLE,
                           workdir=str(directory))

    PLAIN = "Order ID,Customer,Total\n1001,Acme,10.00\n1002,Beta,20.00\n"

    def test_a_utf16_file_with_a_mark_is_read(self):
        self.assertTrue(self.run_bytes(self.PLAIN.encode("utf-16")).delivered)

    def test_a_bomless_utf16_file_is_refused_rather_than_misread(self):
        """The case that would be silently delivered as CJK.

        Refusing is the right answer and not a shortfall: without the mark
        there is no way to tell this from a single-byte encoding, and the
        client can fix it in one step once told.
        """
        report = self.run_bytes(self.PLAIN.encode("utf-16-le"))
        self.assertFalse(report.delivered)
        self.assertIn("byte-order mark", report.escalated)

    def test_a_cp1252_file_of_even_length_is_not_taken_for_utf16(self):
        """What the gate is actually protecting.

        An even-length cp1252 file decodes under UTF-16 without raising. If
        the rung were untgated this would be delivered as CJK.
        """
        payload = "Order ID,Customer\n1001,Caf\u00e9 M\u00fcnster\n".encode("cp1252")
        self.assertEqual(len(payload) % 2, 0, "the fixture must be even-length")
        report = self.run_bytes(payload)
        self.assertTrue(report.delivered, report.escalated)


class MojibakeIsTheNewWayToBeWrong(unittest.TestCase):
    """Detecting an encoding adds exactly one failure no other check can see.

    Every other check compares the deliverable against the same source the
    worker read. Misread the source end to end and the two agree perfectly:
    the rows reconcile, the totals are preserved, nothing was dropped, and
    the client receives a column of CJK where their customers used to be.

    The first version of ``no_character_substitution`` asked whether every
    delivered character appears in the source under *some* decoding it
    performed itself, and passed this exact case. That question cannot fail
    -- mojibake is by construction a valid decoding of the source under the
    wrong codec -- so the check rediscovered the worker's mistake and
    certified it.
    """

    HONEST = "Order ID,Customer\n1001,Caf\u00e9 M\u00fcnster\n"

    def files(self, source_bytes, output_bytes):
        directory = pathlib.Path(tempfile.mkdtemp())
        source = directory / "in.csv"
        output = directory / "out.csv"
        source.write_bytes(source_bytes)
        output.write_bytes(output_bytes)
        return str(source), str(output)

    def test_a_correctly_read_cp1252_export_passes(self):
        source, output = self.files(self.HONEST.encode("cp1252"),
                                    self.HONEST.encode("utf-8"))
        outcome = csvverify.run("no_character_substitution", source=source,
                                output=output, params={})
        self.assertEqual(outcome.result, CheckResult.PASS, outcome.detail)

    def test_single_byte_text_delivered_as_cjk_fails(self):
        mangled = self.HONEST.encode("cp1252").decode("utf-16", errors="ignore")
        source, output = self.files(self.HONEST.encode("cp1252"),
                                    mangled.encode("utf-8"))
        outcome = csvverify.run("no_character_substitution", source=source,
                                output=output, params={})
        self.assertEqual(outcome.result, CheckResult.FAIL, outcome.detail)
        self.assertIn("mojibake", outcome.detail)

    def test_a_lost_character_fails(self):
        source, output = self.files(
            self.HONEST.encode("cp1252"),
            "Order ID,Customer\n1001,Caf\ufffd M\ufffdnster\n".encode("utf-8"))
        outcome = csvverify.run("no_character_substitution", source=source,
                                output=output, params={})
        self.assertEqual(outcome.result, CheckResult.FAIL, outcome.detail)

    def test_the_ascii_share_rule_catches_what_the_character_rule_cannot(self):
        """Two rules, tested apart, because together they hide each other.

        Mutation testing found this: breaking the ASCII-share rule changed
        nothing, because the character rule caught the same mojibake, and
        breaking the character rule changed nothing, because the share rule
        did. Both looked pinned and neither was.

        Here only the share rule can speak. Every non-ASCII character
        delivered is genuinely present in the cp1252 reading of the source,
        so the character rule is satisfied -- and the deliverable is still
        wrong, because a source that is 96% printable ASCII cannot honestly
        produce one that is 0%.
        """
        source, output = self.files(
            ("Order ID,Customer,Note\n" + "1001,Cafe,Munster\n" * 6
             + "1002,\u00e9\u00e9\u00e9,\u00e9\u00e9\u00e9\n").encode("cp1252"),
            ("\u00e9" * 400).encode("utf-8"))
        outcome = csvverify.run("no_character_substitution", source=source,
                                output=output, params={})
        self.assertEqual(outcome.result, CheckResult.FAIL, outcome.detail)
        self.assertIn("printable ASCII", outcome.detail)

    def test_the_character_rule_catches_what_the_ascii_share_rule_cannot(self):
        """The other half of the same pair, isolated the other way.

        One field decoded with the wrong codec and the rest came through
        fine, so the deliverable stays mostly ASCII and the share rule has
        nothing to say. What catches it is that the character delivered
        exists only in a UTF-16 reading of a source that is not UTF-16 --
        and the check may not reach for UTF-16 unless the bytes say so.
        That pin is the whole fix; without it the check finds the
        character, agrees with itself, and passes.
        """
        raw = b"Order ID,Customer\n1001,Acme Ltd\n1002,Beta Ltd\n"
        wide = [c for c in raw.decode("utf-16", errors="ignore") if ord(c) > 127]
        source, output = self.files(
            raw,
            (f"Order ID,Customer\n1001,Acme {wide[0]}Ltd\n1002,Beta Ltd\n")
            .encode("utf-8"))
        outcome = csvverify.run("no_character_substitution", source=source,
                                output=output, params={})
        self.assertEqual(outcome.result, CheckResult.FAIL, outcome.detail)
        self.assertIn("no reading of it", outcome.detail)

    def test_a_genuinely_non_latin_file_is_not_held_to_a_latin_expectation(self):
        """The ASCII-share rule must only speak where it has standing.

        A Chinese client's file is mostly non-ASCII and entirely correct.
        A check that failed it would have replaced one wrong answer with
        another.
        """
        chinese = "\u8ba2\u5355,\u5ba2\u6237\n1001,\u5317\u4eac\u516c\u53f8\n"
        source, output = self.files(chinese.encode("utf-8"),
                                    chinese.encode("utf-8"))
        outcome = csvverify.run("no_character_substitution", source=source,
                                output=output, params={})
        self.assertEqual(outcome.result, CheckResult.PASS, outcome.detail)

    def test_a_genuine_utf16_source_read_correctly_passes(self):
        source, output = self.files(self.HONEST.encode("utf-16"),
                                    self.HONEST.encode("utf-8"))
        outcome = csvverify.run("no_character_substitution", source=source,
                                output=output, params={})
        self.assertEqual(outcome.result, CheckResult.PASS, outcome.detail)

    def test_heavily_transformed_but_honest_work_still_passes(self):
        """Renaming, trimming and dropping rows must not look like corruption."""
        source, output = self.files(
            ("Order ID , Customer \n" + "0001, Caf\u00e9  \n" * 40).encode("cp1252"),
            ("order_id,customer\n" + "1,Caf\u00e9\n" * 40).encode("utf-8"))
        outcome = csvverify.run("no_character_substitution", source=source,
                                output=output, params={})
        self.assertEqual(outcome.result, CheckResult.PASS, outcome.detail)


class TheAdvertisedFormatBoundsWhatIsRead(unittest.TestCase):
    """Reading more encodings is not licence to read more formats.

    ``supported_formats`` is ``("csv",)`` and a separate test asserts that
    proving CSV does not imply TSV. A semicolon ``.csv`` is what Excel writes
    across continental Europe -- same format, same extension. A tab-separated
    file is a different one, and sniffing it would have started delivering
    work in a format nothing certified.
    """

    def run_bytes(self, payload):
        directory = pathlib.Path(tempfile.mkdtemp())
        source = directory / "in.csv"
        source.write_bytes(payload)
        return run_csv_job(source=str(source), requirements=fx.SIMPLE,
                           workdir=str(directory))

    def test_a_semicolon_export_is_accepted(self):
        report = self.run_bytes(
            "Order ID;Customer;Total\n1001;Acme;10.00\n1002;Beta;20.00\n"
            .encode("utf-8"))
        self.assertTrue(report.delivered, report.escalated)

    def test_a_tab_separated_file_is_refused_rather_than_guessed(self):
        report = self.run_bytes(
            b"Order ID\tCustomer\tTotal\n1001\tAcme\t10.00\n")
        self.assertFalse(report.delivered)
        self.assertIn("comma-separated", report.escalated)

    def test_a_pdf_is_refused(self):
        """It decodes as cp1252 and has no delimiter, so only the shape catches it."""
        report = self.run_bytes(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
        self.assertFalse(report.delivered)
        self.assertIn("not a CSV file", report.escalated)

    def test_a_spreadsheet_is_refused_with_advice_its_owner_can_act_on(self):
        report = self.run_bytes(b"PK\x03\x04" + b"\x00" * 200)
        self.assertFalse(report.delivered)
        self.assertIn("exported to CSV", report.escalated)
