"""Historical FOMC calendar from the Federal Reserve's own pages, with point-in-time semantics.

Sources (public, no credentials):
  https://www.federalreserve.gov/monetarypolicy/fomchistorical{YEAR}.htm   (older years)
  https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm          (recent + scheduled future years)

Canonical record: one per policy decision (the LAST day of a meeting, or the day of a call).
Point-in-time (``known_from``):
  * scheduled meetings: known from 1 January of the meeting year (the Fed publishes the next
    year's schedule well before then). Residual: a date rescheduled mid-year is shown with its
    final date; that cannot be reconstructed from these pages.
  * unscheduled meetings / conference calls: NOT knowable in advance. ``known_from`` is the end
    of that day, so a replay can never block a trade before such an event (no leakage).
Time precision: the pages give dates only. Statement release times are bracketed, not guessed:
  2000-2010: ~14:15 ET; 2011-2012: 12:30 ET (press-conference meetings) or ~14:15 ET;
  2013 onward: 14:00 ET. Records carry the bracket [earliest, latest] in UTC and the replay
  emits enough points that the blackout windows cover the whole bracket.
"""

from __future__ import annotations

import hashlib
import html as _html
import re
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sentinel.news import CalendarEvent, CalendarSnapshot

ET = ZoneInfo("America/New_York")
MONTHS = {m: i for i, m in enumerate(["January", "February", "March", "April", "May", "June", "July", "August",
                                       "September", "October", "November", "December"], 1)}
ABBR = {m[:3]: i for m, i in MONTHS.items()}
SOURCE = "federalreserve.gov"


def _text(page: str) -> str:
    return _html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", page)))


def _bracket_et(d: date) -> tuple[time, time, str]:
    if d.year >= 2013:
        return time(14, 0), time(14, 0), "approx_15min"
    if d.year >= 2011:
        return time(12, 30), time(14, 15), "bracket"
    return time(14, 15), time(14, 15), "approx_15min"


def _utc(d: date, t: time) -> int:
    return int(datetime.combine(d, t, ET).astimezone(UTC).timestamp())


def _record(d: date, kind: str, source_url: str, source_sha: str) -> dict:
    if kind == "scheduled":
        lo_t, hi_t, prec = _bracket_et(d)
        lo, hi = _utc(d, lo_t), _utc(d, hi_t)
        known = int(datetime(d.year, 1, 1, tzinfo=UTC).timestamp())
    else:  # time of an unscheduled action is not on the page: whole ET day, not knowable beforehand
        lo, hi, prec = _utc(d, time(0, 0)), _utc(d, time(23, 59)), "day_only"
        known = _utc(d, time(23, 59))
    return {
        "event_id": hashlib.sha256(f"FOMC|{d.isoformat()}|{kind}".encode()).hexdigest()[:20],
        "title": "FOMC Statement" if kind == "scheduled" else f"FOMC {kind.replace('_', ' ')}",
        "currency": "USD", "impact": "HIGH", "is_central_bank": True, "kind": kind,
        "decision_date": d.isoformat(), "window_utc": [lo, hi], "time_precision": prec,
        "known_from": known, "source_url": source_url, "source_sha256": source_sha,
    }


HIST_PAT = re.compile(
    r"(?:(?P<ma>[A-Z][a-z]{2})/)?(?P<m1>[A-Z][a-z]+) (?P<d1>\d{1,2})(?:-(?:(?P<m2>[A-Z][a-z]+) )?(?P<d2>\d{1,2}))?"
    r"(?P<uns> \(unscheduled\))? (?P<what>Meeting|Conference Call)s? - (?P<y>\d{4})")


def parse_historical(page: str, year: int, source_url: str = "") -> list[dict]:
    sha = hashlib.sha256(page.encode()).hexdigest()
    out, seen = [], set()
    for m in HIST_PAT.finditer(_text(page)):
        mon_of = {**MONTHS, **ABBR}
        if int(m["y"]) != year or m["m1"] not in mon_of or (m["m2"] and m["m2"] not in mon_of):
            continue
        mon = mon_of[m["m2"] or m["m1"]]
        day = int(m["d2"] or m["d1"])
        d = date(year, mon, day)
        kind = "conference_call" if m["what"] == "Conference Call" else ("unscheduled" if m["uns"] else "scheduled")
        if (d, kind) in seen:
            continue
        seen.add((d, kind))
        out.append(_record(d, kind, source_url, sha))
    out.sort(key=lambda r: r["decision_date"])
    # the same scheduled meeting listed under two adjacent dates (e.g. 2003-09-15 and 2003-09-16):
    # keep one record but widen its window to cover both days (conservative: blocks more, never less)
    merged: list[dict] = []
    for r in out:
        prev = merged[-1] if merged else None
        if (prev and r["kind"] == prev["kind"] == "scheduled"
                and (date.fromisoformat(r["decision_date"]) - date.fromisoformat(prev["decision_date"])).days <= 2):
            prev["window_utc"] = [prev["window_utc"][0], r["window_utc"][1]]
            prev["decision_date"] = r["decision_date"]
            prev["time_precision"] = "bracket_multi_day"
            continue
        merged.append(r)
    return merged


ROW_PAT = re.compile(r'fomc-meeting__month[^>]*>(.*?)</div>\s*<div class="fomc-meeting__date[^>]*>(.*?)</div>', re.S)
YEAR_PAT = re.compile(r"(\d{4}) FOMC Meetings")


def parse_calendars(page: str, source_url: str = "") -> list[dict]:
    sha = hashlib.sha256(page.encode()).hexdigest()
    heads = sorted((m.start(), int(m.group(1))) for m in YEAR_PAT.finditer(page))
    out = []
    for m in ROW_PAT.finditer(page):
        year = max((h for h in heads if h[0] < m.start()), default=(0, None))[1]
        if year is None:
            continue
        mon_s = _html.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip()
        day_s = _html.unescape(re.sub(r"<[^>]+>", "", m.group(2))).strip()
        if "notation vote" in day_s:
            continue  # a written vote, not a meeting with a market-moving statement at a known time
        kind = "unscheduled" if "unscheduled" in day_s else "scheduled"
        days = re.findall(r"\d{1,2}", day_s)
        mons = [p[:3] for p in mon_s.split("/")]
        if not days or any(p not in ABBR for p in mons):
            raise ValueError(f"unparseable FOMC row: {mon_s!r} {day_s!r}")
        d = date(year, ABBR[mons[-1]], int(days[-1]))
        out.append(_record(d, kind, source_url, sha))
    return sorted(out, key=lambda r: r["decision_date"])


def snapshot_at(records: list[dict], as_of: int, horizon_days: int = 8) -> CalendarSnapshot:
    """Point-in-time view: only records whose ``known_from`` <= ``as_of``.

    Each record becomes one CalendarEvent per point needed for the blackout windows to cover its
    release-time bracket (bracket ends plus hourly points inside). ``fetched_at`` = ``as_of``.
    Coverage spans [as_of - 1 day, as_of + horizon_days] like a live weekly feed.
    """
    events = []
    for r in records:
        if r["known_from"] > as_of:
            continue
        lo, hi = r["window_utc"]
        pts = sorted({lo, hi, *range(lo, hi, 3600)})
        for k, ts in enumerate(pts):
            events.append(CalendarEvent(f"{r['event_id']}-{k}", r["title"], r["currency"], ts, r["impact"], None, None,
                                        None, SOURCE, as_of, r["decision_date"], True))
    events.sort(key=lambda e: e.scheduled_utc)
    return CalendarSnapshot(events, as_of, as_of - 86400, as_of + horizon_days * 86400, SOURCE, 0, len(events))


def blocked_at(records: list[dict], ts: int, before_s: int = 3600, after_s: int = 1800) -> list[str]:
    """Fast point-in-time check equivalent to NewsEngine's central-bank window (research use)."""
    hits = []
    for r in records:
        if r["known_from"] > ts:
            continue
        lo, hi = r["window_utc"]
        if lo - before_s <= ts <= hi + after_s:
            hits.append(r["event_id"])
    return hits


_ = timedelta
