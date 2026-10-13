"""Accepting a contract: the same authority, three more questions.

Submitting a proposal offers to work. Accepting promises to. The gap
between those two sentences is where the extra checks live, and the reason
claiming is a second method on the existing authority rather than a new
component: a separate claiming agent would need its own copy of all seven
submission gates, and the day the copies disagreed one of them would be
accepting work the other refused.
"""

from __future__ import annotations

import unittest

from solvent import sourceaccess as access
from solvent import submission as tx
from tests_solvent.test_submission import OWNER, POSTING, Base as SubmissionCase


class Claiming(SubmissionCase):
    """Reuses the submission fixture deliberately: same gates, same setup."""

    def accepted(self, request):
        return {"status": "accepted", "id": "contract-1"}

    def prepared(self, *, price_cents=50_000, grant_claim=True):
        """Reuses the submission fixture's own helper chain."""
        row = self.discover()
        application = super().prepared(row, price_cents=price_cents)
        self.s.applications.record(application, row)
        if grant_claim:
            self.s.discovery.grant("freelancer", access.ACCEPT_CONTRACT,
                                   owner_identity=OWNER,
                                   why="test: the owner permits accepting")
        return application, row


class AcceptingNeedsItsOwnPermission(Claiming):

    def test_submit_permission_does_not_let_it_accept(self):
        """The distinction the whole permission split exists for."""
        application, opportunity = self.prepared(grant_claim=False)
        self.s.discovery.grant("freelancer", access.SUBMIT_APPLICATION,
                               owner_identity=OWNER, why="test")
        claimed, why = self.s.applications.claim(
            application, offered_cents=50_000, opportunity=opportunity,
            transport=self.accepted)
        self.assertFalse(claimed)
        self.assertIn("ACCEPT_CONTRACT", why)

    def test_with_the_permission_it_gets_past_that_gate(self):
        application, opportunity = self.prepared()
        _, why = self.s.applications.claim(
            application, offered_cents=50_000, opportunity=opportunity,
            transport=self.accepted)
        self.assertNotIn("ACCEPT_CONTRACT", why)


class CanTheWorkStillBeDone(Claiming):

    def test_a_withdrawn_capability_stops_the_commitment(self):
        application, opportunity = self.prepared()
        registry = self.s.capability
        original = registry.may_deploy
        registry.may_deploy = lambda n: (False, "certification withdrawn")
        self.addCleanup(setattr, registry, "may_deploy", original)

        claimed, why = self.s.applications.claim(
            application, offered_cents=50_000, opportunity=opportunity,
            transport=self.accepted)
        self.assertFalse(claimed)
        self.assertIn("promise work nobody can do", why)

    def test_an_opportunity_naming_no_capability_is_refused(self):
        application, opportunity = self.prepared()
        opportunity = {**opportunity, "needs": []}
        claimed, why = self.s.applications.claim(
            application, offered_cents=50_000, opportunity=opportunity,
            transport=self.accepted)
        self.assertFalse(claimed)
        self.assertIn("does not say which capability", why)


class CanItStillBeVerified(Claiming):

    def test_a_high_value_contract_needs_a_producible_tier(self):
        """Asked before accepting, for the same reason the cycle asks
        before budgeting: finding out at delivery means the work is done
        and cannot be handed over.

        T2 is made genuinely unproducible rather than assumed to be. With
        an owner signing key configured -- as this fixture has -- owner
        review *is* a producible second opinion, so a test that merely
        picked a high price would pass the gate and prove nothing.
        """
        from solvent import independent

        application, opportunity = self.prepared(price_cents=200_000)
        nothing = independent.Availability(producers=())
        original = independent.available
        independent.available = lambda **kwargs: nothing
        self.addCleanup(setattr, independent, "available", original)

        claimed, why = self.s.applications.claim(
            application, offered_cents=200_000, opportunity=opportunity,
            transport=self.accepted)
        self.assertFalse(claimed)
        self.assertIn("T2_INDEPENDENT_REVIEW", why)

    def test_and_with_a_reviewer_available_that_gate_passes(self):
        """Guards the above: a check that refused everything would pass it."""
        application, opportunity = self.prepared(price_cents=200_000)
        _, why = self.s.applications.claim(
            application, offered_cents=200_000, opportunity=opportunity,
            transport=self.accepted)
        self.assertNotIn("T2_INDEPENDENT_REVIEW", why)

    def test_a_low_value_contract_does_not_need_one(self):
        application, opportunity = self.prepared(price_cents=5_000)
        _, why = self.s.applications.claim(
            application, offered_cents=5_000, opportunity=opportunity,
            transport=self.accepted)
        self.assertNotIn("INDEPENDENT_REVIEW", why)


class AreTheseTheTermsTheOwnerApproved(Claiming):

    def test_a_client_accepting_at_their_own_number_needs_the_owner(self):
        """A client may accept a bid at different terms. Accepting binds
        DeskPilot to theirs."""
        application, opportunity = self.prepared(price_cents=50_000)
        claimed, why = self.s.applications.claim(
            application, offered_cents=30_000, opportunity=opportunity,
            transport=self.accepted)
        self.assertFalse(claimed)
        self.assertIn("the owner's decision", why)
        self.assertIn("300.00", why)

    def test_matching_terms_pass_that_gate(self):
        application, opportunity = self.prepared(price_cents=50_000)
        _, why = self.s.applications.claim(
            application, offered_cents=50_000, opportunity=opportunity,
            transport=self.accepted)
        self.assertNotIn("rounding difference", why)


class TheGateDecidesLast(Claiming):

    def test_nothing_is_accepted_without_the_gate(self):
        application, opportunity = self.prepared()
        claimed, why = self.s.applications.claim(
            application, offered_cents=50_000, opportunity=opportunity,
            transport=self.accepted)
        self.assertFalse(claimed,
                         "a contract was accepted without the Action Gate")

    def test_no_transport_accepts_nothing(self):
        application, opportunity = self.prepared()
        claimed, why = self.s.applications.claim(
            application, offered_cents=50_000, opportunity=opportunity,
            transport=None)
        self.assertFalse(claimed)


class AtMostOneObligation(Claiming):

    def test_an_accepted_contract_is_not_accepted_again(self):
        application, opportunity = self.prepared()
        self.s.applications._attempt(
            application, self.s.applications.stored(application.id)["digest"],
            phase="CLAIM", outcome=tx.ACCEPTED, detail="already accepted")
        claimed, why = self.s.applications.claim(
            application, offered_cents=50_000, opportunity=opportunity,
            transport=self.accepted)
        self.assertFalse(claimed)
        self.assertIn("second obligation", why)

    def test_an_unknown_outcome_also_blocks_a_second_attempt(self):
        """Uncertainty is not permission to retry -- and for a binding
        commitment the cost of being wrong is an obligation, not a
        duplicate message."""
        application, opportunity = self.prepared()
        self.s.applications._attempt(
            application, self.s.applications.stored(application.id)["digest"],
            phase="CLAIM", outcome=tx.UNKNOWN_REMOTE_STATE,
            detail="the network died mid-accept")
        claimed, why = self.s.applications.claim(
            application, offered_cents=50_000, opportunity=opportunity,
            transport=self.accepted)
        self.assertFalse(claimed)
        self.assertIn("second obligation", why)


class NoSecondAgent(unittest.TestCase):
    """The architectural constraint, checked structurally."""

    def test_claiming_lives_on_the_submission_authority(self):
        from solvent.application import Applications

        self.assertTrue(hasattr(Applications, "claim"))
        self.assertTrue(hasattr(Applications, "submit"))

    def test_there_is_no_separate_claiming_component(self):
        import pathlib
        import re

        offenders = []
        for path in sorted(pathlib.Path("solvent").glob("*.py")):
            text = path.read_text(encoding="utf-8")
            for match in re.finditer(r"^class (\w*(Claim|Bidder|Bidding)\w*)",
                                     text, re.MULTILINE):
                offenders.append(f"{path.name}:{match.group(1)}")
        self.assertEqual(offenders, [],
                         f"a second bidding or claiming agent exists: "
                         f"{offenders}")


if __name__ == "__main__":
    unittest.main()
