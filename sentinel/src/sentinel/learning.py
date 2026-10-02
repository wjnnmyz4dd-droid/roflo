"""Learning / memory. Outside live authority: it can read the trading journal and
write ONLY to its own memory store and to research proposals.

Epistemic kinds are explicit and never silently upgraded:

* FACT        - created only from a verified broker-truth journal event (hash-checked).
* OBSERVATION - an aggregate computed from FACTs; carries the fact references.
* INFERENCE   - an interpretation; never becomes a FACT.
* HYPOTHESIS  - a testable claim; can only be tested via the promotion pipeline.
* LESSON      - a regime-scoped rule of thumb whose status (ACTIVE/WEAKENED/RETIRED)
                is recomputed from supporting / contradicting evidence and age.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from sentinel.journal import DuplicateKey, Journal
from sentinel.runtime import crashpoint

KINDS = ("FACT", "OBSERVATION", "INFERENCE", "HYPOTHESIS", "LESSON")
BROKER_TRUTH_EVENTS = {"POSITION_CLOSED", "ORDER_ACKED", "ORDER_RESOLVED"}


class MemoryRejected(ValueError):
    pass


@dataclass(frozen=True)
class LessonStatus:
    lesson_id: str
    status: str
    score: float
    support: int
    contradict: int
    age_days: float


class Memory:
    def __init__(self, store: Journal, trading_journal: Journal, clock):
        self._m = store
        self._tj = trading_journal
        self._clock = clock

    # --- FACTs: only from the trading journal ------------------------------------------
    def fact_from_journal(self, seq: int) -> str:
        ok, why = self._tj.verify()
        if not ok:
            raise MemoryRejected(f"trading journal integrity failure: {why}")
        ev = self._tj.get(seq)
        if ev is None:
            raise MemoryRejected(f"no journal event {seq}")
        if ev.type not in BROKER_TRUTH_EVENTS:
            raise MemoryRejected(f"event type {ev.type} is not broker truth")
        if ev.ts > self._clock.now() + 1:
            raise MemoryRejected("event timestamp is in the future")
        fact_id = f"fact-{seq}"
        try:
            self._m.append("FACT", "learning", {"fact_id": fact_id, "journal_seq": seq, "journal_hash": ev.hash,
                                               "event_type": ev.type, "payload": ev.payload},
                           correlation_id=fact_id, unique=[("fact", str(seq))])
        except DuplicateKey:
            raise MemoryRejected(f"fact for journal event {seq} already recorded (duplicate)") from None
        return fact_id

    def add(self, kind: str, statement: str, refs: list[str], **extra) -> str:
        if kind not in KINDS:
            raise MemoryRejected(f"unknown kind {kind}")
        if kind == "FACT":
            raise MemoryRejected("FACTs can only be created from verified journal events")
        known = {e.payload.get("fact_id") or e.payload.get("id") for e in self._m.events()}
        missing = [r for r in refs if r not in known]
        if kind in ("OBSERVATION", "LESSON") and (not refs or missing):
            raise MemoryRejected(f"{kind} requires existing references; missing {missing}")
        if kind == "OBSERVATION":
            if any(not r.startswith("fact-") for r in refs):
                raise MemoryRejected("OBSERVATIONs may only cite FACTs")
        rid = f"{kind.lower()}-{len(known) + 1}-{abs(hash(statement)) % 10**8}"
        self._m.append(kind, "learning", {"id": rid, "statement": statement[:2000], "refs": refs,
                                          "created_at": int(self._clock.now()), **extra}, correlation_id=rid)
        return rid

    def lesson(self, statement: str, regime: dict, refs: list[str], half_life_days: float = 60.0) -> str:
        return self.add("LESSON", statement, refs, regime=regime, half_life_days=half_life_days)

    def evidence(self, lesson_id: str, fact_id: str, supports: bool) -> None:
        if not self._m.has_key("fact", fact_id.removeprefix("fact-")):
            raise MemoryRejected("evidence must cite a recorded FACT")
        try:
            self._m.append("LESSON_EVIDENCE", "learning", {"lesson_id": lesson_id, "fact_id": fact_id, "supports": supports},
                           correlation_id=lesson_id, unique=[("lesson_evidence", f"{lesson_id}|{fact_id}")])
        except DuplicateKey:
            raise MemoryRejected("this fact was already counted for this lesson") from None

    def lesson_status(self, lesson_id: str) -> LessonStatus:
        les = next((e for e in self._m.events(type_="LESSON") if e.payload["id"] == lesson_id), None)
        if les is None:
            raise MemoryRejected("unknown lesson")
        now = self._clock.now()
        hl = les.payload.get("half_life_days", 60.0)
        sup = con = 0
        score = 0.0
        for e in self._m.events(type_="LESSON_EVIDENCE", correlation_id=lesson_id):
            w = 0.5 ** (max(0.0, now - e.ts) / 86400 / hl)
            score += w if e.payload["supports"] else -w
            sup += e.payload["supports"]
            con += not e.payload["supports"]
        age = (now - les.payload["created_at"]) / 86400
        n = sup + con
        if n >= 10 and score <= -1.0:
            status = "RETIRED"
        elif n < 5 or score <= 1.0 or 0.5 ** (age / hl) < 0.25:
            status = "WEAKENED"
        else:
            status = "ACTIVE"
        return LessonStatus(lesson_id, status, round(score, 3), sup, con, round(age, 2))

    def propose(self, title: str, change: dict, rationale: str, refs: list[str]) -> dict:
        """Learning's ONLY output toward behaviour change: a research proposal."""
        pid = self.add("HYPOTHESIS", f"PROPOSAL: {title}", refs, change=change, rationale=rationale[:2000])
        return {"proposal_id": pid, "title": title, "change": change, "refs": refs}

    # --- post-mortem ------------------------------------------------------------------------
    def postmortem(self) -> list[str]:
        """Turn every broker-confirmed closed position into a FACT plus a linked OBSERVATION."""
        out = []
        recorded = {e.payload["journal_seq"] for e in self._m.events(type_="FACT")}
        for ev in self._tj.events(type_="POSITION_CLOSED"):
            if ev.seq in recorded:
                continue
            fid = self.fact_from_journal(ev.seq)
            crashpoint("learning_after_fact")
            iid = ev.payload.get("intent_id")
            intent = next((e.payload for e in self._tj.events(type_="INTENT_PERSISTED", correlation_id=iid)), None)
            cand = None
            if intent:
                cid = intent["intent"]["candidate_id"]
                cand = next((e.payload["candidate"] for e in self._tj.events(type_="CANDIDATE", correlation_id=cid)), None)
            net = ev.payload["profit"] - ev.payload.get("commission", 0)
            regime = cand["regime"] if cand else None
            strategy = cand["strategy_version"] if cand else "UNATTRIBUTED"
            out.append(self.add("OBSERVATION", f"closed {ev.payload['ticket']} net {net:.2f}", [fid],
                                regime=regime, strategy=strategy, net=round(net, 2)))
        return out


def wilson_lower(wins: int, n: int, z: float = 1.96) -> float:
    if n == 0:
        return 0.0
    p = wins / n
    d = 1 + z * z / n
    return (p + z * z / (2 * n) - z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / d
