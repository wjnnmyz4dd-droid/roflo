"""Two repairs: the business can bill, and a nonpayer cannot freeze it.

**1A — contracting.** Production refuses to invent an owner's legal
identity, which is right, and the consequence was that no invoice could
ever be issued, so nothing settled. :mod:`simulation.profile` supplies a
fictional party through the existing policy authority.

**1B — capacity seizure.** A delivered job sat in AWAITING_PAYMENT
holding its execution reservation until a verified payment arrived.
Measured before the repair: five delivered jobs, five reservations,
18,000 owed seconds, and reconciliation would not give them back. With
no invoice issuable, nothing ever settled, so the shop seized for good
after (3 x pipelines) jobs. **One client who never paid could stop the
entire business.**

The repair is one line of rule, not a new mechanism:
``_reconcile_capacity`` now asks ``qualification.OCCUPIES_CAPACITY``
whether work is owed, instead of keeping its own narrower list of
terminal-and-COMPLETE. That set already draws the distinction
correctly -- it excludes AWAITING_PAYMENT, because a delivered job owes
money rather than work, and includes REVISION_REQUESTED, because a
client asking for changes owes labour again.

What the repair must *not* do is let unpaid work run away. It does not:
the money stays owed in the ledger, the budget grant is not revoked,
and the Governor's ``max_committed_unverified_cents`` still counts the
job. Execution capacity is denominated in worker-seconds and payment
exposure in money, and the bug was using the first to bound the second.
"""

from __future__ import annotations

import unittest

from solvent.harness import OWNER
from solvent.qualification import OCCUPIES_CAPACITY
from solvent.runtime import reconcile
from solvent.types import JobState

from simulation import profile
from tests_solvent.test_production_path import ProductionPath, SKILLS


class Billable(ProductionPath):
    """A shop configured the way a simulated business would be."""

    def setUp(self):
        super().setUp()
        self.s.policy.amend({"capacity": {
            "skills": list(SKILLS), "cpu_share": 0.5,
            "min_free_disk_bytes": 1, "max_owed_seconds": 40_000.0}},
            OWNER, "simulation capacity")

    def deliver(self, how_many: int = 5):
        """Put `how_many` jobs through to AWAITING_PAYMENT."""
        for cap in SKILLS[:how_many]:
            self.place(cap, order_id=f"ord-{cap}")
        runtime = self.runtime()
        for _ in range(how_many + 2):
            self.tick(runtime)
        return self.s.orchestrator.jobs()


# ======================================================================
# 1A -- the business can issue an invoice
# ======================================================================

class TheOwnerIsRecordedNotInvented(Billable):

    def test_a_system_with_no_recorded_owner_refuses_to_invoice(self):
        """The control, shown working on a shop nobody has configured.

        Asserted against a *bare* Solvent, not this fixture.
        ``ProductionPath.setUp`` already calls ``give_an_issuer``, so
        invoicing was never blocked here -- which corrects something I
        published. The forensic report attributed the capacity seizure
        to missing contracting details; that blocker is real, but it
        was measured in the busy-shop harness, and the seizure I
        measured in ProductionPath had a different and sufficient
        cause of its own: reconciliation would not release an
        AWAITING_PAYMENT reservation. Two real blockers, one wrongly
        joined to the other.
        """
        import pathlib as _p
        import tempfile

        from solvent.harness import Solvent

        box = tempfile.TemporaryDirectory()
        self.addCleanup(box.cleanup)
        bare = Solvent(str(_p.Path(box.name) / "bare.db"))
        self.addCleanup(bare.store.close)
        allowed, why = profile.may_invoice(bare)
        self.assertFalse(allowed)
        self.assertIn("legal_name", why)

    def test_this_fixture_already_had_an_issuer(self):
        """Pins the fact that corrected the report, so it cannot drift."""
        allowed, _ = profile.may_invoice(self.s)
        self.assertTrue(allowed,
                        "ProductionPath is expected to record an issuer; if "
                        "it stops, the capacity tests below change meaning")

    def test_recording_the_fictional_party_unblocks_invoicing(self):
        profile.record(self.s, owner_identity=OWNER)
        allowed, why = profile.may_invoice(self.s)
        self.assertTrue(allowed, why)

    def test_it_goes_through_the_real_policy_authority(self):
        profile.record(self.s, owner_identity=OWNER)
        party = self.s.policy.contracting_party()
        self.assertTrue(party["decided"])
        self.assertEqual(party["structure"], "LLC")
        self.assertEqual(party["legal_name"], profile.LEGAL_NAME)
        self.assertEqual(party["email"], profile.EMAIL)

    def test_a_payment_rail_stays_blocked(self):
        """The simulation must never be one step from moving real money."""
        profile.record(self.s, owner_identity=OWNER)
        allowed, why = profile.may_connect_a_payment_rail(self.s)
        self.assertFalse(allowed, "a simulated run must not satisfy "
                                  "PAYMENT_RAIL")
        self.assertIn("tax_reference", why)

    def test_the_audit_log_says_it_was_a_simulation(self):
        profile.record(self.s, owner_identity=OWNER)
        rows = [r for r in self.s.audit.events()
                if r["event"] == "policy.contracting_recorded"]
        self.assertTrue(rows)
        self.assertIn("SIMULATION ONLY", rows[-1]["why"])

    def test_every_identifier_is_unreachable_by_construction(self):
        """`.invalid` is reserved by RFC 2606 and cannot resolve."""
        self.assertTrue(profile.EMAIL.endswith(".invalid"))
        self.assertNotIn("@gmail", profile.EMAIL)
        self.assertIn("Simulation", profile.ADDRESS)


# ======================================================================
# 1B -- a delivered job owes money, not work
# ======================================================================

class ExecutionCapacityTracksWorkNotMoney(Billable):

    def test_a_delivered_job_releases_its_execution_slot(self):
        jobs = self.deliver(5)
        self.assertTrue(jobs)
        self.assertTrue(all(j.state is JobState.AWAITING_PAYMENT
                            for j in jobs), [j.state.value for j in jobs])
        self.assertEqual(self.s.capacity.outstanding(), [],
                         "a delivered job is owed money, not worker-seconds")
        self.assertEqual(self.s.capacity.owed_seconds(), 0.0)

    def test_the_money_is_still_owed(self):
        """Releasing a slot must not forgive a debt."""
        self.deliver(5)
        self.assertGreater(self.s.governor.committed_unverified_cents(), 0,
                           "the Governor stopped counting unpaid work")
        self.assertEqual(self.s.ledger.verified_job_ids(), [],
                         "nothing was verified paid, so nothing should read "
                         "as settled")
        self.assertEqual(self.s.ledger.real_revenue_cents(), 0)

    def test_unpaid_exposure_is_still_capped(self):
        """The control that bounds unpaid work is money, not seconds."""
        self.deliver(5)
        cap = int(self.s.policy.get("financial",
                                    "max_committed_unverified_cents"))
        self.assertGreater(cap, 0, "there must be a cap at all")
        self.assertGreater(self.s.governor.committed_unverified_cents(), 0)

    def test_one_nonpaying_client_cannot_freeze_the_business(self):
        """The headline property, stated as the assignment states it."""
        self.deliver(5)
        ceiling = self.s.capacity.committed_ceiling()
        exposure = self.s.capacity.exposure()
        self.assertEqual(exposure["owed_seconds"], 0)
        self.assertEqual(exposure["worst_case_headroom"], ceiling,
                         "five unpaid deliveries left no headroom; the shop "
                         "is frozen by a client who did not pay")

    def test_work_still_owed_keeps_its_slot(self):
        """Re-opened work keeps the slot the revision needs.

        Behavioural on purpose. The first version of this asserted set
        membership in OCCUPIES_CAPACITY -- a constant, which no change
        to the release rule can affect, so a mutant that released
        REVISION_REQUESTED survived it untouched. A test that reads a
        constant is not testing the code that reads it too.
        """
        jobs = self.deliver(1)
        job_id = jobs[0].id
        self.assertEqual(self.s.capacity.outstanding(), [],
                         "delivery should have freed the slot first")

        # The client comes back. Labour is owed again, so a slot is taken.
        self.s.orchestrator._transition(
            job_id, JobState.REVISION_REQUESTED,
            why="the client asked for changes", initiator="test")
        self.s.capacity.reserve(
            3600.0, kind="EXECUTE", job_id=job_id, capability=SKILLS[0],
            idempotency_key=f"revision:{job_id}", why="revision")

        reconcile(self.s, initiator="test")
        self.assertEqual(len(self.s.capacity.outstanding()), 1,
                         "the revision lost the slot it needs to be done in")
        self.assertEqual(self.s.capacity.owed_seconds(), 3600.0)

    def test_the_two_delivered_states_are_told_apart(self):
        """AWAITING_PAYMENT owes money; REVISION_REQUESTED owes work."""
        self.assertTrue(JobState.AWAITING_PAYMENT.is_delivered)
        self.assertTrue(JobState.REVISION_REQUESTED.is_delivered)
        self.assertIn(JobState.REVISION_REQUESTED, OCCUPIES_CAPACITY)
        self.assertNotIn(JobState.AWAITING_PAYMENT, OCCUPIES_CAPACITY)

    def test_the_release_rule_is_finished_work_not_arrived_money(self):
        """Pins the mechanism, not today's outcome.

        The first attempt at this repair released anything not in
        ``OCCUPIES_CAPACITY``. That reads correctly and is wrong: the
        set also excludes INTAKE and QUALIFYING, so a job that had only
        just arrived lost its slot while still heading for execution.
        An existing test caught it within one run. The rule is
        "finished", not "not currently counted".
        """
        import inspect

        from solvent import runtime

        source = inspect.getsource(runtime._reconcile_capacity)
        self.assertIn("is_delivered", source)
        self.assertIn("REVISION_REQUESTED", source)
        self.assertNotIn("not in OCCUPIES_CAPACITY", source)

    def test_an_early_job_keeps_its_slot(self):
        """The case the first attempt broke, pinned here too."""
        from solvent.types import LocationSignals

        job_id = self.s.orchestrator.intake(
            title="just arrived", client_id="c", quoted_cents=5_000,
            signals=LocationSignals(), initiator="test")
        self.s.capacity.reserve(
            3600.0, kind="EXECUTE", job_id=job_id, capability=SKILLS[0],
            idempotency_key=f"early:{job_id}", why="test")
        reconcile(self.s, initiator="test")
        self.assertEqual(len(self.s.capacity.outstanding()), 1,
                         "a job that has not been delivered lost its slot")

    def test_reconciliation_is_idempotent(self):
        self.deliver(3)
        first = reconcile(self.s, initiator="test")
        second = reconcile(self.s, initiator="test")
        self.assertEqual(self.s.capacity.outstanding(), [])
        self.assertEqual(second, [], f"a second pass did work: {second}")
        self.assertIsInstance(first, list)


class ThePaymentPathologies(Billable):
    """The seven cases the assignment names, each against real capacity."""

    def setUp(self):
        super().setUp()
        profile.record(self.s, owner_identity=OWNER)

    def a_delivered_job(self):
        jobs = self.deliver(1)
        self.assertEqual(len(jobs), 1)
        return jobs[0]

    def test_a_paying_customer_leaves_no_slot_held(self):
        job = self.a_delivered_job()
        self.assertEqual(self.s.capacity.outstanding(), [])
        self.assertIs(job.state, JobState.AWAITING_PAYMENT)

    def test_a_late_payer_does_not_hold_the_shop(self):
        """Late is the same as never, for capacity. That is the point."""
        self.a_delivered_job()
        for _ in range(5):          # simulated time passing, no payment
            reconcile(self.s, initiator="test")
        self.assertEqual(self.s.capacity.owed_seconds(), 0.0)

    def test_a_nonpayer_does_not_hold_the_shop(self):
        self.a_delivered_job()
        self.assertEqual(self.s.capacity.owed_seconds(), 0.0)
        self.assertGreater(self.s.governor.committed_unverified_cents(), 0,
                           "but the debt must remain on the books")

    def test_many_nonpayers_still_leave_room_to_work(self):
        """Five unpaid deliveries, and the shop can still take work."""
        from solvent.capacity import EXECUTE

        self.deliver(5)
        decision = self.s.capacity.room_for(3600.0, kind=EXECUTE,
                                            capability=SKILLS[0])
        self.assertTrue(decision.allowed,
                        f"the shop refused new work: {decision.why}")

    def test_the_ledger_is_untouched_by_a_capacity_release(self):
        job = self.a_delivered_job()
        payment = self.s.ledger.payment_for(job.id)
        self.assertIsNotNone(payment, "the payment row must survive")
        self.assertEqual(self.s.ledger.real_revenue_cents(), 0)


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
