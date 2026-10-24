"""Turning a verified payment into a closed job. A connector, not an authority.

The last link of the money loop, and it was missing. A verified
payment event reached the Ledger and stopped there: the payment row
read PAID and the job stayed in AWAITING_PAYMENT for ever. So a shop
that delivered, invoiced and was paid still showed nothing completed,
the customer's order never reached CLOSED, and the profit the Ledger
could now compute was never recorded against the work.

Its own module rather than a function in the runtime, because
``test_runtime.TheRuntimeIsNotAnAuthority`` forbids the scheduler
from containing ``.complete(`` at all -- and that guard is right.
The scheduler schedules. This is the same shape as
:mod:`solvent.dealmaking` and :mod:`solvent.contracting`: a join
between two authorities that decides nothing itself.

**It asserts nothing about money.** ``Orchestrator.complete`` asks
the Ledger and refuses a job with no externally verified collection
-- *"the caller does not get to assert that it was paid"*. This only
offers it the jobs worth asking about.
"""

from __future__ import annotations

from .types import JobState

#: Written into every audit record this module makes.
ORIGIN = "settlement"


def settle_paid_jobs(solvent, *, initiator: str) -> list[str]:
    """Close the jobs the Ledger says have been paid for.

    Idempotent, and quiet when there is nothing: a job already
    COMPLETE is not in AWAITING_PAYMENT and is never considered.
    """
    notes: list[str] = []
    ledger = getattr(solvent, "ledger", None)
    if ledger is None:
        return notes
    for job in solvent.orchestrator.jobs():
        if job.state is not JobState.AWAITING_PAYMENT:
            continue
        owed = int((ledger.payment_for(job.id) or {}).get("amount_cents") or 0)
        collected = ledger.collected_for(job.id)
        # Fully paid, not merely paid. The Orchestrator refuses a
        # short payment too -- both checks are deliberate, because
        # this one decides what is worth asking about and that one is
        # the rule. Measured before either existed: a client who paid
        # half closed the job and the rest stopped being chased.
        if collected <= 0 or (owed and collected < owed):
            continue
        try:
            solvent.orchestrator.complete(job_id=job.id, initiator=initiator)
        except Exception as problem:
            # Recorded, never swallowed. A job the Ledger says is
            # paid and the Orchestrator will not close is exactly
            # the disagreement an owner has to be told about.
            solvent.audit.record(
                event="payment.settlement_refused", authority=ORIGIN,
                initiator=initiator, job_id=job.id,
                why=f"{type(problem).__name__}: {problem}"[:200],
                decision="NOT_COMPLETED")
            notes.append(f"{job.id} is paid and would not close: {problem}")
            continue
        notes.append(f"{job.id} closed; the Ledger verified payment")
    return notes
