"""Financial Governor — the one financial decision authority.

Owns one decision: *is this specific spend or commitment financially acceptable?*

No worker, no Orchestrator, no Capability Registry, no Analyst, no Learning
system and not the Action Gate may override it. The Gate may independently deny
an action this authority approved; it may never allow one this authority denied.

**Calibration closes the estimator loophole by removing the writer.** Learning has
no path to any parameter used here. The Governor derives its own conservatism
factor *K* from verified Ledger actuals: the p80 of ``realised ÷ estimated``. An
optimistic estimator produces worse realised ratios, which raises *K*, which
tightens the gate — automatically, with no detector and no owner action. Three
corrections make it sound in practice:

* *K* multiplies only **uncertain** cost. Inflating a published platform fee by a
  1.4 uncertainty factor is nonsense and would reject good work for no reason.
* *K* is **stratified by job class**, so an improving easy-work stratum cannot
  loosen the gate for hard work — which is most of the selection-bias problem.
* Update lag cannot be removed, so **exposure is capped**: committed-but-unverified
  value has a ceiling, bounding the damage of a drift nobody has noticed yet.

Getting safer is automatic and immediate. Getting looser needs verified evidence,
a minimum sample count and an owner-set rate limit.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field

from .audit import AuditLog, new_id, now
from .errors import FailClosed, GovernorRefused
from .ledger import Ledger
from .policy import PolicyStore
from .pricing import PricingReference, resolve_context
from .store import Store
from .types import (
    Cents, CostCategory, CostLine, GovernorVerdict, LocationSignals, PricingContext,
    PricingTier, apply_factor, fmt,
)


@dataclass(slots=True)
class Estimate:
    """A forecast. Never confused with an actual — those live in the Ledger."""

    id: str
    job_id: str
    lines: list[CostLine]
    context: PricingContext
    known_cents: Cents
    estimated_cents: Cents
    calibration: float
    effective_cost_cents: Cents
    preliminary: bool
    allowance_cents: Cents = 0
    unresolved_material: list[CostCategory] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def point_cost_cents(self) -> Cents:
        """The uncalibrated total, before the conservative bound is applied."""
        return self.known_cents + self.estimated_cents

    @property
    def predicted_cost_cents(self) -> Cents:
        """The part of the estimate that is a *prediction*, not a reserve.

        Calibration measures error against this. Reserves are excluded so that an
        unspent safety margin never reads as an overestimate.
        """
        return self.point_cost_cents - self.allowance_cents


class FinancialGovernor:
    """The single money gate."""

    def __init__(self, store: Store, audit: AuditLog, policy: PolicyStore,
                 ledger: Ledger, pricing: PricingReference) -> None:
        self._db = store.for_authority("governor")
        self._audit = audit
        self._policy = policy
        self._ledger = ledger
        self._pricing = pricing

    # ---------------------------------------------------------- calibration

    def calibration_for(self, job_class: str = "general") -> float:
        row = self._db.query_one(
            "SELECT k FROM calibration_state WHERE job_class = ?", (job_class,))
        if row is not None:
            return float(row["k"])
        return float(self._policy.get("calibration", "k_bootstrap", default=1.5))

    def _samples(self, job_class: str) -> list[float]:
        """Realised ÷ estimated, over jobs whose revenue was externally verified.

        Unverified jobs are not evidence. This is verify-before-learn applied to
        the Governor's own self-measurement.
        """
        verified = set(self._ledger.verified_job_ids())
        window = int(self._policy.get("calibration", "window", default=50))
        ratios: list[float] = []
        rows = self._db.query(
            "SELECT job_id, known_cents, estimated_cents, allowance_cents "
            "FROM price_estimates WHERE job_class = ? ORDER BY ts DESC LIMIT ?",
            (job_class, window))
        for row in rows:
            if row["job_id"] not in verified:
                continue
            predicted = (int(row["known_cents"]) + int(row["estimated_cents"])
                         - int(row["allowance_cents"]))
            if predicted <= 0:
                continue
            actual = self._ledger.predicted_costs_for(row["job_id"])
            if actual <= 0:
                continue
            ratios.append(actual / predicted)
        return ratios

    @staticmethod
    def _percentile(values: list[float], p: float) -> float:
        """Nearest-rank percentile. Robust to the outliers a mean would absorb."""
        ordered = sorted(values)
        rank = max(1, math.ceil(p * len(ordered)))
        return ordered[min(rank, len(ordered)) - 1]

    def recalibrate(self, job_class: str = "general") -> dict:
        """Recompute K under asymmetric change control."""
        cfg = self._policy.get("calibration", default={})
        n_min = int(cfg.get("n_min", 5))
        bootstrap = float(cfg.get("k_bootstrap", 1.5))
        max_loosen = float(cfg.get("max_loosen_per_period", 0.10))
        degrade = float(cfg.get("degradation_threshold", 2.0))

        current = self.calibration_for(job_class)
        samples = self._samples(job_class)
        outcome = {"job_class": job_class, "n": len(samples), "previous": current}

        if len(samples) < n_min:
            # Too little evidence to relax. A business with no track record must
            # find clearly profitable work, not marginal work.
            outcome.update(k=max(current, bootstrap), applied=False,
                           reason=f"n={len(samples)} < n_min={n_min}; holding bootstrap")
            self._persist(job_class, outcome["k"], len(samples), outcome["reason"])
            return outcome

        raw = self._percentile(samples, float(cfg.get("percentile", 0.80)))

        if raw >= degrade:
            # Accuracy has degraded badly: revert to the punitive default and escalate.
            new_k, reason = max(raw, bootstrap), (
                f"accuracy degraded (p80={raw:.2f} >= threshold {degrade}); "
                "reverting to conservative calibration and escalating")
            self._audit.record(
                event="governor.calibration_degraded", authority="governor",
                initiator="governor", why=reason, decision=f"K={new_k:.3f}")
        elif raw >= current:
            new_k, reason = raw, f"tightening applies immediately (p80={raw:.3f})"
        else:
            floor = current * (1.0 - max_loosen)
            new_k = max(raw, floor)
            reason = (f"loosening rate-limited to {max_loosen:.0%}: "
                      f"p80={raw:.3f}, applied K={new_k:.3f}")

        outcome.update(k=new_k, applied=True, raw=raw, reason=reason)
        self._persist(job_class, new_k, len(samples), reason)
        self._audit.record(
            event="governor.recalibrated", authority="governor", initiator="governor",
            why=reason, decision=f"K {current:.3f} -> {new_k:.3f}", n=len(samples))
        return outcome

    def _persist(self, job_class: str, k: float, n: int, reason: str) -> None:
        self._db.execute(
            "INSERT INTO calibration_state(job_class,k,n,updated_at,reason) "
            "VALUES(?,?,?,?,?) ON CONFLICT(job_class) DO UPDATE SET "
            "k=excluded.k, n=excluded.n, updated_at=excluded.updated_at, "
            "reason=excluded.reason", (job_class, k, n, now(), reason))
        self._db.commit()

    # ------------------------------------------------------------- estimating

    def estimate(self, *, job_id: str, revenue_cents: Cents, signals: LocationSignals,
                 quantities: dict[CostCategory, float],
                 direct_lines: list[CostLine] | None = None,
                 job_class: str = "general") -> Estimate:
        """Build a cost estimate with location-aware, evidence-backed lines.

        Unresolvable *material* categories are collected rather than guessed; the
        decision step turns them into ``PRICING_LOCATION_UNKNOWN``.
        """
        categories = list(quantities)
        context = resolve_context(signals, categories)
        lines: list[CostLine] = list(direct_lines or [])
        notes: list[str] = []
        unpriced: list[CostCategory] = []

        for category, quantity in quantities.items():
            if not category.is_geographic:
                raise FailClosed(
                    f"{category.value} is not geographic; pass it as a direct line")
            line, problem = self._pricing.price_line(
                category, context.jurisdiction_for(category), quantity)
            if line is None:
                unpriced.append(category)
                notes.append(problem)
                continue
            violation = self._pricing.legal_floor_violation(line)
            if violation:
                raise FailClosed(f"legal floor violated: {violation}")
            lines.append(line)

        known = sum(l.amount for l in lines if not l.is_estimated)
        uncertain = sum(l.amount for l in lines if l.is_estimated)

        # Verification is part of the cost of doing the work, not an afterthought.
        # Including it here is what makes "a job that cannot afford to be verified
        # is not a job Solvent can safely do" a rejection rather than a loss.
        verification = self._policy.verification_budget(revenue_cents)
        if verification:
            lines.append(CostLine(category=CostCategory.VERIFICATION,
                                  amount=verification, note="policy budget for consequence tier"))
            uncertain += verification
        allowance = sum(l.amount for l in lines if l.category.is_allowance)

        k = self.calibration_for(job_class)
        effective = known + apply_factor(uncertain, k)
        preliminary = any(l.pricing_tier is PricingTier.FALLBACK for l in lines)

        material = self._material_unresolved(unpriced, revenue_cents, effective)
        estimate = Estimate(
            id=new_id("est"), job_id=job_id, lines=lines, context=context,
            known_cents=known, estimated_cents=uncertain, calibration=k,
            effective_cost_cents=effective, preliminary=preliminary,
            allowance_cents=allowance, unresolved_material=material, notes=notes)
        self._persist_estimate(estimate, job_class)
        return estimate

    def _material_unresolved(self, unpriced: list[CostCategory], revenue: Cents,
                             effective: Cents) -> list[CostCategory]:
        """Which unpriced categories actually matter.

        Without this test every quote would block on trivia. With it, only
        categories that could move the price meaningfully — and every legally
        governed category, at any size — stop a binding quote.
        """
        fraction = float(self._policy.get("pricing", "materiality_fraction", default=0.05))
        basis = max(revenue, effective, 1)
        material = []
        for category in unpriced:
            if category is CostCategory.PREVAILING_WAGE:
                material.append(category)  # legally governed: material at any size
                continue
            if category.is_geographic:
                # An unpriced geographic category has unknown magnitude; treat it
                # as material unless policy says the whole job is trivially small.
                if basis * fraction > 0:
                    material.append(category)
        return material

    def _persist_estimate(self, estimate: Estimate, job_class: str) -> None:
        self._db.execute(
            "INSERT INTO price_estimates(id,job_id,ts,lines,known_cents,estimated_cents,"
            "calibration,effective_cost_cents,pricing_context,preliminary,job_class,"
            "allowance_cents) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (estimate.id, estimate.job_id, now(),
             json.dumps([{"category": l.category.value, "amount": l.amount,
                          "tier": l.pricing_tier.name if l.pricing_tier else None,
                          "jurisdiction": l.jurisdiction.path() if l.jurisdiction else None,
                          "reference_id": l.reference_id, "note": l.note}
                         for l in estimate.lines]),
             estimate.known_cents, estimate.estimated_cents, estimate.calibration,
             estimate.effective_cost_cents,
             json.dumps({"bindings": {c.value: j.path()
                                      for c, j in estimate.context.bindings.items()},
                         "signals_used": estimate.context.signals_used,
                         "unresolved": [c.value for c in estimate.context.unresolved]}),
             1 if estimate.preliminary else 0, job_class, estimate.allowance_cents))
        self._db.commit()

    # --------------------------------------------------------------- deciding

    def decide(self, *, job_id: str, revenue_cents: Cents, estimate: Estimate,
               conformance_ok: bool, initiator: str = "governor") -> tuple[GovernorVerdict, str]:
        """The financial verdict. Final, and unappealable by any component."""
        if estimate.job_id != job_id:
            # Otherwise a cheap job's estimate could be laundered into an
            # expensive job's decision.
            raise FailClosed(
                f"estimate {estimate.id} was made for job {estimate.job_id!r}, "
                f"not {job_id!r}; an estimate may only decide its own job")
        mode = self._policy.operating_mode
        floor = float(self._policy.get("financial", "margin_floor", default=0.30))
        approval_above = int(self._policy.get(
            "financial", "owner_approval_above_cents", default=50_000))

        verdict, reason = self._evaluate(
            mode=mode, revenue=revenue_cents, estimate=estimate, floor=floor,
            approval_above=approval_above, conformance_ok=conformance_ok)

        margin = ((revenue_cents - estimate.effective_cost_cents) / revenue_cents
                  if revenue_cents > 0 else 0.0)
        decision_id = new_id("gov")
        self._db.execute(
            "INSERT INTO governor_decisions(id,job_id,ts,verdict,estimate_id,"
            "revenue_cents,effective_cost_cents,margin,floor,policy_version,"
            "calibration,reason) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (decision_id, job_id, now(), verdict.value, estimate.id, revenue_cents,
             estimate.effective_cost_cents, margin, floor, self._policy.version,
             estimate.calibration, reason))
        self._db.commit()
        self._audit.record(
            event="governor.decision", authority="governor", initiator=initiator,
            why=reason, job_id=job_id, input_ref=estimate.id, decision=verdict.value,
            financial_authorization=decision_id,
            revenue=fmt(revenue_cents), effective_cost=fmt(estimate.effective_cost_cents),
            margin=round(margin, 4), floor=floor, calibration=estimate.calibration,
            policy_version=self._policy.version)
        return verdict, reason

    def _evaluate(self, *, mode, revenue: Cents, estimate: Estimate, floor: float,
                  approval_above: int, conformance_ok: bool):
        """Ordered checks. The order encodes the laws, so it is not arbitrary."""
        # Conformance before price: a non-conforming option is not a cheap
        # option, it is not an option.
        if not conformance_ok:
            return GovernorVerdict.REJECT, "conformance not established; price is not evaluated"
        # Location before binding price.
        if estimate.unresolved_material:
            missing = ", ".join(c.value for c in estimate.unresolved_material)
            return (GovernorVerdict.PRICING_LOCATION_UNKNOWN,
                    f"material pricing jurisdiction unresolved: {missing}")
        if estimate.preliminary:
            return (GovernorVerdict.NEEDS_INFORMATION,
                    "estimate uses a national fallback line; not valid for a binding quote")
        if mode.blocks_spend:
            return (GovernorVerdict.REJECT,
                    f"operating_mode={mode.value} blocks spending")
        if revenue <= 0:
            return GovernorVerdict.NEEDS_INFORMATION, "no revenue established"

        exposure = self.committed_unverified_cents()
        cap = int(self._policy.get("financial", "max_committed_unverified_cents",
                                   default=150_000))
        if exposure + estimate.effective_cost_cents > cap:
            return (GovernorVerdict.HIGH_RISK,
                    f"committed-but-unverified exposure {fmt(exposure)} + "
                    f"{fmt(estimate.effective_cost_cents)} exceeds cap {fmt(cap)}")

        max_job = int(self._policy.get("financial", "max_job_spend_cents", default=25_000))
        if estimate.effective_cost_cents > max_job:
            return (GovernorVerdict.REQUIRES_APPROVAL,
                    f"estimated cost {fmt(estimate.effective_cost_cents)} exceeds "
                    f"per-job ceiling {fmt(max_job)}")

        margin = (revenue - estimate.effective_cost_cents) / revenue
        if margin < floor:
            return (GovernorVerdict.MARGIN_TOO_LOW,
                    f"margin {margin:.1%} below floor {floor:.0%} at K={estimate.calibration:.2f}")
        if revenue >= approval_above:
            return (GovernorVerdict.REQUIRES_APPROVAL,
                    f"value {fmt(revenue)} at or above owner-approval threshold "
                    f"{fmt(approval_above)}")
        return (GovernorVerdict.PROFITABLE,
                f"margin {margin:.1%} clears floor {floor:.0%} at K={estimate.calibration:.2f}")

    # ---------------------------------------------------------------- budget

    def grant_budget(self, *, job_id: str, decision_id: str,
                     authorized_cents: Cents) -> str:
        grant_id = new_id("grant")
        self._db.execute(
            "INSERT INTO budget_grants(id,job_id,ts,authorized_cents,decision_id) "
            "VALUES(?,?,?,?,?)", (grant_id, job_id, now(), authorized_cents, decision_id))
        self._db.commit()
        self._audit.record(
            event="budget.granted", authority="governor", initiator="governor",
            why="work authorised", job_id=job_id, financial_authorization=grant_id,
            result=fmt(authorized_cents))
        return grant_id

    def authorize_spend(self, *, grant_id: str, amount_cents: Cents, what: str,
                        job_id: str | None = None) -> tuple[bool, str]:
        """Checked before **every** spend — this is chokepoint one of two.

        The kill switch is enforced here rather than at job start, so a mode
        change stops work already in flight.
        """
        mode = self._policy.operating_mode
        if mode.blocks_spend:
            return False, f"operating_mode={mode.value} blocks spending"
        row = self._db.query_one("SELECT * FROM budget_grants WHERE id = ?", (grant_id,))
        if row is None:
            return False, f"no budget grant {grant_id!r}"
        if int(row["revoked"]):
            return False, "budget grant revoked"
        if job_id is not None and row["job_id"] != job_id:
            # Without this, one job's authorised budget funds another's work.
            return False, (f"grant {grant_id} belongs to job {row['job_id']!r}, "
                           f"not {job_id!r}")
        spent, authorized = int(row["spent_cents"]), int(row["authorized_cents"])
        if spent + amount_cents > authorized:
            return False, (f"spend {fmt(amount_cents)} would exceed grant "
                           f"({fmt(spent)} of {fmt(authorized)} used)")
        # Anomaly detection compares a spend against this grant's own history, not
        # against the grant ceiling: a spend larger than the ceiling is already
        # refused above, so a ceiling-based check would be unreachable. A runaway
        # loop shows up as one call that dwarfs the calls before it.
        anomaly = float(self._policy.get("financial", "anomaly_multiple", default=2.0))
        count = int(row["spend_count"])
        if count >= 3:
            mean_prior = spent / count
            if mean_prior > 0 and amount_cents > mean_prior * anomaly:
                return False, (
                    f"anomalous single spend {fmt(amount_cents)} is "
                    f"{amount_cents / mean_prior:.1f}x the mean of {count} prior "
                    f"spends ({fmt(int(mean_prior))}) on this grant")
        self._db.execute(
            "UPDATE budget_grants SET spent_cents = spent_cents + ?, "
            "spend_count = spend_count + 1 WHERE id = ?",
            (amount_cents, grant_id))
        self._db.commit()
        self._audit.record(
            event="spend.authorized", authority="governor", initiator="governor",
            why=what, job_id=row["job_id"], financial_authorization=grant_id,
            result=fmt(amount_cents))
        return True, "authorized"

    def require_spend(self, *, grant_id: str, amount_cents: Cents, what: str,
                      job_id: str | None = None) -> None:
        """Authorise or raise. Callers that cannot handle refusal use this."""
        ok, reason = self.authorize_spend(
            grant_id=grant_id, amount_cents=amount_cents, what=what, job_id=job_id)
        if not ok:
            raise GovernorRefused(reason)

    def committed_unverified_cents(self) -> Cents:
        """Exposure to calibration lag: authorised value not yet verified paid."""
        verified = set(self._ledger.verified_job_ids())
        rows = self._db.query(
            "SELECT job_id, authorized_cents FROM budget_grants WHERE revoked = 0")
        return sum(int(r["authorized_cents"]) for r in rows
                   if r["job_id"] not in verified)

    def decisions_for(self, job_id: str) -> list[dict]:
        return [dict(r) for r in self._db.query(
            "SELECT * FROM governor_decisions WHERE job_id = ? ORDER BY ts", (job_id,))]
