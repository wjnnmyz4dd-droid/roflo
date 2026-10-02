"""System control state, derived ONLY from the journal so it survives restarts.

Rules:
* Every process start writes STARTUP. Trading is forbidden until a clean
  RECONCILED event follows the latest STARTUP.
* HALT(scope="reconcile") is cleared only by a later clean RECONCILED.
* HALT(scope="day") is cleared at the next FTMO trading day (Europe/Prague).
* HALT(scope="manual") is cleared only by an operator RESUME event.
"""

from __future__ import annotations

from sentinel.journal import Journal
from sentinel.runtime import trading_day_cet


class ControlState:
    def __init__(self, journal: Journal, clock):
        self._j = journal
        self._clock = clock

    def startup(self, instance: str, versions: dict) -> None:
        self._j.append("STARTUP", "orchestrator", {"instance": instance, "versions": versions})

    def halt(self, by: str, reason: str, scope: str) -> None:
        assert scope in ("reconcile", "day", "manual")
        self._j.append("HALT", by, {"reason": reason, "scope": scope, "day": trading_day_cet(self._clock.now())})

    def resume(self, operator: str, reason: str) -> None:
        self._j.append("RESUME", "operator", {"operator": operator, "reason": reason})

    def trading_permitted(self) -> tuple[bool, str]:
        today = trading_day_cet(self._clock.now())
        last_startup = None
        last_clean_reconcile = None
        halts: list = []
        resumes: list = []
        for ev in self._j.events_of_types(("STARTUP", "RECONCILED", "HALT", "RESUME")):
            if ev.type == "STARTUP":
                last_startup = ev.seq
            elif ev.type == "RECONCILED" and ev.payload.get("clean"):
                last_clean_reconcile = ev.seq
            elif ev.type == "HALT":
                halts.append(ev)
            elif ev.type == "RESUME":
                resumes.append(ev.seq)
        if last_startup is None:
            return False, "no STARTUP recorded"
        for h in halts:
            if h.payload["scope"] == "manual" and not any(r > h.seq for r in resumes):
                return False, f"manual halt: {h.payload['reason']}"
        if last_clean_reconcile is None or last_clean_reconcile < last_startup:
            return False, "not reconciled since startup"
        for h in halts:
            scope = h.payload["scope"]
            if scope == "reconcile" and h.seq > last_clean_reconcile:
                return False, f"halted pending reconcile: {h.payload['reason']}"
            if scope == "day" and h.payload["day"] == today:
                return False, f"halted for trading day {today}: {h.payload['reason']}"
        return True, "OK"
