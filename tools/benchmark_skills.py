#!/usr/bin/env python3
"""Measured timings for the five certified skills.

Three numbers per skill, all of them measured on this machine by
actually doing the work:

**execution** -- the skill turning a client's file into a deliverable.
**verification** -- the deterministic checks deciding whether it did.
**end to end** -- the whole business cycle: an opportunity triaged,
qualified, priced, approved, executed, verified, delivered and
reconciled.

The inputs come from ``solvent.certgen``, which builds a source file
and the output that is correct for it in the same step, from a seed.
They are generated rather than borrowed from a client, which is the
point: no client has ever sent DeskPilot a file.

What this is not: a measurement of the Windows VPS, and not a
prediction about a real client's file. A generated 20-row CSV is not
a 200,000-row export, and these numbers do not become a delivery time
without the measured history to back them.

``--record PATH`` writes the execution timings into a real DeskPilot
database through the orchestrator's own measurement path, tagged
BENCHMARK. Every row is an execution that happened, on this machine,
just now -- and none of them makes a skill eligible to bid.

That last part was tempting to get wrong. ``Finder.bid`` refuses to
bid on a capability with no measured runs, so recording seven runs
per skill looks like the way to unblock bidding. It is not: these
timings are on inputs DeskPilot generated for itself, and small ones.
csv-cleanup takes 0.7 ms on a generated 605-byte file, and a bidder
that believed it would promise to clean a client's 200,000-row export
in under a millisecond. ``ExecutionHistory.from_store`` therefore
leaves BENCHMARK rows out of the history that prices and promises
work, and the only thing that makes a skill bid-eligible is having
done that skill for a client.

So what these rows are for: showing the owner that all five skills
run correctly on this host and roughly what they cost, which is
operational evidence rather than a quotable delivery time.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import statistics
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from solvent.finder import ExecutionHistory, Finder      # noqa: E402
from solvent.harness import (CAPABILITIES, OWNER,        # noqa: E402
                             run_business_cycle)

#: The five, in the order the owner lists them.
SKILLS = ("csv-cleanup", "spreadsheet-automation", "document-processing",
          "data-entry", "report-builder")

#: Runs per skill. Five is the point at which `completion_estimate`
#: stops calling an estimate provisional, so fewer would measure
#: something the Finder would still refuse to bid on.
RUNS = 7

#: Used nowhere else in the repository: not the development seed, not
#: the certification seed, not the exam seed. A benchmark that reuses
#: a case the implementation was built against measures the fixture.
BENCHMARK_SEED = 54180933


def _box():
    return tempfile.TemporaryDirectory()


def one_run(capability: str, index: int, box: pathlib.Path) -> dict:
    """One job, timed in three places."""
    from tests_solvent import skillmatrix as matrix

    case = matrix._generate(capability, BENCHMARK_SEED, index)
    requirements = matrix._requirements_for(capability, case)
    source_bytes = matrix._source(case)

    service = CAPABILITIES[capability]
    workdir = box / f"{capability}-{index}"
    workdir.mkdir(parents=True, exist_ok=True)
    # The case names its own file, and the name carries the format.
    # A document-processing source called "source.bin" is refused for
    # not being a document, which is the skill being right.
    source = workdir / (case.source_name or "source.dat")
    source.write_bytes(source_bytes)
    destination = workdir / (
        "deliverable." + service.extension_for_requirements(requirements))

    # --- execution, on its own -------------------------------------
    started = time.perf_counter()
    work = service.execute(source=source, destination=destination,
                           requirements=requirements)
    execute_seconds = time.perf_counter() - started
    if getattr(work, "refused", None):
        raise RuntimeError("execution refused: "
                           + "; ".join(work.refused)[:160])

    # --- verification, on its own ----------------------------------
    # Every check the requirements ask for, which is what verification
    # costs in a real job: timing one check would understate it by as
    # many times as there are requirements.
    baseline = list(requirements)
    started = time.perf_counter()
    checks = 0
    for requirement in requirements:
        params = dict(requirement.params)
        params.setdefault("requirements", baseline)
        service.verify(requirement.check, source=source,
                       output=destination, params=params)
        checks += 1
    verify_seconds = time.perf_counter() - started

    return {"execute_seconds": execute_seconds,
            "verify_seconds": verify_seconds,
            "checks": checks,
            "source_bytes": len(source_bytes),
            "produced_bytes": destination.stat().st_size}


def cycle_run(capability: str, index: int, box: pathlib.Path) -> dict:
    """One job through the whole business pipeline, timed."""
    from tests_solvent import skillmatrix as matrix

    case = matrix._generate(capability, BENCHMARK_SEED, index)
    if matrix.expected_outcome(capability, case) == matrix.ASK:
        # The source is ambiguous and the right outcome is a question,
        # not a delivery. Timing it as an end-to-end job would measure
        # how fast DeskPilot stops, which is not the same number.
        return {"end_to_end_seconds": None, "completed": False,
                "stopped_at": "ambiguous-by-design",
                "why_stopped": "the case is meant to end in a question"}
    requirements = matrix._requirements_for(capability, case)
    source_bytes = matrix._source(case)

    workdir = box / f"cycle-{capability}-{index}"
    workdir.mkdir(parents=True, exist_ok=True)
    solvent = matrix._configured(workdir)
    # A contracting identity, because an invoice cannot be issued
    # without one -- the cycle stops at the invoice gate otherwise,
    # which would have made every end-to-end figure below a timing of
    # how long it takes to refuse.
    from tests_solvent.fixtures import give_an_issuer

    give_an_issuer(solvent)
    try:
        source = workdir / (case.source_name or "source.dat")
        source.write_bytes(source_bytes)
        started = time.perf_counter()
        cycle = run_business_cycle(
            solvent, source_name="owner_entered", source_file=str(source),
            workdir=str(workdir), capability=capability,
            posting={"ref": f"bench-{capability}-{index}",
                     "title": f"{capability} job", "quoted_cents": 40_000,
                     "needs": [capability], "project_state": "CT",
                     "client_ref": "client:benchmark",
                     "body": "generated scenario", "deliverables": []},
            agreed_requirements=requirements, owner_accepts_quote=True,
            simulate_payment=True)
        elapsed = time.perf_counter() - started
        return {"end_to_end_seconds": elapsed, "completed": cycle.completed,
                "stopped_at": cycle.stopped_at,
                "why_stopped": cycle.why_stopped}
    finally:
        try:
            solvent.store.close()
        except Exception:
            pass


def summarise(values) -> dict:
    present = [v for v in values if v is not None]
    if not present:
        return {"n": 0, "median_ms": None, "p95_ms": None}
    ordered = sorted(present)
    return {
        "n": len(ordered),
        "median_ms": round(statistics.median(ordered) * 1000, 2),
        "p95_ms": round(
            ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))] * 1000, 2),
        "slowest_ms": round(ordered[-1] * 1000, 2),
    }


def benchmark(skills=SKILLS, runs: int = RUNS, *, cycles: bool = True) -> dict:
    results = {}
    with _box() as name:
        box = pathlib.Path(name)
        for capability in skills:
            per_run, cycle_seconds, failures = [], [], []
            for index in range(runs):
                try:
                    per_run.append(one_run(capability, index, box))
                except Exception as exc:
                    failures.append(f"execute#{index}: {type(exc).__name__}: "
                                    f"{str(exc)[:160]}")
            if cycles:
                for index in range(runs):
                    try:
                        outcome = cycle_run(capability, index, box)
                        if outcome["stopped_at"] == "ambiguous-by-design":
                            continue
                        if outcome["completed"]:
                            cycle_seconds.append(outcome["end_to_end_seconds"])
                        else:
                            failures.append(
                                f"cycle#{index} stopped at "
                                f"{outcome['stopped_at']}: "
                                f"{str(outcome['why_stopped'])[:160]}")
                    except Exception as exc:
                        failures.append(f"cycle#{index}: "
                                        f"{type(exc).__name__}: "
                                        f"{str(exc)[:160]}")
            results[capability] = {
                "execution": summarise([r["execute_seconds"] for r in per_run]),
                "verification": summarise(
                    [r["verify_seconds"] for r in per_run]),
                "end_to_end": summarise(cycle_seconds),
                "median_source_bytes": (
                    int(statistics.median([r["source_bytes"]
                                           for r in per_run]))
                    if per_run else None),
                "failures": failures,
            }
    return results


def eligibility(database: str = "") -> dict:
    """Which skills an install could bid on, and which it could not.

    Read from the install's own measurement table, never from the
    benchmark above: a timing taken in a temporary database does not
    make a real installation able to promise anything.
    """
    out = {}
    if not database:
        return {skill: {"samples": 0, "eligible": False,
                        "why": "no database given; a fresh install has no "
                               "measured runs and cannot bid on anything"}
                for skill in SKILLS}
    from solvent.harness import Solvent

    solvent = Solvent(database)
    try:
        history = ExecutionHistory.from_store(solvent.store)
        finder = Finder(solvent.discovery, solvent.policy, solvent.capability,
                        solvent.orchestrator, solvent.store)
        for skill in SKILLS:
            estimate = finder.completion_estimate(skill, history=history)
            benchmarks = ExecutionHistory.from_store(
                solvent.store, include_benchmarks=True).count(skill)
            out[skill] = {
                "samples": estimate.samples,
                "benchmark_runs_excluded": benchmarks - estimate.samples,
                "confidence": estimate.confidence.name,
                "median_seconds": estimate.seconds,
                "tail_seconds": estimate.tail_seconds,
                "eligible": estimate.known,
                "why": estimate.basis,
            }
    finally:
        try:
            solvent.store.close()
        except Exception:
            pass
    return out


def record_into(database: str, runs: int = RUNS) -> dict:
    """Execute each skill for real and keep what it took.

    Every row written here is an execution that happened. Nothing is
    estimated, copied from this report, or scaled from another
    machine -- which is the only reason writing it is honest.
    """
    from solvent.harness import Solvent

    solvent = Solvent(database)
    written = {}
    try:
        with _box() as name:
            box = pathlib.Path(name)
            for capability in SKILLS:
                count = 0
                for index in range(runs):
                    try:
                        timing = one_run(capability, index, box)
                    except Exception:
                        continue
                    solvent.orchestrator.record_execution(
                        f"benchmark:{capability}:{index}",
                        capability=capability,
                        wall_seconds=timing["execute_seconds"],
                        cpu_seconds=timing["execute_seconds"],
                        units=timing["source_bytes"],
                        outcome="BENCHMARK")
                    count += 1
                written[capability] = count
    finally:
        try:
            solvent.store.close()
        except Exception:
            pass
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=RUNS)
    parser.add_argument("--no-cycles", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--eligibility", default="",
                        help="path to a DeskPilot database to report on")
    parser.add_argument("--record", default="",
                        help="path to a database to write real measurements "
                             "into by executing each skill")
    args = parser.parse_args()

    if args.record:
        written = record_into(args.record, args.runs)
        print(json.dumps({"recorded": written}, indent=2))
        return 0

    report = {"seed": BENCHMARK_SEED, "runs": args.runs,
              "skills": benchmark(runs=args.runs, cycles=not args.no_cycles),
              "eligibility": eligibility(args.eligibility)}
    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    head = (f"{'skill':24}{'exec ms':>10}{'verify ms':>11}"
            f"{'end-to-end ms':>15}{'bytes':>8}  notes")
    print(head)
    print("-" * len(head))
    for skill, row in report["skills"].items():
        print(f"{skill:24}"
              f"{str(row['execution']['median_ms']):>10}"
              f"{str(row['verification']['median_ms']):>11}"
              f"{str(row['end_to_end']['median_ms']):>15}"
              f"{str(row['median_source_bytes']):>8}  "
              f"{(row['failures'][0][:60] + '...') if row['failures'] else ''}")
    print("\nmedians over "
          f"{args.runs} runs each, seed {BENCHMARK_SEED}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
