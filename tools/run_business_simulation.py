"""One shop, one market, ninety simulated days, graded by somebody else.

This is the harness the 30/60/90-day assignment asks for. It owns no
business logic: it opens a real ``Solvent`` through
``simulation.shop``, lets ``simulation.world`` be the clients, ticks
the production scheduler through ``simulation.calendar``, accrues the
owner's business-level costs in ``simulation.books``, and hands the
whole thing to ``simulation.audit`` to be reconciled.

What it does decide is **when the owner acts**: switching a skill on
or off on the configuration's change day, halting for a provider
outage, and raising the recorded cost rates as the market inflates
them. Each of those is performed through the production authority
that owns it, so the run contains owner acts rather than simulator
shortcuts.

Snapshots are cumulative. A ninety-day run is read at day 30, day 60
and day 90 rather than run three times, so the thirty-day figure is
the first thirty days *of the same business* -- the same clients, the
same contracts, the same unpaid invoices carried forward.

Usage::

    python3 -I tools/run_business_simulation.py --configuration A
    python3 -I tools/run_business_simulation.py --all --days 90
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import secrets
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

#: A Monday, so day zero is a working day.
START = datetime.date(2026, 1, 5)

#: Where cumulative figures are taken. The assignment's three periods.
SNAPSHOTS = (30, 60, 90)


def _owner_raises_their_rates(solvent, journal, multiplier, base) -> None:
    """The market got dearer, so the owner writes down new rates.

    Through ``PolicyStore.amend``, because the owner's cost model is
    the owner's: a simulation that edited the rates in place would be
    a second cost authority, and the quoted price would stop matching
    the recorded cost.
    """
    from solvent.harness import OWNER

    raised = {key: int(round(value * multiplier))
              for key, value in base.items()}
    solvent.policy.amend({"commercial": raised}, OWNER,
                         f"the owner's costs rose to {multiplier:.2f}x")
    journal.reread(solvent)


def run_one(*, configuration, conditions, days: int, seed: str,
            root: pathlib.Path, arrivals: float = 1.5,
            verbose: bool = False) -> dict:
    """One configuration under one market. Returns the graded result."""
    from solvent.harness import OWNER
    from solvent.runtime import Runtime
    from solvent.types import OperatingMode

    from simulation import audit, books, rail
    from simulation.calendar import Calendar
    from simulation.customers import Population
    from simulation.market import Market
    from simulation.shop import open_a_shop
    from simulation.skills import sell
    from simulation.world import World

    root = pathlib.Path(root)
    root.mkdir(parents=True, exist_ok=True)
    spool = root / "payments-inbox"
    journal_path = root / "books.jsonl"

    market = Market(conditions, seed=f"{seed}:market", length_days=days)
    solvent = open_a_shop(root / "shop",
                          policy={"runtime": {"payment_spool": str(spool)}})
    started = time.time()
    try:
        sell(solvent, configuration.start,
             why=f"configuration {configuration.name}: "
                 f"{configuration.description}")
        journal = books.Journal.for_shop(solvent, journal_path)
        base_rates = {key: int(solvent.policy.get("commercial", key, default=0)
                               or 0)
                      for key in ("compute_cents_per_cpu_hour",
                                  "verification_cents_per_job",
                                  "expected_revision_cents_per_job",
                                  "acquisition_cents_per_job",
                                  "supervision_cents_per_job",
                                  "subscription_cents_per_day")}

        calendar = Calendar(start=START, length=days)
        clock = calendar.clock()
        runtime = Runtime(solvent, clock=clock)
        runtime.start()
        runtime.default_tasks()

        population = Population(seed=f"{seed}:people", length_days=days,
                                arrivals_per_day=arrivals)
        population.build(
            [d.index for d in calendar.days() if d.working],
            rate_for_day=market.arrivals_on,
            budget_scale=conditions.budgets)
        world = World(solvent=solvent, population=population,
                      workroot=root / "world", spool=spool, market=market)

        snapshots: dict = {}
        daily: list = []
        halted_days: list = []
        changed_on: list = []
        last_multiplier = 1.0

        def morning(day, _clock):
            nonlocal last_multiplier
            # 1. The owner switches skills on or off, if today is the day.
            wanted = configuration.skills_on(day.index)
            if set(wanted) != set(configuration.skills_on(day.index - 1)):
                sell(solvent, wanted,
                     why=f"configuration {configuration.name} changes on "
                         f"day {day.index}")
                changed_on.append({"day": day.index, "skills": list(wanted)})
            # 2. Costs drift up, if this market does that.
            multiplier = market.cost_multiplier(day.index)
            if abs(multiplier - last_multiplier) >= 0.01:
                _owner_raises_their_rates(solvent, journal, multiplier,
                                          base_rates)
                last_multiplier = multiplier
            # 3. The provider is down, or it is not.
            down = market.halted_on(day.index)
            mode = (OperatingMode.HALT if down else OperatingMode.NORMAL)
            if solvent.policy.operating_mode is not mode:
                solvent.policy.set_operating_mode(
                    mode, OWNER,
                    "the provider is down for the day" if down
                    else "the provider is back")
            if down:
                halted_days.append(day.index)
                return []
            return world.open_for_business(day)

        def evening(day, _clock):
            charged = books.post_the_days_costs(journal, solvent, day.index)
            money = audit.profit_and_loss(solvent,
                                          journal_path=journal_path)
            daily.append({"day": day.index, "charged": charged,
                          "cash_profit_cents": money["cash_profit_cents"],
                          "accrual_profit_cents": money["accrual_profit_cents"],
                          "collected_cents": money["collected_cents"],
                          "invoiced_cents": money["invoiced_cents"],
                          "jobs": len(list(solvent.orchestrator.jobs()))})
            if (day.index + 1) in SNAPSHOTS:
                snapshots[day.index + 1] = _snapshot(
                    solvent, world, journal_path, day.index + 1)
            if verbose:
                print(f"  day {day.index:>3}  "
                      f"cash {money['cash_profit_cents']:>10}c  "
                      f"jobs {len(list(solvent.orchestrator.jobs())):>3}")

        for _ in calendar.run(runtime, clock=clock, before_day=morning,
                              after_day=evening):
            pass

        final = _snapshot(solvent, world, journal_path, days)
        grade = audit.reconcile(solvent,
                                acts=[a.__dict__ for a in world.log],
                                journal_path=journal_path,
                                expect_paid=True)
        journal.close()
        return {
            "configuration": configuration.name,
            "description": configuration.description,
            "skills_at_start": list(configuration.start),
            "skills_changed_on": changed_on,
            "conditions": market.summary(),
            "days": days,
            "working_days": calendar.working_days,
            "halted_days": halted_days,
            "seed": seed,
            "arrivals_per_working_day": arrivals,
            "wall_seconds": round(time.time() - started, 1),
            "snapshots": snapshots,
            "final": final,
            "daily": daily,
            "grade": {"passed": grade.passed,
                      "findings": [f.line() for f in grade.findings],
                      "failures": [f.line() for f in grade.failures]},
        }
    finally:
        try:
            solvent.store.close()
        except Exception:
            pass


def _snapshot(solvent, world, journal_path, day: int) -> dict:
    """Everything the mandatory results table needs, at one moment."""
    from simulation import audit

    shop = audit.what_deskpilot_says(solvent)
    sim = audit.what_the_simulation_says([a.__dict__ for a in world.log],
                                         journal_path)
    money = audit.profit_and_loss(solvent, journal_path=journal_path)
    states = shop["job_states"]
    done = states.get("COMPLETE", 0)
    return {
        "through_day": day,
        "customers_arrived": sim["arrived"],
        "customers_acquired": shop["signed"] + shop["orders"],
        "paying_customers": len({
            r["job_id"] for r in solvent.ledger.payment_rows()
            if int(r["collected_cents"] or 0) > 0}),
        "jobs_accepted": shop["jobs"],
        "jobs_completed": done,
        "jobs_awaiting_payment": states.get("AWAITING_PAYMENT", 0),
        "jobs_rejected": states.get("REJECTED", 0) + states.get("FAILED", 0),
        "jobs_blocked": states.get("BLOCKED", 0),
        "contracts": shop["signed"],
        "orders": shop["orders"],
        "complaints": sim["asked_for_changes"],
        "went_quiet": sim["went_quiet"],
        "walked_away": sim["walked_away"],
        "took_a_rival_quote": len([a for a in world.log
                                   if a.act == "take the other quote"]),
        "money": money,
        "capacity": {"owed_seconds": shop["owed_seconds"],
                     "reservations": shop["reservations"],
                     "pipelines": shop["pipelines"]},
        "job_states": states,
    }


def main() -> int:
    from simulation.market import CONDITIONS
    from simulation.skills import CONFIGURATIONS

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--configuration", default="A",
                        choices=sorted(CONFIGURATIONS))
    parser.add_argument("--conditions", default="normal",
                        choices=sorted(CONDITIONS))
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--arrivals", type=float, default=1.5)
    parser.add_argument("--seed", default="ninety")
    parser.add_argument("--all", action="store_true",
                        help="every configuration, same market, same seed")
    parser.add_argument("--out", default="",
                        help="write the result as JSON here")
    parser.add_argument("--root", default="",
                        help="keep the shops here instead of a temp dir")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    os.environ.setdefault("SOLVENT_OWNER_KEY", secrets.token_hex(32))
    from simulation import rail
    os.environ.setdefault(rail.SECRET_ENV, rail.TEST_SECRET)

    import tempfile
    box = None
    if args.root:
        root = pathlib.Path(args.root)
    else:
        box = tempfile.TemporaryDirectory()
        root = pathlib.Path(box.name)

    wanted = sorted(CONFIGURATIONS) if args.all else [args.configuration]
    results = {}
    try:
        for name in wanted:
            print(f"=== configuration {name}: "
                  f"{CONFIGURATIONS[name].description} "
                  f"({args.conditions}, {args.days} days) ===")
            result = run_one(configuration=CONFIGURATIONS[name],
                             conditions=CONDITIONS[args.conditions],
                             days=args.days, seed=args.seed,
                             root=root / f"{name}-{args.conditions}",
                             arrivals=args.arrivals, verbose=args.verbose)
            results[name] = result
            money = result["final"]["money"]
            print(f"  jobs {result['final']['jobs_accepted']:>4}  "
                  f"complete {result['final']['jobs_completed']:>4}  "
                  f"invoiced {money['invoiced_cents']:>9}c  "
                  f"cash in {money['cash_in_cents']:>9}c  "
                  f"cash profit {money['cash_profit_cents']:>10}c  "
                  f"grade {'PASS' if result['grade']['passed'] else 'FAIL'}  "
                  f"{result['wall_seconds']}s")
            for failure in result["grade"]["failures"]:
                print(f"    {failure}")
    finally:
        if args.out:
            pathlib.Path(args.out).write_text(
                json.dumps(results, indent=2, sort_keys=True, default=str),
                encoding="utf-8")
            print(f"wrote {args.out}")
        if box is not None:
            box.cleanup()
    return 0 if all(r["grade"]["passed"] for r in results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
