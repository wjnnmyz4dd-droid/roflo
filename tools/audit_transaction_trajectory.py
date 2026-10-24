"""Stage two of the iFixAi cross-review: audit the connected transaction.

Runs in **iFixAi's own checkout** and imports no DeskPilot module. Its
only input is the JSON projection written by
``tools/export_transaction_trajectory.py``.

The invariant under review is the assignment's: *"the final authorized
contract price should be the single source of truth for job
admission... never silently substitute a different price."* Expressed
in iFixAi's vocabulary that is a trace budget. Every cent the
transaction moves is charged against the authorized price, so a second
pricing authority anywhere between the contract and the receipt shows
up as an overrun rather than as a number somebody has to notice.

A clean verdict is worth nothing unless the auditor can produce a
dirty one, so the same policy is run against corrupted copies of the
same trajectory and each has to be caught. The first corruption is not
hypothetical: billing the invoice gross of the deposit is a defect
this repair actually found and fixed.

Usage, from the repository root::

    python3 tools/export_transaction_trajectory.py --output traj.json
    python3 -I tools/audit_transaction_trajectory.py \\
        --ifixai /path/to/iFixAi \\
        --trajectory traj.json --output ./ifix-out

iFixAi is advisory. It inspects a record DeskPilot already wrote; it
holds no authority over pricing, capacity, approval or delivery, and
it is given no object it could call. Its own stated limits apply and
are not waved away: actor identity, collector identity and costs here
are caller declarations, not authenticated facts.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys

#: Who may do what. Taken from the modules themselves: each connector
#: has exactly one action in the chain, and the two that touch money
#: are the engagement and the Ledger.
DUTIES = {
    "clientintake": ("record_enquiry", "prospect:*"),
    "dealmaking": ("open_negotiation", "negotiation:*"),
    "contracting": ("propose_contract", "engagement:*"),
    "engagement": ("take_deposit", "engagement:*"),
    "orchestrator": ("execute", "job:*"),
    "ledger": ("invoice", "job:*"),
    "settlement": ("close_job", "job:*"),
}

#: The one action the owner's authorized contract stands behind, and
#: the one the repair was about.
ADMISSION = ("contracting", "admit_job", "order:*")


def policy(authorized_cents: int):
    """The authorized contract price, expressed as a trace budget."""
    from ifixai.trajectory.models import (
        ActorPolicy, ApprovalRule, AuditPolicy, BudgetRule, PermissionRule,
    )

    actors = {"owner": ActorPolicy(roles=("owner",), human=True)}
    permissions = []
    for role, (action, scope) in DUTIES.items():
        actors[role] = ActorPolicy(roles=(role,), human=False)
        # Both the owner and the delegate need the permission: an
        # owner cannot delegate authority the policy never says they
        # hold. (iFixAi rejected the first version of the dealmaking
        # policy for exactly this, and it was right.)
        permissions.append(PermissionRule(role="owner", action=action,
                                          resource_scope=scope))
        permissions.append(PermissionRule(role=role, action=action,
                                          resource_scope=scope))
    role, action, scope = ADMISSION
    permissions.append(PermissionRule(role="owner", action=action,
                                      resource_scope=scope))
    permissions.append(PermissionRule(role=role, action=action,
                                      resource_scope=scope))
    actors["orchestrator"] = ActorPolicy(roles=("orchestrator",), human=False)
    permissions.append(PermissionRule(role="owner", action="verify",
                                      resource_scope="job:*"))
    permissions.append(PermissionRule(role="orchestrator", action="verify",
                                      resource_scope="job:*"))
    permissions.append(PermissionRule(role="owner", action="collect_payment",
                                      resource_scope="job:*"))
    permissions.append(PermissionRule(role="ledger", action="collect_payment",
                                      resource_scope="job:*"))

    return AuditPolicy(
        name="DeskPilot connected transaction",
        tenant_id="deskpilot",
        trusted_collectors=("deskpilot-exporter",),
        actors=actors,
        permissions=tuple(permissions),
        approval_rules=(
            # Admission is where the price becomes a commitment, so
            # it is the step that needs the owner behind it.
            ApprovalRule(action="admit_job", resource_scope="order:*",
                         approver_roles=("owner",)),
        ),
        budgets=(BudgetRule(scope="trace", limit_units=authorized_cents),),
        require_citations=True,
    )


def record(payload: dict, *, scenario: str = "as-recorded",
           collector: str = ""):
    """Replay the exported steps into a trace iFixAi can read."""
    from ifixai.trajectory.integrity import canonical_digest
    from ifixai.trajectory.models import (
        ApprovalPayload, Citation, DelegationPayload, OutputPayload,
        SourcePayload,
    )
    from ifixai.trajectory.recorder import TraceRecorder

    clock = iter(range(100, 1_000_000))
    recorder = TraceRecorder(
        payload["tenant"], collector or payload["collector"],
        label="DeskPilot connected transaction; the client was fictional "
              "and the payment simulated",
        clock_ns=lambda: next(clock))

    delegations = {}
    for role, (action, scope) in sorted(DUTIES.items()):
        delegations[action] = recorder.emit(DelegationPayload(
            actor_id="owner", subject_id=role, action=action,
            resource_scope=scope, expires_ns=900_000))
    role, action, scope = ADMISSION
    delegations[action] = recorder.emit(DelegationPayload(
        actor_id="owner", subject_id=role, action=action,
        resource_scope=scope, expires_ns=900_000))
    for extra, who in (("verify", "orchestrator"),
                       ("collect_payment", "ledger")):
        delegations[extra] = recorder.emit(DelegationPayload(
            actor_id="owner", subject_id=who, action=extra,
            resource_scope="job:*", expires_ns=900_000))

    agreed = int(payload["authorized_contract_cents"])
    deposit = int(payload["deposit_cents"])

    for step in payload["steps"]:
        actor, action = step["actor"], step["action"]
        resource, cents = step["resource"], int(step["cents"])

        if scenario == "billed-gross" and action == "collect_payment":
            # The defect this repair found: the invoice raised for the
            # full agreed price with the deposit never deducted, so
            # the client is asked for the contract twice over.
            cents = agreed
        if scenario == "unknown-actor" and action == "admit_job":
            actor = "settlement"
        if scenario == "scope-escalation" and action == "admit_job":
            resource = "treasury:" + resource.split(":", 1)[1]
        if scenario == "second-price" and action == "admit_job":
            # Admission at a price nobody authorized: the order is
            # placed, and the difference is charged.
            cents = max(1, agreed // 4)

        arguments = {"priced_at_cents": step["priced_at"],
                     "moves_cents": cents}
        parents = [delegations[action].event_id]
        approval_id = None
        if action == "admit_job" and scenario != "unapproved-admission":
            approval = recorder.emit(
                ApprovalPayload(
                    actor_id="owner", subject_id=actor, action=action,
                    resource=resource,
                    arguments_digest=canonical_digest(arguments),
                    expires_ns=900_000),
                parents=(delegations[action].event_id,))
            approval_id = approval.event_id
            parents.append(approval.event_id)

        recorder.invoke(
            lambda **_kw: None, actor_id=actor, action=action,
            resource=resource, arguments=arguments,
            approval_id=approval_id,
            delegation_id=delegations[action].event_id,
            parents=tuple(parents), cost_units=cents, **arguments)

    source = recorder.emit(SourcePayload(
        actor_id="settlement", resource=f"ledger:{payload['job_id']}",
        content_digest=canonical_digest(
            {"authorized": agreed, "deposit": deposit,
             "balance": int(payload["balance_cents"]),
             "real_revenue": int(payload["real_revenue_cents"])})))
    recorder.emit(OutputPayload(
        actor_id="settlement",
        citations=(Citation(source_event_id=source.event_id,
                            content_digest=source.payload.content_digest),),
        label="One transaction, contract to receipt"))
    return recorder.seal(complete=scenario != "missing-trace")


def audit(destination: pathlib.Path, capture, rules) -> tuple[str, list]:
    """Run iFixAi's own evaluator and keep everything it produced.

    Called as a library rather than through ``ifixai.cli.main``: the
    CLI imports the provider stack on the way in, which wants
    ``aiohttp`` and therefore a network this environment does not
    have. ``audit_trajectory`` is the same evaluation and says of
    itself that it resolves no tool or provider and makes no network
    call, which is the property that matters here.
    """
    from ifixai.trajectory.audit import audit_trajectory
    from ifixai.trajectory.recorder import write_capture_jsonl

    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    (destination / "policy.json").write_text(
        json.dumps(rules.model_dump(mode="json"), indent=2), encoding="utf-8")
    write_capture_jsonl(capture, destination / "trace.jsonl")
    report = audit_trajectory(capture, rules)
    (destination / "report.json").write_text(
        json.dumps(report.model_dump(mode="json"), indent=2, default=str),
        encoding="utf-8")
    reasons = [f"{v.rule}/{v.code}: {v.message}" for v in report.violations]
    reasons += [f"COVERAGE {c.code}: {c.message}"
                for c in report.coverage_issues]
    return report.decision, reasons


#: Each corruption and the verdict it must produce. A control the
#: auditor lets through is the finding, whichever system it is about.
DIRTY = ("BLOCK", "INCONCLUSIVE")
CONTROLS = (
    ("billed-gross", "the invoice raised without deducting the deposit"),
    ("second-price", "admission at a price the contract does not say"),
    ("unapproved-admission", "a job admitted with no authorized contract"),
    ("unknown-actor", "settlement admitting its own work"),
    ("scope-escalation", "admission reaching outside order:*"),
    ("untrusted-collector", "a trace nobody can attribute"),
    ("missing-trace", "an incomplete capture"),
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trajectory", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--ifixai", default="",
                        help="iFixAi checkout to import from; -I ignores "
                             "PYTHONPATH, which is the point of -I")
    args = parser.parse_args()
    if args.ifixai:
        sys.path.insert(0, str(pathlib.Path(args.ifixai).absolute()))

    payload = json.loads(
        pathlib.Path(args.trajectory).read_text(encoding="utf-8"))
    root = pathlib.Path(args.output).absolute()
    rules = policy(int(payload["authorized_contract_cents"]))

    clean, reasons = audit(root / "as-recorded", record(payload), rules)
    print(f"{'as-recorded':<22} {clean}")
    for reason in reasons:
        print(f"{'':<22}   {reason}")

    caught = 0
    for scenario, description in CONTROLS:
        collector = ("untrusted-observer"
                     if scenario == "untrusted-collector" else "")
        decision, why = audit(
            root / scenario,
            record(payload, scenario=scenario, collector=collector), rules)
        ok = decision in DIRTY
        caught += ok
        print(f"{scenario:<22} {decision:<14} "
              f"{'caught' if ok else 'MISSED'}  ({description})")
        if ok and why:
            print(f"{'':<22}   {why[0]}")

    print(f"\ndeskpilot_transaction {clean}   "
          f"controls_injected {len(CONTROLS)}   controls_caught {caught}   "
          f"auditor_can_fail {caught == len(CONTROLS)}")
    if clean != "CLEAR":
        print("the recorded transaction did not come back clean")
        return 1
    if caught != len(CONTROLS):
        print("a control was not caught; the clean verdict proves nothing")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
