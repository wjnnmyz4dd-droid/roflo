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
        self.policy = PolicyStore(self.store, self.audit, owner_identity=OWNER)
        self.ledger = Ledger(self.store, self.audit)
        self.pricing = PricingReference(self.store, self.policy)
        self.governor = FinancialGovernor(
            self.store, self.audit, self.policy, self.ledger, self.pricing)
        # Evidence above T0 is refused unless the verifier that produced it has
        # shown it can detect wrong work. The rig certifies the real CSV
        # verifier against the real battery so tests about other controls can
        # record evidence — earned, not asserted.
        self.capability = _certify_csv_verifier(self.store, self.audit, self.policy)
        self.audit.trust_verifiers_via(self.capability)

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


# --------------------------------------------------------- verifier certification
#: A real check name from a real certified verifier. Tests that need to get
#: *past* the verifier gate in order to exercise a control underneath it use
#: these, rather than a stub that answers yes — a permissive stub in the
#: fixtures would silently disable the control in every test that touched it.
CERTIFIED_VERIFIER = "solvent.csvverify.run"
CERTIFIED_CAPABILITY = "csv-cleanup/1.0"
CERTIFIED_CHECK = "parses_as_csv"


def _certify_csv_verifier(store, audit, policy):
    """Earn the CSV verifier's certification against the real battery."""
    from solvent import certgen, csvverify, verifiercert
    from solvent.capability import CapabilityRegistry

    registry = CapabilityRegistry(store, audit, policy)
    report = verifiercert.run_generated(
        csvverify.run, capability="csv-cleanup", verifier_ref=CERTIFIED_VERIFIER,
        seed=certgen.CERTIFICATION_SEED, cases=14, stream="certification")
    record = registry.certify_verifier(
        verifier_ref=CERTIFIED_VERIFIER, capability_version=CERTIFIED_CAPABILITY,
        report=report,
        fingerprint=verifiercert.implementation_fingerprint(csvverify.run))
    assert record["state"] == "CERTIFIED", record["why"]
    return registry


def certified_audit(store=None):
    """``(audit, store)`` with the real CSV verifier genuinely certified.

    The certification is earned here the same way the pipeline earns it: the
    real verifier is run against the real hidden-ground-truth battery and the
    real registry judges the result. Nothing is asserted into place.
    """
    from solvent import csvverify, verifiercert
    from solvent.capability import CapabilityRegistry

    store = store or Store(":memory:")
    audit = AuditLog(store)
    policy = PolicyStore(store, audit, owner_identity=OWNER)
    audit.trust_verifiers_via(_certify_csv_verifier(store, audit, policy))
    return audit, store


def certified_evidence_fields(check: str = CERTIFIED_CHECK) -> dict:
    """The three fields ``record_verification`` now requires above T0."""
    return {"verifier_ref": CERTIFIED_VERIFIER,
            "capability_version": CERTIFIED_CAPABILITY, "check": check}


#: Signing keys for the suite. Long enough to satisfy the channel's own floor,
#: and labelled so that anything which ever leaks one into a log, a database or
#: a report is unmistakably a fixture rather than the owner's. Solvent's real
#: key is never generated, read or written by these tests: it lives in the
#: host's secret store and reaches the process as SOLVENT_OWNER_KEY.
TEST_ONLY_OWNER_KEY = b"TEST-ONLY-not-a-real-owner-key-0123456789abcdef"
TEST_ONLY_ATTACKER_KEY = b"TEST-ONLY-an-attackers-key-fedcba9876543210-xyz"
