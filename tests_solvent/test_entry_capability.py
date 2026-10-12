"""Data entry and validation: the worker, and the verifier that does not trust it.

The tests that matter most are in :class:`TheVerifierCatchesAWrongWorker`.
Everything else establishes that a correct job comes out correct, which is the
easy half. Each of those takes a correct delivery, puts one deliberate defect
in it, and asserts which check notices.

The defect this whole design exists for is the one in
:meth:`a_valid_record_held_back_as_an_exception`. Nothing about the delivered
file is wrong — every field is correct, every rule holds, the totals of the
file agree with themselves — and it is short by one record that should have
been in it, with a rejection reason that reads perfectly plausibly. Only
re-deriving the split from the source catches it, and a client never would.
"""

from __future__ import annotations

import csv
import io
import json
import pathlib
import tempfile
import unittest

from solvent import entryverify as verifier
from solvent import entrywork as worker
from solvent import types


class Requirement:
    """The shape a worker reads: an id, a check name, and params."""

    def __init__(self, rid: str, check: str, params: dict,
                 criticality: str = "MANDATORY") -> None:
        self.id = rid
        self.check = check
        self.params = params
        self.criticality = criticality


SOURCE = [
    ["Ref", "Client Name", "Invoiced", "Due", "Status", "Contact"],
    ["A-1001", "  Acme   Ltd ", "1,250.00", "2026-03-01", "open",
     "billing@acme.example"],
    ["A-1002", "Borealis GmbH", "480.50", "2026-03-15", "paid",
     "ap@borealis.example"],
    ["A-1003", "Cyngor LLC", "92.00", "2026-04-02", "open",
     "finance@cyngor.example"],
]

#: One record per defect class the brief can reject, so a job exercises the
#: whole rule vocabulary rather than one rule several times.
BAD_ROWS = [
    ["A-1004", "", "300.00", "2026-04-10", "open", "x@y.example"],   # required
    ["A-1005", "Delta Corp", "not a number", "2026-04-11", "open",
     "d@delta.example"],                                             # type
    ["A-1006", "Ellsworth Ltd", "9000000.00", "2026-04-12", "open",
     "e@ells.example"],                                              # range
    ["A-1007", "Fenwick Group", "120.00", "2026-04-13", "disputed",
     "f@fen.example"],                                               # enum
    ["A-1008", "Gravesend Ltd", "75.00", "05/10/2026", "open",
     "g@grav.example"],                                              # date
    ["XX9", "Halcyon Ltd", "60.00", "2026-04-14", "open", "h@hal.example"],
]


def brief() -> list[Requirement]:
    """One job that uses every operation, so nothing is tested in isolation."""
    return [
        Requirement("r1", "map_fields", {"mapping": {
            "Reference": "Ref", "Client": "Client Name",
            "Amount": "Invoiced", "Due Date": "Due",
            "State": "Status", "Email": "Contact"}}),
        Requirement("r2", "constant_field", {"fields": {"Batch": "2026-W14"}}),
        Requirement("r3", "normalise_whitespace", {"fields": ["Client"]}),
        Requirement("r4", "normalise_case", {"fields": {"State": "upper"}}),
        Requirement("r5", "require_field", {"fields": ["Reference", "Client"]}),
        Requirement("r6", "validate_type", {"types": {
            "Amount": "decimal", "Due Date": "date", "Email": "email"}}),
        Requirement("r7", "validate_range",
                    {"ranges": {"Amount": {"minimum": "0", "maximum": "100000"}}}),
        Requirement("r8", "validate_enum",
                    {"allowed": {"State": ["OPEN", "PAID"]}}),
        Requirement("r9", "validate_pattern",
                    {"patterns": {"Reference": r"^A-\d{4}$"}}),
        Requirement("r10", "deduplicate_by", {"fields": ["Reference"]}),
        Requirement("r11", "select_fields", {"fields": [
            "Reference", "Client", "Amount", "Due Date", "State", "Email",
            "Batch"]}),
        Requirement("r12", "records_reconcile", {}),
        Requirement("r13", "no_invented_values", {}),
        Requirement("r14", "exceptions_justified", {}),
        Requirement("r15", "record_traceability", {}),
        Requirement("r16", "parses_as_delivered", {}),
    ]


class Base(unittest.TestCase):

    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp())
        self.source = self.dir / "client.csv"
        self.output = self.dir / "delivered.csv"
        self.exceptions = self.dir / "delivered-exceptions.csv"

    def write_source(self, rows=None) -> None:
        buffer = io.StringIO(newline="")
        csv.writer(buffer, lineterminator="\n").writerows(rows or SOURCE)
        self.source.write_text(buffer.getvalue(), encoding="utf-8", newline="")

    def run_job(self, requirements=None, rows=None):
        self.write_source(rows)
        report = worker.execute(source=self.source, destination=self.output,
                                requirements=requirements or brief())
        return report

    def verify(self, requirements=None):
        return verifier.verify(source=self.source, output=self.output,
                               requirements=requirements or brief())

    def delivered(self) -> tuple[list[str], list[list[str]]]:
        return verifier.read_table(self.output)

    def rewrite(self, header, rows) -> None:
        buffer = io.StringIO(newline="")
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)
        self.output.write_text(buffer.getvalue(), encoding="utf-8", newline="")

    def finding(self, report, check):
        matches = [f for f in report.findings if f.check == check]
        self.assertTrue(matches, f"no finding for {check}")
        return matches[0]


class ThePlan(Base):

    def test_an_unknown_operation_is_refused_by_name(self):
        steps, refusals = worker.plan(
            [Requirement("r1", "transcribe_handwriting", {})])
        self.assertEqual(steps, [])
        self.assertIn("transcribe_handwriting", refusals[0])
        self.assertIn("map_fields", refusals[0])

    def test_a_verify_only_check_is_not_an_operation(self):
        steps, refusals = worker.plan(
            [Requirement("r1", "records_reconcile", {})])
        self.assertEqual((steps, refusals), ([], []))

    def test_a_mapping_cannot_be_guessed_from_column_names(self):
        _, refusals = worker.plan([Requirement("r1", "map_fields", {})])
        self.assertIn("cannot be guessed", refusals[0])

    def test_a_type_outside_the_vocabulary_is_refused(self):
        _, refusals = worker.plan([Requirement(
            "r1", "validate_type", {"types": {"X": "sort code"}})])
        self.assertIn("sort code", refusals[0])
        self.assertIn("integer", refusals[0])

    def test_a_range_with_neither_bound_bounds_nothing(self):
        _, refusals = worker.plan([Requirement(
            "r1", "validate_range", {"ranges": {"X": {}}})])
        self.assertIn("neither a minimum nor", refusals[0])

    def test_a_range_mixing_a_number_and_a_date_has_no_ordering(self):
        _, refusals = worker.plan([Requirement(
            "r1", "validate_range",
            {"ranges": {"X": {"minimum": "1", "maximum": "2026-01-01"}}})])
        self.assertIn("no ordering", refusals[0])

    def test_an_unusable_pattern_is_refused_rather_than_compiled_at_runtime(self):
        _, refusals = worker.plan([Requirement(
            "r1", "validate_pattern", {"patterns": {"X": "([unclosed"}})])
        self.assertIn("not a usable one", refusals[0])

    def test_a_pattern_longer_than_the_limit_is_refused(self):
        _, refusals = worker.plan([Requirement(
            "r1", "validate_pattern",
            {"patterns": {"X": "a" * (worker.MAX_PATTERN + 1)}})])
        self.assertIn(str(worker.MAX_PATTERN), refusals[0])

    def test_the_order_is_fixed_rather_than_the_order_asked_for(self):
        steps, refusals = worker.plan(list(reversed(brief())))
        self.assertEqual(refusals, [])
        self.assertEqual(steps[0].operation, "map_fields")
        self.assertEqual(steps[-1].operation, "select_fields")
        positions = {s.operation: i for i, s in enumerate(steps)}
        self.assertLess(positions["normalise_case"], positions["validate_enum"],
                        "a rule must be applied to the value it authorised")
        self.assertLess(positions["validate_type"], positions["deduplicate_by"],
                        "an invalid record must never win a key collision")

    def test_a_requirement_naming_no_check_is_refused_not_skipped(self):
        _, refusals = worker.plan([Requirement("r1", "", {})])
        self.assertIn("names no check", refusals[0])


class TheRealRequirementType(Base):
    """The committed dataclass, not a test stub shaped like one.

    Two spellings of the same field exist in this codebase: the dataclass calls
    it ``check`` and the store column is ``check_name``. A worker that reads
    only one of them passes every test built on the other and refuses every
    requirement the orchestrator supplies. That is not a gap in coverage so
    much as two halves that were never introduced, so here they are.
    """

    def test_a_committed_requirement_plans_through_the_worker(self):
        requirement = types.Requirement(
            id="r1", text="map the client's columns",
            source=types.RequirementSource.OWNER_CONFIRMED,
            check="map_fields", params={"mapping": {"Reference": "Ref"}})
        steps, refusals = worker.plan([requirement])
        self.assertEqual(refusals, [])
        self.assertEqual([s.operation for s in steps], ["map_fields"])

    def test_a_store_row_spelling_plans_through_the_worker_too(self):
        class Row:
            id = "r1"
            check_name = "map_fields"
            params = {"mapping": {"Reference": "Ref"}}
            criticality = "MANDATORY"

        steps, refusals = worker.plan([Row()])
        self.assertEqual(refusals, [])
        self.assertEqual([s.operation for s in steps], ["map_fields"])

    def test_the_accessor_prefers_the_committed_field(self):
        class Both:
            check = "map_fields"
            check_name = "validate_type"

        self.assertEqual(types.check_of(Both()), "map_fields")

    def test_the_spreadsheet_worker_reads_the_committed_field_too(self):
        from solvent import sheetwork

        requirement = types.Requirement(
            id="r1", text="require the columns",
            source=types.RequirementSource.OWNER_CONFIRMED,
            check="require_columns", params={"columns": ["Item"]})
        steps, refusals = sheetwork.plan([requirement])
        self.assertEqual(refusals, [])
        self.assertEqual([s.operation for s in steps], ["require_columns"])


class JobLevelRefusals(Base):
    """What cannot be done at all, said before anything is written."""

    def test_a_mapping_naming_an_absent_column_is_refused(self):
        report = self.run_job([Requirement(
            "r1", "map_fields", {"mapping": {"Reference": "Nope"}})])
        self.assertFalse(report.clean)
        self.assertIn("Nope", report.refused[0])
        self.assertIn("Ref", report.refused[0], "the refusal names what is there")
        self.assertFalse(self.output.exists(), "nothing is written on a refusal")

    def test_a_field_both_mapped_and_constant_is_two_requirements_disagreeing(self):
        report = self.run_job([
            Requirement("r1", "map_fields", {"mapping": {"Client": "Ref"}}),
            Requirement("r2", "constant_field", {"fields": {"Client": "X"}})])
        self.assertIn("disagree", report.refused[0])

    def test_a_rule_naming_a_field_nothing_produces_is_refused(self):
        report = self.run_job([
            Requirement("r1", "map_fields", {"mapping": {"Client": "Ref"}}),
            Requirement("r2", "require_field", {"fields": ["Postcode"]})])
        self.assertIn("Postcode", report.refused[0])

    def test_a_delivered_order_naming_an_unproduced_field_is_refused(self):
        report = self.run_job([
            Requirement("r1", "map_fields", {"mapping": {"Client": "Ref"}}),
            Requirement("r2", "select_fields", {"fields": ["Client", "Ghost"]})])
        self.assertIn("Ghost", report.refused[0])

    def test_a_job_with_no_mapping_cannot_infer_a_target_schema(self):
        report = self.run_job([Requirement("r1", "require_field",
                                           {"fields": ["Client"]})])
        self.assertIn("cannot be inferred", report.refused[0])

    def test_a_source_that_is_not_text_at_all_is_refused_not_guessed_at(self):
        self.source.write_bytes(b"\xff\xfe\x00\x01 not utf-8")
        report = worker.execute(source=self.source, destination=self.output,
                                requirements=brief())
        self.assertIn("could not be read", report.refused[0])
        self.assertIn("not UTF-8", report.refused[0])
        self.assertFalse(self.output.exists())

    def test_a_file_that_decodes_but_is_not_this_clients_table(self):
        """Readable text whose columns are not the ones the brief maps."""
        self.source.write_bytes(b"\x00\x01\x02 not a client export")
        report = worker.execute(source=self.source, destination=self.output,
                                requirements=brief())
        self.assertIn("does not have", report.refused[0])
        self.assertFalse(self.output.exists())

    def test_a_duplicated_source_header_could_not_say_which_column_it_meant(self):
        report = self.run_job(rows=[["Ref", "Ref"], ["1", "2"]])
        self.assertIn("same column twice", report.refused[0])


class ACorrectJob(Base):

    def test_the_whole_brief_runs_and_every_check_passes(self):
        report = self.run_job(rows=SOURCE + BAD_ROWS)
        self.assertEqual(report.refused, [])
        self.assertEqual(report.records_in, 9)
        self.assertEqual(report.accepted, 3)
        self.assertEqual(report.rejected, 6)
        verdict = self.verify()
        self.assertEqual(verdict.verdict, verifier.PASS, verdict.summary())
        self.assertEqual(len(verdict.findings), 16)

    def test_whitespace_is_collapsed_only_where_authorised(self):
        self.run_job()
        _, rows = self.delivered()
        self.assertEqual(rows[0][1], "Acme Ltd")

    def test_case_is_converted_only_where_authorised(self):
        self.run_job()
        _, rows = self.delivered()
        self.assertEqual(rows[0][4], "OPEN")
        self.assertEqual(rows[0][0], "A-1001", "an unnamed field is untouched")

    def test_a_constant_appears_in_every_record(self):
        self.run_job()
        _, rows = self.delivered()
        self.assertEqual({row[6] for row in rows}, {"2026-W14"})

    def test_the_delivered_order_is_the_order_asked_for(self):
        self.run_job()
        header, _ = self.delivered()
        self.assertEqual(header[0], "Reference")
        self.assertEqual(header[-1], "Batch")

    def test_each_rejection_names_every_rule_it_breaks_not_the_first(self):
        self.run_job(rows=SOURCE + [[
            "ZZ", "", "nope", "01/02/2026", "disputed", "not-an-address"]])
        held = verifier.read_exceptions(self.exceptions)
        self.assertEqual(len(held), 1)
        self.assertEqual(sorted(held[0]["rules"]), [
            "require_field", "validate_enum", "validate_pattern",
            "validate_range", "validate_type"],
            "a figure that is not a number fails its type and cannot be "
            "compared against its range; the client is told both")
        self.assertIn("cannot be compared", held[0]["detail"])

    def test_the_exceptions_file_carries_the_record_as_supplied(self):
        self.run_job(rows=SOURCE + [BAD_ROWS[0]])
        held = verifier.read_exceptions(self.exceptions)
        record = json.loads(held[0]["record"])
        self.assertEqual(record["Ref"], "A-1004")
        self.assertEqual(record["Client Name"], "",
                         "the gap is recorded as the gap it is")

    def test_a_duplicate_reference_is_dropped_and_counted(self):
        report = self.run_job(rows=SOURCE + [SOURCE[1]])
        self.assertEqual(report.duplicates_dropped, 1)
        self.assertEqual(report.accepted, 3)
        self.assertEqual(self.verify().verdict, verifier.PASS)

    def test_the_earlier_record_is_the_one_that_survives(self):
        changed = list(SOURCE[1])
        changed[1] = "Acme Limited"
        report = self.run_job(rows=SOURCE + [changed])
        self.assertEqual(report.duplicates_dropped, 1)
        _, rows = self.delivered()
        self.assertEqual(rows[0][1], "Acme Ltd")


class TheSourceIsTranscribedNotRepaired(Base):

    def test_a_ragged_row_becomes_an_exception_rather_than_being_padded(self):
        report = self.run_job(rows=SOURCE + [["A-1009", "Short Ltd"]])
        self.assertEqual(report.rejected, 1)
        held = verifier.read_exceptions(self.exceptions)
        self.assertEqual(held[0]["rules"], ("parses_as_delivered",))
        self.assertIn("2 value(s)", held[0]["detail"])

    def test_an_empty_required_field_is_never_filled(self):
        self.run_job(rows=SOURCE + [BAD_ROWS[0]])
        _, rows = self.delivered()
        self.assertEqual(len(rows), 3, "the record is held, not completed")

    def test_an_ambiguous_date_is_rejected_rather_than_read_one_way(self):
        self.run_job(rows=SOURCE + [BAD_ROWS[4]])
        held = verifier.read_exceptions(self.exceptions)
        self.assertEqual(held[0]["rules"], ("validate_type",))
        self.assertIn("ISO 8601", held[0]["detail"])

    def test_a_figure_outside_the_range_is_not_clamped(self):
        self.run_job(rows=SOURCE + [BAD_ROWS[2]])
        _, rows = self.delivered()
        self.assertNotIn("100000", [row[2] for row in rows])

    def test_a_value_outside_the_allowed_list_is_not_mapped_to_the_nearest(self):
        self.run_job(rows=SOURCE + [BAD_ROWS[3]])
        _, rows = self.delivered()
        self.assertEqual({row[4] for row in rows}, {"OPEN", "PAID"})
        held = verifier.read_exceptions(self.exceptions)
        self.assertIn("DISPUTED", held[0]["detail"])


class JsonSources(Base):

    def setUp(self):
        super().setUp()
        self.source = self.dir / "client.json"

    def write_json(self, records) -> None:
        self.source.write_text(json.dumps(records, indent=2), encoding="utf-8")

    def test_a_json_array_of_objects_is_a_data_entry_source(self):
        self.write_json([
            {"Ref": "A-1001", "Client Name": "Acme Ltd", "Invoiced": 1250.0,
             "Due": "2026-03-01", "Status": "open",
             "Contact": "billing@acme.example"}])
        report = worker.execute(source=self.source, destination=self.output,
                                requirements=brief())
        self.assertEqual(report.refused, [])
        self.assertEqual(report.accepted, 1)
        self.assertEqual(self.verify().verdict, verifier.PASS)

    def test_a_json_document_that_is_not_a_list_is_refused(self):
        self.source.write_text('{"records": []}', encoding="utf-8")
        report = worker.execute(source=self.source, destination=self.output,
                                requirements=brief())
        self.assertIn("not a list of records", report.refused[0])

    def test_a_nested_value_is_held_rather_than_flattened(self):
        self.write_json([
            {"Ref": "A-1001", "Client Name": "Acme Ltd", "Invoiced": 10,
             "Due": "2026-03-01", "Status": "open",
             "Contact": {"email": "x@y.example"}}])
        report = worker.execute(source=self.source, destination=self.output,
                                requirements=brief())
        self.assertEqual(report.rejected, 1)
        held = verifier.read_exceptions(self.exceptions)
        self.assertIn("flattening", held[0]["detail"])

    def test_a_json_boolean_keeps_the_spelling_the_client_exported(self):
        self.assertEqual(worker._scalar(True), "true")
        self.assertEqual(verifier._source_json.__name__, "_source_json")

    def test_json_row_numbers_start_at_one_because_there_is_no_header(self):
        self.write_json([{"Ref": "ZZ", "Client Name": "", "Invoiced": 1,
                          "Due": "2026-01-01", "Status": "open",
                          "Contact": "a@b.example"}])
        worker.execute(source=self.source, destination=self.output,
                       requirements=brief())
        held = verifier.read_exceptions(self.exceptions)
        self.assertEqual(held[0]["source_row"], 1)
        self.assertEqual(self.verify().verdict, verifier.PASS,
                         "the verifier counts source rows the same way")


class TwoImplementationsOfOneSpecification(Base):
    """The worker and the verifier parse values separately. They must agree."""

    def test_numbers_agree_across_every_decoration_a_client_sends(self):
        for text in ("1250.00", "1,250.00", "$1,250.00", "(42.50)", "£9",
                     "€3.5", "USD 12", "0", "-7", "  8.25  "):
            with self.subTest(text=text):
                mine = worker.as_number(text)
                theirs = verifier.number(text)
                self.assertEqual(
                    mine, theirs, f"{text!r}: worker {mine}, verifier {theirs}")

    def test_non_numbers_are_non_numbers_on_both_sides(self):
        for text in ("", "   ", "n/a", "1.2.3", "twelve", "1e5", "--4", "NaN",
                     "Infinity", "0x10"):
            with self.subTest(text=text):
                self.assertIsNone(worker.as_number(text))
                self.assertIsNone(verifier.number(text))

    def test_dates_agree_including_the_days_that_do_not_exist(self):
        for text in ("2026-03-01", "2026-02-29", "2024-02-29", "2026-13-01",
                     "2026-00-10", "2026-3-1", "01/02/2026", "", "2026-04-31"):
            with self.subTest(text=text):
                mine = worker.as_date(text)
                theirs = verifier.day(text)
                self.assertEqual(mine, theirs, f"{text!r}")

    def test_the_email_shape_rule_is_the_same_rule_twice(self):
        for text in ("a@b.example", "a.b@c.d.example", "a@b", "a b@c.example",
                     "@b.example", "a@", "a@@b.example", "a@b.", "a,b@c.example",
                     "a@b..example"):
            with self.subTest(text=text):
                mine = not worker.type_problem("email", text)
                theirs = verifier.type_holds("email", text)
                self.assertEqual(mine, theirs, f"{text!r}")

    def test_whitespace_collapses_identically(self):
        for text in ("  a  b ", "a\tb", "a\n\nb", " ", "a", "a  b  c  "):
            with self.subTest(text=text):
                self.assertEqual(worker.normalise_whitespace(text),
                                 verifier.squeeze(text))

    def test_an_integer_is_whole_on_both_sides(self):
        for text in ("7", "7.0", "7.5", "-7", "1,000", "0.1"):
            with self.subTest(text=text):
                mine = not worker.type_problem("integer", text)
                self.assertEqual(mine, verifier.type_holds("integer", text))


class TheVerifierCatchesAWrongWorker(Base):
    """One defect at a time, in an otherwise correct delivery."""

    def setUp(self):
        super().setUp()
        self.run_job(rows=SOURCE + BAD_ROWS)
        self.assertEqual(self.verify().verdict, verifier.PASS,
                         "the baseline must be clean before a defect means anything")

    def test_a_valid_record_held_back_as_an_exception(self):
        """The damaging one: a short deliverable that is correct about itself."""
        header, rows = self.delivered()
        dropped = rows.pop(1)
        self.rewrite(header, rows)
        held = verifier.read_exceptions(self.exceptions)
        with self.exceptions.open("a", encoding="utf-8", newline="") as handle:
            csv.writer(handle, lineterminator="\n").writerow([
                "3", "validate_type", "Amount: could not be read",
                json.dumps({"Ref": dropped[0]})])
        report = self.verify()
        self.assertEqual(report.verdict, verifier.FAIL)
        finding = self.finding(report, "exceptions_justified")
        self.assertEqual(finding.verdict, verifier.FAIL)
        self.assertIn("break", finding.detail)
        self.assertIn("3", finding.detail)

    def test_a_rejection_reason_that_names_the_wrong_rule(self):
        held = verifier.read_exceptions(self.exceptions)
        rows = [[str(e["source_row"]),
                 "validate_range" if e["source_row"] == 5
                 else ";".join(e["rules"]),
                 e["detail"], e["record"]] for e in held]
        self.rewrite_exceptions(rows)
        report = self.verify()
        finding = self.finding(report, "exceptions_justified")
        self.assertEqual(finding.verdict, verifier.FAIL)
        self.assertIn("validate_range", finding.detail)

    def test_a_rejection_with_no_detail_the_client_could_act_on(self):
        held = verifier.read_exceptions(self.exceptions)
        rows = [[str(e["source_row"]), ";".join(e["rules"]), "", e["record"]]
                for e in held]
        self.rewrite_exceptions(rows)
        finding = self.finding(self.verify(), "exceptions_justified")
        self.assertEqual(finding.verdict, verifier.FAIL)
        self.assertIn("no", finding.detail)

    def test_a_record_in_neither_file_is_simply_lost(self):
        header, rows = self.delivered()
        rows.pop(0)
        self.rewrite(header, rows)
        report = self.verify()
        finding = self.finding(report, "records_reconcile")
        self.assertEqual(finding.verdict, verifier.FAIL)
        self.assertIn("unaccounted for", finding.detail)

    def test_an_invented_value_in_a_delivered_record(self):
        header, rows = self.delivered()
        rows[0][1] = "Acme Holdings Ltd"
        self.rewrite(header, rows)
        finding = self.finding(self.verify(), "no_invented_values")
        self.assertEqual(finding.verdict, verifier.FAIL)
        self.assertIn("Acme Holdings Ltd", finding.detail)

    def test_a_blank_filled_in_with_something_plausible(self):
        """The worst case: a gap closed with a value that reads correctly."""
        header, rows = self.delivered()
        rows.append(["A-1004", "Delta Corp", "300.00", "2026-04-10", "OPEN",
                     "x@y.example", "2026-W14"])
        self.rewrite(header, rows)
        report = self.verify()
        self.assertEqual(report.verdict, verifier.FAIL)
        # Not no_invented_values, and the reason is worth stating: every value
        # in the fabricated record is a real value that really is in the
        # source -- the reference from row 5, the client name from row 6. A
        # column-wise check cannot see that, and only comparing whole records
        # against the records they claim to be does.
        self.assertEqual(self.finding(report, "record_traceability").verdict,
                         verifier.FAIL)
        # The exceptions file is untouched and still correct about itself, so
        # the check that reads it has nothing to object to. What catches this
        # is arithmetic over the three places a source record can be: nine
        # records in, ten accounted for.
        reconcile = self.finding(report, "records_reconcile")
        self.assertEqual(reconcile.verdict, verifier.FAIL)
        self.assertIn("unaccounted for", reconcile.detail)

    def test_a_record_grafted_together_from_two_source_records(self):
        """Every value real, every column right, and no such client exists."""
        header, rows = self.delivered()
        rows[0][1], rows[1][1] = rows[1][1], rows[0][1]
        self.rewrite(header, rows)
        report = self.verify()
        self.assertEqual(
            self.finding(report, "no_invented_values").verdict, verifier.PASS,
            "both values are genuinely in the source, in the right columns")
        self.assertEqual(self.finding(report, "record_traceability").verdict,
                         verifier.FAIL)

    def test_a_value_delivered_more_often_than_the_source_holds_it(self):
        header, rows = self.delivered()
        rows[1][1] = rows[0][1]
        self.rewrite(header, rows)
        finding = self.finding(self.verify(), "no_invented_values")
        self.assertEqual(finding.verdict, verifier.FAIL,
                         "set membership would call the second copy present")
        self.assertIn("Acme Ltd", finding.detail)

    def test_an_unauthorised_normalisation_nobody_asked_for(self):
        header, rows = self.delivered()
        rows[0][5] = rows[0][5].upper()
        self.rewrite(header, rows)
        report = self.verify()
        self.assertEqual(report.verdict, verifier.FAIL)
        self.assertEqual(self.finding(report, "normalise_whitespace").verdict,
                         verifier.FAIL)

    def test_an_invalid_record_delivered_anyway(self):
        header, rows = self.delivered()
        rows[0][3] = "01/02/2026"
        self.rewrite(header, rows)
        report = self.verify()
        self.assertEqual(self.finding(report, "validate_type").verdict,
                         verifier.FAIL)

    def test_a_value_outside_the_stated_range_delivered_anyway(self):
        header, rows = self.delivered()
        rows[0][2] = "9000000.00"
        self.rewrite(header, rows)
        self.assertEqual(self.finding(self.verify(), "validate_range").verdict,
                         verifier.FAIL)

    def test_a_duplicate_left_in_the_deliverable(self):
        header, rows = self.delivered()
        rows.append(list(rows[0]))
        self.rewrite(header, rows)
        report = self.verify()
        self.assertEqual(self.finding(report, "deduplicate_by").verdict,
                         verifier.FAIL)
        self.assertEqual(self.finding(report, "record_traceability").verdict,
                         verifier.FAIL)

    def test_the_delivered_field_order_is_not_the_one_asked_for(self):
        header, rows = self.delivered()
        order = [1, 0] + list(range(2, len(header)))
        self.rewrite([header[i] for i in order],
                     [[row[i] for i in order] for row in rows])
        self.assertEqual(self.finding(self.verify(), "select_fields").verdict,
                         verifier.FAIL)

    def test_a_ragged_delivered_row(self):
        with self.output.open("a", encoding="utf-8", newline="") as handle:
            handle.write("A-1009,Short Ltd\n")
        report = self.verify()
        self.assertEqual(
            self.finding(report, "parses_as_delivered").verdict, verifier.FAIL)

    def test_a_mapped_field_holding_another_records_value(self):
        header, rows = self.delivered()
        rows[0][2], rows[1][2] = rows[1][2], rows[0][2]
        self.rewrite(header, rows)
        report = self.verify()
        self.assertEqual(self.finding(report, "map_fields").verdict,
                         verifier.FAIL)

    def test_a_constant_quietly_changed_in_one_record(self):
        header, rows = self.delivered()
        rows[1][6] = "2026-W15"
        self.rewrite(header, rows)
        finding = self.finding(self.verify(), "constant_field")
        self.assertEqual(finding.verdict, verifier.FAIL)
        self.assertIn("record 2", finding.detail)

    def test_a_missing_exceptions_file_is_unverifiable_not_passed(self):
        self.exceptions.unlink()
        report = self.verify()
        for check in verifier.WHOLE_JOB:
            self.assertEqual(self.finding(report, check).verdict,
                             verifier.UNVERIFIABLE, check)
        self.assertEqual(report.verdict, verifier.UNVERIFIABLE)

    def rewrite_exceptions(self, rows) -> None:
        buffer = io.StringIO(newline="")
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(["source_row", "rules", "detail", "record"])
        writer.writerows(rows)
        self.exceptions.write_text(buffer.getvalue(), encoding="utf-8",
                                   newline="")


class TheVerifierSaysWhenItCannotTell(Base):

    def test_an_unknown_check_is_unverifiable_rather_than_passed(self):
        self.run_job()
        report = verifier.verify(
            source=self.source, output=self.output,
            requirements=brief() + [Requirement("rX", "handwriting_legible", {})])
        finding = self.finding(report, "handwriting_legible")
        self.assertEqual(finding.verdict, verifier.UNVERIFIABLE)
        self.assertIn("not been checked by anybody", finding.detail)
        self.assertEqual(report.verdict, verifier.UNVERIFIABLE)

    def test_an_unreadable_deliverable_fails_rather_than_being_unverifiable(self):
        self.run_job()
        self.output.write_bytes(b"\xff\xfe\x00")
        report = self.verify()
        self.assertEqual(report.verdict, verifier.FAIL)

    def test_an_unreadable_source_is_unverifiable_because_nothing_can_be_compared(self):
        self.run_job()
        self.source.write_bytes(b"\xff\xfe\x00")
        report = self.verify()
        self.assertEqual(report.verdict, verifier.UNVERIFIABLE)

    def test_no_requirements_at_all_is_unverifiable_not_pass(self):
        self.run_job()
        report = verifier.verify(source=self.source, output=self.output,
                                 requirements=[])
        self.assertEqual(report.verdict, verifier.UNVERIFIABLE)
        self.assertIn("nothing to verify", report.findings[0].detail)

    def test_a_whole_job_check_through_the_single_entry_point_says_so(self):
        self.run_job()
        finding = verifier.run("records_reconcile", source=self.source,
                               output=self.output, params={})
        self.assertEqual(finding.verdict, verifier.UNVERIFIABLE)
        self.assertIn("whole accept", finding.detail)
        self.assertFalse(finding.passed)

    def test_a_field_level_check_works_through_the_single_entry_point(self):
        self.run_job()
        finding = verifier.run(
            "select_fields", source=self.source, output=self.output,
            params={"fields": ["Reference", "Client", "Amount", "Due Date",
                               "State", "Email", "Batch"]})
        self.assertEqual(finding.verdict, verifier.PASS)
        self.assertTrue(finding.passed)

    def test_an_exceptions_file_that_is_not_one_is_named_as_such(self):
        self.run_job()
        self.exceptions.write_text("a,b\n1,2\n", encoding="utf-8")
        report = self.verify()
        self.assertEqual(report.verdict, verifier.UNVERIFIABLE)
        self.assertIn("does not look like an exceptions file",
                      self.finding(report, "records_reconcile").detail)


if __name__ == "__main__":
    unittest.main()
