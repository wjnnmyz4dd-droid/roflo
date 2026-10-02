"""Clock, crash points, structured logging and ID generation."""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

FTMO_TZ = ZoneInfo("Europe/Prague")  # CE(S)T per FTMO rule text


class SystemClock:
    def now(self) -> float:
        return time.time()


class FakeClock:
    def __init__(self, t: float):
        self.t = float(t)

    def now(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


def trading_day_cet(ts: float) -> str:
    """FTMO trading day label: date in Europe/Prague (handles CET/CEST)."""
    return datetime.fromtimestamp(ts, tz=UTC).astimezone(FTMO_TZ).strftime("%Y-%m-%d")


def cet_midnight_utc(ts: float) -> int:
    local = datetime.fromtimestamp(ts, tz=UTC).astimezone(FTMO_TZ)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(midnight.astimezone(UTC).timestamp())


class CrashInjected(SystemExit):
    pass


def crashpoint(label: str) -> None:
    """Kill the process hard at a named lifecycle boundary when requested.

    ``SENTINEL_CRASH_AT=<label>`` makes the process ``os._exit(137)`` at that
    point: no finally blocks, no flush, no cleanup, exactly like SIGKILL.
    """
    if os.environ.get("SENTINEL_CRASH_AT") == label:
        sys.stdout.flush()
        os._exit(137)


def log(event: str, **fields) -> None:
    rec = {"ts": round(time.time(), 3), "event": event, **fields}
    for k in list(rec):
        if any(s in k.lower() for s in ("secret", "password", "token", "key")) and k not in ("intent_key",):
            rec[k] = "***"
    print(json.dumps(rec, default=str, sort_keys=True), file=sys.stderr)
