from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import pytest

from sentinel.contracts import Bar
from sentinel.news import parse_forex_factory
from sentinel.runtime import FakeClock
from sentinel.system import DEFAULT_COSTS, ReplayMarket, build

# Tuesday 2026-09-29 14:00 UTC: markets open, not near a weekend.
NOW = int(datetime(2026, 9, 29, 14, 0, tzinfo=UTC).timestamp())
WALL = FakeClock(int(datetime(2026, 10, 15, tzinfo=UTC).timestamp()))  # inside the profile validity window
H = 3600


def trending_bars(instrument="XAUUSD", n=1600, end_ts=NOW, start=2000.0, drift=0.6, noise=0.6, seed=7, breakout=0.5):
    """Hourly bars ending at the bar that closed at end_ts; a persistent up-trend with a final breakout bar."""
    rnd = random.Random(seed)
    bars = []
    px = start
    first = end_ts - n * H
    for i in range(n):
        ts = first + i * H
        o = px
        c = o + drift + rnd.gauss(0, noise)
        if i == n - 1:
            c = max(b.high for b in bars[-20:]) + breakout
        h = max(o, c) + abs(rnd.gauss(0, noise * 0.5))
        lo = min(o, c) - abs(rnd.gauss(0, noise * 0.5))
        bars.append(Bar(instrument, ts, round(o, 2), round(h, 2), round(lo, 2), round(c, 2), 100))
        px = c
    return bars


def fixture_calendar(fetched_at=NOW, events=None):
    """A healthy calendar covering the week of NOW with only far-away low-impact events by default."""
    base = datetime.fromtimestamp(NOW, UTC).replace(hour=0, minute=0, second=0)
    sunday = base - timedelta(days=(base.weekday() + 1) % 7)
    raw = [{"title": f"filler {i}", "country": "NZD", "impact": "Low",
            "date": (sunday + timedelta(days=1, hours=i)).isoformat()} for i in range(8)]
    raw += events or []
    return parse_forex_factory(raw, fetched_at)


@pytest.fixture
def clock():
    return FakeClock(NOW)


def make_system(tmp_path, clock, mode="paper", profile="ftmo-2step-challenge-standard", bars=None, calendar=None,
                instance="A", independence=0.8, broker=None, **kw):
    bars = bars or {"XAUUSD": trending_bars()}
    market = ReplayMarket(bars, H, "fixture", NOW)
    built = build(str(tmp_path), clock, market, list(bars), mode=mode, profile_id=profile, account_start_utc=NOW - 30 * 86400,
                  instance=instance, news_snapshot=calendar or fixture_calendar(), independence=independence,
                  broker=broker, wall_clock=WALL, **kw)
    for ins, bs in bars.items():
        last = bs[-1]
        half = DEFAULT_COSTS[ins].spread / 2
        built.broker.set_quote(ins, last.close - half, last.close + half, clock.now())
    return built
