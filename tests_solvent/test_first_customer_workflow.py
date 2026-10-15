"""The whole route to a first customer, end to end, with no live bid.

This is the supervised path as it actually stands: a posting DeskPilot
may not fetch, brought in by the owner instead, priced under probation
as a recommendation rather than a bid, approved by the owner, then
executed, verified, delivered, invoiced and reconciled.

It is one test class per link in that chain, and the links are checked
in the order the work happens, because the interesting failures are
ones where a later step quietly works without an earlier one.

Nothing here contacts a marketplace. The one fixture that came from a
real platform was recorded before its user agreement was read, and is
replayed from disk.
"""

from __future__ import annotations

import json
import pathlib
import tempfile
import unittest

from solvent import sourcecatalog as catalogue
from solvent import sourceaccess as access
from solvent.discovery import Compliance, ManualSource, Readiness
from solvent.finder import ExecutionHistory, Finder
from solvent.harness import (OWNER, Solvent, owner_promotions,
                             promote_capability, run_business_cycle)
from solvent.marketplaces import FreelancerActiveProjects
from solvent.types import JOBS_PER_AGENT_CEILING
from tests_solvent.fixtures import give_an_issuer
from tests_solvent.skillmatrix import (ASK, _generate, _requirements_for,
                                       _source, expected_outcome)

FIXTURE = (pathlib.Path(__file__).parent / "fixtures_marketplace"
           / "freelancer_active.json")

SKILL = "csv-cleanup"
GRANT = {"max_jobs": 2, "max_value_cents": 30_000,
         "min_deadline_hours": 48}
COMMERCIAL = {
    "hourly_rate_cents": 4_000, "minimum_project_fee_cents": 4_000,
    "minimum_margin_fraction": 0.30, "platform_fee_fraction": 0.0,
    "platform_fee_minimum_cents": 0, "payment_fee_fraction": 0.029,
    "payment_fee_flat_cents": 30, "compute_cents_per_cpu_hour": 50,
    "market_hourly_cents": {SKILL: 4_000},
    "market_rate_citations": {SKILL: "owner's own rate, recorded for this test"},
}
CAPACITY = {"max_owed_seconds": 100_000.0, "cpu_share": 0.5,
            "max_open_bids": 2, "min_free_disk_bytes": 0}


def recorded() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class Base(unittest.TestCase):

    def setUp(self):
        self.box = tempfile.TemporaryDirectory()
        self.addCleanup(self.box.cleanup)
        self.dir = pathlib.Path(self.box.name)
        self.s = Solvent(str(self.dir / "first.db"))
        self.addCleanup(self.close)
        give_an_issuer(self.s)
        for name in owner_promotions():
            promote_capability(self.s, name, owner_identity=OWNER,
                               why="test: cleared for client work")
        self.s.discovery.register_source(
            ManualSource("owner_entered", []), owner_identity=OWNER,
            readiness=Readiness.PERMITTED_AUTOMATION,
            compliance=Compliance.PERMITTED,
            determination="owner-entered: no platform terms involved")
        self.s.policy.amend({
            "discovery": {"approved_sources": ["owner_entered"]},
            "commercial": COMMERCIAL,
            "capacity": CAPACITY,
            "qualification": {"minimum_value_cents": 1_000},
            "egress": {"allowlist": [{"host": "client.example.com",
                                      "max_privacy": "CLIENT_CONFIDENTIAL",
                                      "classes": ["C2_EXTERNAL_COMMUNICATION"]}],
                       "simulation_only": True},
        }, OWNER, "test: first-customer route")

    def close(self):
        try:
            self.s.store.close()
        except Exception:
            pass

    def finder(self):
        return Finder(self.s.discovery, self.s.policy, self.s.capability,
                      self.s.orchestrator, self.s.store)


# 1 --------------------------------------------------------------------

class TheMarketplaceRouteIsShut(Base):
    """Before anything else: DeskPilot may not fetch this itself."""

    def test_the_catalogue_records_the_agreement_not_the_endpoint(self):
        entry = catalogue.BY_ID["freelancer"]
        status, why = catalogue.operational_status(entry)
        self.assertEqual(status, catalogue.BLOCKED_PLATFORM_RESTRICTION)
        self.assertIn("express written permission", why)

    def test_discovery_refuses_the_live_source(self):
        allowed, why = self.s.discovery.may("freelancer", access.DISCOVER)
        self.assertFalse(allowed)
        self.assertTrue(why.strip())

    def test_submitting_a_bid_there_is_refused_too(self):
        """Refused here for not being registered at all, which is the
        first of two independent reasons. The second -- the agreement
        -- is what refuses it once a source by that name exists, and
        test_marketplace_permissions covers that case."""
        allowed, why = self.s.discovery.may("freelancer", access.SUBMIT_BID)
        self.assertFalse(allowed)
        self.assertTrue(why.strip())

    def test_the_adapter_still_parses_what_was_recorded(self):
        """Shut is not broken: the code is ready, the permission is not."""
        found = FreelancerActiveProjects().parse(recorded())
        self.assertEqual(len(found), 3)
        self.assertTrue(all(o.quoted_cents > 0 for o in found))


# 2 --------------------------------------------------------------------

class TheOwnerBringsTheWorkInstead(Base):

    def test_the_manual_route_is_permitted(self):
        """IMPORT, not DISCOVER: the owner brings the work, DeskPilot
        does not go looking for it. They are separate permissions and
        the manual route only ever needed the first."""
        allowed, why = self.s.discovery.may("owner_entered", access.IMPORT)
        self.assertTrue(allowed, why)

    def test_it_is_still_not_permission_to_go_looking(self):
        allowed, _ = self.s.discovery.may("owner_entered", access.DISCOVER)
        self.assertFalse(allowed)

    def test_it_needs_no_platform_determination(self):
        entry = catalogue.BY_ID["owner_entered"]
        status, _ = catalogue.operational_status(entry)
        self.assertEqual(status, catalogue.ESTABLISHED_MANUAL_IMPORT)


# 3 --------------------------------------------------------------------

class ProbationPricesItAsARecommendation(Base):

    def opportunity(self):
        from solvent.content import quarantine
        from solvent.discovery import Opportunity
        from solvent.types import PrivacyClass

        return Opportunity(
            id="o1", source="owner_entered", external_ref="manual-1",
            title="A file the owner was sent", quoted_cents=25_000,
            needs=(SKILL,), payment_structure="FIXED",
            posting=quarantine("A spreadsheet to tidy.",
                               source="owner_entered",
                               privacy=PrivacyClass.PUBLIC))

    def benchmarks(self):
        return ExecutionHistory({SKILL: [0.0009] * 7})

    def test_without_a_grant_nothing_is_quoted(self):
        bid = self.finder().bid(self.opportunity(),
                                history=ExecutionHistory(),
                                benchmarks=self.benchmarks())
        self.assertFalse(bid.recommended)
        self.assertFalse(bid.submittable)

    def test_with_a_grant_it_is_recommended_and_not_submittable(self):
        self.s.policy.grant_probation(owner_identity=OWNER,
                                      capability=SKILL, **GRANT)
        bid = self.finder().bid(self.opportunity(),
                                history=ExecutionHistory(),
                                benchmarks=self.benchmarks())
        self.assertTrue(bid.recommended, bid.why)
        self.assertFalse(bid.submittable)
        self.assertTrue(bid.probationary)
        self.assertIn("Submit it yourself", bid.why)


# 4 --------------------------------------------------------------------

class TheJobRunsAllTheWayThrough(Base):
    """Qualification to reconciliation, on a real generated job."""

    def cycle(self):
        # A case meant to be delivered. Several generated csv-cleanup
        # cases are deliberately ambiguous -- dates like 03/10/2025 --
        # and their correct outcome is a question to the client, not a
        # delivery. Running one of those here would have measured how
        # fast DeskPilot stops.
        for index in range(12):
            case = _generate(SKILL, 7717, index)
            if expected_outcome(SKILL, case) != ASK:
                break
        else:
            self.skipTest("no deliverable case in the first twelve")
        requirements = _requirements_for(SKILL, case)
        source = self.dir / (case.source_name or "source.csv")
        source.write_bytes(_source(case))
        return run_business_cycle(
            self.s, source_name="owner_entered", source_file=str(source),
            workdir=str(self.dir), capability=SKILL,
            posting={"ref": "manual-1", "title": "Tidy a file",
                     "quoted_cents": 25_000, "needs": [SKILL],
                     "project_state": "CT", "client_ref": "client:first",
                     "body": "owner-entered", "deliverables": []},
            agreed_requirements=requirements,
            owner_accepts_quote=True, simulate_payment=True)

    def setUp(self):
        super().setUp()
        self.s.policy.grant_probation(owner_identity=OWNER,
                                      capability=SKILL, **GRANT)
        self.result = self.cycle()

    def test_it_completed(self):
        self.assertTrue(
            self.result.completed,
            f"stopped at {self.result.stopped_at}: {self.result.why_stopped}")

    def test_it_was_verified_before_delivery(self):
        self.assertTrue(self.result.delivered)
        self.assertGreaterEqual(
            self.result.verification_rounds, 1,
            "delivered with no verification round, so the tier was never "
            "actually satisfied")
        self.assertTrue(self.result.tier_reachable, self.result.tier_detail)

    def test_an_invoice_was_issued(self):
        self.assertGreater(self.result.invoiced_cents, 0)

    def test_the_payment_reconciled_and_is_marked_simulated(self):
        self.assertEqual(
            self.s.ledger.real_revenue_cents(), 0,
            "simulated money was counted as real revenue")

    def test_no_bid_was_submitted_anywhere(self):
        attempts = self.s.applications.attempts()
        self.assertEqual(attempts, [],
                         f"a submission was attempted: {attempts}")


# 5 --------------------------------------------------------------------

class TheRunBecomesRealHistory(TheJobRunsAllTheWayThrough):
    """The point of the whole exercise."""

    def history(self):
        return ExecutionHistory.from_store(self.s.store)

    def test_the_execution_was_measured_as_client_work(self):
        self.assertGreaterEqual(self.history().count(SKILL), 1)

    def test_that_measurement_is_not_a_benchmark(self):
        rows = self.s.store.raw_readonly(
            "SELECT outcome FROM execution_measurements WHERE capability = ?",
            (SKILL,))
        outcomes = {r["outcome"] for r in rows}
        self.assertTrue(outcomes)
        self.assertNotIn("BENCHMARK", outcomes)

    def test_one_run_is_not_yet_enough_to_bid_on(self):
        """Known, but provisional. Five is where it stops being so."""
        estimate = self.finder().completion_estimate(SKILL,
                                                     history=self.history())
        self.assertTrue(estimate.known)
        self.assertEqual(estimate.confidence.name, "PROVISIONAL")

    def test_probation_is_still_in_force_at_one_run(self):
        grant = self.finder().probation(SKILL, history=self.history())
        self.assertTrue(grant.granted)

    def test_five_client_runs_end_probation(self):
        plenty = ExecutionHistory({SKILL: [60.0] * 5})
        grant = self.finder().probation(SKILL, history=plenty)
        self.assertFalse(grant.granted)
        self.assertIn("no longer needs probation", grant.why)


# 6 --------------------------------------------------------------------

class TheLimitsHeldThroughout(Base):

    def test_three_jobs_by_default(self):
        self.assertEqual(self.s.capacity.budget().max_concurrent_jobs, 3)
        taken = sum(1 for n in range(8)
                    if self.s.capacity.admit(f"j{n}", execute_seconds=10,
                                             idempotency_key=f"k{n}").allowed)
        self.assertEqual(taken, 3)

    def test_four_is_the_ceiling(self):
        self.s.policy.amend(
            {"capacity": {"max_concurrent_jobs": JOBS_PER_AGENT_CEILING + 1}},
            OWNER, "test: an optimistic owner")
        self.assertFalse(self.s.capacity.room_for(1.0).allowed)

    def test_simulation_was_never_turned_off(self):
        self.assertTrue(self.s.policy.get("egress", "simulation_only",
                                          default=False))
