"""The two operations the owner authorised for conditional promotion.

Both were implemented and neither was certified: the battery never built a
deliverable that exercised them, so no verifier had ever been shown a wrong
rename or a wrong ordering, and the capability registry was right to refuse
them. What follows is the evidence that was missing — what each operation must
do, what it must refuse, and what a broken one produces — plus the interaction
tests, because certifying operations one at a time says nothing about what they
do to each other.

Three defects were found here and fixed before any promotion:

* a renamed column shares no name between source and deliverable, so it was
  invisible to every comparison made by name, and swapping it with the column
  beside it moved one of the client's columns undetected;
* authorising a rename authorised *full* change to that column, so the header
  could be renamed and every value beneath it replaced;
* every check compared one column at a time, so a deliverable that reordered a
  single column — which is what sorting a column instead of sorting the rows
  produces — kept every value and put every value on the wrong row.
"""

from __future__ import annotations

import csv
import pathlib
import tempfile
import unittest

from solvent import csvverify, csvwork
from solvent.harness import run_csv_job
from solvent.types import CheckResult
from tests_solvent import fixtures_csv as fx

HEAD = "Order ID,Customer,Order Date,Region,Units,Total\n"
SOURCE = (HEAD +
          "1002,Beta LLC,2026-01-16,South,5,200.00\n"
          "1001,Acme Corp,2026-01-15,North,10,250.00\n"
          "1003,Gamma Inc,2026-01-17,East,8,100.00\n")
GOOD = ("Order ID,Customer,Order Date,Region,Quantity,Total\n"
        "1001,Acme Corp,2026-01-15,North,10,250.00\n"
        "1002,Beta LLC,2026-01-16,South,5,200.00\n"
        "1003,Gamma Inc,2026-01-17,East,8,100.00\n")

#: What the plan derives for a job that renames Units and sorts by Order ID.
NOCHANGE = {"authorised_columns": csvwork.authorised_columns(
                [fx.RENAME, fx.SORT], HEAD.strip().split(",")),
            "renamed_columns": csvwork.renamed_columns([fx.RENAME, fx.SORT])}
PARAMS = {
    "rename_headers": {"mapping": {"Units": "Quantity"}},
    "sort_rows": {"columns": ["Order ID"]},
    "row_reconciliation": {"duplicates_removed": True},
    "drop_exact_duplicates": {},
    "preserve_columns": {"columns": ["Total"]},
    "normalise_dates": {"columns": ["Order Date"]},
    "no_unauthorised_changes": NOCHANGE,
}
EVERY_CHECK = tuple(PARAMS)


def rows(path):
    with open(path, newline="", encoding="utf-8") as handle:
        return list(csv.reader(handle))


def verdicts(output: str, source: str = SOURCE) -> dict:
    """Every check in the promoted scope, run on one artifact, by name."""
    directory = pathlib.Path(tempfile.mkdtemp())
    (directory / "source.csv").write_text(source, encoding="utf-8")
    (directory / "out.csv").write_text(output, encoding="utf-8")
    return {check: csvverify.run(check, source=str(directory / "source.csv"),
                                 output=str(directory / "out.csv"),
                                 params=PARAMS[check])
            for check in EVERY_CHECK}


def deliver(content, requirements):
    source, work = fx.workspace(content)
    return run_csv_job(source=source, requirements=list(requirements),
                       workdir=work)


class RenamingDoesWhatItSays(unittest.TestCase):
    def test_the_named_column_is_renamed_in_place(self):
        report = deliver(fx.CLEAN, [fx.RENAME, fx.OPENS])
        self.assertTrue(report.delivered, report.escalated)
        header = rows(report.artifacts[-1].path)[0]
        self.assertEqual(header,
                         ["Order ID", "Customer", "Order Date", "Region",
                          "Quantity", "Total"])

    def test_the_values_beneath_it_are_untouched(self):
        report = deliver(fx.CLEAN, [fx.RENAME, fx.OPENS])
        delivered = rows(report.artifacts[-1].path)
        self.assertEqual([row[4] for row in delivered[1:]], ["10", "5"])

    def test_no_other_column_is_renamed(self):
        report = deliver(fx.CLEAN, [fx.RENAME, fx.OPENS])
        header = rows(report.artifacts[-1].path)[0]
        self.assertEqual(header[:4],
                         ["Order ID", "Customer", "Order Date", "Region"])

    def test_matching_is_exact_not_by_prefix_or_case(self):
        """``Réf`` and ``Réference`` are different columns and stay that way."""
        report = deliver(fx.UNICODE_HEADERS, [
            fx.req("R-R", "Rename Réf to Ref.", "rename_headers",
                   {"mapping": {"Réf": "Ref"}}), fx.OPENS])
        self.assertTrue(report.delivered, report.escalated)
        self.assertEqual(rows(report.artifacts[-1].path)[0][:2],
                         ["Ref", "Réference"])

    def test_a_byte_order_mark_does_not_hide_the_first_column(self):
        report = deliver(fx.BOM_SOURCE, [fx.RENAME, fx.OPENS])
        self.assertTrue(report.delivered, report.escalated)
        self.assertIn("Quantity", rows(report.artifacts[-1].path)[0])


class RenamingRefusesRatherThanGuesses(unittest.TestCase):
    def test_renaming_an_absent_column_is_refused(self):
        report = deliver(fx.CLEAN, [
            fx.req("R-R", "Rename Unit Price.", "rename_headers",
                   {"mapping": {"Unit Price": "Rate"}}), fx.OPENS])
        self.assertFalse(report.delivered)
        self.assertIn("cannot rename absent column", report.escalated)

    def test_renaming_onto_an_existing_name_is_refused(self):
        """Merging two different columns into one is worse than doing nothing."""
        report = deliver(fx.COLLIDING_TARGET, [fx.RENAME, fx.OPENS])
        self.assertFalse(report.delivered)
        self.assertIn("duplicate headers", report.escalated)

    def test_a_rename_with_no_mapping_is_refused_at_planning(self):
        report = deliver(fx.CLEAN, [
            fx.req("R-R", "Rename the columns.", "rename_headers", {}), fx.OPENS])
        self.assertFalse(report.delivered)


class SortingDoesWhatItSays(unittest.TestCase):
    def test_rows_come_back_in_order(self):
        report = deliver(fx.MESSY, [fx.DEDUPE, fx.SORT, fx.RECONCILE, fx.OPENS])
        self.assertTrue(report.delivered, report.escalated)
        ids = [row[0] for row in rows(report.artifacts[-1].path)[1:]]
        self.assertEqual(ids, sorted(ids))

    def test_ordering_is_textual_so_ten_precedes_nine(self):
        """Pinned, not incidental. An identifier is not a number — sorting
        these numerically would put 9 first and destroy a leading zero."""
        report = deliver(fx.TEXT_ORDER, [fx.SORT, fx.OPENS])
        self.assertEqual([row[0] for row in rows(report.artifacts[-1].path)[1:]],
                         ["10", "2", "9"])

    def test_rows_sharing_a_key_keep_the_order_the_client_sent(self):
        report = deliver(fx.TIED_KEYS, [fx.SORT, fx.OPENS])
        delivered = rows(report.artifacts[-1].path)[1:]
        self.assertEqual([row[1] for row in delivered],
                         ["Beta LLC", "Zeta Co", "Acme Corp"])

    def test_an_empty_key_sorts_first_rather_than_anywhere(self):
        report = deliver(fx.BLANK_KEYS, [fx.SORT, fx.OPENS])
        self.assertEqual([row[0] for row in rows(report.artifacts[-1].path)[1:]],
                         ["", "1000", "1001"])

    def test_sorting_by_several_columns_uses_them_in_order(self):
        report = deliver(fx.TIED_KEYS, [
            fx.req("R-S", "Sort by Order ID then Customer.", "sort_rows",
                   {"columns": ["Order ID", "Customer"]}), fx.OPENS])
        delivered = rows(report.artifacts[-1].path)[1:]
        self.assertEqual([(row[0], row[1]) for row in delivered],
                         [("1000", "Beta LLC"), ("1001", "Acme Corp"),
                          ("1001", "Zeta Co")])

    def test_a_value_containing_a_newline_survives_sorting(self):
        report = deliver(fx.EMBEDDED_NEWLINE, [
            fx.req("R-S", "Sort by Customer.", "sort_rows",
                   {"columns": ["Customer"]}), fx.OPENS])
        self.assertTrue(report.delivered, report.escalated)
        customers = [row[1] for row in rows(report.artifacts[-1].path)[1:]]
        self.assertIn("Acme Corp\nSuite 4", customers)

    def test_crlf_line_endings_do_not_become_part_of_the_sort_key(self):
        report = deliver(fx.CRLF_SOURCE, [fx.SORT, fx.OPENS])
        self.assertEqual([row[0] for row in rows(report.artifacts[-1].path)[1:]],
                         ["1001", "1002"])

    def test_sorting_by_an_absent_column_is_refused(self):
        report = deliver(fx.CLEAN, [
            fx.req("R-S", "Sort by Invoice No.", "sort_rows",
                   {"columns": ["Invoice No"]}), fx.OPENS])
        self.assertFalse(report.delivered)
        self.assertIn("unknown sort column", report.escalated)

    def test_sorting_with_no_column_named_is_refused_at_planning(self):
        report = deliver(fx.CLEAN, [
            fx.req("R-S", "Sort it.", "sort_rows", {}), fx.OPENS])
        self.assertFalse(report.delivered)


class TheChecksCatchABrokenRename(unittest.TestCase):
    def test_a_correct_deliverable_is_accepted_by_every_check(self):
        """Guards the rest of this class: a check that refuses everything
        catches every defect and is worth nothing."""
        for check, outcome in verdicts(GOOD).items():
            with self.subTest(check=check):
                self.assertTrue(outcome.passed, f"{check}: {outcome.detail}")

    def test_a_rename_that_never_happened_is_caught(self):
        outcome = verdicts(GOOD.replace("Quantity", "Units"))["rename_headers"]
        self.assertEqual(outcome.result, CheckResult.FAIL)

    def test_a_rename_to_the_wrong_name_is_caught(self):
        """The old name is gone and the header reads well. Only the agreed
        mapping says it is wrong."""
        outcome = verdicts(GOOD.replace("Quantity", "Qty"))["rename_headers"]
        self.assertEqual(outcome.result, CheckResult.FAIL)

    def test_renaming_a_column_nobody_asked_about_is_caught(self):
        outcome = verdicts(GOOD.replace("Region", "Zone"))["no_unauthorised_changes"]
        self.assertEqual(outcome.result, CheckResult.FAIL)
        self.assertIn("Region", outcome.detail)

    def test_moving_the_renamed_column_is_caught(self):
        """The defect that made this hardening necessary. Both headers hold the
        other four names in the same order, so every comparison by name saw a
        correct file — while the client's Total column had moved."""
        moved = ("Order ID,Customer,Order Date,Region,Total,Quantity\n"
                 "1001,Acme Corp,2026-01-15,North,250.00,10\n"
                 "1002,Beta LLC,2026-01-16,South,200.00,5\n"
                 "1003,Gamma Inc,2026-01-17,East,100.00,8\n")
        outcome = verdicts(moved)["no_unauthorised_changes"]
        self.assertEqual(outcome.result, CheckResult.FAIL)

    def test_renaming_the_header_while_replacing_the_values_is_caught(self):
        """Authorising a rename authorises a change of *name*. It is not a
        licence to rewrite the column."""
        hollowed = ("Order ID,Customer,Order Date,Region,Quantity,Total\n"
                    "1001,Acme Corp,2026-01-15,North,North,250.00\n"
                    "1002,Beta LLC,2026-01-16,South,South,200.00\n"
                    "1003,Gamma Inc,2026-01-17,East,East,100.00\n")
        outcome = verdicts(hollowed)["no_unauthorised_changes"]
        self.assertEqual(outcome.result, CheckResult.FAIL)

    def test_a_rename_authorises_the_name_and_nothing_else(self):
        authorised = csvwork.authorised_columns([fx.RENAME], HEAD.strip().split(","))
        self.assertEqual(authorised["Units"], "rename")
        self.assertNotEqual(authorised["Units"], "full")

    def test_a_broader_requirement_on_the_same_column_still_wins(self):
        """Guards the test above: narrowing a rename must not narrow the
        authorisation another requirement legitimately granted."""
        also_dated = csvwork.authorised_columns(
            [fx.RENAME, fx.req("R-D", "ISO dates.", "normalise_dates",
                               {"columns": ["Units"]})],
            HEAD.strip().split(","))
        self.assertEqual(also_dated["Units"], "full")


class TheChecksCatchABrokenSort(unittest.TestCase):
    def test_an_unsorted_deliverable_is_caught(self):
        unsorted = ("Order ID,Customer,Order Date,Region,Quantity,Total\n"
                    "1002,Beta LLC,2026-01-16,South,5,200.00\n"
                    "1001,Acme Corp,2026-01-15,North,10,250.00\n"
                    "1003,Gamma Inc,2026-01-17,East,8,100.00\n")
        self.assertEqual(verdicts(unsorted)["sort_rows"].result, CheckResult.FAIL)

    def test_a_reversed_deliverable_is_caught(self):
        reversed_rows = ("Order ID,Customer,Order Date,Region,Quantity,Total\n"
                         "1003,Gamma Inc,2026-01-17,East,8,100.00\n"
                         "1002,Beta LLC,2026-01-16,South,5,200.00\n"
                         "1001,Acme Corp,2026-01-15,North,10,250.00\n")
        self.assertEqual(verdicts(reversed_rows)["sort_rows"].result,
                         CheckResult.FAIL)

    def test_sorting_by_the_wrong_column_is_caught(self):
        by_customer = ("Order ID,Customer,Order Date,Region,Quantity,Total\n"
                       "1003,Alpha Co,2026-01-17,East,8,100.00\n"
                       "1002,Beta LLC,2026-01-16,South,5,200.00\n"
                       "1001,Gamma Inc,2026-01-15,North,10,250.00\n")
        source = (HEAD +
                  "1002,Beta LLC,2026-01-16,South,5,200.00\n"
                  "1001,Gamma Inc,2026-01-15,North,10,250.00\n"
                  "1003,Alpha Co,2026-01-17,East,8,100.00\n")
        self.assertEqual(verdicts(by_customer, source)["sort_rows"].result,
                         CheckResult.FAIL)

    def test_sorting_one_column_instead_of_the_rows_is_caught(self):
        """The defect a sorting capability is most likely to ship. Every value
        is present, every column's values are exactly the source's, and every
        one of them is against the wrong record — the client would receive Beta
        LLC's order under Acme Corp's order number."""
        scrambled = ("Order ID,Customer,Order Date,Region,Quantity,Total\n"
                     "1001,Beta LLC,2026-01-16,South,5,200.00\n"
                     "1002,Acme Corp,2026-01-15,North,10,250.00\n"
                     "1003,Gamma Inc,2026-01-17,East,8,100.00\n")
        outcome = verdicts(scrambled)["row_reconciliation"]
        self.assertEqual(outcome.result, CheckResult.FAIL)
        self.assertIn("row(s) are not", outcome.detail)

    def test_the_row_scramble_defeats_every_per_column_check(self):
        """Why the check above had to exist: nothing else notices."""
        scrambled = ("Order ID,Customer,Order Date,Region,Quantity,Total\n"
                     "1001,Beta LLC,2026-01-16,South,5,200.00\n"
                     "1002,Acme Corp,2026-01-15,North,10,250.00\n"
                     "1003,Gamma Inc,2026-01-17,East,8,100.00\n")
        results = verdicts(scrambled)
        for check in ("sort_rows", "rename_headers", "drop_exact_duplicates",
                      "preserve_columns", "no_unauthorised_changes"):
            with self.subTest(check=check):
                self.assertTrue(results[check].passed)

    def test_a_date_moved_onto_another_row_is_caught(self):
        """Even when the day it landed on is one some other row could have
        meant. The source below writes one date unambiguously and one
        ambiguously, so the set of supplied days is unchanged by the swap."""
        source = (HEAD +
                  "1001,Acme Corp,08-Oct-2025,North,10,250.00\n"
                  "1002,Beta LLC,10/08/2025,South,5,200.00\n")
        swapped = ("Order ID,Customer,Order Date,Region,Quantity,Total\n"
                   "1001,Acme Corp,2025-08-10,North,10,250.00\n"
                   "1002,Beta LLC,2025-10-08,South,5,200.00\n")
        outcome = verdicts(swapped, source)["normalise_dates"]
        self.assertEqual(outcome.result, CheckResult.FAIL)
        # Caught by binding each date to its own row, not by the weaker
        # comparison against the set of days the source supplied — that set is
        # unchanged here, which is exactly why the swap used to pass.
        self.assertEqual(outcome.computed["row_key"], "Order ID")
        self.assertIn("Order ID", " ".join(outcome.computed["examples"]))

    def test_an_ambiguous_date_is_still_not_forced_to_one_reading(self):
        """Guards the test above. Binding dates to rows must not turn into
        asserting a convention the client never stated."""
        source = (HEAD +
                  "1001,Acme Corp,10/08/2025,North,10,250.00\n"
                  "1002,Beta LLC,2025-01-16,South,5,200.00\n")
        for reading in ("2025-10-08", "2025-08-10"):
            with self.subTest(reading=reading):
                output = ("Order ID,Customer,Order Date,Region,Quantity,Total\n"
                          f"1001,Acme Corp,{reading},North,10,250.00\n"
                          "1002,Beta LLC,2025-01-16,South,5,200.00\n")
                self.assertTrue(verdicts(output, source)["normalise_dates"].passed)


class TheOperationsWorkTogether(unittest.TestCase):
    """§6: operations certified one at a time say nothing about each other."""

    ALL = (fx.DEDUPE, fx.RENAME, fx.SORT, fx.TRIM, fx.ISO_DATES, fx.REGIONS,
           fx.KEEP_TOTALS, fx.RECONCILE, fx.OPENS)

    def test_every_operation_in_one_job_delivers(self):
        report = deliver(fx.MESSY, self.ALL)
        self.assertTrue(report.delivered, report.escalated)

    def test_the_result_is_renamed_sorted_deduplicated_and_normalised(self):
        report = deliver(fx.MESSY, self.ALL)
        header, *body = rows(report.artifacts[-1].path)
        self.assertEqual(header[4], "Quantity")
        self.assertEqual([row[0] for row in body], sorted(row[0] for row in body))
        self.assertEqual(len(body), len({tuple(row) for row in body}))
        self.assertTrue(all(len(row[2]) in (0, 10) for row in body))
        self.assertEqual([row[1] for row in body],
                         [row[1].strip() for row in body])

    def test_renaming_before_sorting_leaves_the_sort_key_addressable(self):
        """Ordering is fixed, not requirement order: a job that sorts by the
        column it also renames must still resolve the name it was given."""
        report = deliver(fx.CLEAN, [
            fx.req("R-R", "Rename Units to Quantity.", "rename_headers",
                   {"mapping": {"Units": "Quantity"}}),
            fx.req("R-S", "Sort by Units.", "sort_rows",
                   {"columns": ["Units"]}), fx.OPENS])
        self.assertFalse(report.delivered)
        self.assertIn("unknown sort column", report.escalated)

    def test_sorting_after_renaming_uses_the_new_name(self):
        report = deliver(fx.CLEAN, [
            fx.req("R-R", "Rename Units to Quantity.", "rename_headers",
                   {"mapping": {"Units": "Quantity"}}),
            fx.req("R-S", "Sort by Quantity.", "sort_rows",
                   {"columns": ["Quantity"]}), fx.OPENS])
        self.assertTrue(report.delivered, report.escalated)
        self.assertEqual([row[4] for row in rows(report.artifacts[-1].path)[1:]],
                         ["10", "5"])

    def test_the_declared_order_of_operations_is_stated_not_incidental(self):
        """Renaming happens before anything can refer to the new names, and
        sorting happens last. Pinned because requirement order is the client's
        and must not decide what the operations do to each other."""
        steps, refused = csvwork.plan(list(self.ALL))
        self.assertEqual(refused, [])
        names = [step.operation for step in steps]
        self.assertLess(names.index("rename_headers"), names.index("sort_rows"))
        self.assertEqual(names[-1], "sort_rows")
        self.assertEqual(names[0], "rename_headers")
        # And validation comes before any of it, when the plan has any.
        with_columns = csvwork.plan(
            [fx.req("R-COLS", "Keep Order ID.", "require_columns",
                    {"columns": ["Order ID"]}), *self.ALL])[0]
        self.assertEqual(with_columns[0].operation, "require_columns")

    def test_reversing_the_requirements_does_not_reorder_the_work(self):
        """Guards the test above: an order that follows the checklist is not a
        fixed order."""
        forward = [s.operation for s in csvwork.plan(list(self.ALL))[0]]
        backward = [s.operation for s in csvwork.plan(list(reversed(self.ALL)))[0]]
        self.assertEqual(forward, backward)

    def test_a_job_that_was_not_asked_to_deduplicate_keeps_its_duplicates(self):
        """And is not accused of altering anything for it. Comparing protected
        columns against the *distinct* source rows unconditionally rejected
        correct work whenever the plan had no dedupe step."""
        report = deliver(fx.DUPLICATES, [
            fx.SORT,
            fx.req("R-ROWS", "No row may be lost.", "row_reconciliation",
                   {"duplicates_removed": False}),
            fx.OPENS])
        self.assertTrue(report.delivered, report.escalated)
        self.assertEqual(len(rows(report.artifacts[-1].path)) - 1, 4)

    def test_sorting_does_not_resurrect_a_duplicate(self):
        report = deliver(fx.DUPLICATES, [fx.DEDUPE, fx.SORT, fx.RECONCILE,
                                         fx.OPENS])
        body = rows(report.artifacts[-1].path)[1:]
        self.assertEqual(len(body), 2)

    def test_the_derived_no_change_requirement_carries_the_rename(self):
        """Without this the anchor is lost and the whole hardening is inert."""
        from solvent.harness import _csv_derived

        source, _ = fx.workspace(fx.CLEAN)
        derived = _csv_derived(source, [fx.RENAME, fx.SORT, fx.OPENS])
        self.assertEqual(len(derived), 1)
        self.assertEqual(derived[0].params["renamed_columns"],
                         {"Units": "Quantity"})
        self.assertFalse(derived[0].params["duplicates_removed"])

    def test_the_derived_requirement_reports_a_dedupe_when_there_is_one(self):
        """Guards the test above: a flag that is always false is not a flag."""
        from solvent.harness import _csv_derived

        source, _ = fx.workspace(fx.CLEAN)
        derived = _csv_derived(source, [fx.DEDUPE, fx.RENAME, fx.OPENS])
        self.assertTrue(derived[0].params["duplicates_removed"])
