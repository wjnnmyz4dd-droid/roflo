"""Protected columns, against a brief whose transformations reorder the work.

The csv worker's operation order is fixed: trimming (rank 2) happens before
deduplication (rank 5). So by the time rows are deduped, a protected
column's values have legitimately been trimmed, and comparing them against
the values as they *arrived* fails work that is correct.

The point of these tests is that the fix must not buy that by weakening the
check. Each "this should pass" case is paired with the nearest thing that
should still fail.
"""

from __future__ import annotations

import pathlib
import tempfile
import unittest

from solvent import csvverify
from solvent.types import CheckResult, Requirement, RequirementSource


def req(check: str, params: dict | None = None) -> Requirement:
    return Requirement(
        id=f"R-{check}", text=check, source=RequirementSource.CLIENT_CONFIRMED_STRUCTURED,
        acceptance="as agreed", check=check, params=params or {})


class ProtectedColumns(unittest.TestCase):

    #: The brief in every passing case below: trim, then dedupe, and leave
    #: Name and Email alone otherwise.
    TRIM_AND_DEDUPE = [req("trim_whitespace", {"columns": ["Name"]}),
                       req("drop_exact_duplicates"),
                       req("preserve_columns", {"columns": ["Name", "Email"]})]

    def setUp(self):
        self.box = tempfile.TemporaryDirectory()
        self.addCleanup(self.box.cleanup)
        self.dir = pathlib.Path(self.box.name)

    def check(self, source_text, output_text, *, columns=("Name", "Email"),
              requirements=None, **extra):
        src = self.dir / "source.csv"
        out = self.dir / "output.csv"
        src.write_text(source_text, encoding="utf-8")
        out.write_text(output_text, encoding="utf-8")
        params = {"columns": list(columns), **extra}
        if requirements is not None:
            params["requirements"] = requirements
        return csvverify.check_preserve_columns(str(src), str(out), params)

    # ------------------------------------------------- the reported defect
    PADDED_SOURCE = ("Name,Email,Amount\n"
                     "  Acme Ltd ,a@acme.test,100.00\n"
                     "Acme Ltd,a@acme.test,100.00\n"
                     "Beta Co,b@beta.test,250.50\n")
    TRIMMED_OUTPUT = ("Name,Email,Amount\n"
                      "Acme Ltd,a@acme.test,100.00\n"
                      "Beta Co,b@beta.test,250.50\n")

    def test_trim_then_dedupe_passes_when_the_brief_authorised_trimming(self):
        """The defect, reproduced and then required to pass."""
        outcome = self.check(self.PADDED_SOURCE, self.TRIMMED_OUTPUT,
                             requirements=self.TRIM_AND_DEDUPE)
        self.assertEqual(outcome.result, CheckResult.PASS, outcome.detail)

    def test_the_same_output_fails_when_the_brief_authorised_no_trimming(self):
        """The safeguard: padding removed from a column nobody said to touch."""
        outcome = self.check(
            self.PADDED_SOURCE, self.TRIMMED_OUTPUT,
            requirements=[req("drop_exact_duplicates"),
                          req("preserve_columns",
                              {"columns": ["Name", "Email"]})])
        self.assertEqual(outcome.result, CheckResult.FAIL, outcome.detail)

    def test_trimming_a_column_the_trim_requirement_did_not_name_fails(self):
        """Authorisation is per column, not per job."""
        outcome = self.check(
            self.PADDED_SOURCE, self.TRIMMED_OUTPUT,
            requirements=[req("trim_whitespace", {"columns": ["Email"]}),
                          req("drop_exact_duplicates"),
                          req("preserve_columns",
                              {"columns": ["Name", "Email"]})])
        self.assertEqual(outcome.result, CheckResult.FAIL, outcome.detail)

    def test_a_trim_requirement_naming_no_columns_covers_them_all(self):
        outcome = self.check(
            self.PADDED_SOURCE, self.TRIMMED_OUTPUT,
            requirements=[req("trim_whitespace"), req("drop_exact_duplicates"),
                          req("preserve_columns",
                              {"columns": ["Name", "Email"]})])
        self.assertEqual(outcome.result, CheckResult.PASS, outcome.detail)

    def test_without_a_brief_a_padding_only_difference_is_unverifiable(self):
        """Not PASS, not FAIL: there is no way to know, and both block."""
        outcome = self.check(self.PADDED_SOURCE, self.TRIMMED_OUTPUT)
        self.assertEqual(outcome.result, CheckResult.UNVERIFIABLE,
                         outcome.detail)
        self.assertIn("padding", outcome.detail)

    # --------------------------------------- what must still be caught
    def test_a_dropped_column_is_caught(self):
        outcome = self.check(self.PADDED_SOURCE,
                             "Name,Amount\nAcme Ltd,100.00\nBeta Co,250.50\n",
                             requirements=self.TRIM_AND_DEDUPE)
        self.assertEqual(outcome.result, CheckResult.FAIL)
        self.assertIn("missing", outcome.detail)

    def test_an_unauthorised_row_deletion_is_caught(self):
        outcome = self.check(self.PADDED_SOURCE,
                             "Name,Email,Amount\nAcme Ltd,a@acme.test,100.00\n",
                             requirements=self.TRIM_AND_DEDUPE)
        self.assertEqual(outcome.result, CheckResult.FAIL)
        self.assertIn("lost", outcome.detail)

    def test_a_fabricated_value_is_caught(self):
        outcome = self.check(
            self.PADDED_SOURCE,
            "Name,Email,Amount\nAcme Ltd,a@acme.test,100.00\n"
            "Beta Co,b@beta.test,250.50\nGamma Inc,g@gamma.test,10.00\n",
            requirements=self.TRIM_AND_DEDUPE)
        self.assertEqual(outcome.result, CheckResult.FAIL)
        self.assertIn("appeared", outcome.detail)

    def test_an_unauthorised_value_modification_is_caught(self):
        outcome = self.check(
            self.PADDED_SOURCE,
            "Name,Email,Amount\nAcme Ltd,a@acme.test,100.00\n"
            "Beta Co,b@beta.CHANGED,250.50\n",
            requirements=self.TRIM_AND_DEDUPE)
        self.assertEqual(outcome.result, CheckResult.FAIL)

    def test_a_changed_value_in_the_trimmed_column_is_still_caught(self):
        """Authorising a trim on a column does not stop checking it.

        Mutation testing found this hole: every other "must still fail"
        case above changes a column the brief did not authorise trimming
        for, so a repair that simply stopped checking any trim-authorised
        column passed them all.
        """
        outcome = self.check(
            self.PADDED_SOURCE,
            "Name,Email,Amount\nAcme Ltd,a@acme.test,100.00\n"
            "Gamma Co,b@beta.test,250.50\n",
            requirements=self.TRIM_AND_DEDUPE)
        self.assertEqual(outcome.result, CheckResult.FAIL, outcome.detail)
        self.assertIn("Name", outcome.detail)

    def test_a_dropped_row_in_the_trimmed_column_is_still_caught(self):
        outcome = self.check(
            self.PADDED_SOURCE,
            "Name,Email,Amount\nAcme Ltd,a@acme.test,100.00\n",
            requirements=self.TRIM_AND_DEDUPE)
        self.assertEqual(outcome.result, CheckResult.FAIL, outcome.detail)

    def test_a_case_change_is_not_authorised_by_a_trim_requirement(self):
        """csv-cleanup has no case-normalising operation; this is a change."""
        outcome = self.check(
            self.PADDED_SOURCE,
            "Name,Email,Amount\nACME LTD,a@acme.test,100.00\n"
            "Beta Co,b@beta.test,250.50\n",
            requirements=self.TRIM_AND_DEDUPE)
        self.assertEqual(outcome.result, CheckResult.FAIL, outcome.detail)

    def test_internal_whitespace_is_not_trimming(self):
        """The worker trims the ends; it never rewrites inside a value."""
        outcome = self.check(
            "Name,Email\n Acme  Ltd ,a@acme.test\n",
            "Name,Email\nAcme Ltd,a@acme.test\n",
            requirements=self.TRIM_AND_DEDUPE)
        self.assertEqual(outcome.result, CheckResult.FAIL, outcome.detail)

    # ------------------------------------------------- conflicting briefs
    def test_a_protected_column_that_is_also_rewritten_is_unverifiable(self):
        """"Preserve this" and "rewrite this" cannot both be checked."""
        outcome = self.check(
            "Name,When\nAcme Ltd,01/02/2026\n",
            "Name,When\nAcme Ltd,2026-02-01\n",
            columns=("When",),
            requirements=[req("normalise_dates", {"columns": ["When"]}),
                          req("preserve_columns", {"columns": ["When"]})])
        self.assertEqual(outcome.result, CheckResult.UNVERIFIABLE,
                         outcome.detail)

    # ------------------------------------------------------- shapes of data
    def test_duplicate_rows_that_are_already_identical(self):
        outcome = self.check(
            "Name,Email\nAcme Ltd,a@acme.test\nAcme Ltd,a@acme.test\n",
            "Name,Email\nAcme Ltd,a@acme.test\n",
            requirements=self.TRIM_AND_DEDUPE)
        self.assertEqual(outcome.result, CheckResult.PASS, outcome.detail)

    def test_empty_values_survive(self):
        outcome = self.check(
            "Name,Email\n  Acme Ltd ,\nAcme Ltd,\nBeta Co,b@beta.test\n",
            "Name,Email\nAcme Ltd,\nBeta Co,b@beta.test\n",
            requirements=self.TRIM_AND_DEDUPE)
        self.assertEqual(outcome.result, CheckResult.PASS, outcome.detail)

    def test_a_value_that_is_only_whitespace_becomes_empty(self):
        outcome = self.check(
            "Name,Email\n   ,a@acme.test\n",
            "Name,Email\n,a@acme.test\n",
            requirements=self.TRIM_AND_DEDUPE)
        self.assertEqual(outcome.result, CheckResult.PASS, outcome.detail)

    def test_mixed_types_are_compared_as_text(self):
        outcome = self.check(
            "Name,Email\n 42 ,a@acme.test\n-0.5,b@beta.test\n"
            "2026-01-01,c@c.test\n",
            "Name,Email\n42,a@acme.test\n-0.5,b@beta.test\n"
            "2026-01-01,c@c.test\n",
            requirements=self.TRIM_AND_DEDUPE)
        self.assertEqual(outcome.result, CheckResult.PASS, outcome.detail)

    def test_a_numeric_value_that_changed_is_still_caught(self):
        outcome = self.check(
            "Name,Email\n 42 ,a@acme.test\n",
            "Name,Email\n42.0,a@acme.test\n",
            requirements=self.TRIM_AND_DEDUPE)
        self.assertEqual(outcome.result, CheckResult.FAIL, outcome.detail)

    def test_a_malformed_output_is_unverifiable_not_passed(self):
        src = self.dir / "source.csv"
        out = self.dir / "output.csv"
        src.write_text("Name,Email\nAcme Ltd,a@acme.test\n", encoding="utf-8")
        out.write_bytes(b"\xff\xfe\x00not csv at all")
        outcome = csvverify.check_preserve_columns(
            str(src), str(out),
            {"columns": ["Name"], "requirements": self.TRIM_AND_DEDUPE})
        self.assertNotEqual(outcome.result, CheckResult.PASS)

    def test_a_missing_output_is_unverifiable(self):
        src = self.dir / "source.csv"
        src.write_text("Name,Email\nAcme Ltd,a@acme.test\n", encoding="utf-8")
        outcome = csvverify.check_preserve_columns(
            str(src), str(self.dir / "nope.csv"),
            {"columns": ["Name"], "requirements": self.TRIM_AND_DEDUPE})
        self.assertEqual(outcome.result, CheckResult.UNVERIFIABLE)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
