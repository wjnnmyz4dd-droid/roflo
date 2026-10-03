"""Historical FOMC calendar (federalreserve.gov) and point-in-time news replay without leakage."""

from __future__ import annotations

import json
import pathlib
from datetime import UTC, datetime

import pytest

from sentinel.news import NewsEngine
from sentinel.research.calendar_hist import blocked_at, snapshot_at

CAL = json.loads((pathlib.Path(__file__).resolve().parents[1] / "data" / "calendar" / "fomc.json").read_text())["records"]
BY = {(r["decision_date"], r["kind"]): r for r in CAL}


def ts(*a):
    return int(datetime(*a, tzinfo=UTC).timestamp())


def test_known_decisions_present_with_correct_utc_time():
    r = BY[("2015-12-16", "scheduled")]
    assert r["window_utc"] == [ts(2015, 12, 16, 19), ts(2015, 12, 16, 19)]  # 14:00 EST
    r = BY[("2024-07-31", "scheduled")]
    assert r["window_utc"][0] == ts(2024, 7, 31, 18)  # 14:00 EDT
    assert ("2017-02-01", "scheduled") in BY and ("2023-11-01", "scheduled") in BY  # meetings spanning two months
    assert ("2020-03-15", "unscheduled") in BY and ("2008-10-07", "conference_call") in BY
    assert not any(r["decision_date"] == "2025-08-22" for r in CAL)  # notation vote excluded


def test_unscheduled_events_are_not_knowable_in_advance():
    r = BY[("2020-03-15", "unscheduled")]
    assert r["known_from"] >= ts(2020, 3, 16, 3)  # end of the ET day
    snap = snapshot_at(CAL, ts(2020, 3, 15, 12))
    assert not any(e.raw_date == "2020-03-15" for e in snap.events)


@pytest.mark.parametrize("r", CAL[::7])
def test_no_record_visible_before_known_from(r):
    before = snapshot_at(CAL, r["known_from"] - 1)
    assert not any(e.event_id.startswith(r["event_id"]) for e in before.events)
    assert not blocked_at([r], r["known_from"] - 1)


def test_news_engine_blocks_point_in_time_around_fomc():
    eng = NewsEngine()
    t = ts(2015, 12, 16, 18, 30)
    eng.load(snapshot_at(CAL, t))
    assert eng.evaluate("XAUUSD", ("XAU", "USD"), t).verdict.value == "BLOCK"
    t2 = ts(2015, 12, 16, 15, 0)
    eng.load(snapshot_at(CAL, t2))
    assert eng.evaluate("XAUUSD", ("XAU", "USD"), t2).verdict.value == "ALLOW"


def test_pre_2013_release_bracket_is_fully_covered():
    r = BY[("2012-01-25", "scheduled")]
    lo, hi = r["window_utc"]
    assert lo == ts(2012, 1, 25, 17, 30) and hi == ts(2012, 1, 25, 19, 15)
    eng = NewsEngine()
    for t in range(lo - 3000, hi + 1500, 300):
        eng.load(snapshot_at(CAL, t))
        assert eng.evaluate("USDJPY", ("USD", "JPY"), t).verdict.value == "BLOCK", t
    assert blocked_at(CAL, lo - 3000) and blocked_at(CAL, hi + 1500)
