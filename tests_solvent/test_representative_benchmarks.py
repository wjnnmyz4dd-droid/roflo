"""Labels that cannot quietly become more impressive than the evidence.

The owner objected, correctly, that millisecond figures from a
few-hundred-byte generated file are not evidence that DeskPilot can
finish a client's job in a millisecond. The fix is not better numbers
-- it is numbers that say which kind they are, and a label that
cannot be applied without the thing it claims.

So these tests are mostly about the labelling. A REPRESENTATIVE
WORKLOAD label may only appear where the input was actually scaled,
and a REAL CLIENT JOB label may not appear at all, because there have
been none.
"""

from __future__ import annotations

import json
import pathlib
import unittest

from tools import representative_benchmarks as bench

EVIDENCE = (pathlib.Path(__file__).resolve().parent.parent
            / "docs" / "evidence" / "representative-benchmarks.json")


class TheLabelsMeanWhatTheySay(unittest.TestCase):

    def test_the_three_labels_are_distinct(self):
        self.assertEqual(
            len({bench.SYNTHETIC, bench.REPRESENTATIVE, bench.REAL}), 3)

    def test_a_single_part_input_is_never_representative(self):
        """One generator case is the smallest thing it makes."""
        measured = bench.measure_sizes(skills=("csv-cleanup",), repeats=1)
        self.assertEqual(measured["csv-cleanup"]["small"]["label"],
                         bench.SYNTHETIC)

    def test_a_scaled_input_is_representative(self):
        measured = bench.measure_sizes(skills=("csv-cleanup",), repeats=1)
        large = measured["csv-cleanup"]["large"]
        self.assertEqual(large["label"], bench.REPRESENTATIVE)
        self.assertTrue(large["scaled"])
        self.assertGreater(large["median_source_bytes"],
                           measured["csv-cleanup"]["small"][
                               "median_source_bytes"] * 5)

    def test_a_format_that_cannot_be_concatenated_stays_synthetic(self):
        """A .xlsx is a zip. Appending one to another corrupts it.

        The honest result is a synthetic label at every size, not a
        representative label over an input that was never grown.
        """
        measured = bench.measure_sizes(
            skills=("spreadsheet-automation",), repeats=1)
        for size, row in measured["spreadsheet-automation"].items():
            with self.subTest(size=size):
                self.assertEqual(row["label"], bench.SYNTHETIC)

    def test_larger_inputs_are_built_from_distinct_cases(self):
        """Not by repeating rows.

        For csv-cleanup the duplicate rows are the defect under
        correction, so copying them would change what the right
        answer is and the benchmark would measure the wrong job.
        """
        _, small, _ = bench._composite("csv-cleanup", 1, 0)
        _, large, _ = bench._composite("csv-cleanup", 8, 0)
        self.assertGreater(len(large), len(small) * 4)
        lines = [line for line in large.splitlines() if line.strip()]
        self.assertGreater(len(set(lines)) / len(lines), 0.5,
                           "most rows are duplicates, so the larger input "
                           "was built by copying rather than generating")


class TheRecordedEvidenceIsHonest(unittest.TestCase):

    def setUp(self):
        if not EVIDENCE.exists():
            self.skipTest("no recorded benchmark evidence")
        self.data = json.loads(EVIDENCE.read_text(encoding="utf-8"))

    def test_no_real_client_job_is_claimed(self):
        self.assertEqual(self.data["real_client_jobs"], 0)

    def test_no_row_is_labelled_a_real_client_job(self):
        for capability, sizes in self.data["sizes"].items():
            for size, row in sizes.items():
                with self.subTest(capability=capability, size=size):
                    self.assertNotEqual(row.get("label"), bench.REAL)

    def test_the_note_says_the_column_is_empty_by_fact(self):
        self.assertIn("by fact rather than by omission", self.data["note"])


class WhatTheNumbersActuallyShow(unittest.TestCase):
    """Two findings worth keeping as assertions rather than prose."""

    def setUp(self):
        if not EVIDENCE.exists():
            self.skipTest("no recorded benchmark evidence")
        self.sizes = json.loads(
            EVIDENCE.read_text(encoding="utf-8"))["sizes"]

    def test_a_real_sized_job_is_not_a_millisecond(self):
        """The owner's objection, confirmed by measurement."""
        complex_run = self.sizes["csv-cleanup"]["complex"]
        total = (complex_run["execution"]["p50_ms"]
                 + complex_run["verification"]["p50_ms"])
        self.assertGreater(total, 100,
                           "a 90KB job still finishes in under 100ms, which "
                           "would make the original objection unfounded")

    def test_verification_dominates_and_scales_worse_than_execution(self):
        """data-entry is where a client-sized file would hurt.

        553 bytes verifies in about 15ms and 69KB in about 2.7
        seconds: 126 times the input for roughly 180 times the
        verification. Execution over the same range grows far less.
        This is the number that should drive a delivery promise, and
        the reason a deadline must never be quoted from median
        execution alone.
        """
        small = self.sizes["data-entry"]["small"]
        big = self.sizes["data-entry"]["complex"]
        if not big.get("verification", {}).get("n"):
            self.skipTest("the complex case did not run")
        verify_growth = (big["verification"]["p50_ms"]
                         / max(small["verification"]["p50_ms"], 0.01))
        exec_growth = (big["execution"]["p50_ms"]
                       / max(small["execution"]["p50_ms"], 0.01))
        self.assertGreater(verify_growth, exec_growth,
                           "verification no longer grows faster than "
                           "execution, so the bottleneck has moved and the "
                           "deadline model should be re-derived")
        self.assertGreater(big["verification"]["p50_ms"],
                           big["execution"]["p50_ms"],
                           "verification is no longer the dominant cost")


if __name__ == "__main__":
    unittest.main()
