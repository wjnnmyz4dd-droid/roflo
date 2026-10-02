"""Scheduled economic-event safety engine (deterministic; never originates trades).

Scope: SCHEDULED events only. Post-publication news / sentiment is deliberately
not an input to any authority here.

Source in use: the Forex Factory weekly JSON (nfs.faireconomy.media), whose dates
carry explicit UTC offsets. Any record without an offset is rejected rather than
guessed. If the calendar is stale, unreachable, malformed beyond tolerance, or the
query time lies outside the feed's coverage window, the state is UNKNOWN and the
gate BLOCKS (fail closed).
"""

from __future__ import annotations

import hashlib
import json
import re
import urllib.request
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta

from sentinel.contracts import GateDecision, GateVerdict, content_hash

FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
IMPACTS = {"high": "HIGH", "medium": "MEDIUM", "low": "LOW", "holiday": "HOLIDAY", "non-economic": "LOW"}

# Which event currencies move which instruments (beyond the instrument's own currencies).
INSTRUMENT_EVENT_CURRENCIES = {
    "XAUUSD": {"USD"},
    "USOIL": {"USD", "OIL"},
    "UKOIL": {"USD", "OIL"},
    "BTCUSD": {"USD"},
    "USDJPY": {"USD", "JPY"},
}
OIL_TITLES = ("crude oil inventories",)
CENTRAL_BANK_PAT = re.compile(r"(rate statement|funds rate|policy rate|cash rate|bank rate|refinancing rate|overnight rate|monetary policy|press conference|fomc statement|boj)", re.I)


@dataclass(frozen=True)
class CalendarEvent:
    event_id: str
    title: str
    currency: str
    scheduled_utc: int
    impact: str
    forecast: str | None
    previous: str | None
    actual: str | None
    source: str
    source_fetched_at: int
    raw_date: str
    is_central_bank: bool


@dataclass
class CalendarSnapshot:
    events: list
    fetched_at: int
    coverage_start: int
    coverage_end: int
    source: str
    malformed: int
    total: int
    errors: list = field(default_factory=list)

    @property
    def snapshot_hash(self) -> str:
        return content_hash([[e.event_id, e.scheduled_utc, e.impact] for e in self.events])


def _event_id(source: str, currency: str, title: str, ts: int) -> str:
    return hashlib.sha256(f"{source}|{currency}|{title.lower()}|{ts}".encode()).hexdigest()[:20]


def parse_forex_factory(raw: list, fetched_at: int, source: str = "forexfactory") -> CalendarSnapshot:
    events: list[CalendarEvent] = []
    malformed = 0
    errors: list[str] = []
    seen: dict[tuple, CalendarEvent] = {}
    if not isinstance(raw, list):
        return CalendarSnapshot([], fetched_at, 0, 0, source, 1, 1, ["payload is not a list"])
    for item in raw:
        try:
            if not isinstance(item, dict):
                raise ValueError("record not an object")
            title = str(item["title"])[:200]
            cur = str(item["country"]).upper()[:6]
            date_s = str(item["date"])
            dt = datetime.fromisoformat(date_s.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                raise ValueError(f"no timezone offset: {date_s}")
            ts = int(dt.astimezone(UTC).timestamp())
            if not (946684800 < ts < 4102444800):
                raise ValueError("implausible date")
            impact = IMPACTS.get(str(item.get("impact", "")).lower(), "UNKNOWN")
            ccy = "OIL" if title.lower().startswith(OIL_TITLES) else cur
            ev = CalendarEvent(
                _event_id(source, ccy, title, ts), title, ccy, ts, impact,
                item.get("forecast") or None, item.get("previous") or None, item.get("actual") or None,
                source, fetched_at, date_s, bool(CENTRAL_BANK_PAT.search(title)),
            )
            key = (ccy, title.lower(), ts)
            if key in seen:  # duplicate record: keep the most severe impact
                if _sev(ev.impact) > _sev(seen[key].impact):
                    seen[key] = ev
                continue
            seen[key] = ev
        except Exception as e:  # noqa: BLE001 - every bad record is counted, never guessed
            malformed += 1
            errors.append(str(e)[:120])
    events = sorted(seen.values(), key=lambda e: e.scheduled_utc)
    if events:
        first = datetime.fromtimestamp(events[0].scheduled_utc, UTC)
        start = first - timedelta(days=(first.weekday() + 1) % 7)
        cov_start = int(start.replace(hour=0, minute=0, second=0).timestamp())
        cov_end = max(cov_start + 7 * 86400, events[-1].scheduled_utc + 3600)
    else:
        cov_start = cov_end = 0
    return CalendarSnapshot(events, fetched_at, cov_start, cov_end, source, malformed, len(raw), errors[:20])


def _sev(impact: str) -> int:
    return {"HOLIDAY": 0, "LOW": 1, "MEDIUM": 2, "UNKNOWN": 3, "HIGH": 4}.get(impact, 3)


def merge_sources(a: CalendarSnapshot, b: CalendarSnapshot, time_tolerance_s: int = 600) -> tuple[CalendarSnapshot, list]:
    """Reconcile two feeds. Conflicts are resolved conservatively: max impact; if the
    scheduled times disagree, both times are kept so blackout windows cover both."""
    conflicts = []
    merged = list(a.events)
    for eb in b.events:
        match = [ea for ea in a.events if ea.currency == eb.currency and ea.title.lower()[:20] == eb.title.lower()[:20]
                 and abs(ea.scheduled_utc - eb.scheduled_utc) <= time_tolerance_s]
        if not match:
            merged.append(eb)
            continue
        ea = match[0]
        if ea.scheduled_utc != eb.scheduled_utc:
            conflicts.append({"kind": "TIME", "title": ea.title, "a": ea.scheduled_utc, "b": eb.scheduled_utc})
            merged.append(eb)
        if ea.impact != eb.impact:
            conflicts.append({"kind": "IMPACT", "title": ea.title, "a": ea.impact, "b": eb.impact})
            if _sev(eb.impact) > _sev(ea.impact):
                merged[merged.index(ea)] = replace(ea, impact=eb.impact)
    snap = CalendarSnapshot(sorted(merged, key=lambda e: e.scheduled_utc), min(a.fetched_at, b.fetched_at),
                            max(a.coverage_start, b.coverage_start), min(a.coverage_end, b.coverage_end),
                            f"{a.source}+{b.source}", a.malformed + b.malformed, a.total + b.total)
    return snap, conflicts


def fetch_forex_factory(now: int, timeout: float = 15.0) -> CalendarSnapshot:
    req = urllib.request.Request(FF_URL, headers={"User-Agent": "sentinel/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - fixed https URL
        raw = json.loads(r.read().decode())
    return parse_forex_factory(raw, now)


@dataclass(frozen=True)
class BlackoutPolicy:
    """Internal safety policy (NOT an FTMO rule; FTMO's own window lives in compliance)."""

    high_before_s: int = 30 * 60
    high_after_s: int = 15 * 60
    central_bank_before_s: int = 60 * 60
    central_bank_after_s: int = 30 * 60
    medium_before_s: int = 0
    medium_after_s: int = 0
    unknown_impact_as_high: bool = True
    max_staleness_s: int = 6 * 3600
    max_malformed_fraction: float = 0.05
    min_events_per_week: int = 5


class NewsEngine:
    def __init__(self, policy: BlackoutPolicy | None = None, signer=None):
        self.signer = signer
        self.policy = policy or BlackoutPolicy()
        self.snapshot: CalendarSnapshot | None = None

    def load(self, snap: CalendarSnapshot) -> None:
        self.snapshot = snap

    def health(self, now: int) -> tuple[bool, str]:
        s = self.snapshot
        p = self.policy
        if s is None:
            return False, "no calendar loaded"
        if now - s.fetched_at > p.max_staleness_s:
            return False, f"calendar stale ({now - s.fetched_at}s old)"
        if s.fetched_at > now + 300:
            return False, "calendar fetched in the future (clock error)"
        if s.total and s.malformed / s.total > p.max_malformed_fraction:
            return False, f"calendar malformed fraction {s.malformed}/{s.total}"
        if len(s.events) < p.min_events_per_week:
            return False, f"calendar implausibly empty ({len(s.events)} events)"
        if not (s.coverage_start <= now < s.coverage_end):
            return False, "query time outside calendar coverage"
        return True, "OK"

    def event_currencies(self, instrument: str, currencies: tuple) -> set:
        return set(currencies) | INSTRUMENT_EVENT_CURRENCIES.get(instrument, set())

    def _window(self, e: CalendarEvent) -> tuple[int, int]:
        p = self.policy
        if e.is_central_bank and e.impact in ("HIGH", "UNKNOWN"):
            return p.central_bank_before_s, p.central_bank_after_s
        if e.impact == "HIGH" or (e.impact == "UNKNOWN" and p.unknown_impact_as_high):
            return p.high_before_s, p.high_after_s
        if e.impact == "MEDIUM":
            return p.medium_before_s, p.medium_after_s
        return 0, 0

    def evaluate(self, instrument: str, currencies: tuple, now: int, horizon_s: int = 0) -> GateDecision:
        d = self._evaluate(instrument, currencies, now, horizon_s)
        return self.signer.sign(d) if self.signer else d

    def _evaluate(self, instrument: str, currencies: tuple, now: int, horizon_s: int = 0) -> GateDecision:
        ok, why = self.health(now)
        inputs = content_hash([instrument, now, self.snapshot.snapshot_hash if self.snapshot else None])
        if not ok:
            return GateDecision("news", GateVerdict.BLOCK, (f"UNKNOWN calendar state: {why}",), inputs, now)
        ccys = self.event_currencies(instrument, currencies)
        hits = []
        for e in self.snapshot.events:
            if e.currency not in ccys:
                continue
            before, after = self._window(e)
            if before == 0 and after == 0:
                continue
            if e.scheduled_utc - before <= now + horizon_s and now <= e.scheduled_utc + after:
                hits.append(e)
        if hits:
            return GateDecision("news", GateVerdict.BLOCK,
                                tuple(f"{e.currency} {e.impact} '{e.title}' at {e.scheduled_utc}" for e in hits[:5]),
                                inputs, now, detail={"event_ids": [e.event_id for e in hits]})
        return GateDecision("news", GateVerdict.ALLOW, ("no blocking scheduled event",), inputs, now)
