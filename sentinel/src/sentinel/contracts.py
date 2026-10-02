"""Typed contracts exchanged between components.

Every object that crosses a component boundary is a frozen dataclass with a
canonical JSON form and a content hash. Components never pass mutable state to
each other; they pass these records, and the journal stores them verbatim.
"""

from __future__ import annotations

import dataclasses
import enum
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

SCHEMA_VERSION = "1.0.0"


def canonical_json(obj: Any) -> str:
    return json.dumps(_plain(obj), sort_keys=True, separators=(",", ":"), allow_nan=False)


def content_hash(obj: Any) -> str:
    return hashlib.sha256(canonical_json(obj).encode()).hexdigest()


def _plain(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: _plain(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, enum.Enum):
        return obj.value
    if isinstance(obj, dict):
        return {str(k): _plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_plain(v) for v in obj]
    if isinstance(obj, float) and obj != obj:  # NaN is never a legal contract value
        raise ValueError("NaN in contract payload")
    return obj


class Direction(str, enum.Enum):
    LONG = "LONG"
    SHORT = "SHORT"

    @property
    def sign(self) -> int:
        return 1 if self is Direction.LONG else -1


class ValidationVerdict(str, enum.Enum):
    VALID = "VALID"
    INVALID = "INVALID"
    INDETERMINATE = "INDETERMINATE"


class ArbiterVerdict(str, enum.Enum):
    APPROVE_FOR_RISK_REVIEW = "APPROVE_FOR_RISK_REVIEW"
    REJECT = "REJECT"
    NO_TRADE = "NO_TRADE"
    INDETERMINATE = "INDETERMINATE"


class GateVerdict(str, enum.Enum):
    """Shared by the three veto authorities (compliance, news, risk)."""

    ALLOW = "ALLOW"
    REDUCE = "REDUCE"  # risk only
    BLOCK = "BLOCK"
    HALT = "HALT"


@dataclass(frozen=True)
class Bar:
    instrument: str
    ts: int  # bar OPEN time, unix seconds UTC
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass(frozen=True)
class DataProvenance:
    source: str
    retrieved_at: int
    dataset_hash: str
    last_bar_ts: int
    bar_seconds: int


@dataclass(frozen=True)
class TradeCandidate:
    candidate_id: str
    created_at: int
    instrument: str
    direction: Direction
    setup: str
    strategy_version: str
    regime: dict
    entry_reference: float
    entry_hypothesis: str
    invalidation_price: float
    invalidation_hypothesis: str
    horizon_bars: int
    expires_at: int
    evidence: dict
    confidence: float | None
    provenance: DataProvenance
    feature_versions: dict
    model_versions: dict
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.entry_reference <= 0 or self.invalidation_price <= 0:
            raise ValueError("non-positive price in candidate")
        risk = (self.entry_reference - self.invalidation_price) * self.direction.sign
        if risk <= 0:
            raise ValueError("invalidation is on the wrong side of entry")


@dataclass(frozen=True)
class ValidationReport:
    candidate_id: str
    verdict: ValidationVerdict
    checks: dict  # name -> {"passed": bool|None, "detail": str}
    reasons: tuple
    validator_version: str
    feature_set: tuple
    data_sources: tuple
    created_at: int


@dataclass(frozen=True)
class ArbiterDecision:
    candidate_id: str
    verdict: ArbiterVerdict
    rule_trace: tuple  # ordered rule ids that fired
    reasons: tuple
    arbiter_version: str
    created_at: int
    signature: str = ""


@dataclass(frozen=True)
class GateDecision:
    authority: str  # "compliance" | "news" | "risk"
    verdict: GateVerdict
    reasons: tuple
    inputs_hash: str
    created_at: int
    approved_volume: float | None = None
    detail: dict = field(default_factory=dict)
    subject: str = ""  # candidate id the decision is about ("" = account-level)
    signature: str = ""


@dataclass(frozen=True)
class OrderIntent:
    intent_id: str
    candidate_id: str
    instrument: str
    direction: Direction
    volume: float
    stop_loss: float
    take_profit: float | None
    max_slippage_points: int
    created_at: int
    expires_at: int


@dataclass(frozen=True)
class Position:
    ticket: str
    instrument: str
    direction: Direction
    volume: float
    open_price: float
    stop_loss: float | None
    take_profit: float | None
    open_time: int
    client_id: str | None
    unrealized_pnl: float
    swap: float = 0.0
    commission: float = 0.0


@dataclass(frozen=True)
class InstrumentSpec:
    instrument: str
    asset_class: str
    currencies: tuple
    tick_size: float
    tick_value: float  # account currency per tick per 1.0 volume
    volume_min: float
    volume_max: float
    volume_step: float
    contract_size: float


@dataclass(frozen=True)
class Quote:
    instrument: str
    bid: float
    ask: float
    ts: int


@dataclass(frozen=True)
class AccountSnapshot:
    """Broker truth at a moment. Local state never overrides this."""

    taken_at: int
    server_time: int
    balance: float
    equity: float
    margin: float
    free_margin: float
    currency: str
    positions: tuple
    pending_orders: tuple
    snapshot_hash: str = ""

    def with_hash(self) -> "AccountSnapshot":
        return dataclasses.replace(self, snapshot_hash=content_hash(dataclasses.replace(self, snapshot_hash="")))
