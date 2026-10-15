#!/usr/bin/env python3
"""What the owner's hourly revenue target would require.

The owner has named a gross revenue target of $80 per operating hour.
This inverts the question the revenue model answers: instead of "what
could this capacity produce", it asks "what would have to be true for
that figure to be reached", and reports the two halves separately
because only one of them is knowable.

**The supply half is measured.** Jobs per hour, seconds per job, the
worker limit, the owner's rate and minimum fee, the configured fees.
Every number in it came from this machine or from the owner's own
policy.

**The demand half has no observations at all.** DeskPilot has never
submitted a bid, never won one, never invoiced anybody and never been
paid. The number of jobs that exist at the owner's price, the share of
bids that win and the share of invoices that get paid are all unknown,
and this tool does not supply them. Where a win rate is needed it is
printed as a grid of what-ifs, each one labelled as a what-if, so that
the output reads as a requirement rather than a forecast. There is no
default win rate here, because a default would become the answer.

The distinction that matters for reading the result: the target is not
a capacity problem. The measured end-to-end time for all five skills is
under a fifth of a second, so a single worker could in principle run
thousands of jobs an hour. What the target needs is *won work at the
owner's price*, and nothing in this repository can tell anyone whether
that work exists.

Fees configured as zero mean the owner has not recorded them. They are
reported as unrecorded, never as free: a net figure computed from
unrecorded fees is the gross figure wearing a different label.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

#: The owner's stated target, in cents of gross revenue per operating
#: hour. Overridable, but recorded here so the default is the owner's
#: figure rather than a convenient one.
TARGET_GROSS_CENTS_PER_HOUR = 8_000

#: Win rates printed as what-ifs. Not estimates, not a range anybody
#: has observed, and deliberately wide: a narrow band would read as
#: knowledge.
WIN_RATE_GRID = (0.02, 0.05, 0.10, 0.20, 0.50)


def _money(cents) -> str:
    if cents is None:
        return "unrecorded"
    return f"${cents / 100:,.2f}"


def requirement(*, target_cents_per_hour=TARGET_GROSS_CENTS_PER_HOUR,
                db: str = "") -> dict:
    """What the target needs, split into measured and unknown."""
    import tempfile

    from solvent.harness import Solvent

    box = None
    if db:
        solvent = Solvent(db)
    else:
        box = tempfile.TemporaryDirectory()
        solvent = Solvent(str(pathlib.Path(box.name) / "model.db"))
    try:
        return _requirement(solvent, target_cents_per_hour)
    finally:
        try:
            solvent.store.close()
        except Exception:
            pass
        if box is not None:
            box.cleanup()


def _requirement(solvent, target_cents_per_hour: int) -> dict:
    get = lambda k, d=0: solvent.policy.get("commercial", k, default=d)  # noqa: E731

    hourly = int(get("hourly_rate_cents", 0) or 0)
    minimum_fee = int(get("minimum_project_fee_cents", 0) or 0)
    platform_fraction = float(get("platform_fee_fraction", 0) or 0)
    platform_minimum = int(get("platform_fee_minimum_cents", 0) or 0)
    payment_fraction = float(get("payment_fee_fraction", 0) or 0)
    payment_flat = int(get("payment_fee_flat_cents", 0) or 0)
    overhead = int(get("fixed_monthly_overhead_cents", 0) or 0)

    unrecorded = [name for name, value in (
        ("minimum_project_fee_cents", minimum_fee),
        ("platform_fee_fraction", platform_fraction),
        ("platform_fee_minimum_cents", platform_minimum),
        ("payment_fee_fraction", payment_fraction),
        ("payment_fee_flat_cents", payment_flat),
        ("fixed_monthly_overhead_cents", overhead),
    ) if not value]

    # ---- what a job grosses -----------------------------------------
    # The owner's rate applies to the hours a job is *billed*, which is
    # not the machine time it takes: csv-cleanup runs in a millisecond
    # and nobody sells a millisecond. The floor is therefore the
    # minimum fee, and the only other figure that can be stated
    # without inventing one is the rate times a billed hour.
    gross_options = {}
    if minimum_fee:
        gross_options["at_the_minimum_fee"] = minimum_fee
    if hourly:
        gross_options["at_one_billed_hour"] = max(hourly, minimum_fee)
        gross_options["at_two_billed_hours"] = max(2 * hourly, minimum_fee)

    per_job = {}
    for label, gross in sorted(gross_options.items()):
        platform = max(gross * platform_fraction, float(platform_minimum))
        payment = gross * payment_fraction + payment_flat
        net = gross - platform - payment
        jobs = target_cents_per_hour / gross if gross else None
        per_job[label] = {
            "gross_cents": gross,
            "platform_fee_cents": round(platform, 2),
            "payment_fee_cents": round(payment, 2),
            "net_cents": round(net, 2),
            "fees_recorded": not unrecorded,
            "jobs_per_hour_for_target": round(jobs, 2) if jobs else None,
            "jobs_per_day_for_target": round(jobs * 24, 1) if jobs else None,
            "net_cents_per_hour_at_that_rate": (
                round(jobs * net, 2) if jobs else None),
        }

    # ---- can the machine supply it? ---------------------------------
    # From the measured end-to-end figures, not from the design.
    measured = _measured_throughput()

    # ---- what the demand side would have to be ----------------------
    # Expressed per win rate, each labelled a what-if. The win rate
    # itself stays unknown and is not averaged into anything.
    demand = {}
    for label, row in per_job.items():
        jobs = row["jobs_per_hour_for_target"]
        if not jobs:
            continue
        demand[label] = {
            f"win_rate_{int(rate * 100)}_percent": {
                "basis": "ASSUMED what-if; no bid has ever been submitted",
                "bids_per_hour": round(jobs / rate, 1),
                "bids_per_day": round(jobs * 24 / rate, 1),
            } for rate in WIN_RATE_GRID
        }

    # An hourly target means very different things depending on how
    # many hours are being counted, and the difference is two orders
    # of magnitude. Stated rather than left for the reader to assume.
    horizons = {
        "an_eight_hour_working_day": 8 * 22,
        "every_hour_the_vps_is_billed_for": 730,
    }

    return {
        "target_gross_cents_per_hour": target_cents_per_hour,
        "what_the_target_comes_to_monthly": {
            name: {"hours": hours,
                   "gross_cents": target_cents_per_hour * hours}
            for name, hours in horizons.items()
        },
        "owner_inputs": {
            "hourly_rate_cents": hourly,
            "minimum_project_fee_cents": minimum_fee,
            "basis": "OBSERVED: the owner's own configured policy; what "
                     "they charge, not what the market pays",
        },
        "unrecorded_cost_inputs": unrecorded,
        "per_job": per_job,
        "supply": measured,
        "demand_required": demand,
        "demand_observations": {
            "bids_submitted": 0,
            "bids_won": 0,
            "invoices_paid": 0,
            "basis": "OBSERVED: counted, and all three are zero",
        },
        "verdict": _verdict(per_job, measured, unrecorded),
    }


def _measured_throughput() -> dict:
    """Jobs per hour the measured timings allow, per skill."""
    path = ROOT / "docs" / "evidence" / "skill-benchmarks.json"
    if not path.exists():
        return {"basis": "UNMEASURED",
                "note": "run tools/benchmark_skills.py --json into "
                        "docs/evidence/skill-benchmarks.json first"}
    data = json.loads(path.read_text(encoding="utf-8"))
    skills = data.get("skills", data)
    rows = {}
    for name, row in skills.items():
        if not isinstance(row, dict):
            continue
        ms = (row.get("end_to_end") or {}).get("median_ms")
        if not ms:
            continue
        rows[name] = {
            "end_to_end_median_ms": ms,
            "jobs_per_hour_one_worker": round(3_600_000 / ms),
        }
    return {
        "basis": "SIMULATED: generated inputs on this host, not a client's "
                 "file and not the owner's VPS",
        "per_skill": rows,
        "note": "a generated 605-byte CSV is not a 200,000-row export; "
                "these rates are an upper bound on machine throughput "
                "and say nothing about how long a real job takes",
    }


def _verdict(per_job, measured, unrecorded) -> list:
    lines = []
    cheapest = min((row["jobs_per_hour_for_target"] for row in per_job.values()
                    if row["jobs_per_hour_for_target"]), default=None)
    dearest = max((row["jobs_per_hour_for_target"] for row in per_job.values()
                   if row["jobs_per_hour_for_target"]), default=None)
    if cheapest is None:
        lines.append("the owner has not configured a price, so the target "
                     "has no job count attached to it")
        return lines
    lines.append(
        f"the target needs between {cheapest:g} and {dearest:g} delivered "
        "jobs an hour depending on what a job is billed at")
    per_skill = (measured.get("per_skill") or {})
    if per_skill:
        slowest = max(per_skill.values(),
                      key=lambda r: r["end_to_end_median_ms"])
        lines.append(
            f"machine capacity is not the constraint: the slowest measured "
            f"skill allows about {slowest['jobs_per_hour_one_worker']:,} "
            "jobs an hour on one worker, which is several thousand times "
            "the requirement")
    lines.append(
        "the constraint is won work at the owner's price, and there are "
        "zero observations of it: no bid submitted, none won, none paid")
    if unrecorded:
        lines.append(
            "the net figures are not yet real: "
            + ", ".join(unrecorded)
            + " are unrecorded, which is not the same as zero")
    return lines


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-cents-per-hour", type=int,
                        default=TARGET_GROSS_CENTS_PER_HOUR)
    parser.add_argument("--db", default="")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    result = requirement(target_cents_per_hour=args.target_cents_per_hour,
                         db=args.db)
    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return 0

    print(f"target: {_money(result['target_gross_cents_per_hour'])} gross "
          "per operating hour")
    for name, row in result["what_the_target_comes_to_monthly"].items():
        print(f"    over {name.replace('_', ' ')} "
              f"({row['hours']} h/month): "
              f"{_money(row['gross_cents'])} a month")
    print()
    print(f"{'if a job is billed':<24} {'gross':>9} {'net':>9} "
          f"{'jobs/hr':>8} {'jobs/day':>9}")
    for label, row in sorted(result["per_job"].items()):
        print(f"{label.replace('_', ' '):<24} "
              f"{_money(row['gross_cents']):>9} "
              f"{_money(row['net_cents']):>9} "
              f"{row['jobs_per_hour_for_target']:>8} "
              f"{row['jobs_per_day_for_target']:>9}")
    if result["unrecorded_cost_inputs"]:
        print("\n  net is provisional -- unrecorded: "
              + ", ".join(result["unrecorded_cost_inputs"]))

    print("\nbids required, by what-if win rate (none of these is a "
          "measurement):")
    for label, grid in sorted(result["demand_required"].items()):
        print(f"  {label.replace('_', ' ')}:")
        for rate, row in grid.items():
            print(f"      {rate.replace('_', ' '):<28} "
                  f"{row['bids_per_hour']:>8} /hr   "
                  f"{row['bids_per_day']:>9} /day")

    print("\nverdict:")
    for line in result["verdict"]:
        print("  -", line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
