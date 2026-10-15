"""Acquiring work without submitting a proposal.

Every freelance agreement read in this engagement forbids the same
act. Freelancer's section 33 forbids automated API access outright.
Freelancehunt's clause 3.1.11 forbids "automatic proposal submission
on projects" by name. None of them forbids being hired.

That is the finding these tests protect. A client who picks a
freelancer and sends them work has not received a proposal, so the
clause is not engaged -- and the channel that looks like a way round
the rule is the only compliant route to a first paying job.

The freeze on competitive bidding is an *added* control. These tests
check both halves of that: that it refuses what it should, and that
it did not quietly replace the permission checks that were already
there.
"""

from __future__ import annotations

import tempfile
import unittest

from solvent import acquisition as acq
from solvent import sourceaccess as access
from solvent import sourcecatalog as catalogue
from solvent.harness import OWNER, Solvent

INVITATION = ("addressed to a specific freelancer rather than open "
              "to proposals",)


class Opportunity:
    """The fields acquisition reads, and nothing else."""

    def __init__(self, *, owner_entered=False, source="freelancehunt",
                 risk_indicators=()):
        self.owner_entered = owner_entered
        self.source = source
        self.risk_indicators = risk_indicators


class Base(unittest.TestCase):

    def setUp(self):
        self.box = tempfile.TemporaryDirectory()
        self.addCleanup(self.box.cleanup)
        self.s = Solvent(f"{self.box.name}/hire.db")
        self.addCleanup(self.close)

    def close(self):
        try:
            self.s.store.close()
        except Exception:
            pass

    def grant(self, capability="csv-cleanup", **over):
        grant = {"max_value_cents": 30_000, "min_deadline_hours": 48,
                 "max_jobs": 4, "channels": list(acq.OPEN_CHANNELS)}
        grant.update(over)
        self.s.policy.amend(
            {"acquisition": {"standing": {capability: grant}}},
            OWNER, "test")


class WorkIsClassifiedByHowItArrived(unittest.TestCase):

    def test_the_owners_own_client_is_direct(self):
        self.assertEqual(
            acq.classify(Opportunity(owner_entered=True)), acq.DIRECT_CLIENT)

    def test_a_personal_posting_is_an_invitation(self):
        self.assertEqual(
            acq.classify(Opportunity(risk_indicators=INVITATION)),
            acq.DIRECT_INVITATION)

    def test_an_open_posting_is_a_bid(self):
        self.assertEqual(acq.classify(Opportunity()), acq.COMPETITIVE_BID)

    def test_the_three_channels_are_declared(self):
        self.assertEqual(len(acq.CHANNELS), 3)
        self.assertNotIn(acq.COMPETITIVE_BID, acq.OPEN_CHANNELS)


class CompetitiveBiddingIsFrozen(Base):

    def test_an_open_posting_is_refused(self):
        problem = acq.channel_problem(Opportunity())
        self.assertIn("frozen", problem)

    def test_the_refusal_cites_the_clause(self):
        self.assertIn("3.1.11", acq.channel_problem(Opportunity()))

    def test_no_standing_grant_can_unfreeze_it(self):
        """Even an owner editing policy by hand cannot authorise it."""
        self.grant(channels=[acq.COMPETITIVE_BID, acq.DIRECT_CLIENT])
        allowed, why = acq.may_accept(
            self.s.policy, Opportunity(), capability="csv-cleanup",
            value_cents=10_000, deadline_hours=72)
        self.assertFalse(allowed)
        self.assertIn("frozen", why)

    def test_the_grant_refuses_a_bid_on_its_own_account(self):
        """The second gate, reached directly.

        may_accept refuses a competitive bid at its first gate, so
        the check inside StandingAuthority.problem is unreachable
        through it -- a mutant that deleted that check survived,
        because the test above was satisfied by the outer gate and
        never exercised the inner one.

        It is not redundant. Anything holding a StandingAuthority and
        asking it directly gets the same answer, which is what makes
        it defence in depth rather than decoration.
        """
        authority = acq.StandingAuthority(
            capability="csv-cleanup", max_value_cents=30_000,
            min_deadline_hours=48, max_jobs=4,
            channels=(acq.COMPETITIVE_BID, acq.DIRECT_CLIENT))
        problem = authority.problem(
            channel=acq.COMPETITIVE_BID, value_cents=10_000,
            deadline_hours=72, already_taken=0)
        self.assertIn("frozen", problem)

    def test_the_same_grant_allows_the_open_channels(self):
        """So the test above is refusing the channel, not everything."""
        authority = acq.StandingAuthority(
            capability="csv-cleanup", max_value_cents=30_000,
            min_deadline_hours=48, max_jobs=4,
            channels=(acq.COMPETITIVE_BID, acq.DIRECT_CLIENT))
        self.assertEqual(
            authority.problem(channel=acq.DIRECT_CLIENT, value_cents=10_000,
                              deadline_hours=72, already_taken=0), "")

    def test_the_freeze_adds_a_gate_and_removes_none(self):
        """A route the platform forbids stays forbidden.

        The danger in adding a freeze is that it becomes the only
        check anybody looks at. Freelancehunt permits discovery and
        forbids submission; the catalogue still says so, and this
        test fails if the freeze ever became a substitute for
        reading it.
        """
        entry = catalogue.entry("freelancehunt")
        self.assertEqual(entry.terms_for(access.SUBMIT_BID).finding,
                         catalogue.TERMS_FORBIDDEN)
        self.assertEqual(entry.terms_for(access.DISCOVER).finding,
                         catalogue.TERMS_PERMITTED)


class AnInvitationStillHasToBeAccepted(Base):
    """Discovery and acceptance are different permissions."""

    def test_an_invitation_from_a_platform_that_has_not_been_read(self):
        problem = acq.channel_problem(
            Opportunity(risk_indicators=INVITATION),
            source_id="freelancehunt")
        self.assertIn("automated contract acceptance", problem)

    def test_an_invitation_from_an_uncatalogued_source(self):
        problem = acq.channel_problem(
            Opportunity(source="nowhere", risk_indicators=INVITATION),
            source_id="nowhere")
        self.assertIn("not in the catalogue", problem)

    def test_the_owners_own_client_needs_no_platform_permission(self):
        self.assertEqual(
            acq.channel_problem(Opportunity(owner_entered=True)), "")


class StandingAuthorityIsTheOwnersPreAgreedYes(Base):

    def direct(self):
        return Opportunity(owner_entered=True, source="owner_entered")

    def ask(self, **over):
        kw = {"capability": "csv-cleanup", "value_cents": 25_000,
              "deadline_hours": 72, "already_taken": 0}
        kw.update(over)
        return acq.may_accept(self.s.policy, self.direct(), **kw)

    def test_without_a_grant_every_job_needs_the_owner(self):
        allowed, why = self.ask()
        self.assertFalse(allowed)
        self.assertIn("granted no standing authority", why)

    def test_inside_the_grant_it_may_be_taken_unasked(self):
        self.grant()
        allowed, why = self.ask()
        self.assertTrue(allowed, why)

    def test_a_job_above_the_ceiling_goes_back_to_the_owner(self):
        self.grant()
        allowed, why = self.ask(value_cents=50_000)
        self.assertFalse(allowed)
        self.assertIn("above the", why)

    def test_a_job_with_too_little_notice_goes_back(self):
        self.grant()
        allowed, why = self.ask(deadline_hours=2)
        self.assertFalse(allowed)
        self.assertIn("notice the owner required", why)

    def test_the_count_is_a_limit(self):
        self.grant()
        allowed, why = self.ask(already_taken=4)
        self.assertFalse(allowed)
        self.assertIn("which is the 4", why)

    def test_an_unpriced_job_is_never_a_standing_yes(self):
        """A blank cheque is not an authorisation."""
        self.grant()
        allowed, why = self.ask(value_cents=0)
        self.assertFalse(allowed)
        self.assertIn("blank cheque", why)

    def test_a_grant_for_one_skill_does_not_cover_another(self):
        self.grant(capability="csv-cleanup")
        allowed, why = self.ask(capability="data-entry")
        self.assertFalse(allowed)
        self.assertIn("no standing authority", why)

    def test_revocation_zeroes_rather_than_deletes(self):
        """The policy merge cannot remove a key, only overwrite it.

        Writing an empty dict is a silent no-op against a deep
        merge. Probation hit this trap in an earlier sweep and this
        grant is shaped the same way, so it is checked here too.
        """
        self.grant()
        self.s.policy.amend(
            {"acquisition": {"standing": {"csv-cleanup": {
                "max_value_cents": 0, "min_deadline_hours": 0,
                "max_jobs": 0, "revoked": True}}}},
            OWNER, "revoke")
        allowed, why = self.ask()
        self.assertFalse(allowed)
        self.assertIn("no standing authority", why)

    def test_it_decides_nothing_about_capacity_or_price(self):
        """Two authorities on one question is how they disagree."""
        surface = {n for n in dir(acq) if not n.startswith("_")}
        for forbidden in ("capacity", "room_for", "reserve", "price",
                          "quote", "bid_floor", "admit"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, surface)


class TheCommandRecordsIt(unittest.TestCase):

    def setUp(self):
        self.box = tempfile.TemporaryDirectory()
        self.addCleanup(self.box.cleanup)
        self.db = f"{self.box.name}/cli.db"

    def run_cli(self, *args):
        import contextlib
        import io

        from solvent.cli import main

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(["setup", "standing", "--db", self.db, *args])
        return code, out.getvalue()

    def test_a_grant_is_recorded(self):
        code, said = self.run_cli("--capability", "csv-cleanup",
                                  "--max-value-cents", "30000",
                                  "--min-deadline-hours", "48",
                                  "--max-jobs", "4")
        self.assertEqual(code, 0, said)
        self.assertIn("standing authority recorded", said)

    def test_it_names_the_freeze_so_the_owner_knows(self):
        _, said = self.run_cli("--capability", "csv-cleanup",
                               "--max-value-cents", "30000",
                               "--max-jobs", "4")
        self.assertIn("competitive bidding is frozen", said)

    def test_a_grant_with_no_ceiling_is_refused(self):
        code, said = self.run_cli("--capability", "csv-cleanup",
                                  "--max-jobs", "4")
        self.assertEqual(code, 1)
        self.assertIn("blank cheque", said)

    def test_an_uncertified_skill_is_refused(self):
        code, said = self.run_cli("--capability", "penetration-testing",
                                  "--max-jobs", "1",
                                  "--max-value-cents", "100")
        self.assertEqual(code, 1)
        self.assertIn("no such certified skill", said)

    def test_revoking_takes_effect(self):
        self.run_cli("--capability", "csv-cleanup",
                     "--max-value-cents", "30000", "--max-jobs", "4")
        code, said = self.run_cli("--capability", "csv-cleanup", "--revoke")
        self.assertEqual(code, 0, said)
        solvent = Solvent(self.db)
        self.addCleanup(lambda: solvent.store.close())
        authority = acq.standing_authority(solvent.policy, "csv-cleanup")
        self.assertFalse(authority.configured)


if __name__ == "__main__":
    unittest.main()
