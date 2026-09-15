"""Qualification: say no well, rank what is left, and own no economics."""

import unittest

from solvent.discovery import Compliance, FixtureSource, OpportunityStatus, Readiness
from solvent.harness import Solvent
from solvent.capability import Capability
from solvent.qualification import RejectReason, Verdict
from solvent.types import (
    CostCategory, GovernorVerdict, JobState, Jurisdiction, OperatingMode, PricingTier,
    money,
)

OWNER = "owner:demo"


def rig(board, *, max_concurrent=2, minimum="100", approval_above="2000",
        max_job_spend="600", capabilities=("spreadsheet",)):
    s = Solvent()
    for state, rate in (("CA", 9_500), ("CT", 7_800), ("NC", 5_200)):
        s.pricing.ingest(
            category=CostCategory.ON_SITE_LABOR, jurisdiction=Jurisdiction(state=state),
            tier=PricingTier.MARKET_DATA, unit_cents=rate, unit="hour",
            source="FIXTURE", effective_date="2026-06-01", retrieved_date="2026-09-01",
            confidence=0.9, owner_identity=OWNER, is_fixture=True)
    for name in capabilities:
        s.capability.register(Capability(name=name, covers=frozenset({name}),
                                         proven=True), owner_identity=OWNER)
    source = FixtureSource("board", board)
    s.discovery.register_source(
        source, owner_identity=OWNER, readiness=Readiness.PERMITTED_AUTOMATION,
        compliance=Compliance.PERMITTED, determination="fixture, no external call")
    s.policy.amend({
        "discovery": {"approved_sources": ["board"]},
        "qualification": {"minimum_value_cents": money(minimum),
                          "max_concurrent_jobs": max_concurrent,
                          "default_hours": 2.0},
        "financial": {"max_job_spend_cents": money(max_job_spend),
                      "max_committed_unverified_cents": money("5000"),
                      "owner_approval_above_cents": money(approval_above)},
    }, OWNER, "test")
    return s


def posting(ref, dollars, needs=("spreadsheet",), state="CA", body="do the work",
            client="client_x"):
    return {"ref": ref, "title": f"job {ref}", "quoted_cents": money(dollars),
            "needs": list(needs), "project_state": state, "client_ref": client,
            "body": body}


def run(s):
    candidates = s.discovery.poll("board")
    survivors, cheap = s.qualification.triage(candidates)
    decisions = [s.qualification.qualify(c) for c in survivors]
    return cheap, decisions


class SolventSaysNo(unittest.TestCase):
    """Optimise for good jobs, not for the number of jobs."""

    def test_an_unprofitable_job_is_rejected(self):
        s = rig([posting("thin", "300")])
        _, decisions = run(s)
        self.assertIs(decisions[0].verdict, Verdict.REJECT)
        self.assertIs(decisions[0].reject_reason, RejectReason.MARGIN_TOO_LOW)

    def test_an_unsupported_job_is_rejected(self):
        s = rig([posting("video", "4000", needs=("video_editing",))])
        _, decisions = run(s)
        self.assertIs(decisions[0].verdict, Verdict.REJECT)
        self.assertIs(decisions[0].reject_reason, RejectReason.MISSING_CAPABILITY)

    def test_a_job_below_the_minimum_is_triaged_out_cheaply(self):
        """Rejecting obvious non-starters must not cost a full estimate."""
        s = rig([posting("tiny", "5")])
        cheap, decisions = run(s)
        self.assertEqual(decisions, [])
        self.assertIs(cheap[0].reject_reason, RejectReason.BELOW_MINIMUM_VALUE)
        self.assertEqual(s.store.raw_readonly("SELECT * FROM price_estimates"), [],
                         "triage must not run an estimate")

    def test_a_job_with_no_stated_capability_is_triaged_out(self):
        s = rig([posting("vague", "900", needs=())])
        cheap, _ = run(s)
        self.assertIs(cheap[0].reject_reason, RejectReason.INSUFFICIENT_INFORMATION)

    def test_a_job_with_no_location_cannot_get_a_binding_quote(self):
        s = rig([{"ref": "nowhere", "title": "job", "quoted_cents": money("900"),
                  "needs": ["spreadsheet"], "client_ref": "c", "body": "x"}])
        _, decisions = run(s)
        self.assertIs(decisions[0].verdict, Verdict.NEEDS_INFORMATION)
        self.assertIs(decisions[0].governor_verdict,
                      GovernorVerdict.PRICING_LOCATION_UNKNOWN)

    def test_a_job_from_an_unpermitted_source_is_refused(self):
        s = rig([posting("ok", "900")])
        s.discovery.suspend_source("board", "terms changed")
        candidates = s.discovery.opportunities()
        self.assertEqual(candidates, [], "a suspended source yields nothing to triage")

    def test_a_non_conforming_scraped_posting_escalates_rather_than_committing(self):
        """Prose cannot establish conformance, so a human is asked."""
        s = rig([posting("scraped", "900")])
        scraped = FixtureSource("scraped_board", [posting("s1", "900")],
                                structured=False)
        s.discovery.register_source(
            scraped, owner_identity=OWNER, readiness=Readiness.PERMITTED_AUTOMATION,
            compliance=Compliance.PERMITTED, determination="prose only")
        s.policy.amend({"discovery": {"approved_sources": ["board", "scraped_board"]}},
                       OWNER, "t")
        candidates = s.discovery.poll("scraped_board")
        decision = s.qualification.qualify(candidates[0])
        self.assertIs(decision.verdict, Verdict.NEEDS_INFORMATION)
        self.assertIs(decision.conformance.value, "UNKNOWN")


class GoodWorkProceeds(unittest.TestCase):
    def test_a_profitable_conforming_job_is_accepted(self):
        s = rig([posting("good", "900")])
        _, decisions = run(s)
        self.assertIs(decisions[0].verdict, Verdict.ACCEPT)
        self.assertIs(decisions[0].governor_verdict, GovernorVerdict.PROFITABLE)

    def test_a_high_value_job_waits_for_the_owner(self):
        s = rig([posting("big", "9000")], approval_above="2000")
        _, decisions = run(s)
        self.assertIs(decisions[0].verdict, Verdict.NEEDS_OWNER_APPROVAL)
        ranked = s.qualification.rank(decisions)
        s.qualification.select(ranked)
        self.assertIs(s.orchestrator.job(decisions[0].job_id).state,
                      JobState.AWAITING_OWNER_APPROVAL)

    def test_no_new_work_is_taken_while_paused(self):
        s = rig([posting("good", "900")])
        s.policy.set_operating_mode(OperatingMode.PAUSE_NEW_WORK, OWNER, "test")
        from solvent.errors import FailClosed
        candidates = s.discovery.poll("board")
        survivors, _ = s.qualification.triage(candidates)
        with self.assertRaises(FailClosed):
            s.qualification.qualify(survivors[0])


class Ranking(unittest.TestCase):
    def test_a_higher_value_job_outranks_a_lower_value_one(self):
        s = rig([posting("small", "900"), posting("large", "1800")],
                max_job_spend="5000", approval_above="10000")
        _, decisions = run(s)
        ranked = s.qualification.rank(decisions)
        self.assertEqual(ranked[0].opportunity.external_ref, "large")
        self.assertGreater(ranked[0].score, ranked[1].score)

    def test_opportunity_cost_defers_the_weaker_job(self):
        """A profitable job can still lose to a better profitable job."""
        s = rig([posting("small", "900"), posting("large", "1800")],
                max_concurrent=1, max_job_spend="5000", approval_above="10000")
        _, decisions = run(s)
        ranked = s.qualification.rank(decisions)
        selected, deferred = s.qualification.select(ranked)
        self.assertEqual([d.opportunity.external_ref for d in selected], ["large"])
        self.assertEqual([d.opportunity.external_ref for d in deferred], ["small"])
        self.assertIs(deferred[0].reject_reason,
                      RejectReason.BETTER_OPPORTUNITY_AVAILABLE)

    def test_every_candidate_is_judged_before_any_is_committed(self):
        """Otherwise the first job seen wins rather than the best one."""
        # Ceilings lifted so both candidates clear the Governor cleanly; the
        # behaviour under test is hold-then-commit, not the spend ceiling.
        s = rig([posting("first", "900"), posting("better", "1800")],
                max_concurrent=1, max_job_spend="5000", approval_above="10000")
        _, decisions = run(s)
        for decision in decisions:
            self.assertIs(s.orchestrator.job(decision.job_id).state,
                          JobState.QUALIFYING,
                          "qualification must hold, not commit")
        ranked = s.qualification.rank(decisions)
        s.qualification.select(ranked)
        self.assertIs(s.orchestrator.job(ranked[0].job_id).state, JobState.ACCEPTED)

    def test_ranking_never_overturns_the_governor(self):
        """A rejected job is not rankable at any score."""
        s = rig([posting("rejected", "300"), posting("good", "900")])
        _, decisions = run(s)
        ranked = s.qualification.rank(decisions)
        refs = [d.opportunity.external_ref for d in ranked]
        self.assertNotIn("rejected", refs)

    def test_an_unknown_client_is_neither_rewarded_nor_punished(self):
        """Solvent does not invent a reputation for a client it has never met."""
        s = rig([posting("unknown", "900", client="never_seen_before")])
        _, decisions = run(s)
        self.assertEqual(s.qualification._client_factor("never_seen_before"), 1.0)


class QualificationOwnsNoEconomics(unittest.TestCase):
    def test_every_economic_figure_comes_from_the_governor(self):
        """A second place computing margin would be a second financial authority."""
        source = __import__("pathlib").Path("solvent/qualification.py").read_text()
        for forbidden in ("margin_floor", "effective_cost_cents =", "apply_factor",
                          "calibration"):
            with self.subTest(term=forbidden):
                self.assertNotIn(forbidden, source)

    def test_the_decision_cites_the_governor_verdict_it_relayed(self):
        s = rig([posting("good", "900")])
        _, decisions = run(s)
        record = s.qualification.verdicts()[0]
        self.assertEqual(record["governor_verdict"], "PROFITABLE")

    def test_rejection_reasons_are_recorded_as_a_business_metric(self):
        s = rig([posting("thin", "300"), posting("video", "4000",
                                                 needs=("video_editing",))])
        run(s)
        reasons = s.qualification.rejection_reasons()
        self.assertIn(RejectReason.MARGIN_TOO_LOW.value, reasons)
        self.assertIn(RejectReason.MISSING_CAPABILITY.value, reasons)


if __name__ == "__main__":
    unittest.main()
