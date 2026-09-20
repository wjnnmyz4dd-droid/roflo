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

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field

from .errors import FailClosed
from .ledger import SIMULATED_PREFIX
from .policy import DIGEST_RE
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


#: Evidence an artifact clearance must carry. Policy refuses to store a record
#: missing any of these, and this side re-checks rather than trusting that.
ARTIFACT_EVIDENCE = ("model", "tag", "digest", "license_id", "license_source",
                     "verified_on")


#: The environment overrides the config file, so these are read second and win.
#: Mirrors ``roflo.config._env_overrides``; if that list grows, so must this one.
ENV_BACKEND, ENV_MODEL = "ROFLO_BACKEND", "ROFLO_MODEL"


def configured_model(config_path: str = "roflo.toml",
                     env: Mapping[str, str] | None = None) -> tuple[str, str]:
    """``(backend_kind, model_tag)`` the deployment will actually run.

    Read from configuration rather than remembered, and read the way the runtime
    reads it: roflo's precedence is *flags > env > file*, so a file-only answer is
    the wrong answer. ``ROFLO_MODEL`` pointing at an uncleared checkpoint is
    exactly the silent downgrade this exists to catch.

    An unreadable file yields ``("", "")`` — unknown is not approved — but an
    environment override still counts, because the process would still honour it.
    """
    kind = model = ""
    try:
        with open(config_path, "rb") as handle:
            doc = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        doc = {}
    backend = doc.get("backend")
    if isinstance(backend, dict):
        kind = str(backend.get("kind", ""))
        model = str(backend.get("model", ""))

    environ = os.environ if env is None else env
    return environ.get(ENV_BACKEND) or kind, environ.get(ENV_MODEL) or model


def model_artifact_check(policy, config_path: str = "roflo.toml",
                         env: Mapping[str, str] | None = None) -> Check:
    """Is the exact artifact the deployment will run cleared for paid work?

    Two facts must agree: Policy has cleared a specific artifact by digest, and
    configuration names that same artifact. Either alone is insufficient — a
    clearance nothing runs is useless, and a configured model nobody cleared is
    the defect this check exists to catch.
    """
    blocked = lambda detail: Check(  # noqa: E731 - one shape, four call sites
        "model commercial rights", False, detail,
        "OD-3: commercial use of the exact production artifact",
        category=EXTERNAL_VERIFICATION)

    record = policy.get("governance", "approved_model_artifact", default=None)
    if not isinstance(record, dict):
        return blocked("no artifact cleared for commercial use")
    missing = [f for f in ARTIFACT_EVIDENCE if not str(record.get(f, "")).strip()]
    if missing:
        return blocked(f"clearance is missing evidence: {', '.join(missing)}")
    if not DIGEST_RE.match(str(record["digest"])):
        return blocked(f"clearance carries no content digest ({record['digest']!r})")

    _, configured = configured_model(config_path, env)
    if not configured:
        return blocked("cannot read the configured production model")
    if configured != str(record["tag"]):
        # Configuration must not be able to move production onto weights nobody
        # cleared. Within one family the licence genuinely differs by size.
        return blocked(f"configured {configured!r} is not the cleared "
                       f"{record['tag']!r}")

    return Check("model commercial rights", True,
                 f"{record['tag']} @ {str(record['digest'])[:19]}… under "
                 f"{record['license_id']} (verified {record['verified_on']})",
                 category=EXTERNAL_VERIFICATION)


def first_revenue_readiness(*, policy, ledger, capability, discovery,
                            owner_channel=None,
                            config_path: str = "roflo.toml",
                            env: Mapping[str, str] | None = None) -> Readiness:
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
    # Distinguish "no key" from "a key that must not be used". Both fail
    # closed, but only one of them is a mistake somebody has already made, and
    # reporting them identically is how a provisioned placeholder survives.
    refusal = getattr(owner_channel, "key_refusal", "") if owner_channel else ""
    add(Check("authenticated owner approval", authenticated,
              "Owner Channel active: signed, scoped, single-use, expiring"
              if authenticated else (refusal or
              "no owner signing key configured (SOLVENT_OWNER_KEY)"),
              "" if authenticated else
              "OD-5: provision an owner signing key before real execution",
              category=OWNER_CONFIG))

    # --- things only the owner can decide --------------------------------
    # OD-1 is two facts, and reporting them as one is how a decision stays
    # "unanswered" for want of data the decision never included. The structure
    # is the owner's answer; the identity fields are theirs to supply.
    party = policy.contracting_party()
    add(Check("contracting structure decided", party["decided"],
              party["structure"] or "not recorded",
              "" if party["decided"]
              else "OD-1: does the owner contract personally or through an entity?",
              category=OWNER_CONFIG))
    # Reported per purpose rather than as one list. A trial that never issues
    # an invoice does not need a postal address, and asking for everything up
    # front turns a short owner action into a long one while holding personal
    # data on the chance it might matter. The blocking question is the narrow
    # one: can Solvent tell a client who they are contracting with?
    needed = policy.contracting_shortfall("CLIENT_AGREEMENT")
    later = sorted({f for purpose in ("INVOICE", "PAYMENT_RAIL")
                    for f in policy.contracting_shortfall(purpose)}
                   - set(needed))
    add(Check("contracting identity for a client agreement", not needed,
              ("supplied" if not needed
               else f"{policy.PROVISIONING_REQUIRED}: {', '.join(needed)}"),
              "" if not needed else
              ("OD-1 activation: to name the contracting party the owner must "
               f"supply {', '.join(needed)}; these are never inferred"
               + (f". Not needed yet: {', '.join(later)}" if later else "")),
              category=OWNER_CONFIG))

    # OD-2 is two facts as well: which rail, and whether it can actually move
    # money. Choosing Stripe does not create a webhook secret.
    rail_record = policy.payment_rail()
    rail, signal = rail_record["rail"], rail_record["verification_signal"]
    add(Check("payment rail with a verification signal", bool(rail and signal),
              f"rail={rail or 'none'}, signal={signal or 'none'}",
              "" if rail and signal else
              "OD-2: without a verification signal PAID is unreachable and "
              "profit cannot be computed", category=EXTERNAL_VERIFICATION))
    operational = rail_record["operational_status"] == policy.RAIL_OPERATIONAL
    add(Check("payment rail operational", operational,
              rail_record["operational_status"],
              "" if operational else
              "OD-2 activation: the chosen rail has no credentials or webhook "
              "secret, so no payment can be verified yet",
              category=EXTERNAL_VERIFICATION))

    add(model_artifact_check(policy, config_path, env))

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
