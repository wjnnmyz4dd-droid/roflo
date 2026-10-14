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


# --------------------------------------------------------------- commerce
#
# Two things every rig that runs a whole business cycle now needs, and a
# real installation needs them too. They live here rather than in each
# suite because they are the same two answers everywhere, and because a
# suite that forgets one gets a confusing stop rather than a clear one.

def _an_owner(solvent, owner_identity: str = "") -> str:
    """Whoever this installation has registered as its owner.

    Asked rather than assumed: this module's OWNER and the harness's are
    different strings, and a helper that hard-codes one works in half
    the suites and fails confusingly in the other half.
    """
    if owner_identity:
        return owner_identity
    registered = solvent.policy.get("governance", "owner_identities",
                                    default=[])
    if not registered:
        raise AssertionError(
            "this installation has no registered owner, so nothing can be "
            "recorded on the owner's behalf")
    return registered[0]


def give_an_issuer(solvent, *, owner_identity: str = "") -> None:
    """Record who is asking to be paid.

    An invoice refuses to name nobody. It used to print "(contracting
    identity not recorded)" into the From line and count the stage as
    done, which produced a document that looks like a bill, names
    nobody to pay, and cannot be sent.
    """
    solvent.policy.record_contracting_structure(
        owner_identity=_an_owner(solvent, owner_identity),
        structure="INDIVIDUAL",
        reason="test: so an invoice can name an issuer",
        legal_name="TEST-ONLY Owner",
        email="test-only@example.invalid",
        address="1 Test Street, Testville")


def set_commercial_terms(solvent, *, owner_identity: str = "",
                         **overrides) -> None:
    """The four terms that decide what work is worth taking.

    The Job Finder will not recommend pursuing anything while one of
    them is missing, so a suite testing triage against an unconfigured
    installation would be testing that gate over and over. The values
    are test values; the citation says so, because a rate without one
    is refused and a fake citation that reads like a real one would be
    worse than the refusal.
    """
    terms = {
        "minimum_project_fee_cents": 4_000,
        "market_hourly_cents": {"csv-cleanup": 3_000},
        "market_rate_citations": {
            "csv-cleanup": "TEST-ONLY fixture, not a real citation"},
        "compute_cents_per_cpu_hour": 12,
        "minimum_margin_fraction": 0.05,
    }
    terms.update(overrides)
    solvent.policy.amend({"commercial": terms},
                         _an_owner(solvent, owner_identity),
                         "test: the owner records the commercial terms")


def source_names(path: str) -> set:
    """Identifiers and real string literals in a module, ignoring prose.

    Four separate tests this session have failed because they grepped a
    module's text for a forbidden word and found it in a docstring
    explaining that the module does not do that thing. A test that
    cannot tell a promise from its breach is worse than no test, so
    structural checks go through here instead: the AST carries no
    comments at all, and docstrings are dropped on the way past.
    """
    import ast
    import pathlib

    tree = ast.parse(pathlib.Path(path).read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", None) or []
            first = body[0] if body else None
            if (isinstance(first, ast.Expr)
                    and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                docstrings.add(id(first.value))

    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, ast.keyword) and node.arg:
            found.add(node.arg)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name.split(".")[0])
                found.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            found.add((node.module or "").split(".")[0])
            found.update(a.name for a in node.names)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                               ast.ClassDef)):
            found.add(node.name)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docstrings:
                found.add(node.value)
    return found
