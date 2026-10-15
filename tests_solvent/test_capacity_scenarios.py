"""The nine situations the owner asked DeskPilot to survive.

The invariants in test_capacity.py pin individual rules. These pin
behaviour under the conditions that actually break a job shop: more
offers than hands, bids that all land at once, customers who come back,
work that runs long, a marketplace that stops answering, a machine that
is also somebody else's, and a restart in the middle of everything.

Every test here is written so that it fails if the control it names is
removed. Where a scenario exposes a limitation rather than a defence,
the test asserts the limitation plainly and the docstring says so --
a test that quietly tolerates the thing it was written to catch is
worth less than no test.
"""

from __future__ import annotations

import tempfile
import unittest

from solvent import capacity as cap
from solvent.harness import OWNER, Solvent

#: A deliberately small shop: ten minutes of work owed at once, three
#: jobs at a time, two bids out, half the machine, and forty per cent
#: of every job held back for the revisions it will attract.
BUDGET = {"max_owed_seconds": 600.0, "max_concurrent_jobs": 3,
          "max_open_bids": 2, "cpu_share": 0.5, "min_free_disk_bytes": 0,
          "revision_headroom_fraction": 0.0, "halted": False}


class Base(unittest.TestCase):

    def setUp(self):
        self.box = tempfile.TemporaryDirectory()
        self.addCleanup(self.box.cleanup)
        self.db = f"{self.box.name}/scenarios.db"
        self.s = Solvent(self.db)
        self.addCleanup(self.close)
        self.configure()

    def close(self):
        try:
            self.s.store.close()
        except Exception:
            pass

    def configure(self, **over):
        budget = dict(BUDGET)
        budget.update(over)
        self.s.policy.amend({"capacity": budget}, OWNER, "test: budget")

    def reserve(self, seconds, *, kind=cap.EXECUTE, job="job", key=None,
                capability=""):
        return self.s.capacity.reserve(
            seconds, kind=kind, job_id=job,
            idempotency_key=key or f"{kind}:{job}:{seconds}",
            capability=capability)

    def offer(self, count, seconds=120.0, kind=cap.EXECUTE,
              capability=""):
        """Offer `count` jobs at once and report which were taken."""
        taken, refused = [], []
        for n in range(count):
            decision = self.reserve(seconds, kind=kind, job=f"j{n}",
                                    key=f"offer-{kind}-{n}",
                                    capability=capability)
            (taken if decision.allowed else refused).append(decision)
        return taken, refused


# ------------------------------------------------------------ scenario 1

class S1_TenFiftyAndOneHundredAtOnce(Base):
    """Ten, fifty and a hundred jobs arriving together."""

    def test_the_number_accepted_does_not_grow_with_the_number_offered(self):
        """The whole point. A hundred offers is not a hundred jobs."""
        accepted = {}
        for count in (10, 50, 100):
            self.setUp()
            taken, _ = self.offer(count)
            accepted[count] = len(taken)
        self.assertEqual(accepted[10], accepted[50])
        self.assertEqual(accepted[50], accepted[100])

    def test_the_concurrency_limit_is_what_binds(self):
        """With workers raised, only the job limit can refuse.

        Left at the default the worker limit falls back to this same
        number, so both controls refuse at three and removing either
        changes nothing -- which is how the first version of this test
        let a mutant through that ignored the job limit entirely.
        """
        # Workers at the ceiling, jobs at the default: the job limit
        # is the lower of the two, so it is the one that refuses.
        #
        # Five skills are configured so the *global* ceiling is
        # 5 x 3 = 15 while the per-pipeline limit stays 3. Without
        # that, both land on 3 -- a shop with no skills has a global
        # ceiling equal to its per-pipeline limit -- and the two
        # controls become indistinguishable, which let a mutant that
        # deleted the per-pipeline check survive. Two controls that
        # refuse at the same number hide each other, and this suite
        # has now found that shape three times.
        self.configure(max_execution_workers=4, max_concurrent_jobs=3,
                       skills=["csv-cleanup", "data-entry",
                               "document-processing", "report-builder",
                               "spreadsheet-automation"])
        self.assertEqual(self.s.capacity.committed_ceiling(), 15,
                         "the global ceiling equals the per-pipeline "
                         "limit, so this cannot tell them apart")
        taken, refused = self.offer(100, capability="csv-cleanup")
        self.assertEqual(len(taken), 3)
        self.assertEqual(len(refused), 97)
        self.assertEqual(self.s.capacity.jobs_in_flight(), 3)

    def test_owed_work_never_passes_the_budget_at_any_offer_volume(self):
        for count in (10, 50, 100):
            with self.subTest(offered=count):
                self.setUp()
                self.offer(count)
                self.assertLessEqual(self.s.capacity.owed_seconds(),
                                     BUDGET["max_owed_seconds"])

    def test_every_refusal_says_which_limit_it_hit(self):
        _, refused = self.offer(50)
        for decision in refused:
            self.assertIs(decision.refusal, cap.Refusal.NO_ROOM)
            self.assertTrue(decision.why.strip())

    def test_the_seconds_limit_binds_even_when_the_job_count_does_not(self):
        """One job can fill the budget on its own.

        Concurrency cannot be raised out of the way any more -- four
        jobs is the ceiling -- so this piles obligations onto a single
        job, which the job limit does not count twice. What stops it
        is the only thing left: the seconds.
        """
        taken = [self.reserve(70, kind=cap.REVISE, job="one", key=f"r{n}")
                 for n in range(100)]
        allowed = [d for d in taken if d.allowed]
        self.assertEqual(len(allowed), 8)        # 8 x 70 = 560 <= 600
        self.assertEqual(self.s.capacity.jobs_in_flight(), 1)
        self.assertLessEqual(self.s.capacity.owed_seconds(), 600.0)


# ------------------------------------------------------------ scenario 2

class S2_MultipleBidsAcceptedTogether(Base):
    """Several bids out; the client accepts them on the same morning."""

    def test_an_open_bid_consumes_room_before_anyone_accepts(self):
        self.reserve(280, kind=cap.BID, job="b1", key="bid1")
        decision = self.reserve(400, kind=cap.EXECUTE, job="j9", key="j9")
        self.assertFalse(decision.allowed,
                         "a bid that reserves nothing is a promise nobody "
                         "costed")
        self.assertIs(decision.refusal, cap.Refusal.NO_ROOM)

    def test_bids_beyond_the_open_limit_are_refused(self):
        self.assertTrue(self.reserve(100, kind=cap.BID, job="b1",
                                     key="bid1").allowed)
        self.assertTrue(self.reserve(100, kind=cap.BID, job="b2",
                                     key="bid2").allowed)
        third = self.reserve(100, kind=cap.BID, job="b3", key="bid3")
        self.assertFalse(third.allowed)
        self.assertIn("could be accepted tomorrow", third.why)

    def test_every_open_bid_landing_at_once_still_fits(self):
        """The reason bids reserve: simultaneous acceptance cannot fail."""
        bids = [self.reserve(280, kind=cap.BID, job=f"b{n}", key=f"bid{n}")
                for n in range(2)]
        self.assertTrue(all(b.allowed for b in bids))
        for n, bid in enumerate(bids):
            self.s.capacity.release(bid.reservation_id, "bid accepted")
            won = self.reserve(280, kind=cap.EXECUTE, job=f"b{n}",
                               key=f"won{n}")
            self.assertTrue(won.allowed,
                            "a bid was accepted that there was no room for")
        self.assertEqual(self.s.capacity.owed_seconds(), 560.0)

    def test_a_lost_bid_gives_its_room_back(self):
        bid = self.reserve(280, kind=cap.BID, job="b1", key="bid1")
        self.s.capacity.release(bid.reservation_id, "bid lost")
        self.assertEqual(self.s.capacity.owed_seconds(), 0.0)
        self.assertTrue(self.reserve(500, job="j1", key="j1").allowed)

    def test_room_is_not_handed_back_twice(self):
        bid = self.reserve(280, kind=cap.BID, job="b1", key="bid1")
        self.s.capacity.release(bid.reservation_id, "lost")
        self.s.capacity.release(bid.reservation_id, "lost again")
        self.assertEqual(self.s.capacity.owed_seconds(), 0.0)


# ------------------------------------------------------------ scenario 3

class S3_SeveralCustomersRequestRevisions(Base):
    """Three jobs delivered; three customers come back the same day."""

    def test_without_headroom_a_revision_competes_with_new_work(self):
        """A limitation, asserted rather than hidden.

        With the headroom fraction at zero -- the default -- nothing is
        held back for revisions, so a full queue has no room for one.
        """
        self.configure(revision_headroom_fraction=0.0)
        for n in range(3):
            self.assertTrue(self.reserve(200, job=f"j{n}",
                                         key=f"e{n}").allowed)
        revision = self.reserve(60, kind=cap.REVISE, job="j0", key="rev0")
        self.assertFalse(revision.allowed)
        self.assertIs(revision.refusal, cap.Refusal.NO_ROOM)

    def test_headroom_makes_the_shop_take_fewer_jobs(self):
        """The fix is accepting less, not finding room later."""
        self.configure(revision_headroom_fraction=0.5)
        taken, refused = self.offer(10, seconds=200.0)
        self.assertEqual(len(taken), 2)      # 2 x 300 = 600
        self.assertTrue(refused)

    def test_headroom_leaves_room_for_the_revisions_it_was_held_for(self):
        self.configure(revision_headroom_fraction=0.5)
        for n in range(2):
            self.assertTrue(self.reserve(150, job=f"j{n}",
                                         key=f"e{n}").allowed)
        for n in range(2):
            with self.subTest(revision=n):
                self.assertTrue(
                    self.reserve(70, kind=cap.REVISE, job=f"j{n}",
                                 key=f"rev{n}").allowed)

    def test_a_revision_does_not_count_as_a_new_job_against_concurrency(self):
        self.configure(max_concurrent_jobs=1)
        self.assertTrue(self.reserve(100, job="j0", key="e0").allowed)
        self.assertTrue(self.reserve(50, kind=cap.REVISE, job="j0",
                                     key="rev0").allowed)
        self.assertFalse(self.reserve(50, job="j1", key="e1").allowed)

    def test_an_endless_revision_loop_runs_out_of_room_not_patience(self):
        """Nothing stops a customer asking again; the budget stops us."""
        self.reserve(100, job="j0", key="e0")
        for n in range(100):
            if not self.reserve(60, kind=cap.REVISE, job="j0",
                                key=f"rev{n}").allowed:
                self.assertLessEqual(self.s.capacity.owed_seconds(), 600.0)
                return
        self.fail("a hundred revisions fitted in a ten-minute budget")


# ------------------------------------------------------------ scenario 4

class S4_ExecutionTakesLongerThanEstimated(Base):
    """The estimate said four minutes. It is at nine and still going."""

    def test_an_overrun_must_be_reserved_like_any_other_work(self):
        self.reserve(240, job="j0", key="e0")
        self.reserve(300, job="j1", key="e1")
        overrun = self.reserve(200, job="j0", key="over0")
        self.assertFalse(overrun.allowed,
                         "an overrun that needs no room is a budget that "
                         "means nothing")

    def test_a_refused_overrun_does_not_quietly_increase_what_is_owed(self):
        self.reserve(240, job="j0", key="e0")
        self.reserve(300, job="j1", key="e1")
        before = self.s.capacity.owed_seconds()
        self.reserve(200, job="j0", key="over0")
        self.assertEqual(self.s.capacity.owed_seconds(), before)

    def test_an_overrun_that_fits_is_taken_against_the_same_budget(self):
        self.reserve(240, job="j0", key="e0")
        self.assertTrue(self.reserve(200, job="j0", key="over0").allowed)
        self.assertEqual(self.s.capacity.owed_seconds(), 440.0)

    def test_a_job_running_long_blocks_the_next_job_not_the_books(self):
        self.reserve(500, job="j0", key="e0")
        self.assertFalse(self.reserve(200, job="j1", key="e1").allowed)
        self.assertEqual(self.s.capacity.jobs_in_flight(), 1)

    def test_the_promise_already_assumed_the_overrun(self):
        """The safety factor exists for exactly this scenario."""
        from solvent.finder import ExecutionHistory, Finder

        finder = Finder(self.s.discovery, self.s.policy, self.s.capability,
                        self.s.orchestrator, self.s.store)
        estimate = finder.completion_estimate(
            "csv-cleanup", history=ExecutionHistory({"csv-cleanup": [60.0] * 5}))
        promised, _ = finder.promised_seconds(estimate)
        self.assertGreaterEqual(promised, 120.0)


# ------------------------------------------------------------ scenario 5

class S5_DeadlineChanges(Base):
    """The customer moves the deadline in after the job is taken."""

    def finder(self):
        from solvent.finder import Finder

        return Finder(self.s.discovery, self.s.policy, self.s.capability,
                      self.s.orchestrator, self.s.store)

    def estimate(self, *seconds):
        from solvent.finder import ExecutionHistory

        return self.finder().completion_estimate(
            "csv-cleanup",
            history=ExecutionHistory({"csv-cleanup": list(seconds)}))

    def test_a_queue_ahead_lengthens_the_promise(self):
        """A shortened deadline is judged against the queue, not the task."""
        owed = 480.0
        self.reserve(owed, job="j0", key="e0")
        promised, _ = self.finder().promised_seconds(
            self.estimate(*[30.0] * 5),
            queued_seconds=self.s.capacity.owed_seconds())
        self.assertEqual(promised, 60.0 + owed)

    def test_a_deadline_shorter_than_the_promise_cannot_be_met(self):
        promised, _ = self.finder().promised_seconds(
            self.estimate(*[30.0] * 5), queued_seconds=480.0)
        new_deadline_seconds = 300.0
        self.assertGreater(promised, new_deadline_seconds)

    def test_an_unmeasured_capability_promises_no_deadline_at_all(self):
        promised, basis = self.finder().promised_seconds(
            self.finder().completion_estimate("never-measured"))
        self.assertEqual(promised, 0.0)
        self.assertIn("not known", basis)

    def test_the_promise_does_not_shrink_when_the_owner_wishes_it_would(self):
        self.s.policy.amend({"capacity": {"deadline_safety_factor": 0.01}},
                            OWNER, "test: wishful thinking")
        promised, _ = self.finder().promised_seconds(self.estimate(*[30.0] * 5))
        self.assertGreaterEqual(promised, 30.0)


# ------------------------------------------------------------ scenario 6

class S6_VerificationFails(Base):
    """The work ran. The verifier says it is not right."""

    def test_verification_is_reserved_work_not_free_work(self):
        self.reserve(560, job="j0", key="e0")
        check = self.reserve(80, kind=cap.VERIFY, job="j0", key="v0")
        self.assertFalse(check.allowed)

    def test_a_failed_verification_does_not_release_the_job(self):
        held = self.reserve(300, job="j0", key="e0")
        rework = self.reserve(100, kind=cap.REVISE, job="j0", key="r0")
        self.assertTrue(rework.allowed)
        self.assertEqual(self.s.capacity.owed_seconds(), 400.0)
        self.assertTrue(self.s.capacity.has_obligations())
        self.assertIn(held.reservation_id,
                      [r.id for r in self.s.capacity.outstanding()])

    def test_rework_after_a_failure_competes_with_everything_else(self):
        self.reserve(300, job="j0", key="e0")
        self.reserve(250, job="j1", key="e1")
        self.assertFalse(self.reserve(100, kind=cap.REVISE, job="j0",
                                      key="r0").allowed)

    def test_only_finishing_the_work_gives_the_room_back(self):
        held = self.reserve(400, job="j0", key="e0")
        self.assertFalse(self.reserve(300, job="j1", key="e1").allowed)
        self.s.capacity.consume(held.reservation_id, "verified and delivered")
        self.assertTrue(self.reserve(300, job="j1", key="e1").allowed)


# ------------------------------------------------------------ scenario 7

class S7_MarketplaceApiUnavailable(Base):
    """The marketplace stops answering mid-submission."""

    def unresolved(self, count):
        self.s.capacity._applications = type(
            "Unresolved", (), {
                "unresolved": lambda _self: [{"id": f"a{n}"}
                                             for n in range(count)]})()

    def test_an_outcome_nobody_knows_stops_further_commitments(self):
        self.unresolved(1)
        decision = self.s.capacity.room_for(10.0)
        self.assertFalse(decision.allowed)
        self.assertIs(decision.refusal, cap.Refusal.UNRECONCILED)

    def test_it_stops_bids_as_well_as_jobs(self):
        self.unresolved(1)
        for kind in (cap.EXECUTE, cap.VERIFY, cap.REVISE, cap.BID):
            with self.subTest(kind=kind):
                self.assertFalse(self.s.capacity.room_for(10.0,
                                                          kind=kind).allowed)

    def test_work_already_in_flight_is_not_cancelled_by_an_outage(self):
        """Stop taking on; do not drop what was taken on."""
        held = self.reserve(200, job="j0", key="e0")
        self.unresolved(2)
        self.assertEqual(self.s.capacity.owed_seconds(), 200.0)
        self.assertIn(held.reservation_id,
                      [r.id for r in self.s.capacity.outstanding()])

    def test_the_refusal_says_how_many_outcomes_are_unknown(self):
        self.unresolved(3)
        self.assertIn("3", self.s.capacity.room_for(10.0).why)

    def test_reconciling_the_books_lets_work_resume(self):
        self.unresolved(1)
        self.assertFalse(self.s.capacity.room_for(10.0).allowed)
        self.unresolved(0)
        self.assertTrue(self.s.capacity.room_for(10.0).allowed)


# ------------------------------------------------------------ scenario 8

class S8_VpsResourceConstrained(Base):
    """The machine is short of room -- and it is not DeskPilot's machine."""

    def disk(self, free_bytes):
        self.s.capacity._diagnostics = type(
            "Host", (), {"host": lambda _self: {"disk_free_bytes": free_bytes}})()

    def test_low_disk_refuses_new_work(self):
        self.configure(min_free_disk_bytes=5_000_000_000)
        self.disk(1_000_000_000)
        decision = self.s.capacity.room_for(10.0)
        self.assertFalse(decision.allowed)
        self.assertIn("disk", decision.why)

    def test_sufficient_disk_does_not_refuse(self):
        self.configure(min_free_disk_bytes=5_000_000_000)
        self.disk(50_000_000_000)
        self.assertTrue(self.s.capacity.room_for(10.0).allowed)

    def test_a_share_above_the_whole_machine_is_not_a_configuration(self):
        self.configure(cpu_share=1.5)
        decision = self.s.capacity.room_for(10.0)
        self.assertFalse(decision.allowed)
        self.assertIs(decision.refusal, cap.Refusal.NOT_CONFIGURED)
        self.assertIn("MetaTrader 5", decision.why)

    def test_the_owner_can_halt_new_work_without_dropping_current_work(self):
        held = self.reserve(200, job="j0", key="e0")
        self.configure(halted=True)
        decision = self.s.capacity.room_for(10.0)
        self.assertFalse(decision.allowed)
        self.assertIs(decision.refusal, cap.Refusal.HALTED)
        self.assertIn(held.reservation_id,
                      [r.id for r in self.s.capacity.outstanding()])

    def test_the_machine_is_measured_not_assumed(self):
        """Only what diagnostics already reports; no new agent, no probe."""
        host = self.s.diagnostics.host()
        for key in ("disk_free_bytes", "disk_total_bytes", "database_bytes"):
            with self.subTest(key=key):
                self.assertIn(key, host)


# ------------------------------------------------------------ scenario 9

class S9_RestartWithUnfinishedJobs(Base):
    """The VPS reboots with four jobs half done."""

    def restart(self):
        self.s.store.close()
        self.s = Solvent(self.db)
        return self.s

    def test_obligations_are_still_owed_after_a_restart(self):
        self.reserve(200, job="j0", key="e0")
        self.reserve(150, kind=cap.BID, job="b0", key="bid0")
        self.restart()
        self.assertEqual(self.s.capacity.owed_seconds(), 350.0)
        self.assertEqual(self.s.capacity.jobs_in_flight(), 1)
        self.assertEqual(self.s.capacity.open_bids(), 1)

    def test_the_budget_still_binds_after_a_restart(self):
        self.offer(3, seconds=180.0)
        self.restart()
        self.assertFalse(self.reserve(200, job="j9", key="e9").allowed)

    def test_a_replayed_acceptance_after_a_restart_is_still_a_duplicate(self):
        self.reserve(200, job="j0", key="marketplace-event-9")
        self.restart()
        again = self.reserve(200, job="j0", key="marketplace-event-9")
        self.assertIs(again.refusal, cap.Refusal.DUPLICATE)
        self.assertEqual(self.s.capacity.owed_seconds(), 200.0)

    def test_restarting_does_not_invent_room(self):
        self.reserve(550, job="j0", key="e0")
        for _ in range(5):
            self.restart()
        self.assertEqual(self.s.capacity.owed_seconds(), 550.0)

    def test_unfinished_work_is_visible_to_whoever_restarts_it(self):
        self.reserve(200, job="j0", key="e0")
        self.restart()
        report = self.s.capacity.report()
        self.assertTrue(report["has_obligations"])
        self.assertEqual(report["owed_seconds"], 200.0)
        self.assertEqual(report["by_kind"], {cap.EXECUTE: 200.0})


if __name__ == "__main__":
    unittest.main()
