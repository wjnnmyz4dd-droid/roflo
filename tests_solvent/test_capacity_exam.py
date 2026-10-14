"""The sealed exam, sat as part of the suite.

The exam itself lives in capacityexam.py, which carries the frozen
digests, the frozen criteria and the first-run record. This file sits
it and checks three things: that the seal is intact, that the paper is
genuinely unseen, and that the marks still match the first sitting.
"""

from __future__ import annotations

import time
import unittest

from tests_solvent import capacityexam as exam
from tools.loadsim_solvent import BUDGET as DEV_BUDGET
from tools.loadsim_solvent import LEVELS as DEV_LEVELS


class TheSealHolds(unittest.TestCase):

    def test_nothing_under_examination_has_moved(self):
        intact, why = exam.seal_is_intact()
        self.assertTrue(intact, why)

    def test_the_seal_covers_the_code_the_exam_exercises(self):
        for module in ("solvent/capacity.py", "solvent/finder.py",
                       "solvent/store.py", "solvent/revenuemodel.py"):
            with self.subTest(module=module):
                self.assertIn(module, exam.DIGESTS)

    def test_the_seal_would_notice_a_change(self):
        """Absences prove nothing unless the check can fail."""
        real = dict(exam.DIGESTS)
        try:
            exam.DIGESTS["solvent/capacity.py"] = "0" * 64
            intact, why = exam.seal_is_intact()
            self.assertFalse(intact)
            self.assertIn("capacity.py", why)
        finally:
            exam.DIGESTS.clear()
            exam.DIGESTS.update(real)

    def test_the_seed_does_not_come_from_the_environment(self):
        """An exam whose seed is a variable is an exam that can be re-sat."""
        from tests_solvent.fixtures import source_names

        names = source_names("tests_solvent/capacityexam.py")
        for forbidden in ("environ", "getenv", "argv", "time"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, names)


class ThePaperIsUnseen(unittest.TestCase):

    def test_no_question_reuses_a_development_workload_level(self):
        for question in exam.paper():
            with self.subTest(level=question["level"]):
                self.assertNotIn(question["level"], DEV_LEVELS)

    def test_no_question_reuses_the_development_budget(self):
        for question in exam.paper():
            with self.subTest(level=question["level"]):
                self.assertNotEqual(question["budget"], DEV_BUDGET)
                self.assertNotEqual(
                    question["budget"]["max_owed_seconds"],
                    DEV_BUDGET["max_owed_seconds"])

    def test_every_question_combines_stress_conditions(self):
        """Development tested them one at a time."""
        for question in exam.paper():
            with self.subTest(level=question["level"]):
                self.assertEqual(len(question["stress"]), 2)

    def test_the_paper_is_the_same_paper_every_time(self):
        self.assertEqual(exam.paper(), exam.paper())


class TheMarksStillHold(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        started = time.monotonic()
        cls.result = exam.sit()
        cls.seconds = time.monotonic() - started

    def test_every_question_passes(self):
        self.assertEqual(self.result["failed"], 0,
                         str(self.result["failures"]))
        self.assertEqual(self.result["passed"], self.result["questions"])

    def test_the_criteria_are_the_ones_that_were_frozen(self):
        self.assertEqual(tuple(self.result["criteria"]), exam.CRITERIA)
        self.assertIn("deadline_misses", exam.CRITERIA)
        self.assertIn("duplicate_commitments", exam.CRITERIA)

    def test_the_first_run_record_is_preserved_not_refreshed(self):
        self.assertIn("Six questions, six passed", exam.FIRST_RUN)
        self.assertIn(exam.FROZEN_AT, exam.FIRST_RUN)

    def test_the_marks_match_the_first_sitting(self):
        for question in self.result["results"]:
            with self.subTest(level=question["level"]):
                self.assertIn(f"level {question['level']:>3}",
                              exam.FIRST_RUN)
                self.assertIn(f"{question['delivered']:>3} delivered",
                              exam.FIRST_RUN)


if __name__ == "__main__":
    unittest.main()
