"""Project State — a projection, not an authority.

Gives Solvent one coherent view of a job by reading the authorities that own each
part: workflow from the Orchestrator, money from the Ledger and the Governor,
evidence from the Audit Log, knowledge from Business Memory.

Two rules keep it from quietly becoming a thirteenth authority:

1. **Nothing writes into it.** It stores nothing and is rebuilt on every call.
2. **No gate may read from it.** The Governor reads cost from the Ledger, not from
   here. The moment a decision depends on the projection, the projection has
   become authoritative by accident — which is the most likely way this concept
   turns into a duplicate authority, so ``test_architecture`` asserts that no
   gate imports it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .types import fmt


@dataclass(slots=True)
class ProjectState:
    """A composed, read-only view. Every field names the authority it came from."""

    job_id: str
    title: str = ""
    client_id: str = ""
    state: str = ""
    blocked_on: str | None = None
    consequence: str = ""
    requirements: list[dict] = field(default_factory=list)
    unconfirmed_requirements: int = 0
    quoted: str = ""
    authorized_budget: str = ""
    incurred_cost: str = ""
    collected_revenue: str = ""
    actual_profit: str = ""
    payment_state: str = ""
    verification: str = ""
    governor_decisions: list[str] = field(default_factory=list)
    external_actions: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    audit_events: int = 0
    sources: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def project(*, job_id: str, orchestrator, ledger, governor, audit, gate=None,
            memory=None) -> ProjectState:
    """Compose the current picture from its authoritative sources."""
    job = orchestrator.job(job_id)
    profit = ledger.actual_profit(job_id)
    payment = ledger.payment_for(job_id) or {}
    decisions = governor.decisions_for(job_id)
    verified, detail = orchestrator.verification_satisfied(job_id)
    requirements = orchestrator.requirements(job_id)
    grants = governor._db.query(
        "SELECT authorized_cents, spent_cents FROM budget_grants WHERE job_id = ?",
        (job_id,))

    return ProjectState(
        job_id=job_id,
        title=job.title,
        client_id=job.client_id,
        state=job.state.value,
        blocked_on=job.blocked_on.value if job.blocked_on else None,
        consequence=job.consequence.value,
        requirements=[{"text": r["text"], "source": r["source"]} for r in requirements],
        unconfirmed_requirements=sum(
            1 for r in requirements if r["source"] == "MODEL_EXTRACTED_UNCONFIRMED"),
        quoted=fmt(job.quoted_cents),
        authorized_budget=fmt(sum(int(g["authorized_cents"]) for g in grants)),
        incurred_cost=fmt(profit["actual_cost_cents"]),
        collected_revenue=fmt(profit["collected_revenue_cents"]),
        actual_profit=fmt(profit["actual_profit_cents"]),
        payment_state=payment.get("state", "NONE"),
        verification=f"{'satisfied' if verified else 'INSUFFICIENT'}: {detail}",
        governor_decisions=[f"{d['verdict']}: {d['reason']}" for d in decisions],
        external_actions=[
            f"{'ALLOW' if a['allowed'] else 'DENY'} {a['action_class']} -> "
            f"{a['destination']}{' (SIMULATED)' if a['simulated'] else ''}"
            for a in (gate.requests_for(job_id) if gate else [])],
        blockers=[e["why"] for e in audit.events(job_id=job_id)
                  if e["event"] in ("job.blocked", "job.timeout")
                  or e["decision"] == "ESCALATE"],
        audit_events=len(audit.events(job_id=job_id)),
        sources={
            "state": "orchestrator", "money": "ledger", "budget": "governor",
            "evidence": "audit", "knowledge": "memory" if memory else "n/a",
        },
    )
