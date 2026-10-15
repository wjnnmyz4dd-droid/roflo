"""Seven activities a marketplace treats as different things.

Permission for one is never permission for another. That sounds obvious and
is the single easiest thing to get wrong, because the activities sound
adjacent: a platform that lets you read its public listings sounds like one
that lets you search while signed in; one that lets you bid sounds like one
that lets you accept.

They are different clauses in different sections of different documents, and
an API may permit account management while forbidding unrestricted job
searching. So each is a separate permission, and two separate facts have to
line up before anything happens:

* the **owner granted** it — an intention;
* the **platform's terms permit** it — an observation, with a citation.

The observation wins. An owner can decide DeskPilot should apply for work;
that does not change what the platform allows.
"""

from __future__ import annotations

import itertools
import tempfile
import unittest

from solvent import sourceaccess as access
from solvent import sourcecatalog
from solvent.discovery import Compliance, FixtureSource, Readiness
from solvent.harness import OWNER, Solvent

TODAY = "2026-10-13"


class Board(FixtureSource):
    is_fixture = False
    kind = "marketplace"
    work_type = access.CLIENT_SERVICES


class TheSevenAreDistinct(unittest.TestCase):

    def test_every_required_distinction_has_its_own_permission(self):
        required = [
            "public job discovery", "authenticated job discovery",
            "automated applications", "automated proposals",
            "automated contract acceptance", "automated service delivery",
            "automated customer communication"]
        self.assertEqual(sorted(access.ACTIVITY_KINDS), sorted(required))

    def test_no_two_activities_share_a_permission(self):
        permissions = list(access.ACTIVITY_KINDS.values())
        self.assertEqual(len(permissions), len(set(permissions)),
                         "two activities mapped to one permission, so "
                         "granting one would silently grant the other")

    def test_public_and_authenticated_discovery_are_not_the_same(self):
        self.assertNotEqual(access.DISCOVER, access.DISCOVER_AUTHENTICATED)
        self.assertIn(access.DISCOVER, access.DEFAULT_PERMISSIONS)
        self.assertNotIn(
            access.DISCOVER_AUTHENTICATED, access.DEFAULT_PERMISSIONS,
            "registering a source must not grant the right to query its "
            "signed-in search")

    def test_accepting_a_contract_is_consequential_and_not_default(self):
        self.assertIn(access.ACCEPT_CONTRACT, access.CONSEQUENTIAL)
        self.assertNotIn(access.ACCEPT_CONTRACT, access.DEFAULT_PERMISSIONS)

    def test_delivering_through_a_platform_is_its_own_permission(self):
        self.assertIn(access.DELIVER_THROUGH_PLATFORM, access.CONSEQUENTIAL)
        self.assertNotIn(access.DELIVER_THROUGH_PLATFORM,
                         access.DEFAULT_PERMISSIONS)

    def test_account_management_does_not_imply_job_searching(self):
        """The example that makes the whole distinction concrete."""
        self.assertNotEqual(access.ACCOUNT_MANAGEMENT,
                            access.DISCOVER_AUTHENTICATED)
        self.assertIn(access.ACCOUNT_MANAGEMENT, access.NEEDS_CREDENTIAL)

    def test_registration_grants_only_the_public_read_side(self):
        self.assertEqual(set(access.DEFAULT_PERMISSIONS),
                         {access.DISCOVER, access.READ, access.IMPORT})


class Granting(unittest.TestCase):

    def setUp(self):
        self.box = tempfile.TemporaryDirectory()
        self.addCleanup(self.box.cleanup)
        self.s = Solvent(f"{self.box.name}/perm.db")
        self.addCleanup(self.close)
        self.s.discovery.register_source(
            Board("plain_board", []), owner_identity=OWNER,
            readiness=Readiness.PERMITTED_AUTOMATION,
            compliance=Compliance.PERMITTED, determination="test")

    def close(self):
        try:
            self.s.store.close()
        except Exception:
            pass


class OneGrantIsNeverAnother(Granting):

    def test_granting_each_activity_grants_only_that_one(self):
        """The property stated as a sweep over every pair."""
        activities = list(access.ACTIVITY_KINDS.values())
        for granted in activities:
            with self.subTest(granted=granted):
                self.setUp()
                self.s.discovery.grant(
                    "plain_board", granted, owner_identity=OWNER,
                    why="test: one permission at a time")
                held = set(self.s.discovery.permissions("plain_board"))
                for other in activities:
                    if other == granted or other in access.DEFAULT_PERMISSIONS:
                        continue
                    self.assertNotIn(
                        other, held,
                        f"granting {granted} also granted {other}")

    def test_there_is_no_grant_everything(self):
        registry = self.s.discovery
        for name in dir(registry):
            with self.subTest(method=name):
                self.assertNotIn("grant_all", name)
                self.assertNotIn("grant_many", name)

    def test_an_unknown_permission_cannot_be_granted(self):
        from solvent.errors import FailClosed

        with self.assertRaises((FailClosed, ValueError)):
            self.s.discovery.grant("plain_board", "DO_WHATEVER",
                                   owner_identity=OWNER, why="test")


class TheTermsOutrankTheGrant(unittest.TestCase):
    """Two facts, recorded separately, and the observation wins."""

    def setUp(self):
        self.box = tempfile.TemporaryDirectory()
        self.addCleanup(self.box.cleanup)
        self.s = Solvent(f"{self.box.name}/terms.db")
        self.addCleanup(self.close)
        # 'upwork' exists in the catalogue, so it is held to its terms.
        self.s.discovery.register_source(
            Board("upwork", []), owner_identity=OWNER,
            readiness=Readiness.PERMITTED_AUTOMATION,
            compliance=Compliance.PERMITTED, determination="test")

    def close(self):
        try:
            self.s.store.close()
        except Exception:
            pass

    def test_an_owner_grant_alone_does_not_authorise_a_governed_activity(self):
        self.s.discovery.grant("upwork", access.SUBMIT_BID,
                               owner_identity=OWNER,
                               why="the owner wants DeskPilot to bid")
        allowed, why = self.s.discovery.may("upwork", access.SUBMIT_BID)
        self.assertFalse(allowed)
        self.assertIn("nobody has read", why)

    def test_an_unread_clause_is_not_permission(self):
        allowed, why = self.s.discovery.may("upwork",
                                            access.DISCOVER_AUTHENTICATED)
        self.assertFalse(allowed)

    def test_a_source_with_no_catalogue_entry_is_not_held_to_absent_terms(self):
        """Fixtures and owner-entered work have no external terms at all."""
        self.s.discovery.register_source(
            Board("not_catalogued", []), owner_identity=OWNER,
            readiness=Readiness.PERMITTED_AUTOMATION,
            compliance=Compliance.PERMITTED, determination="test")
        self.s.discovery.grant("not_catalogued", access.SUBMIT_BID,
                               owner_identity=OWNER, why="test")
        allowed, _ = self.s.discovery.may("not_catalogued", access.SUBMIT_BID)
        self.assertTrue(allowed)

    def test_public_reading_is_not_gated_on_a_per_activity_clause(self):
        allowed, why = self.s.discovery.may("upwork", access.DISCOVER)
        self.assertTrue(allowed, why)


class AFindingNeedsEvidence(unittest.TestCase):

    def test_a_permitted_finding_without_a_citation_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            sourcecatalog.TermsFinding(access.SUBMIT_BID,
                                       sourcecatalog.TERMS_PERMITTED)
        self.assertIn("opinion about them", str(caught.exception))

    def test_a_forbidden_finding_also_needs_a_citation(self):
        with self.assertRaises(ValueError):
            sourcecatalog.TermsFinding(access.SUBMIT_BID,
                                       sourcecatalog.TERMS_FORBIDDEN)

    def test_undetermined_needs_nothing_because_it_claims_nothing(self):
        finding = sourcecatalog.TermsFinding(access.SUBMIT_BID)
        self.assertEqual(finding.finding, sourcecatalog.TERMS_UNDETERMINED)
        self.assertFalse(finding.allows)

    def test_a_finding_about_a_non_permission_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            sourcecatalog.TermsFinding("BE_NICE",
                                       sourcecatalog.TERMS_PERMITTED,
                                       "terms", TODAY)
        self.assertIn("could never be enforced", str(caught.exception))

    def test_a_missing_finding_reads_as_undetermined_not_as_absent(self):
        entry = sourcecatalog.CatalogueEntry("x", "X", "FREELANCE_MARKETPLACE",
                                             "n")
        finding = entry.terms_for(access.SUBMIT_BID)
        self.assertIsNotNone(finding)
        self.assertFalse(finding.allows)

    def test_every_catalogued_finding_is_about_a_real_permission(self):
        for entry in sourcecatalog.CATALOGUE:
            for finding in entry.terms:
                with self.subTest(source=entry.source_id,
                                  activity=finding.activity):
                    self.assertIn(finding.activity, access.PERMISSIONS)


if __name__ == "__main__":
    unittest.main()


class AFixtureIsNotTheMarketplaceItIsNamedAfter(unittest.TestCase):
    """The exemption, and the reason it is not a loophole.

    A `FixtureSource` called "freelancer" is an in-process test double, not
    Freelancer.com, and holding it to that platform's terms of service is a
    category error. It is safe to exempt because a fixture can never be an
    active source at all -- suitability returns FIXTURE before any of this
    is reached -- so nothing registered that way is a route to anywhere.
    """

    def setUp(self):
        self.box = tempfile.TemporaryDirectory()
        self.addCleanup(self.box.cleanup)
        self.s = Solvent(f"{self.box.name}/fx.db")
        self.addCleanup(self.close)

    def close(self):
        try:
            self.s.store.close()
        except Exception:
            pass

    def register(self, source):
        self.s.discovery.register_source(
            source, owner_identity=OWNER,
            readiness=Readiness.PERMITTED_AUTOMATION,
            compliance=Compliance.PERMITTED, determination="test")

    def test_a_fixture_named_after_a_marketplace_is_not_held_to_its_terms(self):
        self.register(FixtureSource("freelancer", []))
        self.s.discovery.grant("freelancer", access.SUBMIT_BID,
                               owner_identity=OWNER, why="test")
        allowed, why = self.s.discovery.may("freelancer", access.SUBMIT_BID)
        self.assertTrue(allowed, why)

    def test_but_a_real_source_with_that_name_still_is(self):
        """The exemption turns on being a fixture, not on the name."""
        self.register(Board("freelancer", []))
        self.s.discovery.grant("freelancer", access.SUBMIT_BID,
                               owner_identity=OWNER, why="test")
        allowed, why = self.s.discovery.may("freelancer", access.SUBMIT_BID)
        self.assertFalse(allowed)
        # Was "nobody has read". Somebody has now, and the refusal
        # names the clause instead of the silence.
        self.assertIn("express written permission", why)
        self.assertIn("the owner's grant does not change what the "
                      "platform allows", why)

    def test_a_fixture_can_never_be_an_active_source_anyway(self):
        self.register(FixtureSource("freelancer", []))
        verdict, _ = self.s.discovery.source_suitability("freelancer")
        self.assertEqual(verdict, "FIXTURE")
        self.assertEqual(self.s.discovery.active_sources(), [])
