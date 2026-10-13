"""What to build next, answered from evidence rather than appetite.

Two bars, and the second is the one that quietly rules out most of what
sounds attractive: a skill must be deterministically verifiable, and real
opportunities must actually have asked for it.
"""

from __future__ import annotations

import unittest

from solvent import skillcandidates as sc


def posting(title, needs=()):
    return {"title": title, "needs": str(list(needs)), "raw_ref": ""}


class NothingIsBuiltOnOneBar(unittest.TestCase):

    def test_with_no_evidence_nothing_is_built(self):
        verdicts = {a.candidate.name: a.verdict for a in sc.evaluate([])}
        self.assertNotIn(sc.Verdict.BUILD, verdicts.values())

    def test_a_verifiable_candidate_waits_rather_than_being_refused(self):
        """'Not yet' and 'never' are different answers and must read so."""
        assessments = {a.candidate.name: a for a in sc.evaluate([])}
        self.assertIs(assessments["pdf-table-extraction"].verdict,
                      sc.Verdict.AWAIT_DEMAND)

    def test_an_unverifiable_candidate_is_refused_whatever_the_demand(self):
        wanted = [posting("Need a copywriter for blog post work")] * 20
        assessments = {a.candidate.name: a for a in sc.evaluate(wanted)}
        copy = assessments["copywriting"]
        self.assertIs(copy.verdict, sc.Verdict.NOT_CERTIFIABLE)
        self.assertGreaterEqual(copy.demand_seen, 20)
        self.assertIn("recompute", copy.why)

    def test_demand_alone_never_produces_build(self):
        for name in ("web-scraping", "copywriting", "translation",
                     "data-analysis-and-insight"):
            with self.subTest(candidate=name):
                signals = sc.BY_NAME[name].signals
                heavy = [posting(f"We need {signals[0]} work")] * 50
                verdict = {a.candidate.name: a.verdict
                           for a in sc.evaluate(heavy)}[name]
                self.assertIsNot(verdict, sc.Verdict.BUILD)


class TheAnswerChangesWhenDemandArrives(unittest.TestCase):
    """The mechanism must not be a permanent no dressed as a process."""

    def test_three_real_opportunities_flip_it_to_build(self):
        asked = [posting("Extract tables from these PDFs into Excel"),
                 posting("pdf to excel conversion needed"),
                 posting("Need someone to extract table data from a pdf")]
        assessments = {a.candidate.name: a for a in sc.evaluate(asked)}
        pdf = assessments["pdf-table-extraction"]
        self.assertIs(pdf.verdict, sc.Verdict.BUILD)
        self.assertGreaterEqual(pdf.demand_seen, 3)

    def test_two_is_not_enough(self):
        asked = [posting("Extract tables from this pdf"),
                 posting("pdf to excel please")]
        pdf = {a.candidate.name: a for a in sc.evaluate(asked)}[
            "pdf-table-extraction"]
        self.assertIs(pdf.verdict, sc.Verdict.AWAIT_DEMAND)
        self.assertEqual(pdf.demand_seen, 2)

    def test_one_posting_counts_once_however_often_it_says_so(self):
        """A listing repeating itself is an anecdote, not four data points."""
        shouty = [posting("pdf pdf extract table pdf to excel scrape pdf")]
        pdf = {a.candidate.name: a for a in sc.evaluate(shouty)}[
            "pdf-table-extraction"]
        self.assertEqual(pdf.demand_seen, 1)

    def test_the_threshold_is_three_and_says_why(self):
        import pathlib

        self.assertEqual(sc.DEMAND_THRESHOLD, 3)
        source = pathlib.Path("solvent/skillcandidates.py").read_text()
        self.assertIn("anecdote", source,
                      "the threshold is a number with no stated reasoning")


class TheRecommendationIsReadableByAPerson(unittest.TestCase):

    def test_with_nothing_it_says_build_nothing_yet(self):
        line = sc.recommendation([])
        self.assertIn("Build nothing yet", line)
        self.assertIn("no real opportunity has been recorded", line)

    def test_with_demand_it_names_what_to_build(self):
        asked = [posting("validate this dataset against our rules")] * 3
        line = sc.recommendation(asked)
        self.assertIn("data-validation", line)
        self.assertIn("Build next", line)


class EveryCandidateIsJustified(unittest.TestCase):

    def test_each_one_says_why_it_is_or_is_not_deterministic(self):
        for candidate in sc.CANDIDATES:
            with self.subTest(candidate=candidate.name):
                self.assertTrue(candidate.note,
                                "a candidate with no reasoning is a hunch")
                self.assertTrue(candidate.signals)

    def test_the_excluded_ones_are_listed_rather_than_omitted(self):
        """A list of only the plausible candidates hides the reasoning."""
        excluded = [c.name for c in sc.CANDIDATES if not c.certifiable]
        self.assertGreaterEqual(len(excluded), 3)

    def test_no_candidate_duplicates_an_existing_skill(self):
        from solvent.harness import owner_promotions

        existing = set(owner_promotions())
        for candidate in sc.CANDIDATES:
            with self.subTest(candidate=candidate.name):
                self.assertNotIn(candidate.name, existing)


if __name__ == "__main__":
    unittest.main()
