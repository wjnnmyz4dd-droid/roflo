"""Throughput: does it move to the next job, and does anything sleep?

RULE 6 says that once work is verified and authorised for delivery, nothing
waits on purpose. That is easy to claim and easy to break by accident -- a
retry backoff, a polite pause before contacting a client, a sleep somebody
added while debugging. These tests measure instead of asserting intent.

They also answer the question the hourly-billing rules depend on: how long
does a job actually take? Every figure in the pricing layer rests on that,
and a number nobody measured is a guess.
"""

from __future__ import annotations

import pathlib
import tempfile
import time
import unittest

from solvent.discovery import Compliance, FixtureSource, Readiness
from solvent.harness import (
    OWNER, Solvent, owner_promotions, promote_capability, run_business_cycle,
)
from solvent.types import Criticality, Requirement, RequirementSource

JOBS = 12
ROWS = 500

#: The one module allowed to sleep, and why. A rate limiter that paces
#: requests to a third party's server is honouring their limits, which is
#: the opposite of an artificial delay -- and this module is a read-only
#: research prototype that no live code imports, so no job can ever wait
#: on it. The companion test below keeps both halves of that true.
SLEEPS_FOR_SOMEBODY_ELSE = {"prospecting.py"}


def req(rid, check, params=None):
    return Requirement(
        id=rid, text=check, source=RequirementSource.CLIENT_CONFIRMED_STRUCTURED,
        acceptance=check, check=check, params=params or {},
        criticality=Criticality.MANDATORY)


AGREED = [
    req("R1", "require_columns", {"columns": ["Name", "Email", "Amount"]}),
    req("R2", "trim_whitespace", {"columns": ["Name"]}),
    req("R3", "drop_exact_duplicates", {}),
]


class ManyJobsInARow(unittest.TestCase):

    def setUp(self):
        self.box = tempfile.TemporaryDirectory()
        self.addCleanup(self.box.cleanup)
        self.dir = pathlib.Path(self.box.name)
        self.s = Solvent(str(self.dir / "throughput.db"))
        self.addCleanup(self.close)
        for name in owner_promotions():
            promote_capability(self.s, name, owner_identity=OWNER,
                               why="test: cleared for the throughput suite")
        # One source, re-stocked between cycles. Discovery deduplicates by
        # content, which is correct -- polling the same board twice must not
        # invent a second job out of one posting -- so a throughput fixture
        # has to supply genuinely new postings, exactly as a real board
        # would.
        self.board = FixtureSource("board", [])
        self.s.discovery.register_source(
            self.board, owner_identity=OWNER,
            readiness=Readiness.PERMITTED_AUTOMATION,
            compliance=Compliance.PERMITTED, determination="test fixture")
        self.next_ref = 0
        self.s.policy.amend({
            "discovery": {"approved_sources": ["board"]},
            "financial": {"max_job_spend_cents": 500_000,
                          "max_committed_unverified_cents": 10_000_000,
                          "owner_approval_above_cents": 10_000_000},
            "qualification": {"max_concurrent_jobs": JOBS + 2},
            "egress": {"allowlist": [{"host": "client.example.com",
                                      "max_privacy": "CLIENT_CONFIDENTIAL",
                                      "classes": ["C2_EXTERNAL_COMMUNICATION"]}],
                       "simulation_only": True},
        }, OWNER, "test: enough headroom to measure throughput, not limits")

        self.source = self.dir / "export.csv"
        rows = ["Name,Email,Amount"]
        for i in range(ROWS):
            rows.append(f"  Client {i % 300} ,c{i % 300}@x.test,{i % 89}.50")
        self.source.write_text("\n".join(rows) + "\n", encoding="utf-8")

    def close(self):
        try:
            self.s.store.close()
        except Exception:
            pass

    def one(self):
        self.next_ref += 1
        i = self.next_ref
        self.board._postings = [{
            "ref": f"tp-{i}", "title": f"Clean export {i}",
            "quoted_cents": 40_000, "needs": ["csv-cleanup"],
            "project_state": "CT", "client_ref": f"client:{i}",
            "body": "fixed price for the project", "deliverables": []}]
        return run_business_cycle(
            self.s, source_name="board", source_file=str(self.source),
            workdir=self.box.name, capability="csv-cleanup",
            agreed_requirements=AGREED, owner_accepts_quote=True,
            simulate_payment=True)

    def test_twelve_jobs_complete_one_after_another(self):
        started = time.monotonic()
        cycles = [self.one() for _ in range(JOBS)]
        elapsed = time.monotonic() - started

        done = [c for c in cycles if c.completed]
        self.assertEqual(
            len(done), JOBS,
            "stopped: " + "; ".join(
                f"{c.stopped_at}: {c.why_stopped}"
                for c in cycles if not c.completed))

        per_job = elapsed / JOBS
        print(f"\n  {JOBS} jobs x {ROWS} rows in {elapsed:.2f}s "
              f"({per_job * 1000:.0f} ms/job)")
        # Generous by two orders of magnitude. This is not a benchmark; it is
        # a tripwire for something that sleeps.
        self.assertLess(
            per_job, 5.0,
            f"{per_job:.2f}s per job suggests something is waiting rather "
            "than working")

    def test_nothing_in_the_delivery_path_sleeps(self):
        """RULE 6, checked against the source rather than the clock.

        A timing test can be defeated by a short sleep; a structural check
        cannot. Neither alone is enough, so both are here.
        """
        import re

        offenders = []
        for path in sorted(pathlib.Path("solvent").glob("*.py")):
            if path.name in SLEEPS_FOR_SOMEBODY_ELSE:
                continue
            for number, line in enumerate(path.read_text().splitlines(), 1):
                if re.search(r"\b(time\.sleep|asyncio\.sleep)\s*\(", line):
                    offenders.append(f"{path.name}:{number}: {line.strip()}")
        self.assertEqual(
            offenders, [],
            "something in the business path waits on purpose:\n"
            + "\n".join(offenders))

    def test_the_only_excused_sleep_is_outside_the_business_path(self):
        """Guards the exemption above, which is otherwise a way to smuggle
        a delay in. prospecting.py may sleep because a rate limiter pacing
        requests to somebody else's server is courtesy, not a delay to
        delivery -- and because nothing in the live pipeline imports it,
        so a job cannot wait on it however long it waits."""
        import re

        self.assertEqual(SLEEPS_FOR_SOMEBODY_ELSE, {"prospecting.py"})

        importers = []
        for path in sorted(pathlib.Path("solvent").glob("*.py")):
            if path.name in ("prospecting.py", "cli.py"):
                continue
            if re.search(r"^from \.prospecting import|^from \. import prospecting",
                         path.read_text(), re.MULTILINE):
                importers.append(path.name)
        self.assertEqual(
            importers, [],
            "the excused module is now reachable from the live pipeline, so "
            "its sleep is in the business path after all")

    def test_each_job_is_measured_and_the_measurements_accumulate(self):
        for _ in range(3):
            self.one()
        rows = self.s.store.raw_readonly(
            "SELECT capability, wall_seconds FROM execution_measurements")
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(r["wall_seconds"] > 0 for r in rows))

        from solvent.finder import ExecutionHistory

        history = ExecutionHistory.from_store(self.s.store)
        self.assertEqual(history.count("csv-cleanup"), 3)
        expected = history.expected_seconds("csv-cleanup")
        self.assertIsNotNone(expected)
        print(f"  median csv-cleanup execution: {expected * 1000:.0f} ms "
              f"over {ROWS} rows")

    def test_revenue_accumulates_and_stays_marked_simulated(self):
        for _ in range(4):
            self.one()
        self.assertEqual(
            self.s.ledger.real_revenue_cents(), 0,
            "simulated money was counted as real revenue")
        collected = sum(
            r["collected_cents"] for r in
            self.s.store.raw_readonly("SELECT collected_cents FROM payments"))
        self.assertEqual(collected, 4 * 40_000)


if __name__ == "__main__":
    unittest.main()
