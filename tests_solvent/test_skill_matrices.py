"""The 45 development scenarios and the 45-scenario final exam.

Both run a complete job through the whole business pipeline and grade the
file that came out against the file that was correct for the input *before
the worker ran*. See :mod:`tests_solvent.skillmatrix` for the method.

The exam's only claim is the one that matters: its seed appears nowhere else
in this repository, and the scenarios were generated after the implementation
was frozen. Nothing was tuned against them.
"""

from __future__ import annotations

import unittest

from solvent import certgen
from tests_solvent import skillmatrix


class ADevelopmentMatrix(unittest.TestCase):
    """45 scenarios from the seed the library was built against."""

    @classmethod
    def setUpClass(cls):
        cls.report = skillmatrix.run_matrix("development",
                                            certgen.DEVELOPMENT_SEED)

    def test_there_are_forty_five(self):
        self.assertEqual(len(self.report.results), 45)

    def test_nine_per_capability(self):
        from collections import Counter

        counts = Counter(r.capability for r in self.report.results)
        self.assertEqual(set(counts.values()), {9}, dict(counts))

    def test_every_scenario_passes(self):
        failures = [r.line() for r in self.report.failed]
        self.assertEqual(failures, [], "\n" + "\n".join(failures))

    def test_some_scenarios_expect_a_question_rather_than_a_file(self):
        """Otherwise the matrix is only testing the easy direction.

        A source with a date like 05/10/2026 has no single right answer, and
        a system that delivered one would be guessing. Those scenarios pass by
        asking.
        """
        asked = [r for r in self.report.results
                 if r.expected == skillmatrix.ASK]
        self.assertTrue(asked, "no scenario tests refusing to guess")
        for result in asked:
            self.assertIn("asked the client", result.detail)


class AnUnseenFinalExam(unittest.TestCase):
    """45 scenarios from a seed used nowhere else, after the freeze."""

    @classmethod
    def setUpClass(cls):
        cls.report = skillmatrix.run_matrix("final exam", skillmatrix.EXAM_SEED)

    def test_the_exam_seed_is_not_one_the_system_has_seen(self):
        self.assertNotIn(skillmatrix.EXAM_SEED,
                         {certgen.DEVELOPMENT_SEED, certgen.CERTIFICATION_SEED,
                          certgen.SURPRISE_SEED})

    def test_there_are_forty_five(self):
        self.assertEqual(len(self.report.results), 45)

    def test_every_scenario_passes(self):
        failures = [r.line() for r in self.report.failed]
        self.assertEqual(failures, [], "\n" + "\n".join(failures))

    def test_the_exam_is_not_the_development_matrix_under_another_name(self):
        """Different seeds must produce different work, or the exam is a
        re-run of the revision."""
        development = skillmatrix.run_scenario("csv-cleanup",
                                               certgen.DEVELOPMENT_SEED, 1)
        exam = skillmatrix.run_scenario("csv-cleanup", skillmatrix.EXAM_SEED, 1)
        self.assertNotEqual(development.source_bytes, exam.source_bytes,
                            "two seeds produced the same source file")


if __name__ == "__main__":
    unittest.main()
