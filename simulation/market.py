"""The weather: demand, competition, and the things a shop cannot choose.

The fourth authority in :mod:`simulation`, and the one most easily
mistaken for a result. It decides **how many clients turn up, what
they can pay, and what else they could buy instead**. It decides
nothing about DeskPilot. There is no code path here that prices a
job, accepts work, refuses a client or touches a ``Solvent`` -- a
guard in the tests asserts that, because a market model that reached
into the shop would be writing the conclusion the run is supposed to
measure.

**Why a competitor matters.** Without one, a client's only reason to
refuse is their own budget, and a budget is private: DeskPilot can
quote at the ceiling every time and never learn it was expensive. A
competitor gives the refusal a *reason* that exists outside the
client's head, which is the only way a run can distinguish "too dear
for them" from "too dear".

**Rising costs are an owner act, not a market act.** When the VPS
bill goes up the owner amends their own recorded rates through
``PolicyStore.amend``; this module says when and by how much, and
``simulation.world`` performs it. Writing the policy from here would
make the market an owner, which it is not.

**An outage is modelled as a halt, and that is not the same thing.**
A provider going down for a day is represented by the owner putting
the shop into ``OperatingMode.HALT`` -- a real control, exercised
through the real authority. What it does *not* do is kill work
already in flight. That case is covered, properly, by the crash
injection in ``tests_solvent/test_crash_recovery.py``, where the
process is actually killed. Stated here so no reader takes a clean
halt as evidence that a mid-job provider failure is survivable.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class Conditions:
    """One named market. Every field is a condition, not an outcome."""

    name: str
    #: What the market is, in one line, for a report to quote.
    description: str
    #: Multiplier on the base arrival rate. 1.0 is the normal market.
    demand: float = 1.0
    #: What a competitor would quote, as a fraction of DeskPilot's
    #: asking price. 1.0 means they are level; 0.8 means a rival is
    #: a fifth cheaper and the client knows it. ``None`` means the
    #: client has no alternative in front of them.
    competitor_at: float | None = None
    #: How much a client will pay over the competitor before they
    #: switch. Nobody moves for a cent, and this is why.
    switches_above: float = 0.10
    #: Multiplier on every client's budget. Below 1.0 is a market
    #: that cannot afford what it used to.
    budgets: float = 1.0
    #: Chance, per working day, of a burst: one day's arrivals
    #: multiplied by :attr:`spike_size`.
    spike_chance: float = 0.0
    spike_size: float = 3.0
    #: Chance, per working day, that the shop's provider is down and
    #: the owner halts for the day.
    outage_chance: float = 0.0
    #: What the owner's recorded cost rates are multiplied by after
    #: ninety days, interpolated linearly. 1.0 is a flat market.
    cost_inflation_over_90_days: float = 1.0


#: The markets this assignment names. Each is a condition set, not a
#: prediction: a run under ``low_demand`` says what happens if demand
#: is low, and says nothing about whether it will be.
CONDITIONS = {
    "normal": Conditions(
        "normal", "the market the other runs are measured against",
        demand=1.0, competitor_at=0.95, budgets=1.0,
        spike_chance=0.05, outage_chance=0.01,
        cost_inflation_over_90_days=1.05),
    "low_demand": Conditions(
        "low_demand", "a third of the usual enquiries",
        demand=0.33, competitor_at=0.90, budgets=0.9),
    "high_demand": Conditions(
        "high_demand", "three times the usual enquiries",
        demand=3.0, competitor_at=1.05, budgets=1.1, spike_chance=0.15),
    "strong_competition": Conditions(
        "strong_competition", "a rival quoting thirty per cent under",
        demand=1.0, competitor_at=0.70, switches_above=0.05),
    "weak_competition": Conditions(
        "weak_competition", "no credible alternative in front of the client",
        demand=1.0, competitor_at=None),
    "price_sensitive": Conditions(
        "price_sensitive", "budgets halved and a rival in the room",
        demand=1.0, competitor_at=0.80, budgets=0.5, switches_above=0.02),
    "high_value": Conditions(
        "high_value", "larger clients with budgets to match",
        demand=0.7, competitor_at=1.10, budgets=2.5, switches_above=0.25),
    "rising_costs": Conditions(
        "rising_costs", "the owner's own costs doubling over the quarter",
        demand=1.0, competitor_at=0.95,
        cost_inflation_over_90_days=2.0),
    "outages": Conditions(
        "outages", "the provider down one working day in ten",
        demand=1.0, competitor_at=0.95, outage_chance=0.10),
}


@dataclass
class Market:
    """The conditions, made concrete for one run.

    Deterministic in the same way the population is: every draw is
    keyed by the seed and the day, so a market can be re-run and two
    configurations can be compared under the identical weather.
    """

    conditions: Conditions
    seed: str
    length_days: int = 90
    #: Filled by :meth:`arrivals_on` and :meth:`halted_on` so a report
    #: can state what the weather actually did rather than what it
    #: was configured to do.
    log: list = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.seed:
            raise ValueError(
                "an unseeded market is not reproducible, and two "
                "configurations cannot be compared under weather that "
                "differs between runs")
        if self.length_days <= 0:
            raise ValueError("a market with no days has no conditions")

    def _draw(self, *parts) -> random.Random:
        return random.Random(":".join([self.seed, *(str(p) for p in parts)]))

    # ---- demand ------------------------------------------------------

    def arrivals_on(self, day: int, base: float) -> float:
        """How many clients turn up today, before rounding.

        The spike is drawn per day rather than scheduled, so a run
        cannot be tuned by moving the busy days somewhere convenient.
        """
        rate = base * self.conditions.demand
        if self.conditions.spike_chance and (
                self._draw("spike", day).random() < self.conditions.spike_chance):
            rate *= self.conditions.spike_size
            self.log.append({"day": day, "what": "demand spike",
                             "detail": f"x{self.conditions.spike_size}"})
        return rate

    def budget_for(self, base_cents: int) -> int:
        """What this market lets a client spend, from what they would."""
        return max(1, int(round(base_cents * self.conditions.budgets)))

    # ---- competition --------------------------------------------------

    def competitor_quote(self, reference_cents: int) -> int | None:
        """What the client has been quoted elsewhere, or ``None``.

        Anchored to the **client's own reference** -- what the job is
        worth to them -- and deliberately not to DeskPilot's asking
        price. The first version of this read the ask, so the rival
        undercut whatever DeskPilot said by a fixed fraction and no
        price on earth was acceptable: ``strong_competition`` was
        unwinnable by construction, which measures the model rather
        than the shop.

        Modelling the rival's own cost base would be inventing a
        second business this run cannot observe, so the rival is a
        number attached to the job and nothing more.
        """
        if self.conditions.competitor_at is None:
            return None
        return max(1, int(round(reference_cents
                                * self.conditions.competitor_at)))

    def would_switch(self, asking_cents: int, reference_cents: int) -> bool:
        """True if the client walks rather than pay this much.

        Nobody moves for a cent. ``switches_above`` is how much
        dearer DeskPilot may be before the rival wins, which is the
        difference between a market and a price list.
        """
        rival = self.competitor_quote(reference_cents)
        if rival is None:
            return False
        return asking_cents > rival * (1.0 + self.conditions.switches_above)

    # ---- disruption ----------------------------------------------------

    def halted_on(self, day: int) -> bool:
        """True if the provider is down and the shop stops for the day."""
        if not self.conditions.outage_chance:
            return False
        down = (self._draw("outage", day).random()
                < self.conditions.outage_chance)
        if down:
            self.log.append({"day": day, "what": "provider outage",
                             "detail": "the owner halted for the day"})
        return down

    def cost_multiplier(self, day: int) -> float:
        """What the owner's recorded rates are worth today.

        Linear from 1.0 on day zero to ``cost_inflation_over_90_days``
        at ninety days, so a sixty-day run sees two thirds of the
        rise rather than all of it or none.
        """
        rise = self.conditions.cost_inflation_over_90_days - 1.0
        if not rise:
            return 1.0
        return 1.0 + rise * (min(day, 90) / 90.0)

    # ---- what happened --------------------------------------------------

    def summary(self) -> dict:
        spikes = len([e for e in self.log if e["what"] == "demand spike"])
        outages = len([e for e in self.log if e["what"] == "provider outage"])
        return {"conditions": self.conditions.name,
                "description": self.conditions.description,
                "demand_multiplier": self.conditions.demand,
                "competitor_at": self.conditions.competitor_at,
                "budget_multiplier": self.conditions.budgets,
                "spikes": spikes, "outages": outages,
                "cost_multiplier_at_end":
                    round(self.cost_multiplier(self.length_days), 4)}
