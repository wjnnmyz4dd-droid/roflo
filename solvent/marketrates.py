"""What the market pays for the five services, and how well we know it.

Phase 4's requirement is to price competitively **without giving away
profit**, and the hard part is not the arithmetic. It is that a price
recommendation is only as good as the evidence under it, and evidence
about freelance rates is mostly *asking* prices: what somebody
advertised, not what anybody paid. Treating the two as the same number
is how a pricing engine talks itself into a floor.

So every observation here carries three things the arithmetic cannot
supply:

* **Where it came from**, as a :class:`~solvent.evidence.Provenance`.
* **Whether it is an asking price or a transaction.** An advertised
  rate is what a seller hoped for. A completed contract is what a buyer
  paid. They are different populations and this module never averages
  them together.
* **When it was seen.** A rate nobody has checked for a year is not
  market evidence, and :meth:`MarketEvidence.band` drops stale
  observations rather than quietly ageing into fiction.

Storage is :class:`solvent.pricing.PricingReference`, which already
exists, is owner-governed, refuses unapproved sources and records
source, dates and confidence per row. A second store would be a second
answer to "what does this cost", and the two would disagree the first
time anything changed between them.

**This module recommends nothing and authorises nothing.** It reports
what the evidence supports. The Financial Governor remains the only
financial authority; :mod:`solvent.negotiation` is what turns a band
into an offer, inside limits the owner set.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum

from .errors import FailClosed
from .evidence import (
    Confidence, Figure, Provenance, figure_from_dict, unavailable,
)
from .types import Cents, fmt

#: The five, and nothing else. A rate for a service DeskPilot cannot
#: perform is not useful evidence, it is a reason to be asked for work
#: that would then be refused.
SERVICES = ("csv-cleanup", "data-entry", "document-processing",
            "report-builder", "spreadsheet-automation")

#: Beyond this, an observation is history rather than market evidence.
#: Ninety days is not a law of nature; it is short enough that a rate
#: shift shows up and long enough that a quiet service still has data.
DEFAULT_MAX_AGE_DAYS = 90

#: Below this many usable observations, a band is not a band. Three
#: points can be a coincidence; the figure says INSUFFICIENT_DATA and
#: the caller is expected to fall back to cost-plus rather than guess.
MINIMUM_OBSERVATIONS = 5


class Basis(Enum):
    """Asking price, or money that actually changed hands."""

    #: Advertised: a listing, a rate card, a published guide. What a
    #: seller hoped for.
    ASKING = "ASKING"
    #: A completed engagement at a known price. What a buyer paid.
    TRANSACTED = "TRANSACTED"


@dataclass(frozen=True, slots=True)
class Observation:
    """One comparable price, with everything needed to discount it.

    ``scope_units`` is what makes two prices comparable at all -- rows
    for a cleanup, pages for a document, records for data entry. A
    price without a scope is not a comparable, and the constructor
    refuses one, because "someone charged $300" says nothing until you
    know for how much work.
    """

    service: str
    cents: Cents
    basis: Basis
    scope_units: int
    unit: str
    source: str
    seen_on: str
    #: Free text, for a person. Never parsed.
    note: str = ""
    turnaround_days: int = 0
    revisions_included: int = 0

    def __post_init__(self) -> None:
        if self.service not in SERVICES:
            raise FailClosed(
                f"{self.service!r} is not one of the five services; a rate "
                "for work DeskPilot cannot do is not evidence")
        if self.cents <= 0:
            raise FailClosed("an observation with no price is not a price")
        if self.scope_units <= 0:
            raise FailClosed(
                f"observation from {self.source!r} names no scope; a price "
                "without the work it covers is not comparable to anything")
        if not self.source:
            raise FailClosed("an observation with no source is hearsay")
        if not self.seen_on:
            raise FailClosed(
                "an observation with no date cannot be aged out, so it "
                "would stay 'current' for ever")

    @property
    def cents_per_unit(self) -> float:
        """The only figure that compares across jobs of different sizes."""
        return self.cents / self.scope_units

    def age_days(self, today: str = "") -> int | None:
        """``None`` when the date cannot be read, never zero.

        Unreadable must not read as fresh, for the same reason an
        unreadable lease timestamp must not read as "just now": it
        would make every stale row look current.
        """
        try:
            seen = date.fromisoformat(self.seen_on)
            now = date.fromisoformat(today) if today else datetime.now().date()
        except (TypeError, ValueError):
            return None
        return (now - seen).days


@dataclass(frozen=True, slots=True)
class Band:
    """What the evidence supports for one service, per unit of scope.

    Deliberately a range and not a number. A single "market price" is a
    claim the evidence almost never supports, and quoting one invites
    the arithmetic downstream to treat it as fact.
    """

    service: str
    low: Figure
    median: Figure
    high: Figure
    observations: int
    transacted: int
    asking: int
    confidence: Confidence
    why: str = ""

    @property
    def usable(self) -> bool:
        return self.median.known and self.confidence is not Confidence.INSUFFICIENT_DATA

    def render(self) -> str:
        if not self.usable:
            return f"{self.service}: no supported band — {self.why}"
        return (f"{self.service}: {self.low.render()} – {self.high.render()} "
                f"per unit, median {self.median.render()} "
                f"[{self.confidence.value}, {self.observations} obs, "
                f"{self.transacted} transacted]")

    def as_dict(self) -> dict:
        """Enough to rebuild it. The counts travel with the figures.

        ``observations``, ``transacted`` and ``asking`` are what make
        the confidence checkable, so a stored band that kept the
        numbers and dropped the counts would look exactly as
        well-evidenced as one that kept both.
        """
        return {"service": self.service, "low": self.low.as_dict(),
                "median": self.median.as_dict(), "high": self.high.as_dict(),
                "observations": self.observations,
                "transacted": self.transacted, "asking": self.asking,
                "confidence": self.confidence.value, "why": self.why}


def band_from_dict(doc: dict) -> Band:
    return Band(service=doc["service"], low=figure_from_dict(doc["low"]),
                median=figure_from_dict(doc["median"]),
                high=figure_from_dict(doc["high"]),
                observations=int(doc["observations"]),
                transacted=int(doc["transacted"]),
                asking=int(doc["asking"]),
                confidence=Confidence(doc["confidence"]),
                why=doc.get("why", ""))


class MarketEvidence:
    """Observations in, a band out. No recommendation, no authority.

    Rows live in :class:`solvent.pricing.PricingReference` when one is
    supplied, so that market data is owner-governed and subject to the
    same approved-source rule as every other price DeskPilot holds.
    Without one it keeps them in memory, which is what the tests use
    and what a caller gets if they forget to wire the store -- and
    :meth:`durable` says which, so nobody mistakes one for the other.
    """

    def __init__(self, pricing=None, policy=None) -> None:
        self._pricing = pricing
        self._policy = policy
        self._memory: list[Observation] = []

    @property
    def durable(self) -> bool:
        """Whether observations survive a restart. Asked, not assumed."""
        return self._pricing is not None

    def record(self, observation: Observation, *, owner_identity: str = "") -> None:
        """Add one observation. Owner-governed when a store is wired.

        The owner gate is not ceremony: an unapproved source feeding
        market evidence is the straightforward way to talk this system
        into a price, and Phase 17 asks for exactly that attack.
        """
        if self._pricing is not None:
            if not owner_identity:
                raise FailClosed(
                    "market observations are owner-governed when a pricing "
                    "store is wired; no owner identity was supplied")
            from .types import CostCategory, Jurisdiction, PricingTier

            self._pricing.ingest(
                category=CostCategory.MARKET_RATE,
                jurisdiction=Jurisdiction(country="US"),
                tier=PricingTier.MARKET_DATA,
                unit_cents=int(observation.cents_per_unit),
                unit=observation.unit,
                source=observation.source,
                effective_date=observation.seen_on,
                retrieved_date=observation.seen_on,
                confidence=0.8 if observation.basis is Basis.TRANSACTED else 0.5,
                owner_identity=owner_identity,
                reference_id=(f"mkt_{observation.service}_{observation.basis.value}"
                              f"_{observation.source}_{observation.seen_on}"))
        self._memory.append(observation)

    def observations(self, service: str) -> list[Observation]:
        return [o for o in self._memory if o.service == service]

    def band(self, service: str, *, today: str = "",
             max_age_days: int = DEFAULT_MAX_AGE_DAYS) -> Band:
        """The supported range per unit of scope, or a refusal that says why.

        Transacted prices are preferred outright rather than blended
        with asking prices: when there are enough of them the band is
        built from those alone. Mixing the two produces a number that
        describes no real population, and it is biased upward by
        exactly the amount sellers optimistically ask for.
        """
        if service not in SERVICES:
            raise FailClosed(f"{service!r} is not one of the five services")

        fresh, stale, undated = [], 0, 0
        for o in self.observations(service):
            age = o.age_days(today)
            if age is None:
                undated += 1
                continue
            if age > max_age_days:
                stale += 1
                continue
            fresh.append(o)

        transacted = [o for o in fresh if o.basis is Basis.TRANSACTED]
        asking = [o for o in fresh if o.basis is Basis.ASKING]
        chosen, label = ((transacted, "transacted")
                         if len(transacted) >= MINIMUM_OBSERVATIONS
                         else (fresh, "asking and transacted together"))

        if len(chosen) < MINIMUM_OBSERVATIONS:
            why = (f"{len(chosen)} usable observation(s), fewer than the "
                   f"{MINIMUM_OBSERVATIONS} this needs")
            if stale:
                why += f"; {stale} dropped as older than {max_age_days} days"
            if undated:
                why += f"; {undated} dropped for an unreadable date"
            return Band(service, unavailable(why), unavailable(why),
                        unavailable(why), len(chosen), len(transacted),
                        len(asking), Confidence.INSUFFICIENT_DATA, why)

        per_unit = sorted(o.cents_per_unit for o in chosen)
        detail = f"{len(chosen)} observation(s), {label}"
        provenance = (Provenance.HISTORICAL_OBSERVATION if label == "transacted"
                      else Provenance.MODEL_ESTIMATE)
        return Band(
            service=service,
            low=Figure(per_unit[0], provenance, detail),
            median=Figure(statistics.median(per_unit), provenance, detail),
            high=Figure(per_unit[-1], provenance, detail),
            observations=len(chosen), transacted=len(transacted),
            asking=len(asking),
            confidence=_confidence(len(chosen), len(transacted)),
            why=detail)


def _confidence(total: int, transacted: int) -> Confidence:
    """Confidence follows the evidence, not the sample size alone.

    A hundred asking prices are still a hundred things nobody paid, so
    a band made only of them never reads HIGH.
    """
    if total < MINIMUM_OBSERVATIONS:
        return Confidence.INSUFFICIENT_DATA
    if transacted >= MINIMUM_OBSERVATIONS and total >= 10:
        return Confidence.HIGH
    if transacted >= 3 or total >= 10:
        return Confidence.MEDIUM
    return Confidence.LOW


def supported_price_cents(band: Band, scope_units: int) -> Figure:
    """What the band says a job of this size is worth, or UNAVAILABLE."""
    if scope_units <= 0:
        raise FailClosed("a job with no scope has no comparable price")
    if not band.usable:
        return unavailable(band.why)
    return Figure(int(band.median.value * scope_units),
                  band.median.provenance,
                  f"{band.observations} comparable(s) at "
                  f"{band.median.value:.2f}/unit")
