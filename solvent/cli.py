"""Command line interface.

``solvent doctor`` is the important one: it reports what is *measured* rather than
what is claimed, so the owner can tell an enforced control from a documented one.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys

from . import egress
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
    metrics.add_argument("--db", default=":memory:")
    metrics.set_defaults(func=cmd_metrics)

    status = sub.add_parser("status", help="business status dashboard")
    status.add_argument("--db", default=":memory:")
    status.set_defaults(func=cmd_status)

    readiness = sub.add_parser("readiness",
                               help="what blocks the first real paid job")
    readiness.add_argument("--db", default=":memory:")
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
    health.add_argument("--db", default=":memory:")
    health.add_argument("--always-on", action="store_true")
    health.set_defaults(func=cmd_health)

    recover = sub.add_parser("recover",
                             help="reconcile durable state after a crash or reboot")
    recover.add_argument("--db", default=":memory:")
    recover.set_defaults(func=cmd_recover)

    halt = sub.add_parser("halt", help="throw the kill switch (survives restarts)")
    halt.add_argument("--db", default=":memory:")
    halt.add_argument("--reason", default="owner halted Solvent")
    halt.set_defaults(func=cmd_halt)

    resume = sub.add_parser("resume", help="clear the kill switch")
    resume.add_argument("--db", default=":memory:")
    resume.add_argument("--reason", default="owner resumed Solvent")
    resume.set_defaults(func=cmd_resume)

    state = sub.add_parser("state", help="print the Project State projection")
    state.add_argument("job_id")
    state.add_argument("--db", default=":memory:")
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
