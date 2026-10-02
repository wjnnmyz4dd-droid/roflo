"""Neutral Arbiter. Deterministic rules; no market prediction, no broker, no sizing.

It compares the Finder's case with the Validator's case and decides only whether
the candidate may proceed to risk review. Rules are evaluated in a fixed order;
the first terminal rule wins and the full trace is recorded.
"""

from __future__ import annotations

from dataclasses import dataclass

from sentinel.contracts import ArbiterDecision, ArbiterVerdict, TradeCandidate, ValidationReport, ValidationVerdict
from sentinel.validator import REQUIRED_CHECKS

ARBITER_VERSION = "arbiter_rules_v1"
REQUIRED_EVIDENCE = ("atr", "breakout_atr", "signal_bar_ts")


@dataclass(frozen=True)
class ArbiterPolicy:
    min_edge_r: float = 0.05
    min_edge_r_when_correlated: float = 0.10  # demanded when measured Finder/Validator independence is low
    independence_threshold: float = 0.5
    min_t_stat: float = 1.0


class Arbiter:
    def __init__(self, policy: ArbiterPolicy | None = None, measured_independence: float | None = None, signer=None):
        self.signer = signer
        self.policy = policy or ArbiterPolicy()
        self.independence = measured_independence

    def decide(self, c: TradeCandidate | None, v: ValidationReport | None, now: int) -> ArbiterDecision:
        trace: list[str] = []

        def out(verdict: ArbiterVerdict, rule: str, reason: str) -> ArbiterDecision:
            trace.append(rule)
            d = ArbiterDecision(c.candidate_id if c else "?", verdict, tuple(trace), (reason,), ARBITER_VERSION, now)
            return self.signer.sign(d) if self.signer else d

        if c is None:
            return out(ArbiterVerdict.INDETERMINATE, "R0_no_candidate", "no candidate supplied")
        if now >= c.expires_at:
            return out(ArbiterVerdict.NO_TRADE, "R1_expired", "candidate expired")
        trace.append("R1_pass")
        if v is None or v.candidate_id != c.candidate_id:
            return out(ArbiterVerdict.INDETERMINATE, "R2_missing_validation", "no validation for this candidate")
        trace.append("R2_pass")
        if v.verdict is ValidationVerdict.INVALID:
            return out(ArbiterVerdict.REJECT, "R3_invalid", "validator falsified the candidate: " + "; ".join(v.reasons))
        if v.verdict is ValidationVerdict.INDETERMINATE:
            return out(ArbiterVerdict.INDETERMINATE, "R4_indeterminate", "validator could not decide: " + "; ".join(v.reasons))
        if v.verdict is not ValidationVerdict.VALID:
            return out(ArbiterVerdict.INDETERMINATE, "R4b_unknown_verdict", f"unknown verdict {v.verdict}")
        trace.append("R3_R4_pass")
        missing = [k for k in REQUIRED_EVIDENCE if k not in c.evidence]
        if missing:
            return out(ArbiterVerdict.INDETERMINATE, "R5_evidence_incomplete", f"candidate lacks evidence {missing}")
        absent = [k for k in REQUIRED_CHECKS if k not in v.checks or v.checks[k].get("passed") is not True]
        if absent:
            return out(ArbiterVerdict.INDETERMINATE, "R6_checks_incomplete", f"validator checks not all passed: {absent}")
        trace.append("R5_R6_pass")
        if v.data_sources and c.provenance.dataset_hash not in v.data_sources:
            return out(ArbiterVerdict.INDETERMINATE, "R7_data_mismatch", "finder and validator examined different data")
        trace.append("R7_pass")
        cs = v.checks["cost_survival"]
        need = self.policy.min_edge_r
        if self.independence is None or self.independence < self.policy.independence_threshold:
            need = self.policy.min_edge_r_when_correlated
        if cs.get("mean_r", -1) < need or cs.get("t_stat", 0) < self.policy.min_t_stat:
            return out(ArbiterVerdict.NO_TRADE, "R8_edge_insufficient",
                       f"edge {cs.get('mean_r')}R / t={cs.get('t_stat')} below required {need}R (independence={self.independence})")
        return out(ArbiterVerdict.APPROVE_FOR_RISK_REVIEW, "R9_approve", "validated with sufficient post-cost evidence")
