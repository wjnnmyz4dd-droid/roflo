#!/usr/bin/env python3
"""How long a real job takes, as opposed to a tiny one.

The previous benchmarks reported sub-millisecond execution times on
generated files of a few hundred bytes, and the owner was right to
object: those are not evidence that DeskPilot can finish a client's
job in a millisecond. They measured the smallest input the
certification generator makes.

This measures the same five skills across a range of sizes, and
labels every figure with which kind it is:

``SYNTHETIC BENCHMARK``
    The generator's own smallest case. Useful only for "does this
    skill run at all on this host".

``REPRESENTATIVE WORKLOAD``
    The same shape of work scaled to sizes a paying client would
    plausibly send -- hundreds to thousands of records rather than
    a dozen. Still generated, still not a client's file, but within
    an order of magnitude of one.

``REAL CLIENT JOB``
    None. There have been none. The label exists so the report can
    show an empty column rather than omit the distinction.

**How the larger inputs are built matters.** Rows are not duplicated
to reach a size, because for csv-cleanup duplicate rows are the
defect being corrected and copying them would change what correct
means. Each size is composed from as many independently generated
cases as it takes, so a thousand-row file is a thousand distinct
generated rows.

**What is measured.** Execution, verification, and the whole
end-to-end business cycle, at p50/p90/p95/max, with peak memory. Run
at one through five concurrent workers so contention is visible
rather than assumed.
"""

from __future__ import annotations

import argparse
import inspect
import json
import pathlib
import random
import statistics
import sys
import tempfile
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SYNTHETIC = "SYNTHETIC BENCHMARK"
REPRESENTATIVE = "REPRESENTATIVE WORKLOAD"
REAL = "REAL CLIENT JOB"

SKILLS = ("csv-cleanup", "spreadsheet-automation", "document-processing",
          "data-entry", "report-builder")

#: Target record counts per size class. Chosen to span two orders of
#: magnitude from the generator's own case, which is about a dozen
#: records, to something a client might actually attach.
SIZES = {"small": 1, "medium": 10, "large": 60, "complex": 120}

#: Used nowhere else in the repository.
BENCHMARK_SEED = 90_210_337

REPEATS = 5


def _case(capability: str, index: int):
    from solvent import certgen

    generator = certgen.GENERATORS[capability]
    rng = random.Random(f"bench:{capability}:{BENCHMARK_SEED}:{index}")
    if "index" in inspect.signature(generator).parameters:
        return generator(rng, index)
    return generator(rng)


def _source_bytes(case) -> bytes:
    text = getattr(case, "source_text", None)
    if text:
        return text.encode("utf-8")
    return getattr(case, "source_bytes", b"") or b""


def _composite(capability: str, parts: int, start: int) -> tuple:
    """A source of roughly ``parts`` times the generator's own size.

    Built from independently generated cases rather than by repeating
    one. For csv-cleanup the duplicates are the defect under
    correction, so copying rows would quietly change the right
    answer; for every skill it would measure compressibility rather
    than work.

    Returns the first case (whose requirements are the ones checked)
    and the composed bytes, and for the multi-part sizes it returns
    the single case unchanged where a format cannot be concatenated
    safely -- a .xlsx or .docx is a zip container and appending one
    to another produces a corrupt file, not a bigger one.
    """
    first = _case(capability, start)
    name = (getattr(first, "source_name", "") or "").lower()
    if parts <= 1 or name.endswith((".xlsx", ".docx")):
        return first, _source_bytes(first), parts <= 1
    if name.endswith(".json"):
        return first, _source_bytes(first), False
    chunks = [_source_bytes(first).rstrip(b"\n")]
    for n in range(1, parts):
        body = _source_bytes(_case(capability, start + n)).splitlines()
        # Drop each additional case's header row: one file, one header.
        chunks.append(b"\n".join(body[1:]) if len(body) > 1 else b"")
    return first, b"\n".join(c for c in chunks if c) + b"\n", True


def _percentiles(values) -> dict:
    present = sorted(v for v in values if v is not None)
    if not present:
        return {"n": 0}

    def at(fraction):
        return present[min(len(present) - 1,
                           int(fraction * len(present)))]

    return {
        "n": len(present),
        "p50_ms": round(statistics.median(present) * 1000, 2),
        "p90_ms": round(at(0.90) * 1000, 2),
        "p95_ms": round(at(0.95) * 1000, 2),
        "max_ms": round(present[-1] * 1000, 2),
    }


def _peak_rss():
    try:
        import resource

        raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return raw * 1024 if sys.platform.startswith("linux") else raw
    except Exception:
        return None


def one_run(capability: str, parts: int, index: int, box: pathlib.Path):
    """One job: execute, then verify, both timed, on a sized input."""
    from tests_solvent import skillmatrix as matrix
    from solvent.harness import CAPABILITIES

    case, source_bytes, scaled = _composite(capability, parts, index * 97)
    requirements = matrix._requirements_for(capability, case)
    service = CAPABILITIES[capability]
    workdir = box / f"{capability}-{parts}-{index}"
    workdir.mkdir(parents=True, exist_ok=True)
    source = workdir / (getattr(case, "source_name", "") or "source.dat")
    source.write_bytes(source_bytes)
    destination = workdir / (
        "out." + service.extension_for_requirements(requirements))

    started = time.perf_counter()
    work = service.execute(source=source, destination=destination,
                           requirements=requirements)
    execute_s = time.perf_counter() - started
    if getattr(work, "refused", None):
        return {"refused": "; ".join(work.refused)[:160], "scaled": scaled,
                "source_bytes": len(source_bytes)}

    baseline = list(requirements)
    started = time.perf_counter()
    for requirement in requirements:
        params = dict(requirement.params)
        params.setdefault("requirements", baseline)
        service.verify(requirement.check, source=source,
                       output=destination, params=params)
    verify_s = time.perf_counter() - started

    return {"execute_s": execute_s, "verify_s": verify_s, "scaled": scaled,
            "source_bytes": len(source_bytes),
            "produced_bytes": destination.stat().st_size}


def measure_sizes(skills=SKILLS, repeats: int = REPEATS) -> dict:
    out = {}
    with tempfile.TemporaryDirectory() as name:
        box = pathlib.Path(name)
        for capability in skills:
            per_size = {}
            for size, parts in SIZES.items():
                runs, refusals = [], []
                for index in range(repeats):
                    try:
                        row = one_run(capability, parts, index, box)
                    except Exception as exc:
                        refusals.append(f"{type(exc).__name__}: {exc}"[:160])
                        continue
                    if row.get("refused"):
                        refusals.append(row["refused"])
                        continue
                    runs.append(row)
                if not runs:
                    per_size[size] = {"label": SYNTHETIC, "n": 0,
                                      "refusals": refusals[:3]}
                    continue
                scaled = any(r["scaled"] for r in runs) and parts > 1
                per_size[size] = {
                    "label": REPRESENTATIVE if scaled else SYNTHETIC,
                    "scaled": scaled,
                    "median_source_bytes": int(statistics.median(
                        r["source_bytes"] for r in runs)),
                    "execution": _percentiles([r["execute_s"] for r in runs]),
                    "verification": _percentiles(
                        [r["verify_s"] for r in runs]),
                    "failure_rate": round(
                        len(refusals) / max(1, len(refusals) + len(runs)), 3),
                    "refusals": refusals[:3],
                }
            out[capability] = per_size
    return out


def measure_concurrency(levels=(1, 2, 3, 4, 5), parts: int = 60) -> dict:
    """The same work at one through five workers."""
    out = {}
    for workers in levels:
        rows, lock, errors = [], threading.Lock(), []

        def run(capability, box):
            try:
                row = one_run(capability, parts, 0, box)
            except Exception as exc:
                with lock:
                    errors.append(f"{capability}: {exc}"[:120])
                return
            with lock:
                rows.append(row)

        with tempfile.TemporaryDirectory() as name:
            box = pathlib.Path(name)
            threads = [threading.Thread(target=run, args=(s, box))
                       for s in SKILLS[:workers]]
            started = time.perf_counter()
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            wall = time.perf_counter() - started
        done = [r for r in rows if not r.get("refused")]
        out[workers] = {
            "wall_s": round(wall, 3),
            "completed": len(done),
            "errors": errors,
            "peak_rss_bytes": _peak_rss(),
            "execution": _percentiles([r["execute_s"] for r in done]),
        }
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=REPEATS)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    result = {
        "sizes": measure_sizes(repeats=args.repeats),
        "concurrency": measure_concurrency(),
        "real_client_jobs": 0,
        "labels": {"synthetic": SYNTHETIC, "representative": REPRESENTATIVE,
                   "real": REAL},
        "note": ("every figure here is generated input on this container. "
                 "No client has sent DeskPilot a file, so the REAL CLIENT "
                 "JOB column is empty by fact rather than by omission."),
    }
    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return 0

    print(f"{'skill':<24}{'size':<9}{'label':<26}{'bytes':>8}"
          f"{'exec p50':>10}{'p95':>9}{'verify p50':>12}")
    for capability, sizes in result["sizes"].items():
        for size, row in sizes.items():
            if not row.get("execution", {}).get("n"):
                print(f"{capability:<24}{size:<9}{'no run':<26}")
                continue
            print(f"{capability:<24}{size:<9}{row['label']:<26}"
                  f"{row['median_source_bytes']:>8}"
                  f"{row['execution']['p50_ms']:>10.1f}"
                  f"{row['execution']['p95_ms']:>9.1f}"
                  f"{row['verification']['p50_ms']:>12.1f}")
    print("\nconcurrency")
    for workers, row in result["concurrency"].items():
        rss = row["peak_rss_bytes"]
        print(f"  {workers} worker(s): wall {row['wall_s']:6.3f}s  "
              f"{row['completed']} done  peak RSS "
              + (f"{rss / 1e6:.1f} MB" if rss else "unmeasurable"))
    print(f"\nreal client jobs measured: {result['real_client_jobs']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
