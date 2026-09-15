"""Command line interface.

``solvent doctor`` is the important one: it reports what is *measured* rather than
what is claimed, so the owner can tell an enforced control from a documented one.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import egress
from .harness import Solvent, run_acquisition_cycle, run_first_job
from .store import TABLE_OWNER


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

    metrics = sub.add_parser("metrics", help="print business metrics")
    metrics.add_argument("--db", default=":memory:")
    metrics.set_defaults(func=cmd_metrics)

    demo = sub.add_parser("demo", help="run the complete-job harness (fixtures only)")
    demo.add_argument("--state", default="CA", help="project jurisdiction")
    demo.add_argument("--quote", default="900", help="quoted price in dollars")
    demo.set_defaults(func=cmd_demo)

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
