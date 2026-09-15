"""Location-aware pricing — a service the Governor uses, not an authority.

Solvent must not assume one price model applies everywhere: California labour is
not Connecticut labour, and North Carolina is neither. But location changes *cost
assumptions*, never *profit rules*, so there is no California Governor. There is
one Governor, and this module hands it better inputs.

Two design decisions carry the weight:

**Jurisdiction binds per cost category, not per job.** A Connecticut client with a
California project and a remote worker elsewhere is three bindings. On-site labour
follows the project, materials follow the delivery destination, market rate follows
the client. Collapsing them into "the job's state" is the mistake this module
exists to prevent.

**Every adjustment must resolve to a reference record.** A model may propose a
lookup key; it may never supply a value. If no record matches, the category is
UNKNOWN and — when it is material — the quote cannot bind. That is what stops an
LLM inventing a regional multiplier.

The reference data itself lives in tables owned by the Policy Store, never in
Business Memory. Learning writes to Memory, and pricing data Learning could edit
would be the estimator loophole wearing a different hat.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from .errors import FailClosed
from .store import Store
from .types import (
    Cents, CostCategory, CostLine, Jurisdiction, LocationSignals, PricingContext,
    PricingTier,
)

#: Which signal each geographic category takes its jurisdiction from, in order.
#: Explicit contract clause wins, then the statutory/physical location, then the
#: client. The owner's location is deliberately absent: it is not a signal.
SIGNAL_PRECEDENCE: dict[CostCategory, tuple[str, ...]] = {
    CostCategory.ON_SITE_LABOR: ("contract_clause", "project"),
    CostCategory.PREVAILING_WAGE: ("project",),
    CostCategory.PERMITS: ("project",),
    CostCategory.REMOTE_LABOR: ("worker",),
    CostCategory.MATERIALS: ("material_destination", "project"),
    CostCategory.SALES_TAX: ("material_destination", "project"),
    CostCategory.MARKET_RATE: ("client", "project"),
}


@dataclass(frozen=True, slots=True)
class PriceLookup:
    """A resolved price, with the evidence that produced it."""

    category: CostCategory
    jurisdiction: Jurisdiction
    unit_cents: Cents
    unit: str
    tier: PricingTier
    reference_id: str
    source: str
    confidence: float
    is_fixture: bool
    note: str = ""


class PricingResolutionError(FailClosed):
    """Raised when a material category cannot be priced. Never guessed around."""


def resolve_context(signals: LocationSignals,
                    categories: list[CostCategory]) -> PricingContext:
    """Bind each geographic category to its own jurisdiction.

    Non-geographic categories (API cost, platform fees) are skipped: they do not
    vary by place, so demanding a jurisdiction for them would block quotes for no
    reason.
    """
    context = PricingContext()
    for category in categories:
        if not category.is_geographic:
            continue
        for signal_name in SIGNAL_PRECEDENCE.get(category, ()):
            jurisdiction = getattr(signals, signal_name, None)
            if jurisdiction:
                context.bindings[category] = jurisdiction
                context.signals_used[category.value] = signal_name
                break
        else:
            context.unresolved.append(category)
    return context


class PricingReference:
    """Read access to approved pricing data, with freshness and conflict rules."""

    def __init__(self, store: Store, policy) -> None:
        self._store = store
        self._policy = policy
        # Writes go through the Policy authority: pricing data is owner-governed.
        self._db = store.for_authority("policy")

    # ------------------------------------------------------------- ingestion

    def ingest(self, *, category: CostCategory, jurisdiction: Jurisdiction,
               tier: PricingTier, unit_cents: Cents, unit: str, source: str,
               effective_date: str, retrieved_date: str, confidence: float,
               owner_identity: str, is_fixture: bool = False,
               reference_id: str | None = None) -> str:
        """Load a reference record. Owner path only, and only approved sources."""
        if not self._policy.is_owner(owner_identity):
            raise FailClosed(
                f"pricing reference data is owner-governed (got {owner_identity!r})")
        approved = self._policy.get("pricing", "approved_sources", default=[])
        if source not in approved:
            raise FailClosed(
                f"pricing source {source!r} is not approved; approved: {approved}")
        reference_id = reference_id or f"px_{category.value}_{jurisdiction.path()}_{tier.value}"
        self._db.execute(
            "INSERT OR REPLACE INTO pricing_reference(id,category,jurisdiction,tier,"
            "unit_cents,unit,source,effective_date,retrieved_date,confidence,is_fixture)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (reference_id, category.value, jurisdiction.path(), tier.value, unit_cents,
             unit, source, effective_date, retrieved_date, confidence,
             1 if is_fixture else 0))
        self._db.commit()
        return reference_id

    # --------------------------------------------------------------- lookup

    def _age_days(self, effective_date: str) -> int:
        try:
            eff = datetime.fromisoformat(effective_date).date()
        except ValueError:
            return 10**6  # unparseable is infinitely stale, never "fresh enough"
        return (date.today() - eff).days

    def _max_age(self, category: CostCategory) -> int:
        ages = self._policy.get("pricing", "max_age_days", default={})
        return int(ages.get(category.value, ages.get("DEFAULT", 365)))

    def _candidates(self, category: CostCategory,
                    jurisdiction: Jurisdiction) -> list[dict]:
        """Records for this exact jurisdiction, freshest tier order."""
        return [dict(r) for r in self._db.query(
            "SELECT * FROM pricing_reference WHERE category = ? AND jurisdiction = ? "
            "ORDER BY tier ASC", (category.value, jurisdiction.path()))]

    def lookup(self, category: CostCategory,
               jurisdiction: Jurisdiction) -> PriceLookup | None:
        """Resolve a price, walking up the jurisdiction hierarchy if needed.

        Returns ``None`` when nothing in-date matches — the caller decides whether
        that is material. Stale records are treated as *absent*, not as weak
        evidence, because a two-year-old labour rate is not a worse number, it is
        a different one.
        """
        owner_rate = self._owner_override(category, jurisdiction)
        if owner_rate is not None:
            return owner_rate

        current: Jurisdiction | None = jurisdiction
        while current:
            fresh: list[dict] = []
            for row in self._candidates(category, current):
                if self._age_days(row["effective_date"]) <= self._max_age(category):
                    fresh.append(row)
            if fresh:
                return self._choose(category, current, fresh)
            current = current.generalise()
        return None

    def _owner_override(self, category: CostCategory,
                        jurisdiction: Jurisdiction) -> PriceLookup | None:
        """Tier 2: the company's own approved rate for this place."""
        overrides = self._policy.get("pricing", "owner_rate_overrides", default={})
        key = f"{category.value}:{jurisdiction.path()}"
        entry = overrides.get(key)
        if not entry:
            return None
        return PriceLookup(
            category=category, jurisdiction=jurisdiction,
            unit_cents=int(entry["unit_cents"]), unit=entry.get("unit", "each"),
            tier=PricingTier.OWNER_POLICY, reference_id=f"owner:{key}",
            source="OWNER", confidence=float(entry.get("confidence", 1.0)),
            is_fixture=False, note="owner-approved company rate")

    def _choose(self, category: CostCategory, jurisdiction: Jurisdiction,
                fresh: list[dict]) -> PriceLookup:
        """Pick among in-date records, resolving conflict conservatively."""
        best_tier = min(int(r["tier"]) for r in fresh)
        tied = [r for r in fresh if int(r["tier"]) == best_tier]
        # Conflict within a tier: take the higher cost. Being wrong in the
        # expensive direction loses a job; being wrong the other way loses money.
        chosen = max(tied, key=lambda r: int(r["unit_cents"]))
        note = ""
        if len(tied) > 1:
            low = min(int(r["unit_cents"]) for r in tied)
            high = int(chosen["unit_cents"])
            spread = (high - low) / high if high else 0.0
            note = (f"conflict among {len(tied)} sources at tier {best_tier}: "
                    f"spread {spread:.1%}; took the higher")
        return PriceLookup(
            category=category, jurisdiction=jurisdiction,
            unit_cents=int(chosen["unit_cents"]), unit=chosen["unit"],
            tier=PricingTier(int(chosen["tier"])), reference_id=chosen["id"],
            source=chosen["source"], confidence=float(chosen["confidence"]),
            is_fixture=bool(chosen["is_fixture"]), note=note)

    def conflict_spread(self, category: CostCategory,
                        jurisdiction: Jurisdiction) -> float:
        """Relative disagreement among in-date same-tier sources."""
        fresh = [r for r in self._candidates(category, jurisdiction)
                 if self._age_days(r["effective_date"]) <= self._max_age(category)]
        if not fresh:
            return 0.0
        best_tier = min(int(r["tier"]) for r in fresh)
        tied = [int(r["unit_cents"]) for r in fresh if int(r["tier"]) == best_tier]
        if len(tied) < 2:
            return 0.0
        return (max(tied) - min(tied)) / max(tied)

    # ------------------------------------------------------------ materiality

    def price_line(self, category: CostCategory, jurisdiction: Jurisdiction | None,
                   quantity: float) -> tuple[CostLine | None, str]:
        """Produce one cost line, or explain why it cannot be produced."""
        if jurisdiction is None:
            return None, f"{category.value}: no jurisdiction bound"
        found = self.lookup(category, jurisdiction)
        if found is None:
            return None, (f"{category.value}: no in-date approved reference for "
                          f"{jurisdiction.path()}")
        amount = int(round(found.unit_cents * quantity))
        line = CostLine(category=category, amount=amount, jurisdiction=found.jurisdiction,
                        pricing_tier=found.tier, reference_id=found.reference_id,
                        note=found.note or f"{found.source} @ {found.jurisdiction.path()}")
        return line, ""

    def legal_floor_violation(self, line: CostLine) -> str:
        """Tier 1 is a floor, not merely a priority.

        A company rate below a legally required rate is invalid, not outranked —
        so the Governor rejects the estimate rather than silently substituting.
        """
        if line.jurisdiction is None or line.pricing_tier is PricingTier.LAW_CONTRACT:
            return ""
        legal = [r for r in self._candidates(line.category, line.jurisdiction)
                 if int(r["tier"]) == PricingTier.LAW_CONTRACT.value]
        if not legal:
            return ""
        required = max(int(r["unit_cents"]) for r in legal)
        if line.amount < required:
            return (f"{line.category.value}: {line.amount} is below the legally "
                    f"required {required} in {line.jurisdiction.path()}")
        return ""
