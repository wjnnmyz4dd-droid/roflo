"""What the work is worth, what it costs us, and what we may honestly bill.

Three numbers that were one number, and the conflation was the business model's
central defect.

**Human-equivalent hours** — how long a competent human would take. This is a
*value* measure. It is what the client is comparing against when they decide a
price is fair, and it is the only defensible basis for a fixed-price quote. It
is derived from a measured unit count times a recorded per-unit standard, and
the standard has to carry a citation. There is no default: without evidence
there is no human-equivalent figure and no binding fixed-price quote.

**AI execution time** — how long DeskPilot actually took. Measured, never
estimated. It is what an hourly contract may be billed for, and nothing else
may be.

**Actual operating cost** — what DeskPilot actually spent: compute, model API,
platform fee, payment fee, verification budget. Also measured or taken from
recorded policy. It is *not* a labour rate.

The Financial Governor used to compute DeskPilot's cost as hours times a human
market wage, which is how a job that took half a second of CPU arrived with
$294 of "cost" against $400 of revenue and a 26% margin. That number was not
conservative, it was wrong: it priced DeskPilot's own labour as though it hired
somebody. Market wages belong on the client's side of the ledger, as the value
of the alternative they are not buying. **DeskPilot's cost has no geography.**

Where the authority boundary sits, so that this does not become a second one:

* the **Financial Governor** decides whether a job is worth doing;
* the **Ledger** is the authority on what money moved and what was spent;
* this module is a *calculator*. It owns no table, makes no decision, and
  reaches no network. It turns measurements and recorded evidence into
  numbers the other two use.

What it refuses, and why each refusal exists:

* a fixed-price quote with no cited standard behind the human-equivalent
  figure — otherwise "a human would take five hours" is a number somebody
  wanted to be true;
* an hourly bill for human-equivalent hours — that is invoicing for work
  nobody did, and it is the single rule in this file that is not a trade-off;
* a quote below the owner's configured minimum, unless the owner has said a
  minimum does not apply;
* profit computed against anything other than measured cost.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from enum import Enum

from .errors import FailClosed
from .types import Cents, CostCategory, CostLine, PricingTier, fmt

#: Seconds in an hour, named because the conversion appears in both directions
#: and a stray 3600 in a money calculation is hard to read.
SECONDS_PER_HOUR = 3600.0


class BillingBasis(Enum):
    """How the client agreed to be charged. Decides what may be invoiced."""

    FIXED_PRICE = "FIXED_PRICE"
    HOURLY = "HOURLY"

    @property
    def bills_time(self) -> bool:
        return self is BillingBasis.HOURLY


@dataclass(frozen=True, slots=True)
class WorkStandard:
    """How long one unit of this work takes a human, and who says so.

    ``citation`` is not decoration. A per-unit minute figure with no source is
    an opinion, and an opinion is what a fixed price must not rest on. The
    fields mirror what :class:`solvent.pricing.PricingReference` already demands
    of a rate: where it came from, when it was retrieved, and how confident the
    owner is in it.
    """

    unit: str
    minutes_per_unit: float
    source: str
    citation: str
    retrieved_date: str
    tier: PricingTier = PricingTier.MARKET_DATA
    #: Fixed minutes the job costs a human regardless of size: opening the
    #: file, reading the brief, sending it back.
    setup_minutes: float = 0.0
    note: str = ""

    def __post_init__(self) -> None:
        if self.minutes_per_unit < 0 or self.setup_minutes < 0:
            raise FailClosed("a work standard cannot take negative time")
        for name in ("unit", "source", "citation", "retrieved_date"):
            if not str(getattr(self, name)).strip():
                raise FailClosed(
                    f"a work standard needs {name}; an uncited standard is an "
                    "opinion, and a fixed price may not rest on one")

    @property
    def binding(self) -> bool:
        """Whether a quote resting on this may be given to a client."""
        return self.tier.binding_quote_allowed


@dataclass(frozen=True, slots=True)
class HumanEquivalent:
    """What this job would cost a client in human time. A value measure."""

    hours: float
    units: int
    standard: WorkStandard

    @property
    def basis(self) -> str:
        return (f"{self.units} {self.standard.unit}(s) at "
                f"{self.standard.minutes_per_unit:g} min each"
                + (f" plus {self.standard.setup_minutes:g} min setup"
                   if self.standard.setup_minutes else "")
                + f" — {self.standard.source} ({self.standard.citation}, "
                  f"retrieved {self.standard.retrieved_date})")


def human_equivalent(units: int, standard: WorkStandard) -> HumanEquivalent:
    """How long a human would take, from a measured count and a cited standard.

    ``units`` is counted off the client's own file — rows, fields, pages — not
    asserted by anyone. That is what keeps this from drifting into a number
    chosen to justify a price.
    """
    if units < 0:
        raise FailClosed("a job cannot have a negative number of units")
    minutes = standard.setup_minutes + units * standard.minutes_per_unit
    return HumanEquivalent(hours=minutes / 60.0, units=units, standard=standard)


@dataclass(frozen=True, slots=True)
class ExecutionMeasurement:
    """What DeskPilot actually spent doing the work. Measured.

    ``wall_seconds`` is what an hourly contract bills against, because it is
    what the client would call elapsed time. ``cpu_seconds`` drives the compute
    cost, because that is what the machine actually consumed.
    """

    wall_seconds: float
    cpu_seconds: float
    api_tokens: int = 0
    api_cost_cents: Cents = 0

    def __post_init__(self) -> None:
        for name in ("wall_seconds", "cpu_seconds"):
            if getattr(self, name) < 0:
                raise FailClosed(f"{name} cannot be negative")
        if self.api_tokens < 0 or self.api_cost_cents < 0:
            raise FailClosed("API usage cannot be negative")

    @property
    def hours(self) -> float:
        """Elapsed hours. What an hourly contract may bill, and the only thing."""
        return self.wall_seconds / SECONDS_PER_HOUR


@dataclass(frozen=True, slots=True)
class OperatingCost:
    """What the job actually cost to run, line by line.

    Every line is either measured or read from recorded policy. None of it is
    a labour rate, because no labour was hired.
    """

    compute_cents: Cents = 0
    api_cents: Cents = 0
    platform_fee_cents: Cents = 0
    payment_fee_cents: Cents = 0
    verification_cents: Cents = 0
    other_cents: Cents = 0
    basis: tuple[str, ...] = ()

    @property
    def total_cents(self) -> Cents:
        return (self.compute_cents + self.api_cents + self.platform_fee_cents
                + self.payment_fee_cents + self.verification_cents
                + self.other_cents)

    def as_cost_lines(self) -> list[CostLine]:
        """For the Ledger, which is the authority on what was actually spent.

        This module computes; it does not record. Handing back cost lines keeps
        it that way.
        """
        pairs = ((CostCategory.COMPUTE, self.compute_cents),
                 (CostCategory.AI_API, self.api_cents),
                 (CostCategory.PLATFORM_FEES, self.platform_fee_cents),
                 (CostCategory.PAYMENT_FEES, self.payment_fee_cents),
                 (CostCategory.VERIFICATION, self.verification_cents))
        return [CostLine(category=category, amount=amount,
                         note="measured operating cost")
                for category, amount in pairs if amount]


@dataclass(frozen=True, slots=True)
class CostRates:
    """The owner's recorded cost of running DeskPilot. Configuration, not guesswork."""

    compute_cents_per_cpu_hour: Cents = 0
    #: Proportion of the contract price the platform keeps, 0.0–1.0.
    platform_fee_fraction: float = 0.0
    #: Payment processor: a proportion plus a flat charge per transaction.
    payment_fee_fraction: float = 0.0
    payment_fee_flat_cents: Cents = 0
    #: The least the platform will take, whatever the percentage works
    #: out to. Freelancer's published fee is "10% or $5.00 USD,
    #: whichever is greater", and a model that knows only the 10% puts
    #: the fee on a $40 job at $4 when the platform will take $5.
    #: Understating cost overstates margin, which is the direction that
    #: makes DeskPilot accept work it should have refused. Zero means
    #: no minimum, not no fee.
    platform_fee_minimum_cents: Cents = 0

    def __post_init__(self) -> None:
        for name in ("platform_fee_fraction", "payment_fee_fraction"):
            value = getattr(self, name)
            if not 0.0 <= value < 1.0:
                raise FailClosed(
                    f"{name} must be a proportion between 0 and 1 (got {value})")


def operating_cost(measurement: ExecutionMeasurement, *, price_cents: Cents,
                   rates: CostRates, verification_cents: Cents = 0) -> OperatingCost:
    """Turn measurements into money, with each line saying where it came from."""
    compute = round(rates.compute_cents_per_cpu_hour
                    * measurement.cpu_seconds / SECONDS_PER_HOUR)
    platform = round(price_cents * rates.platform_fee_fraction)
    platform_basis = (f"platform fee {rates.platform_fee_fraction:.1%} of "
                      f"{fmt(price_cents)} = {fmt(platform)}")
    if price_cents and rates.platform_fee_minimum_cents > platform:
        platform = rates.platform_fee_minimum_cents
        platform_basis = (
            f"platform fee {fmt(platform)}: the floor the platform takes, "
            f"which is more than {rates.platform_fee_fraction:.1%} of "
            f"{fmt(price_cents)}")
    payment = (round(price_cents * rates.payment_fee_fraction)
               + rates.payment_fee_flat_cents) if price_cents else 0
    basis = [
        f"compute {measurement.cpu_seconds:.3f} CPU-seconds at "
        f"{fmt(rates.compute_cents_per_cpu_hour)}/CPU-hour = {fmt(compute)}",
        f"model API {measurement.api_tokens} token(s) = "
        f"{fmt(measurement.api_cost_cents)}",
        platform_basis,
        f"payment fee {rates.payment_fee_fraction:.1%} + "
        f"{fmt(rates.payment_fee_flat_cents)} = {fmt(payment)}",
    ]
    if verification_cents:
        basis.append(f"verification budget = {fmt(verification_cents)}")
    return OperatingCost(
        compute_cents=compute, api_cents=measurement.api_cost_cents,
        platform_fee_cents=platform, payment_fee_cents=payment,
        verification_cents=verification_cents, basis=tuple(basis))


@dataclass(frozen=True, slots=True)
class Quote:
    """A price, and everything needed to defend it to the client or the owner."""

    basis: BillingBasis
    price_cents: Cents
    human: HumanEquivalent | None
    market_hourly_cents: Cents
    #: What the client would pay a human for the same outcome.
    human_cost_cents: Cents
    discount_fraction: float
    minimum_fee_applied: bool
    binding: bool
    rationale: tuple[str, ...]

    @property
    def client_saving_cents(self) -> Cents:
        """What the client keeps by not hiring the human. Never negative."""
        return max(0, self.human_cost_cents - self.price_cents)

    def summary(self) -> str:
        return (f"{self.basis.value} {fmt(self.price_cents)}"
                + (f" (a human: {self.human.hours:.2f}h at "
                   f"{fmt(self.market_hourly_cents)}/h = "
                   f"{fmt(self.human_cost_cents)})" if self.human else "")
                + ("" if self.binding else " — INDICATIVE, not binding"))


def quote_fixed_price(*, human: HumanEquivalent, market_hourly_cents: Cents,
                      discount_fraction: float = 0.0,
                      minimum_fee_cents: Cents = 0,
                      waive_minimum: bool = False) -> Quote:
    """Price the outcome, not the clock.

    A fixed price is what the work is worth to the client, and what it is worth
    is bounded by their alternative: paying a human. So the price is the human
    cost less whatever discount the owner competes on, floored at the minimum
    project fee. How fast DeskPilot is does not appear in this calculation at
    all — that is the whole point of a fixed price, and it is where the business
    margin comes from.
    """
    if market_hourly_cents <= 0:
        raise FailClosed(
            "a fixed price needs a market hourly rate to compare against; "
            "without one there is nothing establishing what the work is worth")
    if not 0.0 <= discount_fraction < 1.0:
        raise FailClosed(
            f"the discount must be a proportion between 0 and 1 "
            f"(got {discount_fraction})")

    human_cost = round(human.hours * market_hourly_cents)
    priced = round(human_cost * (1.0 - discount_fraction))
    rationale = [
        f"human-equivalent {human.hours:.2f}h — {human.basis}",
        f"at {fmt(market_hourly_cents)}/h a human costs {fmt(human_cost)}",
        f"less {discount_fraction:.0%} = {fmt(priced)}",
    ]

    applied = False
    if minimum_fee_cents and priced < minimum_fee_cents and not waive_minimum:
        rationale.append(
            f"below the minimum project fee {fmt(minimum_fee_cents)}; "
            "quoted at the minimum")
        priced = minimum_fee_cents
        applied = True
    elif minimum_fee_cents and priced < minimum_fee_cents and waive_minimum:
        rationale.append(
            f"below the minimum project fee {fmt(minimum_fee_cents)}, which "
            "the owner waived for this job")

    return Quote(
        basis=BillingBasis.FIXED_PRICE, price_cents=priced, human=human,
        market_hourly_cents=market_hourly_cents, human_cost_cents=human_cost,
        discount_fraction=discount_fraction, minimum_fee_applied=applied,
        binding=human.standard.binding, rationale=tuple(rationale))


def hours_billable(measurement: ExecutionMeasurement, *,
                   round_to_minutes: int = 1) -> float:
    """Hours an hourly contract may be invoiced for. **Actual elapsed, only.**

    It takes the measurement and nothing else. There is deliberately no
    parameter that could carry the human-equivalent figure into this function,
    because invoicing for hours nobody worked is fraud whatever the contract
    says about value, and the commonest route to it is a caller holding both
    numbers and reaching for the bigger one.

    Rounding is to the nearest minute by default and may be coarsened to a
    billing increment the client agreed to, but never beyond the quarter hour:
    a six-second job billed as one hour is the same lie by another route.
    """
    if round_to_minutes < 1 or round_to_minutes > 15:
        raise FailClosed(
            f"a billing increment of {round_to_minutes} minute(s) is not "
            "honest rounding; use between 1 and 15")
    minutes = measurement.wall_seconds / 60.0
    increments = -(-minutes // round_to_minutes)  # ceiling
    return (increments * round_to_minutes) / 60.0


def refuse_fabricated_hours(billed_hours: float,
                            measurement: ExecutionMeasurement,
                            human: HumanEquivalent | None = None) -> None:
    """Raise unless the hours about to be invoiced are hours that happened.

    Called by whatever is about to put a number on an hourly invoice. A
    tolerance of one minute absorbs rounding; anything beyond it is time that
    was not worked, and the commonest way for that to happen is somebody
    reaching for the human-equivalent figure because it is bigger.
    """
    tolerance = 1.0 / 60.0
    actual = measurement.hours
    if billed_hours > actual + tolerance + 15.0 / 60.0:
        detail = ""
        if human is not None and abs(billed_hours - human.hours) < tolerance:
            detail = (" That is the human-equivalent figure. It is what the "
                      "work is worth, not what was worked, and it may be "
                      "quoted as a fixed price but never billed as time.")
        raise FailClosed(
            f"refusing to bill {billed_hours:.2f} hour(s) for work that took "
            f"{actual:.4f} hour(s).{detail}")


@dataclass(frozen=True, slots=True)
class ConversionAdvice:
    """Whether this job should be quoted fixed-price instead of hourly."""

    recommend: bool
    hourly_revenue_cents: Cents
    fixed_revenue_cents: Cents
    reason: str

    @property
    def uplift_cents(self) -> Cents:
        return self.fixed_revenue_cents - self.hourly_revenue_cents


def recommend_fixed_price(*, measurement: ExecutionMeasurement,
                          agreed_hourly_cents: Cents, fixed_quote: Quote,
                          threshold: float = 2.0,
                          round_to_minutes: int = 1) -> ConversionAdvice:
    """Say plainly when hourly billing is the wrong instrument for this work.

    An hourly contract pays DeskPilot for elapsed time, and DeskPilot's elapsed
    time is tiny. That is not a reason to pad the clock; it is a reason to sell
    the outcome. This produces the sentence the owner needs for the client:
    here is what you would pay a person, here is our price, here is what you
    keep — and it is a proposal, never an automatic re-pricing of an agreement
    already made.
    """
    # Billable hours, not raw elapsed: an hourly invoice is written in the
    # increment the client agreed to, so that is what the comparison has to
    # use. Reading raw seconds here would understate hourly revenue on every
    # short job and make the recommendation look better than it is.
    billable = hours_billable(measurement, round_to_minutes=round_to_minutes)
    hourly_revenue = round(billable * agreed_hourly_cents)
    fixed = fixed_quote.price_cents

    if measurement.wall_seconds < round_to_minutes * 60.0:
        return ConversionAdvice(
            recommend=True, hourly_revenue_cents=hourly_revenue,
            fixed_revenue_cents=fixed,
            reason=(f"the work took {measurement.wall_seconds:.1f}s, less "
                    f"than one {round_to_minutes}-minute billing increment, "
                    f"so an hourly invoice of {fmt(hourly_revenue)} would be "
                    f"rounding rather than time. Quote the outcome instead: "
                    f"{fmt(fixed)} against a human's "
                    f"{fmt(fixed_quote.human_cost_cents)}"))
    if hourly_revenue <= 0:
        return ConversionAdvice(
            recommend=True, hourly_revenue_cents=hourly_revenue,
            fixed_revenue_cents=fixed,
            reason=("hourly billing yields nothing for this job. Quote it as "
                    "a fixed price or decline it"))
    if fixed >= hourly_revenue * threshold:
        return ConversionAdvice(
            recommend=True, hourly_revenue_cents=hourly_revenue,
            fixed_revenue_cents=fixed,
            reason=(f"hourly would bill {fmt(hourly_revenue)} for "
                    f"{measurement.hours:.3f}h of actual work; the same "
                    f"outcome is worth {fmt(fixed)} against a human's "
                    f"{fmt(fixed_quote.human_cost_cents)}. Propose a fixed "
                    f"price: the client still keeps "
                    f"{fmt(fixed_quote.client_saving_cents)}"))
    return ConversionAdvice(
        recommend=False, hourly_revenue_cents=hourly_revenue,
        fixed_revenue_cents=fixed,
        reason=("hourly and fixed are within range of each other for this "
                "job; there is no case for renegotiating the basis"))


@dataclass(frozen=True, slots=True)
class Profitability:
    """Price against what was actually spent. No labour fiction either side."""

    price_cents: Cents
    operating_cost_cents: Cents
    human_cost_cents: Cents

    @property
    def profit_cents(self) -> Cents:
        return self.price_cents - self.operating_cost_cents

    @property
    def margin(self) -> float:
        return (self.profit_cents / self.price_cents) if self.price_cents else 0.0

    @property
    def client_saving_cents(self) -> Cents:
        return max(0, self.human_cost_cents - self.price_cents)

    def summary(self) -> str:
        return (f"price {fmt(self.price_cents)} − cost "
                f"{fmt(self.operating_cost_cents)} = "
                f"{fmt(self.profit_cents)} ({self.margin:.1%})")


def profitability(*, price_cents: Cents, cost: OperatingCost,
                  human_cost_cents: Cents = 0) -> Profitability:
    """Profit, from the measured cost and nothing else."""
    return Profitability(price_cents=price_cents,
                         operating_cost_cents=cost.total_cents,
                         human_cost_cents=human_cost_cents)


def predicted_operating_lines(*, price_cents: Cents, rates: CostRates,
                              compute_allowance_cents: Cents = 0
                              ) -> list[CostLine]:
    """What this job will cost DeskPilot to run, before it runs.

    For the Financial Governor's go/no-go decision, which needs a cost *before*
    there is a measurement. Every line is a real expense DeskPilot will incur:
    the platform's cut, the processor's cut, and an allowance for compute.

    What is deliberately absent is a labour line. The Governor used to be given
    hours at a human market wage as DeskPilot's cost, which made a job that
    takes half a second of CPU look like it cost $294 — and that is not a
    conservative estimate, it is the wrong quantity. A human wage is what the
    *client* avoids paying; it is not money leaving DeskPilot's account.
    """
    lines: list[CostLine] = []
    platform = round(price_cents * rates.platform_fee_fraction)
    if platform:
        lines.append(CostLine(
            category=CostCategory.PLATFORM_FEES, amount=platform,
            pricing_tier=PricingTier.OWNER_POLICY,
            note=f"{rates.platform_fee_fraction:.1%} of {fmt(price_cents)}"))
    payment = round(price_cents * rates.payment_fee_fraction)
    payment += rates.payment_fee_flat_cents if price_cents else 0
    if payment:
        lines.append(CostLine(
            category=CostCategory.PAYMENT_FEES, amount=payment,
            pricing_tier=PricingTier.OWNER_POLICY,
            note=f"{rates.payment_fee_fraction:.1%} plus "
                 f"{fmt(rates.payment_fee_flat_cents)}"))
    if compute_allowance_cents:
        lines.append(CostLine(
            category=CostCategory.COMPUTE, amount=compute_allowance_cents,
            pricing_tier=PricingTier.OWNER_POLICY,
            note="owner's recorded compute allowance for one job"))
    return lines


def rates_from_policy(policy) -> CostRates:
    """Read the owner's recorded cost of running DeskPilot.

    Every value defaults to zero, which is correct rather than convenient: an
    unrecorded fee is not a fee of some plausible size, it is one the owner has
    not told us about, and inventing it would put a number in a quote that
    nobody can defend.
    """
    def value(name, default):
        return policy.get("commercial", name, default=default)

    return CostRates(
        compute_cents_per_cpu_hour=int(value("compute_cents_per_cpu_hour", 0)),
        platform_fee_fraction=float(value("platform_fee_fraction", 0.0)),
        payment_fee_fraction=float(value("payment_fee_fraction", 0.0)),
        payment_fee_flat_cents=int(value("payment_fee_flat_cents", 0)),
        platform_fee_minimum_cents=int(
            value("platform_fee_minimum_cents", 0)))
