"""Shared test scaffolding.

Pricing fixtures are marked ``is_fixture=True`` and sourced ``FIXTURE`` so a
reader can never mistake them for live market truth. Rates are plausible but
invented; their only job is to be *different per state* so location handling is
actually exercised.
"""

from __future__ import annotations

from solvent.audit import AuditLog
from solvent.governor import FinancialGovernor
from solvent.ledger import Ledger
from solvent.policy import PolicyStore
from solvent.pricing import PricingReference
from solvent.store import Store
from solvent.types import CostCategory, Jurisdiction, PricingTier

OWNER = "owner:test"

#: Invented per-state hourly rates (cents). Deliberately far apart so a test
#: that ignores jurisdiction cannot accidentally pass.
STATE_LABOR_RATES = {"CA": 9500, "CT": 7800, "NC": 5200}


class Rig:
    """A fully wired Solvent with an in-memory database."""

    def __init__(self) -> None:
        self.store = Store()
        self.audit = AuditLog(self.store)
        self.policy = PolicyStore(self.store, self.audit)
        self.ledger = Ledger(self.store, self.audit)
        self.pricing = PricingReference(self.store, self.policy)
        self.governor = FinancialGovernor(
            self.store, self.audit, self.policy, self.ledger, self.pricing)

    def load_labor_fixtures(self, effective: str = "2026-06-01") -> None:
        for state, rate in STATE_LABOR_RATES.items():
            self.pricing.ingest(
                category=CostCategory.ON_SITE_LABOR, jurisdiction=Jurisdiction(state=state),
                tier=PricingTier.MARKET_DATA, unit_cents=rate, unit="hour",
                source="FIXTURE", effective_date=effective, retrieved_date="2026-09-01",
                confidence=0.9, owner_identity=OWNER, is_fixture=True)

    def relax_caps(self, *, max_job=10_000_000, exposure=10_000_000,
                   approval_above=10_000_000, floor=0.30) -> None:
        """Lift the deliberately punitive bootstrap caps for tests about other things."""
        self.policy.amend({"financial": {
            "max_job_spend_cents": max_job,
            "max_committed_unverified_cents": exposure,
            "owner_approval_above_cents": approval_above,
            "margin_floor": floor,
        }}, OWNER, "test: isolate the control under test")
