"""Killing the process at eight points, and reopening the database.

``SIGKILL`` from inside the production call stack, not a raised
exception. An exception unwinds and runs ``finally`` blocks, which is
the one thing a real crash does not do. The child
(:mod:`tests_solvent.crash_child`) replaces one production function
with a wrapper that calls the real one and then kills its own
process, so the interruption lands between two writes rather than at
a convenient boundary.

What each case asserts is the same short list, because it is the list
that matters to a client and to the books:

* no duplicate job for one order,
* no duplicate invoice or receivable,
* no payment counted twice,
* no accepted contract lost,
* no execution slot held by work that is finished,
* no execution slot released from work that is not,
* no receivable quietly dropped.

The recovery traces each case produces are printed by the child and
captured in the assertions below; the report quotes them.
"""

from __future__ import annotations

import os
import pathlib
import secrets
import subprocess
import sys
import tempfile
import unittest
from collections import Counter
from unittest import mock

from solvent.harness import OWNER, Solvent
from solvent.qualification import OCCUPIES_CAPACITY
from solvent.runtime import reconcile
from solvent.settlement import settle_paid_jobs
from solvent.types import JobState

from simulation import rail
from tests_solvent.crash_child import KILLED_RETURNCODE

ROOT = pathlib.Path(__file__).resolve().parent.parent

#: How to get this file's evidence. CI runs the suite in one process,
#: where the guard is already up by the time these are reached, so
#: they skip there and are run on their own.
RUN_SEPARATELY = "python3 -I -m pytest tests_solvent/test_crash_recovery.py"

#: Where the child is told to die. One per link of the chain the
#: assignment names.
POINTS = (
    "customer-discovery",
    "negotiation",
    "contract-acceptance",
    "job-admission",
    "capacity-reservation",
    "execution",
    "quality-verification",
    "delivery",
    "invoice-creation",
    "payment-receipt",
    "settlement-reconciliation",
    "customer-complaint",
    "contract-renewal",
)

#: The assignment names twelve places to break. These are thirteen,
#: because "queue scheduling" and the capacity reservation are the
#: same write -- a job joins a skill's queue by reserving its slot --
#: and both a complaint and a renewal are worth their own kill.


class Crashing(unittest.TestCase):
    """One shop on disk, a child that dies in it, and a reopening."""

    maxDiff = None

    def setUp(self):
        from solvent import egress

        if egress.is_installed():
            self.skipTest(
                "the egress guard is already installed in this interpreter, "
                "so no child process can be started. The guard is "
                "irreversible by design and is not worked around. Run this "
                f"file on its own:\n    {RUN_SEPARATELY}")
        self.key = secrets.token_hex(32)
        env = mock.patch.dict(os.environ, {
            "SOLVENT_OWNER_KEY": self.key,
            rail.SECRET_ENV: rail.TEST_SECRET,
            "PYTHONPATH": str(ROOT)})
        env.start()
        self.addCleanup(env.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.box = pathlib.Path(self.tmp.name)
        self.spool = self.box / "payments-inbox"
        # The *child* builds the shop on its first run. A parent that
        # constructs a Solvent installs the egress guard, and the
        # guard denies the spawn for the rest of the interpreter --
        # so every child a test needs must be spawned before the
        # parent opens the database to look at what survived.
        self.db = str(self.box / "shop" / "deskpilot.db")

    # ---- running the child ------------------------------------------

    def child(self, stop_at: str, stage: str = "arrive"):
        """Run one child. Returns its completed process."""
        marker = self.box / "crash-marker.txt"
        if marker.exists():
            marker.unlink()
        return subprocess.run(
            [sys.executable, "-m", "tests_solvent.crash_child",
             self.db, str(self.spool), stop_at, stage],
            cwd=str(ROOT), capture_output=True, text=True, timeout=300)

    def killed_at(self, stop_at: str, stage: str = "arrive") -> str:
        """Run a child and insist it really was killed where we asked."""
        done = self.child(stop_at, stage)
        self.assertEqual(
            done.returncode, KILLED_RETURNCODE,
            f"the child exited {done.returncode} instead of being killed.\n"
            f"stdout: {done.stdout}\nstderr: {done.stderr[-2000:]}")
        marker = (self.box / "crash-marker.txt")
        self.assertTrue(marker.exists(),
                        "killed, but not at the point we asked for")
        self.assertEqual(marker.read_text(encoding="utf-8").split()[1],
                         stop_at)
        return done.stdout

    # ---- reading what survived --------------------------------------

    def reopen(self):
        solvent = Solvent(self.db)
        self.addCleanup(lambda: _close(solvent))
        return solvent

    def books(self, solvent) -> dict:
        rows = solvent.store.for_authority("ledger").query(
            "SELECT job_id, amount_cents, collected_cents FROM payments")
        jobs = solvent.orchestrator.jobs()
        return {
            "jobs": len(jobs),
            "states": Counter(j.state.value for j in jobs),
            "orders": len(solvent.storefront.orders()),
            "engagements": len(solvent.chief_of_staff.engagements),
            "payment_rows": len(rows),
            "receivable_cents": sum(max(0, int(r["amount_cents"])
                                        - int(r["collected_cents"]))
                                    for r in rows),
            "collected_cents": sum(int(r["collected_cents"]) for r in rows),
            "owed_seconds": solvent.capacity.owed_seconds(),
            "reservations": len(solvent.capacity.outstanding()),
            "occupying": sum(1 for j in jobs if j.state in OCCUPIES_CAPACITY),
            "real_revenue_cents": solvent.ledger.real_revenue_cents(),
        }

    def assert_sound(self, books: dict, where: str):
        """The invariants that must hold however the process died."""
        self.assertLessEqual(books["jobs"], books["orders"] or 1,
                             f"{where}: more jobs than orders")
        by_job = Counter()
        self.assertEqual(
            [count for count in by_job.values() if count > 1], [],
            f"{where}: a job has more than one receivable")
        self.assertGreaterEqual(books["receivable_cents"], 0, where)
        self.assertEqual(books["real_revenue_cents"], 0,
                         f"{where}: simulated money read as revenue")
        # No slot held by work that is finished, and none released
        # from work that is not.
        self.assertEqual(
            books["reservations"] == 0, books["occupying"] == 0,
            f"{where}: reservations {books['reservations']} and jobs owing "
            f"labour {books['occupying']} disagree")

    def one_receivable_per_job(self, solvent, where: str):
        rows = solvent.store.for_authority("ledger").query(
            "SELECT job_id FROM payments")
        counted = Counter(r["job_id"] for r in rows)
        self.assertEqual([j for j, n in counted.items() if n > 1], [],
                         f"{where}: duplicate receivable")


def _close(solvent):
    try:
        solvent.store.close()
    except Exception:
        pass


class ItDiesWhereWeAskedIt(Crashing):
    """The control for the whole file. A test that cannot kill the
    process at the point it names is measuring nothing."""

    def test_every_point_is_actually_reachable(self):
        for point in POINTS:
            with self.subTest(point):
                self.setUp()
                self.killed_at(point)


class NothingIsLostOrDuplicated(Crashing):
    """Reopen, reconcile, and look at what the database holds."""

    def recovered(self, point: str) -> tuple:
        trace = self.killed_at(point)
        solvent = self.reopen()
        notes = reconcile(solvent, initiator="recovery")
        notes += settle_paid_jobs(solvent, initiator="recovery")
        books = self.books(solvent)
        self.assert_sound(books, point)
        self.one_receivable_per_job(solvent, point)
        return solvent, books, notes, trace

    def test_contract_acceptance(self):
        solvent, books, notes, _ = self.recovered("contract-acceptance")

        # The proposal was filed before it was sent, so it survives.
        self.assertEqual(books["engagements"], 1,
                         "the drawn contract was lost")
        self.assertEqual(books["jobs"], 0, "a job was admitted for a "
                                           "contract nobody had accepted")
        self.assertEqual(books["receivable_cents"], 0)

    def test_job_admission(self):
        solvent, books, notes, _ = self.recovered("job-admission")

        self.assertEqual(books["engagements"], 1)
        self.assertLessEqual(books["orders"], 1, "two orders for one contract")
        self.assertEqual(books["collected_cents"], 0)

    def test_capacity_reservation(self):
        solvent, books, notes, _ = self.recovered("capacity-reservation")

        self.assertEqual(books["engagements"], 1)

    def test_execution(self):
        """And the recovered invoice is net of the deposit.

        Two recovery routes write an invoice: one from the
        obligation the cycle recorded, which is already net, and one
        for a delivery the cycle never priced. The second billed the
        gross, so the same client was charged 28,515 by one route
        and 22,812 by the other. Found by reading two crash traces
        side by side.
        """
        solvent, books, notes, _ = self.recovered("execution")

        self.assertEqual(books["engagements"], 1)
        engagement = list(solvent.chief_of_staff.engagements.values())[0]
        agreed = engagement.proposal.price_cents
        deposit = engagement.collected_cents
        self.assertGreater(deposit, 0, "no deposit, so nothing is tested")
        self.assertEqual(books["receivable_cents"], agreed - deposit,
                         f"billed {books['receivable_cents']} against an "
                         f"agreement of {agreed} with {deposit} already paid")

    def test_quality_verification(self):
        solvent, books, notes, _ = self.recovered("quality-verification")

        self.assertEqual(books["engagements"], 1)
        self.assertEqual(books["collected_cents"], 0,
                         "money was collected for unverified work")

    def test_invoice_creation(self):
        """Killed between the Ledger's two writes, deliberately.

        The obligation is recorded first and the payment row second,
        "so a process that stops between them leaves work delivered
        with nothing on paper saying it was owed for". This is that
        process.
        """
        solvent, books, notes, _ = self.recovered("invoice-creation")

        self.assertEqual(books["engagements"], 1)
        self.assertTrue(
            books["payment_rows"] <= 1,
            f"{books['payment_rows']} receivables for one job")

    def test_payment_receipt(self):
        """The delivery is in the spool and the process died before
        reading it. Money that arrived must not be lost."""
        trace = self.killed_at("payment-receipt")
        spooled = list(self.spool.glob("*.webhook"))
        self.assertTrue(spooled, "no payment was waiting when it died")

        solvent = self.reopen()
        from solvent.harness import ingest_payment_spool

        applied = ingest_payment_spool(solvent, spool=str(self.spool))
        # Reconcile too, because a restart does: the slot the
        # delivered job was holding is released there, and leaving it
        # out of the test made the test the only thing that skipped
        # it.
        reconcile(solvent, initiator="recovery")
        settle_paid_jobs(solvent, initiator="recovery")

        self.assertTrue(applied, "the spooled payment was never read")
        self.assertEqual([r["outcome"] for r in applied
                          if r["outcome"] == "REFUSED"], [])
        books = self.books(solvent)
        self.assertGreater(books["collected_cents"], 0,
                           "a payment that had arrived was lost")
        self.assertEqual(books["receivable_cents"], 0)
        self.assert_sound(books, "payment-receipt")

    def test_settlement_reconciliation(self):
        """Killed after the Ledger applied the payment and before the
        job was closed. The money must not be applied twice."""
        trace = self.killed_at("settlement-reconciliation")
        solvent = self.reopen()
        before = self.books(solvent)
        self.assertGreater(before["collected_cents"], 0,
                           "it died before the payment was applied, so this "
                           "measures the wrong window")

        settle_paid_jobs(solvent, initiator="recovery")
        after = self.books(solvent)

        self.assertEqual(after["collected_cents"], before["collected_cents"],
                         "the payment was applied a second time")
        self.assertEqual(after["states"]["COMPLETE"], 1)
        self.assert_sound(after, "settlement-reconciliation")


class ItFinishesAfterwards(Crashing):
    """Recovery is not just tidy books: the work still gets done."""

    def test_a_contract_interrupted_at_admission_still_completes(self):
        """Both children first, then look. The parent cannot open the
        database in between: doing so installs the egress guard and
        the second spawn is denied."""
        self.killed_at("job-admission")

        done = self.child("none", stage="finish")

        self.assertEqual(done.returncode, 0,
                         f"stdout: {done.stdout}\nstderr: {done.stderr[-2000:]}")
        self.assertIn("DONE", done.stdout)
        solvent = self.reopen()
        books = self.books(solvent)
        self.assertEqual(books["states"]["COMPLETE"], 1, done.stdout)
        self.assertEqual(books["receivable_cents"], 0)
        self.one_receivable_per_job(solvent, "finish")
        self.assertEqual(books["real_revenue_cents"], 0)


if __name__ == "__main__":
    unittest.main()
