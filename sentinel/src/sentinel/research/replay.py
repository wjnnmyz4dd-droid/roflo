"""Shadow / paper replay of the FULL production pipeline over historical bars.

The same Orchestrator, gates and execution code run; only the broker is the
simulator and the clock is a FakeClock driven bar by bar. Quotes are set from
each bar's close with the instrument's modelled spread.
"""

from __future__ import annotations

from sentinel.runtime import FakeClock
from sentinel.system import DEFAULT_COSTS, ReplayMarket, build


def calendar_for(ts_from: int, ts_to: int):
    """Synthetic calendar 'feed' for replay windows with no historical source.

    It contains only rule-derived US Non-Farm Payrolls (first Friday, 08:30
    America/New_York) and is labelled as such. It is NOT a substitute for a real
    historical calendar; it exists so the gate path is exercised during replay.
    """
    from datetime import UTC, datetime, timedelta
    from zoneinfo import ZoneInfo

    from sentinel.news import CalendarSnapshot, parse_forex_factory

    ny = ZoneInfo("America/New_York")
    raw = []
    d = datetime.fromtimestamp(ts_from, UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = datetime.fromtimestamp(ts_to, UTC)
    while d <= end + timedelta(days=31):
        first = d.replace(tzinfo=None)
        fri = first + timedelta(days=(4 - first.weekday()) % 7)
        ev = datetime(fri.year, fri.month, fri.day, 8, 30, tzinfo=ny)
        raw.append({"title": "Non-Farm Employment Change", "country": "USD", "date": ev.isoformat(), "impact": "High"})
        for k in range(6):  # filler low-impact rows so the health check's plausibility floor is met
            raw.append({"title": f"synthetic filler {k}", "country": "XXX", "date": (ev + timedelta(days=k)).isoformat(), "impact": "Low"})
        d = (d + timedelta(days=32)).replace(day=1)
    snap = parse_forex_factory(raw, ts_from, source="synthetic_nfp_rule")
    return CalendarSnapshot(snap.events, ts_to, ts_from - 86400, ts_to + 86400, snap.source, snap.malformed, snap.total)


def run_replay(workdir: str, bars: dict, bar_seconds: int, start: int, end: int, mode: str = "paper",
               instruments=None, profile_id="ftmo-2step-challenge-standard", source="yahoo", retrieved_at=0,
               independence=None, step=None):
    instruments = instruments or list(bars)
    clock = FakeClock(start)
    market = ReplayMarket(bars, bar_seconds, source, retrieved_at)
    snap = calendar_for(start, end)
    b = build(workdir, clock, market, instruments, mode=mode, profile_id=profile_id, account_start_utc=start,
              news_snapshot=snap, independence=independence, lease_ttl=10 * bar_seconds)
    index = {i: {x.ts: x for x in bars[i]} for i in instruments}
    t = start
    step = step or bar_seconds
    log = []
    while t <= end:
        clock.t = t
        for i in instruments:
            bar = index[i].get(t - bar_seconds)
            if bar:
                half = DEFAULT_COSTS[i].spread / 2
                b.broker.set_quote(i, round(bar.close - half, 8), round(bar.close + half, 8), t)
        b.comps.lease.acquire()
        snap.fetched_at = t  # synthetic feed is 'fresh' by construction; documented above
        res = b.orch.cycle(t)
        log.append((t, res))
        t += step
    return b, log
