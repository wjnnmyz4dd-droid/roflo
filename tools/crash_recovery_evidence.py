"""Run each crash-recovery case in its own interpreter, and print the trace.

One process per case, because the egress guard is irreversible: the
first test to open a database installs it, and from then on no child
can be spawned. ``pytest`` in one process therefore skips every case
after the first, which looks like a pass and is not one.

Two things come out of a run, and the report quotes both:

**The traces.** For each of the eight injection points, a child is
killed there and the database is reopened; what it held before
recovery, what recovery said, and what it held afterwards are printed
side by side. This is the raw material -- no assertions, just the
state.

**The cases.** The same eight points as assertions, which is where
"no duplicate invoice" and "no slot held by finished work" are
actually checked.

Exit code is non-zero if any case failed or was skipped.

Usage::

    python3 -I tools/crash_recovery_evidence.py
"""

from __future__ import annotations

import argparse
import os
import pathlib
import secrets
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
FILE = "tests_solvent/test_crash_recovery.py"


# ---- the trace of one injection point ------------------------------
#
# Runs as its own process, spawned by `traces` below. It has to be a
# separate process from the one that spawns it: opening a Solvent
# installs the egress guard, which denies `subprocess.Popen` for the
# rest of that interpreter's life.

def snapshot(solvent, label: str) -> None:
    rows = solvent.store.for_authority("ledger").query(
        "SELECT job_id, amount_cents, collected_cents FROM payments")
    print(f"{label} jobs        :",
          [(j.id[:12], j.state.value) for j in solvent.orchestrator.jobs()])
    print(f"{label} engagements :",
          [(k, e.stage.value, e.collected_cents)
           for k, e in solvent.chief_of_staff.engagements.items()])
    print(f"{label} orders      :",
          [(o.order_id, o.state.value) for o in solvent.storefront.orders()])
    print(f"{label} receivables :",
          [(r["job_id"][:12], r["amount_cents"], r["collected_cents"])
           for r in rows])
    print(f"{label} reservations:",
          [(r.job_id[:12], r.kind) for r in solvent.capacity.outstanding()],
          "owed", solvent.capacity.owed_seconds())
    print(f"{label} real revenue:", solvent.ledger.real_revenue_cents())


def trace_one(point: str) -> int:
    sys.path.insert(0, str(ROOT))
    from simulation import rail

    os.environ.setdefault("SOLVENT_OWNER_KEY", secrets.token_hex(32))
    os.environ.setdefault(rail.SECRET_ENV, rail.TEST_SECRET)
    os.environ["PYTHONPATH"] = str(ROOT)

    box = pathlib.Path(tempfile.mkdtemp())
    database = str(box / "shop" / "deskpilot.db")
    done = subprocess.run(
        [sys.executable, "-m", "tests_solvent.crash_child", database,
         str(box / "payments-inbox"), point, "arrive"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=300)
    print("rc:", done.returncode)
    print("stdout:", done.stdout.strip()[:600])
    if done.stderr.strip():
        print("stderr tail:", done.stderr.strip()[-400:])

    # Only now is a Solvent opened, and with it the egress guard.
    from solvent.harness import Solvent
    from solvent.runtime import reconcile
    from solvent.settlement import settle_paid_jobs

    solvent = Solvent(database)
    snapshot(solvent, "BEFORE")
    print("notes:", reconcile(solvent, initiator="crash-recovery-evidence"))
    print("settle:", settle_paid_jobs(
        solvent, initiator="crash-recovery-evidence"))
    snapshot(solvent, "AFTER ")
    solvent.store.close()
    return 0


def traces() -> None:
    from tests_solvent.test_crash_recovery import POINTS

    for point in POINTS:
        print(f"========== KILLED AT: {point} ==========")
        done = subprocess.run(
            [sys.executable, "-I", __file__, "--trace-one", point],
            cwd=str(ROOT), capture_output=True, text=True, timeout=900)
        print(done.stdout.rstrip())
        if done.returncode != 0:
            print(f"(trace runner exited {done.returncode})")
            print(done.stderr[-2000:])
        print()


# ---- the assertions ------------------------------------------------

def cases() -> list:
    out = subprocess.run(
        [sys.executable, "-I", "-m", "pytest", FILE, "--collect-only", "-q",
         "--no-header"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=300)
    return [line.strip() for line in out.stdout.splitlines()
            if "::" in line and line.strip().startswith(FILE)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace-one", default="",
                        help="internal: produce one trace and exit")
    args = parser.parse_args()
    if args.trace_one:
        return trace_one(args.trace_one)

    sys.path.insert(0, str(ROOT))
    print("RECOVERY TRACES")
    print("===============")
    print()
    traces()

    print("CASES")
    print("=====")
    print()
    found = cases()
    if not found:
        print("no cases collected")
        return 1
    bad = []
    for case in found:
        done = subprocess.run(
            [sys.executable, "-I", "-m", "pytest", case, "-q", "--no-header",
             "-p", "no:randomly"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=900)
        tail = [line for line in done.stdout.splitlines() if line.strip()]
        verdict = tail[-1] if tail else "(no output)"
        name = case.split("::", 1)[1]
        print(f"{'PASS' if done.returncode == 0 else 'FAIL'}  {name}")
        print(f"      {verdict}")
        if done.returncode != 0 or " skipped" in verdict:
            bad.append(name)
            print(done.stdout[-3000:])
    print()
    print(f"{len(found) - len(bad)}/{len(found)} cases passed in their own "
          "interpreter")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
