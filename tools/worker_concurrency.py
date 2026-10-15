#!/usr/bin/env python3
"""What five workers actually cost when they run at the same time.

The operating model says five skills, each holding four jobs and
running one. Two questions follow from it and neither is answerable
from the design:

1. Can five workers run simultaneously without interfering with each
   other -- and if they slow down, by how much?
2. Can each of the five hold four committed jobs at once, in the real
   gate against the real database, rather than in a diagram?

This measures both, by doing them. Every job below is a real end-to-end
business cycle through ``run_business_cycle``: a real file produced by
a real skill, verified by the real checks, through the real capacity
gate and the real SQLite store. Nothing is stubbed and nothing is
slept.

**What this is not.** It is a measurement of *this* machine -- a Linux
container -- and not of the Windows VPS the owner intends to run on.
The two differ in processor, disk, filesystem and the presence of
MetaTrader 5, which is the whole reason the owner caps concurrency in
the first place. Peak memory below comes from ``resource.getrusage``,
which does not exist on Windows; the VPS figure is unmeasurable
through anything currently installed there, and this tool says so
rather than extrapolating. Treat the numbers as a floor on cost and a
ceiling on speed, not as a forecast.

The sequential baseline runs the same jobs one after another. The
comparison between the two is the only thing here that is really about
concurrency: a parallel run that is no faster than the sequential one
means the workers are queueing behind something, and a parallel run
whose per-job latency matches the sequential one means they are not
interfering at all.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import platform
import statistics
import sys
import tempfile
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from solvent import capacity as cap                      # noqa: E402
from solvent.harness import OWNER, run_business_cycle    # noqa: E402
from solvent.types import (JOBS_PER_AGENT_CEILING,       # noqa: E402
                           JOBS_PER_AGENT_DEFAULT)

SKILLS = ("csv-cleanup", "spreadsheet-automation", "document-processing",
          "data-entry", "report-builder")

#: Not the development seed, not the certification seed, not the
#: benchmark seed, not the exam seed. A concurrency measurement that
#: reuses a case the code was built against measures the fixture.
CONCURRENCY_SEED = 71_552_309

JOBS_PER_WORKER = 4


def _peak_rss_bytes():
    """Peak resident set size, or None where it cannot be read.

    None is a real answer here and is reported as one. Windows has no
    ``resource`` module, so on the owner's VPS this returns None and
    the report says the figure is unavailable rather than guessing it.
    """
    try:
        import resource
    except ImportError:
        return None
    try:
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    except (OSError, ValueError):
        return None
    # Linux reports kilobytes, macOS bytes. Both are plausible; the
    # unit is part of the measurement, so pick it from the platform
    # rather than from the magnitude.
    return peak * 1024 if sys.platform.startswith("linux") else peak


def _one_cycle(capability: str, index: int, box: pathlib.Path) -> dict:
    """One real job, start to finish, timed."""
    from tests_solvent import skillmatrix as matrix
    from tests_solvent.fixtures import give_an_issuer

    case = matrix._generate(capability, CONCURRENCY_SEED, index)
    if matrix.expected_outcome(capability, case) == matrix.ASK:
        return {"capability": capability, "index": index, "seconds": None,
                "completed": False, "stopped_at": "ambiguous-by-design"}
    requirements = matrix._requirements_for(capability, case)

    workdir = box / f"{capability}-{index}"
    workdir.mkdir(parents=True, exist_ok=True)
    solvent = matrix._configured(workdir)
    give_an_issuer(solvent)
    try:
        source = workdir / (case.source_name or "source.dat")
        source.write_bytes(matrix._source(case))
        started = time.perf_counter()
        cycle = run_business_cycle(
            solvent, source_name="owner_entered", source_file=str(source),
            workdir=str(workdir), capability=capability,
            posting={"ref": f"conc-{capability}-{index}",
                     "title": f"{capability} job", "quoted_cents": 40_000,
                     "needs": [capability], "project_state": "CT",
                     "client_ref": "client:concurrency",
                     "body": "generated scenario", "deliverables": []},
            agreed_requirements=requirements, owner_accepts_quote=True,
            simulate_payment=True)
        return {"capability": capability, "index": index,
                "seconds": time.perf_counter() - started,
                "completed": cycle.completed,
                "stopped_at": cycle.stopped_at}
    finally:
        try:
            solvent.store.close()
        except Exception:
            pass


def _latencies(rows):
    present = [r["seconds"] for r in rows if r["seconds"] is not None]
    if not present:
        return {"n": 0}
    ordered = sorted(present)
    return {
        "n": len(ordered),
        "median_ms": round(statistics.median(ordered) * 1000, 1),
        "slowest_ms": round(ordered[-1] * 1000, 1),
        "total_work_seconds": round(sum(ordered), 3),
    }


def sequential(jobs_per_skill: int) -> dict:
    """The baseline: the same jobs, one worker, one after another."""
    rows = []
    with tempfile.TemporaryDirectory() as name:
        box = pathlib.Path(name)
        started = time.perf_counter()
        for capability in SKILLS:
            for index in range(jobs_per_skill):
                rows.append(_one_cycle(capability, index, box))
        wall = time.perf_counter() - started
    return {"mode": "sequential", "workers": 1,
            "wall_seconds": round(wall, 3),
            "jobs": len(rows),
            "completed": sum(1 for r in rows if r["completed"]),
            "latency": _latencies(rows),
            "peak_rss_bytes": _peak_rss_bytes()}


def parallel(jobs_per_skill: int) -> dict:
    """Five workers, one per skill, running at the same time."""
    rows, lock = [], threading.Lock()
    errors = []

    def worker(capability: str, box: pathlib.Path):
        for index in range(jobs_per_skill):
            try:
                row = _one_cycle(capability, index, box)
            except Exception as exc:                      # measured, not hidden
                with lock:
                    errors.append(f"{capability}[{index}]: {exc!r}"[:300])
                continue
            with lock:
                rows.append(row)

    with tempfile.TemporaryDirectory() as name:
        box = pathlib.Path(name)
        threads = [threading.Thread(target=worker, args=(s, box), daemon=True)
                   for s in SKILLS]
        started = time.perf_counter()
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        wall = time.perf_counter() - started
    return {"mode": "parallel", "workers": len(SKILLS),
            "wall_seconds": round(wall, 3),
            "jobs": len(rows),
            "completed": sum(1 for r in rows if r["completed"]),
            "errors": errors,
            "latency": _latencies(rows),
            "peak_rss_bytes": _peak_rss_bytes()}


def pipelines_at_depth() -> dict:
    """Can each of the five hold four at once? Ask the real gate.

    One database, one gate, five skills, four admissions each. The
    numbers that come back are the gate's own, read after the fact,
    not a tally kept by this tool.
    """
    from tests_solvent import skillmatrix as matrix

    with tempfile.TemporaryDirectory() as name:
        workdir = pathlib.Path(name)
        solvent = matrix._configured(workdir)
        try:
            # Start from whatever the policy already holds and change
            # only what this measurement is about. The shipped default
            # has cpu_share 0.0 and max_owed_seconds 0.0, which the
            # gate refuses rather than reading as unlimited, so a
            # measurement run has to state a budget to have one.
            budget = dict(solvent.policy.get("capacity", default={}) or {})
            budget.update({
                "skills": list(SKILLS),
                "pipeline_target": JOBS_PER_AGENT_CEILING,
                "replenish_at": 2,
                "max_concurrent_jobs": JOBS_PER_AGENT_CEILING,
                "max_owed_seconds": 10_000_000.0,
                "max_execution_workers": len(SKILLS),
                "max_verification_workers": len(SKILLS),
                "max_open_bids": JOBS_PER_AGENT_CEILING,
                # Half the machine. The ship-shipped default is 0.0,
                # which is refused rather than treated as unlimited.
                "cpu_share": 0.5,
            })
            solvent.policy.amend({"capacity": budget}, OWNER,
                                 "concurrency measurement")
            admitted, started_count, refused = {}, 0, []
            for skill in SKILLS:
                admitted[skill] = 0
                for n in range(JOBS_PER_AGENT_CEILING):
                    decision = solvent.capacity.admit(
                        f"{skill}-{n}", execute_seconds=60.0,
                        capability=skill,
                        idempotency_key=f"{skill}:{n}")
                    if decision.allowed:
                        admitted[skill] += 1
                    else:
                        refused.append(f"{skill}-{n}: {decision.why}"[:200])
            for skill in SKILLS:
                held = solvent.capacity.reservation_for(f"{skill}-0",
                                                        cap.EXECUTE)
                if held and solvent.capacity.start(held).allowed:
                    started_count += 1
            sixth = solvent.capacity.reservation_for("csv-cleanup-1",
                                                     cap.EXECUTE)
            sixth_allowed = bool(sixth) and solvent.capacity.start(
                sixth).allowed
            return {
                "admitted_per_skill": admitted,
                "total_committed": sum(admitted.values()),
                "workers_started": started_count,
                "a_sixth_worker_started": sixth_allowed,
                "refusals": refused,
                "pipelines": solvent.capacity.pipelines(),
            }
        finally:
            try:
                solvent.store.close()
            except Exception:
                pass


def measure(jobs_per_skill: int = JOBS_PER_WORKER) -> dict:
    seq = sequential(jobs_per_skill)
    par = parallel(jobs_per_skill)
    speedup = (round(seq["wall_seconds"] / par["wall_seconds"], 2)
               if par["wall_seconds"] else None)
    slowdown = None
    if seq["latency"].get("median_ms") and par["latency"].get("median_ms"):
        slowdown = round(par["latency"]["median_ms"]
                         / seq["latency"]["median_ms"], 2)
    return {
        "host": {
            # Composed by hand rather than from ``platform.platform()``,
            # which shells out for the processor name and is refused by
            # the egress guard -- correctly, since a subprocess outside
            # an Action Gate window is a subprocess outside the gate
            # whoever started it.
            "platform": " ".join((platform.system(), platform.release(),
                                  platform.machine())),
            "python": platform.python_version(),
            "is_the_owners_vps": False,
            "peak_rss_measurable": _peak_rss_bytes() is not None,
            "cpu_percent_measurable": False,
            "note": "measured on this container, not on the Windows VPS; "
                    "MetaTrader 5 is not running here and is the reason "
                    "the owner caps concurrency at all",
        },
        "defaults": {"jobs_per_agent": JOBS_PER_AGENT_DEFAULT,
                     "ceiling": JOBS_PER_AGENT_CEILING},
        "jobs_per_skill": jobs_per_skill,
        "sequential": seq,
        "parallel": par,
        "wall_clock_speedup": speedup,
        "per_job_slowdown_under_contention": slowdown,
        "pipeline_depth": pipelines_at_depth(),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs-per-skill", type=int, default=JOBS_PER_WORKER)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    result = measure(args.jobs_per_skill)
    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return 0

    seq, par = result["sequential"], result["parallel"]
    print(f"host: {result['host']['platform']} (NOT the owner's VPS)")
    print(f"jobs: {args.jobs_per_skill} per skill, "
          f"{len(SKILLS)} skills\n")
    for row in (seq, par):
        rss = row["peak_rss_bytes"]
        print(f"{row['mode']:>11}  {row['workers']} worker(s)  "
              f"wall {row['wall_seconds']:7.3f}s  "
              f"{row['completed']}/{row['jobs']} completed  "
              f"median {row['latency'].get('median_ms')} ms  "
              f"peak RSS "
              + (f"{rss / 1e6:.1f} MB" if rss else "unmeasurable"))
    print(f"\nwall-clock speedup: {result['wall_clock_speedup']}x")
    print("per-job slowdown under contention: "
          f"{result['per_job_slowdown_under_contention']}x")
    depth = result["pipeline_depth"]
    print(f"\ncommitted per skill: {depth['admitted_per_skill']}")
    print(f"total committed: {depth['total_committed']}  "
          f"workers started: {depth['workers_started']}  "
          f"a sixth worker started: {depth['a_sixth_worker_started']}")
    if par["errors"]:
        print("\nerrors under contention:")
        for line in par["errors"]:
            print("   ", line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
