"""MT5 adapter contract tests — SIMULATED against tests/fake_mt5.py (no real terminal available)."""

from __future__ import annotations

from types import SimpleNamespace as NS

import fake_mt5 as F
import pytest
from conftest import NOW, WALL, fixture_calendar, trending_bars

from sentinel.broker.base import BrokerError, BrokerUncertain
from sentinel.broker.mt5 import DemoAuthorization, MT5Broker, intent_comment
from sentinel.contracts import Direction
from sentinel.execution import intent_state
from sentinel.system import ReplayMarket, build

AUTH = DemoAuthorization("DEMO", (5550001,), ("Broker-Demo",), 777001)


def make(clock, trade_mode=F.ACCOUNT_TRADE_MODE_DEMO, auth=AUTH, login=5550001, offset=3):
    wall = NS(time=lambda: clock.now())
    term = F.FakeTerminal(wall, offset_hours=offset, trade_mode=trade_mode, login=login)
    br = MT5Broker(F.module(term), auth, {"XAUUSD": "metal"}, wall_clock=wall)
    br.connect()
    return term, br


def pipeline(tmp_path, clock, term, br):
    bars = {"XAUUSD": trending_bars()}
    last = bars["XAUUSD"][-1].close
    term.ticks["XAUUSD"] = (last - 0.15, last + 0.15)
    b = build(str(tmp_path), clock, ReplayMarket(bars, 3600, "fixture", NOW), ["XAUUSD"], mode="paper",
              account_start_utc=NOW - 30 * 86400, news_snapshot=fixture_calendar(), independence=0.8, broker=br, wall_clock=WALL)
    return b


@pytest.mark.parametrize("mode,auth,login,reason", [
    (F.ACCOUNT_TRADE_MODE_REAL, AUTH, 5550001, "not DEMO"),
    (F.ACCOUNT_TRADE_MODE_CONTEST, AUTH, 5550001, "not DEMO"),
    (F.ACCOUNT_TRADE_MODE_DEMO, None, 5550001, "no DEMO authorization"),
    (F.ACCOUNT_TRADE_MODE_DEMO, DemoAuthorization("LIVE", (5550001,), ("Broker-Demo",), 1), 5550001, "no DEMO authorization"),
    (F.ACCOUNT_TRADE_MODE_DEMO, AUTH, 9999999, "not in allow-list"),
])
def test_execution_disabled_unless_authorized_demo(clock, mode, auth, login, reason):
    term, br = make(clock, mode, auth, login)
    assert not br.execution_enabled and reason in br.disabled_reason
    with pytest.raises(BrokerError, match="EXECUTION DISABLED"):
        br.send_order("i1", "XAUUSD", Direction.LONG, 0.1, 1990.0, None, 20, 1)
    assert term.order_send_calls == 0  # nothing ever reached the terminal
    br.quote("XAUUSD")  # read-only access still works (shadow)


def test_relogin_to_real_account_after_connect_disables_execution(clock):
    term, br = make(clock)
    assert br.execution_enabled
    term.account.trade_mode = F.ACCOUNT_TRADE_MODE_REAL  # terminal switched to a live login
    with pytest.raises(BrokerError, match="EXECUTION DISABLED"):
        br.send_order("i1", "XAUUSD", Direction.LONG, 0.1, 1990.0, None, 20, 1)
    assert term.order_send_calls == 0


def test_server_time_offset_is_removed_and_irregular_offsets_are_uncertain(clock):
    term, br = make(clock, offset=3)
    assert br.server_time() == int(clock.now())
    assert br.quote("XAUUSD").ts == int(clock.now())
    term.offset = 3 * 3600 + 900  # 15 min off: stale tick or clock problem
    term2, br2 = make(clock, offset=0)
    term2.offset = 2 * 3600 + 700
    with pytest.raises(BrokerUncertain):
        br2.server_time()
    term3, br3 = make(clock, offset=3)
    br3.server_time()
    term3.offset = 2 * 3600  # DST switch on the broker server
    with pytest.raises(BrokerUncertain, match="offset changed"):
        br3.server_time()


def test_spec_comes_from_broker_metadata(clock):
    term, br = make(clock)
    term.symbols["XAUUSD"].trade_tick_value = 0.01  # e.g. a micro contract
    s = br.spec("XAUUSD")
    assert s.tick_value == 0.01 and s.volume_step == 0.01 and s.currencies == ("XAU", "USD")


@pytest.mark.parametrize("fault,exc", [("requote", BrokerError), ("no_money", BrokerError),
                                       ("none_result_no_fill", BrokerUncertain), ("none_result_after_fill", BrokerUncertain),
                                       ("timeout_retcode_after_fill", BrokerUncertain)])
def test_retcode_classification(clock, fault, exc):
    term, br = make(clock)
    term.faults.append(fault)
    with pytest.raises(exc):
        br.send_order("i1", "XAUUSD", Direction.LONG, 0.1, 1990.0, None, 20, 1)
    assert term.order_send_calls == 1


def test_pipeline_unknown_order_reconciled_through_mt5_history_without_resend(tmp_path, clock):
    term, br = make(clock)
    b = pipeline(tmp_path, clock, term, br)
    term.faults.append("none_result_after_fill")
    r = b.orch.cycle(NOW)
    assert r["candidates"][0]["verdict"] == "UNKNOWN" and term.order_send_calls == 1
    clock.advance(60)
    b.orch.cycle(int(clock.now()))
    iid = next(b.journal.events(type_="INTENT_PERSISTED")).payload["intent"]["intent_id"]
    assert intent_state(b.journal, iid) == "RESOLVED_CONFIRMED"
    assert term.order_send_calls == 1 and len(term.positions) == 1
    assert list(term.positions.values())[0].comment == intent_comment(iid)


def test_history_lag_is_not_guessed_as_not_executed(tmp_path, clock):
    term, br = make(clock)
    b = pipeline(tmp_path, clock, term, br)
    term.faults.append("none_result_after_fill")
    b.orch.cycle(NOW)
    term.history_lag = 50  # terminal history not synchronised yet
    clock.advance(60)
    r = b.orch.cycle(int(clock.now()))
    assert r["status"] == "HALTED"
    iid = next(b.journal.events(type_="INTENT_PERSISTED")).payload["intent"]["intent_id"]
    assert intent_state(b.journal, iid) == "UNKNOWN"  # NOT wrongly resolved as NOT_EXECUTED
    halts = [e.payload["scope"] for e in b.journal.events(type_="HALT")]
    assert "reconcile" in halts and "manual" not in halts  # retryable, not a false foreign-position alarm
    term.history_lag = 0
    clock.advance(60)
    b.orch.cycle(int(clock.now()))
    assert intent_state(b.journal, iid) == "RESOLVED_CONFIRMED" and term.order_send_calls == 1


def test_broker_rewritten_comment_fails_closed(tmp_path, clock):
    term, br = make(clock)
    term.comment_rewrite = lambda c: c[:10]  # broker truncates comments
    b = pipeline(tmp_path, clock, term, br)
    term.faults.append("none_result_after_fill")
    b.orch.cycle(NOW)
    clock.advance(60)
    r = b.orch.cycle(int(clock.now()))
    assert r["status"] == "HALTED"
    assert not b.comps.control.trading_permitted()[0]
    assert term.order_send_calls == 1 and len(term.positions) == 1


def test_partial_fill_and_filling_mode(tmp_path, clock):
    term, br = make(clock)
    b = pipeline(tmp_path, clock, term, br)
    term.faults.append("partial")
    b.orch.cycle(NOW)
    ack = next(b.journal.events(type_="ORDER_ACKED")).payload["ack"]
    assert ack["status"] == "PARTIAL" and ack["filled_volume"] < ack["requested_volume"]
    assert list(term.positions.values())[0].volume == ack["filled_volume"]


def test_shadow_with_real_account_reads_but_cannot_trade(tmp_path, clock):
    term, br = make(clock, F.ACCOUNT_TRADE_MODE_REAL)
    bars = {"XAUUSD": trending_bars()}
    last = bars["XAUUSD"][-1].close
    term.ticks["XAUUSD"] = (last - 0.15, last + 0.15)
    b = build(str(tmp_path), clock, ReplayMarket(bars, 3600, "fixture", NOW), ["XAUUSD"], mode="shadow",
              account_start_utc=NOW - 30 * 86400, news_snapshot=fixture_calendar(), independence=0.8, broker=br, wall_clock=WALL)
    r = b.orch.cycle(NOW)
    assert r["candidates"][0]["verdict"] == "WOULD_EXECUTE" and term.order_send_calls == 0
