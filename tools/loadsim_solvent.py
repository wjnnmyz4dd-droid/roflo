#!/usr/bin/env python3
"""Deterministic load simulation against the real capacity gate.

What is real here and what is not, stated plainly because the whole
value of the exercise depends on the distinction:

REAL -- the capacity ledger, its SQLite tables, the admission
decisions, the idempotency index, the budget arithmetic, the restart
behaviour. Every reservation below goes through ``Capacity.reserve``
exactly as a live job would.

SIMULATED -- the jobs. Nobody offered them, nobody paid for them, no
marketplace was contacted and no skill was executed. Durations come
from a seeded generator, and the clock is virtual: nothing sleeps.
This measures whether the gate holds its promises under load. It does
not measure how fast the five skills run, and it is not evidence of
demand, throughput or revenue.

The criteria for "unreliable" are fixed in UNRELIABLE_IF before any
level is run, so that a level cannot be declared fine after the fact.
Refusing an offered job is not unreliability -- it is the gate doing
the one thing it exists to do.
"""

from __future__ import annotations

import argparse
import heapq
import json
import random
import statistics
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from solvent import capacity as cap                      # noqa: E402
from solvent.finder import ExecutionHistory, Finder      # noqa: E402
from solvent.harness import OWNER, Solvent               # noqa: E402

LEVELS = (1, 5, 10, 25, 50, 100, 250, 500)

#: A job offer stays available this long before somebody else takes it.
OFFER_TTL = 900.0

BUDGET = {
    "max_owed_seconds": 3600.0,
    "max_concurrent_jobs": 8,
    "max_open_bids": 4,
    "cpu_share": 0.5,
    "min_free_disk_bytes": 0,
    "max_execution_workers": 4,
    "max_verification_workers": 2,
    "max_memory_used_percent": 0.0,
    "revision_headroom_fraction": 0.4,
    "deadline_safety_factor": 2.0,
    "halted": False,
}

#: Fixed before the first run. A level is unreliable if any of these is
#: true of it. Each one is a promise broken, never merely a job not won.
UNRELIABLE_IF = {
    "accepted_not_completed": "a job was accepted and not delivered",
    "duplicate_commitments": "one marketplace event became two obligations",
    "budget_exceeded": "more was owed at once than the owner allows",
    "deadline_misses": "a job was delivered after the deadline promised",
    "lost_on_restart": "an obligation did not survive a restart",
    "leaked_reservations": "work finished but its room was never given back",
    "concurrency_exceeded": "more jobs ran at once than the limit allows",
}

OFFER, EXEC_DONE, VERIFY_DONE, REVISE_DONE, RETRY = range(5)


@dataclass(order=True)
class Event:
    at: float
    seq: int
    kind: int = field(compare=False)
    job: "Job | None" = field(compare=False, default=None)


@dataclass
class Job:
    id: str
    capability: str
    offered_at: float
    estimate: float          # what measurement says it should take
    actual: float            # what it really takes in this run
    verify_cost: float
    fails_verification: bool
    revision_cost: float
    deadline: float = 0.0
    accepted_at: float | None = None
    delivered_at: float | None = None
    expired: bool = False
    reservation: str = ""
    revisions: int = 0


class Stress:
    """Named conditions, each either off or on. No hidden dials."""

    def __init__(self, **flags):
        self.spike = flags.get("spike", False)
        self.slow_execution = flags.get("slow_execution", False)
        self.delayed_verification = flags.get("delayed_verification", False)
        self.failed_submissions = flags.get("failed_submissions", False)
        self.revisions = flags.get("revisions", False)
        self.payment_delays = flags.get("payment_delays", False)
        self.outage = flags.get("outage", False)
        self.restart_during = flags.get("restart_during", "")
        #: Quote deadlines the way the first version did -- median times
        #: the safety factor, ignoring the measured tail. Kept so the
        #: deadline criterion can be shown to fire: a criterion nothing
        #: has ever tripped is not evidence of anything.
        self.optimistic_deadlines = flags.get("optimistic_deadlines", False)

    def names(self) -> list:
        return [k for k, v in vars(self).items() if v]


class Simulation:
    """One workload level, run once, on a virtual clock."""

    def __init__(self, level: int, *, seed: int = 20261014,
                 stress: Stress | None = None, budget: dict | None = None):
        self.level = level
        self.stress = stress or Stress()
        # Seeded on the stress conditions but NOT on the level, so that
        # level 100 is level 50's jobs plus fifty more. Reseeding per
        # level gives each one a different job mix and makes the sweep
        # incomparable -- the first version did that, and p95 fell as
        # the load rose, which was the mix moving, not the system.
        self.rng = random.Random(f"{seed}:{sorted(self.stress.names())}")
        self.budget = dict(budget or BUDGET)
        self.box = tempfile.TemporaryDirectory()
        self.db = f"{self.box.name}/load.db"
        self.s = Solvent(self.db)
        self.s.policy.amend({"capacity": self.budget}, OWNER,
                            f"load simulation level {level}")
        self.clock = 0.0
        self.seq = 0
        self.queue: list[Event] = []
        self.jobs: list[Job] = []
        self.pending: list[Job] = []
        self.running = 0
        self.max_running = 0
        self.max_queue_depth = 0
        self.max_owed = 0.0
        self.refusals: dict[str, int] = {}
        self.duplicates_attempted = 0
        self.duplicate_commitments = 0
        self.verification_failures = 0
        self.restarts = 0
        self.lost_on_restart = 0
        self.restart_cost = 0.0
        self.history = ExecutionHistory()
        self.finder = Finder(self.s.discovery, self.s.policy,
                             self.s.capability, self.s.orchestrator,
                             self.s.store)

    # ------------------------------------------------------------ setup

    def push(self, at: float, kind: int, job: Job | None = None) -> None:
        self.seq += 1
        heapq.heappush(self.queue, Event(at, self.seq, kind, job))

    def build(self) -> None:
        """Offers arrive over the window; a spike drops them all at once."""
        window = 0.0 if self.stress.spike else max(1.0, self.level * 30.0)
        # Past runs of each kind of work, drawn from the same generator
        # the simulated jobs are. This is what the real system would
        # have in execution_measurements after a few weeks, and it is
        # what the deadline is promised from -- through the real
        # Finder, not through arithmetic written in this file.
        for size in (45, 90, 180, 300, 600):
            self.history.samples[f"cap-{size}"] = [
                round(size * self.spread(), 1) for _ in range(20)]
        for n in range(self.level):
            estimate = round(self.rng.choice([45, 90, 180, 300, 600]) * 1.0, 1)
            spread = self.spread()
            verify = round(estimate * 0.2, 1)
            if self.stress.delayed_verification:
                verify = round(verify * 5.0, 1)
            fails = self.rng.random() < (0.25 if self.stress.revisions else 0.08)
            # Drawn as a fraction so a job keeps its place in the
            # arrival order whatever the window length.
            arrival = self.rng.random()
            job = Job(
                id=f"job-{n:04d}",
                capability=f"cap-{int(estimate)}",
                offered_at=round(arrival * window, 1),
                estimate=estimate,
                actual=round(estimate * spread, 1),
                verify_cost=verify,
                fails_verification=fails,
                revision_cost=round(estimate * 0.4, 1))
            self.jobs.append(job)
            self.push(job.offered_at, OFFER, job)
        if self.stress.restart_during:
            self.push(max(1.0, window * 0.5), RETRY)

    # ------------------------------------------------- the gate, for real

    def spread(self) -> float:
        """How far a run strays from its nominal size."""
        return (self.rng.uniform(1.8, 4.0) if self.stress.slow_execution
                else self.rng.uniform(0.7, 1.6))

    def promised(self, job: Job) -> float:
        """The deadline DeskPilot would quote -- via the real Finder."""
        if self.stress.optimistic_deadlines:
            factor = max(1.0, float(
                self.budget.get("deadline_safety_factor", 2.0)))
            return (self.clock + job.estimate * factor
                    + self.s.capacity.owed_seconds())
        estimate = self.finder.completion_estimate(job.capability,
                                                   history=self.history)
        promised, _ = self.finder.promised_seconds(
            estimate, queued_seconds=self.s.capacity.owed_seconds(),
            downstream_seconds=job.verify_cost + job.revision_cost)
        return self.clock + promised

    def note_refusal(self, decision) -> None:
        name = decision.refusal.value
        self.refusals[name] = self.refusals.get(name, 0) + 1

    def try_accept(self, job: Job) -> bool:
        if self.stress.outage and 0.35 < (self.clock % 1000) / 1000 < 0.55:
            return False                      # marketplace not answering
        deadline = self.promised(job)
        decision = self.s.capacity.admit(
            job.id, execute_seconds=job.estimate,
            verify_seconds=job.verify_cost, revise_seconds=job.revision_cost,
            idempotency_key=f"accept:{job.id}", why="load simulation")
        if not decision.allowed:
            self.note_refusal(decision)
            return False
        job.accepted_at = self.clock
        job.deadline = deadline
        job.reservation = decision.reservation_id
        self.running += 1
        self.max_running = max(self.max_running, self.running)
        self.max_owed = max(self.max_owed, self.s.capacity.owed_seconds())
        self.push(self.clock + job.actual, EXEC_DONE, job)

        if self.stress.failed_submissions and self.rng.random() < 0.2:
            # The same acceptance delivered twice. One obligation, or
            # the ledger is not doing its job.
            self.duplicates_attempted += 1
            again = self.s.capacity.admit(
                job.id, execute_seconds=job.estimate,
                verify_seconds=job.verify_cost,
                revise_seconds=job.revision_cost,
                idempotency_key=f"accept:{job.id}", why="replayed event")
            if again.allowed:
                self.duplicate_commitments += 1
        return True

    def restart(self) -> None:
        """Close the store and open it again, as a reboot would."""
        before = self.s.capacity.owed_seconds()
        held = {r.id for r in self.s.capacity.outstanding()}
        self.s.store.close()
        self.s = Solvent(self.db)
        after = self.s.capacity.owed_seconds()
        now_held = {r.id for r in self.s.capacity.outstanding()}
        self.restarts += 1
        self.lost_on_restart += len(held - now_held)
        self.restart_cost += abs(before - after)

    # -------------------------------------------------------------- run

    def run(self) -> dict:
        self.build()
        while self.queue:
            event = heapq.heappop(self.queue)
            self.clock = event.at
            self.expire_offers()
            if event.kind == OFFER:
                if not self.try_accept(event.job):
                    self.pending.append(event.job)
            elif event.kind == RETRY:
                if self.stress.restart_during:
                    self.restart()
                self.sweep_pending()
            elif event.kind == EXEC_DONE:
                self.finish_execution(event.job)
            elif event.kind == VERIFY_DONE:
                self.finish_verification(event.job)
            elif event.kind == REVISE_DONE:
                self.finish_revision(event.job)
            self.max_queue_depth = max(self.max_queue_depth,
                                       len(self.pending))
        return self.measure()

    def expire_offers(self) -> None:
        for job in list(self.pending):
            if self.clock - job.offered_at > OFFER_TTL:
                job.expired = True
                self.pending.remove(job)

    def sweep_pending(self) -> None:
        """Room freed up; see whether anything still on offer fits."""
        for job in list(self.pending):
            if self.try_accept(job):
                self.pending.remove(job)

    def finish_execution(self, job: Job) -> None:
        """Execution is done; its room goes back. Verification owns its own.

        No refusal is possible here any more. Admission bought the
        verification and the revision along with the execution, which
        is what stopped this simulation deadlocking: before that, a job
        could finish executing and find no room left to be checked, and
        because its execution reservation was held until delivery,
        nothing ever freed up again.
        """
        self.running -= 1
        self.consume(job, cap.EXECUTE)
        self.push(self.clock + job.verify_cost, VERIFY_DONE, job)

    def finish_verification(self, job: Job) -> None:
        self.consume(job, cap.VERIFY)
        if job.fails_verification and job.revisions == 0:
            self.verification_failures += 1
            job.revisions += 1
            self.push(self.clock + job.revision_cost, REVISE_DONE, job)
            return
        self.deliver(job)

    def finish_revision(self, job: Job) -> None:
        self.consume(job, cap.REVISE)
        self.deliver(job)

    def consume(self, job: Job, kind: str) -> None:
        reservation = self.s.capacity.reservation_for(job.id, kind)
        if reservation:
            self.s.capacity.consume(reservation, "simulated work done")

    def deliver(self, job: Job) -> None:
        # Room held for a revision nobody asked for is given back.
        for kind in (cap.EXECUTE, cap.VERIFY, cap.REVISE):
            reservation = self.s.capacity.reservation_for(job.id, kind)
            if reservation:
                self.s.capacity.release(reservation, "not needed")
        job.delivered_at = self.clock
        if self.stress.payment_delays:
            pass        # payment timing does not free capacity either way
        self.sweep_pending()

    # ---------------------------------------------------------- measure

    def measure(self) -> dict:
        accepted = [j for j in self.jobs if j.accepted_at is not None]
        delivered = [j for j in accepted if j.delivered_at is not None]
        times = sorted((j.delivered_at - j.accepted_at) for j in delivered)
        misses = [j for j in delivered if j.delivered_at > j.deadline]
        leaked = [r for r in self.s.capacity.outstanding()]
        host = self.s.diagnostics.host()
        failures = {
            "accepted_not_completed": len(accepted) - len(delivered),
            "duplicate_commitments": self.duplicate_commitments,
            "budget_exceeded": int(self.max_owed
                                   > self.budget["max_owed_seconds"] + 1e-6),
            "deadline_misses": len(misses),
            "lost_on_restart": self.lost_on_restart,
            "leaked_reservations": len(leaked),
            "concurrency_exceeded": int(
                self.max_running > self.budget["max_concurrent_jobs"]),
        }
        reliable = not any(failures.values())
        return {
            "level": self.level,
            "stress": self.stress.names(),
            "offered": len(self.jobs),
            "accepted": len(accepted),
            "refused_or_expired": len(self.jobs) - len(accepted),
            "delivered": len(delivered),
            "acceptance_rate": round(len(accepted) / len(self.jobs), 3),
            "completion_rate_of_accepted": (
                round(len(delivered) / len(accepted), 3) if accepted else None),
            "mean_completion_seconds": (
                round(statistics.fmean(times), 1) if times else None),
            "p95_completion_seconds": (
                round(times[min(len(times) - 1, int(0.95 * len(times)))], 1)
                if times else None),
            "max_queue_depth": self.max_queue_depth,
            "max_concurrency": self.max_running,
            "peak_owed_seconds": round(self.max_owed, 1),
            "budget_seconds": self.budget["max_owed_seconds"],
            "peak_utilisation": round(
                self.max_owed / self.budget["max_owed_seconds"], 3),
            "verification_failures": self.verification_failures,
            "duplicates_attempted": self.duplicates_attempted,
            "restarts": self.restarts,
            "obligation_seconds_lost_on_restart": round(self.restart_cost, 1),
            "refusals": dict(sorted(self.refusals.items())),
            "virtual_seconds_elapsed": round(self.clock, 1),
            "database_bytes": host.get("database_bytes"),
            "failures": {k: v for k, v in failures.items() if v},
            "reliable": reliable,
        }

    def close(self) -> None:
        try:
            self.s.store.close()
        finally:
            self.box.cleanup()


def run_level(level: int, **stress) -> dict:
    sim = Simulation(level, stress=Stress(**stress))
    try:
        return sim.run()
    finally:
        sim.close()


def sweep(levels=LEVELS, **stress) -> dict:
    results = [run_level(level, **stress) for level in levels]
    unreliable = [r for r in results if not r["reliable"]]
    return {
        "criteria": UNRELIABLE_IF,
        "budget": BUDGET,
        "results": results,
        "first_unreliable_level": unreliable[0]["level"] if unreliable else None,
        "simulated": True,
        "note": ("The gate is real; the jobs are not. No marketplace was "
                 "contacted, no skill was executed and no money moved."),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--levels", default=",".join(str(x) for x in LEVELS))
    parser.add_argument("--stress", default="",
                        help="comma-separated: spike, slow_execution, "
                             "delayed_verification, failed_submissions, "
                             "revisions, payment_delays, outage, "
                             "optimistic_deadlines")
    parser.add_argument("--restart-during", default="")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    levels = [int(x) for x in args.levels.split(",") if x.strip()]
    flags = {name: True for name in args.stress.split(",") if name.strip()}
    if args.restart_during:
        flags["restart_during"] = args.restart_during
    report = sweep(levels, **flags)

    if args.json:
        print(json.dumps(report, indent=2))
        return 0
    head = (f"{'level':>6} {'acc':>5} {'done':>5} {'rate':>6} {'p95':>8} "
            f"{'queue':>6} {'conc':>5} {'util':>6}  verdict")
    print(head)
    print("-" * len(head))
    for r in report["results"]:
        print(f"{r['level']:>6} {r['accepted']:>5} {r['delivered']:>5} "
              f"{str(r['completion_rate_of_accepted']):>6} "
              f"{str(r['p95_completion_seconds']):>8} "
              f"{r['max_queue_depth']:>6} {r['max_concurrency']:>5} "
              f"{r['peak_utilisation']:>6}  "
              f"{'reliable' if r['reliable'] else sorted(r['failures'])}")
    print(f"\nfirst unreliable level: {report['first_unreliable_level']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
