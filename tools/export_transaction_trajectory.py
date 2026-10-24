"""Stage one of the iFixAi cross-review of the connected transaction.

Runs the whole transaction -- the real connectors through the real
scheduler, as ``tests_solvent/test_connected_transaction.py`` does --
and writes a JSON projection of the steps that moved money or state.
**No DeskPilot object crosses the boundary**: the auditor gets names,
actions, resources and amounts, and nothing it could call.

Two stages because the two cannot share an interpreter. DeskPilot
installs the egress guard, and iFixAi is only importable from its own
checkout. Stage two is ``tools/audit_transaction_trajectory.py``.

What this one is for, specifically: the assignment's rule that *"the
final authorized contract price should be the single source of truth
for job admission"*. Expressed as a trajectory, that is a budget --
every cent the transaction moves is charged against the authorized
price, and a second pricing authority anywhere in the chain shows up
as an overrun. The clean run has to fit exactly; the corrupted copies
in stage two have to not.

Usage, from the repository root::

    python3 tools/export_transaction_trajectory.py --output traj.json
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def project(cls) -> dict:
    """Read the finished transaction off the shop that performed it."""
    solvent = cls.s
    engagement = solvent.chief_of_staff.engagements[cls.CLIENT]
    order = solvent.storefront.order(f"eng-{cls.CLIENT}")
    agreed = int(engagement.proposal.price_cents)
    deposit = int(engagement.collected_cents)
    balance = int(solvent.ledger.collected_for(cls.job_id))

    # The chain of custody for the price, in the order it was set and
    # then relied on. Each entry names who acted, on what, and how
    # many cents of the authorized price the step moved. Steps that
    # move no money still appear: a job admitted without an approval
    # is a finding even when it is free.
    steps = [
        {"actor": "clientintake", "action": "record_enquiry",
         "resource": f"prospect:{cls.CLIENT}", "cents": 0,
         "priced_at": 0},
        {"actor": "dealmaking", "action": "open_negotiation",
         "resource": f"negotiation:{cls.CLIENT}", "cents": 0,
         "priced_at": agreed},
        {"actor": "contracting", "action": "propose_contract",
         "resource": f"engagement:{cls.CLIENT}", "cents": 0,
         "priced_at": agreed},
        {"actor": "engagement", "action": "take_deposit",
         "resource": f"engagement:{cls.CLIENT}", "cents": deposit,
         "priced_at": agreed},
        {"actor": "contracting", "action": "admit_job",
         "resource": f"order:{order.order_id}", "cents": 0,
         "priced_at": int(order.offered_cents)},
        {"actor": "orchestrator", "action": "execute",
         "resource": f"job:{cls.job_id}", "cents": 0, "priced_at": 0},
        {"actor": "orchestrator", "action": "verify",
         "resource": f"job:{cls.job_id}", "cents": 0, "priced_at": 0},
        {"actor": "ledger", "action": "invoice",
         "resource": f"job:{cls.job_id}", "cents": 0,
         "priced_at": deposit + int(cls.due)},
        {"actor": "ledger", "action": "collect_payment",
         "resource": f"job:{cls.job_id}", "cents": balance,
         "priced_at": agreed},
        {"actor": "settlement", "action": "close_job",
         "resource": f"job:{cls.job_id}", "cents": 0, "priced_at": 0},
    ]

    events = solvent.audit.events()
    return {
        "tenant": "deskpilot",
        "collector": "deskpilot-exporter",
        "client_ref": cls.CLIENT,
        "job_id": cls.job_id,
        "order_id": order.order_id,
        "authorized_contract_cents": agreed,
        "deposit_cents": deposit,
        "balance_cents": balance,
        "real_revenue_cents": int(solvent.ledger.real_revenue_cents()),
        "audit_rows": len(events),
        "audit_chain_ok": bool(solvent.audit.verify_chain()[0]),
        "steps": steps,
    }


def run() -> dict:
    from tests_solvent import test_connected_transaction as whole

    case = whole.TheWholeTransaction
    case.CLIENT = whole.CLIENT
    captured: dict = {}
    teardown = case.tearDownClass.__func__

    def export_first(cls):
        captured["payload"] = project(cls)
        teardown(cls)

    case.tearDownClass = classmethod(export_first)

    suite = unittest.defaultTestLoader.loadTestsFromTestCase(case)
    result = unittest.TextTestRunner(verbosity=0, stream=sys.stderr).run(suite)
    if not result.wasSuccessful():
        raise SystemExit("the transaction did not complete; nothing to audit")
    return captured["payload"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="transaction-trajectory.json")
    args = parser.parse_args()
    payload = run()
    pathlib.Path(args.output).write_text(
        json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(f"wrote {args.output}: {len(payload['steps'])} step(s), "
          f"{payload['authorized_contract_cents']}c authorized, "
          f"{payload['deposit_cents'] + payload['balance_cents']}c moved")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
