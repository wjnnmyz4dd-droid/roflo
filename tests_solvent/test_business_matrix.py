"""The ninety, run through both channels.

Outbound and inbound are tested separately and then at their handoff,
because the whole point of the design is that they converge: two ways of
learning about work, one way of doing it.
"""

from __future__ import annotations

import collections
import tempfile
import unittest

from tests_solvent.fixtures import give_an_issuer, set_commercial_terms
from solvent import sourceaccess as access
from solvent import storefront as sf
from solvent.content import quarantine
from solvent.discovery import (
    Compliance, FixtureSource, ManualSource, Opportunity, Readiness,
)
from solvent.finder import Finder
from solvent.harness import OWNER, Solvent, owner_promotions, promote_capability
from solvent.types import PrivacyClass
from tests_solvent.businessmatrix import (
    ADVERSARIAL, CHANNELS, INBOUND, OUTBOUND, SKILLS, check, scenarios,
)


class Board(FixtureSource):
    is_fixture = False
    kind = "marketplace"
    work_type = access.CLIENT_SERVICES


class Matrix(unittest.TestCase):

    def setUp(self):
        self.box = tempfile.TemporaryDirectory()
        self.addCleanup(self.box.cleanup)
        self.s = Solvent(f"{self.box.name}/matrix.db")
        give_an_issuer(self.s)
        set_commercial_terms(self.s)
        self.addCleanup(self.close)
        for name in owner_promotions():
            promote_capability(self.s, name, owner_identity=OWNER,
                               why="test: cleared for the business matrix")
        self.shop = sf.Storefront(self.s.capability, self.s.policy)
        for source in (Board("outbound_board", []), self.shop.source):
            self.s.discovery.register_source(
                source, owner_identity=OWNER,
                readiness=Readiness.PERMITTED_AUTOMATION,
                compliance=Compliance.PERMITTED, determination="matrix")
        self.s.policy.amend({
            "commercial": {"minimum_project_fee_cents": 4_000},
        }, OWNER, "test: the owner's floor")
        self.finder = Finder(self.s.discovery, self.s.policy,
                             self.s.capability, self.s.orchestrator,
                             self.s.store)

    def close(self):
        try:
            self.s.store.close()
        except Exception:
            pass

    def opportunity(self, scenario):
        """One posting, on whichever channel the scenario names."""
        source = ("storefront" if scenario.channel == INBOUND
                  else "outbound_board")
        return Opportunity(
            id=f"opp_{scenario.name}", source=source,
            external_ref=scenario.name,
            title=f"{scenario.skill} work",
            quoted_cents=scenario.cents,
            needs=(scenario.skill,),
            client_ref="client:matrix",
            posting=quarantine(scenario.posting_body, source=source,
                               privacy=PrivacyClass.CLIENT_CONFIDENTIAL),
            # The inbound channel's terms are structured: the customer
            # answered a form with named fields rather than writing prose.
            structured_terms=(scenario.channel == INBOUND),
            payment_structure="FIXED",
            risk_indicators=scenario.risk_indicators)


class TheNinety(Matrix):

    def test_the_matrix(self):
        failures = []
        counts = collections.Counter()
        for scenario in scenarios():
            with self.subTest(scenario=scenario.name):
                assessment = self.finder.assess(
                    self.opportunity(scenario), extension="xlsx")
                problems = check(scenario, assessment)
                counts[f"{scenario.channel}:{assessment.verdict.value}"] += 1
                if problems:
                    failures.append(f"{scenario.name}: " + "; ".join(problems))

        self.assertEqual(sum(counts.values()), 90,
                         "the matrix must run all ninety scenarios")
        self.assertEqual(
            failures, [],
            f"{len(failures)} of 90 scenarios disagree with the operating "
            "rules:\n" + "\n".join(failures[:12]))

    def test_both_channels_reach_the_same_verdicts(self):
        """The handoff: the channel must not change the answer.

        A posting is the same piece of work whether the customer found
        DeskPilot or DeskPilot found the customer. If the two channels
        disagreed, one of them would be taking work the other refused.
        """
        by_channel = {OUTBOUND: {}, INBOUND: {}}
        for scenario in scenarios():
            assessment = self.finder.assess(
                self.opportunity(scenario), extension="xlsx")
            key = (scenario.skill, scenario.value_label, scenario.difficulty)
            by_channel[scenario.channel][key] = assessment.verdict.value

        disagreements = [
            f"{key}: outbound={by_channel[OUTBOUND][key]} "
            f"inbound={by_channel[INBOUND][key]}"
            for key in by_channel[OUTBOUND]
            if by_channel[OUTBOUND][key] != by_channel[INBOUND][key]]
        self.assertEqual(disagreements, [],
                         "the acquisition channel changed the verdict")


class WhatTheMatrixMeasures(Matrix):

    def assess(self, scenario):
        return self.finder.assess(self.opportunity(scenario),
                                  extension="xlsx")

    def by(self, **criteria):
        for scenario in scenarios():
            if all(getattr(scenario, k) == v for k, v in criteria.items()):
                yield scenario

    def test_a_ten_thousand_dollar_job_is_held_not_grabbed(self):
        """The one most likely to be got wrong by wanting the money."""
        for scenario in self.by(value_label="10000", difficulty="easy"):
            with self.subTest(scenario=scenario.name):
                assessment = self.assess(scenario)
                self.assertEqual(assessment.verdict.value, "HOLD")
                self.assertEqual(assessment.required_tier.value,
                                 "T3_EXTERNAL_FACT")
                self.assertIsNone(assessment.tier_reachable,
                                  "T3 reachability must read as unknown, "
                                  "not as a confident no")

    def test_the_smallest_prescribed_job_clears_the_floor_by_ten_dollars(self):
        """$50 against a $40 floor. Worth knowing how little room that is:
        a floor set anywhere above $50 turns a third of this matrix into
        declines, and the owner has not set one above $40."""
        for scenario in self.by(value_label="50", difficulty="easy"):
            with self.subTest(scenario=scenario.name):
                self.assertEqual(self.assess(scenario).verdict.value,
                                 "PURSUE")

    def test_raising_the_floor_above_it_does_decline_it(self):
        """The floor is doing work, not decoration."""
        self.s.policy.amend(
            {"commercial": {"minimum_project_fee_cents": 7_500}}, OWNER,
            "test: the owner will not take work under $75")
        for scenario in self.by(value_label="50", difficulty="easy"):
            with self.subTest(scenario=scenario.name):
                assessment = self.assess(scenario)
                self.assertEqual(assessment.verdict.value, "DECLINE")
                self.assertIn("below the", assessment.why)

    def test_every_adversarial_posting_reaches_a_person(self):
        for scenario in self.by(difficulty=ADVERSARIAL):
            with self.subTest(scenario=scenario.name):
                assessment = self.assess(scenario)
                self.assertEqual(assessment.verdict.value, "HOLD")
                self.assertTrue(assessment.risks)

    def test_a_high_value_adversarial_posting_is_still_held(self):
        """Money is not a reason to overlook a fraud indicator."""
        for scenario in self.by(difficulty=ADVERSARIAL, value_label="10000"):
            with self.subTest(scenario=scenario.name):
                self.assertEqual(self.assess(scenario).verdict.value, "HOLD")

    def test_every_skill_is_exercised_at_every_value(self):
        seen = {(s.skill, s.value_label) for s in scenarios()}
        self.assertEqual(len(seen), len(SKILLS) * 3)

    def test_nothing_in_the_matrix_takes_an_external_action(self):
        """The Finder reads. Ninety assessments must change nothing."""
        before = len(self.s.store.raw_readonly("SELECT * FROM jobs"))
        for scenario in scenarios():
            self.assess(scenario)
        after = len(self.s.store.raw_readonly("SELECT * FROM jobs"))
        self.assertEqual(before, after, "assessing created a job")
        self.assertEqual(
            self.s.store.raw_readonly("SELECT * FROM budget_grants"), [],
            "assessing committed money")

    def test_no_duplicate_execution_across_channels(self):
        """The same work offered on both channels is still one job."""
        from solvent.errors import FailClosed

        self.shop.place(
            order_id="dup", offer_id="csv-cleanup", client_ref="client:acme",
            answers={"source_file": "f.csv", "rules": "dedupe"},
            offered_cents=15_000)
        self.shop.submit_to_pipeline("dup")
        with self.assertRaises(FailClosed):
            self.shop.submit_to_pipeline("dup")


if __name__ == "__main__":
    unittest.main()
