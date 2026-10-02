"""Evidence / promotion pipeline. Nothing learned goes live without passing it.

* Stages must be passed IN ORDER; each stage result is journaled with its artifact hash.
* Criteria are registered (hashed) before the holdout may be opened; the holdout can be
  opened exactly once per proposal; results recorded under different criteria are refused.
* LIMITED_LIVE is never reachable from code alone and PROMOTION needs an operator approval
  signed with a key the system does not hold (verified against a public fingerprint).
"""

from __future__ import annotations

import hashlib
import hmac

from sentinel.contracts import content_hash
from sentinel.journal import DuplicateKey, Journal
from sentinel.runtime import crashpoint

STAGES = (
    "RESEARCH_TEST", "BACKTEST", "LEAKAGE_CHECK", "OUT_OF_SAMPLE", "WALK_FORWARD", "COST_STRESS",
    "REGIME_TEST", "MONTE_CARLO", "SHADOW", "PAPER", "REVIEW",
)


class PromotionRefused(RuntimeError):
    pass


class PromotionPipeline:
    def __init__(self, journal: Journal, operator_key_fingerprint: str | None):
        self._j = journal
        self._fp = operator_key_fingerprint

    def submit(self, proposal: dict) -> str:
        pid = proposal["proposal_id"]
        try:
            self._j.append("PROPOSAL", "promotion", proposal, correlation_id=pid, unique=[("proposal", pid)])
        except DuplicateKey:
            raise PromotionRefused("proposal already submitted") from None
        return pid

    def register_criteria(self, pid: str, criteria: dict) -> str:
        h = content_hash(criteria)
        try:
            self._j.append("CRITERIA_REGISTERED", "promotion", {"proposal_id": pid, "criteria": criteria, "hash": h},
                           correlation_id=pid, unique=[("criteria", pid)])
        except DuplicateKey:
            raise PromotionRefused("criteria already registered; they cannot be changed") from None
        return h

    def _criteria_hash(self, pid: str) -> str | None:
        ev = self._j.last("CRITERIA_REGISTERED", correlation_id=pid)
        return ev.payload["hash"] if ev else None

    def open_holdout(self, pid: str) -> None:
        if self._criteria_hash(pid) is None:
            raise PromotionRefused("register criteria before touching the holdout")
        try:
            self._j.append("HOLDOUT_OPENED", "promotion", {"proposal_id": pid}, correlation_id=pid, unique=[("holdout", pid)])
        except DuplicateKey:
            raise PromotionRefused("holdout already used for this proposal (no reuse)") from None

    def record(self, pid: str, stage: str, passed: bool, artifact: dict, criteria_hash: str) -> None:
        if stage not in STAGES:
            raise PromotionRefused(f"unknown stage {stage}")
        if criteria_hash != self._criteria_hash(pid):
            raise PromotionRefused("result evaluated against criteria other than the registered ones")
        done = self.passed_stages(pid)
        expected = STAGES[len(done)]
        if stage != expected:
            raise PromotionRefused(f"stage out of order: expected {expected}, got {stage}")
        crashpoint("promotion_before_record")
        if stage == "OUT_OF_SAMPLE" and self._j.last("HOLDOUT_OPENED", correlation_id=pid) is None:
            raise PromotionRefused("out-of-sample result without a recorded holdout opening")
        self._j.append("STAGE_RESULT", "promotion", {"proposal_id": pid, "stage": stage, "passed": passed,
                                                    "artifact_hash": content_hash(artifact), "artifact": artifact},
                       correlation_id=pid)

    def passed_stages(self, pid: str) -> list[str]:
        out = []
        for e in self._j.events(type_="STAGE_RESULT", correlation_id=pid):
            if not e.payload["passed"]:
                return out  # a failed stage ends the line; a new proposal is required
            out.append(e.payload["stage"])
        return out

    def promote(self, pid: str, approval: dict | None) -> dict:
        if self.passed_stages(pid) != list(STAGES):
            raise PromotionRefused("not all stages passed")
        if not approval or not self._fp:
            raise PromotionRefused("operator approval required")
        msg = f"PROMOTE|{pid}|{self._criteria_hash(pid)}"
        key = approval.get("operator_key", "")
        if hashlib.sha256(key.encode()).hexdigest() != self._fp:
            raise PromotionRefused("approval not signed by the registered operator")
        if not hmac.compare_digest(hmac.new(key.encode(), msg.encode(), hashlib.sha256).hexdigest(), approval.get("signature", "")):
            raise PromotionRefused("approval signature invalid")
        self._j.append("PROMOTED", "promotion", {"proposal_id": pid, "note": "promoted to PAPER-eligible config; live requires separate authorization"},
                       correlation_id=pid)
        return {"promoted": pid, "live": False}
