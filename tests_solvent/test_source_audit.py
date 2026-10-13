"""Which sources belong in front of the owner, and which only in the record.

A source can be perfectly reachable, fully compliant, switched on, and still
be no use — because what it lists is not work this installation can be paid
for. Health does not notice that. A grants feed answering every request in
200ms is HEALTHY and always will be.

So suitability asks four questions, not one, and a source has to pass all
four. Failing any of them keeps it registered, with its determination, its
permission grants and every opportunity it produced intact; it simply stops
being presented as somewhere money might come from.
"""

from __future__ import annotations

import tempfile
import unittest

from tests_solvent.fixtures import set_commercial_terms
from solvent import discovery
from solvent import sourceaccess as access
from solvent.discovery import (
    Compliance, FixtureSource, ManualSource, Readiness, WorkSource,
)
from solvent.harness import OWNER, Solvent


class ClientBoard(FixtureSource):
    """A marketplace that really would sell what the five skills do."""

    is_fixture = False
    kind = "marketplace"
    work_type = access.CLIENT_SERVICES


class Undeclared(FixtureSource):
    is_fixture = False
    kind = "marketplace"
    # work_type deliberately not set


class GrantsFeed(FixtureSource):
    """Grant-funded work: reachable, compliant, and not what DeskPilot sells.

    Was the real Grants.gov adapter until the catalogue was cut to the six
    marketplaces. The adapter went with its entry; the behaviour it was
    demonstrating here -- healthy and still out of scope -- is the point of
    this module, so it keeps a double rather than losing the case.
    """

    is_fixture = False
    kind = "feed"
    work_type = access.GRANT_FUNDING


class NeedsAKey(FixtureSource):
    """Reachable only with a credential the owner has not configured."""

    is_fixture = False
    kind = "api"
    work_type = access.CONTRACT_WORK

    def request(self):
        return {"url": "https://example.test/search",
                "requires_credential": "EXAMPLE_API_KEY"}


class SourceAudit(unittest.TestCase):

    def setUp(self):
        self.box = tempfile.TemporaryDirectory()
        self.addCleanup(self.box.cleanup)
        self.s = Solvent(f"{self.box.name}/sources.db")
        set_commercial_terms(self.s)
        self.addCleanup(self.close)

    def close(self):
        try:
            self.s.store.close()
        except Exception:
            pass

    def register(self, source, compliance=Compliance.PERMITTED):
        self.s.discovery.register_source(
            source, owner_identity=OWNER,
            readiness=Readiness.PERMITTED_AUTOMATION, compliance=compliance,
            determination="test")
        return source

    def verdict(self, name):
        return self.s.discovery.source_suitability(name)[0]


class WhatEachKindOfSourceIs(SourceAudit):

    def test_a_fixture_is_never_a_revenue_route(self):
        self.register(FixtureSource("demo", []))
        self.assertEqual(self.verdict("demo"), "FIXTURE")

    def test_owner_entered_work_is_active(self):
        self.register(ManualSource("by_hand", []))
        self.assertEqual(self.verdict("by_hand"), "ACTIVE")

    def test_a_grants_feed_is_out_of_scope_however_healthy(self):
        """The case the whole module exists for."""
        self.register(GrantsFeed("grants_feed", []))
        verdict, why = self.s.discovery.source_suitability("grants_feed")
        self.assertEqual(verdict, "OUT_OF_SCOPE")
        self.assertIn("GRANT_FUNDING", why)
        state = self.s.discovery.source_state("grants_feed")
        self.assertEqual(state["health"], access.HEALTHY,
                         "it really is healthy; that is the point")

    def test_a_source_needing_an_unconfigured_credential_is_not_active(self):
        self.register(NeedsAKey("keyed_board", []))
        self.assertEqual(self.verdict("keyed_board"), "NEEDS_CREDENTIAL")

    def test_a_marketplace_selling_the_right_work_is_active(self):
        self.register(ClientBoard("good_board", []))
        self.assertEqual(self.verdict("good_board"), "ACTIVE")

    def test_an_undeclared_source_fails_closed(self):
        """Silence is not a claim of suitability."""
        self.register(Undeclared("mystery", []))
        verdict, why = self.s.discovery.source_suitability("mystery")
        self.assertEqual(verdict, "OUT_OF_SCOPE")
        self.assertIn("UNDECLARED", why)

    def test_an_unregistered_source_is_not_quietly_fine(self):
        self.assertEqual(self.verdict("never_heard_of_it"), "UNKNOWN_SOURCE")


class TheActiveInterfaceShowsOnlyUsableSources(SourceAudit):

    def setUp(self):
        super().setUp()
        self.register(FixtureSource("demo", []))
        self.register(ManualSource("by_hand", []))
        self.register(GrantsFeed("grants_feed", []))
        self.register(NeedsAKey("keyed_board", []))
        self.register(ClientBoard("good_board", []))

    def test_only_the_usable_ones_are_offered(self):
        self.assertEqual(
            sorted(r["name"] for r in self.s.discovery.active_sources()),
            ["by_hand", "good_board"])

    def test_the_unusable_ones_keep_their_records(self):
        """Removed from the interface is not removed from the repository.

        A source's determination, its permission grants and the
        opportunities it produced are the record of a decision somebody
        made. Hiding a row is not a reason to lose it.
        """
        named = {r["name"] for r in self.s.discovery.sources()}
        self.assertEqual(
            named,
            {"demo", "by_hand", "grants_feed", "keyed_board", "good_board"})
        for name in ("grants_feed", "keyed_board", "demo"):
            with self.subTest(source=name):
                state = self.s.discovery.source_state(name)
                self.assertIsNotNone(state)
                self.assertTrue(state["determination"])

    def test_the_report_explains_every_exclusion(self):
        for row in self.s.discovery.source_report():
            with self.subTest(source=row["name"]):
                self.assertTrue(row["why"], "an exclusion with no reason")
                if row["suitability"] != "ACTIVE":
                    self.assertNotIn(
                        row["name"],
                        [a["name"] for a in self.s.discovery.active_sources()])

    def test_suspending_a_source_removes_it_from_the_interface(self):
        self.s.discovery.suspend_source("good_board", "terms changed")
        self.assertEqual(self.verdict("good_board"), "SUSPENDED")
        self.assertNotIn("good_board",
                         [r["name"] for r in self.s.discovery.active_sources()])

    def test_disabling_a_source_removes_it_from_the_interface(self):
        self.s.discovery.set_enabled("good_board", False, owner_identity=OWNER,
                                     why="test: the owner switches it off")
        self.assertEqual(self.verdict("good_board"), "DISABLED")


class EveryDeclaredSourceSaysWhatItCarries(unittest.TestCase):
    """RULE 9 applied to sources: no silent defaults anywhere.

    Scanned ``adapters`` until the four non-marketplace sources were
    retired with their catalogue entries. It scans ``discovery`` now,
    which ships the remaining source classes -- a wider net than the one
    it replaced, not a narrower one.
    """

    def test_no_shipped_source_is_undeclared_by_omission(self):
        import inspect

        offenders = []
        for module in (discovery,):
            for name, obj in vars(module).items():
                if (inspect.isclass(obj) and issubclass(obj, WorkSource)
                        and obj is not WorkSource):
                    if "work_type" not in vars(obj):
                        offenders.append(f"{module.__name__}.{name}")
        self.assertEqual(
            offenders, [],
            "a shipped source inherits UNDECLARED instead of saying what it "
            "carries")

    def test_every_declared_work_type_is_a_known_one(self):
        import inspect

        for module in (discovery,):
            for name, obj in vars(module).items():
                if (inspect.isclass(obj) and issubclass(obj, WorkSource)
                        and obj is not WorkSource):
                    with self.subTest(source=name):
                        self.assertIn(obj.work_type, access.WORK_TYPES)


if __name__ == "__main__":
    unittest.main()


class EveryCapabilityRoutesToItsOwnSpecialist(unittest.TestCase):
    """One Finder, five skills, and each job reaching the right one.

    A centralised finder is only safe if routing is exact. If a posting
    needing `document-processing` were quietly executed by `csv-cleanup`,
    every verification afterwards would be checking the wrong thing against
    the wrong requirements and passing.
    """

    CAPABILITIES = ("csv-cleanup", "spreadsheet-automation",
                    "document-processing", "data-entry", "report-builder")

    def test_all_five_are_cleared_and_distinct(self):
        from solvent.harness import owner_promotions

        promotions = set(owner_promotions())
        for capability in self.CAPABILITIES:
            with self.subTest(capability=capability):
                self.assertIn(capability, promotions)
        self.assertEqual(len(set(self.CAPABILITIES)), 5)

    def test_the_finder_reaches_every_one_of_them(self):
        import tempfile as tf

        from solvent.finder import Finder, Verdict
        from solvent.harness import owner_promotions, promote_capability
        from tests_solvent.findermatrix import Scenario, _posting, build_opportunity

        box = tf.TemporaryDirectory()
        self.addCleanup(box.cleanup)
        s = Solvent(f"{box.name}/routing.db")
        set_commercial_terms(s)
        self.addCleanup(s.store.close)
        for name in owner_promotions():
            promote_capability(s, name, owner_identity=OWNER,
                               why="test: cleared for the routing check")
        s.discovery.register_source(
            ManualSource("by_hand", []), owner_identity=OWNER,
            readiness=Readiness.PERMITTED_AUTOMATION,
            compliance=Compliance.PERMITTED, determination="test")
        finder = Finder(s.discovery, s.policy, s.capability, s.orchestrator,
                        s.store)

        for capability in self.CAPABILITIES:
            with self.subTest(capability=capability):
                opportunity = build_opportunity(Scenario(
                    capability,
                    _posting("r", needs=[capability], quoted_cents=60_000),
                    Verdict.PURSUE, None), source="by_hand")
                assessment = finder.assess(opportunity, extension="xlsx")
                self.assertIs(assessment.verdict, Verdict.PURSUE,
                              f"{capability}: {assessment.why}")
                self.assertIn(capability, assessment.feasibility_detail)

    def test_an_uncertified_capability_is_not_silently_substituted(self):
        """The failure that would be worst: the wrong skill doing the job."""
        import tempfile as tf

        from solvent.finder import Finder, Verdict
        from solvent.harness import owner_promotions, promote_capability
        from tests_solvent.findermatrix import Scenario, _posting, build_opportunity

        box = tf.TemporaryDirectory()
        self.addCleanup(box.cleanup)
        s = Solvent(f"{box.name}/sub.db")
        set_commercial_terms(s)
        self.addCleanup(s.store.close)
        for name in owner_promotions():
            promote_capability(s, name, owner_identity=OWNER, why="test")
        s.discovery.register_source(
            ManualSource("by_hand", []), owner_identity=OWNER,
            readiness=Readiness.PERMITTED_AUTOMATION,
            compliance=Compliance.PERMITTED, determination="test")
        finder = Finder(s.discovery, s.policy, s.capability, s.orchestrator,
                        s.store)

        opportunity = build_opportunity(Scenario(
            "unknown", _posting("u", needs=["quantum-origami"],
                                quoted_cents=60_000),
            Verdict.DECLINE, None), source="by_hand")
        assessment = finder.assess(opportunity, extension="xlsx")
        self.assertIs(assessment.verdict, Verdict.DECLINE)
        self.assertIn("quantum-origami", assessment.feasibility_detail)


class ARobotsRefusalIsEnforcedNotJustRecorded(SourceAudit):
    """A catalogued marketplace that forbids automated reading of its
    listings must not appear as an automated route -- even when it is
    permitted, enabled, healthy, and carrying exactly the right kind of
    work. Every other signal says yes; the platform says no, and the
    platform decides.
    """

    class Marketplace(FixtureSource):
        is_fixture = False
        kind = "marketplace"
        work_type = access.CLIENT_SERVICES

    def setUp(self):
        super().setUp()
        # 'upwork' is catalogued with a FORBIDDEN robots finding, read and
        # cited on 2026-10-13.
        self.register(self.Marketplace("upwork", []))

    def test_it_is_not_active(self):
        verdict, why = self.s.discovery.source_suitability("upwork")
        self.assertEqual(verdict, "TERMS_FORBID")
        self.assertIn("disallows automated reading", why)
        self.assertIn("robots.txt", why)

    def test_everything_else_about_it_says_yes(self):
        """Proving the refusal is the robots finding and nothing else."""
        state = self.s.discovery.source_state("upwork")
        self.assertEqual(state["health"], access.HEALTHY)
        self.assertEqual(state["compliance"], Compliance.PERMITTED.value)
        self.assertTrue(int(state.get("enabled", 1)))
        self.assertEqual(self.s.discovery._work_type("upwork"),
                         access.CLIENT_SERVICES)

    def test_it_is_absent_from_the_active_interface(self):
        self.assertNotIn("upwork",
                         [r["name"] for r in self.s.discovery.active_sources()])

    def test_manual_import_is_still_offered_as_the_way_in(self):
        _, why = self.s.discovery.source_suitability("upwork")
        self.assertIn("Manual import", why)
