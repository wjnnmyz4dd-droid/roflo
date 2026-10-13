"""The four sources that were retired, and the hole retiring them opened.

SAM.gov, Grants.gov, a generic state/local procurement portal and a
generic RFP board were in the catalogue, and two of them had working
adapters tested against a recorded real response. The owner replaced
that list with the six freelance marketplaces, so all four went, and
their adapters went with them: an adapter whose catalogue entry has been
removed is a source nobody has researched, which is worse than no source
at all.

Worse in a specific, mechanical way, which is what this module is really
about. Both terms gates used to answer "no catalogue entry" with *yes*,
on the reasoning that a source nobody wrote terms for should not be held
to a standard nobody wrote down. True of a fixture. Not true of anything
with a host: it meant deleting an entry would have made its source
*more* permitted than it was before, and an unresearched site less
governed than a researched one.

These tests pin the retirement and the fail-closed rule that had to come
with it. ``git log`` has the adapters if they are ever wanted back; what
must not happen is their silent return without the research.
"""

from __future__ import annotations

import tempfile
import unittest

from solvent import sourceaccess as access
from solvent import sourcecatalog as catalogue
from solvent.discovery import (
    Compliance, FixtureSource, Readiness, WorkSource,
)
from solvent.harness import OWNER, Solvent

RETIRED = ("sam_gov", "grants_gov", "state_local_procurement", "rfp_board")


class Uncatalogued(WorkSource):
    """A source with a real host that nobody has researched."""

    name = "mystery_board"
    kind = "http"
    host = "jobs.example.test"
    is_fixture = False
    work_type = access.CLIENT_SERVICES
    min_poll_interval_seconds = 3600.0

    def request(self):
        return {"url": "https://jobs.example.test/api"}

    def parse(self, payload):
        return []


class TheyAreGone(unittest.TestCase):

    def test_no_catalogue_entry_remains(self):
        for source_id in RETIRED:
            with self.subTest(source=source_id):
                self.assertIsNone(catalogue.entry(source_id))

    def test_the_adapters_module_is_gone(self):
        with self.assertRaises(ImportError):
            __import__("solvent.adapters")

    def test_the_catalogue_is_the_six_plus_the_owners_own_channels(self):
        by_type = {}
        for entry in catalogue.CATALOGUE:
            by_type.setdefault(entry.source_type, []).append(entry.source_id)
        self.assertEqual(sorted(by_type), ["DIRECT", "FREELANCE_MARKETPLACE"])
        self.assertEqual(len(by_type["FREELANCE_MARKETPLACE"]), 6)


class WhatRetiringThemWouldHaveOpened(unittest.TestCase):
    """The fail-closed rule, which is the load-bearing part."""

    def setUp(self):
        self.box = tempfile.TemporaryDirectory()
        self.addCleanup(self.box.cleanup)
        self.s = Solvent(f"{self.box.name}/retired.db")
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
            compliance=Compliance.PERMITTED,
            determination="test: the owner believes this is fine")
        return source

    def test_an_uncatalogued_host_may_not_have_its_listings_read(self):
        self.register(Uncatalogued())
        allowed, why = self.s.discovery._public_discovery_allowed(
            "mystery_board")
        self.assertFalse(allowed)
        self.assertIn("not in the source catalogue", why)

    def test_an_uncatalogued_host_may_not_be_acted_on(self):
        self.register(Uncatalogued())
        for activity in (access.SUBMIT_BID, access.SUBMIT_APPLICATION,
                         access.ACCEPT_CONTRACT, access.SEND_MESSAGE,
                         access.DELIVER_THROUGH_PLATFORM):
            with self.subTest(activity=activity):
                allowed, why = self.s.discovery._terms_allow(
                    "mystery_board", activity)
                self.assertFalse(allowed)
                self.assertIn("not in the source catalogue", why)

    def test_an_uncatalogued_host_is_not_polled(self):
        """End to end, through the real poll path."""
        self.register(Uncatalogued())
        self.s.policy.amend(
            {"discovery": {"approved_sources": ["mystery_board"]}},
            OWNER, "test: approved despite not being researched")
        self.assertEqual(self.s.discovery.poll("mystery_board"), [])
        refusals = [e for e in self.s.audit.events()
                    if e["event"] == "discovery.poll_refused"]
        self.assertTrue(refusals)
        self.assertIn("not in the source catalogue", refusals[-1]["why"])

    def test_the_owners_compliance_determination_does_not_substitute(self):
        """PERMITTED is a human judgement about a platform. It is not
        research into what that platform's terms say."""
        self.register(Uncatalogued())
        state = self.s.discovery.source_state("mystery_board")
        self.assertEqual(state["compliance"], Compliance.PERMITTED.value)
        allowed, _ = self.s.discovery._public_discovery_allowed(
            "mystery_board")
        self.assertFalse(allowed)

    def test_a_fixture_is_still_exempt(self):
        """An in-process fixture contacts nobody, so there are no terms
        to honour. Holding it to a standard would break every test in
        the suite and protect no one."""
        self.register(FixtureSource("demo", []))
        allowed, _ = self.s.discovery._public_discovery_allowed("demo")
        self.assertTrue(allowed)

    def test_the_owners_own_channels_are_still_exempt(self):
        """Work the owner typed in contacts nobody either."""
        from solvent.discovery import ManualSource

        self.register(ManualSource("owner_entered", []))
        allowed, _ = self.s.discovery._public_discovery_allowed(
            "owner_entered")
        self.assertTrue(allowed)


class AFetchingSourceDeclaresItsLimit(unittest.TestCase):
    """The second thing retiring the adapters exposed.

    The rule was guarded by a test asserting that the two shipped
    adapters each declared an interval -- a property of those classes,
    not a rule anything enforced. With them gone the test would have
    passed over an empty list forever, and the rate limiter returned
    "no interval recorded" as permission to poll freely.
    """

    def setUp(self):
        self.box = tempfile.TemporaryDirectory()
        self.addCleanup(self.box.cleanup)
        self.s = Solvent(f"{self.box.name}/limits.db")
        self.addCleanup(self.close)

    def close(self):
        try:
            self.s.store.close()
        except Exception:
            pass

    def test_a_fetching_source_without_an_interval_cannot_poll(self):
        class NoInterval(Uncatalogued):
            name = "contra"
            host = "contra.com"
            min_poll_interval_seconds = 0.0

        source = NoInterval()
        self.s.discovery.register_source(
            source, owner_identity=OWNER,
            readiness=Readiness.PERMITTED_AUTOMATION,
            compliance=Compliance.PERMITTED, determination="test")
        self.s.policy.amend({"discovery": {"approved_sources": ["contra"]}},
                            OWNER, "test: approved, so the refusal below is "
                                   "the interval and not the approval")
        allowed, why = self.s.discovery._may_poll(source)
        self.assertFalse(allowed)
        self.assertIn("interval", why)

    def test_a_source_that_contacts_nobody_needs_no_interval(self):
        source = FixtureSource("demo", [])
        self.s.discovery.register_source(
            source, owner_identity=OWNER,
            readiness=Readiness.PERMITTED_AUTOMATION,
            compliance=Compliance.PERMITTED, determination="test")
        self.s.policy.amend({"discovery": {"approved_sources": ["demo"]}},
                            OWNER, "test")
        allowed, why = self.s.discovery._may_poll(source)
        self.assertTrue(allowed, why)


if __name__ == "__main__":
    unittest.main()
