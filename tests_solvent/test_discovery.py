"""Discovery finds work. It can never take it."""

import unittest

from solvent.discovery import (
    Compliance, Discovery, FixtureSource, OpportunityStatus, Readiness, WorkSource,
)
from solvent.errors import FailClosed
from solvent.gate import ActionGate
from solvent.types import ActionClass, PrivacyClass
from tests_solvent.fixtures import OWNER, Rig

POSTING = [{"ref": "X-1", "title": "Sheet", "quoted_cents": 90_000,
            "needs": ["spreadsheet"], "project_state": "CA", "body": "build a sheet"}]


class Base(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()
        self.gate = ActionGate(self.rig.store, self.rig.audit, self.rig.policy,
                               self.rig.governor)
        self.discovery = Discovery(self.rig.store, self.rig.audit, self.rig.policy,
                                   self.gate)
        self.source = FixtureSource("board", POSTING)

    def register(self, readiness=Readiness.PERMITTED_AUTOMATION,
                 compliance=Compliance.PERMITTED, determination="fixture"):
        self.discovery.register_source(
            self.source, owner_identity=OWNER, readiness=readiness,
            compliance=compliance, determination=determination)

    def approve(self, name="board"):
        self.rig.policy.amend({"discovery": {"approved_sources": [name]}}, OWNER, "t")


class DiscoveryIsNotAcceptance(Base):
    def test_discovery_exposes_no_way_to_accept_bid_or_commit(self):
        """Finding a job is not taking it — there is no method that could."""
        for forbidden in ("accept", "bid", "claim", "commit", "apply", "purchase",
                          "spend", "pay"):
            with self.subTest(method=forbidden):
                self.assertFalse(hasattr(self.discovery, forbidden))

    def test_a_discovered_opportunity_starts_as_a_candidate_only(self):
        self.register()
        self.approve()
        found = self.discovery.poll("board")
        self.assertEqual(len(found), 1)
        self.assertEqual(self.discovery.counts(),
                         {OpportunityStatus.DISCOVERED.value: 1})


class SourceGovernance(Base):
    def test_solvent_cannot_register_a_source_for_itself(self):
        for who in ("discovery", "qualification", "execution", "owner:impostor"):
            with self.subTest(who=who), self.assertRaises(FailClosed):
                self.discovery.register_source(
                    self.source, owner_identity=who,
                    readiness=Readiness.PERMITTED_AUTOMATION,
                    compliance=Compliance.PERMITTED, determination="x")

    def test_permission_requires_a_recorded_determination(self):
        """Nobody may mark a platform automatable without writing down why."""
        with self.assertRaises(FailClosed):
            self.register(determination="")

    def test_permission_requires_the_readiness_ladder_to_be_climbed(self):
        """Scraping a page is not marketplace readiness."""
        for readiness in (Readiness.RESEARCH_REQUIRED, Readiness.RESEARCHED,
                          Readiness.SUPPORTED_BY_API, Readiness.SUPPORTED_BY_BROWSER):
            with self.subTest(readiness=readiness), self.assertRaises(FailClosed):
                self.register(readiness=readiness)

    def test_an_unapproved_source_is_never_polled(self):
        """Policy approval is separate from the compliance determination."""
        self.register()
        self.assertEqual(self.discovery.poll("board"), [])

    def test_an_undetermined_source_is_never_polled(self):
        self.discovery.register_source(
            self.source, owner_identity=OWNER, readiness=Readiness.RESEARCHED,
            compliance=Compliance.UNDETERMINED, determination="")
        self.approve()
        self.assertEqual(self.discovery.poll("board"), [])

    def test_a_prohibited_source_is_never_polled(self):
        self.discovery.register_source(
            self.source, owner_identity=OWNER, readiness=Readiness.RESEARCHED,
            compliance=Compliance.PROHIBITED,
            determination="terms forbid automated operation")
        self.approve()
        self.assertEqual(self.discovery.poll("board"), [])

    def test_a_source_can_be_suspended_when_its_terms_change(self):
        self.register()
        self.approve()
        self.assertEqual(len(self.discovery.poll("board")), 1)
        self.discovery.suspend_source("board", "terms of service changed")
        self.assertEqual(self.discovery.poll("board"), [])

    def test_unknown_source_fails_closed(self):
        with self.assertRaises(FailClosed):
            self.discovery.poll("a_platform_nobody_registered")


class ExternalFetchGoesThroughTheGate(Base):
    """Discovery gets no bypass, no private path, no special credential."""

    class RemoteSource(WorkSource):
        name = "remote_board"
        kind = "http"
        host = "jobs.example.com"
        structured_offer_terms = True

        def request(self):
            return {"path": "/api/jobs"}

        def parse(self, payload):
            return []

    def test_a_remote_source_is_refused_when_the_gate_refuses(self):
        remote = self.RemoteSource()
        self.discovery.register_source(
            remote, owner_identity=OWNER, readiness=Readiness.PERMITTED_AUTOMATION,
            compliance=Compliance.PERMITTED, determination="public API, terms allow")
        self.approve("remote_board")
        # No egress allowlist entry, so the Gate denies and Discovery has no fallback.
        self.assertEqual(self.discovery.poll("remote_board"), [])
        denials = [a for a in self.rig.store.raw_readonly(
            "SELECT * FROM action_requests") if not a["allowed"]]
        self.assertTrue(denials)

    def test_discovery_without_a_gate_cannot_fetch_remotely(self):
        gateless = Discovery(self.rig.store, self.rig.audit, self.rig.policy)
        remote = self.RemoteSource()
        gateless.register_source(
            remote, owner_identity=OWNER, readiness=Readiness.PERMITTED_AUTOMATION,
            compliance=Compliance.PERMITTED, determination="public API")
        self.approve("remote_board")
        with self.assertRaises(FailClosed) as ctx:
            gateless.poll("remote_board")
        self.assertIn("no path around it", str(ctx.exception))

    def test_a_fixture_source_makes_no_external_call_at_all(self):
        self.register()
        self.approve()
        self.discovery.poll("board")
        self.assertEqual(self.rig.store.raw_readonly(
            "SELECT * FROM action_requests"), [])


class PostingsAreUntrusted(Base):
    def test_the_posting_body_is_quarantined(self):
        self.register()
        self.approve()
        found = self.discovery.poll("board")
        rendered = f"{found[0].posting}"
        self.assertIn("<untrusted:", rendered)
        self.assertNotIn("build a sheet", rendered)

    def test_a_scraped_source_cannot_supply_structured_terms(self):
        """Prose is prose. Only a schema-backed source asserts terms."""
        scraped = FixtureSource("scraped", POSTING, structured=False)
        self.discovery.register_source(
            scraped, owner_identity=OWNER, readiness=Readiness.PERMITTED_AUTOMATION,
            compliance=Compliance.PERMITTED, determination="scraped prose")
        self.approve("scraped")
        found = self.discovery.poll("scraped")
        self.assertFalse(found[0].structured_terms)


if __name__ == "__main__":
    unittest.main()
