"""Freelancehunt: the first source that actually permits a machine read.

And the first where a prohibition is specific rather than general.
Freelancer's section 33 forbids API access for any purpose, which
settles every activity at once. Freelancehunt's clause 3.1.11 forbids
"tools for mass mailing of private messages or automatic proposal
submission on projects" -- one sentence that forbids this system's
bidding by name and says nothing about its reading.

So the tests here are mostly about the boundary rather than the
parsing: that discovery is permitted, that bidding is refused with a
clause number rather than deferred as undetermined, and that nothing
in the adapter can be talked into crossing between the two.

The fixture is five real postings taken in a single unauthenticated
request on 2026-10-15. Structural fields are exactly as the platform
sent them; every piece of prose and every name is replaced, because
the structure is what is being tested and the words belong to people
who did not publish them for this.
"""

from __future__ import annotations

import json
import pathlib
import unittest

from solvent import sourceaccess as access
from solvent import sourcecatalog as catalogue
from solvent.marketplaces import (FREELANCEHUNT_HOST, FreelancehuntProjects)

FIXTURE = (pathlib.Path(__file__).parent / "fixtures_marketplace"
           / "freelancehunt_projects.json")


def payload():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class TheAgreementDecidesActivityByActivity(unittest.TestCase):

    def setUp(self):
        self.entry = catalogue.entry("freelancehunt")

    def test_the_source_is_in_the_catalogue(self):
        self.assertIsNotNone(self.entry)

    def test_reading_is_permitted(self):
        for activity in (access.DISCOVER, access.DISCOVER_AUTHENTICATED,
                         access.READ):
            with self.subTest(activity=activity):
                finding = self.entry.terms_for(activity)
                self.assertEqual(finding.finding, catalogue.TERMS_PERMITTED)

    def test_submitting_is_forbidden_not_undetermined(self):
        """The difference matters: one is a gap, the other is an answer."""
        for activity in (access.SUBMIT_BID, access.SUBMIT_APPLICATION):
            with self.subTest(activity=activity):
                finding = self.entry.terms_for(activity)
                self.assertEqual(finding.finding, catalogue.TERMS_FORBIDDEN)

    def test_the_prohibition_cites_the_clause(self):
        finding = self.entry.terms_for(access.SUBMIT_BID)
        self.assertIn("3.1.11", finding.citation)
        self.assertIn("automatic", finding.citation.lower())

    def test_preparing_a_bid_is_not_submitting_one(self):
        self.assertEqual(self.entry.terms_for(access.PREPARE_BID).finding,
                         catalogue.TERMS_PERMITTED)

    def test_messaging_is_left_undetermined_rather_than_assumed(self):
        """'Mass mailing' does not obviously cover one clarification."""
        self.assertEqual(self.entry.terms_for(access.SEND_MESSAGE).finding,
                         catalogue.TERMS_UNDETERMINED)

    def test_accepting_a_contract_is_undetermined(self):
        self.assertEqual(
            self.entry.terms_for(access.ACCEPT_CONTRACT).finding,
            catalogue.TERMS_UNDETERMINED)

    def test_every_permission_records_when_it_was_read(self):
        for finding in self.entry.terms:
            if finding.finding != catalogue.TERMS_UNDETERMINED:
                with self.subTest(activity=finding.activity):
                    self.assertTrue(finding.read_on)

    def test_the_access_method_is_the_official_api(self):
        self.assertEqual(self.entry.access_method, access.OFFICIAL_API)


class TheAdapterReadsAndOnlyReads(unittest.TestCase):

    def test_it_has_no_way_to_send_anything(self):
        """A discovery adapter that grew a bid method would be
        deciding a prohibited permission by writing code."""
        surface = {n for n in dir(FreelancehuntProjects)
                   if not n.startswith("_")}
        for forbidden in ("bid", "submit", "apply", "send", "message",
                          "accept", "propose"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, surface)

    def test_the_request_is_a_get_to_the_one_documented_path(self):
        request = FreelancehuntProjects().request()
        self.assertEqual(request["method"], "GET")
        self.assertIn(FREELANCEHUNT_HOST, request["url"])
        self.assertTrue(request["url"].endswith("/v2/projects"))

    def test_it_cannot_be_pointed_at_another_host(self):
        """An allowlist entry means nothing if the source roams."""
        self.assertEqual(FreelancehuntProjects.host, FREELANCEHUNT_HOST)
        self.assertIn(FREELANCEHUNT_HOST,
                      FreelancehuntProjects(name="x").request()["url"])


class ItParsesWhatThePlatformActuallySent(unittest.TestCase):

    def opportunities(self, **kw):
        return FreelancehuntProjects(**kw).parse(payload())

    def test_the_open_projects_come_through(self):
        self.assertEqual(len(self.opportunities()), 5)

    def test_rubbish_is_refused_rather_than_guessed_at(self):
        for junk in (None, [], "", {"data": "no"}, {"data": [1, 2]}):
            with self.subTest(junk=junk):
                self.assertEqual(
                    FreelancehuntProjects().parse(junk), [])

    def test_a_closed_project_is_not_an_opportunity(self):
        data = payload()
        data["data"][0]["attributes"]["status"] = {"id": 30, "name": "Closed"}
        self.assertEqual(len(FreelancehuntProjects().parse(data)), 4)

    def test_status_comes_from_the_platform_not_from_the_date(self):
        """A project can be open past its expiry and closed before it."""
        data = payload()
        data["data"][0]["attributes"]["expired_at"] = "2000-01-01T00:00:00+00:00"
        self.assertEqual(len(FreelancehuntProjects().parse(data)), 5)

    def test_the_owners_mapping_decides_what_a_skill_label_means(self):
        unmapped = self.opportunities()[0]
        self.assertEqual(unmapped.needs, ())
        mapped = self.opportunities(
            capability_for={"BI & Data Analytics": "csv-cleanup"})[0]
        self.assertEqual(mapped.needs, ("csv-cleanup",))

    def test_an_unmapped_posting_says_so_rather_than_guessing(self):
        flags = " ".join(self.opportunities()[0].risk_indicators)
        self.assertIn("no DeskPilot capability is mapped", flags)

    def test_the_bidding_deadline_is_carried_through(self):
        self.assertTrue(self.opportunities()[0].deadline.startswith("2026-"))

    def test_the_posting_text_is_quarantined(self):
        posting = self.opportunities()[0].posting
        self.assertFalse(isinstance(posting, str),
                         "client prose must not arrive as a bare string")


class ADirectInvitationIsNotAnOpenPosting(unittest.TestCase):
    """is_personal is the platform saying "this one is for you".

    It matters more than it looks. Clause 3.1.11 forbids automatic
    proposal submission on projects; a posting addressed to one
    freelancer is not something you submit a proposal to, so it is
    the one shape of marketplace work this system could legitimately
    acquire unattended -- if the platform's acceptance terms were
    ever established.
    """

    def invitation(self):
        data = payload()
        data["data"][0]["attributes"]["is_personal"] = True
        return FreelancehuntProjects().parse(data)[0]

    def test_a_personal_posting_is_flagged(self):
        flags = " ".join(self.invitation().risk_indicators)
        self.assertIn("addressed to a specific freelancer", flags)

    def test_acquisition_reads_that_flag_as_an_invitation(self):
        from solvent import acquisition as acq

        self.assertEqual(acq.classify(self.invitation()),
                         acq.DIRECT_INVITATION)

    def test_an_ordinary_posting_reads_as_a_bid(self):
        from solvent import acquisition as acq

        ordinary = FreelancehuntProjects().parse(payload())[0]
        self.assertEqual(acq.classify(ordinary), acq.COMPETITIVE_BID)

    def test_an_invitation_is_still_not_acceptable_unattended(self):
        """Discovery is permitted here; acceptance has not been read."""
        from solvent import acquisition as acq

        problem = acq.channel_problem(self.invitation(),
                                      source_id="freelancehunt")
        self.assertIn("automated contract acceptance", problem)


class MoneyIsNotInvented(unittest.TestCase):
    """Every budget on this platform is hryvnia, and that is the result."""

    def opportunities(self):
        return FreelancehuntProjects().parse(payload())

    def test_a_hryvnia_budget_produces_no_price(self):
        for opportunity in self.opportunities():
            with self.subTest(ref=opportunity.external_ref):
                self.assertEqual(opportunity.quoted_cents, 0)

    def test_it_says_why_there_is_no_price(self):
        flags = " ".join(self.opportunities()[0].risk_indicators)
        self.assertIn("no exchange rate", flags)
        self.assertIn("UAH", flags)

    def test_a_dollar_budget_is_priced(self):
        data = payload()
        data["data"][0]["attributes"]["budget"] = {"amount": 250,
                                                   "currency": "USD"}
        opportunity = FreelancehuntProjects().parse(data)[0]
        self.assertEqual(opportunity.quoted_cents, 25_000)

    def test_a_budget_that_is_not_a_number_is_refused(self):
        data = payload()
        data["data"][0]["attributes"]["budget"] = {"amount": "lots",
                                                   "currency": "USD"}
        self.assertEqual(
            FreelancehuntProjects().parse(data)[0].quoted_cents, 0)

    def test_a_missing_budget_is_refused(self):
        data = payload()
        del data["data"][0]["attributes"]["budget"]
        opportunity = FreelancehuntProjects().parse(data)[0]
        self.assertEqual(opportunity.quoted_cents, 0)
        self.assertIn("states no budget",
                      " ".join(opportunity.risk_indicators))

    def test_a_zero_budget_is_not_a_price(self):
        data = payload()
        data["data"][0]["attributes"]["budget"] = {"amount": 0,
                                                   "currency": "USD"}
        self.assertEqual(
            FreelancehuntProjects().parse(data)[0].quoted_cents, 0)


class AnOwnerRecordedRateUnblocksThePlatform(unittest.TestCase):
    """The permitted marketplace prices nothing until a rate exists.

    This is the part that would have been easy to fake. Every budget
    on Freelancehunt is hryvnia, so a permitted integration that
    cannot convert produces a stream of unpriceable postings -- which
    looks like a broken adapter and is actually a missing owner
    decision. The temptation is to pick a plausible rate. A rate
    multiplies every price that comes off the platform, so a guessed
    one makes every quote a guess.
    """

    def priced(self, rates):
        return FreelancehuntProjects(rates=rates).parse(payload())[0]

    def test_a_recorded_rate_converts_the_budget(self):
        # 16,500 UAH at 2.4 cents per hryvnia is $396.00.
        self.assertEqual(self.priced({"UAH": 2.4}).quoted_cents, 39_600)

    def test_the_conversion_says_it_is_the_owners_figure(self):
        flags = " ".join(self.priced({"UAH": 2.4}).risk_indicators)
        self.assertIn("owner's recorded rate", flags)
        self.assertIn("not a live quote", flags)

    def test_it_says_what_the_client_actually_committed_to(self):
        """The client agreed to hryvnia, not to the converted figure."""
        flags = " ".join(self.priced({"UAH": 2.4}).risk_indicators)
        self.assertIn("committed to the hryvnia amount", flags)

    def test_a_rate_for_another_currency_does_not_apply(self):
        self.assertEqual(self.priced({"PLN": 25.0}).quoted_cents, 0)

    def test_a_zero_or_negative_rate_is_discarded(self):
        for bad in (0, -2.4, "nonsense", None):
            with self.subTest(bad=bad):
                self.assertEqual(self.priced({"UAH": bad}).quoted_cents, 0)

    def test_no_rate_is_the_default(self):
        self.assertEqual(FreelancehuntProjects().rates, {})


class TheCounterpartyRecordIsCarriedNotScored(unittest.TestCase):

    def test_the_platforms_own_numbers_are_reported(self):
        flags = " ".join(
            FreelancehuntProjects().parse(payload())[0].risk_indicators)
        self.assertIn("completed project(s)", flags)
        self.assertIn("positive review(s)", flags)

    def test_reliability_is_left_to_the_owner(self):
        flags = " ".join(
            FreelancehuntProjects().parse(payload())[0].risk_indicators)
        self.assertIn("the owner's call", flags)

    def test_a_missing_employer_record_is_said(self):
        data = payload()
        del data["data"][0]["attributes"]["employer"]
        flags = " ".join(
            FreelancehuntProjects().parse(data)[0].risk_indicators)
        self.assertIn("no employer record", flags)


if __name__ == "__main__":
    unittest.main()
