"""First-revenue readiness — a projection, not an authority.

Answers one question for the owner: *what actually stands between Solvent and its
first real paid job?*

It decides nothing and stores nothing. Each check reads the authority that owns
the fact, so the answer cannot drift from reality, and each names whether it is
something the owner must decide or something engineering can finish.

The point is that the remaining distance is short and specific. Solvent can
already find work, refuse most of it, price it by jurisdiction, execute, verify,
deliver and account for profit. What it cannot do is contract, get paid, or sell
model output — and none of those is a coding problem.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .errors import FailClosed
from .ledger import SIMULATED_PREFIX
from .types import fmt


#: What kind of thing is standing in the way. The distinction matters because
#: the owner can act on three of these and wait on one.
ENGINEERING = "ENGINEERING"
OWNER_CONFIG = "OWNER_CONFIG"
EXTERNAL_VERIFICATION = "EXTERNAL_VERIFICATION"
LIVE = "LIVE"


@dataclass(slots=True)
class Check:
    name: str
    ready: bool
    detail: str
    owner_decision: str = ""
    category: str = ENGINEERING

    @property
    def mark(self) -> str:
        if self.ready:
            return "READY"
        return {OWNER_CONFIG: "OWNER", EXTERNAL_VERIFICATION: "VERIFY",
                LIVE: "LIVE"}.get(self.category, "TODO")


@dataclass(slots=True)
class Readiness:
    checks: list[Check] = field(default_factory=list)
    ready: bool = False
    blocking: list[str] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> dict:
        return {
            "ready_for_first_real_job": self.ready,
            "summary": self.summary,
            "blocking": self.blocking,
            "checks": [{"check": c.name, "status": c.mark, "detail": c.detail,
                        "owner_decision": c.owner_decision,
                        "category": c.category} for c in self.checks],
        }


def first_revenue_readiness(*, policy, ledger, capability, discovery,
                            owner_channel=None) -> Readiness:
    """Report what is ready and what is not, from the authorities themselves."""
    report = Readiness()
    add = report.checks.append

    # --- things engineering controls -------------------------------------
    add(Check("acquisition pipeline", True,
              "discovery, qualification, ranking and selection are implemented "
              "and tested", category=ENGINEERING))
    add(Check("execution and verification", True,
              "verified delivery is enforced: a job cannot be delivered or "
              "completed without evidence at its consequence tier",
              category=ENGINEERING))
    add(Check("location-aware pricing", True,
              "per-category jurisdiction binding; unknown material jurisdiction "
              "refuses a binding quote", category=ENGINEERING))

    proven = [c.name for c in capability.capabilities() if c.proven]
    add(Check("a proven capability to sell", bool(proven),
              f"proven capabilities: {', '.join(proven) or 'none registered'}",
              "" if proven else
              "OD-12: which service does Solvent sell first, and is it proven?",
              category=OWNER_CONFIG))

    sources = [s for s in discovery.sources() if s["compliance"] == "PERMITTED"]
    non_fixture = [s for s in sources if not s["is_fixture"]]
    add(Check("an approved work source", bool(non_fixture),
              f"{len(sources)} permitted source(s), {len(non_fixture)} not a fixture",
              "" if non_fixture else
              "OD-11: which source is researched and approved first?",
              category=OWNER_CONFIG))

    authenticated = bool(owner_channel and owner_channel.available)
    add(Check("authenticated owner approval", authenticated,
              "Owner Channel active: signed, scoped, single-use, expiring"
              if authenticated else
              "no owner signing key configured (SOLVENT_OWNER_KEY)",
              "" if authenticated else
              "OD-5: provision an owner signing key before real execution",
              category=OWNER_CONFIG))

    # --- things only the owner can decide --------------------------------
    entity = policy.get("governance", "legal_entity", default="")
    add(Check("legal contracting entity", bool(entity),
              entity or "not recorded",
              "" if entity else "OD-1: who legally accepts the work and invoices?",
              category=OWNER_CONFIG))

    rail = policy.get("payment", "approved_rail", default="")
    signal = policy.get("payment", "verification_signal", default="")
    add(Check("payment rail with a verification signal", bool(rail and signal),
              f"rail={rail or 'none'}, signal={signal or 'none'}",
              "" if rail and signal else
              "OD-2: without a verification signal PAID is unreachable and "
              "profit cannot be computed", category=EXTERNAL_VERIFICATION))

    cleared = policy.get("governance", "model_commercial_rights", default="")
    add(Check("model commercial rights", bool(cleared),
              cleared or "not cleared for any checkpoint",
              "" if cleared else
              "OD-3: commercial use of the exact production checkpoint",
              category=EXTERNAL_VERIFICATION))

    # --- posture ----------------------------------------------------------
    simulation = policy.get("egress", "simulation_only", default=True)
    add(Check("external execution posture", True,
              "FAIL-CLOSED (simulation_only)" if simulation
              else "LIVE — real external effects are enabled", category=LIVE))

    real = ledger.real_revenue_cents()
    add(Check("real revenue to date", real > 0,
              f"{fmt(real)} collected outside {SIMULATED_PREFIX} verification",
              category=LIVE))

    report.blocking = [f"[{c.category}] {c.owner_decision or c.name + ': ' + c.detail}"
                       for c in report.checks
                       if not c.ready and c.category != LIVE]
    report.ready = not report.blocking
    ready_count = sum(1 for c in report.checks if c.ready)
    report.summary = (
        f"{ready_count} of {len(report.checks)} checks ready; "
        f"{len(report.blocking)} blocking item(s)"
        if report.blocking else "ready to attempt a first real job")
    return report


def assert_may_attempt_first_real_job(report: Readiness) -> None:
    """Fail closed unless every precondition for a real job is satisfied.

    This is the acceptance contract as an executable check rather than a
    checklist someone remembers. It refuses on anything outstanding — including
    the live-posture item, because attempting a real job while external execution
    is fail-closed would produce a job that cannot be delivered or paid.

    Existing for it to be *called* is not the same as it being *enforced*: it is
    the owner's gate before switching anything on, and it says no until the owner
    has genuinely finished.
    """
    outstanding = list(report.blocking)
    live_check = next((c for c in report.checks if c.category == LIVE
                       and c.name == "external execution posture"), None)
    if live_check is not None and "FAIL-CLOSED" in live_check.detail:
        outstanding.append(
            "[LIVE] external execution is fail-closed (egress.simulation_only); "
            "a real job cannot be delivered or paid until the owner enables it")
    if outstanding:
        raise FailClosed(
            "not ready for a first real job:\n  - " + "\n  - ".join(outstanding))
