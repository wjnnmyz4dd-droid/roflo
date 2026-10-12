"""Spreadsheet automation: the worker, and the verifier that does not trust it.

The tests that matter most here are in :class:`TheVerifierCatchesAWrongWorker`.
Everything else establishes that a correct job comes out correct, which is the
easy half. What makes the two-sided design worth anything is that a *wrong*
output is caught -- so each of those tests takes a correct delivery, puts one
deliberate defect in it, and asserts which check notices.

One of them is the reason the design has two questions per computed cell
rather than one: a value that is right beside a formula pointing at the wrong
column. The arithmetic check passes it, correctly, because the number is
right. Only parsing the formula catches it, and a client would find it the
moment they changed an input and watched the total not move.
"""

from __future__ import annotations

import pathlib
import tempfile
import unittest
from decimal import Decimal

from solvent import sheetverify as verifier
from solvent import sheetwork as worker
from solvent import xlsx


class Requirement:
    """The shape the capability reads: an id, a check name, and params."""

    def __init__(self, rid: str, check: str, params: dict,
                 criticality: str = "MANDATORY") -> None:
        self.id = rid
        self.check_name = check
        self.params = params
        self.criticality = criticality


class Base(unittest.TestCase):

    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp())
        self.source = self.dir / "client.xlsx"
        self.output = self.dir / "delivered.xlsx"

    def given(self, sheets):
        self.source.write_bytes(xlsx.write_workbook(sheets))

    def sheet(self, name, rows):
        return xlsx.SheetOut(name=name, rows=rows)

    def run_job(self, requirements, sheet_name=""):
        return worker.execute(source=self.source, destination=self.output,
                              requirements=requirements, sheet_name=sheet_name)

    def check(self, requirements, sheet_name=""):
        return verifier.verify(source=self.source, output=self.output,
                               requirements=requirements,
                               sheet_name=sheet_name)

    def delivered(self):
        book = xlsx.read_workbook(self.output.read_bytes())
        return book.first.values()

    def delivered_cells(self):
        return xlsx.read_workbook(self.output.read_bytes()).first.rows


class ThePlanRefusesWhatItCannotDo(unittest.TestCase):

    def plan_for(self, check, params):
        return worker.plan([Requirement("r1", check, params)])

    def test_an_operation_it_does_not_perform_is_refused_by_name(self):
        """An unsupported ask is a conversation with the client, not
        something to improvise."""
        steps, refusals = self.plan_for("make_it_look_nicer", {})
        self.assertEqual(steps, [])
        self.assertIn("not something this capability does", refusals[0])

    def test_renaming_without_a_mapping_is_refused(self):
        """This capability never guesses a heading."""
        _steps, refusals = self.plan_for("rename_headers", {})
        self.assertIn("never guesses a heading", refusals[0])

    def test_an_expression_it_cannot_verify_is_refused(self):
        """A capability that accepted arbitrary expressions would be one whose
        output nobody could verify without a second expression evaluator --
        and two evaluators that agree prove only that they share a bug."""
        _steps, refusals = self.plan_for(
            "compute_column", {"into": "x", "using": ["a", "b"],
                               "expression": "regression"})
        self.assertIn("not one this capability can compute and verify",
                      refusals[0])

    def test_a_quotient_of_three_columns_is_refused(self):
        _steps, refusals = self.plan_for(
            "compute_column", {"into": "x", "using": ["a", "b", "c"],
                               "expression": "quotient"})
        self.assertIn("exactly two columns", refusals[0])

    def test_an_aggregate_it_cannot_compute_is_refused(self):
        _steps, refusals = self.plan_for(
            "total_row", {"aggregates": {"x": "median"}})
        self.assertIn("median", refusals[0])

    def test_consolidating_one_sheet_is_refused(self):
        _steps, refusals = self.plan_for(
            "consolidate_sheets", {"sheets": ["only"]})
        self.assertIn("at least two", refusals[0])

    def test_the_order_is_fixed_rather_than_the_clients(self):
        """Some operations only make sense after others. A plan that honoured
        the brief's ordering would give a different answer depending on how
        the client happened to write it."""
        steps, _refusals = worker.plan([
            Requirement("a", "total_row", {"aggregates": {"t": "sum"}}),
            Requirement("b", "compute_column",
                        {"into": "t", "using": ["q", "p"],
                         "expression": "product"}),
            Requirement("c", "consolidate_sheets", {"sheets": ["x", "y"]}),
            Requirement("d", "require_columns", {"columns": ["q"]}),
        ])
        self.assertEqual([s.operation for s in steps],
                         ["consolidate_sheets", "require_columns",
                          "compute_column", "total_row"])

    def test_a_property_of_the_result_is_not_planned_as_a_step(self):
        """"No row may be lost" is something to be true, not something to
        carry out. Refusing it would refuse exactly the requirements that
        catch the quietest failures."""
        steps, refusals = self.plan_for("row_reconciliation", {})
        self.assertEqual((steps, refusals), ([], []))


class TheArithmeticIsExact(unittest.TestCase):

    def test_money_is_added_in_decimal_not_binary_floating_point(self):
        """A spreadsheet of money added up in floats produces totals wrong in
        the last penny, and a client checking the column by hand finds it."""
        self.assertEqual(
            worker.compute("sum", [Decimal("0.1"), Decimal("0.2")]),
            Decimal("0.3"))

    def test_a_currency_symbol_and_thousands_separator_are_display(self):
        """A column of "£1,234.50" is numeric to everybody except a parser."""
        self.assertEqual(worker.as_number("£1,234.50"), Decimal("1234.50"))
        self.assertEqual(worker.as_number("(99)"), Decimal("-99"))

    def test_a_blank_cell_is_not_a_zero(self):
        """Treating it as one makes a total that looks right and is not, and
        nobody can see the difference by reading the output."""
        self.assertIsNone(worker.as_number(""))
        self.assertIsNone(worker.as_number(None))

    def test_a_missing_input_leaves_the_computed_cell_blank(self):
        """Filling it with zero would be inventing data, and the client's
        total would be wrong by exactly the rows they could not see."""
        self.assertIsNone(
            worker.compute("product", [Decimal(2), None]))

    def test_division_by_zero_is_not_a_number(self):
        self.assertIsNone(worker.compute("quotient", [Decimal(1), Decimal(0)]))
        self.assertIsNone(
            worker.compute("percentage", [Decimal(1), Decimal(0)]))

    def test_an_average_skips_blanks_rather_than_counting_them_as_zero(self):
        self.assertEqual(
            worker.aggregate("average", [Decimal(10), None, Decimal(20)]),
            Decimal(15))

    def test_a_boolean_is_not_a_number(self):
        """True is not 1 in a client's spreadsheet column."""
        self.assertIsNone(worker.as_number(True))

    def test_the_two_sides_agree_exactly_despite_different_orderings(self):
        """The worker computes a percentage as a/b*100 and the verifier as
        a*100/b. They agree because multiplying by a power of ten is exact in
        Decimal, so both reduce to the same correctly-rounded division. This
        is why the comparison is exact rather than using a tolerance -- a
        tolerance is the knob somebody widens when a check starts failing, and
        the failure it is widened past is the one that mattered.
        """
        import random

        random.seed(11)
        for _ in range(2000):
            a = Decimal(str(random.uniform(-1e6, 1e6))).quantize(
                Decimal("0.0000001"))
            b = Decimal(str(random.uniform(-1e6, 1e6))).quantize(
                Decimal("0.0000001"))
            if b == 0:
                continue
            for expression in ("quotient", "percentage"):
                with self.subTest(expression=expression, a=a, b=b):
                    self.assertEqual(worker.compute(expression, [a, b]),
                                     verifier.recompute(expression, [a, b]))


class ACorrectJobComesOutCorrect(Base):

    REQUIREMENTS = None

    def arrange(self):
        self.given([self.sheet("Jan", [
            ["  Item ", "Qty", "Unit price", "Client ref"],
            ["widget ", 3, "£2.50", "REF-1"],
            ["", None, None, ""],
            [" gadget", 10, "1.25", "REF-2"]]),
            self.sheet("Feb", [
                ["Item", "Qty", "Unit price", "Client ref"],
                ["sprocket", 4, "7.00", "REF-3"]])])
        return [
            Requirement("r1", "consolidate_sheets",
                        {"sheets": ["Jan", "Feb"]}),
            Requirement("r2", "trim_whitespace", {}),
            Requirement("r3", "drop_empty_rows", {"columns": ["Item"]}),
            Requirement("r4", "require_columns",
                        {"columns": ["Item", "Qty", "Unit price"]}),
            Requirement("r5", "compute_column",
                        {"into": "Line total", "using": ["Qty", "Unit price"],
                         "expression": "product", "format": "0.00"}),
            Requirement("r6", "sort_rows",
                        {"columns": ["Line total"], "descending": True}),
            Requirement("r7", "total_row",
                        {"aggregates": {"Line total": "sum", "Qty": "sum"}}),
            Requirement("r8", "preserve_columns", {"columns": ["Client ref"]}),
            Requirement("r9", "row_reconciliation",
                        {"expected_dropped": 1, "expected_added": 1}),
            Requirement("r10", "no_unauthorised_changes",
                        {"authorised_columns": ["Item"],
                         "added_columns": ["Line total"]}),
            Requirement("r11", "formulas_agree", {}),
        ]

    def test_the_worker_refuses_nothing_and_the_verifier_passes(self):
        requirements = self.arrange()
        report = self.run_job(requirements)
        self.assertEqual(report.refused, [], report.summary())
        checked = self.check(requirements)
        self.assertTrue(checked.passed, checked.summary())

    def test_the_rows_reconcile(self):
        requirements = self.arrange()
        report = self.run_job(requirements)
        self.assertEqual(
            report.rows_in - report.rows_dropped + report.rows_added,
            report.rows_out)

    def test_every_computed_cell_carries_its_formula(self):
        """So the file opens with a correct number in a reader that does not
        evaluate formulas, and a person can still see how it was derived."""
        self.run_job(self.arrange())
        rows = self.delivered_cells()
        total_column = [c.value for c in rows[0]].index("Line total")
        for row in rows[1:]:
            with self.subTest(row=row[0].value):
                self.assertTrue(row[total_column].formula)

    def test_a_column_nobody_authorised_changing_is_untouched(self):
        """The client's own reference column keeps its values exactly."""
        self.run_job(self.arrange())
        rows = self.delivered()
        column = rows[0].index("Client ref")
        refs = {r[column] for r in rows[1:] if r[column]}
        self.assertEqual(refs, {"REF-1", "REF-2", "REF-3"})

    def test_a_price_written_with_a_currency_symbol_stays_as_written(self):
        """Nothing authorised reformatting it. The computed column used its
        numeric value, which is a different thing from rewriting the client's
        cell."""
        self.run_job(self.arrange())
        rows = self.delivered()
        column = rows[0].index("Unit price")
        self.assertIn("£2.50", [str(r[column]) for r in rows[1:]])

    def test_the_sort_put_the_largest_line_first(self):
        self.run_job(self.arrange())
        rows = self.delivered()
        column = rows[0].index("Line total")
        values = [verifier.number(r[column]) for r in rows[1:-1]]
        self.assertEqual(values, sorted(values, reverse=True))


class TheVerifierCatchesAWrongWorker(Base):
    """Each test delivers a correct file, puts one defect in it, and asserts
    which check notices. Without these the rest of the suite proves only that
    the worker agrees with itself."""

    def arrange(self):
        self.given([self.sheet("S", [
            ["Item", "Qty", "Unit price", "Client ref"],
            ["widget", 3, "2.50", "REF-1"],
            ["gadget", 10, "1.25", "REF-2"]])])
        return [
            Requirement("r1", "trim_whitespace", {}),
            Requirement("r2", "require_columns",
                        {"columns": ["Item", "Qty", "Unit price"]}),
            Requirement("r3", "compute_column",
                        {"into": "Line total", "using": ["Qty", "Unit price"],
                         "expression": "product"}),
            Requirement("r4", "total_row",
                        {"aggregates": {"Line total": "sum"}}),
            Requirement("r5", "preserve_columns", {"columns": ["Client ref"]}),
            Requirement("r6", "row_reconciliation",
                        {"expected_dropped": 0, "expected_added": 1}),
            Requirement("r7", "no_unauthorised_changes",
                        {"authorised_columns": ["Item"],
                         "added_columns": ["Line total"]}),
            Requirement("r8", "formulas_agree", {}),
        ]

    def deliver_then_break(self, change):
        requirements = self.arrange()
        report = self.run_job(requirements)
        self.assertEqual(report.refused, [], report.summary())
        clean = self.check(requirements)
        self.assertTrue(clean.passed, f"the undamaged job failed: "
                                      f"{clean.summary()}")
        cells = self.delivered_cells()
        rows = [[c.value for c in row] for row in cells]
        formulas = {(r, c): cell.formula
                    for r, row in enumerate(cells, 1)
                    for c, cell in enumerate(row, 1) if cell.formula}
        change(rows, formulas)
        self.output.write_bytes(xlsx.write_workbook([xlsx.SheetOut(
            name="Result", rows=rows, formulas=formulas)]))
        return self.check(requirements)

    def assertCaughtBy(self, report, check):
        self.assertEqual(report.verdict, verifier.FAIL, report.summary())
        self.assertIn(check, [f.check for f in report.failures()],
                      report.summary())

    def test_a_wrong_computed_value_is_caught(self):
        report = self.deliver_then_break(
            lambda rows, formulas: rows[1].__setitem__(4, 99))
        self.assertCaughtBy(report, "compute_column")

    def test_a_wrong_total_is_caught(self):
        report = self.deliver_then_break(
            lambda rows, formulas: rows[-1].__setitem__(4, 1))
        self.assertCaughtBy(report, "total_row")

    def test_a_changed_preserved_column_is_caught(self):
        report = self.deliver_then_break(
            lambda rows, formulas: rows[1].__setitem__(3, "REF-HACKED"))
        self.assertCaughtBy(report, "preserve_columns")

    def test_a_lost_row_is_caught(self):
        report = self.deliver_then_break(
            lambda rows, formulas: rows.pop(1))
        self.assertCaughtBy(report, "row_reconciliation")

    def test_whitespace_left_in_is_caught(self):
        report = self.deliver_then_break(
            lambda rows, formulas: rows[1].__setitem__(0, " widget "))
        self.assertCaughtBy(report, "trim_whitespace")

    def test_an_altered_source_column_is_caught(self):
        report = self.deliver_then_break(
            lambda rows, formulas: rows[1].__setitem__(1, 999))
        self.assertCaughtBy(report, "no_unauthorised_changes")

    def test_a_right_value_with_a_formula_on_the_wrong_column_is_caught(self):
        """The reason a computed cell is two questions rather than one. The
        arithmetic check passes this, correctly -- the number is right. A
        client finds it the moment they change an input."""
        report = self.deliver_then_break(
            lambda rows, formulas: formulas.__setitem__((2, 5), "A2*C2"))
        self.assertCaughtBy(report, "formulas_agree")
        self.assertNotIn("compute_column",
                         [f.check for f in report.failures()])

    def test_a_formula_referring_to_another_row_is_caught(self):
        """A formula naming the right columns on the wrong row is a cell that
        will change when an unrelated row does."""
        report = self.deliver_then_break(
            lambda rows, formulas: formulas.__setitem__((2, 5), "B3*C3"))
        self.assertCaughtBy(report, "formulas_agree")

    def test_an_invented_column_is_caught(self):
        def add_column(rows, formulas):
            rows[0].append("Discount")
            for row in rows[1:]:
                row.append(5)

        self.assertCaughtBy(self.deliver_then_break(add_column),
                            "no_unauthorised_changes")


class TheWorkerRefusesRatherThanImprovising(Base):

    def test_a_workbook_it_cannot_read_is_refused_and_nothing_is_written(self):
        self.source.write_bytes(b"this is not a spreadsheet")
        report = self.run_job([Requirement("r1", "trim_whitespace", {})])
        self.assertTrue(report.refused)
        self.assertFalse(self.output.exists(),
                         "a partial deliverable was written for a brief the "
                         "capability could not honour")

    def test_a_named_column_that_is_not_there_stops_a_mandatory_step(self):
        self.given([self.sheet("S", [["a"], [1]])])
        report = self.run_job([
            Requirement("r1", "require_columns", {"columns": ["Total"]})])
        self.assertTrue(report.refused)
        self.assertIn("no column named 'Total'", report.refused[0])

    def test_sheets_with_different_columns_are_not_stacked(self):
        """Stacking by position would put one sheet's prices into another's
        quantity column, and every figure in the result would be valid."""
        self.given([self.sheet("A", [["Item", "Qty"], ["x", 1]]),
                    self.sheet("B", [["Qty", "Item"], [2, "y"]])])
        report = self.run_job([
            Requirement("r1", "consolidate_sheets", {"sheets": ["A", "B"]})])
        self.assertTrue(report.refused)
        self.assertIn("whose columns differ", report.refused[0])

    def test_it_will_not_overwrite_a_column_the_client_supplied(self):
        self.given([self.sheet("S", [["Qty", "Price", "Total"], [2, 3, 0]])])
        report = self.run_job([
            Requirement("r1", "compute_column",
                        {"into": "Total", "using": ["Qty", "Price"],
                         "expression": "product"})])
        self.assertTrue(report.refused)
        self.assertIn("already exists", report.refused[0])

    def test_a_worksheet_with_no_header_is_refused(self):
        """This capability does not invent column names."""
        self.given([self.sheet("S", [[None, None], [1, 2]])])
        report = self.run_job([
            Requirement("r1", "trim_whitespace", {})])
        self.assertTrue(report.refused)
        self.assertIn("does not invent", report.refused[0])

    def test_an_advisory_step_that_cannot_run_is_recorded_and_skipped(self):
        """A mandatory step stops the job; an advisory one is noted. The
        difference is whether delivering without it means delivering work the
        client is owed and did not get."""
        self.given([self.sheet("S", [["Item"], ["x"]])])
        report = self.run_job([
            Requirement("r1", "sort_rows", {"columns": ["Missing"]},
                        criticality="ADVISORY")])
        self.assertEqual(report.refused, [])
        self.assertTrue(report.exceptions)
        self.assertTrue(self.output.exists())


class TheVerifierSaysWhenItCannotTell(Base):

    def test_a_check_it_has_no_implementation_for_is_unverifiable(self):
        """Not passed. A verifier that silently approved what it could not
        check would be worse than no verifier."""
        self.given([self.sheet("S", [["a"], [1]])])
        self.run_job([])
        report = self.check([Requirement("r1", "colour_the_headers", {})])
        self.assertEqual(report.verdict, verifier.UNVERIFIABLE)
        self.assertIn("no check for", report.findings[-1].detail)

    def test_unverifiable_outranks_pass_in_the_overall_verdict(self):
        """A job where half the requirements could not be checked has not been
        verified, and reporting the half that passed would mislead."""
        report = verifier.Report(findings=[
            verifier.Finding("a", "x", verifier.PASS, "fine", evidence="e"),
            verifier.Finding("b", "y", verifier.UNVERIFIABLE, "cannot tell")])
        self.assertEqual(report.verdict, verifier.UNVERIFIABLE)

    def test_a_fail_outranks_everything(self):
        report = verifier.Report(findings=[
            verifier.Finding("a", "x", verifier.PASS, "fine", evidence="e"),
            verifier.Finding("b", "y", verifier.UNVERIFIABLE, "cannot tell"),
            verifier.Finding("c", "z", verifier.FAIL, "wrong")])
        self.assertEqual(report.verdict, verifier.FAIL)

    def test_no_findings_at_all_is_unverifiable_not_pass(self):
        self.assertEqual(verifier.Report().verdict, verifier.UNVERIFIABLE)

    def test_an_unreadable_delivered_file_fails_rather_than_puzzles(self):
        self.given([self.sheet("S", [["a"], [1]])])
        self.output.write_bytes(b"not a workbook")
        report = self.check([Requirement("r1", "trim_whitespace", {})])
        self.assertEqual(report.verdict, verifier.FAIL)

    def test_an_unreadable_source_makes_the_output_unverifiable(self):
        """Nothing about the output can be established without the input it
        was supposed to be derived from."""
        self.source.write_bytes(b"not a workbook")
        self.output.write_bytes(xlsx.write_workbook(
            [self.sheet("Result", [["a"], [1]])]))
        report = self.check([Requirement("r1", "trim_whitespace", {})])
        self.assertEqual(report.verdict, verifier.UNVERIFIABLE)

    def test_a_display_format_is_reported_as_unverifiable_not_passed(self):
        """It changes how a figure is shown, not what it is, so there is no
        value to recompute."""
        self.given([self.sheet("S", [["a"], [1]])])
        self.run_job([Requirement("r1", "number_format",
                                  {"column": "a", "format": "0.00"})])
        report = self.check([Requirement("r1", "number_format",
                                         {"column": "a", "format": "0.00"})])
        self.assertEqual(report.verdict, verifier.UNVERIFIABLE)


class TheVerifierDoesNotShareTheWorkersCode(unittest.TestCase):

    def test_it_does_not_import_the_worker(self):
        """If it shared the worker's arithmetic, two copies of one bug would
        agree and the agreement would be reported as certification."""
        import ast
        import inspect

        tree = ast.parse(inspect.getsource(verifier))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    imported.add(alias.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add(alias.name)
        self.assertNotIn("sheetwork", imported)

    def test_it_has_its_own_arithmetic_for_every_expression(self):
        """A missing branch would raise rather than pass, but a verifier that
        could not check an expression the worker computes is a gap worth
        failing a test over."""
        for expression in worker.EXPRESSIONS:
            with self.subTest(expression=expression):
                verifier.recompute(expression,
                                   [Decimal(6), Decimal(3)])

    def test_it_has_its_own_arithmetic_for_every_aggregate(self):
        for operation in worker.AGGREGATES:
            with self.subTest(operation=operation):
                verifier.reaggregate(operation, [Decimal(1), Decimal(2)])

    def test_every_operation_the_worker_performs_has_a_check(self):
        """An operation with no check is work nobody verifies."""
        self.assertEqual(worker.OPERATIONS - verifier.CHECKS, set())

    def test_every_verify_only_requirement_has_a_check(self):
        self.assertEqual(worker.VERIFY_ONLY - verifier.CHECKS, set())


class FormulaReferencesAreParsedNotGuessed(unittest.TestCase):

    def test_a_simple_product(self):
        self.assertEqual(verifier.cells_in("B2*C2"), [("B", 2), ("C", 2)])

    def test_a_range_is_expanded_down_one_column(self):
        self.assertEqual(verifier.cells_in("SUM(D2:D4)"),
                         [("D", 2), ("D", 3), ("D", 4)])

    def test_a_range_spanning_columns_is_not_invented(self):
        """This capability does not write one, and expanding it would invent a
        shape the check then approved."""
        self.assertEqual(verifier.cells_in("SUM(A1:C1)"), [])

    def test_absolute_references_are_the_same_cells(self):
        self.assertEqual(verifier.cells_in("$B$2*C2"), [("B", 2), ("C", 2)])

    def test_a_mix_of_a_range_and_a_single_cell(self):
        self.assertEqual(verifier.cells_in("SUM(D2:D3)+E9"),
                         [("D", 2), ("D", 3), ("E", 9)])


if __name__ == "__main__":
    unittest.main()
