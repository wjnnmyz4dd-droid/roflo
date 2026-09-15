"""Ledger — authoritative financial actuals.

Owns one decision: *what money actually moved?*

The Governor forecasts; the Ledger records. Keeping them apart is what stops a
forecast contaminating the books and stops the component that wants to say yes
from also keeping score.

Two rules do most of the work here:

* **Expected revenue is not collected revenue.** A payment reaches ``PAID`` only
  with an external verification method and timestamp. An invoice is not income.
* **Entries are immutable.** A correction is a new, linked entry — never an edit
  — so the history of what Solvent believed remains readable after it was wrong.

Learning may read this and may never write it. Calibration and autonomy evidence
are drawn from here precisely because Learning cannot reach it.
"""

from __future__ import annotations

from .audit import AuditLog, new_id, now
from .errors import FailClosed
from .store import Store
from .types import Cents, CostCategory, PaymentState

#: Cost sources Solvent trusts. A heuristic token count is not among them:
#: roflo's ``len(text)/4`` estimator must never reach the books.
TRUSTED_COST_SOURCES = frozenset({"PROVIDER_BILLING", "RECEIPT", "VERIFIED_PAYMENT",
                                  "OWNER_RECORDED", "SIMULATED_FIXTURE"})


class Ledger:
    """Financial truth. Append-oriented, idempotent, verification-gated."""

    def __init__(self, store: Store, audit: AuditLog) -> None:
        self._db = store.for_authority("ledger")
        self._audit = audit

    # ----------------------------------------------------------------- costs

    def record_cost(self, *, job_id: str, category: CostCategory, amount_cents: Cents,
                    source: str, note: str = "", entry_id: str | None = None,
                    corrects: str | None = None) -> str:
        """Record an incurred cost. ``source`` must be a trusted origin."""
        if source not in TRUSTED_COST_SOURCES:
            raise FailClosed(
                f"cost source {source!r} is not trusted; "
                f"expected one of {sorted(TRUSTED_COST_SOURCES)}"
            )
        if amount_cents < 0:
            raise FailClosed("costs are positive; a negative cost must be a correction")
        entry_id = entry_id or new_id("led")
        if self._db.query_one("SELECT id FROM ledger_entries WHERE id = ?", (entry_id,)):
            return entry_id  # idempotent: the same entry twice is one entry
        self._db.execute(
            "INSERT INTO ledger_entries(id,ts,job_id,category,amount_cents,direction,"
            "source,corrects,note) VALUES(?,?,?,?,?,?,?,?,?)",
            (entry_id, now(), job_id, category.value, amount_cents, "COST", source,
             corrects, note))
        self._db.commit()
        self._audit.record(
            event="ledger.cost", authority="ledger", initiator="ledger",
            why=note or f"cost {category.value}", job_id=job_id, input_ref=entry_id,
            result=f"{amount_cents} cents", source=source)
        return entry_id

    def correct(self, *, original_id: str, job_id: str, category: CostCategory,
                corrected_amount_cents: Cents, source: str, reason: str) -> str:
        """Correct an earlier entry by appending a delta. Never an edit."""
        row = self._db.query_one(
            "SELECT amount_cents FROM ledger_entries WHERE id = ?", (original_id,))
        if row is None:
            raise FailClosed(f"cannot correct unknown entry {original_id!r}")
        delta = corrected_amount_cents - int(row["amount_cents"])
        entry_id = new_id("led")
        self._db.execute(
            "INSERT INTO ledger_entries(id,ts,job_id,category,amount_cents,direction,"
            "source,corrects,note) VALUES(?,?,?,?,?,?,?,?,?)",
            (entry_id, now(), job_id, category.value, abs(delta),
             "COST" if delta >= 0 else "CREDIT", source, original_id, reason))
        self._db.commit()
        self._audit.record(
            event="ledger.correction", authority="ledger", initiator="ledger",
            why=reason, job_id=job_id, input_ref=entry_id,
            result=f"delta {delta} cents against {original_id}")
        return entry_id

    def predicted_costs_for(self, job_id: str) -> Cents:
        """Costs excluding deliberate reserves, for calibration only.

        Compared against the estimate's predicted portion so that an unspent
        allowance never reads as an estimation error.
        """
        rows = self._db.query(
            "SELECT direction, amount_cents, category FROM ledger_entries "
            "WHERE job_id = ?", (job_id,))
        return sum(int(r["amount_cents"]) * (1 if r["direction"] == "COST" else -1)
                   for r in rows if not CostCategory(r["category"]).is_allowance)

    def costs_for(self, job_id: str) -> Cents:
        rows = self._db.query(
            "SELECT direction, amount_cents FROM ledger_entries WHERE job_id = ?",
            (job_id,))
        return sum(int(r["amount_cents"]) * (1 if r["direction"] == "COST" else -1)
                   for r in rows)

    # -------------------------------------------------------------- payments

    def open_payment(self, *, job_id: str, amount_cents: Cents, rail: str,
                     payment_id: str | None = None) -> str:
        payment_id = payment_id or new_id("pay")
        self._db.execute(
            "INSERT INTO payments(id,job_id,state,amount_cents,collected_cents,rail,"
            "updated_at) VALUES(?,?,?,?,0,?,?)",
            (payment_id, job_id, PaymentState.ESTIMATED.value, amount_cents, rail, now()))
        self._db.commit()
        self._audit.record(
            event="payment.opened", authority="ledger", initiator="ledger",
            why="quote recorded", job_id=job_id, input_ref=payment_id,
            result=PaymentState.ESTIMATED.value)
        return payment_id

    def set_payment_state(self, payment_id: str, state: PaymentState, *,
                          collected_cents: Cents = 0, verification_method: str = "") -> None:
        """Advance a payment. Collection requires external verification.

        ``PAID`` and ``PARTIALLY_PAID`` are the only states that mean money
        arrived, so they are the only ones that demand proof. Without a
        verification method the call fails closed rather than inventing revenue.
        """
        row = self._db.query_one("SELECT * FROM payments WHERE id = ?", (payment_id,))
        if row is None:
            raise FailClosed(f"unknown payment {payment_id!r}")
        if state.is_collected and not verification_method:
            raise FailClosed(
                f"{state.value} requires an external verification method; "
                "expected revenue is not collected revenue"
            )
        verified_at = now() if state.is_collected else row["verified_at"]
        self._db.execute(
            "UPDATE payments SET state=?, collected_cents=?, verification_method=?, "
            "verified_at=?, updated_at=? WHERE id=?",
            (state.value, collected_cents or int(row["collected_cents"]),
             verification_method or row["verification_method"], verified_at,
             now(), payment_id))
        self._db.commit()
        self._audit.record(
            event="payment.state", authority="ledger", initiator="ledger",
            why=f"payment moved to {state.value}", job_id=row["job_id"],
            input_ref=payment_id, result=state.value,
            financial_authorization=verification_method)

    def payment_for(self, job_id: str) -> dict | None:
        row = self._db.query_one(
            "SELECT * FROM payments WHERE job_id = ? ORDER BY updated_at DESC", (job_id,))
        return dict(row) if row else None

    def collected_for(self, job_id: str) -> Cents:
        """Revenue that actually arrived and was externally verified."""
        row = self._db.query_one(
            "SELECT collected_cents, state, verification_method FROM payments "
            "WHERE job_id = ?", (job_id,))
        if row is None or not PaymentState(row["state"]).is_collected:
            return 0
        if not row["verification_method"]:
            return 0
        return int(row["collected_cents"])

    # ---------------------------------------------------------------- profit

    def actual_profit(self, job_id: str) -> dict:
        """Actual profit for a job, from collected revenue and recorded cost."""
        revenue = self.collected_for(job_id)
        cost = self.costs_for(job_id)
        profit = revenue - cost
        return {
            "job_id": job_id, "collected_revenue_cents": revenue,
            "actual_cost_cents": cost, "actual_profit_cents": profit,
            "margin": (profit / revenue) if revenue > 0 else 0.0,
        }

    def verified_job_ids(self) -> list[str]:
        """Jobs whose revenue was externally verified — the calibration sample."""
        return [r["job_id"] for r in self._db.query(
            "SELECT DISTINCT job_id FROM payments WHERE state IN (?,?) "
            "AND verification_method != ''",
            (PaymentState.PAID.value, PaymentState.PARTIALLY_PAID.value))]
