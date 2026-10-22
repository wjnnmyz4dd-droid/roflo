"""The Job Finder, driven through ninety postings and its own edges."""

from __future__ import annotations

import collections
import contextlib
import tempfile
import unittest

from solvent import sourceaccess
from solvent.discovery import Compliance, FixtureSource, Readiness
from solvent.finder import (
    ContractType, ExecutionHistory, Finder, Verdict, classify_contract,
)
from solvent.harness import OWNER, Solvent, owner_promotions, promote_capability
from tests_solvent.findermatrix import (
    CAPABILITIES, build_opportunity, check, scenarios,
)

PLAIN = "board"
MINIMUM_OK = "board-with-minimum"


class FinderFixture(unittest.TestCase):

    def setUp(self):
        self.box = tempfile.TemporaryDirectory()
        self.addCleanup(self.box.cleanup)
        self.s = Solvent(f"{self.box.name}/finder.db")
        self.addCleanup(self._close)
        for name in owner_promotions():
            promote_capability(self.s, name, owner_identity=OWNER,
                               why="test: cleared for the Finder matrix")
        for source in (PLAIN, MINIMUM_OK):
            self.s.discovery.register_source(
                FixtureSource(source, []), owner_identity=OWNER,
                readiness=Readiness.PERMITTED_AUTOMATION,
                compliance=Compliance.PERMITTED, determination="test fixture")
        self.s.discovery.grant(
            MINIMUM_OK, sourceaccess.HOURLY_MINIMUM, owner_identity=OWNER,
            why="test: this platform's terms allow an agreed minimum")
        # A fully configured business. All four commercial terms, because
        # the Finder will not recommend pursuing anything while one is
        # missing -- and a triage matrix run against an unconfigured
        # installation would be testing that gate over and over instead
        # of testing triage.
        self.s.policy.amend(
            {"commercial": {
                "minimum_project_fee_cents": 4_000,
                "market_hourly_cents": {"csv-cleanup": 3_000},
                "market_rate_citations": {
                    "csv-cleanup": "test fixture, not a real citation"},
                "compute_cents_per_cpu_hour": 12,
                "minimum_margin_fraction": 0.05,
            }},
            OWNER, "test: the owner will not take work under $40, which is "
                   "exactly one minimum engagement at their hourly rate")
        self.finder = Finder(self.s.discovery, self.s.policy,
                             self.s.capability, self.s.orchestrator,
                             self.s.store)

    def _close(self):
        try:
            self.s.store.close()
        except Exception:
            pass

    def bare_finder(self) -> Finder:
        """A Finder over an installation the owner has not configured.

        A fresh database rather than an amendment: ``amend`` merges, so
        writing an empty dict over a recorded one leaves the recorded
        one in place and the test would pass for the wrong reason.
        """
        box = tempfile.TemporaryDirectory()
        self.addCleanup(box.cleanup)
        bare = Solvent(f"{box.name}/bare.db")
        self.addCleanup(lambda: self._quietly_close(bare))
        return Finder(bare.discovery, bare.policy, bare.capability,
                      bare.orchestrator, bare.store)

    @staticmethod
    def _quietly_close(solvent):
        try:
            solvent.store.close()
        except Exception:
            pass

    @contextlib.contextmanager
    def withheld(self, capabilities):
        """Make some capabilities undeployable, as uncertified skills are.

        A context manager rather than addCleanup: inside a subTest, cleanups
        do not run until the whole test method ends, so a withheld capability
        would silently leak into every later scenario and turn the rest of
        the matrix into the same failure.
        """
        registry = self.s.capability
        original = registry.may_deploy
        wanted = set(capabilities)

        def gated(name):
            if name in wanted:
                return False, f"{name} is not cleared for deployment"
            return original(name)

        registry.may_deploy = gated
        try:
            yield
        finally:
            registry.may_deploy = original


class NinetyPostings(FinderFixture):
    """The development matrix. One test so a failure reports the whole table."""

    def test_the_matrix(self):
        failures = []
        by_family = collections.Counter()
        for scenario in scenarios():
            with self.subTest(scenario=scenario.name):
                source = MINIMUM_OK if scenario.grant else PLAIN
                history = ExecutionHistory(dict(scenario.history))
                opportunity = build_opportunity(scenario, source=source)
                with self.withheld(scenario.withhold):
                    assessment = self.finder.assess(
                        opportunity, history=history, extension="xlsx")
                outcome = check(scenario, assessment)
                by_family[scenario.expect_verdict.value] += 1
                if not outcome.passed:
                    failures.append(f"{scenario.name}: {outcome.detail}")

        self.assertEqual(
            sum(by_family.values()), 90,
            "the matrix must run all ninety scenarios")
        self.assertEqual(
            failures, [],
            f"{len(failures)} of 90 scenarios disagree with the operating "
            f"rules:\n" + "\n".join(failures))


class ReadingTheContractType(unittest.TestCase):

    def one(self, body="", structure="", title="Some work"):
        from solvent.content import quarantine
        from solvent.discovery import Opportunity
        from solvent.types import PrivacyClass

        return Opportunity(
            id="o", source="s", external_ref="r", title=title,
            quoted_cents=10_000, payment_structure=structure,
            posting=quarantine(body, source="s", privacy=PrivacyClass.PUBLIC))

    def test_a_posting_naming_both_is_not_guessed(self):
        both = self.one("$40/hr, or a fixed price for the whole project")
        self.assertIs(classify_contract(both), ContractType.UNKNOWN)

    def test_a_structured_field_outranks_the_prose(self):
        mixed = self.one("paid hourly", structure="FIXED")
        self.assertIs(classify_contract(mixed), ContractType.FIXED_PRICE)

    def test_silence_is_unknown_not_fixed(self):
        self.assertIs(classify_contract(self.one("please help")),
                      ContractType.UNKNOWN)


class TheMinimumNeedsAllThreeConditions(FinderFixture):

    def posting(self, source=PLAIN, **over):
        from tests_solvent.findermatrix import Scenario, _posting

        scenario = Scenario(
            "ad-hoc", _posting("x", payment_structure="HOURLY", **over),
            Verdict.PURSUE, ContractType.HOURLY)
        return build_opportunity(scenario, source=source)

    def test_the_owner_configuring_it_is_not_enough(self):
        allowed, why = self.finder.minimum_hours_applicable(
            self.posting(structured_terms=True))
        self.assertFalse(allowed)
        self.assertIn("does not permit", why)

    def test_the_platform_permitting_it_is_not_enough(self):
        allowed, why = self.finder.minimum_hours_applicable(
            self.posting(source=MINIMUM_OK))
        self.assertFalse(allowed)
        self.assertIn("unconfirmed", why)

    def test_with_all_three_it_applies(self):
        allowed, why = self.finder.minimum_hours_applicable(
            self.posting(source=MINIMUM_OK, structured_terms=True))
        self.assertTrue(allowed, why)

    def test_an_unconfigured_minimum_is_never_applied(self):
        self.s.policy.amend({"commercial": {"minimum_hours": 0}}, OWNER,
                            "test: the owner withdraws the minimum")
        allowed, why = self.finder.minimum_hours_applicable(
            self.posting(source=MINIMUM_OK, structured_terms=True))
        self.assertFalse(allowed)
        self.assertIn("not configured", why)


class ItWillNotInventTheNumbersItLacks(FinderFixture):

    def hourly(self, **over):
        from tests_solvent.findermatrix import Scenario, _posting

        return build_opportunity(
            Scenario("h", _posting("h", payment_structure="HOURLY", **over),
                     Verdict.HOLD, ContractType.HOURLY),
            source=PLAIN)

    def test_no_history_means_unknown_not_zero_and_not_an_average(self):
        econ = self.finder.economics(self.hourly(), history=ExecutionHistory())
        self.assertIsNone(econ.revenue_cents)
        self.assertIsNone(econ.profit_cents)
        self.assertIn("no execution has been observed", econ.basis)

    def test_no_configured_rate_means_unknown(self):
        self.s.policy.amend({"commercial": {"hourly_rate_cents": 0}}, OWNER,
                            "test: the owner has no hourly rate")
        econ = self.finder.economics(
            self.hourly(), history=ExecutionHistory({"csv-cleanup": [20.0]}))
        self.assertIsNone(econ.revenue_cents)
        self.assertIn("inventing one", econ.basis)

    def test_history_comes_from_what_actually_ran(self):
        """Not a fixture: the measurements a real cycle wrote."""
        rows = self.s.store.raw_readonly(
            "SELECT * FROM execution_measurements")
        self.assertEqual(rows, [], "no job has run in this fixture yet")
        self.s.orchestrator.record_execution(
            "job_x", capability="csv-cleanup", wall_seconds=42.0,
            outcome="DELIVERED")
        history = ExecutionHistory.from_store(self.s.store)
        self.assertEqual(history.expected_seconds("csv-cleanup"), 42.0)
        self.assertIsNone(history.expected_seconds("report-builder"))

    def test_the_median_resists_one_pathological_run(self):
        history = ExecutionHistory({"csv-cleanup": [20.0, 21.0, 19.0, 9000.0]})
        self.assertLess(history.expected_seconds("csv-cleanup"), 100)


class RankingIsExplainable(FinderFixture):

    def postings(self, values):
        from tests_solvent.findermatrix import Scenario, _posting

        return [build_opportunity(
            Scenario(f"p{v}", _posting(f"p{v}", quoted_cents=v),
                     Verdict.PURSUE, ContractType.FIXED_PRICE), source=PLAIN)
            for v in values]

    def test_the_most_profitable_job_comes_first(self):
        ranked = self.finder.rank(self.postings([20_000, 90_000, 40_000]),
                                  extension="xlsx")
        self.assertEqual([a.economics.revenue_cents for a in ranked],
                         [90_000, 40_000, 20_000])

    def test_nothing_unworth_doing_outranks_something_worth_doing(self):
        ranked = self.finder.rank(self.postings([1_000, 60_000]),
                                  extension="xlsx")
        self.assertIs(ranked[0].verdict, Verdict.PURSUE)
        self.assertEqual(ranked[-1].score, 0.0)

    def test_next_to_qualify_returns_nothing_when_nothing_qualifies(self):
        self.assertIsNone(
            self.finder.next_to_qualify(self.postings([100, 200]), extension="xlsx"))

    def test_a_less_certain_posting_ranks_below_an_equal_certain_one(self):
        from tests_solvent.findermatrix import Scenario, _posting

        pair = [build_opportunity(Scenario(
            f"c{c}", _posting(f"c{c}", quoted_cents=60_000, confidence=c),
            Verdict.PURSUE, ContractType.FIXED_PRICE), source=PLAIN)
            for c in (0.4, 1.0)]
        ranked = self.finder.rank(pair, extension="xlsx")
        self.assertEqual(ranked[0].economics.revenue_cents, 60_000)
        self.assertGreater(ranked[0].score, ranked[1].score)


class OneFinderServesEveryCapability(FinderFixture):
    """RULE 1, checked rather than asserted in a comment."""

    def test_only_one_class_polls_and_only_one_ranks(self):
        """RULE 1 is about authorities, not about how many sources exist.

        Several WorkSource adapters plugged into the one Discovery is the
        intended shape -- each is a place to look, not a second agent doing
        the looking. What the rule forbids is a second thing that *polls*,
        or a second thing that decides what to do next.
        """
        import pathlib
        import re

        polls, ranks = [], []
        for path in sorted(pathlib.Path("solvent").glob("*.py")):
            text = path.read_text(encoding="utf-8")
            for match in re.finditer(
                    r"^class (\w+)[^\n]*:\n(?:(?!^class ).*\n)*", text,
                    re.MULTILINE):
                body = match.group(0)
                if re.search(r"^    def poll\b", body, re.MULTILINE):
                    polls.append(f"{path.name}:{match.group(1)}")
                # "def rank(self, ..." -- a method that ranks something.
                # Not "@property def rank(self)", which several enums use
                # to order their own members and which ranks no work.
                if re.search(r"^    def (rank|next_to_qualify)\(self, ", body,
                             re.MULTILINE):
                    ranks.append(f"{path.name}:{match.group(1)}")

        self.assertEqual(polls, ["discovery.py:Discovery"],
                         f"more than one thing polls sources: {polls}")
        # Two rankers, at two stages, and the division is the point.
        # Qualification orders *Decisions* on the Governor's economics and is
        # the authority on what to do next. The Finder orders *Opportunities*
        # for triage, before any of that exists. A third would be a duplicate
        # authority; so would the Finder calling its output a job queue.
        self.assertEqual(
            ranks, ["finder.py:Finder", "qualification.py:Qualification"],
            f"an unexpected thing ranks work: {ranks}")
        self.assertFalse(
            hasattr(self.finder, "next_job"),
            "the Finder must not present a work queue; qualification, the "
            "Governor and the owner choose the job")

    def test_every_cleared_capability_is_rankable(self):
        from tests_solvent.findermatrix import Scenario, _posting

        for capability in CAPABILITIES:
            with self.subTest(capability=capability):
                opportunity = build_opportunity(Scenario(
                    capability, _posting("x", needs=[capability],
                                         quoted_cents=60_000),
                    Verdict.PURSUE, ContractType.FIXED_PRICE), source=PLAIN)
                assessment = self.finder.assess(opportunity, extension="xlsx")
                self.assertIs(assessment.verdict, Verdict.PURSUE,
                              f"{capability}: {assessment.why}")


if __name__ == "__main__":
    unittest.main()


class TermsOnlyTheOwnerCanSet(FinderFixture):
    """Gaps that silently change every assessment, reported once not per job."""

    def test_a_floor_no_hourly_job_can_clear_is_reported(self):
        self.s.policy.amend(
            {"commercial": {"minimum_project_fee_cents": 20_000}}, OWNER,
            "test: the owner will not take work under $200")
        gaps = self.finder.configuration_gaps()
        conflict = [g for g in gaps if "can ever be accepted" in g]
        self.assertTrue(
            conflict,
            "a $200 floor against a $40 minimum engagement rejects every "
            "hourly job, and nothing said so")
        self.assertIn("40.00", conflict[0])

    def test_a_coherent_configuration_reports_no_conflict(self):
        gaps = self.finder.configuration_gaps()
        self.assertEqual([g for g in gaps if "can ever be accepted" in g], [])

    def test_the_uncosted_and_unset_terms_are_named(self):
        """On an installation where they really are unset.

        The shared fixture configures all four now, because the Finder
        will not recommend anything while one is missing. This one
        clears them again: the gap list has to name what is missing, and
        a fixture with nothing missing cannot show that.
        """
        gaps = " | ".join(self.bare_finder().configuration_gaps())
        self.assertIn("compute is costed at zero", gaps)
        self.assertIn("no cited market rates", gaps)
        self.assertIn("no minimum margin", gaps)

    def test_an_unset_floor_is_a_gap_not_a_default(self):
        self.s.policy.amend(
            {"commercial": {"minimum_project_fee_cents": 0}}, OWNER,
            "test: the owner clears the floor")
        gaps = " | ".join(self.finder.configuration_gaps())
        self.assertIn("no minimum project fee is set", gaps)
