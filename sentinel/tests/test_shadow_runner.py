"""Continuous SHADOW runner: one decision per closed bar, fail-closed feed handling, restart, counterfactuals."""

from __future__ import annotations

from conftest import NOW, WALL, H, fixture_calendar, trending_bars

from sentinel.journal import Journal
from sentinel.shadow_runner import ShadowConfig, ShadowRunner

CFG = ShadowConfig(instruments=("XAUUSD",), account_start_utc=NOW - 30 * 86400)


def runner(tmp_path, clock, bars_fn=None, cal_fn=None):
    bars = {"XAUUSD": trending_bars()}
    return ShadowRunner(str(tmp_path), CFG, bars_fn or (lambda: bars), cal_fn or (lambda now: fixture_calendar(fetched_at=now)),
                        clock, wall_clock=WALL)


def test_one_decision_per_closed_bar_and_no_execution(tmp_path, clock):
    r = runner(tmp_path, clock)
    out = r.step()
    assert out["status"] == "DECIDED" and out["result"]["candidates"][0]["verdict"] == "WOULD_EXECUTE"
    clock.advance(600)
    assert r.step()["status"] == "ALREADY_DECIDED"
    j = Journal(str(tmp_path / "sys" / "journal-A.db"))
    assert not list(j.events(type_="INTENT_PERSISTED")) and not list(j.events(type_="ORDER_SENT"))


def test_feed_outage_keeps_old_bars_then_goes_stale_without_deciding(tmp_path, clock):
    state = {"fail": False}
    bars = {"XAUUSD": trending_bars()}

    def fetch():
        if state["fail"]:
            raise ConnectionError("feed down")
        return bars

    r = runner(tmp_path, clock, bars_fn=fetch)
    assert r.step()["status"] == "DECIDED"
    state["fail"] = True
    clock.advance(4 * H)
    assert r.step()["status"] == "FEED_STALE"
    types = [e.type for e in r.ops.events()]
    assert "FEED_ERROR" in types and "FEED_STALE" in types
    assert sum(t == "SHADOW_CYCLE" for t in types) == 1


def test_calendar_outage_fails_closed_through_news_gate(tmp_path, clock):
    stale = fixture_calendar(fetched_at=NOW - 7 * 3600)

    def cal(now):
        raise TimeoutError("calendar unreachable")

    r = runner(tmp_path, clock, cal_fn=cal)
    r.calendar = stale  # last good snapshot is too old
    out = r.step()
    assert out["result"]["candidates"][0]["stage"] == "news"
    assert out["result"]["candidates"][0]["verdict"] == "BLOCK"


def test_restart_does_not_decide_the_same_bar_twice(tmp_path, clock):
    assert runner(tmp_path, clock).step()["status"] == "DECIDED"
    assert runner(tmp_path, clock).step()["status"] == "ALREADY_DECIDED"  # new process, same disk state


def test_counterfactual_resolved_once_only_after_full_horizon_and_never_touches_production(tmp_path, clock):
    short = {"XAUUSD": trending_bars()}
    r = runner(tmp_path, clock, bars_fn=lambda: short)
    out = r.step()
    assert out["counterfactuals_resolved"] == 0  # horizon not observable yet: never resolved on partial data
    prod = Journal(str(tmp_path / "sys" / "journal-A.db"))
    n_prod = len(list(prod.events()))
    r.bars = {"XAUUSD": trending_bars(n=1600 + 30, end_ts=NOW + 30 * H)}  # same path, 30 more bars
    assert r.resolve_counterfactuals() == 1
    assert r.resolve_counterfactuals() == 0
    cf = list(r.cf.events(type_="COUNTERFACTUAL"))[0].payload
    assert cf["verdict"] == "WOULD_EXECUTE" and isinstance(cf["r"], float) and cf["provenance"].startswith("PROXY")
    assert len(list(prod.events())) == n_prod
    assert r.counterfactual_summary()


def test_in_progress_unaligned_bar_does_not_create_new_decisions(tmp_path, clock):
    """D4 regression (found in the live shadow run): Yahoo appends an in-progress bar stamped at the
    latest minute; each poll must still map to the same closed bar."""
    import dataclasses

    base = trending_bars()
    state = {"k": 0}

    def fetch():
        state["k"] += 1
        partial = dataclasses.replace(base[-1], ts=int(clock.now()) - 60)  # "now"-stamped partial bar
        return {"XAUUSD": base + [partial]}

    r = runner(tmp_path, clock, bars_fn=fetch)
    assert r.step()["status"] == "DECIDED"
    for _ in range(3):
        clock.advance(300)
        assert r.step()["status"] == "ALREADY_DECIDED"
