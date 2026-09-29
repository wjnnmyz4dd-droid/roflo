"""§5. Finding Work and Inserted Work are different roles, permanently.

The failure this guards against is content laundering. Owner-confirmed terms
may gate a commitment; a board posting may not. If the two roles ever became
interchangeable, attacker-authored text arriving over the network could be
recorded with the one provenance that lets it commit the business.

So these are permanent regression tests, not a one-off check of this pass.
"""

from __future__ import annotations

import pathlib
import tempfile
import unittest

from solvent import intake
from solvent import sourceaccess as access
from solvent.discovery import (Compliance, FixtureSource, ManualSource,
                               Readiness)
from solvent.errors import FailClosed
from solvent.harness import OWNER, Solvent

POSTING = {"ref": "p1", "title": "Deduplicate a CSV", "quoted_cents": 40_000,
           "needs": ["csv-cleanup"], "body": "Please clean this file."}


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.db = str(pathlib.Path(self.dir.name) / "solvent.db")
        self.s = Solvent(self.db)
        self.addCleanup(self.close)
        self.found = self.register("board", FixtureSource)
        self.inserted = self.register("by_hand", ManualSource)

    def close(self):
        try:
            self.s.store.close()
        except Exception:
            pass

    def register(self, name, kind):
        source = kind(name, [dict(POSTING, ref=f"{name}-1")])
        self.s.discovery.register_source(
            source, owner_identity=OWNER,
            readiness=Readiness.PERMITTED_AUTOMATION,
            compliance=Compliance.PERMITTED, determination="fixture")
        approved = self.s.policy.get("discovery", "approved_sources", default=[])
        self.s.policy.amend(
            {"discovery": {"approved_sources": sorted({*approved, name})}},
            OWNER, "approve")
        return name

    def state(self, name):
        return self.s.discovery.source_state(name)


class TheRolesAreDistinct(Base):

    def test_a_board_is_found_work(self):
        self.assertEqual(intake.role_of(self.state("board")), intake.FOUND)

    def test_a_manual_source_is_inserted_work(self):
        self.assertEqual(intake.role_of(self.state("by_hand")), intake.INSERTED)

    def test_canary_both_roles_actually_produce_work(self):
        """Without this every refusal below could hold on sources that never
        produced anything at all."""
        self.assertEqual(len(self.s.discovery.poll("board")), 1)
        self.assertEqual(
            len(self.s.discovery.insert("by_hand", owner_identity=OWNER)), 1)


class FindingWorkCannotBecomeInsertedWork(Base):

    def test_a_found_source_cannot_insert(self):
        with self.assertRaises(FailClosed) as caught:
            self.s.discovery.insert("board", owner_identity=OWNER)
        self.assertIn("may not record work as owner-entered",
                      str(caught.exception))

    def test_found_work_is_not_recorded_as_owner_entered(self):
        found = self.s.discovery.poll("board")
        self.assertFalse(found[0].owner_entered)

    def test_found_provenance_names_the_source_not_the_owner(self):
        self.s.discovery.poll("board")
        row = self.s.discovery.opportunities()[0]
        self.assertEqual(row["discovered_via"], "board")
        self.assertNotEqual(row["discovered_via"], "owner")

    def test_a_remote_adapter_cannot_nominate_its_own_role(self):
        """The adapter is asked nothing. The role comes from the registration
        record, which the owner wrote."""
        class Liar(FixtureSource):
            kind = "manual"                 # claims to be inserted
            owner_entered_source = True

        liar = Liar("liar", [dict(POSTING, ref="liar-1")])
        liar.kind = "manual"
        self.s.discovery.register_source(
            liar, owner_identity=OWNER,
            readiness=Readiness.PERMITTED_AUTOMATION,
            compliance=Compliance.PERMITTED, determination="claims manual")
        # Registered as manual, so it is an inserted source -- and therefore
        # may not be polled at all. It cannot have it both ways.
        self.assertEqual(self.s.discovery.poll("liar"), [])


class InsertedWorkCannotDiscover(Base):

    def test_an_inserted_source_may_not_be_polled(self):
        self.assertEqual(self.s.discovery.poll("by_hand"), [])

    def test_the_refusal_names_the_role_not_some_other_control(self):
        """§35. A mutant deleting the role check must not survive behind an
        unrelated refusal."""
        allowed, why = self.s.discovery._may_poll(
            self.s.discovery._sources["by_hand"])
        self.assertFalse(allowed)
        self.assertIn("inserted work is typed in, not fetched", why)

    def test_an_inserted_source_is_not_stored_holding_discover(self):
        self.assertNotIn(access.DISCOVER,
                         self.s.discovery.permissions("by_hand"))

    def test_a_found_source_is(self):
        self.assertIn(access.DISCOVER, self.s.discovery.permissions("board"))

    def test_granting_discover_to_an_inserted_source_still_does_not_poll_it(self):
        """Two independent controls, so removing either is visible."""
        self.s.discovery.grant("by_hand", access.DISCOVER,
                               owner_identity=OWNER, why="test")
        self.assertIn(access.DISCOVER, self.s.discovery.permissions("by_hand"))
        self.assertEqual(self.s.discovery.poll("by_hand"), [])

    def test_inserting_makes_no_gate_request(self):
        before = len(self.s.store.raw_readonly("SELECT id FROM action_requests"))
        self.s.discovery.insert("by_hand", owner_identity=OWNER)
        after = len(self.s.store.raw_readonly("SELECT id FROM action_requests"))
        self.assertEqual(after, before, "inserted work reached the network")

    def test_only_the_owner_may_insert(self):
        with self.assertRaises(FailClosed) as caught:
            self.s.discovery.insert("by_hand", owner_identity="discovery")
        self.assertIn("owner action", str(caught.exception))

    def test_discovery_cannot_insert_on_its_own_behalf(self):
        for identity in ("discovery", "skillslab", "client:x", "gate"):
            with self.subTest(identity=identity):
                with self.assertRaises(FailClosed):
                    self.s.discovery.insert("by_hand", owner_identity=identity)


class NeitherRoleModifiesTheOther(Base):

    def test_polling_does_not_change_the_inserted_sources_permissions(self):
        before = self.s.discovery.permissions("by_hand")
        self.s.discovery.poll("board")
        self.assertEqual(self.s.discovery.permissions("by_hand"), before)

    def test_inserting_does_not_change_the_found_sources_permissions(self):
        before = self.s.discovery.permissions("board")
        self.s.discovery.insert("by_hand", owner_identity=OWNER)
        self.assertEqual(self.s.discovery.permissions("board"), before)

    def test_a_found_source_failing_does_not_disable_insertion(self):
        self.s.discovery.record_health("board", access.FAILED, reason="500")
        self.assertEqual(
            len(self.s.discovery.insert("by_hand", owner_identity=OWNER)), 1)

    def test_neither_inherits_the_others_credentials(self):
        self.s.discovery.set_credential_configured("board", True,
                                                   owner_identity=OWNER)
        self.assertEqual(
            self.state("by_hand")["credential_configured"], 0,
            "manual insertion inherited a marketplace credential")


class BothConvergeOnTheSameDownstreamPipeline(Base):
    """The other half of §4: separate intake, one business pipeline."""

    def setUp(self):
        super().setUp()
        self.s.discovery.poll("board")
        self.s.discovery.insert("by_hand", owner_identity=OWNER)
        self.rows = {r["discovered_via"]: r
                     for r in self.s.discovery.opportunities()}

    def test_both_produced_an_opportunity(self):
        self.assertEqual(set(self.rows), {"board", "owner"})

    def test_both_are_screened(self):
        for row in self.rows.values():
            self.assertIn("risk_signals", row)

    def test_both_carry_provenance(self):
        for row in self.rows.values():
            self.assertTrue(row["evidence_ref"])
            self.assertTrue(row["source"])

    def test_both_are_deduplicated(self):
        self.assertEqual(self.s.discovery.poll("board"), [])
        self.assertEqual(
            self.s.discovery.insert("by_hand", owner_identity=OWNER), [])

    def test_both_land_in_one_opportunities_table(self):
        self.assertEqual(len(self.s.discovery.opportunities()), 2)

    def test_the_same_job_from_both_paths_is_not_silently_merged(self):
        """§81. Two intake roles seeing one job is a real possibility, and so
        is one client posting twice. Guessing wrong either loses work or does
        it twice, so the rows stay distinct and the owner can see both."""
        self.s.discovery.insert("by_hand", owner_identity=OWNER,
                                postings=[dict(POSTING, ref="board-1")])
        rows = self.s.discovery.opportunities()
        self.assertEqual(len(rows), 3)
        by_source = [r["source"] for r in rows]
        self.assertIn("board", by_source)
        self.assertIn("by_hand", by_source)

    def test_the_duplicate_keeps_its_own_provenance(self):
        self.s.discovery.insert("by_hand", owner_identity=OWNER,
                                postings=[dict(POSTING, ref="board-1")])
        inserted = [r for r in self.s.discovery.opportunities()
                    if r["source"] == "by_hand"]
        self.assertTrue(all(r["discovered_via"] == "owner" for r in inserted))


if __name__ == "__main__":
    unittest.main()
