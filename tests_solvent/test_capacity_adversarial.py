"""Eighteen attempts to make DeskPilot promise what it cannot deliver.

Each class below is one attack. Two of them found real defects, which
are now fixed and pinned here: concurrent reservations could both pass
the same gate and overcommit the budget, and a job admitted with room
for its execution alone could finish executing and find no room left
to be verified -- which, because execution reservations are held until
delivery, stopped the whole shop rather than just that job.

An attack test earns its place only if it would notice the defence
being removed. Where an attack is blocked by something other than
capacity, the test calls that thing rather than asserting from a
distance.
"""

from __future__ import annotations

import tempfile
import threading
import unittest

from solvent import capacity as cap
from solvent import revenuemodel as rm
from solvent.errors import AuthorityError, FailClosed
from solvent.finder import ExecutionHistory, Finder
from solvent.harness import OWNER, Solvent
from tests_solvent.fixtures import source_names

BUDGET = {"max_owed_seconds": 600.0, "max_concurrent_jobs": 3,
          "max_open_bids": 2, "cpu_share": 0.5, "min_free_disk_bytes": 0,
          "max_execution_workers": 3, "max_verification_workers": 2,
          "revision_headroom_fraction": 0.0, "halted": False}


class Base(unittest.TestCase):

    def setUp(self):
        self.box = tempfile.TemporaryDirectory()
        self.addCleanup(self.box.cleanup)
        self.db = f"{self.box.name}/attack.db"
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

    def reserve(self, seconds, *, kind=cap.EXECUTE, job="job", key=None):
        return self.s.capacity.reserve(
            seconds, kind=kind, job_id=job,
            idempotency_key=key or f"{kind}:{job}:{seconds}")

    def finder(self):
        return Finder(self.s.discovery, self.s.policy, self.s.capability,
                      self.s.orchestrator, self.s.store)


# 1 ---------------------------------------------------------------------

class A01_BidAcceptedAfterCapacityIsExhausted(Base):

    def test_the_bid_held_its_room_so_acceptance_cannot_fail(self):
        bid = self.reserve(300, kind=cap.BID, job="b1", key="bid1")
        self.assertTrue(bid.allowed)
        # Everything else fills up while the client thinks about it.
        self.assertFalse(self.reserve(400, job="other", key="other").allowed)
        self.s.capacity.release(bid.reservation_id, "accepted")
        self.assertTrue(self.reserve(300, job="b1", key="won").allowed)

    def test_an_acceptance_with_no_reservation_behind_it_is_refused(self):
        """What happens if the bid never reserved: the attack succeeds.

        This is why bids reserve. The refusal here is the system
        telling the truth -- it cannot do the work -- but by then a
        client has been told otherwise.
        """
        self.reserve(550, job="other", key="other")
        late = self.reserve(300, job="b1", key="won")
        self.assertFalse(late.allowed)
        self.assertIs(late.refusal, cap.Refusal.NO_ROOM)


# 2 ---------------------------------------------------------------------

class A02_TwoSimultaneousBidsTakeTheSameRoom(Base):
    """A real defect, found here and fixed in store.transaction().

    room_for() read the ledger, decided, and then reserve() wrote to
    it. Two threads both read 500s owed against a 600s budget, both
    found room for 80s, and both took it: 660s owed through a gate
    that refused neither.
    """

    def race(self, threads, seconds):
        # A bid is not a job in flight, so the job ceiling does not
        # apply to it; only the open-bid limit needs to be out of the
        # way for the race to be about the budget.
        self.configure(max_concurrent_jobs=4, max_open_bids=50,
                       max_execution_workers=4)
        self.reserve(500, job="base", key="base")
        barrier = threading.Barrier(threads)
        out = []

        def attempt(n):
            barrier.wait()
            out.append(self.s.capacity.reserve(
                seconds, kind=cap.BID, job_id=f"b{n}",
                idempotency_key=f"bid{n}"))

        workers = [threading.Thread(target=attempt, args=(n,))
                   for n in range(threads)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        return out

    def test_two_concurrent_reservations_cannot_both_take_the_last_room(self):
        self.race(2, 80)
        self.assertLessEqual(self.s.capacity.owed_seconds(), 600.0)

    def test_only_one_of_them_is_told_yes(self):
        out = self.race(2, 80)
        self.assertEqual(sum(1 for d in out if d.allowed), 1)

    def test_it_holds_with_eight_of_them(self):
        out = self.race(8, 80)
        self.assertEqual(sum(1 for d in out if d.allowed), 1)
        self.assertLessEqual(self.s.capacity.owed_seconds(), 600.0)

    def test_concurrent_transactions_do_not_corrupt_each_other(self):
        """Sixteen that all succeed, not eight that all but one fail.

        The budget race above is the defect this class was written
        for, and it is limited by design: only one of the eight can
        win, so only one transaction ever completes and the window
        where two overlap is never really opened.

        That let a mutant removing the Python lock survive. Removing
        it genuinely breaks things -- sixteen concurrent
        transactions on a shared connection produce SystemError and
        "cannot commit - no transaction is active", and six of the
        sixteen writes vanish -- but nothing asserted it, because no
        test ran enough transactions that were all allowed to finish.

        BEGIN IMMEDIATE serialises *writers across processes*. The
        lock serialises *this process's threads* on one shared
        connection. Two halves, and only one of them was covered.
        """
        db = self.s.store.for_authority("capacity")
        barrier = threading.Barrier(16)
        errors = []

        def write(n):
            barrier.wait()
            try:
                with db.transaction():
                    db.query_one("SELECT count(*) AS c FROM "
                                 "capacity_reservations")
                    db.execute(
                        "INSERT INTO capacity_reservations"
                        "(id,ts,job_id,kind,units,state,why,"
                        "idempotency_key,capability) "
                        "VALUES(?,?,?,?,?,?,?,?,?)",
                        (f"r{n}", "t", f"j{n}", cap.EXECUTE, 1.0,
                         cap.HELD, "concurrency", f"key{n}", ""))
            except Exception as exc:
                errors.append(f"{type(exc).__name__}: {exc}")

        threads = [threading.Thread(target=write, args=(n,))
                   for n in range(16)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [], "concurrent transactions raised")
        rows = db.query("SELECT id FROM capacity_reservations "
                        "WHERE why = 'concurrency'")
        self.assertEqual(len(rows), 16, "writes were lost under contention")

    def test_the_write_lock_is_taken_against_other_processes_too(self):
        """The lock serialises threads; BEGIN IMMEDIATE serialises writers.

        Two halves guarding different things. A second connection to
        the same file stands in for a second process here -- SQLite
        does not distinguish them -- and must find the database locked
        for the whole of a capacity transaction, not only at its
        final write. A deferred transaction would not take the write
        lock until the INSERT, leaving the read-decide window open to
        anything outside this process.
        """
        import sqlite3

        db = self.s.store.for_authority("capacity")
        other = sqlite3.connect(self.db, timeout=0.1)
        self.addCleanup(other.close)
        with db.transaction():
            with self.assertRaises(sqlite3.OperationalError) as caught:
                other.execute(
                    "INSERT INTO capacity_reservations(id,ts,job_id,kind,"
                    "units,state,why,idempotency_key) VALUES('x','t','j',"
                    "'EXECUTE',1,'HELD','','outsider')")
                other.commit()
        self.assertIn("locked", str(caught.exception))

    def test_the_lock_is_released_when_the_transaction_ends(self):
        """A guard that never lets go is an outage, not a guarantee."""
        import sqlite3

        db = self.s.store.for_authority("capacity")
        with db.transaction():
            pass
        other = sqlite3.connect(self.db, timeout=1.0)
        self.addCleanup(other.close)
        other.execute(
            "INSERT INTO capacity_reservations(id,ts,job_id,kind,units,"
            "state,why,idempotency_key) VALUES('x','t','j','EXECUTE',1,"
            "'HELD','','outsider')")
        other.commit()

    def test_the_race_would_be_visible_if_the_guard_were_gone(self):
        """The threads really do collide; the test is not a no-op."""
        out = self.race(8, 80)
        self.assertEqual(len(out), 8)
        self.assertEqual(sum(1 for d in out if not d.allowed), 7)


# 3 ---------------------------------------------------------------------

class A03_FalseCompletionTimeEstimates(Base):

    def test_an_estimate_cannot_be_asserted_only_measured(self):
        estimate = self.finder().completion_estimate("never-run")
        self.assertFalse(estimate.known)
        self.assertEqual(self.finder().promised_seconds(estimate)[0], 0.0)

    def test_one_fast_run_does_not_become_a_confident_estimate(self):
        estimate = self.finder().completion_estimate(
            "csv-cleanup", history=ExecutionHistory({"csv-cleanup": [5.0]}))
        self.assertEqual(estimate.confidence.name, "PROVISIONAL")

    def test_a_fast_median_cannot_hide_a_slow_tail(self):
        """Nine quick runs and one slow one is a slow capability."""
        history = ExecutionHistory({"csv-cleanup": [10.0] * 9 + [900.0]})
        estimate = self.finder().completion_estimate("csv-cleanup",
                                                     history=history)
        promised, _ = self.finder().promised_seconds(estimate)
        self.assertGreaterEqual(promised, 900.0)

    def test_an_understated_estimate_still_buys_the_room_it_asked_for(self):
        """Lying about the size does not make the budget bigger."""
        self.reserve(550, job="a", key="a")
        self.assertFalse(self.reserve(100, job="b", key="b").allowed)


# 4 ---------------------------------------------------------------------

class A04_ManipulatedDeadlines(Base):

    def history(self):
        return ExecutionHistory({"csv-cleanup": [100.0] * 12})

    def promise(self):
        finder = self.finder()
        return finder.promised_seconds(
            finder.completion_estimate("csv-cleanup", history=self.history()))[0]

    def test_a_safety_factor_below_one_is_clamped(self):
        self.s.policy.amend({"capacity": {"deadline_safety_factor": 0.01}},
                            OWNER, "test: wishful")
        self.assertGreaterEqual(self.promise(), 100.0)

    def test_a_negative_safety_factor_is_clamped(self):
        self.s.policy.amend({"capacity": {"deadline_safety_factor": -5.0}},
                            OWNER, "test: hostile")
        self.assertGreaterEqual(self.promise(), 100.0)

    def test_the_promise_never_drops_below_the_slowest_measured_run(self):
        self.s.policy.amend({"capacity": {"deadline_safety_factor": 1.0}},
                            OWNER, "test: no safety at all")
        finder = self.finder()
        estimate = finder.completion_estimate(
            "csv-cleanup",
            history=ExecutionHistory({"csv-cleanup": [10.0] * 11 + [800.0]}))
        self.assertGreaterEqual(finder.promised_seconds(estimate)[0],
                                estimate.tail_seconds)

    def test_the_promise_covers_verification_not_only_execution(self):
        """A deadline is for delivery, and delivery is after checking."""
        finder = self.finder()
        estimate = finder.completion_estimate("csv-cleanup",
                                              history=self.history())
        bare, _ = finder.promised_seconds(estimate, downstream_seconds=0.0)
        whole, _ = finder.promised_seconds(estimate, downstream_seconds=500.0)
        self.assertEqual(whole - bare, 500.0)

    def test_omitting_the_downstream_work_does_not_mean_zero(self):
        self.configure(revision_headroom_fraction=0.5)
        finder = self.finder()
        estimate = finder.completion_estimate("csv-cleanup",
                                              history=self.history())
        omitted, _ = finder.promised_seconds(estimate)
        explicit, _ = finder.promised_seconds(estimate, downstream_seconds=0.0)
        self.assertGreater(omitted, explicit)


# 5 ---------------------------------------------------------------------

class A05_DuplicateNotifications(Base):

    def test_the_same_event_ten_times_is_one_obligation(self):
        for _ in range(10):
            self.s.capacity.reserve(100, job_id="j1",
                                    idempotency_key="event-1")
        self.assertEqual(self.s.capacity.owed_seconds(), 100.0)

    def test_an_admission_replayed_is_one_admission(self):
        for _ in range(5):
            self.s.capacity.admit("j1", execute_seconds=100,
                                  verify_seconds=20, revise_seconds=30,
                                  idempotency_key="accept-1")
        self.assertEqual(self.s.capacity.owed_seconds(), 150.0)

    def test_a_reservation_with_no_key_is_refused_outright(self):
        with self.assertRaises(FailClosed):
            self.s.capacity.reserve(10, job_id="j1", idempotency_key="")

    def test_concurrent_duplicates_still_make_one_obligation(self):
        barrier = threading.Barrier(6)

        def attempt():
            barrier.wait()
            self.s.capacity.reserve(100, job_id="j1",
                                    idempotency_key="same-event")

        workers = [threading.Thread(target=attempt) for _ in range(6)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        self.assertEqual(self.s.capacity.owed_seconds(), 100.0)


# 6 ---------------------------------------------------------------------

class A06_QueueCorruption(Base):

    def test_another_authority_cannot_write_the_reservation_table(self):
        ledger = self.s.store.for_authority("ledger")
        with self.assertRaises(AuthorityError):
            ledger.execute(
                "INSERT INTO capacity_reservations(id,ts,job_id,kind,units,"
                "state,why,idempotency_key) VALUES('x','t','j','EXECUTE',"
                "1,'HELD','','k')")

    def test_another_authority_cannot_delete_reservations(self):
        self.reserve(100, job="j1", key="k1")
        ledger = self.s.store.for_authority("ledger")
        with self.assertRaises(AuthorityError):
            ledger.execute("DELETE FROM capacity_reservations")
        self.assertEqual(self.s.capacity.owed_seconds(), 100.0)

    def test_a_duplicate_key_cannot_be_forced_in(self):
        import sqlite3

        self.reserve(100, job="j1", key="k1")
        db = self.s.store.for_authority("capacity")
        with self.assertRaises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO capacity_reservations(id,ts,job_id,kind,units,"
                "state,why,idempotency_key) VALUES('x','t','j','EXECUTE',"
                "1,'HELD','','k1')")

    def test_an_unknown_reservation_cannot_be_finished(self):
        with self.assertRaises(FailClosed):
            self.s.capacity.consume("cap-does-not-exist")


# 7 ---------------------------------------------------------------------

class A07_CapacityReservationLoss(Base):

    def test_a_reservation_cannot_be_released_twice_for_double_room(self):
        held = self.reserve(400, job="j1", key="k1")
        self.s.capacity.release(held.reservation_id, "once")
        self.s.capacity.release(held.reservation_id, "twice")
        self.assertEqual(self.s.capacity.owed_seconds(), 0.0)
        self.assertTrue(self.reserve(500, job="j2", key="k2").allowed)
        self.assertFalse(self.reserve(500, job="j3", key="k3").allowed)

    def test_consuming_then_releasing_does_not_double_count(self):
        held = self.reserve(400, job="j1", key="k1")
        self.s.capacity.consume(held.reservation_id)
        self.s.capacity.release(held.reservation_id)
        self.assertEqual(self.s.capacity.owed_seconds(), 0.0)

    def test_a_reservation_is_on_disk_before_the_call_returns(self):
        self.reserve(400, job="j1", key="k1")
        other = Solvent(self.db)
        self.addCleanup(lambda: other.store.close())
        self.assertEqual(other.capacity.owed_seconds(), 400.0)


# 8 ---------------------------------------------------------------------

class A08_RestartWithActiveObligations(Base):

    def restart(self):
        self.s.store.close()
        self.s = Solvent(self.db)

    def test_obligations_survive_a_restart(self):
        self.s.capacity.admit("j1", execute_seconds=200, verify_seconds=40,
                              revise_seconds=60, idempotency_key="a1")
        self.restart()
        self.assertEqual(self.s.capacity.owed_seconds(), 300.0)

    def test_a_restart_does_not_reopen_the_gate(self):
        self.reserve(550, job="j1", key="k1")
        for _ in range(4):
            self.restart()
        self.assertFalse(self.reserve(100, job="j2", key="k2").allowed)

    def test_a_replayed_event_after_a_restart_is_still_a_duplicate(self):
        self.reserve(100, job="j1", key="event-1")
        self.restart()
        again = self.reserve(100, job="j1", key="event-1")
        self.assertIs(again.refusal, cap.Refusal.DUPLICATE)


# 9 ---------------------------------------------------------------------

class A09_FailedVerification(Base):
    """The second real defect: admission that did not buy verification.

    A job admitted with room for execution alone could finish executing
    and be refused the room to be checked. Its execution reservation is
    held until delivery, so nothing freed up: in load simulation every
    worker ended up holding room for work it was not allowed to finish.
    """

    def test_admission_buys_the_verification_too(self):
        decision = self.s.capacity.admit(
            "j1", execute_seconds=100, verify_seconds=20, revise_seconds=30,
            idempotency_key="a1")
        self.assertTrue(decision.allowed)
        kinds = {r.kind for r in self.s.capacity.outstanding()}
        self.assertEqual(kinds, {cap.EXECUTE, cap.VERIFY, cap.REVISE})

    def test_verification_cannot_be_refused_after_admission(self):
        self.configure(max_owed_seconds=300.0)
        self.assertTrue(self.s.capacity.admit(
            "j1", execute_seconds=200, verify_seconds=50, revise_seconds=50,
            idempotency_key="a1").allowed)
        # The shop fills up behind it.
        self.reserve(100, kind=cap.BID, job="b", key="bid")
        verify = self.s.capacity.reservation_for("j1", cap.VERIFY)
        self.assertTrue(verify, "the room to verify was never held")

    def test_a_job_that_does_not_fit_whole_is_not_taken_at_all(self):
        self.configure(max_owed_seconds=250.0)
        decision = self.s.capacity.admit(
            "j1", execute_seconds=200, verify_seconds=50, revise_seconds=50,
            idempotency_key="a1")
        self.assertFalse(decision.allowed)
        self.assertEqual(self.s.capacity.outstanding(), [],
                         "a refused admission left reservations behind")

    def test_a_failed_verification_does_not_free_the_job(self):
        self.s.capacity.admit("j1", execute_seconds=200, verify_seconds=40,
                              revise_seconds=60, idempotency_key="a1")
        self.s.capacity.consume(self.s.capacity.reservation_for("j1",
                                                                cap.VERIFY))
        self.assertTrue(self.s.capacity.has_obligations())


# 10 --------------------------------------------------------------------

class A10_EndlessRevisionLoops(Base):

    def test_revisions_run_out_of_room_rather_than_running_for_ever(self):
        self.reserve(100, job="j1", key="e1")
        for n in range(200):
            if not self.reserve(60, kind=cap.REVISE, job="j1",
                                key=f"r{n}").allowed:
                return
        self.fail("two hundred revisions fitted in a ten-minute budget")

    def test_a_revision_cannot_be_reserved_twice_for_the_same_request(self):
        self.reserve(100, job="j1", key="e1")
        first = self.reserve(60, kind=cap.REVISE, job="j1", key="rev-req-7")
        second = self.reserve(60, kind=cap.REVISE, job="j1", key="rev-req-7")
        self.assertTrue(first.allowed)
        self.assertIs(second.refusal, cap.Refusal.DUPLICATE)


# 11 --------------------------------------------------------------------

class A11_ResourceExhaustion(Base):

    def host(self, **fields):
        self.s.capacity._diagnostics = type(
            "Host", (), {"host": lambda _self: dict(fields)})()

    def test_no_disk_means_no_new_work(self):
        self.configure(min_free_disk_bytes=10_000_000_000)
        self.host(disk_free_bytes=1_000)
        self.assertFalse(self.s.capacity.room_for(1.0).allowed)

    def test_memory_above_the_ceiling_means_no_new_work(self):
        self.configure(max_memory_used_percent=80.0)
        self.host(memory={"used_percent": 97.0})
        self.assertFalse(self.s.capacity.room_for(1.0).allowed)

    def broken_host(self):
        def explode(_self):
            raise OSError("the host will not say")

        self.s.capacity._diagnostics = type("Broken", (),
                                            {"host": explode})()

    def test_a_host_that_lies_by_silence_does_not_open_the_gate(self):
        """An unreadable host is not a healthy host.

        Found by reading capacity against iFixAi's silent-failure
        inspection. The unreadable-books path refused; these two
        returned "no problem" and admitted work, so the quieter of
        two ways of failing to check was the dangerous one.
        """
        self.configure(min_free_disk_bytes=10_000_000_000)
        self.broken_host()
        decision = self.s.capacity.room_for(1.0)
        self.assertFalse(decision.allowed)
        self.assertIn("nobody can check", decision.why)

    def test_an_unreadable_memory_reading_also_refuses(self):
        self.configure(max_memory_used_percent=80.0)
        self.broken_host()
        self.assertFalse(self.s.capacity.room_for(1.0).allowed)

    def test_an_unreadable_host_still_reports_the_signal_as_unmeasured(self):
        self.configure(min_free_disk_bytes=10_000_000_000)
        self.broken_host()
        self.assertIn("disk_free_bytes",
                      self.s.capacity.pressure()["unmeasured"])

    def test_a_host_that_simply_has_no_memory_figure_is_not_a_failure(self):
        """Windows reports no memory. That is ordinary, not broken."""
        self.configure(max_memory_used_percent=80.0, min_free_disk_bytes=0)
        self.s.capacity._diagnostics = type(
            "Quiet", (), {"host": lambda _self: {"memory": {}}})()
        self.assertTrue(self.s.capacity.room_for(1.0).allowed)

    def test_an_unset_limit_is_not_checked_at_all(self):
        """No floor configured means nothing to fail to check."""
        self.configure(min_free_disk_bytes=0, max_memory_used_percent=0.0)
        self.broken_host()
        self.assertTrue(self.s.capacity.room_for(1.0).allowed)

    def test_the_whole_machine_is_never_claimed(self):
        self.configure(cpu_share=1.5)
        self.assertIn("MetaTrader 5", self.s.capacity.budget().problem())


# 12 --------------------------------------------------------------------

class A12_MarketplaceRateLimitViolations(Base):

    def test_capacity_cannot_reach_a_marketplace_at_all(self):
        names = source_names("solvent/capacity.py")
        for forbidden in ("requests", "urllib", "urlopen", "httpx", "socket",
                          "egress", "submit", "poll"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, names)

    def test_having_room_is_not_permission_to_poll(self):
        """Capacity answers "is there room", never "may we ask again"."""
        self.assertTrue(self.s.capacity.room_for(10.0).allowed)
        allowed, why = self.s.discovery.may("unknown-source", "DISCOVER")
        self.assertFalse(allowed)
        self.assertTrue(why.strip())


# 13 --------------------------------------------------------------------

class A13_UnauthorizedBidding(Base):

    def test_a_reservation_is_not_an_authorisation_to_send(self):
        bid = self.reserve(100, kind=cap.BID, job="b1", key="bid1")
        self.assertTrue(bid.allowed)
        allowed, why = self.s.applications.may_submit("unknown-source")
        self.assertFalse(allowed, "capacity opened a door it does not own")
        self.assertTrue(why.strip())

    def test_capacity_holds_no_owner_approval_machinery(self):
        names = source_names("solvent/capacity.py")
        for forbidden in ("approve", "approval", "owner", "sign", "signature",
                          "grant", "authorise", "authorize"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, names)


# 14 --------------------------------------------------------------------

class A14_CommercialPolicyBypass(Base):

    def test_capacity_prices_nothing(self):
        names = source_names("solvent/capacity.py")
        for forbidden in ("cents", "price", "quote", "invoice", "margin",
                          "hourly_rate_cents", "charge"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, names)

    def test_room_for_work_is_not_a_contracting_identity(self):
        self.assertTrue(self.s.capacity.room_for(10.0).allowed)
        allowed, why = self.s.policy.may_contract_for("INVOICE")
        self.assertFalse(allowed)
        self.assertTrue(why.strip())

    def test_the_scan_would_notice_a_violation(self):
        names = source_names("solvent/capacity.py")
        self.assertIn("room_for", names)
        self.assertIn("admit", names)


# 15 --------------------------------------------------------------------

class A15_ExpiredOpportunityExecution(Base):

    def test_capacity_does_not_decide_whether_an_offer_is_still_open(self):
        """It answers one question, and this is not it."""
        surface = {n for n in dir(self.s.capacity) if not n.startswith("_")}
        for absent in ("expired", "still_open", "deadline", "expiry"):
            with self.subTest(absent=absent):
                self.assertNotIn(absent, surface)

    def test_room_held_for_work_that_went_away_is_given_back(self):
        held = self.reserve(500, job="j1", key="k1")
        self.s.capacity.release(held.reservation_id, "the offer expired")
        self.assertEqual(self.s.capacity.owed_seconds(), 0.0)

    def test_room_is_not_given_back_by_the_passage_of_time_alone(self):
        self.reserve(500, job="j1", key="k1")
        for _ in range(3):
            self.s.capacity.pressure()
        self.assertEqual(self.s.capacity.owed_seconds(), 500.0)


# 16 --------------------------------------------------------------------

class A16_UncertainBidOutcomes(Base):

    def unresolved(self, count):
        self.s.capacity._applications = type(
            "Unresolved", (), {
                "unresolved": lambda _self: [{"id": f"a{n}"}
                                             for n in range(count)]})()

    def test_an_unknown_outcome_stops_every_kind_of_commitment(self):
        self.unresolved(1)
        for kind in cap.KINDS:
            with self.subTest(kind=kind):
                self.assertIs(self.s.capacity.room_for(1.0, kind=kind).refusal,
                              cap.Refusal.UNRECONCILED)

    def test_it_stops_admission_as_well_as_reservation(self):
        self.unresolved(1)
        self.assertFalse(self.s.capacity.admit(
            "j1", execute_seconds=10, idempotency_key="a1").allowed)

    def test_unreadable_books_count_as_unresolved(self):
        def explode(_self):
            raise RuntimeError("unreadable")

        self.s.capacity._applications = type("Broken", (),
                                             {"unresolved": explode})()
        self.assertFalse(self.s.capacity.room_for(1.0).allowed)


# 17 --------------------------------------------------------------------

class A17_InaccurateRevenueProjections(Base):

    def model(self, **assumptions):
        return rm.build(policy=self.s.policy, capacity=self.s.capacity,
                        ledger=self.s.ledger,
                        history=ExecutionHistory({"csv-cleanup": [600.0] * 12}),
                        capability="csv-cleanup",
                        assumptions=assumptions or None)

    def test_a_projection_cannot_be_made_without_a_win_rate(self):
        model = self.model()
        self.assertIsNone(
            model.scenarios["HIGH_DEMAND"]["gross_revenue_cents"])

    def test_a_projection_never_counts_as_collected_revenue(self):
        model = self.model(bid_win_rate=0.2, collection_rate=1.0)
        self.assertGreater(
            model.scenarios["HIGH_DEMAND"]["gross_revenue_cents"], 0)
        self.assertEqual(model.collected_revenue_cents, 0)

    def test_demand_beyond_capacity_adds_no_profit(self):
        model = self.model(bid_win_rate=0.2, collection_rate=1.0)
        self.assertEqual(model.scenarios["OVERLOAD"]["net_profit_cents"],
                         model.scenarios["HIGH_DEMAND"]["net_profit_cents"])

    def test_no_gate_reads_the_model(self):
        for module in ("solvent/capacity.py", "solvent/gate.py",
                       "solvent/governor.py", "solvent/application.py"):
            with self.subTest(module=module):
                self.assertNotIn("revenuemodel", source_names(module))


# 18 --------------------------------------------------------------------

class A18_IncorrectPaymentReconciliation(Base):

    def test_simulated_money_is_not_revenue(self):
        self.assertEqual(self.s.ledger.real_revenue_cents(), 0)

    def test_capacity_cannot_touch_the_ledger(self):
        names = source_names("solvent/capacity.py")
        for forbidden in ("ledger", "real_revenue_cents", "payments",
                          "record_payment"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, names)

    def test_a_job_delivered_is_not_a_job_paid(self):
        held = self.reserve(100, job="j1", key="k1")
        self.s.capacity.consume(held.reservation_id, "delivered")
        self.assertEqual(self.s.ledger.real_revenue_cents(), 0)

    def test_an_unresolved_payment_outcome_blocks_further_work(self):
        self.s.capacity._applications = type(
            "Unresolved", (), {"unresolved": lambda _self: [{"id": "a"}]})()
        self.assertIs(self.s.capacity.room_for(1.0).refusal,
                      cap.Refusal.UNRECONCILED)


if __name__ == "__main__":
    unittest.main()
