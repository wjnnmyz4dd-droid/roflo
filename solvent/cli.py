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
from .errors import FailClosed
from .harness import OWNER, Solvent, run_acquisition_cycle, run_first_job
from .store import TABLE_OWNER
from .types import OperatingMode


def cmd_doctor(args: argparse.Namespace) -> int:
    """Report measured enforcement, not intentions."""
    solvent = Solvent()
    print("Solvent readiness")
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
    """Register the owner's promotion of csv-cleanup/1.0 in this database.

    The decision was made in OD-12 and widened in OD-14; this applies it to the
    deployment the owner actually runs. Before recording anything it re-runs the
    verifier's certification against the *current* implementation, so a scope
    the evidence no longer supports cannot be registered by rerunning a command.
    """
    from .harness import CSV_PROMOTION, provision_capability

    solvent = _owner_solvent(args)
    try:
        record = provision_capability(solvent, CSV_PROMOTION,
                                      owner_identity=args.owner)
    except FailClosed as exc:
        print(f"refused: {exc}")
        return 1
    permitted, why = solvent.capability.may_deploy(CSV_PROMOTION.name)
    if not permitted:
        # Provisioning is only done when the capability can actually be
        # delivered. Reporting success while delivery would refuse is the
        # failure this command existed to prevent and did not.
        print(f"refused: provisioning completed but delivery would still be "
              f"refused: {why}")
        return 1
    print(f"registered {CSV_PROMOTION.version} as proven, covering "
          f"{len(CSV_PROMOTION.covers)} certified check(s).")
    print(f"  verifier {record['verifier_ref']} @ {record['fingerprint']}")
    print("  Nothing else was promoted. report-builder remains unregistered.")
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
            print("  payment when it arrives. Solvent sends nothing itself.")
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
    print("Solvent business status")
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
            print(f"  {key:<28} {metrics[key]}")
        print()
    for caveat in metrics["caveats"]:
        print(f"!! {caveat}")
    return 0


def cmd_laws(args: argparse.Namespace) -> int:
    """Print the laws and where each is enforced in code."""
    print("  Solvent exists to FIND, QUALIFY, COMPLETE, DELIVER and PROFIT FROM")
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
        ("solvent may not expand itself", "capability.register owner check"),
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
        print(f"solvent did not start: {refusal}", file=sys.stderr)
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
        prog="solvent", description="Owner-governed business operating system.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="report measured enforcement").set_defaults(
        func=cmd_doctor)
    sub.add_parser("laws", help="print the mission, laws, and where each is enforced"
                   ).set_defaults(func=cmd_laws)
    sub.add_parser("acquire", help="run one acquisition cycle (fixtures only)"
                   ).set_defaults(func=cmd_acquire)
    sub.add_parser("services", help="print the service-selection scorecard"
                   ).set_defaults(func=cmd_services)

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

    demo = sub.add_parser("demo", help="run the complete-job harness (fixtures only)")
    demo.add_argument("--state", default="CA", help="project jurisdiction")
    demo.add_argument("--quote", default="900", help="quoted price in dollars")
    demo.set_defaults(func=cmd_demo)

    run = sub.add_parser("run", help="run Solvent as a long-lived service")
    run.add_argument("--db", default=rt.DEFAULT_DB)
    run.add_argument("--always-on", action="store_true",
                     help="production mode: refuse to start without durable storage")
    run.add_argument("--tick", type=float, default=1.0, help="loop interval seconds")
    run.add_argument("--max-ticks", type=int, default=None,
                     help="stop after N ticks (testing)")
    run.set_defaults(func=cmd_run)

    health = sub.add_parser("health", help="what this Solvent can currently do")
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
        help="record that a tax reference exists. Takes no value: Solvent "
             "stores presence, never the number")
    contracting.add_argument("--reason", default="OD-1: owner contracting details")
    contracting.set_defaults(func=cmd_setup_contracting)

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

    capability = owned(setup_sub.add_parser(
        "capability", help="register the owner's promotion of csv-cleanup/1.0"))
    capability.set_defaults(func=cmd_setup_capability)

    stripe = owned(setup_sub.add_parser(
        "stripe", help="advance the payment rail (evidence, never a credential)"))
    stripe.add_argument("state", choices=["SELECTED", "ENGINEERING_READY",
                                          "CONFIGURED", "LIVE_VERIFIED"])
    stripe.add_argument("--evidence", default="",
                        help="what the owner did or saw. Required for "
                             "CONFIGURED and LIVE_VERIFIED. Never a credential")
    stripe.add_argument("--reason", default="OD-2: payment rail progress")
    stripe.set_defaults(func=cmd_setup_stripe)

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
