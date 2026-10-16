"""Sit the sealed reading examination.

See :mod:`tests_solvent.readingexam` for the rules. The exam is sealed
against the modules it examines, so this test's first job is to report
an invalidated seal as an invalidation rather than marking a different
system against a paper set for an older one.
"""

from __future__ import annotations

import json
import pathlib
import unittest

from tests_solvent import readingexam


class TheSeal(unittest.TestCase):
    def test_nothing_under_examination_has_moved(self):
        intact, why = readingexam.seal_is_intact()
        self.assertTrue(intact, why)

    def test_the_seed_comes_from_the_freeze(self):
        """An exam whose seed can vary is an exam that can be re-sat."""
        import hashlib

        self.assertEqual(
            readingexam.SEED,
            int(hashlib.sha256(
                readingexam.FROZEN_AT.encode()).hexdigest()[:8], 16))


class ThePaper(unittest.TestCase):
    def test_it_asks_about_both_outcomes(self):
        """A paper where every answer is "refuse" measures nothing."""
        questions = readingexam.paper()
        readable = [q for q in questions if q.must_be_read]
        damaged = [q for q in questions if not q.must_be_read]
        self.assertGreaterEqual(len(readable), 20)
        self.assertGreaterEqual(len(damaged), 20)

    def test_it_uses_scripts_the_development_tests_do_not(self):
        """Otherwise it is the development suite under another name."""
        corpus = "".join(readingexam.UNSEEN_NAMES)
        for fixture in ("Café", "Münster", "Szabó"):
            self.assertNotIn(fixture, corpus)


class TheSitting(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = readingexam.sit()

    def test_the_paper_was_marked_at_all(self):
        self.assertTrue(self.result["sealed"], self.result.get("why"))

    def test_every_question_is_answered_correctly(self):
        wrong = self.result["wrong"]
        self.assertEqual(
            wrong, [],
            "\n" + json.dumps(wrong, indent=2, ensure_ascii=False))

    def test_the_result_is_recorded_as_evidence(self):
        """The sitting on disk must be this sitting, not an older one."""
        recorded = json.loads(
            pathlib.Path("docs/evidence/reading-exam.json").read_text(
                encoding="utf-8"))
        self.assertEqual(recorded["frozen_at"], readingexam.FROZEN_AT)
        self.assertEqual(recorded["seed"], readingexam.SEED)
        self.assertEqual(recorded["questions"], self.result["questions"])
        self.assertEqual(recorded["correct"], self.result["correct"])


if __name__ == "__main__":
    unittest.main()
