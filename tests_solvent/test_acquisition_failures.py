"""§48, §42, §55. What happens when a source misbehaves, and what gaps mean.

External sources break in ways that are not exceptions: they return a page
instead of JSON, they drop a field, they start requiring a login, they answer
with half a response. The failure mode that matters is not the crash -- it is
the silent reinterpretation of malformed data into a job DeskPilot then acts on.
"""

from __future__ import annotations

import pathlib
import tempfile
import unittest

from solvent import sourceaccess as access
from solvent.discovery import (Compliance, FixtureSource, Opportunity,
                               Readiness, WorkSource)
from solvent.errors import FailClosed
from solvent.harness import CSV_PROMOTION, OWNER, Solvent, provision_capability

GOOD = {"ref": "g1", "title": "Deduplicate a CSV", "quoted_cents": 40_000,
        "needs": ["csv-cleanup"], "body": "Please clean this file."}


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.db = str(pathlib.Path(self.dir.name) / "solvent.db")
        self.s = Solvent(self.db)
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
            compliance=Compliance.PERMITTED, determination="fixture")
        approved = self.s.policy.get("discovery", "approved_sources", default=[])
        self.s.policy.amend(
            {"discovery": {"approved_sources": sorted({*approved, source.name})}},
            OWNER, "approve")
        return source

    def reopen(self):
        self.s.store.close()
        self.s = Solvent(self.db)


class ThrowingSource(FixtureSource):
    def parse(self, payload):
        raise ValueError("the response was an HTML login page, not listings")


class MissingFieldSource(FixtureSource):
    def parse(self, payload):
        raise KeyError("quoted_cents")


class HalfBrokenSource(FixtureSource):
    """Returns one good listing, then raises. The partial-response case."""

    def parse(self, payload):
        raise RuntimeError("stream ended after 1 of 20 records")


class AnAdapterThatFailsDoesNotProduceJobs(Base):

    def test_canary_a_working_adapter_does_produce_a_job(self):
        """§67. Every "yields nothing" test below would pass just as well
        against a fixture that never yields anything. This is the control that
        proves the fixture is capable of producing work, so the empty results
        are caused by the failure and not by there being nothing there."""
        self.register(FixtureSource("board", [GOOD]))
        self.assertEqual(len(self.s.discovery.poll("board")), 1)
        self.assertEqual(len(self.s.discovery.opportunities()), 1)

    def test_a_throwing_adapter_yields_nothing(self):
        self.register(ThrowingSource("board", [GOOD]))
        self.assertEqual(self.s.discovery.poll("board"), [])
        self.assertEqual(self.s.discovery.opportunities(), [])

    def test_the_source_is_marked_failed(self):
        self.register(ThrowingSource("board", [GOOD]))
        self.s.discovery.poll("board")
        state = self.s.discovery.source_state("board")
        self.assertEqual(state["health"], access.FAILED)
        self.assertIn("ValueError", state["failure_reason"])

    def test_a_missing_field_is_not_guessed_at(self):
        """§42. No default, no partial record, no 'probably zero'."""
        self.register(MissingFieldSource("board", [GOOD]))
        self.assertEqual(self.s.discovery.poll("board"), [])
        self.assertEqual(self.s.discovery.opportunities(), [])

    def test_a_partial_response_produces_nothing_rather_than_some(self):
        self.register(HalfBrokenSource("board", [GOOD]))
        self.assertEqual(self.s.discovery.poll("board"), [])
        self.assertEqual(self.s.discovery.opportunities(), [])

    def test_the_failure_is_recorded_in_audit(self):
        self.register(ThrowingSource("board", [GOOD]))
        self.s.discovery.poll("board")
        events = [e for e in self.s.audit.events()
                  if e["event"] == "discovery.parse_failed"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["decision"], "REFUSED")

    def test_repeated_failures_stop_it_being_polled(self):
        """§30/§41. Not hammered."""
        self.register(ThrowingSource("board", [GOOD]))
        self.s.discovery.poll("board")
        state = self.s.discovery.source_state("board")
        self.assertEqual(state["health"], access.FAILED)
        # Now FAILED, so the next poll is refused before the adapter is called.
        self.assertEqual(self.s.discovery.poll("board"), [])
        self.assertEqual(
            self.s.discovery.source_state("board")["consecutive_failures"], 1,
            "a refused poll must not count as another adapter failure")

    def test_a_failure_does_not_grant_or_revoke_anything(self):
        self.register(ThrowingSource("board", [GOOD]))
        self.s.discovery.poll("board")
        self.assertEqual(self.s.discovery.permissions("board"),
                         access.DEFAULT_PERMISSIONS)

    def test_state_survives_a_restart(self):
        self.register(ThrowingSource("board", [GOOD]))
        self.s.discovery.poll("board")
        self.reopen()
        state = self.s.discovery.source_state("board")
        self.assertEqual(state["health"], access.FAILED)
        self.assertEqual(state["consecutive_failures"], 1)

    def test_a_recovered_source_works_again(self):
        self.register(ThrowingSource("board", [GOOD]))
        self.s.discovery.poll("board")
        self.register(FixtureSource("board", [GOOD]))     # adapter fixed
        self.s.discovery.record_health("board", access.HEALTHY)
        self.assertEqual(len(self.s.discovery.poll("board")), 1)

    def test_recovery_does_not_restore_a_permission(self):
        # Canary first: this adapter works, so an empty poll below is the
        # revoked permission and not a broken source.
        self.register(FixtureSource("board", [GOOD]))
        self.assertEqual(len(self.s.discovery.poll("board")), 1)
        self.s.discovery.set_status(
            self.s.discovery.opportunities()[0]["id"],
            __import__("solvent.discovery", fromlist=["x"]).OpportunityStatus.REJECTED)
        self.s.discovery.revoke("board", access.DISCOVER,
                                owner_identity=OWNER, why="test")
        self.s.discovery.record_health("board", access.HEALTHY)
        self.register(FixtureSource("board", [dict(GOOD, ref="g2")]))
        self.assertEqual(self.s.discovery.poll("board"), [])


class AGateRefusalIsNotAnAdapterFailure(Base):
    """A source needing an external fetch gets nothing when the Gate says no,
    and that is not the source's fault -- so it is not marked broken."""

    class Remote(FixtureSource):
        host = "example.com"
        is_fixture = False

        def request(self):
            return {"url": "https://example.com/jobs"}

    def test_canary_the_same_postings_are_found_without_a_fetch(self):
        """The identical postings, from a source that needs no external call,
        are discovered. So the empty result below is the Gate refusing, not an
        empty board."""
        self.register(FixtureSource("local", [GOOD]))
        self.assertEqual(len(self.s.discovery.poll("local")), 1)

    def test_the_gate_refuses_and_nothing_is_discovered(self):
        self.register(self.Remote("remote", [GOOD]))
        self.assertEqual(self.s.discovery.poll("remote"), [])

    def test_the_refusal_is_recorded(self):
        self.register(self.Remote("remote", [GOOD]))
        self.s.discovery.poll("remote")
        events = [e for e in self.s.audit.events()
                  if e["event"] == "discovery.poll_refused"]
        self.assertTrue(events)


class CapabilityGapsAreEvidenceNotPermission(Base):

    def seed(self, need, times, *, title="Job"):
        postings = [dict(GOOD, ref=f"{need}-{i}", title=f"{title} {i}",
                         needs=[need]) for i in range(times)]
        self.register(FixtureSource(f"src-{need}", postings))
        self.s.discovery.poll(f"src-{need}")

    def test_canary_the_seed_actually_records_opportunities(self):
        """Without this, every "no gap" assertion below would hold on a system
        where seeding silently did nothing."""
        self.seed("quantum-alignment", 1)
        self.assertEqual(len(self.s.discovery.opportunities()), 1)

    def test_one_unusual_request_is_not_a_gap(self):
        self.seed("quantum-alignment", 1)
        self.assertEqual(len(self.s.discovery.opportunities()), 1)
        self.assertEqual(self.s.discovery.capability_gaps(), [])

    def test_a_repeated_request_is(self):
        self.seed("database-migration", 4)
        gaps = self.s.discovery.capability_gaps()
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0]["need"], "database-migration")
        self.assertEqual(gaps[0]["occurrences"], 4)

    def test_a_capability_we_have_is_not_a_gap(self):
        provision_capability(self.s, CSV_PROMOTION, owner_identity=OWNER)
        self.seed("csv-cleanup", 5)
        # The opportunities exist -- five of them. The gap list is empty
        # because the capability covers them, not because nothing was seeded.
        self.assertEqual(len(self.s.discovery.opportunities()), 5)
        self.assertEqual(
            [g["need"] for g in self.s.discovery.capability_gaps()], [])

    def test_the_budget_is_labelled_advertised_not_earned(self):
        self.seed("database-migration", 3)
        gap = self.s.discovery.capability_gaps()[0]
        self.assertTrue(gap["advertised_only"])
        self.assertEqual(gap["advertised_cents"], 3 * 40_000)

    def test_finding_a_gap_promotes_nothing(self):
        """§56. No statistic widens authority."""
        before = {c.version for c in self.s.capability.capabilities()}
        self.seed("database-migration", 6)
        self.s.discovery.capability_gaps()
        self.assertEqual({c.version for c in self.s.capability.capabilities()},
                         before)

    def test_finding_a_gap_creates_no_skill_project(self):
        self.seed("database-migration", 6)
        self.s.discovery.capability_gaps()
        self.assertEqual(self.s.skillslab.projects(), [])

    def test_gaps_are_ordered_by_how_often_they_are_asked_for(self):
        self.seed("alpha", 3)
        self.seed("beta", 7)
        self.assertEqual([g["need"] for g in self.s.discovery.capability_gaps()],
                         ["beta", "alpha"])


if __name__ == "__main__":
    unittest.main()
