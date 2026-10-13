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

    # What DeskPilot's own work costs. Zero is not a cheap deployment, it is
    # an unconfigured one -- and an unconfigured one disables a control: the
    # Governor's margin floor compares revenue against cost, so with every
    # cost at zero every job shows a 100% margin and the floor can never
    # reject anything. The check that looks like it is protecting the owner
    # is protecting nothing until these are recorded.
    cost_keys = ("compute_cents_per_cpu_hour", "platform_fee_fraction",
                 "payment_fee_fraction", "payment_fee_flat_cents")
    recorded = {k: policy.get("commercial", k, default=0) for k in cost_keys}
    any_cost = any(float(v or 0) > 0 for v in recorded.values())
    add(Check("operating costs recorded", any_cost,
              ("costs recorded: "
               + ", ".join(f"{k}={v}" for k, v in recorded.items() if float(v or 0) > 0))
              if any_cost else
              "every operating cost is zero, so the Governor's margin floor "
              "compares revenue against nothing and cannot reject a job",
              "" if any_cost else
              "OD-16: record the platform cut, the payment processor's fee and "
              "the compute cost per CPU-hour under `commercial` in policy",
              category=OWNER_CONFIG))

    minimum_fee = int(policy.get("commercial", "minimum_project_fee_cents",
                                 default=0))
    add(Check("a minimum project fee", minimum_fee > 0,
              f"minimum project fee {minimum_fee} cents" if minimum_fee else
              "no minimum project fee is set, so a job worth almost nothing "
              "is still worth taking as far as the Governor is concerned",
              "" if minimum_fee else
              "OD-17: set `commercial.minimum_project_fee_cents` to the "
              "smallest job worth the overhead of taking",
              category=OWNER_CONFIG))

    margin = float(policy.get("commercial", "minimum_margin_fraction",
                              default=0) or 0)
    add(Check("a minimum margin", margin > 0,
              f"minimum margin {margin:.0%}" if margin > 0 else
              "no minimum margin is set, so a job clearing its cost by one "
              "cent counts as worth taking",
              "" if margin > 0 else
              "set `commercial.minimum_margin_fraction` to the smallest "
              "share of revenue that must survive as profit",
              category=OWNER_CONFIG))

    rates = dict(policy.get("commercial", "market_hourly_cents",
                            default={}) or {})
    citations = dict(policy.get("commercial", "market_rate_citations",
                                default={}) or {})
    uncited = sorted(name for name in rates
                     if not str(citations.get(name, "")).strip())
    cited = bool(rates) and not uncited
    add(Check("cited market rates", cited,
              f"{len(rates)} rate(s), each with a citation" if cited else
              (f"{len(uncited)} rate(s) recorded with no citation: "
               + ", ".join(uncited[:3])) if uncited else
              "no market rates are recorded, so a fixed price can only be "
              "taken from what the client already offered",
              "" if cited else
              "OD-7: load public wage data per capability through the "
              "pricing reference with its citation, and approve the source "
              "in Policy first",
              category=OWNER_CONFIG))

    # Whether a second opinion can be produced at all on this host. Checked
    # here as well as per job, because the per-job check arrives when a
    # client is already waiting: the owner should know before they go
    # looking for four-figure work that this installation cannot verify it.
    from . import independent

    review = independent.available(extension="xlsx")
    add(Check("independent review is producible", review.any_usable,
              review.summary(),
              "" if review.any_usable else
              "OD-18: install the independent readers "
              "(`pip install .[verification]`) or configure the owner signing "
              "key, or every job above the high-value threshold is declined",
              category=OWNER_CONFIG))

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

    # The control centre is the one part of Solvent that deliberately listens,
    # so it is the one part that can be exposed by accident. Unrecorded counts
    # as unsafe: silence is not default-deny.
    network = policy.network_posture()
    firewall_ok = network["default_deny"] is True
    add(Check("host network posture", firewall_ok,
              {True: (f"inbound default-deny recorded"
                      + (f", proxy on {network['proxy_port']}"
                         if network["proxy_port"] else "")),
               False: "the host firewall does not default to deny",
               None: "nobody has recorded the host firewall posture"}[
                  network["default_deny"]],
              "" if firewall_ok else
              "run deploy/firewall.sh --apply, then record it with "
              "`solvent setup firewall --default-deny`",
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


#: Checks a supervised, human-relayed trial genuinely does not use.
#:
#: Not a discount. Each one is here because the trial has no way to reach the
#: thing it guards: nothing is sent, so egress stays fail-closed; no payment is
#: collected through Solvent, so the rail's credentials are not exercised. A
#: check that the trial *does* use is not on this list, whatever it would cost
#: the owner to satisfy.
_NOT_USED_BY_A_SUPERVISED_TRIAL = (
    "payment rail operational",
    "real revenue to date",
    "external execution posture",
    # A supervised trial quotes nobody and collects nothing: it produces a
    # verified file and a drafted message for the owner to read. Both of
    # these decide what a client is *charged* and whether the margin the
    # Governor checks means anything, so neither is in a trial's way -- and
    # both stop a real job, where a price goes to a client and money is
    # supposed to arrive.
    "operating costs recorded",
    "a minimum project fee",
    # A supervised trial produces one verified file for the owner to read,
    # at whatever value they choose to trial at. It does not need the
    # machinery for four-figure work to exist yet -- but a real job above
    # the threshold does, and this stops that one.
    "independent review is producible",
    # The host firewall guards what can reach this machine. A supervised trial
    # run from the terminal sends nothing, collects nothing and listens on
    # nothing, so it does not depend on it.
    #
    # This is emphatically *not* an excuse for the control centre, which does
    # listen. That has its own gate: `solvent web-exposure` reports an
    # unrecorded posture as unsafe, and the service refuses to start without a
    # password. Two different questions, deliberately not answered by one
    # verdict — conflating them would either block a terminal-only trial on a
    # firewall it never touches, or let an exposed website ride in on a trial's
    # readiness.
    "host network posture",
)


def assert_may_attempt_supervised_trial(report: Readiness) -> None:
    """Fail closed unless a supervised, human-relayed first trial may proceed.

    The difference from :func:`assert_may_attempt_first_real_job` is what the
    trial actually does. It accepts one hand-fed job, executes it, verifies it
    independently, and stops with a deliverable and a drafted message for the
    owner to relay. It sends nothing, so external execution stays fail-closed.
    It collects nothing, so the payment rail's credentials are never used — and
    the job stays at AWAITING_PAYMENT, which is the truth rather than an
    oversight.

    Requiring a webhook signing secret before Solvent may *prepare* a verified
    file would be blocking on something the work never touches. Everything the
    trial does use is still required: the owner's signing key, their contracting
    identity, a promoted capability, an approved source, a cleared model.
    """
    outstanding = [item for item in report.blocking
                   if not any(name in item for name in _blocking_names(report))]
    if outstanding:
        raise FailClosed(
            "not ready for a supervised first trial:\n  - "
            + "\n  - ".join(outstanding))


def _blocking_names(report: Readiness) -> list[str]:
    """The owner-decision text of the checks a supervised trial does not use."""
    return [check.owner_decision for check in report.checks
            if check.name in _NOT_USED_BY_A_SUPERVISED_TRIAL
            and check.owner_decision]


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
