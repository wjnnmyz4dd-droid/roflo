"""What capacity could be worth -- and which half of that is not known.

A model, never an authority and never a record of earnings. Nothing
here is revenue; `Ledger.real_revenue_cents` is revenue. This computes
what a given amount of capacity *could* produce under stated demand
conditions, and it is built to make the division obvious:

    capacity is knowable          -- workers, measured job times, the
                                     owner's budget, the owner's rates
    demand is not                 -- how many jobs exist, what share of
                                     bids are won, what clients pay

DeskPilot has never submitted a bid or completed a paid job, so the
second column has no observations in it at all. Every input below
carries a basis saying where it came from, and the model refuses to
produce a revenue figure from inputs it had to invent: an UNKNOWN input
makes the scenario's revenue None, not a plausible-looking number.

Three rules this file exists to keep:

1. **Modelled is not earned.** Every figure returned is marked
   ``modelled``, and the collected-revenue figure beside it comes from
   the Ledger and counts only money actually received.
2. **Contribution before overhead, net after.** Reported separately,
   so a missing overhead figure cannot flatter the net.
3. **Unpaid is not collected.** Work delivered and not paid for is
   counted as delivered, never as revenue.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Where a number came from. The distinction is the point of the file.
OBSERVED = "OBSERVED"        # measured by this system, or set by the owner
SIMULATED = "SIMULATED"      # from a deterministic simulation, not from life
ASSUMED = "ASSUMED"          # a stated what-if, supplied for the model
UNKNOWN = "UNKNOWN"          # nobody knows, and the model will not pretend

#: Demand expressed as a multiple of what capacity can deliver, so that
#: no scenario has to invent a number of jobs that exist in the world.
#: The multiple is of *winnable* work, not of raw offers: at a 15% win
#: rate, three times the offers is half the capacity, and an OVERLOAD
#: scenario that does not overload measures nothing.
SCENARIOS = {
    "LOW_DEMAND": 0.25,
    "NORMAL_DEMAND": 0.75,
    "HIGH_DEMAND": 1.0,
    "OVERLOAD": 3.0,
}

#: Hours in an average month, for a machine that is already paid for
#: around the clock. Stated rather than assumed silently.
HOURS_PER_MONTH = 730.0


@dataclass(frozen=True, slots=True)
class Input:
    """One number, and an honest account of where it came from."""

    name: str
    value: float | None
    basis: str
    note: str = ""

    @property
    def known(self) -> bool:
        return self.value is not None and self.basis != UNKNOWN


@dataclass
class Model:
    inputs: dict = field(default_factory=dict)
    scenarios: dict = field(default_factory=dict)
    unknowns: list = field(default_factory=list)
    collected_revenue_cents: int = 0

    def to_dict(self) -> dict:
        return {
            "modelled": True,
            "warning": ("Modelled figures are not earnings. Nothing here "
                        "has been invoiced, collected or disputed."),
            "collected_revenue_cents": self.collected_revenue_cents,
            "inputs": {name: {"value": i.value, "basis": i.basis,
                              "note": i.note}
                       for name, i in self.inputs.items()},
            "unknowns": self.unknowns,
            "scenarios": self.scenarios,
        }


def build(*, policy, capacity, ledger=None, history=None, capability="",
          assumptions: dict | None = None) -> Model:
    """Compose the model from policy, measurement and stated what-ifs.

    ``assumptions`` carries the things nobody can observe yet -- a win
    rate, a collection rate, a dispute rate. Anything left out stays
    UNKNOWN and takes the revenue figures down with it, which is the
    intended behaviour rather than a gap to be filled with a default.
    """
    assumptions = assumptions or {}
    get = lambda k, d=0: policy.get("commercial", k, default=d)  # noqa: E731
    budget = capacity.budget()

    inputs: dict[str, Input] = {}

    def record(name, value, basis, note=""):
        inputs[name] = Input(name, value, basis, note)
        return inputs[name]

    # ---- what the machine can do -------------------------------------
    workers = budget.workers("EXECUTE")
    record("execution_workers", float(workers), OBSERVED,
           "the owner's configured worker limit")
    record("operating_hours_per_month", HOURS_PER_MONTH, ASSUMED,
           "a VPS billed monthly and left running")

    seconds = None
    if history is not None and capability:
        seconds = history.expected_seconds(capability)
    if seconds:
        record("seconds_per_job", float(seconds), OBSERVED,
               f"median of {history.count(capability)} measured run(s) of "
               f"{capability!r}")
    else:
        record("seconds_per_job", None, UNKNOWN,
               "no execution of this capability has been measured")

    headroom = 1.0 + budget.revision_headroom_fraction
    record("verification_and_revision_multiplier", headroom, OBSERVED,
           "the owner's revision headroom; a job is not finished when "
           "execution finishes")

    capacity_jobs = None
    if seconds:
        job_seconds = float(seconds) * headroom
        capacity_jobs = (workers * HOURS_PER_MONTH * 3600.0) / job_seconds
    record("capacity_jobs_per_month", capacity_jobs,
           OBSERVED if capacity_jobs else UNKNOWN,
           "workers x hours / time per job, including its revisions")

    # ---- what a job is worth -----------------------------------------
    hourly = float(get("hourly_rate_cents", 0) or 0)
    minimum = float(get("minimum_project_fee_cents", 0) or 0)
    price = None
    if hourly and seconds:
        price = max(hourly * (float(seconds) / 3600.0), minimum)
    record("price_per_job_cents", price, OBSERVED if price else UNKNOWN,
           "the owner's own rate applied to measured time, floored at the "
           "owner's minimum fee; this is what the owner charges, not "
           "evidence of what the market pays")

    # ---- what nobody knows -------------------------------------------
    for name, note in (
            ("bid_win_rate",
             "no bid has ever been submitted, so no bid has ever been won"),
            ("collection_rate",
             "no invoice has ever been paid, so the share that gets paid "
             "is not known"),
            ("dispute_rate",
             "no job has ever been disputed, because no job has been sold")):
        if name in assumptions:
            record(name, float(assumptions[name]), ASSUMED,
                   "supplied as a what-if, not measured: " + note)
        else:
            record(name, None, UNKNOWN, note)

    # ---- what each delivered job costs -------------------------------
    record("platform_fee_fraction", float(get("platform_fee_fraction", 0) or 0),
           OBSERVED, "the marketplace's cut, as configured")
    record("payment_fee_fraction", float(get("payment_fee_fraction", 0) or 0),
           OBSERVED, "the payment provider's percentage, as configured")
    record("payment_fee_flat_cents",
           float(get("payment_fee_flat_cents", 0) or 0), OBSERVED,
           "the payment provider's per-transaction charge, as configured")
    compute_hourly = float(get("compute_cents_per_cpu_hour", 0) or 0)
    record("compute_cents_per_cpu_hour", compute_hourly, OBSERVED,
           "incremental compute, as configured; zero means the owner has "
           "not recorded it, not that compute is free")
    record("other_direct_cost_per_job_cents",
           float(get("other_direct_cost_per_job_cents", 0) or 0), OBSERVED)
    record("fixed_monthly_overhead_cents",
           float(get("fixed_monthly_overhead_cents", 0) or 0), OBSERVED,
           "the VPS and anything else billed monthly whether or not "
           "DeskPilot works")

    completion = assumptions.get("completion_rate_of_accepted")
    if completion is None:
        record("completion_rate_of_accepted", 1.0, SIMULATED,
               "every accepted job was delivered in deterministic load "
               "simulation up to 500 offers; no job has been delivered "
               "to a paying client")
    else:
        record("completion_rate_of_accepted", float(completion), ASSUMED)

    unknowns = sorted(name for name, i in inputs.items() if not i.known)

    model = Model(inputs=inputs, unknowns=unknowns)
    if ledger is not None:
        try:
            model.collected_revenue_cents = int(ledger.real_revenue_cents())
        except Exception:
            model.collected_revenue_cents = 0

    for name, multiple in SCENARIOS.items():
        model.scenarios[name] = _scenario(name, multiple, inputs, unknowns)
    return model


def _scenario(name: str, multiple: float, inputs: dict,
              unknowns: list) -> dict:
    """One demand level. Returns None figures where an input is unknown."""
    capacity_jobs = inputs["capacity_jobs_per_month"].value
    price = inputs["price_per_job_cents"].value
    win = inputs["bid_win_rate"].value
    collect = inputs["collection_rate"].value
    completion = inputs["completion_rate_of_accepted"].value

    blocked = [n for n in ("capacity_jobs_per_month", "price_per_job_cents",
                           "bid_win_rate", "collection_rate")
               if n in unknowns]

    result = {
        "demand_multiple_of_capacity": multiple,
        "offers_per_month": (
            round(capacity_jobs * multiple / win, 1)
            if capacity_jobs and win else None),
        "capacity_jobs_per_month": (round(capacity_jobs, 1)
                                    if capacity_jobs else None),
        "unknown_inputs": blocked,
    }
    if blocked:
        result.update({
            "accepted_jobs": None, "completed_jobs": None,
            "gross_revenue_cents": None, "contribution_profit_cents": None,
            "net_profit_cents": None,
            "why_no_figure": (
                "no revenue figure is produced because "
                + ", ".join(blocked)
                + " has no observed value; a number here would be invented"),
        })
        return result

    # Work out the offers needed to put `multiple` times capacity of
    # winnable work in front of the gate, then let the gate clamp it.
    winnable = capacity_jobs * multiple
    offers = winnable / win if win else None
    accepted = min(winnable, capacity_jobs)
    completed = accepted * completion
    gross = completed * price
    # Only the share actually collected is revenue. The rest is
    # delivered work, which is not the same thing.
    collected = gross * collect

    # Money is rounded once, to whole cents, and every line is derived
    # from the rounded figures so that the statement reconciles exactly.
    # Rounding each line independently left gross, collected and
    # uncollected a cent apart, which in a financial model is not a
    # presentation detail -- it is a statement that does not add up.
    gross_cents = round(gross)
    collected_cents = round(collected)
    uncollected_cents = gross_cents - collected_cents

    seconds = inputs["seconds_per_job"].value or 0.0
    platform = round(collected_cents * inputs["platform_fee_fraction"].value)
    payment = round(collected_cents * inputs["payment_fee_fraction"].value
                    + completed * inputs["payment_fee_flat_cents"].value)
    compute = round(completed * (seconds / 3600.0)
                    * inputs["compute_cents_per_cpu_hour"].value)
    other = round(completed * inputs["other_direct_cost_per_job_cents"].value)
    contribution = collected_cents - platform - payment - compute - other
    overhead = round(inputs["fixed_monthly_overhead_cents"].value)

    result.update({
        "accepted_jobs": round(accepted, 1),
        "completed_jobs": round(completed, 1),
        "gross_revenue_cents": gross_cents,
        "uncollected_cents": uncollected_cents,
        "collected_revenue_cents_modelled": collected_cents,
        "marketplace_fees_cents": platform,
        "payment_fees_cents": payment,
        "incremental_compute_cents": compute,
        "other_direct_costs_cents": other,
        "contribution_profit_cents": contribution,
        "fixed_overhead_cents": overhead,
        "net_profit_cents": contribution - overhead,
        "turned_away_jobs": round(max(0.0, winnable - capacity_jobs), 1),
        "capacity_bound": winnable > capacity_jobs,
    })
    return result
