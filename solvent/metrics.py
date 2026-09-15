"""Business metrics — a projection, not an authority.

Solvent's purpose is to find, complete and profit from client work, so these are
the numbers that say whether it is working. They are composed read-only from the
authorities that own them; nothing here stores anything, and no gate reads from
here.

**The primary metric is verified profitable client work.** Opportunities
discovered, jobs accepted and revenue invoiced are all secondary: they can rise
while the business loses money. Collected revenue from verified payments is the
only figure that means anything happened.

Simulated money is excluded by construction, not by convention — the Ledger keeps
fixture verifications behind a marker, so a demo can never inflate these numbers.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .types import JobState, fmt


@dataclass(slots=True)
class BusinessMetrics:
    """What the business actually did. Every field names its source authority."""

    # Acquisition (Discovery, Qualification)
    opportunities_discovered: int = 0
    opportunities_by_status: dict = field(default_factory=dict)
    rejected_by_reason: dict = field(default_factory=dict)
    jobs_accepted: int = 0
    jobs_rejected: int = 0

    # Delivery (Orchestrator)
    jobs_completed: int = 0
    jobs_failed: int = 0
    jobs_active: int = 0
    jobs_blocked: int = 0
    jobs_overdue: int = 0

    # Money (Ledger) — the part that counts
    revenue_invoiced: str = "$0.00"
    revenue_collected: str = "$0.00"
    real_revenue_collected: str = "$0.00"
    actual_costs: str = "$0.00"
    actual_profit: str = "$0.00"
    actual_margin: float = 0.0

    # Quality and learning
    estimate_accuracy: str = "no verified samples"
    verification_failures: int = 0
    lessons_learned: int = 0

    # Concentration risk
    client_concentration: dict = field(default_factory=dict)
    source_concentration: dict = field(default_factory=dict)

    headline: str = ""
    caveats: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def business_metrics(*, orchestrator, ledger, governor, audit, discovery=None,
                     qualification=None, memory=None) -> BusinessMetrics:
    """Compose the business picture from its authoritative sources."""
    metrics = BusinessMetrics()
    jobs = orchestrator.jobs()

    metrics.jobs_completed = sum(1 for j in jobs if j.state is JobState.COMPLETE)
    metrics.jobs_failed = sum(1 for j in jobs if j.state is JobState.FAILED)
    metrics.jobs_rejected = sum(1 for j in jobs if j.state is JobState.REJECTED)
    metrics.jobs_blocked = sum(1 for j in jobs if j.state is JobState.BLOCKED)
    metrics.jobs_active = sum(1 for j in jobs if not j.state.is_terminal)
    metrics.jobs_accepted = sum(
        1 for j in jobs if j.state not in (JobState.INTAKE, JobState.QUALIFYING,
                                           JobState.REJECTED))
    metrics.jobs_overdue = len(orchestrator.overdue())

    if discovery is not None:
        metrics.opportunities_by_status = discovery.counts()
        metrics.opportunities_discovered = sum(metrics.opportunities_by_status.values())
        sources: dict[str, int] = {}
        for opportunity in discovery.opportunities():
            sources[opportunity["source"]] = sources.get(opportunity["source"], 0) + 1
        metrics.source_concentration = sources
    if qualification is not None:
        metrics.rejected_by_reason = qualification.rejection_reasons()

    invoiced = collected = costs = 0
    clients: dict[str, int] = {}
    for job in jobs:
        payment = ledger.payment_for(job.id) or {}
        invoiced += int(payment.get("amount_cents", 0) or 0)
        job_collected = ledger.collected_for(job.id)
        collected += job_collected
        costs += ledger.costs_for(job.id)
        if job_collected:
            clients[job.client_id] = clients.get(job.client_id, 0) + job_collected

    profit = collected - costs
    metrics.revenue_invoiced = fmt(invoiced)
    metrics.revenue_collected = fmt(collected)
    metrics.real_revenue_collected = fmt(ledger.real_revenue_cents())
    metrics.actual_costs = fmt(costs)
    metrics.actual_profit = fmt(profit)
    metrics.actual_margin = (profit / collected) if collected else 0.0
    metrics.client_concentration = {k: fmt(v) for k, v in clients.items()}

    calibration = governor.calibration_for()
    samples = governor._samples("general")
    metrics.estimate_accuracy = (
        f"K={calibration:.2f} from {len(samples)} verified sample(s)"
        if samples else f"K={calibration:.2f} (bootstrap; no verified samples)")

    metrics.verification_failures = sum(
        1 for e in audit.events() if e["event"] == "verification.recorded"
        and e["result"] == "FAIL")
    if memory is not None:
        metrics.lessons_learned = len(memory.recall())

    real = ledger.real_revenue_cents()
    metrics.headline = (
        f"verified profitable client work: {fmt(real)} collected across "
        f"{metrics.jobs_completed} completed job(s)")
    if collected and not real:
        metrics.caveats.append(
            "All collected revenue is SIMULATED. Real revenue is $0.00.")
    if metrics.jobs_overdue:
        metrics.caveats.append(
            f"{metrics.jobs_overdue} job(s) past their state timeout and escalated.")
    if not samples:
        metrics.caveats.append(
            "Cost estimates are uncalibrated: no verified job outcomes yet.")
    return metrics
