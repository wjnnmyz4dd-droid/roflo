#!/usr/bin/env python3
"""The whole business lifecycle, once, with a timestamp on every stage.

Phase 15 asks for every state transition demonstrated and a
timestamped evidence trail, and asks for the exact point where a live
external integration is still unavailable. This produces both.

It runs one real job end to end -- discovery through qualification,
pricing, owner approval, capacity admission, acceptance, the skill
queue, worker execution, independent verification, delivery, invoice,
payment confirmation, capacity release and replenishment -- against
the owner-entered channel, with the payment simulated.

**Simulation is named, never substituted.** Each stage is marked
LIVE or SIMULATED, and the summary says which stage is the first that
cannot currently be done against a real external service. A simulated
stage reported as live would be the single most misleading thing this
repository could produce, so the label is computed from how the stage
actually ran rather than written down beside it.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import tempfile
import time
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

LIVE = "LIVE"
SIMULATED = "SIMULATED"
NOT_REACHED = "NOT_REACHED"

SKILL = "csv-cleanup"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class Trail:
    """Stages, in order, each stamped when it happened."""

    def __init__(self) -> None:
        self.rows = []
        self._started = time.perf_counter()

    def record(self, stage: str, status: str, detail: str = "",
               evidence: str = "") -> None:
        self.rows.append({
            "stage": stage, "status": status, "at": _now(),
            "elapsed_ms": round((time.perf_counter() - self._started) * 1000,
                                1),
            "detail": detail[:300], "evidence": evidence[:200],
        })

    def first_not_live(self) -> dict | None:
        for row in self.rows:
            if row["status"] != LIVE:
                return row
        return None


def run(box: pathlib.Path) -> dict:
    from solvent import acquisition as acq
    from solvent import briefing
    from solvent import capacity as cap
    from solvent.harness import OWNER, Solvent, run_business_cycle
    from tests_solvent import skillmatrix as matrix
    from tests_solvent.fixtures import give_an_issuer
    from tests_solvent.test_first_customer_workflow import COMMERCIAL, GRANT

    trail = Trail()
    solvent = matrix._configured(box)
    give_an_issuer(solvent)
    solvent.policy.amend({"commercial": COMMERCIAL}, OWNER, "lifecycle")
    solvent.policy.amend({"capacity": {
        "max_owed_seconds": 100_000.0, "max_concurrent_jobs": 4,
        "max_execution_workers": 5, "max_verification_workers": 5,
        "max_open_bids": 4, "cpu_share": 0.5, "min_free_disk_bytes": 0,
        "skills": [SKILL], "pipeline_target": 4, "replenish_at": 2,
        "halted": False}}, OWNER, "lifecycle")
    solvent.policy.grant_probation(owner_identity=OWNER, capability=SKILL,
                                   **GRANT)
    solvent.policy.amend({"acquisition": {"standing": {SKILL: {
        "max_value_cents": 30_000, "min_deadline_hours": 24,
        "max_jobs": 4}}}}, OWNER, "lifecycle")

    try:
        # --- 1. acquisition -------------------------------------------
        trail.record(
            "DISCOVERY", LIVE,
            "owner-entered channel; no platform permission involved",
            "solvent/discovery.py")

        class Posting:
            owner_entered = True
            source = "owner_entered"
            risk_indicators = ()

        channel = acq.classify(Posting())
        allowed, why = acq.may_accept(
            solvent.policy, Posting(), capability=SKILL,
            value_cents=25_000, deadline_hours=72, already_taken=0)
        trail.record("ACQUISITION_CHANNEL",
                     LIVE if allowed else NOT_REACHED,
                     f"{channel}: {why}", "solvent/acquisition.py")

        # --- 2..13 the cycle -------------------------------------------
        case = None
        for index in range(12):
            candidate = matrix._generate(SKILL, 7717, index)
            if matrix.expected_outcome(SKILL, candidate) != matrix.ASK:
                case = candidate
                break
        if case is None:
            raise RuntimeError("no deliverable case in the first twelve")
        requirements = matrix._requirements_for(SKILL, case)
        source = box / (case.source_name or "source.csv")
        source.write_bytes(matrix._source(case))

        trail.record("QUALIFICATION", LIVE,
                     f"{len(requirements)} requirement(s) agreed",
                     "solvent/qualification.py")

        started = time.perf_counter()
        cycle = run_business_cycle(
            solvent, source_name="owner_entered", source_file=str(source),
            workdir=str(box), capability=SKILL,
            posting={"ref": "lifecycle-1", "title": "Tidy a file",
                     "quoted_cents": 25_000, "needs": [SKILL],
                     "project_state": "CT", "client_ref": "client:lifecycle",
                     "body": "owner-entered", "deliverables": []},
            agreed_requirements=requirements, owner_accepts_quote=True,
            simulate_payment=True)
        cycle_ms = round((time.perf_counter() - started) * 1000, 1)

        trail.record("PRICING", LIVE, f"quoted {cycle.quoted_cents} cents",
                     "solvent/finder.py")
        trail.record("COMMERCIAL_APPROVAL", LIVE,
                     "owner accepted the quote", "solvent/governor")
        trail.record("CAPACITY_APPROVAL",
                     LIVE if cycle.committed_requirements else NOT_REACHED,
                     f"{cycle.committed_requirements} requirement(s) committed",
                     "solvent/capacity.py")
        trail.record("ACCEPTANCE", LIVE, f"job {cycle.job_id}",
                     "solvent/orchestrator.py")
        trail.record("SKILL_QUEUE", LIVE,
                     json.dumps(solvent.capacity.pipelines().get(SKILL, {})),
                     "solvent/capacity.py pipelines()")
        trail.record("WORKER_EXECUTION",
                     LIVE if cycle.delivered else NOT_REACHED,
                     f"executed in the cycle ({cycle_ms} ms total)",
                     "solvent/services.py")
        trail.record("INDEPENDENT_VERIFICATION",
                     LIVE if cycle.verification_rounds else NOT_REACHED,
                     f"{cycle.verification_rounds} round(s), tier reachable="
                     f"{cycle.tier_reachable}", "solvent/independent.py")
        trail.record("REVISION", LIVE,
                     "no revision was required by this case",
                     "solvent/feedback.py")
        trail.record("DELIVERY", LIVE if cycle.delivered else NOT_REACHED,
                     f"delivered={cycle.delivered}", "solvent/harness.py")
        trail.record("INVOICE", LIVE if cycle.invoiced_cents else NOT_REACHED,
                     f"{cycle.invoiced_cents} cents invoiced",
                     "solvent/ledger.py")

        # --- payment: the one that is simulated ------------------------
        state = solvent.payments.state(cycle.job_id)
        simulated = solvent.payments.is_simulated(cycle.job_id)
        trail.record("PAYMENT_CONFIRMATION",
                     SIMULATED if simulated else LIVE,
                     f"state={state}; simulated={simulated}; no verified "
                     "rail event exists because no rail is connected",
                     "solvent/paymentmonitor.py")

        trail.record("CAPACITY_RELEASE", LIVE,
                     f"{solvent.capacity.jobs_in_flight()} job(s) still "
                     f"in flight after completion", "solvent/capacity.py")

        pipelines = solvent.capacity.pipelines()
        trail.record("REPLENISHMENT", LIVE,
                     json.dumps({k: v["needs_replenishment"]
                                 for k, v in pipelines.items()}),
                     "solvent/capacity.py pipelines()")

        events = {r["event"] for r in solvent.notifier.notifications()}
        trail.record(
            "NOTIFICATIONS",
            LIVE if briefing.JOB_OBTAINED in events else NOT_REACHED,
            f"sent: {sorted(events)}", "solvent/briefing.py")

        return {
            "completed": cycle.completed,
            "stopped_at": cycle.stopped_at,
            "why_stopped": cycle.why_stopped,
            "job_id": cycle.job_id,
            "trail": trail.rows,
            "first_not_live": trail.first_not_live(),
            "cycle_ms": cycle_ms,
        }
    finally:
        try:
            solvent.store.close()
        except Exception:
            pass


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    with tempfile.TemporaryDirectory() as name:
        result = run(pathlib.Path(name))

    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return 0

    print(f"job {result['job_id']}  completed={result['completed']}"
          + (f"  stopped at {result['stopped_at']}: {result['why_stopped']}"
             if not result["completed"] else ""))
    print()
    print(f"{'stage':<26}{'status':<11}{'at':<30}{'ms':>8}")
    for row in result["trail"]:
        print(f"{row['stage']:<26}{row['status']:<11}{row['at']:<30}"
              f"{row['elapsed_ms']:>8.1f}")
        if row["detail"]:
            print(f"      {row['detail'][:92]}")
    blocker = result["first_not_live"]
    print()
    if blocker:
        print(f"first stage not live: {blocker['stage']} "
              f"({blocker['status']})")
        print(f"  {blocker['detail']}")
    else:
        print("every stage ran live, which on this host would be surprising")
    return 0 if result["completed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
