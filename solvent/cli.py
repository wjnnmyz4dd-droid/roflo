"""Command line interface.

``solvent doctor`` is the important one: it reports what is *measured* rather than
what is claimed, so the owner can tell an enforced control from a documented one.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sqlite3
import sys

from . import egress
from . import harness
from . import runtime as rt
from . import services
from .branding import ENGINE, PRODUCT
from .errors import FailClosed
from .harness import OWNER, Solvent, run_acquisition_cycle, run_first_job
from .store import TABLE_OWNER
from .types import OperatingMode, fmt


def cmd_doctor(args: argparse.Namespace) -> int:
    """Report measured enforcement, not intentions.

    This opened ``Solvent()`` with no path for a long time, which meant an
    in-memory database: every run created an empty DeskPilot and then reported
    its governance and evidence as though they were the deployment's. The
    numbers looked right because a fresh database seeds the same safe defaults,
    which is exactly what makes it the wrong kind of mistake -- a report whose
    subject is not the thing anybody wanted reported.

    So the database is named like it is for every other operational command,
    through the same ``--db`` and the same opener.
    """
    # ``:memory:`` is sqlite's own sentinel rather than a path, and asking for
    # it is a deliberate request for a throwaway -- which is a different thing
    # from naming a database that is not there.
    if args.db != ":memory:" and not pathlib.Path(args.db).exists():
        # Refusing rather than letting sqlite create one. Every other command
        # would make an empty database here and carry on, and for most of them
        # that only shows an empty business. For this command it would print
        # measured enforcement for a database that did not exist a moment ago,
        # which is the defect above wearing a different hat -- and a mistyped
        # path is the likeliest way to arrive here.
        print(f"no database at {args.db}", file=sys.stderr)
        print("doctor reports what a deployment is actually enforcing, so it "
              "will not create one to report on. Check the path, or run "
              "`solvent setup capability --db <path>` to provision it.",
              file=sys.stderr)
        return 1
    solvent = _open(args)
    if solvent is None:
        return 1
    print(f"{PRODUCT} readiness")
    print("=" * 60)

    probe = egress.probe()
    print("\nEgress (in-process layer)")
    print(f"  guard installed        : {probe['guard_installed']}")
    print(f"  socket.connect         : {probe['socket.connect']}")
    print(f"  dns resolution         : {probe['dns']}")
    print("  NOTE: a ctypes call to libc bypasses this layer. The OS layer")
    print("        (network namespace) is verified by tools/verify_egress.py.")

    mode = solvent.policy.operating_mode
    sim = solvent.policy.get("egress", "simulation_only", default=True)
    allow = solvent.policy.get("egress", "allowlist", default=[])
    print("\nGovernance")
    print(f"  operating_mode         : {mode.value}")
    print(f"  policy version         : {solvent.policy.version}")
    print(f"  margin floor           : {solvent.policy.get('financial','margin_floor')}")
    print(f"  egress simulation_only : {sim}")
    print(f"  egress allowlist       : {len(allow)} destination(s)")

    ok, detail = solvent.audit.verify_chain()
    print("\nEvidence")
    print(f"  audit chain            : {'intact' if ok else 'BROKEN'} ({detail})")
    print(f"  real (non-simulated)   : {solvent.ledger.real_revenue_cents()} cents")

    print("\nAuthorities and the tables they own")
    by_owner: dict[str, list[str]] = {}
    for table, owner in sorted(TABLE_OWNER.items()):
        by_owner.setdefault(owner, []).append(table)
    for owner, tables in sorted(by_owner.items()):
        print(f"  {owner:<13}: {', '.join(tables)}")

    blocked = sim or not allow
    print("\nExternal execution:", "FAIL-CLOSED (nothing can leave)" if blocked
          else "ENABLED for allowlisted destinations")
    return 0


def cmd_cycle(args: argparse.Namespace) -> int:
    """Take one opportunity from an approved source through to reconciled profit.

    **Owner path, and deliberately not an automatic one.** Two decisions bound
    this command and neither has a default that says yes:

    ``--accept-quote``
        Your answer when the Governor asks for approval. Without it the job
        stops in AWAITING_OWNER_APPROVAL, which is where it belongs until you
        decide.

    ``--simulate-payment``
        Injects a simulated payment so the back half of the cycle can be
        exercised. The Ledger marks it simulated and it never counts as
        revenue. Nothing here can collect real money.

    ``--brief`` is the checklist you agreed with the client, as JSON: a list of
    ``{"id", "check", "params"}`` objects. It is required, because a posting's
    `needs` name a service and its body is prose -- neither is terms, and the
    deliverable is recomputed against the checklist rather than against
    anything Solvent inferred for itself.
    """
    import json as _json

    from .harness import CYCLE_STAGES, run_business_cycle
    from .types import Criticality, Requirement, RequirementSource

    solvent = _owner_solvent(args)
    posting = None
    if args.posting:
        try:
            posting = _json.loads(
                pathlib.Path(args.posting).read_text(encoding="utf-8"))
        except (OSError, ValueError) as problem:
            print(f"refused: the opportunity could not be read: {problem}")
            return 1
        if not isinstance(posting, dict) or not posting.get("ref"):
            print('refused: --posting must be a JSON object with at least '
                  '{"ref", "title", "quoted_cents", "needs", "project_state"}')
            return 1
    try:
        agreed = _json.loads(pathlib.Path(args.brief).read_text(encoding="utf-8"))
    except (OSError, ValueError) as problem:
        print(f"refused: the agreed checklist could not be read: {problem}")
        return 1
    if not isinstance(agreed, list) or not agreed:
        print("refused: --brief must be a non-empty JSON list of agreed terms")
        return 1

    requirements = []
    for index, term in enumerate(agreed, 1):
        if not isinstance(term, dict) or not term.get("check"):
            print(f"refused: term {index} names no check. Each term needs "
                  f'{{"check": "...", "params": {{...}}}}')
            return 1
        requirements.append(Requirement(
            id=str(term.get("id") or f"R{index}"),
            text=str(term.get("text") or term["check"]),
            source=RequirementSource.CLIENT_CONFIRMED_STRUCTURED,
            acceptance=str(term.get("acceptance") or term["check"]),
            check=str(term["check"]), params=dict(term.get("params") or {}),
            criticality=Criticality.MANDATORY))

    cycle = run_business_cycle(
        solvent, source_name=args.source, source_file=args.file,
        workdir=args.workdir, capability=args.capability, posting=posting,
        owner_identity=args.owner, agreed_requirements=requirements,
        owner_accepts_quote=args.accept_quote,
        simulate_payment=args.simulate_payment)

    reached = set(cycle.stages)
    for stage in CYCLE_STAGES:
        if stage in reached:
            print(f"  [done]    {stage}")
        elif stage == cycle.stopped_at:
            print(f"  [STOPPED] {stage} — {cycle.why_stopped}")
        else:
            print(f"  [ ]       {stage}")

    print(f"\nopportunity : {cycle.opportunity_ref or '(none)'}")
    print(f"job         : {cycle.job_id or '(none)'}")
    print(f"verdict     : {cycle.verdict or '-'}"
          + (f" / governor {cycle.governor_verdict}" if cycle.governor_verdict
             else ""))
    if cycle.owner_approval_required:
        print(f"approval    : required; "
              f"{'given' if cycle.owner_approval_given else 'NOT GIVEN'}")
    if cycle.grant_id:
        print(f"budget      : {fmt(cycle.authorised_cents)} under "
              f"{cycle.grant_id}")
    if cycle.delivered:
        print(f"delivered   : {cycle.deliverable_path}")
    if cycle.invoiced_cents:
        print(f"invoiced    : {fmt(cycle.invoiced_cents)}")
    if cycle.collected_cents:
        print(f"collected   : {fmt(cycle.collected_cents)} "
              f"({'SIMULATED' if cycle.payment_simulated else 'real'})")
        print(f"profit      : {fmt(cycle.actual_profit_cents)}")
    print(f"real revenue: {fmt(cycle.real_revenue_cents)} "
          "— simulated money is never counted here")
    print(f"final state : {cycle.final_state or '-'}")
    print(f"audit chain : {'verifies' if cycle.audit_chain_ok else 'BROKEN'}")

    if not cycle.completed:
        print(f"\nThe cycle stopped at {cycle.stopped_at}. Nothing beyond that "
              "stage happened.")
        return 1
    print("\nThe cycle completed in simulation. No real money moved, no client "
          "was contacted, and nothing left this machine.")
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    """Run the complete-job harness with fixtures."""
    report = run_first_job(state=args.state, quote_dollars=args.quote)
    print(f"job {report.job_id}\n")
    print("steps")
    for step in report.steps:
        print(f"  - {step}")
    print("\ngates exercised")
    for gate in report.gates_exercised:
        print(f"  - {gate}")
    print(f"\nfinal state : {report.final_state}")
    print(f"audit chain : {report.audit_chain}")
    print(f"profit      : {report.profit}")
    print(f"real revenue: {report.real_revenue}")
    for warning in report.warnings:
        print(f"\n!! {warning}")
    return 0


def cmd_acquire(args: argparse.Namespace) -> int:
    """Run one acquisition cycle over a fixture board."""
    report = run_acquisition_cycle()
    print(f"discovered {report.discovered} opportunit"
          f"{'y' if report.discovered == 1 else 'ies'}\n")
    print("triaged out (cheap checks, no estimate run)")
    for line in report.triaged_out:
        print(f"  - {line}")
    print("\nqualified")
    for line in report.decisions:
        print(f"  - {line}")
    print("\nranked (best first)")
    for line in report.ranked:
        print(f"  - {line}")
    print(f"\nselected : {', '.join(report.selected) or '(none)'}")
    for line in report.deferred:
        print(f"deferred : {line}")
    metrics = report.metrics
    print(f"\n{metrics['headline']}")
    print(f"refusals by reason: {metrics['rejected_by_reason']}")
    for warning in report.warnings:
        print(f"\n!! {warning}")
    return 0


def cmd_metrics(args: argparse.Namespace) -> int:
    """Print business metrics for a fresh instance (or a database)."""
    solvent = Solvent(args.db)
    print(json.dumps(solvent.metrics().to_dict(), indent=2))
    return 0


# --------------------------------------------------------------- owner setup
#
# Everything the owner has to configure, as commands rather than as Python they
# would have to write. None of these decide anything: each one calls the
# authority that already owns the decision, with the owner's identity, and
# reports what that authority said. There is no second readiness here and no
# owner-profile store — `setup check` reads the existing readiness projection.
#
# No command in this section accepts a secret as an argument. A signing key or a
# webhook secret typed on a command line lands in shell history and in the
# process list, where any other user on the machine can read it. Those two
# values reach Solvent only through the environment file, and these commands
# report on their presence without ever displaying them.

def _owner_solvent(args) -> "Solvent":
    return Solvent(args.db)


def cmd_setup_contracting(args: argparse.Namespace) -> int:
    """Record who the owner contracts as. Values come from the owner, never here."""
    solvent = _owner_solvent(args)
    fields = {name: value for name, value in
              (("legal_name", args.legal_name), ("email", args.email),
               ("address", args.address)) if value}
    if args.tax_reference_provisioned:
        # Recorded as present. Solvent never stores or prints the number, so
        # this flag takes no value on purpose.
        fields["tax_reference"] = "provisioned"
    try:
        solvent.policy.record_contracting_structure(
            owner_identity=args.owner, structure=args.structure,
            reason=args.reason, **fields)
    except FailClosed as exc:
        print(f"refused: {exc}")
        return 1
    party = solvent.policy.contracting_party()
    print(f"contracting structure: {party['structure']}")
    for purpose in sorted(solvent.policy.CONTRACTING_PURPOSES):
        allowed, why = solvent.policy.may_contract_for(purpose)
        print(f"  [{'OK ' if allowed else 'NEED'}] {purpose}: "
              f"{'complete' if allowed else why.split(': ', 1)[-1]}")
    return 0


def cmd_marketplaces(args: argparse.Namespace) -> int:
    """Print the six-platform interface: what is permitted where.

    Seven activities per platform, kept apart. Permission to manage an
    account is not permission to search jobs, and neither is permission
    to bid -- collapsing them into one word per platform is how a
    catalogue comes to claim an integration nobody has.
    """
    from . import sourcecatalog

    print(sourcecatalog.marketplace_summary(), end="")
    return 0


def cmd_setup_crypto(args: argparse.Namespace) -> int:
    """Record where crypto payment should arrive. The field starts blank.

    This stores an address and nothing else. There is no key here, no
    seed phrase, no credential: DeskPilot cannot spend from the address
    it records and has no code path that could learn how. The address
    is validated before it is stored and refused if it cannot be fully
    checked -- a wrong address on a chain is money gone, and the cost
    of refusing a good one is a retype.
    """
    from .payments import CRYPTO_NETWORKS

    solvent = _owner_solvent(args)
    if args.show or not args.address:
        receipt = solvent.policy.crypto_receipt()
        problem = receipt.problem()
        print(f"network : {receipt.network or '(not set)'}")
        print(f"asset   : {receipt.asset or '(not set)'}")
        print(f"address : {receipt.address or '(not set)'}")
        print(f"confirm : {receipt.minimum_confirmations or '(not set)'}")
        print(f"status  : {'configured' if not problem else problem}")
        print("networks: " + ", ".join(
            f"{n} ({'/'.join(a)})" for n, a in sorted(CRYPTO_NETWORKS.items())))
        conversion = solvent.policy.conversion_policy()
        print(f"convert : {conversion.state}"
              + (f" via {conversion.provider}" if conversion.provider else ""))
        return 0
    try:
        solvent.policy.record_crypto_receipt(
            owner_identity=args.owner, network=args.network,
            asset=args.asset, address=args.address,
            minimum_confirmations=args.minimum_confirmations,
            reason=args.reason)
    except FailClosed as exc:
        print(f"refused: {exc}")
        return 1
    receipt = solvent.policy.crypto_receipt()
    print(f"recorded {receipt.asset} on {receipt.network}, settled after "
          f"{receipt.minimum_confirmations} confirmation(s)")
    print("DeskPilot cannot spend from this address and holds no key for it.")
    return 0


def cmd_setup_commercial(args: argparse.Namespace) -> int:
    """Record the four terms that decide what work is worth taking.

    These were the last four owner decisions with no command behind
    them, which is why they were still unset on an installation whose
    owner had followed the manual to the letter. Until all four are
    recorded the Job Finder will not recommend pursuing anything: it can
    still assess, rank and explain, but it cannot tell work worth taking
    from work that merely looks like it.

    A market rate must come with its citation. Nothing here invents a
    number -- every value is the owner's, and a rate the owner cannot
    cite is one this refuses to store.
    """
    solvent = _owner_solvent(args)
    terms: dict = {}
    if args.minimum_fee_cents is not None:
        if args.minimum_fee_cents <= 0:
            print("refused: a minimum project fee of zero is not a floor")
            return 1
        terms["minimum_project_fee_cents"] = int(args.minimum_fee_cents)
    if args.compute_cents_per_cpu_hour is not None:
        if args.compute_cents_per_cpu_hour <= 0:
            print("refused: compute costed at zero flatters every margin; "
                  "record what running this machine actually costs")
            return 1
        terms["compute_cents_per_cpu_hour"] = int(args.compute_cents_per_cpu_hour)
    if args.minimum_margin_fraction is not None:
        if not 0 < args.minimum_margin_fraction < 1:
            print("refused: a minimum margin is a fraction of revenue "
                  "between 0 and 1")
            return 1
        terms["minimum_margin_fraction"] = float(args.minimum_margin_fraction)
    if args.market_rate:
        rates = dict(solvent.policy.get("commercial", "market_hourly_cents",
                                        default={}) or {})
        citations = dict(solvent.policy.get("commercial",
                                            "market_rate_citations",
                                            default={}) or {})
        for entry in args.market_rate:
            parts = entry.split("=", 2)
            if len(parts) != 3 or not parts[2].strip():
                print(f"refused: {entry!r} is not "
                      "CAPABILITY=CENTS=CITATION. A rate with no citation "
                      "cannot be told apart from one somebody invented")
                return 1
            capability, cents, citation = parts
            try:
                value = int(cents)
            except ValueError:
                print(f"refused: {cents!r} is not a whole number of cents")
                return 1
            if value <= 0:
                print(f"refused: a market rate of {value} is not a rate")
                return 1
            rates[capability.strip()] = value
            citations[capability.strip()] = citation.strip()
        terms["market_hourly_cents"] = rates
        terms["market_rate_citations"] = citations

    # ---- what a delivered job costs ---------------------------------
    # The revenue model reads all five of these and treats an unset one
    # as unknown rather than as zero, which is right -- but there was
    # no command to set any of them, so every net figure the model
    # produced was the gross figure wearing a different label, and the
    # only remedy documented anywhere was editing the policy by hand.
    for name, value, what in (
            ("platform_fee_fraction", args.platform_fee_fraction,
             "the marketplace's cut as a fraction, e.g. 0.1 for 10%"),
            ("payment_fee_fraction", args.payment_fee_fraction,
             "the payment provider's percentage, e.g. 0.029"),
    ):
        if value is None:
            continue
        if not 0 <= value < 1:
            print(f"refused: {name} of {value} is not a fraction below "
                  f"one ({what})")
            return 1
        terms[name] = float(value)

    for name, value in (
            ("platform_fee_minimum_cents", args.platform_fee_minimum_cents),
            ("payment_fee_flat_cents", args.payment_fee_flat_cents),
            ("fixed_monthly_overhead_cents",
             args.fixed_monthly_overhead_cents),
            ("other_direct_cost_per_job_cents",
             args.other_direct_cost_per_job_cents),
    ):
        if value is None:
            continue
        if value < 0:
            print(f"refused: {name} cannot be negative")
            return 1
        terms[name] = int(value)

    if args.hourly_rate_cents is not None:
        if args.hourly_rate_cents <= 0:
            print("refused: an hourly rate of zero is not a rate")
            return 1
        terms["hourly_rate_cents"] = int(args.hourly_rate_cents)

    if args.exchange_rate:
        rates = dict(solvent.policy.get("commercial", "exchange_rate_cents",
                                        default={}) or {})
        citations = dict(solvent.policy.get(
            "commercial", "exchange_rate_citations", default={}) or {})
        for item in args.exchange_rate:
            parts = item.split("=", 2)
            if len(parts) != 3 or not parts[2].strip():
                print(f"refused: {item!r} is not CURRENCY=CENTS=CITATION. "
                      "An exchange rate multiplies every price that comes "
                      "off that platform; one with no source cannot be "
                      "told apart from one somebody invented")
                return 1
            code, cents, citation = parts
            try:
                value = float(cents)
            except ValueError:
                print(f"refused: {cents!r} is not a number of cents")
                return 1
            if value <= 0:
                print(f"refused: an exchange rate of {value} is not a rate")
                return 1
            code = code.strip().upper()
            if code == "USD":
                print("refused: USD is the owner's own currency and needs "
                      "no rate; recording one would mean every dollar "
                      "budget was multiplied by it")
                return 1
            rates[code] = value
            citations[code] = citation.strip()
        terms["exchange_rate_cents"] = rates
        terms["exchange_rate_citations"] = citations

    if not terms:
        print("nothing to record; pass at least one term")
        return 1
    solvent.policy.amend({"commercial": terms}, args.owner, args.reason)

    from .finder import Finder

    finder = Finder(solvent.discovery, solvent.policy, solvent.capability,
                    solvent.orchestrator, solvent.store)
    configured, gaps = finder.commercially_configured()
    print("commercial terms recorded")
    unrecorded = [name for name in (
        "platform_fee_fraction", "platform_fee_minimum_cents",
        "payment_fee_fraction", "payment_fee_flat_cents",
        "fixed_monthly_overhead_cents")
        if not solvent.policy.get("commercial", name, default=0)]
    if configured:
        print("  [OK ] all four are set; the Finder may recommend work")
    for gap in gaps:
        print(f"  [NEED] {gap}")
    if unrecorded:
        print("  [NOTE] unrecorded, and unrecorded is not zero: "
              + ", ".join(unrecorded))
        print("         every net and margin figure stays provisional "
              "until these are set")
    if configured:
        return 0
    return 0


def cmd_setup_probation(args: argparse.Namespace) -> int:
    """Grant or end probation for one capability.

    Probation is the only way out of the zero-history deadlock: the
    Finder will not bid on work it has never done, and it can only
    have done work it was allowed to bid on. A grant lets one
    capability be *recommended* to the owner, bounded, from benchmark
    timings -- and a probationary bid is never submittable by
    DeskPilot.
    """
    solvent = _owner_solvent(args)
    if args.revoke:
        try:
            solvent.policy.revoke_probation(
                owner_identity=args.owner, capability=args.revoke,
                reason=args.reason)
        except FailClosed as refusal:
            print(f"refused: {refusal}")
            return 1
        print(f"probation ended for {args.revoke}")
        return 0

    if not args.capability:
        grants = solvent.policy.get("probation", "capabilities",
                                    default={}) or {}
        if not grants:
            print("no capability is on probation")
            return 0
        for name, terms in sorted(grants.items()):
            if terms.get("revoked"):
                print(f"  {name}: revoked")
                continue
            print(f"  {name}: up to {terms.get('max_jobs', 0)} job(s), "
                  f"{fmt(int(terms.get('max_value_cents', 0)))} each, "
                  f"at least {terms.get('min_deadline_hours', 0)}h to "
                  "deliver")
        return 0

    try:
        solvent.policy.grant_probation(
            owner_identity=args.owner, capability=args.capability,
            max_jobs=args.max_jobs, max_value_cents=args.max_value_cents,
            min_deadline_hours=args.min_deadline_hours, reason=args.reason)
    except FailClosed as refusal:
        print(f"refused: {refusal}")
        return 1
    print(f"probation granted for {args.capability}")
    print(f"  up to {args.max_jobs} job(s) at "
          f"{fmt(args.max_value_cents)} each, at least "
          f"{args.min_deadline_hours:g}h to deliver")
    print("  DeskPilot will recommend these to you and may not send them")
    return 0


def cmd_setup_standing(args: argparse.Namespace) -> int:
    """Record what DeskPilot may accept without asking the owner.

    The owner wants no manual job management during normal operation,
    which needs something able to say yes on their behalf. This is
    that, and it is deliberately narrow: one capability, a ceiling in
    money, a floor in notice, a count. Everything outside goes back
    to them.

    It cannot authorise a bid. ``solvent.acquisition`` refuses the
    competitive channel whatever a grant says, because no platform
    read so far permits a machine to submit proposals.
    """
    from . import acquisition as acq
    from .harness import CAPABILITIES

    solvent = _owner_solvent(args)
    capability = (args.capability or "").strip()
    if capability not in CAPABILITIES:
        print(f"refused: no such certified skill: {capability}. The five "
              f"are {', '.join(sorted(CAPABILITIES))}")
        return 1

    grants = dict(solvent.policy.get("acquisition", "standing",
                                     default={}) or {})
    if args.revoke:
        # Zeroed and marked, never deleted: the policy merge is a deep
        # merge, so removing a key by writing an empty dict is a silent
        # no-op. That trap cost a sweep once already.
        grants[capability] = {"max_value_cents": 0, "min_deadline_hours": 0,
                              "max_jobs": 0, "revoked": True}
        solvent.policy.amend({"acquisition": {"standing": grants}},
                             args.owner, args.reason)
        print(f"standing authority for {capability} revoked; every job in "
              "that skill now needs the owner")
        return 0

    if args.max_jobs <= 0:
        print("refused: a standing grant of zero jobs is not a grant; to "
              "end one use --revoke, which says so in the record")
        return 1
    if args.max_value_cents <= 0:
        print("refused: a standing yes with no value ceiling is a blank "
              "cheque; set --max-value-cents")
        return 1
    grants[capability] = {
        "max_value_cents": int(args.max_value_cents),
        "min_deadline_hours": float(args.min_deadline_hours),
        "max_jobs": int(args.max_jobs),
        "channels": list(acq.OPEN_CHANNELS),
    }
    solvent.policy.amend({"acquisition": {"standing": grants}},
                         args.owner, args.reason)
    print(f"standing authority recorded for {capability}")
    print(f"  [OK ] up to {args.max_jobs} job(s), each at most "
          f"${args.max_value_cents / 100:,.2f}, with at least "
          f"{args.min_deadline_hours:.0f}h notice")
    print(f"  [OK ] channels: {', '.join(acq.OPEN_CHANNELS)}")
    print(f"  [NOTE] {acq.BID_FREEZE}")
    return 0


def cmd_setup_capacity(args: argparse.Namespace) -> int:
    """Record how much work DeskPilot may owe at one time.

    The one setting with no command behind it, which mattered more
    than the others: `max_owed_seconds` starts at zero, zero means
    "take nothing on", and the capacity gate therefore refuses every
    job on a fresh install. An owner who followed the manual to the
    letter ended up with a business that would not take work and no
    documented way to change that.

    Every value is the owner's and none is inferred from the machine.
    Deciding a budget by looking at the hardware is how a shared VPS
    becomes an unshared one, and this one is shared with MetaTrader 5.
    """
    from .harness import CAPABILITIES
    from .types import JOBS_PER_AGENT_CEILING

    #: One worker per certified skill, and no more. Not a hardware
    #: judgement -- the five are what the shop can do.
    CERTIFIED_SKILLS = tuple(sorted(CAPABILITIES))
    MAX_WORKERS = len(CERTIFIED_SKILLS)

    solvent = _owner_solvent(args)
    budget: dict = {}

    if args.max_owed_seconds is not None:
        if args.max_owed_seconds <= 0:
            print("refused: zero seconds owed means DeskPilot takes nothing "
                  "on. That is a legitimate setting, but set it with --halt "
                  "so the reason is recorded rather than looking like an "
                  "unconfigured install")
            return 1
        budget["max_owed_seconds"] = float(args.max_owed_seconds)

    # The job ceiling and the worker count are different numbers, and
    # for a while this treated them as the same one. The ceiling is
    # how many jobs a single skill pipeline may hold -- three by
    # default, four at most. The worker count is how many jobs run at
    # once across the whole shop, and the owner's operating model has
    # five skills with a worker each, so capping it at four refused
    # the architecture the rest of the system was built for.
    if args.max_concurrent_jobs is not None:
        if args.max_concurrent_jobs < 1:
            print(f"refused: max_concurrent_jobs of "
                  f"{args.max_concurrent_jobs} is not a limit")
            return 1
        if args.max_concurrent_jobs > JOBS_PER_AGENT_CEILING:
            print(f"refused: max_concurrent_jobs of "
                  f"{args.max_concurrent_jobs} is above the "
                  f"{JOBS_PER_AGENT_CEILING} a skill pipeline may hold. "
                  "Three is the default and four is the most that can be "
                  "configured; past that is a decision about the "
                  "architecture, not about a number")
            return 1
        budget["max_concurrent_jobs"] = int(args.max_concurrent_jobs)

    for name, value in (("max_execution_workers", args.max_execution_workers),
                        ("max_verification_workers",
                         args.max_verification_workers)):
        if value is None:
            continue
        if value < 1:
            print(f"refused: {name} of {value} is not a limit")
            return 1
        if value > MAX_WORKERS:
            print(f"refused: {name} of {value} is above {MAX_WORKERS}. "
                  "The model is one worker per certified skill, and there "
                  "are five; more workers than skills is concurrency "
                  "nobody asked for on a machine shared with "
                  "MetaTrader 5")
            return 1
        budget[name] = int(value)

    if args.skill:
        unknown = [s for s in args.skill if s not in CERTIFIED_SKILLS]
        if unknown:
            print("refused: no such certified skill: "
                  + ", ".join(sorted(unknown))
                  + ". The five are " + ", ".join(CERTIFIED_SKILLS))
            return 1
        budget["skills"] = sorted(set(args.skill))

    if args.pipeline_target is not None:
        if not 0 < args.pipeline_target <= JOBS_PER_AGENT_CEILING:
            print(f"refused: a pipeline target of {args.pipeline_target} is "
                  f"outside 1 to {JOBS_PER_AGENT_CEILING}. A target nothing "
                  "can reach is not a target")
            return 1
        budget["pipeline_target"] = int(args.pipeline_target)

    if args.replenish_at is not None:
        if args.replenish_at < 0:
            print("refused: a negative top-up line is not a line")
            return 1
        budget["replenish_at"] = int(args.replenish_at)

    if args.cpu_share is not None:
        if not 0 < args.cpu_share <= 1:
            print("refused: the CPU share is a fraction above zero and at "
                  "most one. This machine also runs MetaTrader 5 and the "
                  "whole of it is not DeskPilot's to take")
            return 1
        budget["cpu_share"] = float(args.cpu_share)

    if args.max_open_bids is not None:
        if args.max_open_bids < 0:
            print("refused: a negative bid limit is not a limit")
            return 1
        budget["max_open_bids"] = int(args.max_open_bids)

    if args.revision_headroom_fraction is not None:
        if args.revision_headroom_fraction < 0:
            print("refused: revision headroom cannot be negative")
            return 1
        budget["revision_headroom_fraction"] = float(
            args.revision_headroom_fraction)

    if args.min_free_disk_bytes is not None:
        if args.min_free_disk_bytes < 0:
            print("refused: a negative disk floor is not a floor")
            return 1
        budget["min_free_disk_bytes"] = int(args.min_free_disk_bytes)

    if args.deadline_safety_factor is not None:
        if args.deadline_safety_factor < 1:
            print("refused: a safety factor below one promises delivery "
                  "faster than the work has ever been measured taking")
            return 1
        budget["deadline_safety_factor"] = float(args.deadline_safety_factor)

    if args.halt:
        budget["halted"] = True
    elif args.resume:
        budget["halted"] = False

    if not budget:
        print("nothing to record; pass at least one limit")
        return 1
    solvent.policy.amend({"capacity": budget}, args.owner, args.reason)

    current = solvent.capacity.budget()
    print("capacity budget recorded")
    problem = current.problem()
    if problem:
        print(f"  [NEED] {problem}")
        return 0
    print(f"  [OK ] {current.max_owed_seconds:.0f}s owed per pipeline, "
          f"{current.max_concurrent_jobs} job(s) per skill in flight, "
          f"{current.workers('EXECUTE')} execution worker(s), "
          f"{current.cpu_share:.0%} of a machine shared with MetaTrader 5")
    pipelines = solvent.capacity.pipelines()
    if pipelines:
        print("  [OK ] pipelines: " + ", ".join(
            f"{name} {row['committed']}/{row['target']}"
            for name, row in sorted(pipelines.items())))
    else:
        print("  [NEED] no skill pipelines are configured; pass --skill "
              "once per certified skill so the Finder knows what to "
              "replenish")
    if solvent.policy.get("capacity", "halted", default=False):
        print("  [NOTE] new work is halted; pass --resume to allow it")
    return 0


def cmd_setup_work_source(args: argparse.Namespace) -> int:
    """Register and approve the owner-entered work source for the first trial."""
    from .discovery import Compliance, ManualSource, Readiness

    solvent = _owner_solvent(args)
    try:
        solvent.discovery.register_source(
            ManualSource(args.name, []), owner_identity=args.owner,
            readiness=Readiness.PERMITTED_AUTOMATION,
            compliance=Compliance.PERMITTED,
            determination=args.determination)
        approved = set(solvent.policy.get("discovery", "approved_sources",
                                          default=[]) or [])
        approved.add(args.name)
        solvent.policy.amend({"discovery": {"approved_sources": sorted(approved)}},
                             args.owner, f"OD-11: approve {args.name}")
    except FailClosed as exc:
        print(f"refused: {exc}")
        return 1
    print(f"work source {args.name!r} registered and approved.")
    print("  This authorises one hand-fed opportunity at a time. It makes no")
    print("  external call, holds no credentials, and does not authorise")
    print("  browsing, bidding, claiming, outreach or scraping.")
    return 0


def cmd_setup_model(args: argparse.Namespace) -> int:
    """Record the commercial clearance for the model that will actually run.

    The defaults are the artifact verified in docs/solvent-model-rights-evidence.md
    — digests re-fetched, the packaged licence hashed byte-identical against the
    publisher's. The owner is applying a finding, not making a licensing
    judgement; the judgement is already recorded, with its sources.
    """
    solvent = _owner_solvent(args)
    try:
        solvent.policy.approve_model_artifact(
            owner_identity=args.owner, model=args.model, tag=args.tag,
            digest=args.digest, license_id=args.license_id,
            license_source=args.license_source, verified_on=args.verified_on,
            restrictions=args.restrictions, reason=args.reason,
            placement=solvent.policy.LOCAL, provider=args.provider,
            commercial_use=True, max_privacy=args.max_privacy,
            cost_per_1k_tokens_cents=0,
            capabilities=tuple(args.capabilities.split(",")))
    except FailClosed as exc:
        print(f"refused: {exc}")
        return 1
    print(f"cleared {args.tag} ({args.license_id}) for commercial use.")
    print(f"  digest {args.digest}")
    print("  Clearance is keyed on that content address. A different artifact")
    print("  under the same tag is not covered by it.")
    return 0


def cmd_setup_capability(args: argparse.Namespace) -> int:
    """Register the owner's promotions in this database. **Owner path.**

    The decisions live in :func:`solvent.harness.owner_promotions`: csv-cleanup
    under OD-12 and OD-14, the other four under OD-15. This applies them to the
    deployment the owner actually runs.

    Before recording anything, each capability's verifier certification is
    re-run against the *current* implementation and the evidence floor is
    re-checked, so a scope the evidence no longer supports cannot be registered
    by rerunning a command. A capability already APPROVED is left alone and
    reported as such — this is idempotent, because an owner may reasonably run
    it twice and a restart must not need it again.

    Moving a capability from LIMITED to APPROVED is a *new* decision and is
    recorded as one. The LIMITED row stays exactly where it is: the table is
    append-only, and "when did this become sellable, and on what evidence" has
    to stay answerable.
    """
    from .checks import checks_for
    from .harness import owner_promotions, promote_capability

    solvent = _owner_solvent(args)
    promotions = owner_promotions()
    if args.only:
        if args.only not in promotions:
            print(f"refused: {args.only!r} carries no owner promotion record. "
                  f"This build has: {', '.join(sorted(promotions))}")
            return 1
        promotions = {args.only: promotions[args.only]}

    failures = []
    for name, capability in sorted(promotions.items()):
        already, _ = solvent.capability.development_decision(name)
        try:
            record = promote_capability(solvent, name,
                                        owner_identity=args.owner,
                                        why=args.why)
        except FailClosed as exc:
            print(f"[REFUSED] {name}: {exc}")
            failures.append(name)
            continue
        listed = len(checks_for(name))
        covered = len(capability.covers)
        print(f"[APPROVED] {capability.version} — cleared for client work")
        print(f"  verifier  {record['verifier_ref']} @ {record['fingerprint']}")
        print(f"  certified {covered} of {listed} check(s) this verifier lists")
        if covered < listed:
            withheld = sorted(set(checks_for(name)) - set(capability.covers))
            print(f"  NOT certified, and not claimed: {', '.join(withheld)}")
        print(f"  evidence  {capability.evidence_summary}")
        if already and already != solvent.capability.APPROVED:
            print(f"  previously {already}; that row stays on the record")

    for name in sorted(promotions):
        permitted, why = solvent.capability.may_deploy(name)
        if not permitted and name not in failures:
            print(f"[DEFECT] {name}: recorded as promoted and delivery would "
                  f"still be refused: {why}")
            failures.append(name)

    if failures:
        # One capability's evidence going bad says nothing about another's, so
        # the others are still promoted -- but the dangerous reading of a
        # non-zero exit is "nothing happened". Whatever did happen is now
        # sellable, so it is named on the same screen as the refusal rather
        # than left to be inferred from the lines above.
        cleared = sorted(set(promotions) - set(failures))
        print()
        if cleared:
            print(f"{len(cleared)} capability(ies) ARE now cleared for client "
                  f"work: {', '.join(cleared)}")
        else:
            print("Nothing was cleared for client work.")
        print(f"{len(set(failures))} capability(ies) were not promoted: "
              f"{', '.join(sorted(set(failures)))}. Each refusal above says "
              "what the evidence does not support. Nothing else changed: no "
              "job was accepted, no proposal sent, no money moved, and "
              "simulation mode is exactly as you left it.")
        return 1
    print(f"\n{len(promotions)} capability(ies) cleared for client work. "
          "Nothing else changed: no job was accepted, no proposal sent, no "
          "money moved, and simulation mode is exactly as you left it.")
    return 0


def cmd_setup_notifications(args: argparse.Namespace) -> int:
    """Record that a phone number is present, and which providers carry it.

    Takes no number and no credential. The number is read from the runtime's
    environment at dispatch; what is recorded here is a **mask** of it, so the
    owner can confirm Solvent has the right number without the number existing
    in the database, on a page, in the audit log or in a backup.
    """
    from . import notify

    solvent = _owner_solvent(args)
    number = os.environ.get(notify.PHONE_ENV, "")
    masked = notify.mask(number)
    if not masked:
        print(f"refused: {notify.PHONE_ENV} is not set in this process's "
              f"environment, or does not look like a phone number.\n"
              f"         Put it in the runtime's environment file and run this "
              f"again. It is never passed on the command line, because a "
              f"command line ends up in shell history.", file=sys.stderr)
        return 2
    patch = {"notifications": {
        "owner_phone_mask": masked,
        "sms_provider": args.sms_provider,
        "voice_provider": args.voice_provider,
    }}
    if args.escalate_after_minutes:
        patch["notifications"]["escalate_after_seconds"] = \
            args.escalate_after_minutes * 60
    solvent.policy.amend(patch, args.owner,
                         "owner recorded notification routing")
    print(f"recorded. {PRODUCT} will text {masked}"
          + (f" via {args.sms_provider}" if args.sms_provider else
             " once an SMS provider is configured") + ".")
    print("  The number itself was not stored. Nothing here can print it back.")
    if not args.sms_provider:
        print("  No SMS provider yet, so nothing can actually be sent.")
    return 0


def cmd_setup_stripe(args: argparse.Namespace) -> int:
    """Advance the payment rail one step. Takes evidence, never a credential."""
    solvent = _owner_solvent(args)
    try:
        if args.state == "SELECTED":
            # Choosing the rail is a separate act from moving along it, and the
            # ladder has nothing to advance until it has been chosen.
            solvent.policy.approve_payment_rail(
                owner_identity=args.owner, rail="stripe",
                verification_signal="stripe_webhook_signed_event",
                reason=args.reason)
        else:
            solvent.policy.advance_payment_rail(
                owner_identity=args.owner, state=args.state, reason=args.reason,
                evidence=args.evidence)
    except FailClosed as exc:
        print(f"refused: {exc}")
        return 1
    rail = solvent.policy.payment_rail()
    print(f"payment rail: {rail['operational_status']} — {rail['means']}")
    if rail["next_state"]:
        print(f"  next: {rail['next_state']}")
    return 0


def cmd_setup_runtime(args: argparse.Namespace) -> int:
    """Record where the runtime's own files live on this machine.

    Both paths have defaults suited to the ``deploy/`` unit files, and a
    deployment whose filesystem is laid out differently has to say so -- the
    runtime reads the heartbeat path from policy, and the control centre is
    given the spool path on its command line, so the two can disagree unless
    one place records them.

    This command is deliberately free of any knowledge of which platform it is
    running on. Where the files belong is a property of an installation, and
    the installer is what knows that.
    """
    solvent = _owner_solvent(args)
    patch: dict = {}
    if args.heartbeat_path:
        patch["heartbeat_path"] = args.heartbeat_path
    if args.owner_intent_spool:
        patch["owner_intent_spool"] = args.owner_intent_spool
    if not patch:
        print("refused: nothing to record; pass --heartbeat-path or "
              "--owner-intent-spool", file=sys.stderr)
        return 2
    solvent.policy.amend({"runtime": patch}, args.owner,
                         "owner recorded the runtime's paths for this machine")
    for name, value in sorted(patch.items()):
        print(f"{name}: {value}")
    return 0


def cmd_setup_firewall(args: argparse.Namespace) -> int:
    """Record what the host firewall does, so readiness can see it."""
    solvent = _owner_solvent(args)
    try:
        solvent.policy.record_network_posture(
            owner_identity=args.owner, default_deny=args.default_deny,
            proxy_port=args.proxy_port, tls_terminated=args.tls_terminated,
            note=args.note)
    except FailClosed as exc:
        print(f"refused: {exc}")
        return 1
    posture = solvent.policy.network_posture()
    print(f"host network posture: "
          f"{'default-deny' if posture['default_deny'] else 'NOT default deny'}")
    print(f"  proxy port {posture['proxy_port'] or 'not recorded'}, "
          f"TLS terminated: {posture['tls_terminated']}")
    if not posture["default_deny"]:
        print("  Readiness will report this as unsafe, which is correct.")
    return 0


def cmd_web_exposure(args: argparse.Namespace) -> int:
    """Would this binding expose the control centre? Checks configuration."""
    import os

    from .web import auth as web_auth
    from .web import exposure

    solvent = _owner_solvent(args)
    posture = solvent.policy.network_posture()
    findings = exposure.assess(
        host=args.host, port=args.port, behind_proxy=args.behind_proxy,
        tls_terminated=posture["tls_terminated"] or args.behind_proxy,
        secure_cookies=not args.insecure_cookies,
        firewall_default_deny=posture["default_deny"],
        password_configured=bool(web_auth.configured_hash()))
    print("Control centre exposure")
    print("=" * 70)
    for finding in findings:
        print(f"  {'OK    ' if finding.safe else 'UNSAFE'} {finding.name}: "
              f"{finding.detail}")
        if finding.remedy:
            print(f"         -> {finding.remedy}")
    print("-" * 70)
    print(f"  {exposure.summary(findings)}")
    return 0 if not exposure.unsafe(findings) else 1


def cmd_setup_check(args: argparse.Namespace) -> int:
    """One line per thing the owner configures, and whether it is done.

    Reads the existing readiness projection and the authorities themselves. It
    is a *view*, not a second opinion: nothing here decides readiness, and if it
    disagreed with `solvent readiness` that would be a defect in this function.
    """
    import os

    from .harness import STRIPE_SECRET_ENV
    from .owner import KEY_ENV

    solvent = _owner_solvent(args)
    report = solvent.readiness()
    # Read the checks themselves. Parsing the blocking *strings* looked like it
    # worked and quietly reported MODEL as OK with nothing cleared at all,
    # because the prefix it split on was the category, not the check name.
    checks = {check.name: check for check in report.checks}

    def blocked(name: str) -> bool:
        check = checks.get(name)
        return check is not None and not check.ready

    def line(name, ok, detail):
        print(f"  {name:24s} {'OK      ' if ok else 'ACTION  '} {detail}")

    print("Owner configuration")
    print("=" * 70)

    key_ok = solvent.owner.available
    line("OWNER_KEY", key_ok,
         f"present and valid ({KEY_ENV})" if key_ok else solvent.owner.key_refusal)

    party = solvent.policy.contracting_party()
    needed = solvent.policy.contracting_shortfall("CLIENT_AGREEMENT")
    line("CONTRACTING_IDENTITY", party["decided"] and not needed,
         f"{party['structure']}, complete for a client agreement"
         if party["decided"] and not needed
         else f"still needed: {', '.join(needed) or 'a contracting structure'}")

    rail = solvent.policy.payment_rail()
    secret_present = bool(os.environ.get(STRIPE_SECRET_ENV, ""))
    line("STRIPE", rail["operational_status"] != solvent.policy.RAIL_SELECTED,
         f"{rail['operational_status']}; webhook secret "
         f"{'present' if secret_present else 'not set'} "
         "(not required until a payment must be collected)")

    cleared = solvent.policy.approved_models()
    model_ok = not blocked("model commercial rights")
    line("MODEL", model_ok,
         f"{len(cleared)} artifact(s) cleared"
         + ("" if model_ok else "; the configured artifact is not one of them"))

    real_sources = [s for s in solvent.discovery.sources() if not s["is_fixture"]]
    line("WORK_SOURCE", bool(real_sources),
         f"{len(real_sources)} approved: "
         f"{', '.join(s['name'] for s in real_sources)}" if real_sources
         else "none registered")

    proven = [c for c in solvent.capability.capabilities() if c.proven]
    line("CSV_CAPABILITY", bool(proven),
         f"{', '.join(c.version for c in proven)}" if proven
         else "nothing promoted")

    line("CLIENT_COMMUNICATION", True,
         "human relay — every reply is drafted and needs owner approval to send")

    simulation = solvent.policy.get("egress", "simulation_only", default=True)
    line("SIMULATION_ONLY", True, "ON — no real external effects" if simulation
         else "OFF — real external effects are enabled")

    halted = solvent.policy.operating_mode is OperatingMode.HALT
    line("HALT", not halted, "not engaged" if not halted else "ENGAGED")

    print("-" * 70)

    # Two different questions, and reporting only the stricter one told an owner
    # who was ready for a supervised trial that they were blocked -- by a
    # webhook secret the trial never uses.
    from .readiness import (
        assert_may_attempt_first_real_job, assert_may_attempt_supervised_trial,
    )

    def verdict(check):
        try:
            check(report)
        except FailClosed as exc:
            return False, [l.strip(" -") for l in str(exc).splitlines()[1:]]
        return True, []

    trial_ready, trial_blockers = verdict(assert_may_attempt_supervised_trial)
    print(f"  FIRST_TRIAL_READINESS    {'READY' if trial_ready else 'BLOCKED'}"
          "   (supervised, human-relayed, one hand-fed job)")
    for item in trial_blockers:
        print(f"      - {item}")

    full_ready, full_blockers = verdict(assert_may_attempt_first_real_job)
    print(f"  FULL_ACTIVATION          {'READY' if full_ready else 'BLOCKED'}"
          "   (autonomous delivery and payment collection)")
    for item in full_blockers:
        print(f"      - {item}")

    print("\nNo secret is printed by this command, only whether one is present.")
    return 0 if trial_ready else 1


def cmd_web(args: argparse.Namespace) -> int:
    """Run the owner control centre. A separate process on purpose.

    Constructing a Solvent installs the egress audit hook, which denies
    socket.bind for the whole interpreter and cannot be removed. So this command
    never builds one: it opens the database read-only and serves pages. That is
    also the security property -- the control centre has no Action Gate, no
    policy handle and no owner key, because it cannot have them.
    """
    from .web import server as web_server

    web_server.serve(args.db, host=args.host, port=args.port,
                     spool=args.spool, secure_cookies=not args.insecure_cookies)
    return 0


def cmd_web_password(args: argparse.Namespace) -> int:
    """Hash a web password, read from a prompt rather than an argument.

    Prompted rather than passed: a password on a command line is in shell history
    and in the process list, where any other user on the machine can read it.
    The hash is printed for the owner to paste into the environment file; the
    password itself is never stored or echoed.
    """
    import getpass

    from .web import auth as web_auth

    first = getpass.getpass("New control-centre password: ")
    second = getpass.getpass("Again: ")
    if first != second:
        print("those did not match; nothing was changed")
        return 1
    try:
        encoded = web_auth.hash_password(first)
    except ValueError as exc:
        print(f"refused: {exc}")
        return 1
    print("\nAdd this line to /etc/solvent/solvent.env, then restart the "
          "control centre:\n")
    print(f"{web_auth.PASSWORD_HASH_ENV}={encoded}\n")
    print("This is a hash, not your password. It cannot be turned back into one.")
    return 0


def _intent_spool(args) -> str:
    return args.spool or harness.OWNER_INTENT_SPOOL


def cmd_intents_list(args: argparse.Namespace) -> int:
    """What the control centre has asked for and has not been able to do.

    The website can queue a consequential request and cannot complete one: it
    does not hold the key that mints an owner approval, which is the whole
    reason taking over the website does not take over the business. This is the
    other end of that — the owner, at a terminal, seeing what is waiting.
    """
    from .web.intents import (CONSEQUENTIAL, IntentWriter, TERMINAL_COMMAND,
                              TERMINAL_ONLY, classify, describe)

    pending = IntentWriter(_intent_spool(args)).pending()
    if not pending:
        print("nothing is queued.")
        return 0

    print(f"{len(pending)} queued intent(s) in {_intent_spool(args)}\n")
    for record in pending:
        verb = record.get("verb", "")
        try:
            kind = classify(verb)
        except ValueError:
            kind = "UNKNOWN VERB"
        print(f"{record.get('id', '?')}  {verb}  [{kind}]")
        print(f"  means      {describe(verb) if kind != 'UNKNOWN VERB' else '—'}")
        print(f"  because    {record.get('why', '')}")
        if record.get("subject"):
            print(f"  subject    {record['subject']}")
        if record.get("params"):
            print(f"  params     {record['params']}")
        print(f"  asked by   {record.get('requested_by', '')}")
        if verb in TERMINAL_ONLY:
            print(f"  ACTION     not approvable from here — run "
                  f"`{TERMINAL_COMMAND.get(verb, 'solvent setup')}`")
        elif kind == CONSEQUENTIAL:
            print(f"  ACTION     solvent intents approve {record.get('id')} "
                  f"--reason '...'")
        else:
            print("  ACTION     solvent intents run   (safe verbs need no approval)")
        print()
    return 0


def cmd_intents_run(args: argparse.Namespace) -> int:
    """Apply the safe intents. Consequential ones are listed, never guessed at."""
    solvent = _owner_solvent(args)
    results = harness.consume_owner_intents(solvent, spool=_intent_spool(args))
    return _report_intent_results(results)


def cmd_intents_approve(args: argparse.Namespace) -> int:
    """Approve exactly one queued intent and carry it out.

    The approval is minted here, on the owner's machine, from the owner key in
    this process's environment — never in the web process, which is why the
    control centre can ask for this and not do it. It is bound by subject to
    the single intent named on the command line, so approving one thing does
    not authorise whatever else happens to be sitting in the spool.
    """
    from .types import ActionClass
    from .web.intents import IntentWriter, TERMINAL_COMMAND, TERMINAL_ONLY

    solvent = _owner_solvent(args)
    spool = _intent_spool(args)
    queued = {r.get("id"): r for r in IntentWriter(spool).pending()}
    record = queued.get(args.intent_id)
    if record is None:
        print(f"no queued intent {args.intent_id!r}. `solvent intents list` "
              f"shows what is waiting.", file=sys.stderr)
        return 2
    verb = record.get("verb", "")
    if verb in TERMINAL_ONLY:
        print(f"{verb} is not completed by approving a file. Run "
              f"`{TERMINAL_COMMAND.get(verb, 'solvent setup')}` instead.",
              file=sys.stderr)
        return 2

    problem = solvent.owner.key_problem() if hasattr(solvent.owner, "key_problem") \
        else ("" if solvent.owner.available else "no owner key")
    if problem:
        print(f"cannot approve: {problem}", file=sys.stderr)
        return 2

    approval = solvent.owner.issue(
        owner_identity=args.owner,
        subject=harness.intent_subject(args.intent_id),
        action_class=ActionClass.C5_AUTHORITY_CHANGE,
        ttl_minutes=args.ttl_minutes)
    results = harness.consume_owner_intents(solvent, spool=spool,
                                            approval=approval)
    return _report_intent_results(results)


def cmd_intents_reject(args: argparse.Namespace) -> int:
    """Refuse one queued intent, on the record, without carrying it out."""
    from .web.intents import IntentWriter

    solvent = _owner_solvent(args)
    spool = pathlib.Path(_intent_spool(args))
    queued = {r.get("id"): r for r in IntentWriter(str(spool)).pending()}
    if args.intent_id not in queued:
        print(f"no queued intent {args.intent_id!r}.", file=sys.stderr)
        return 2
    refused = spool / "refused"
    refused.mkdir(parents=True, exist_ok=True)
    os.replace(spool / f"{args.intent_id}.intent",
               refused / f"{args.intent_id}.intent")
    solvent.audit.record(
        event="owner.intent_rejected", authority="policy", initiator=args.owner,
        why=args.reason, decision=queued[args.intent_id].get("verb", ""),
        result="REJECTED by the owner at the terminal",
        external_effect="owner intent from the control centre")
    print(f"{args.intent_id} rejected and recorded.")
    return 0


def _report_intent_results(results: list) -> int:
    if not results:
        print("nothing was queued.")
        return 0
    counts = {"applied": 0, "refused": 0, "queued": 0}
    for record in results:
        if record["outcome"] == "REFUSED":
            mark = "refused"
        elif record["outcome"] == harness.QUEUED:
            mark = "queued"
        else:
            mark = "applied"
        counts[mark] += 1
        print(f"{mark:8} {record['intent']}  {record['verb'] or '?'}")
        print(f"         {record['why']}")
    # A refusal is a correct outcome, not a failure of this command: the exit
    # code reports whether the command ran, and the lines above report what
    # happened to each intent. "queued" is neither — it is still waiting.
    print(f"\n{counts['applied']} applied, {counts['refused']} refused, "
          f"{counts['queued']} still waiting for an approval.")
    return 0


def cmd_relay(args: argparse.Namespace) -> int:
    """What is finished and waiting for a person to pass on.

    A view over the authorities that already hold these facts, not a new state.
    The Orchestrator knows which jobs have a verified deliverable; Client
    Relations holds the drafted message. The owner's question is "what do I
    send, and to whom" — not "is this file correct". Verification has already
    answered the second one, and this says so explicitly, because an owner who
    is asked to check technical correctness is being asked to be the control
    rather than to authorise one.
    """
    from .types import JobState

    solvent = _owner_solvent(args)
    waiting = [job for job in solvent.orchestrator.jobs()
               if job.state in (JobState.READY_FOR_DELIVERY,
                                JobState.AWAITING_PAYMENT)]
    if not waiting:
        print("nothing is waiting to be relayed.")
        return 0

    for job in waiting:
        verified, why = solvent.orchestrator.verification_satisfied(job.id)
        artifact = solvent.orchestrator.current_deliverable(job.id)
        print(f"job {job.id}  [{job.state.value}]")
        print(f"  client        {job.client_id}")
        print(f"  verification  {'PASSED' if verified else 'NOT SATISFIED'} — {why}")
        if artifact:
            print(f"  deliverable   {artifact.path}")
            print(f"                {artifact.size} bytes, "
                  f"digest {artifact.digest[:23]}…")
        drafts = [r for r in solvent.relations.responses(job.id)
                  if r["phase"] == "PREPARED"]
        for draft in drafts:
            print(f"  message to    {draft['routed_to']}")
            for line in draft["body"].splitlines():
                print(f"      | {line}")
        if not verified:
            print("  DO NOT SEND — verification is not satisfied for this job.")
        elif artifact:
            print("  READY_FOR_HUMAN_RELAY — send the file above, then record")
            print(f"  payment when it arrives. {PRODUCT} sends nothing itself.")
        print()
    return 0


def cmd_payments_ingest(args: argparse.Namespace) -> int:
    """Verify spooled payment deliveries and hand the genuine ones to the Ledger."""
    from .harness import PAYMENT_SPOOL, ingest_payment_spool

    solvent = _owner_solvent(args)
    results = ingest_payment_spool(solvent, spool=args.spool or PAYMENT_SPOOL)
    if not results:
        print("no deliveries waiting")
        return 0
    for record in results:
        print(f"  {record['delivery']}: {record['outcome']}")
        if record.get("why"):
            print(f"      {record['why']}")
    return 0


def cmd_client_add(args: argparse.Namespace) -> int:
    """Record a client who wrote in, and what they asked about.

    The door the relationship layer never had. ``CRM.discover`` had
    no caller anywhere in the package -- no task, no command, no
    route -- so the CRM, the negotiation engine and the engagement
    were reachable only from test code.

    Every field is something the owner was told. Nothing here is
    inferred, and the facts a client's order cannot carry -- who they
    are, what trade they are in -- are typed rather than guessed.
    """
    from .clientintake import record_enquiry

    solvent = _owner_solvent(args)
    recorded = record_enquiry(
        solvent, prospect_id=args.id, company=args.company,
        industry=args.industry, state=args.state,
        contact_name=args.contact or "", contact_email=args.email,
        wants=args.wants, budget_cents=int(args.budget_cents),
        seen_on=args.seen_on, owner_identity=args.owner,
        source=args.source)
    print(recorded.summary())
    return 0 if recorded.done else 1


def cmd_readiness(args: argparse.Namespace) -> int:
    """What stands between Solvent and its first real paid job."""
    solvent = Solvent(args.db)
    report = solvent.readiness()
    print("First-revenue readiness")
    print("=" * 66)
    for check in report.checks:
        print(f"  [{check.mark:5}] {check.name}")
        print(f"          {check.detail}")
    print(f"\n{report.summary}")
    if report.blocking:
        print("\nblocking:")
        for item in report.blocking:
            print(f"  - {item}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """Business status without terminal archaeology."""
    solvent = Solvent(args.db)
    metrics = solvent.metrics().to_dict()
    print(f"{PRODUCT} business status")
    print("=" * 66)
    print(f"\n{metrics['headline']}\n")
    groups = {
        "acquisition": ["opportunities_discovered", "opportunities_by_status",
                        "rejected_by_reason", "jobs_accepted", "jobs_rejected"],
        "delivery": ["jobs_completed", "jobs_failed", "jobs_active", "jobs_blocked",
                     "jobs_overdue"],
        "money": ["revenue_invoiced", "revenue_collected", "real_revenue_collected",
                  "actual_costs", "actual_profit", "actual_margin"],
        "quality": ["estimate_accuracy", "verification_failures", "lessons_learned"],
        "concentration": ["client_concentration", "source_concentration"],
    }
    for group, keys in groups.items():
        print(f"{group}")
        for key in keys:
            value = metrics[key]
            if value is None:
                # A null margin is "there is none", not "it is zero". Printing
                # None would be worse than either.
                value = "not applicable (no real revenue)"
            print(f"  {key:<28} {value}")
        print()
    for caveat in metrics["caveats"]:
        print(f"!! {caveat}")
    return 0


def cmd_laws(args: argparse.Namespace) -> int:
    """Print the laws and where each is enforced in code."""
    print(f"  {PRODUCT} exists to FIND, QUALIFY, COMPLETE, DELIVER and PROFIT FROM")
    print("  legitimate client work. Everything below protects that mission.\n")
    laws = [
        ("discovery finds work, never takes it", "discovery.Discovery (no accept path)"),
        ("qualification owns no economics", "qualification.Qualification -> Governor"),
        ("one authority per decision", "store.TABLE_OWNER + AuthorityConnection"),
        ("policy is owner-writable only", "policy.PolicyStore.amend"),
        ("governor before spend", "governor.authorize_spend"),
        ("gate before external effect", "gate.ActionGate + egress audit hook"),
        ("conformance before price", "governor._evaluate (first check)"),
        ("location before binding price", "governor._material_unresolved"),
        ("unknown fails closed", "errors.FailClosed, raised at each gate"),
        ("verify before learn", "memory.learn + audit.record_verification"),
        ("ledger and audit are append-only", "store immutability triggers"),
        ("learning cannot touch gate parameters", "no write path in TABLE_OWNER"),
        ("external content has no authority", "content.UntrustedContent"),
        (f"{PRODUCT} may not expand itself", "capability.register owner check"),
        ("no silent stalls", "orchestrator.TIMEOUTS_MINUTES + escalate_overdue"),
    ]
    for law, where in laws:
        print(f"  {law:<42} {where}")
    return 0


def cmd_state(args: argparse.Namespace) -> int:
    solvent = Solvent(args.db)
    print(json.dumps(solvent.project(args.job_id).to_dict(), indent=2))
    return 0


def cmd_services(args: argparse.Namespace) -> int:
    """Print the service-selection scorecard and the first service's contract.

    Ranking only. The Financial Governor still prices every individual job, so
    nothing printed here is a price or an authorisation to trade.
    """
    print("Service selection scorecard (ranking only — no prices)")
    print("=" * 66)
    for candidate, score in services.scorecard():
        print(f"  {score:>6.1f}  {candidate.name}")
        if candidate.note:
            print(f"          {candidate.note}")

    first, backup = services.recommended()
    print(f"\nRecommended first service : {first.name}")
    print(f"Backup service            : {backup.name}")

    contract = services.SPREADSHEET_CLEANUP
    print(f"\nContract for {contract.id}")
    print("-" * 66)
    for label, values in (
        ("accepted inputs", contract.accepted_inputs),
        ("deliverables", contract.deliverables),
        ("will not accept", contract.unsupported_requests),
        ("verification", contract.verification_requirements),
        ("rejects when", contract.rejection_conditions),
        ("owner approval", contract.owner_approval_triggers),
    ):
        print(f"  {label}:")
        for value in values:
            print(f"    - {value}")

    print("\nSelection is a recommendation. OD-12 is answered by the owner, and")
    print("the capability must be proven before readiness clears it.")
    return 0


def _mode(args: argparse.Namespace) -> rt.Mode:
    return rt.Mode.ALWAYS_ON if getattr(args, "always_on", False) else rt.Mode.LOCAL


def _open(args: argparse.Namespace) -> Solvent | None:
    """Open the store, or explain why not.

    A corrupt or unreadable database raises before any startup check can run, so
    without this the operator gets a traceback and systemd gets an exit code with
    no explanation in the journal. Refusing is already the right behaviour; this
    makes the refusal legible.
    """
    try:
        return Solvent(args.db)
    except sqlite3.Error as exc:
        print(f"cannot open {args.db}: {exc}", file=sys.stderr)
        print("The database is unreadable. Restore the most recent verified "
              "backup rather than deleting it — see deploy/backup.sh.",
              file=sys.stderr)
        return None


def cmd_run(args: argparse.Namespace) -> int:
    """Run Solvent as a long-lived service. This is what the service unit execs.

    It runs in the foreground and stops on SIGTERM, because that is what a
    service manager expects; daemonising here would take process supervision away
    from the thing that is good at it.
    """
    solvent = _open(args)
    if solvent is None:
        return 1
    try:
        report = rt.serve(solvent, mode=_mode(args), tick_s=args.tick,
                          max_ticks=args.max_ticks)
    except FailClosed as refusal:
        print(f"{PRODUCT} did not start: {refusal}", file=sys.stderr)
        return 1
    print(f"stopped: {report}")
    return 0


def cmd_health(args: argparse.Namespace) -> int:
    """Report what this Solvent can currently do — not merely that it is alive."""
    solvent = _open(args)
    if solvent is None:
        return 1
    started = rt.startup_checks(solvent, mode=_mode(args))
    print(started.render())
    state, why = rt.health(solvent, started=started)
    print(f"\nHealth: {state.value} — {why}")
    print("May start work:", "yes" if state.may_start_work else "no")
    unsettled = solvent.gate.unsettled()
    if unsettled:
        print(f"\n{len(unsettled)} external action(s) of unknown outcome:")
        for row in unsettled:
            print(f"  {row['request_id']}  {row['action_class']} -> "
                  f"{row['destination']}  (job {row['job_id'] or 'none'})")
        print("\nNothing here may be retried on the strength of this list.")
    return 0 if state.may_start_work else 2


def cmd_recover(args: argparse.Namespace) -> int:
    """Bring durable state to a safe resting point after a crash or reboot."""
    solvent = _open(args)
    if solvent is None:
        return 1
    notes = rt.reconcile(solvent)
    if not notes:
        print("nothing outstanding")
        return 0
    for note in notes:
        print(f"  {note}")
    print(f"\n{len(notes)} item(s) need the owner to confirm what actually "
          "happened before anything is repeated.")
    return 0


def cmd_halt(args: argparse.Namespace) -> int:
    """Throw the kill switch. Survives restarts because Policy is durable."""
    solvent = Solvent(args.db)
    solvent.policy.set_operating_mode(OperatingMode.HALT, OWNER, args.reason)
    print(f"operating mode: HALT — {args.reason}")
    print("No new work, no spending, no external effects. A restart will not "
          "clear this; `solvent resume` is the only way back.")
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    solvent = Solvent(args.db)
    solvent.policy.set_operating_mode(OperatingMode.NORMAL, OWNER, args.reason)
    state, why = rt.health(solvent)
    print(f"operating mode: NORMAL — {args.reason}")
    print(f"health: {state.value} — {why}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=ENGINE.lower(),
        description=f"{PRODUCT} — AI office operating system. "
                    f"Engine: {ENGINE}.")
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor", help="report measured enforcement")
    doctor.add_argument("--db", default=rt.DEFAULT_DB)
    doctor.set_defaults(func=cmd_doctor)
    sub.add_parser("laws", help="print the mission, laws, and where each is enforced"
                   ).set_defaults(func=cmd_laws)
    sub.add_parser("acquire", help="run one acquisition cycle (fixtures only)"
                   ).set_defaults(func=cmd_acquire)
    sub.add_parser("services", help="print the service-selection scorecard"
                   ).set_defaults(func=cmd_services)

    marketplaces = sub.add_parser(
        "marketplaces", help="what each marketplace permits, activity by "
                             "activity")
    marketplaces.add_argument("--db", default=rt.DEFAULT_DB)
    marketplaces.set_defaults(func=cmd_marketplaces)

    metrics = sub.add_parser("metrics", help="print business metrics")
    metrics.add_argument("--db", default=rt.DEFAULT_DB)
    metrics.set_defaults(func=cmd_metrics)

    status = sub.add_parser("status", help="business status dashboard")
    status.add_argument("--db", default=rt.DEFAULT_DB)
    status.set_defaults(func=cmd_status)

    readiness = sub.add_parser("readiness",
                               help="what blocks the first real paid job")
    readiness.add_argument("--db", default=rt.DEFAULT_DB)
    readiness.set_defaults(func=cmd_readiness)

    cycle = sub.add_parser(
        "cycle", help="take one opportunity from an approved source through "
                      "to reconciled profit (simulation)")
    cycle.add_argument("--owner", default=OWNER,
                       help="the registered owner identity making the "
                            "decisions this cycle needs")
    cycle.add_argument("--db", default=rt.DEFAULT_DB)
    cycle.add_argument("--source", required=True,
                       help="the registered source this opportunity belongs to")
    cycle.add_argument("--posting", default="",
                       help="JSON file: one opportunity you are entering by "
                            "hand. Without it the source is polled instead")
    cycle.add_argument("--file", required=True,
                       help="the client's file to work on")
    cycle.add_argument("--brief", required=True,
                       help="JSON file: the checklist agreed with the client")
    cycle.add_argument("--capability", default="csv-cleanup",
                       help="which cleared capability performs the work")
    cycle.add_argument("--workdir", default=".",
                       help="where the deliverable is written")
    cycle.add_argument("--accept-quote", action="store_true",
                       help="your approval of the Governor's quote, if it "
                            "asks for one. Without it the job waits")
    cycle.add_argument("--simulate-payment", action="store_true",
                       help="inject a simulated payment to exercise invoicing "
                            "and reconciliation. Never counts as revenue")
    cycle.set_defaults(func=cmd_cycle)

    demo = sub.add_parser("demo", help="run the complete-job harness (fixtures only)")
    demo.add_argument("--state", default="CA", help="project jurisdiction")
    demo.add_argument("--quote", default="900", help="quoted price in dollars")
    demo.set_defaults(func=cmd_demo)

    run = sub.add_parser("run", help=f"run {PRODUCT} as a long-lived service")
    run.add_argument("--db", default=rt.DEFAULT_DB)
    run.add_argument("--always-on", action="store_true",
                     help="production mode: refuse to start without durable storage")
    run.add_argument("--tick", type=float, default=1.0, help="loop interval seconds")
    run.add_argument("--max-ticks", type=int, default=None,
                     help="stop after N ticks (testing)")
    run.set_defaults(func=cmd_run)

    health = sub.add_parser("health", help=f"what this {PRODUCT} can currently do")
    health.add_argument("--db", default=rt.DEFAULT_DB)
    health.add_argument("--always-on", action="store_true")
    health.set_defaults(func=cmd_health)

    recover = sub.add_parser("recover",
                             help="reconcile durable state after a crash or reboot")
    recover.add_argument("--db", default=rt.DEFAULT_DB)
    recover.set_defaults(func=cmd_recover)

    halt = sub.add_parser("halt", help="throw the kill switch (survives restarts)")
    halt.add_argument("--db", default=rt.DEFAULT_DB)
    halt.add_argument("--reason", default="owner halted Solvent")
    halt.set_defaults(func=cmd_halt)

    resume = sub.add_parser("resume", help="clear the kill switch")
    resume.add_argument("--db", default=rt.DEFAULT_DB)
    resume.add_argument("--reason", default="owner resumed Solvent")
    resume.set_defaults(func=cmd_resume)

    # --- owner setup ---------------------------------------------------
    setup = sub.add_parser(
        "setup", help="record the owner's configuration (no secrets here)")
    setup_sub = setup.add_subparsers(dest="setup_command", required=True)

    def owned(parser):
        parser.add_argument("--owner", default=OWNER,
                            help="the registered owner identity making this record")
        parser.add_argument("--db", default=rt.DEFAULT_DB)
        return parser

    contracting = owned(setup_sub.add_parser(
        "contracting", help="record who the owner contracts as"))
    contracting.add_argument("--structure", default="INDIVIDUAL",
                             choices=["INDIVIDUAL", "LLC", "CORPORATION",
                                      "PARTNERSHIP"])
    contracting.add_argument("--legal-name", default="",
                             help="the name the owner contracts under")
    contracting.add_argument("--email", default="",
                             help="where a client reaches the owner")
    contracting.add_argument("--address", default="",
                             help="only needed to issue an invoice")
    contracting.add_argument(
        "--tax-reference-provisioned", action="store_true",
        help=f"record that a tax reference exists. Takes no value: {PRODUCT} "
             "stores presence, never the number")
    contracting.add_argument("--reason", default="OD-1: owner contracting details")
    contracting.set_defaults(func=cmd_setup_contracting)

    crypto = owned(setup_sub.add_parser(
        "crypto", help="record a cryptocurrency receipt address (blank "
                       "until you set it)"))
    crypto.add_argument("--network", default="",
                        help="ethereum, base, polygon or bitcoin")
    crypto.add_argument("--asset", default="", help="e.g. USDC, ETH, BTC")
    crypto.add_argument("--address", default="",
                        help="the owner's own receiving address. DeskPilot "
                             "stores it and can never spend from it")
    crypto.add_argument("--minimum-confirmations", type=int, default=0,
                        help="defaults to this network's recorded minimum")
    crypto.add_argument("--show", action="store_true",
                        help="print what is recorded and change nothing")
    crypto.add_argument("--reason",
                        default="owner records a crypto receipt address")
    crypto.set_defaults(func=cmd_setup_crypto)

    probation = owned(setup_sub.add_parser(
        "probation", help="let a capability be quoted before it has "
                          "client history"))
    probation.add_argument("--capability", default="",
                           help="the skill to put on probation; omit to "
                                "list what is on probation now")
    probation.add_argument("--max-jobs", type=int, default=0)
    probation.add_argument("--max-value-cents", type=int, default=0,
                           help="ceiling per job: a first job is for "
                                "learning what the work takes")
    probation.add_argument("--min-deadline-hours", type=float, default=0.0,
                           help="least delivery room to ask for, since how "
                                "long the work takes is what nobody knows")
    probation.add_argument("--revoke", default="",
                           help="end probation for this capability")
    probation.add_argument("--reason",
                           default="owner changes probation")
    probation.set_defaults(func=cmd_setup_probation)

    capacity = owned(setup_sub.add_parser(
        "capacity", help="record how much work may be owed at one time"))
    capacity.add_argument("--max-owed-seconds", type=float, default=None,
                          help="seconds of expected work that may be owed "
                               "at once, across execution, verification and "
                               "revisions together")
    capacity.add_argument("--max-concurrent-jobs", type=int, default=None,
                          help="jobs in flight at once; 3 by default, 4 at "
                               "the very most")
    capacity.add_argument("--max-execution-workers", type=int, default=None)
    capacity.add_argument("--max-verification-workers", type=int,
                          default=None)
    capacity.add_argument("--cpu-share", type=float, default=None,
                          help="fraction of this machine DeskPilot may use. "
                               "It also runs MetaTrader 5; set this low")
    capacity.add_argument("--max-open-bids", type=int, default=None,
                          help="bids outstanding at once; each one could be "
                               "accepted tomorrow")
    capacity.add_argument("--revision-headroom-fraction", type=float,
                          default=None,
                          help="room held per job for the verification and "
                               "revisions that follow it, e.g. 0.4")
    capacity.add_argument("--min-free-disk-bytes", type=int, default=None)
    capacity.add_argument("--deadline-safety-factor", type=float,
                          default=None,
                          help="multiplier on a measured estimate before a "
                               "deadline is promised; never below 1")
    capacity.add_argument("--skill", action="append", default=[],
                          metavar="CAPABILITY",
                          help="a certified skill that gets its own "
                               "pipeline and its own worker; pass once per "
                               "skill")
    capacity.add_argument("--pipeline-target", type=int, default=None,
                          help="jobs each skill's pipeline aims to hold; "
                               "3 by default, 4 at the very most")
    capacity.add_argument("--replenish-at", type=int, default=None,
                          help="the depth at which a pipeline asks for more "
                               "work, e.g. 2; must be below the target")
    capacity.add_argument("--halt", action="store_true",
                          help="stop taking new work, without dropping work "
                               "in flight")
    capacity.add_argument("--resume", action="store_true",
                          help="allow new work again")
    capacity.add_argument("--reason",
                          default="owner records the capacity budget")
    capacity.set_defaults(func=cmd_setup_capacity)

    commercial = owned(setup_sub.add_parser(
        "commercial", help="record the terms that decide what work is worth "
                           "taking"))
    commercial.add_argument("--minimum-fee-cents", type=int, default=None,
                            help="the smallest job worth the overhead")
    commercial.add_argument("--hourly-rate-cents", type=int, default=None,
                            help="the rate an hourly contract is billed "
                                 "at; the owner's own rate, not a market "
                                 "observation")
    commercial.add_argument("--platform-fee-fraction", type=float,
                            default=None,
                            help="the marketplace's cut, e.g. 0.1 for 10%%")
    commercial.add_argument("--platform-fee-minimum-cents", type=int,
                            default=None,
                            help="the least the marketplace takes whatever "
                                 "the percentage comes to, e.g. 500 for "
                                 "Freelancer's '10%% or $5, whichever is "
                                 "greater'")
    commercial.add_argument("--payment-fee-fraction", type=float,
                            default=None,
                            help="the payment provider's percentage, "
                                 "e.g. 0.029")
    commercial.add_argument("--payment-fee-flat-cents", type=int,
                            default=None,
                            help="the payment provider's per-transaction "
                                 "charge, e.g. 30")
    commercial.add_argument("--fixed-monthly-overhead-cents", type=int,
                            default=None,
                            help="the VPS and anything else billed monthly "
                                 "whether or not DeskPilot works")
    commercial.add_argument("--other-direct-cost-per-job-cents", type=int,
                            default=None,
                            help="anything else each delivered job costs")
    commercial.add_argument(
        "--exchange-rate", action="append", default=[],
        metavar="CURRENCY=CENTS=CITATION",
        help="cents of the owner's currency per one unit of another, "
             "with its source, e.g. UAH=2.4=\"ECB reference rate, "
             "2026-10-15\". Needed for any marketplace whose budgets "
             "are not in USD; without one those postings arrive "
             "with no price rather than a guessed one")
    commercial.add_argument("--compute-cents-per-cpu-hour", type=int,
                            default=None,
                            help="what running this machine actually costs")
    commercial.add_argument("--minimum-margin-fraction", type=float,
                            default=None,
                            help="smallest share of revenue that must "
                                 "survive as profit, e.g. 0.30")
    commercial.add_argument(
        "--market-rate", action="append", default=[], metavar="CAP=CENTS=CITE",
        help="a cited market rate, e.g. "
             "csv-cleanup=3000='BLS OEWS 43-9021, May 2025'. Repeatable. "
             "The citation is required")
    commercial.add_argument("--reason",
                            default="owner records the commercial terms")
    commercial.set_defaults(func=cmd_setup_commercial)

    source = owned(setup_sub.add_parser(
        "work-source", help="register the owner-entered work source"))
    source.add_argument("--name", default="owner_entered")
    source.add_argument(
        "--determination",
        default="owner-entered: the owner found this client themselves and "
                "typed the terms in; no platform terms apply and no external "
                "call is made")
    source.set_defaults(func=cmd_setup_work_source)

    model = owned(setup_sub.add_parser(
        "model", help="record commercial clearance for the model that will run"))
    model.add_argument("--model", default="Qwen2.5-14B-Instruct")
    model.add_argument("--tag", default="qwen2.5:14b-instruct")
    model.add_argument(
        "--digest",
        default="sha256:2049f5674b1e92b4464e5729975c9689fcfbf0b0e4443ccf10b53"
                "39f370f9a54")
    model.add_argument("--license-id", default="Apache-2.0")
    model.add_argument(
        "--license-source",
        default="https://huggingface.co/Qwen/Qwen2.5-14B-Instruct/raw/main/LICENSE")
    model.add_argument("--verified-on", default="2026-09-15")
    model.add_argument("--provider", default="ollama")
    model.add_argument("--max-privacy", default="CLIENT_CONFIDENTIAL")
    model.add_argument("--capabilities", default="summarise,extract")
    model.add_argument(
        "--restrictions",
        default="No trademark licence (Apache-2.0 §6): do not brand the "
                "service with Qwen or Alibaba marks. Weights carry no warranty "
                "(§7-8). Redistributing weights or a fine-tune would trigger "
                "§4 notice duties; consuming output does not.")
    model.add_argument(
        "--reason",
        default="OD-3: verified against publisher LICENSE and the packaged "
                "licence blob, byte-identical "
                "(docs/solvent-model-rights-evidence.md)")
    model.set_defaults(func=cmd_setup_model)

    standing = owned(setup_sub.add_parser(
        "standing", help="pre-agree to take direct-hire work unasked"))
    standing.add_argument("--capability", required=True)
    standing.add_argument("--max-value-cents", type=int, default=0,
                          help="the most a job may be worth and still be "
                               "taken without asking")
    standing.add_argument("--min-deadline-hours", type=float, default=0.0,
                          help="the least notice a job may give and still "
                               "be taken without asking")
    standing.add_argument("--max-jobs", type=int, default=0,
                          help="how many jobs this grant covers in total")
    standing.add_argument("--revoke", action="store_true")
    standing.add_argument("--reason",
                          default="owner grants standing authority")
    standing.set_defaults(func=cmd_setup_standing)

    capability = owned(setup_sub.add_parser(
        "capability", help="register the owner's promotions in this database"))
    capability.add_argument(
        "--only", default="",
        help="promote one named capability instead of every promotion this "
             "build carries")
    capability.add_argument(
        "--why", default="",
        help="the owner's reason, recorded with the decision")
    capability.set_defaults(func=cmd_setup_capability)

    notifications = owned(setup_sub.add_parser(
        "notifications",
        help="record that an owner phone is present (a mask, never the number)"))
    notifications.add_argument("--sms-provider", default="")
    notifications.add_argument("--voice-provider", default="")
    notifications.add_argument("--escalate-after-minutes", type=int, default=0)
    notifications.set_defaults(func=cmd_setup_notifications)

    stripe = owned(setup_sub.add_parser(
        "stripe", help="advance the payment rail (evidence, never a credential)"))
    stripe.add_argument("state", choices=["SELECTED", "ENGINEERING_READY",
                                          "CONFIGURED", "LIVE_VERIFIED"])
    stripe.add_argument("--evidence", default="",
                        help="what the owner did or saw. Required for "
                             "CONFIGURED and LIVE_VERIFIED. Never a credential")
    stripe.add_argument("--reason", default="OD-2: payment rail progress")
    stripe.set_defaults(func=cmd_setup_stripe)

    runtime_paths = owned(setup_sub.add_parser(
        "runtime", help="record where the runtime's own files live"))
    runtime_paths.add_argument(
        "--heartbeat-path", default="",
        help="where the runtime touches its liveness file")
    runtime_paths.add_argument(
        "--owner-intent-spool", default="",
        help="the directory the control centre drops requests into")
    runtime_paths.set_defaults(func=cmd_setup_runtime)

    firewall = owned(setup_sub.add_parser(
        "firewall", help="record what the host firewall does"))
    firewall.add_argument("--default-deny", action="store_true",
                          help="inbound denied by default")
    firewall.add_argument("--proxy-port", type=int, default=443)
    firewall.add_argument("--tls-terminated", action="store_true",
                          help="a proxy terminates TLS in front of the site")
    firewall.add_argument("--note", default="host network posture recorded")
    firewall.set_defaults(func=cmd_setup_firewall)

    check = setup_sub.add_parser(
        "check", help="one line per thing the owner configures")
    check.add_argument("--db", default=rt.DEFAULT_DB)
    check.set_defaults(func=cmd_setup_check)

    web = sub.add_parser("web", help="run the owner control centre")
    web.add_argument("--db", default=rt.DEFAULT_DB)
    web.add_argument("--host", default="127.0.0.1",
                     help="loopback by default; exposure belongs to a proxy")
    web.add_argument("--port", type=int, default=8765)
    web.add_argument("--spool", default="",
                     help="where owner intents are written")
    web.add_argument("--insecure-cookies", action="store_true",
                     help="omit the Secure cookie flag, for local testing "
                          "over plain HTTP only")
    web.set_defaults(func=cmd_web)

    exposure_cmd = sub.add_parser(
        "web-exposure", help="would this binding expose the control centre?")
    exposure_cmd.add_argument("--db", default=rt.DEFAULT_DB)
    exposure_cmd.add_argument("--host", default="127.0.0.1")
    exposure_cmd.add_argument("--port", type=int, default=8765)
    exposure_cmd.add_argument("--behind-proxy", action="store_true")
    exposure_cmd.add_argument("--insecure-cookies", action="store_true")
    exposure_cmd.set_defaults(func=cmd_web_exposure)

    web_password = sub.add_parser(
        "web-password", help="hash a control-centre password (prompts; no echo)")
    web_password.set_defaults(func=cmd_web_password)

    relay = sub.add_parser(
        "relay", help="what is verified and waiting for a person to send")
    relay.add_argument("--db", default=rt.DEFAULT_DB)
    relay.set_defaults(func=cmd_relay)

    intents = sub.add_parser(
        "intents", help="review and answer what the control centre asked for")
    intents_sub = intents.add_subparsers(dest="intents_command", required=True)

    def spooled(parser):
        parser.add_argument("--spool", default="",
                            help="intent spool directory")
        parser.add_argument("--db", default=rt.DEFAULT_DB)
        return parser

    spooled(intents_sub.add_parser(
        "list", help="show what is queued and what each one needs")
    ).set_defaults(func=cmd_intents_list)

    spooled(intents_sub.add_parser(
        "run", help="apply the safe intents; leave the rest queued")
    ).set_defaults(func=cmd_intents_run)

    approve = spooled(intents_sub.add_parser(
        "approve", help="approve and carry out exactly one queued intent"))
    approve.add_argument("intent_id")
    approve.add_argument("--reason", required=True,
                         help="why the owner is approving this")
    approve.add_argument("--owner", default=OWNER)
    approve.add_argument("--ttl-minutes", type=int, default=20)
    approve.set_defaults(func=cmd_intents_approve)

    reject = spooled(intents_sub.add_parser(
        "reject", help="refuse one queued intent, on the record"))
    reject.add_argument("intent_id")
    reject.add_argument("--reason", required=True)
    reject.add_argument("--owner", default=OWNER)
    reject.set_defaults(func=cmd_intents_reject)

    client = sub.add_parser("client", help="the people who write in")
    client_sub = client.add_subparsers(dest="client_command", required=True)
    add_client = client_sub.add_parser(
        "add", help="record a client who asked for a price, and the enquiry")
    add_client.add_argument("--id", required=True,
                            help="how this client is referred to, e.g. "
                                 "client:northstar")
    add_client.add_argument("--company", required=True)
    add_client.add_argument("--industry", required=True)
    add_client.add_argument("--state", required=True,
                            help="where the client is, for the location rules")
    add_client.add_argument("--email", required=True,
                            help="the address they wrote from")
    add_client.add_argument("--contact", default="",
                            help="the person's name, if they gave one")
    add_client.add_argument("--wants", required=True,
                            help="the skill they asked about")
    add_client.add_argument("--budget-cents", type=int, default=0,
                            dest="budget_cents",
                            help="what they said they could spend, if they said")
    add_client.add_argument("--seen-on", required=True, dest="seen_on",
                            help="the date they wrote, YYYY-MM-DD")
    add_client.add_argument("--source", default="inbox",
                            help="the registered source to enter the enquiry "
                                 "under")
    add_client.add_argument("--owner", default=OWNER)
    add_client.add_argument("--db", default=rt.DEFAULT_DB)
    add_client.set_defaults(func=cmd_client_add)

    payments = sub.add_parser("payments", help="payment rail operations")
    payments_sub = payments.add_subparsers(dest="payments_command", required=True)
    ingest = payments_sub.add_parser(
        "ingest", help="verify spooled webhook deliveries and apply them")
    ingest.add_argument("--spool", default="")
    ingest.add_argument("--db", default=rt.DEFAULT_DB)
    ingest.set_defaults(func=cmd_payments_ingest)

    state = sub.add_parser("state", help="print the Project State projection")
    state.add_argument("job_id")
    state.add_argument("--db", default=rt.DEFAULT_DB)
    state.set_defaults(func=cmd_state)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
