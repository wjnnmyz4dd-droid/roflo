"""Capacity, commitments, receivables and exposure, counted separately.

The assignment asks for four controls to stay apart:

    A. Execution capacity.
    B. Accepted contractual commitments.
    C. Outstanding receivables.
    D. Financial exposure.

and for proof that a shop whose clients pay late, or never, does not
seize permanently. The way to show that is to run one and look.

Two runs of the same length over the same simulated clients, under
the real scheduler:

* **paying** -- the simulated provider drops signed webhooks into the
  spool, the production ``payments`` task reads them, and the
  production Ledger decides what they mean;
* **nobody pays** -- no spool at all, so every delivered job stays a
  receivable for ever.

The second run is the interesting one. If capacity is tied to payment
the shop stops, and the whole point of the repair is that it is tied
to *delivery* instead. What stops a shop in the second run is the
owner's exposure ceiling, which is a different control, says so, and
lifts the moment somebody pays.

Usage::

    python3 -I tools/capacity_accounting.py [--days 12]
"""

from __future__ import annotations

import argparse
import collections
import datetime
import os
import pathlib
import secrets
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

#: A Monday, so a run of a few days is a run of working days.
START = datetime.date(2026, 1, 5)


def books(solvent) -> dict:
    """The four counters, each read from the authority that owns it."""
    from solvent.qualification import OCCUPIES_CAPACITY

    jobs = list(solvent.orchestrator.jobs())
    rows = solvent.store.for_authority("ledger").query(
        "SELECT job_id, amount_cents, collected_cents FROM payments")
    outstanding = sum(int(r["amount_cents"]) - int(r["collected_cents"])
                      for r in rows
                      if int(r["collected_cents"]) < int(r["amount_cents"]))
    return {
        # A. Execution capacity.
        "owed_seconds": solvent.capacity.owed_seconds(),
        "reservations": len(solvent.capacity.outstanding()),
        "holding_a_slot": len([j for j in jobs
                               if j.state in OCCUPIES_CAPACITY]),
        # B. Accepted contractual commitments.
        "engagements": len(solvent.chief_of_staff.engagements),
        "orders_open": len([o for o in solvent.storefront.orders()
                            if o.state.value not in ("CLOSED", "DECLINED")]),
        # C. Outstanding receivables.
        "receivables_cents": outstanding,
        "invoices": len(rows),
        # D. Financial exposure. The Governor's own figure, read from
        # the Governor: work committed and not yet verified as paid.
        "exposure_cents": solvent.governor.committed_unverified_cents(),
        "real_revenue_cents": solvent.ledger.real_revenue_cents(),
        "states": dict(sorted(collections.Counter(
            j.state.value for j in jobs).items())),
    }


def room(solvent) -> str:
    """Can this shop still take the next job? The question that matters."""
    verdict = solvent.capacity.room_for(3_600.0, kind="EXECUTE",
                                        capability="csv-cleanup")
    return f"{'yes' if verdict.allowed else 'NO'} -- {verdict.why}"


def one_run(label: str, days: int, *, paying: bool) -> dict:
    from solvent.runtime import Runtime

    from simulation.calendar import Calendar
    from simulation.customers import Population
    from simulation.shop import open_a_shop
    from simulation.world import World

    box = tempfile.TemporaryDirectory()
    root = pathlib.Path(box.name)
    spool = root / "payments-inbox"
    solvent = open_a_shop(root / "shop", policy={
        "runtime": {"payment_spool": str(spool)}})
    try:
        calendar = Calendar(start=START, length=days)
        clock = calendar.clock()
        runtime = Runtime(solvent, clock=clock)
        runtime.start()
        runtime.default_tasks()
        population = Population(seed="capacity", length_days=days,
                                arrivals_per_day=1.0)
        population.build([d.index for d in calendar.days() if d.working])
        world = World(solvent=solvent, population=population,
                      workroot=root / "world",
                      spool=spool if paying else None)

        print(f"--- {label} ---")
        header = (f"{'day':>4}  {'owed_s':>8} {'resv':>5} {'slots':>5}  "
                  f"{'engs':>5} {'orders':>6}  {'recv_c':>8} {'inv':>4}  "
                  f"states")
        print(header)
        for day in calendar.run(runtime, clock=clock,
                                before_day=lambda d, c:
                                world.open_for_business(d)):
            now = books(solvent)
            print(f"{day.index:>4}  {now['owed_seconds']:>8.0f} "
                  f"{now['reservations']:>5} {now['holding_a_slot']:>5}  "
                  f"{now['engagements']:>5} {now['orders_open']:>6}  "
                  f"{now['receivables_cents']:>8} {now['invoices']:>4}  "
                  f"{now['states']}")

        final = books(solvent)
        final["room_for_the_next_job"] = room(solvent)
        final["unbacked_acts"] = world.unbacked_acts
        print()
        for key, value in final.items():
            print(f"  {key:<24} {value}")
        print()
        return final
    finally:
        solvent.store.close()
        box.cleanup()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=12)
    args = parser.parse_args()

    os.environ.setdefault("SOLVENT_OWNER_KEY", secrets.token_hex(32))
    from simulation import rail
    os.environ.setdefault(rail.SECRET_ENV, rail.TEST_SECRET)

    paid = one_run(f"{args.days} business days, clients pay", args.days,
                   paying=True)
    unpaid = one_run(f"{args.days} business days, nobody ever pays",
                     args.days, paying=False)

    print("=" * 70)
    print("A. EXECUTION CAPACITY")
    print(f"   paying      owed {paid['owed_seconds']:.0f}s, "
          f"{paid['reservations']} reservation(s); next job: "
          f"{paid['room_for_the_next_job']}")
    print(f"   nobody pays owed {unpaid['owed_seconds']:.0f}s, "
          f"{unpaid['reservations']} reservation(s); next job: "
          f"{unpaid['room_for_the_next_job']}")
    print("B. ACCEPTED COMMITMENTS")
    print(f"   paying      {paid['engagements']} engagement(s), "
          f"{paid['orders_open']} order(s) open")
    print(f"   nobody pays {unpaid['engagements']} engagement(s), "
          f"{unpaid['orders_open']} order(s) open")
    print("C. OUTSTANDING RECEIVABLES")
    print(f"   paying      {paid['receivables_cents']}c over "
          f"{paid['invoices']} invoice(s)")
    print(f"   nobody pays {unpaid['receivables_cents']}c over "
          f"{unpaid['invoices']} invoice(s)")
    print("D. FINANCIAL EXPOSURE / REVENUE")
    print(f"   paying      exposure {paid['exposure_cents']}, "
          f"real revenue {paid['real_revenue_cents']}c")
    print(f"   nobody pays exposure {unpaid['exposure_cents']}, "
          f"real revenue {unpaid['real_revenue_cents']}c")
    print()
    print("The claim being tested: a shop nobody pays still has its "
          "execution\ncapacity back, because capacity is released by "
          "delivery and not by\nsettlement, while the receivable stays "
          "outstanding and uncollected.")
    seized = not unpaid["room_for_the_next_job"].startswith("yes")
    print(f"\nSEIZED PERMANENTLY ON NON-PAYMENT: {'YES' if seized else 'no'}")
    return 1 if unpaid["owed_seconds"] > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
