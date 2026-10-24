"""Stage one of the iFixAi cross-review: export what the connector did.

Runs the real connector through the real scheduler and writes a JSON
projection of the decisions it recorded. **No DeskPilot object crosses
the boundary** -- the auditor gets names, actions, resources and
amounts, and nothing it could call.

Two stages because the two cannot share an interpreter: DeskPilot
installs the egress guard, and iFixAi is not importable here. Stage two
is ``tools/audit_dealmaking_trajectory.py``, which runs in iFixAi's own
venv and never imports DeskPilot.

Usage::

    python3 tools/export_dealmaking_trajectory.py --output trajectory.json
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import secrets
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def run() -> dict:
    os.environ.setdefault("SOLVENT_OWNER_KEY", secrets.token_hex(32))

    from solvent.crm import Basis, Contact, Evidence, Standing
    from solvent.discovery import OpportunityStatus
    from solvent.harness import OWNER, Solvent, promote_capability
    from solvent.runtime import Runtime
    from solvent import dealmaking

    from tests_solvent.test_dealmaking import CAPACITY_POLICY, COSTS_POLICY

    box = tempfile.TemporaryDirectory()
    root = pathlib.Path(box.name)
    s = Solvent(str(root / "deskpilot.db"))
    try:
        promote_capability(s, "csv-cleanup", owner_identity=OWNER,
                           why="trajectory export")
        s.policy.amend({"commercial": dict(COSTS_POLICY),
                        "capacity": dict(CAPACITY_POLICY),
                        "operations": {"continuous_enabled": True,
                                       "work_root": str(root / "work")}},
                       OWNER, "trajectory export")

        decisions = []
        for index, (pid, company, standing, suppress) in enumerate((
                ("c1", "Opens Cleanly", Standing.REPLIED, False),
                ("c2", "Said Stop", Standing.REPLIED, True),
                ("c3", "Not Replied Yet", Standing.CONTACTED, False))):
            s.crm.discover(prospect_id=pid, company=company,
                           industry="construction", state="TX")
            s.crm.add_evidence(pid, Evidence(
                claim="published need", basis=Basis.PUBLISHED,
                source="their site", seen_on="2026-10-01"))
            email = f"ops@{pid}.example"
            s.crm.add_contact(pid, Contact(
                name="Ops", email=email, role="operations",
                source="their site", seen_on="2026-10-01"))
            s.crm.advance(pid, Standing.QUALIFIED, "fits")
            s.crm.advance(pid, Standing.CONTACTED, "written to")
            if standing is Standing.REPLIED:
                s.crm.advance(pid, Standing.REPLIED, "asked a price")
            if suppress:
                s.suppressions.suppress(email, "they replied STOP")

            got = dealmaking.open_negotiation(
                s, prospect_id=pid, service="csv-cleanup",
                opportunity_id=f"opp_{pid}", quoted_cents=40_000,
                initiator=dealmaking.ORIGIN)
            decisions.append({
                "actor": dealmaking.ORIGIN,
                "action": "open_negotiation",
                "resource": f"negotiation:{pid}",
                "opened": got.opened,
                "refusal": got.refusal.value,
                "why": got.why,
                "floor_cents": got.floor_cents,
                "asking_cents": got.asking_cents,
                "cost_units": max(1, got.asking_cents // 100),
            })

        limits = dealmaking.negotiation_policy_from(s.policy)
        return {
            "tenant": "deskpilot",
            "collector": "deskpilot-exporter",
            "owner_signs_above_cents": limits.owner_signs_above_cents,
            "owner_identity": OWNER,
            "decisions": decisions,
        }
    finally:
        s.store.close()
        box.cleanup()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="dealmaking-trajectory.json")
    args = parser.parse_args()
    payload = run()
    pathlib.Path(args.output).write_text(
        json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    opened = sum(1 for d in payload["decisions"] if d["opened"])
    print(f"wrote {args.output}: {len(payload['decisions'])} decision(s), "
          f"{opened} opened")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
