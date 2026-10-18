"""Upwork bidding: the figures, the owner's approval, and the boundary.

Every scenario the brief names, against synthetic opportunities. No real
bid is made, no Connect is spent, no customer is contacted, and nothing
is submitted anywhere -- which is not merely a test convention here, it
is what the module does in production too.

The thing most worth testing is the one that is easiest to get wrong:
that a figure DeskPilot could not obtain is reported as unobtainable,
rather than as zero, as a default, or as a confident-sounding estimate.
"""

from __future__ import annotations

import unittest

from solvent import upworkbid as ub
from solvent.errors import FailClosed
from solvent.owner import OwnerChannel
from solvent.types import ActionClass, money
from tests_solvent.fixtures import OWNER, Rig, TEST_ONLY_OWNER_KEY

KEY = TEST_ONLY_OWNER_KEY


def recommendation(**overrides) -> ub.Recommendation:
    """A complete, ordinary recommendation. Overridden per scenario."""
    base = dict(
        opportunity_id="UPW-1", title="Clean a 40k-row customer export",
        summary="De-duplicate and normalise a CSV export",
        category="Data Entry", skill="csv-cleanup", version=1,
        client_budget_cents=ub.platform(money(500), "the posting's budget"),
        bid_cents=money(200), bid_floor_cents=money(120),
        floor_reason="cost plus the owner's 30% margin",
        competing_proposals=ub.unavailable(
            "Upwork exposes no proposal count through a verified route"),
        competing_bids_cents=ub.unavailable(
            "no official source for competing bid amounts"),
        client_hiring_history=ub.unavailable(
            "no official source for client hiring history"),
        market_rate_cents=ub.estimate(money(300), "from observed runs"),
        service_fee_cents=ub.estimate(money(20), "modelled at 10%"),
        connects_required=ub.estimate(4, "modelled"),
        connects_cost_cents=ub.estimate(money(0.60), "modelled"),
        execution_cost_cents=ub.observed(money(8), "measured runs"),
        checker_cost_cents=ub.observed(money(2), "measured runs"),
        revision_allowance_cents=ub.estimate(money(10), "one revision"),
        win_probability=ub.unavailable("no outcomes observed yet"),
        win_confidence=ub.Confidence.INSUFFICIENT_DATA,
        why="clears the floor with room, and the skill is certified",
        expires_at="2026-10-19T00:00:00Z",
    )
    base.update(overrides)
    return ub.Recommendation(**base)


class AFigureCarriesItsProvenance(unittest.TestCase):
    """The failure this guards against is presenting an estimate as a fact."""

    def test_an_unavailable_figure_may_not_carry_a_value(self):
        with self.assertRaises(FailClosed):
            ub.Figure(300, ub.Provenance.UNAVAILABLE, "no source")

    def test_a_figure_with_no_value_may_not_claim_a_source(self):
        with self.assertRaises(FailClosed):
            ub.Figure(None, ub.Provenance.MODEL_ESTIMATE, "modelled")

    def test_an_unavailable_figure_must_say_why(self):
        with self.assertRaises(FailClosed):
            ub.Figure(None, ub.Provenance.UNAVAILABLE, "")

    def test_rendering_never_drops_the_provenance(self):
        for figure in (ub.estimate(300, "x"), ub.observed(0.4, "y"),
                       ub.platform(money(500), "z")):
            with self.subTest(figure=figure):
                self.assertIn(figure.provenance.value, figure.render())

    def test_only_platform_data_reads_as_verified(self):
        self.assertTrue(ub.platform(1, "x").verified)
        for other in (ub.estimate(1, "x"), ub.observed(1, "x"),
                      ub.unavailable("x")):
            with self.subTest(other=other):
                self.assertFalse(other.verified)


class AnUnknownCostIsNotAZeroCost(unittest.TestCase):
    """The arithmetic that decides whether the owner is told a number."""

    def test_a_complete_recommendation_states_its_contribution(self):
        rec = recommendation()
        self.assertTrue(rec.net_contribution.known)
        self.assertEqual(rec.net_contribution.value,
                         rec.bid_cents - rec.known_costs_cents)

    def test_a_missing_fee_makes_the_contribution_unavailable(self):
        rec = recommendation(service_fee_cents=ub.unavailable(
            "the freelancer fee page returns 403 to an automated fetch"))
        self.assertFalse(rec.net_contribution.known)
        self.assertIn("service fee", rec.net_contribution.detail)

    def test_a_missing_connects_cost_makes_the_contribution_unavailable(self):
        rec = recommendation(connects_cost_cents=ub.unavailable(
            "no official per-Connect price"))
        self.assertFalse(rec.net_contribution.known)

    def test_an_unknown_contribution_makes_the_margin_unknown(self):
        rec = recommendation(execution_cost_cents=ub.unavailable("no runs"))
        self.assertFalse(rec.margin.known)

    def test_profit_per_connect_needs_both_halves(self):
        self.assertTrue(recommendation().profit_per_connect.known)
        self.assertFalse(recommendation(
            connects_required=ub.unavailable("unknown")
        ).profit_per_connect.known)

    def test_zero_connects_is_not_a_divisor(self):
        rec = recommendation(connects_required=ub.estimate(0, "free?"))
        self.assertFalse(rec.profit_per_connect.known)


class TheWinProbabilityIsNeverInvented(unittest.TestCase):

    def test_no_history_yields_insufficient_data(self):
        figure, confidence = ub.win_probability(won=0, lost=0)
        self.assertFalse(figure.known)
        self.assertIs(confidence, ub.Confidence.INSUFFICIENT_DATA)

    def test_thin_history_yields_insufficient_data(self):
        figure, confidence = ub.win_probability(won=2, lost=3)
        self.assertFalse(figure.known)
        self.assertIs(confidence, ub.Confidence.INSUFFICIENT_DATA)

    def test_enough_history_yields_a_rate_at_low_confidence(self):
        figure, confidence = ub.win_probability(won=3, lost=9)
        self.assertTrue(figure.known)
        self.assertEqual(figure.value, 0.25)
        self.assertIs(figure.provenance, ub.Provenance.HISTORICAL_OBSERVATION)
        self.assertIs(confidence, ub.Confidence.LOW)

    def test_confidence_rises_with_the_sample_and_not_with_the_rate(self):
        _, low = ub.win_probability(won=5, lost=7)
        _, medium = ub.win_probability(won=15, lost=20)
        _, high = ub.win_probability(won=50, lost=70)
        self.assertIs(low, ub.Confidence.LOW)
        self.assertIs(medium, ub.Confidence.MEDIUM)
        self.assertIs(high, ub.Confidence.HIGH)

    def test_a_rate_is_never_platform_data(self):
        figure, _ = ub.win_probability(won=40, lost=60)
        self.assertFalse(figure.verified)


class TheBidCompetesButNeverAtALoss(unittest.TestCase):

    def test_a_known_competition_is_undercut(self):
        bid, why = ub.recommended_bid(
            client_budget_cents=money(500), floor_cents=money(100),
            competing_bids=ub.estimate(money(300), "estimated"))
        self.assertEqual(bid, money(270))
        self.assertIn("MODEL ESTIMATE", why)

    def test_the_floor_outranks_the_competition(self):
        bid, why = ub.recommended_bid(
            client_budget_cents=money(500), floor_cents=money(400),
            competing_bids=ub.estimate(money(300), "estimated"))
        self.assertEqual(bid, money(400))
        self.assertIn("floor stands", why)

    def test_without_competition_data_it_does_not_guess_at_undercutting(self):
        bid, why = ub.recommended_bid(
            client_budget_cents=money(500), floor_cents=money(100),
            competing_bids=ub.unavailable("no official source"))
        self.assertEqual(bid, money(500))
        self.assertIn("nothing is known about the competition", why)

    def test_it_never_exceeds_the_clients_own_budget(self):
        bid, why = ub.recommended_bid(
            client_budget_cents=money(100), floor_cents=money(10),
            competing_bids=ub.estimate(money(900), "estimated"))
        self.assertEqual(bid, money(100))
        self.assertIn("capped", why)

    def test_a_bid_with_no_floor_is_refused(self):
        with self.assertRaises(FailClosed):
            ub.recommended_bid(client_budget_cents=money(500), floor_cents=0,
                               competing_bids=ub.unavailable("none"))

    def test_the_brief_s_own_example(self):
        """$500 budget, ~$300 competition, $200 recommended if it clears."""
        bid, _ = ub.recommended_bid(
            client_budget_cents=money(500), floor_cents=money(150),
            competing_bids=ub.estimate(money(222), "estimated"))
        self.assertLessEqual(bid, money(200))
        self.assertGreaterEqual(bid, money(150))


class TheRankingDoesNotFloatUnknownsToTheTop(unittest.TestCase):

    def test_a_known_contribution_outranks_an_unknown_one(self):
        known = recommendation(opportunity_id="known")
        unknown = recommendation(
            opportunity_id="unknown", bid_cents=money(5000),
            service_fee_cents=ub.unavailable("no fee data"))
        order = [r.opportunity_id for r in ub.rank([unknown, known])]
        self.assertEqual(order[0], "known")

    def test_more_profit_per_connect_ranks_higher(self):
        cheap = recommendation(opportunity_id="cheap",
                               connects_required=ub.estimate(2, "modelled"))
        dear = recommendation(opportunity_id="dear",
                              connects_required=ub.estimate(16, "modelled"))
        order = [r.opportunity_id for r in ub.rank([dear, cheap])]
        self.assertEqual(order[0], "cheap")

    def test_even_a_loss_making_priced_job_outranks_an_unpriceable_one(self):
        """The invariant is "unknown sorts last", whatever else is true.

        Added because the first version of this class passed with the
        rule deleted: in the ordinary case the sibling keys already put
        the priced one first, so nothing was testing the rule itself. A
        priced job that loses money is the case where the two disagree,
        and the ranking must still prefer it -- not because losing money
        is good, but because the owner can see that it loses money,
        which is the whole difference between the two.
        """
        losing = recommendation(opportunity_id="losing",
                                bid_cents=money(5))
        self.assertTrue(losing.net_contribution.known)
        self.assertLess(losing.net_contribution.value, 0)
        unpriceable = recommendation(
            opportunity_id="unpriceable", bid_cents=money(5000),
            service_fee_cents=ub.unavailable("no fee data"))
        self.assertFalse(unpriceable.net_contribution.known)
        order = [r.opportunity_id for r in ub.rank([unpriceable, losing])]
        self.assertEqual(order, ["losing", "unpriceable"])

    def test_the_ranking_is_stable_for_identical_inputs(self):
        batch = [recommendation(opportunity_id=f"O{i}") for i in range(5)]
        self.assertEqual([r.opportunity_id for r in ub.rank(batch)],
                         [r.opportunity_id for r in ub.rank(batch)])


class TheApprovalIsBoundToWhatWasApproved(unittest.TestCase):
    """Authenticated, job-specific, bid-specific, time-limited, single use."""

    def setUp(self):
        self.rig = Rig()
        self.owner = OwnerChannel(self.rig.store, self.rig.audit,
                                  self.rig.policy, key=KEY)
        self.rec = recommendation()

    def _approve(self, rec=None, **overrides):
        rec = rec or self.rec
        kwargs = dict(owner_identity=OWNER, subject=rec.approval_subject(),
                      action_class=ub.PROPOSAL_ACTION_CLASS,
                      job_id=rec.opportunity_id, max_cents=rec.bid_cents)
        kwargs.update(overrides)
        return self.owner.issue(**kwargs)

    def test_an_approval_for_this_recommendation_is_accepted(self):
        approval = self._approve()
        self.assertEqual(ub.approval_problem(approval, self.rec), "")
        self.assertEqual(self.owner.verify(
            approval, action_class=ub.PROPOSAL_ACTION_CLASS,
            job_id=self.rec.opportunity_id,
            amount_cents=self.rec.bid_cents), "")

    def test_a_changed_bid_invalidates_the_approval(self):
        approval = self._approve()
        changed = recommendation(bid_cents=money(250))
        problem = ub.approval_problem(approval, changed)
        self.assertIn("different proposal", problem)

    def test_a_redrafted_proposal_invalidates_the_approval(self):
        approval = self._approve()
        self.assertIn("different proposal",
                      ub.approval_problem(approval, recommendation(version=2)))

    def test_a_changed_connects_estimate_invalidates_the_approval(self):
        approval = self._approve()
        changed = recommendation(connects_required=ub.estimate(12, "revised"))
        self.assertIn("different proposal",
                      ub.approval_problem(approval, changed))

    def test_a_changed_fee_estimate_invalidates_the_approval(self):
        approval = self._approve()
        changed = recommendation(
            service_fee_cents=ub.estimate(money(60), "revised"))
        self.assertIn("different proposal",
                      ub.approval_problem(approval, changed))

    def test_rewording_the_pitch_does_not_invalidate_the_approval(self):
        """The owner approved the money, not the prose."""
        approval = self._approve()
        reworded = recommendation(summary="A warmer way of saying the same")
        self.assertEqual(ub.approval_problem(approval, reworded), "")

    def test_an_approval_for_another_opportunity_is_rejected(self):
        approval = self._approve(recommendation(opportunity_id="UPW-OTHER"))
        self.assertIn("different proposal",
                      ub.approval_problem(approval, self.rec))

    def test_a_communication_approval_does_not_authorise_a_proposal(self):
        approval = self._approve(
            action_class=ActionClass.C2_EXTERNAL_COMMUNICATION)
        self.assertIn("C3_FINANCIAL_COMMITMENT",
                      ub.approval_problem(approval, self.rec))

    def test_an_approval_below_the_bid_is_rejected(self):
        approval = self._approve(max_cents=money(100))
        self.assertIn("authorises up to",
                      ub.approval_problem(approval, self.rec))

    def test_an_expired_approval_is_rejected(self):
        approval = self._approve(ttl_minutes=-1)
        self.assertNotEqual(self.owner.verify(
            approval, action_class=ub.PROPOSAL_ACTION_CLASS,
            job_id=self.rec.opportunity_id,
            amount_cents=self.rec.bid_cents), "")

    def test_an_approval_cannot_be_used_twice(self):
        approval = self._approve()
        self.assertEqual(self.owner.verify(
            approval, action_class=ub.PROPOSAL_ACTION_CLASS,
            job_id=self.rec.opportunity_id,
            amount_cents=self.rec.bid_cents), "")
        self.owner.consume(approval)
        self.assertNotEqual(self.owner.verify(
            approval, action_class=ub.PROPOSAL_ACTION_CLASS,
            job_id=self.rec.opportunity_id,
            amount_cents=self.rec.bid_cents), "")

    def test_a_tampered_signature_is_rejected(self):
        import dataclasses

        approval = self._approve()
        forged = dataclasses.replace(approval, max_cents=money(10_000))
        self.assertNotEqual(self.owner.verify(
            forged, action_class=ub.PROPOSAL_ACTION_CLASS,
            job_id=self.rec.opportunity_id,
            amount_cents=money(10_000)), "")

    def test_the_digest_covers_every_material_field(self):
        base = self.rec.material_digest()
        for changed in (
                recommendation(bid_cents=money(201)),
                recommendation(version=2),
                recommendation(bid_floor_cents=money(121)),
                recommendation(connects_required=ub.estimate(5, "x")),
                recommendation(connects_cost_cents=ub.estimate(money(1), "x")),
                recommendation(service_fee_cents=ub.estimate(money(21), "x")),
                recommendation(execution_cost_cents=ub.observed(money(9), "x")),
                recommendation(checker_cost_cents=ub.observed(money(3), "x")),
                recommendation(revision_allowance_cents=ub.estimate(money(11), "x")),
        ):
            with self.subTest(changed=changed.material_fields()):
                self.assertNotEqual(changed.material_digest(), base)

    def test_an_unauthenticated_channel_approves_nothing(self):
        """No key, no approvals -- the whole workflow fails closed."""
        blind = OwnerChannel(self.rig.store, self.rig.audit, self.rig.policy,
                             key=None)
        self.assertFalse(blind.available)
        with self.assertRaises(FailClosed):
            blind.issue(owner_identity=OWNER, subject=self.rec.approval_subject(),
                        action_class=ub.PROPOSAL_ACTION_CLASS,
                        job_id=self.rec.opportunity_id,
                        max_cents=self.rec.bid_cents)

    def test_only_a_registered_owner_may_approve(self):
        with self.assertRaises(FailClosed):
            self._approve(owner_identity="someone-else@example.com")


class NothingIsSubmitted(unittest.TestCase):
    """The boundary, which is not a convention of the tests."""

    def test_an_unapproved_proposal_is_not_eligible(self):
        allowed, why = ub.eligibility(recommendation())
        self.assertFalse(allowed)
        self.assertIn("has not approved", why)

    def test_an_approved_proposal_is_still_not_submitted_by_deskpilot(self):
        allowed, why = ub.eligibility(recommendation(approved_nonce="abc123"))
        self.assertFalse(allowed)
        self.assertIn("submit it yourself", why)
        self.assertIn("has not been verified", why)

    def test_the_submission_client_for_upwork_is_prohibited(self):
        from solvent.submission import UpworkProposalClient

        client = UpworkProposalClient()
        self.assertIn("prohibited", client._why)
        self.assertIn("unverified is not permitted", client._why)

    def test_the_module_contains_no_submission_path(self):
        """A path that does not exist cannot be re-enabled by accident."""
        import inspect

        source = inspect.getsource(ub)
        for forbidden in ("requests.", "httpx.", "urlopen", "socket.",
                          "subprocess."):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


class TheBudgetCountsWhatHappened(unittest.TestCase):

    def setUp(self):
        self.budget = ub.BidBudget(monthly_budget_cents=money(150))

    def _with(self, *events):
        budget = self.budget
        for event in events:
            budget = budget.record(event)
        return budget

    def test_an_unrecognised_event_kind_is_refused(self):
        with self.assertRaises(FailClosed):
            self.budget.record(ub.BidEvent(kind="connects_spent_probably"))

    def test_connects_are_tracked_in_connects(self):
        budget = self._with(
            ub.BidEvent("connects_purchased", connects=100),
            ub.BidEvent("connects_consumed", connects=4, opportunity_id="A"),
            ub.BidEvent("connects_consumed", connects=6, opportunity_id="B"))
        self.assertEqual(budget.connects_purchased, 100)
        self.assertEqual(budget.connects_consumed, 10)
        self.assertEqual(budget.connects_remaining, 90)

    def test_proposal_cost_is_summed_not_multiplied(self):
        """The brief forbids assuming every proposal costs the same."""
        budget = self._with(
            ub.BidEvent("proposal_cost", cents=money(2), opportunity_id="A"),
            ub.BidEvent("proposal_cost", cents=money(0.9), opportunity_id="B"),
            ub.BidEvent("proposal_cost", cents=money(5.1), opportunity_id="C"))
        self.assertEqual(budget.actual_proposal_cost_cents, money(8))

    def test_net_contribution_subtracts_every_recorded_cost(self):
        budget = self._with(
            ub.BidEvent("revenue_collected", cents=money(1000)),
            ub.BidEvent("platform_fee", cents=money(100)),
            ub.BidEvent("processing_cost", cents=money(30)),
            ub.BidEvent("proposal_cost", cents=money(20)))
        self.assertEqual(budget.net_contribution_cents, money(850))

    def test_acquisition_cost_is_unavailable_before_the_first_win(self):
        budget = self._with(
            ub.BidEvent("proposal_cost", cents=money(40)),
            ub.BidEvent("proposal_submitted", opportunity_id="A"))
        figure = budget.acquisition_cost_per_won_contract
        self.assertFalse(figure.known)
        self.assertIn("no contract has been won", figure.detail)

    def test_acquisition_cost_counts_the_losses_too(self):
        budget = self._with(
            ub.BidEvent("proposal_cost", cents=money(40)),
            ub.BidEvent("contract_won", opportunity_id="A"),
            ub.BidEvent("contract_lost", opportunity_id="B"))
        figure = budget.acquisition_cost_per_won_contract
        self.assertTrue(figure.known)
        self.assertEqual(figure.value, float(money(40)))
        self.assertIs(figure.provenance, ub.Provenance.HISTORICAL_OBSERVATION)

    def test_the_monthly_budget_binds(self):
        budget = self._with(
            ub.BidEvent("proposal_cost", cents=money(149)))
        allowed, why = budget.affordable(money(2))
        self.assertFalse(allowed)
        self.assertIn("exceed", why)

    def test_a_proposal_with_no_recorded_cost_is_not_waved_through(self):
        allowed, why = self.budget.affordable(0)
        self.assertFalse(allowed)
        self.assertIn("must come from the platform", why)

    def test_budget_remaining_never_goes_negative(self):
        budget = self._with(ub.BidEvent("proposal_cost", cents=money(400)))
        self.assertEqual(budget.budget_remaining_cents, 0)

    def test_recording_does_not_mutate_the_earlier_budget(self):
        later = self.budget.record(ub.BidEvent("proposal_cost", cents=money(5)))
        self.assertEqual(self.budget.actual_proposal_cost_cents, 0)
        self.assertEqual(later.actual_proposal_cost_cents, money(5))


class TheOwnerSeesEveryFieldAndItsProvenance(unittest.TestCase):

    def test_the_notification_names_every_required_field(self):
        body = recommendation().notification()
        for required in (
                "Upwork opportunity", "category:", "DeskPilot skill:",
                "client budget:", "proposals so far:", "competing bids:",
                "client history:", "market rate:", "recommended bid:",
                "floor:", "Upwork service fee:", "Connects required:",
                "Connects cost:", "execution cost:", "checker cost:",
                "revision allowance:", "net contribution:", "margin:",
                "profit per Connect:", "win probability:", "confidence:",
                "Why:", "Approval expires:"):
            with self.subTest(required=required):
                self.assertIn(required, body)

    def test_an_estimate_is_never_shown_as_platform_data(self):
        body = recommendation().notification()
        self.assertIn("MODEL ESTIMATE", body)
        self.assertIn("UNAVAILABLE", body)
        lines = [l for l in body.splitlines() if "competing bids" in l]
        self.assertTrue(lines)
        self.assertNotIn("VERIFIED PLATFORM DATA", lines[0])

    def test_the_notification_says_approval_is_not_submission(self):
        body = recommendation().notification()
        self.assertIn("does not submit it", body)
        self.assertIn("Upwork's own interface", body)

    def test_an_unavailable_figure_shows_its_reason_to_the_owner(self):
        body = recommendation().notification()
        self.assertIn("no official source for competing bid amounts", body)


if __name__ == "__main__":
    unittest.main()
