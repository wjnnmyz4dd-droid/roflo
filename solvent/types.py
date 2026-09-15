"""Data contracts.

Money is **integer cents**, never float. Costs round *up* when a factor is
applied, because a rounding error in Solvent's favour is a rounding error
against the owner.

Every enum here is a closed set. Gates compare enum members and integers, never
free text, because free text is how untrusted content reaches a decision.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum

Cents = int


def money(dollars: float | str) -> Cents:
    """Convert a dollar amount to cents, rounding half away from zero."""
    from decimal import ROUND_HALF_UP, Decimal

    return int(Decimal(str(dollars)).quantize(Decimal("0.01"), ROUND_HALF_UP) * 100)


def fmt(cents: Cents) -> str:
    """Render cents as a dollar string for humans and audit records."""
    sign = "-" if cents < 0 else ""
    return f"{sign}${abs(cents) // 100}.{abs(cents) % 100:02d}"


def apply_factor(cents: Cents, factor: float) -> Cents:
    """Apply a calibration factor to a cost, always rounding **up**.

    Used by the Financial Governor's conservative bound. Rounding up is the
    fail-closed direction for an expense.
    """
    if factor < 1.0 and cents >= 0:
        # A factor below 1 would make a cost cheaper than measured; the Governor
        # never uses calibration to reduce a cost estimate.
        factor = 1.0
    return int(math.ceil(cents * factor))


class OperatingMode(Enum):
    """Kill-switch state. Owned by the Policy Store, enforced at two chokepoints."""

    NORMAL = "NORMAL"
    PAUSE_NEW_WORK = "PAUSE_NEW_WORK"
    PAUSE_SPEND = "PAUSE_SPEND"
    PAUSE_EXTERNAL = "PAUSE_EXTERNAL"
    SAFE_MODE = "SAFE_MODE"
    HALT = "HALT"

    @property
    def blocks_new_work(self) -> bool:
        return self is not OperatingMode.NORMAL

    @property
    def blocks_spend(self) -> bool:
        return self in (
            OperatingMode.PAUSE_SPEND, OperatingMode.PAUSE_EXTERNAL,
            OperatingMode.SAFE_MODE, OperatingMode.HALT,
        )

    @property
    def blocks_external(self) -> bool:
        return self in (
            OperatingMode.PAUSE_EXTERNAL, OperatingMode.SAFE_MODE, OperatingMode.HALT,
        )


class ActionClass(Enum):
    """What kind of effect an action has. Policy assigns a level to each class."""

    C0_INTERNAL_READ = "C0_INTERNAL_READ"
    C1_EXTERNAL_READ = "C1_EXTERNAL_READ"
    C2_EXTERNAL_COMMUNICATION = "C2_EXTERNAL_COMMUNICATION"
    C3_FINANCIAL_COMMITMENT = "C3_FINANCIAL_COMMITMENT"
    C4_CREDENTIAL_CONFIG_CHANGE = "C4_CREDENTIAL_CONFIG_CHANGE"
    C5_AUTHORITY_CHANGE = "C5_AUTHORITY_CHANGE"

    @property
    def is_external(self) -> bool:
        """C0 is the only class with no external effect."""
        return self is not ActionClass.C0_INTERNAL_READ

    @property
    def spends(self) -> bool:
        """Classes that may cost money and therefore need a Governor grant."""
        return self in (
            ActionClass.C1_EXTERNAL_READ, ActionClass.C2_EXTERNAL_COMMUNICATION,
            ActionClass.C3_FINANCIAL_COMMITMENT,
        )


class PermissionLevel(Enum):
    AUTONOMOUS = "AUTONOMOUS"
    PREAUTHORIZED = "PREAUTHORIZED"
    OWNER_APPROVAL_REQUIRED = "OWNER_APPROVAL_REQUIRED"
    PROHIBITED = "PROHIBITED"


class PrivacyClass(Enum):
    """Sensitivity of the payload. Privacy overrides cost, always."""

    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    CLIENT_CONFIDENTIAL = "CLIENT_CONFIDENTIAL"
    RESTRICTED = "RESTRICTED"


class JobState(Enum):
    """The twelve P0 states. See docs/solvent-p0-architecture-contract.md §E."""

    INTAKE = "INTAKE"
    QUALIFYING = "QUALIFYING"
    AWAITING_OWNER_APPROVAL = "AWAITING_OWNER_APPROVAL"
    ACCEPTED = "ACCEPTED"
    EXECUTING = "EXECUTING"
    BLOCKED = "BLOCKED"
    VERIFYING = "VERIFYING"
    READY_FOR_DELIVERY = "READY_FOR_DELIVERY"
    AWAITING_PAYMENT = "AWAITING_PAYMENT"
    COMPLETE = "COMPLETE"
    REJECTED = "REJECTED"
    FAILED = "FAILED"

    @property
    def is_terminal(self) -> bool:
        return self in (JobState.COMPLETE, JobState.REJECTED, JobState.FAILED)


class BlockedOn(Enum):
    """Why a job is blocked. Required whenever state is BLOCKED.

    Four proposed states (WAITING / BLOCKED / NEEDS_CLIENT / NEEDS_OWNER)
    collapse into one state plus this field, so the timeout rule is written once.
    """

    CLIENT = "CLIENT"
    OWNER = "OWNER"
    EXTERNAL = "EXTERNAL"
    DEPENDENCY = "DEPENDENCY"
    INFORMATION = "INFORMATION"


class RequirementSource(Enum):
    """Provenance of a requirement. This is what closes the injection path.

    Only OWNER_CONFIRMED and CLIENT_CONFIRMED_STRUCTURED may gate a commitment;
    a requirement a model extracted from untrusted text is a draft.
    """

    OWNER_CONFIRMED = "OWNER_CONFIRMED"
    CLIENT_CONFIRMED_STRUCTURED = "CLIENT_CONFIRMED_STRUCTURED"
    MODEL_EXTRACTED_UNCONFIRMED = "MODEL_EXTRACTED_UNCONFIRMED"

    @property
    def may_gate_commitment(self) -> bool:
        return self is not RequirementSource.MODEL_EXTRACTED_UNCONFIRMED


class Conformance(Enum):
    CONFORMS = "CONFORMS"
    FAILS = "FAILS"
    UNKNOWN = "UNKNOWN"

    @property
    def may_commit(self) -> bool:
        """UNKNOWN is not conforming for commitment purposes."""
        return self is Conformance.CONFORMS


class CapabilityVerdict(Enum):
    CAN_EXECUTE = "CAN_EXECUTE"
    CAN_EXECUTE_WITH_ASSISTANCE = "CAN_EXECUTE_WITH_ASSISTANCE"
    REQUIRES_NEW_CAPABILITY = "REQUIRES_NEW_CAPABILITY"
    REQUIRES_HUMAN = "REQUIRES_HUMAN"
    REQUIRES_OWNER_APPROVAL = "REQUIRES_OWNER_APPROVAL"
    CANNOT_EXECUTE = "CANNOT_EXECUTE"

    @property
    def can_proceed_unaided(self) -> bool:
        return self in (
            CapabilityVerdict.CAN_EXECUTE, CapabilityVerdict.CAN_EXECUTE_WITH_ASSISTANCE,
        )


class GovernorVerdict(Enum):
    PROFITABLE = "PROFITABLE"
    MARGIN_TOO_LOW = "MARGIN_TOO_LOW"
    REQUIRES_APPROVAL = "REQUIRES_APPROVAL"
    HIGH_RISK = "HIGH_RISK"
    REJECT = "REJECT"
    NEEDS_INFORMATION = "NEEDS_INFORMATION"
    PRICING_LOCATION_UNKNOWN = "PRICING_LOCATION_UNKNOWN"

    @property
    def authorises_spend(self) -> bool:
        return self is GovernorVerdict.PROFITABLE


class ConsequenceTier(Enum):
    """How much a mistake costs. Drives how much verification is required."""

    C_LOW = "C_LOW"
    C_MED = "C_MED"
    C_HIGH = "C_HIGH"
    C_CRITICAL = "C_CRITICAL"

    @property
    def requires_independent_verifier(self) -> bool:
        """At C_HIGH and above the executor may not vouch for itself."""
        return self in (ConsequenceTier.C_HIGH, ConsequenceTier.C_CRITICAL)


class VerificationTier(Enum):
    T0_SELF_REPORT = "T0_SELF_REPORT"
    T1_DETERMINISTIC = "T1_DETERMINISTIC"
    T2_INDEPENDENT_REVIEW = "T2_INDEPENDENT_REVIEW"
    T3_EXTERNAL_FACT = "T3_EXTERNAL_FACT"

    @property
    def rank(self) -> int:
        return {"T0_SELF_REPORT": 0, "T1_DETERMINISTIC": 1,
                "T2_INDEPENDENT_REVIEW": 2, "T3_EXTERNAL_FACT": 3}[self.value]

    @property
    def may_feed_learning(self) -> bool:
        """T0 is never evidence of anything."""
        return self.rank >= 1

    @property
    def may_feed_economic_learning(self) -> bool:
        """Only an external fact proves a business outcome."""
        return self is VerificationTier.T3_EXTERNAL_FACT


class PaymentState(Enum):
    ESTIMATED = "ESTIMATED"
    AUTHORIZED = "AUTHORIZED"
    INCURRED = "INCURRED"
    INVOICED = "INVOICED"
    PAID = "PAID"
    PARTIALLY_PAID = "PARTIALLY_PAID"
    OVERDUE = "OVERDUE"
    REFUNDED = "REFUNDED"
    DISPUTED = "DISPUTED"

    @property
    def is_collected(self) -> bool:
        """Expected revenue is not collected revenue."""
        return self in (PaymentState.PAID, PaymentState.PARTIALLY_PAID)


class CostCategory(Enum):
    """Cost lines. Each names the jurisdiction signal it takes pricing from."""

    ON_SITE_LABOR = "ON_SITE_LABOR"
    REMOTE_LABOR = "REMOTE_LABOR"
    MATERIALS = "MATERIALS"
    SALES_TAX = "SALES_TAX"
    PREVAILING_WAGE = "PREVAILING_WAGE"
    PERMITS = "PERMITS"
    MARKET_RATE = "MARKET_RATE"
    AI_API = "AI_API"
    COMPUTE = "COMPUTE"
    TOOLS = "TOOLS"
    VENDOR = "VENDOR"
    PLATFORM_FEES = "PLATFORM_FEES"
    PAYMENT_FEES = "PAYMENT_FEES"
    ACQUISITION = "ACQUISITION"
    VERIFICATION = "VERIFICATION"
    QC = "QC"
    REVISION_ALLOWANCE = "REVISION_ALLOWANCE"
    RISK_ALLOWANCE = "RISK_ALLOWANCE"

    @property
    def is_geographic(self) -> bool:
        """Categories whose cost depends on where the work happens."""
        return self in GEOGRAPHIC_CATEGORIES

    @property
    def is_allowance(self) -> bool:
        """Deliberate reserves, not predictions.

        These are set conservatively on purpose. Measuring estimation error
        against them would teach the Governor that its own safety margins are
        overestimates and shrink them — the estimator loophole in another form —
        so calibration excludes them from both sides of the ratio.
        """
        return self in (
            CostCategory.VERIFICATION, CostCategory.QC,
            CostCategory.RISK_ALLOWANCE, CostCategory.REVISION_ALLOWANCE,
        )

    @property
    def is_known_cost(self) -> bool:
        """Costs Solvent knows exactly, so calibration must not inflate them.

        Applying an uncertainty factor to a published platform fee is nonsense
        and would reject good work for no reason.
        """
        return self in (
            CostCategory.PLATFORM_FEES, CostCategory.PAYMENT_FEES, CostCategory.SALES_TAX,
        )


GEOGRAPHIC_CATEGORIES = frozenset({
    CostCategory.ON_SITE_LABOR, CostCategory.REMOTE_LABOR, CostCategory.MATERIALS,
    CostCategory.SALES_TAX, CostCategory.PREVAILING_WAGE, CostCategory.PERMITS,
    CostCategory.MARKET_RATE,
})


class PricingTier(Enum):
    """Override precedence. Lower number wins.

    Every tier lives in a store Learning cannot write — that is the point of the
    hierarchy, not merely its ordering.
    """

    LAW_CONTRACT = 1
    OWNER_POLICY = 2
    MARKET_DATA = 3
    HISTORICAL_ACTUALS = 4
    FALLBACK = 5

    @property
    def binding_quote_allowed(self) -> bool:
        """A national fallback may inform analysis; it may never bind a price."""
        return self is not PricingTier.FALLBACK


@dataclass(frozen=True, slots=True)
class Jurisdiction:
    """A hierarchical place. State is the P0 baseline; finer levels are data."""

    country: str = "US"
    state: str = ""
    county: str = ""
    city: str = ""
    postal: str = ""

    def path(self) -> str:
        parts = [p for p in (self.country, self.state, self.county, self.city, self.postal) if p]
        return ">".join(parts)

    def specificity(self) -> int:
        return len([p for p in (self.country, self.state, self.county, self.city, self.postal) if p])

    def generalise(self) -> "Jurisdiction | None":
        """Drop the most specific level, for falling back up the hierarchy."""
        for attr in ("postal", "city", "county", "state"):
            if getattr(self, attr):
                return _drop(self, attr)
        return None

    def __bool__(self) -> bool:
        return bool(self.state or self.city or self.postal or self.county)


def _drop(j: Jurisdiction, attr: str) -> Jurisdiction:
    vals = {"country": j.country, "state": j.state, "county": j.county,
            "city": j.city, "postal": j.postal}
    vals[attr] = ""
    return Jurisdiction(**vals)


@dataclass(frozen=True, slots=True)
class CostLine:
    """One line of an estimate, with the evidence that produced it."""

    category: CostCategory
    amount: Cents
    jurisdiction: Jurisdiction | None = None
    pricing_tier: PricingTier | None = None
    reference_id: str = ""
    note: str = ""

    @property
    def is_estimated(self) -> bool:
        """Uncertain lines take the calibration factor; known lines do not."""
        return not self.category.is_known_cost


@dataclass(frozen=True, slots=True)
class Requirement:
    id: str
    text: str
    source: RequirementSource
    confirmed_by: str = ""


@dataclass(frozen=True, slots=True)
class LocationSignals:
    """Where the job actually happens. The owner's location is not a signal."""

    project: Jurisdiction | None = None
    client: Jurisdiction | None = None
    worker: Jurisdiction | None = None
    material_destination: Jurisdiction | None = None
    contract_clause: Jurisdiction | None = None


@dataclass(slots=True)
class PricingContext:
    """Per-category jurisdiction bindings — deliberately not one jurisdiction.

    A Connecticut client with a California project and a remote worker is three
    bindings. Collapsing them to one is the error this type exists to prevent.
    """

    bindings: dict[CostCategory, Jurisdiction] = field(default_factory=dict)
    unresolved: list[CostCategory] = field(default_factory=list)
    signals_used: dict[str, str] = field(default_factory=dict)

    def jurisdiction_for(self, category: CostCategory) -> Jurisdiction | None:
        return self.bindings.get(category)
