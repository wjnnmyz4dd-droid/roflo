"""Exactly-once intents, unknown outcomes, partial fills, reconciliation, split-brain, risk persistence."""

from __future__ import annotations

import pytest
from conftest import NOW, make_system

from sentinel.broker.sim import SimBroker
from sentinel.contracts import Direction
from sentinel.execution import intent_state, unresolved_intents
from sentinel.lease import Lease, LeaseLost
from sentinel.runtime import FakeClock


def _sends(b):
    return [c for c in b.broker.calls if c.startswith("send")]


def test_timeout_after_fill_is_unknown_halts_and_never_resends(tmp_path, clock):
    b = make_system(tmp_path, clock)
    b.broker.arm_fault("send", "timeout_after_fill")
    r = b.orch.cycle(NOW)
    assert r["candidates"][0]["verdict"] == "UNKNOWN"
    assert len(_sends(b)) == 1 and b.broker.open_position_count() == 1
    ok, why = b.comps.control.trading_permitted()
    assert not ok and "reconcile" in why
    # next cycle reconciles from broker truth; the intent resolves to CONFIRMED, nothing is re-sent
    clock.advance(60)
    b.orch.cycle(clock.now())
    iid = next(b.journal.events(type_="INTENT_PERSISTED")).payload["intent"]["intent_id"]
    assert intent_state(b.journal, iid) == "RESOLVED_CONFIRMED"
    assert len([c for c in _sends(b) if iid in c]) == 1
    assert b.broker.open_position_count() == 1


def test_timeout_before_fill_resolves_not_executed(tmp_path, clock):
    b = make_system(tmp_path, clock)
    b.broker.arm_fault("send", "timeout_before_fill")
    b.orch.cycle(NOW)
    assert b.broker.open_position_count() == 0
    res = b.comps.reconciler.run()
    assert res["clean"]
    iid = next(b.journal.events(type_="INTENT_PERSISTED")).payload["intent"]["intent_id"]
    assert intent_state(b.journal, iid) == "RESOLVED_NOT_EXECUTED"


def test_disconnect_mid_send_then_broker_unreachable_stays_halted(tmp_path, clock):
    b = make_system(tmp_path, clock)
    b.broker.arm_fault("send", "disconnect")
    b.orch.cycle(NOW)
    clock.t = NOW + 60
    r = b.orch.cycle(NOW + 60)
    assert r["status"] == "HALTED"
    assert not b.comps.control.trading_permitted()[0]
    b.broker.set_connected(True)
    clock.t = NOW + 120
    r = b.orch.cycle(NOW + 120)
    assert b.comps.control.trading_permitted()[0]
    assert b.broker.open_position_count() == 0  # disconnect fault fired before the fill


def test_definite_reject_and_requote_are_terminal_no_retry(tmp_path, clock):
    for kind in ("reject", "requote"):
        b = make_system(tmp_path / kind, FakeClock(NOW))
        b.broker.arm_fault("send", kind)
        r = b.orch.cycle(NOW)
        assert r["candidates"][0]["verdict"] == "REJECTED"
        assert len(_sends(b)) == 1 and b.broker.open_position_count() == 0


def test_partial_fill_records_actual_volume(tmp_path, clock):
    b = make_system(tmp_path, clock)
    b.broker.arm_fault("send", "partial", "0.5")
    b.orch.cycle(NOW)
    ack = next(b.journal.events(type_="ORDER_ACKED")).payload["ack"]
    pos = b.broker.account().positions[0]
    assert ack["status"] == "PARTIAL" and ack["filled_volume"] == pos.volume < ack["requested_volume"]


def test_duplicate_ack_recorded_once(tmp_path, clock):
    b = make_system(tmp_path, clock)
    b.orch.cycle(NOW)
    ev = next(b.journal.events(type_="ORDER_ACKED"))
    again = b.comps.execution.record_ack(ev.payload["intent_id"], ev.payload["ack"])
    assert again["status"] == "DUPLICATE_ACK_IGNORED"
    assert sum(1 for _ in b.journal.events(type_="ORDER_ACKED")) == 1


def test_same_candidate_cycle_twice_does_not_double_position(tmp_path, clock):
    b = make_system(tmp_path, clock)
    b.orch.cycle(NOW)
    b.orch.cycle(NOW)  # same bar again (scheduler duplication)
    assert b.broker.open_position_count() == 1  # risk: instrument already has a position


def test_foreign_position_halts_until_operator(tmp_path, clock):
    b = make_system(tmp_path, clock)
    b.broker.inject_foreign_position("XAUUSD", Direction.SHORT, 1.0, 2950.0)
    r = b.orch.cycle(NOW)
    assert r["status"] == "HALTED"
    assert not b.comps.control.trading_permitted()[0]
    assert b.broker.open_position_count() == 1  # nothing traded on top of unknown exposure


def test_stop_out_is_recorded_from_broker_deal_not_local_estimate(tmp_path, clock):
    b = make_system(tmp_path, clock)
    b.orch.cycle(NOW)
    pos = b.broker.account().positions[0]
    clock.advance(3600)
    b.broker.set_quote("XAUUSD", pos.stop_loss - 5, pos.stop_loss - 4.7, clock.now())  # gap through the stop
    b.comps.reconciler.run()
    closed = next(b.journal.events(type_="POSITION_CLOSED")).payload
    deal = [d for d in b.broker.deals_since(0) if d["kind"].startswith("OUT")][0]
    assert closed["source"] == "broker_deal" and closed["profit"] == deal["profit"] and closed["price"] < pos.stop_loss


# ------------------------------------------------------------------ split brain
def test_split_brain_only_one_leader_and_fence_rejects_stale(tmp_path):
    clk = FakeClock(NOW)
    a = Lease(str(tmp_path / "c.db"), "A", ttl=10, clock=clk)
    bb = Lease(str(tmp_path / "c.db"), "B", ttl=10, clock=clk)
    assert a.acquire() and not bb.acquire()
    ta = a.assert_leader()
    clk.advance(11)  # A paused (GC, VM freeze, partition) past expiry
    assert bb.acquire()
    tb = bb.assert_leader()
    assert tb > ta
    with pytest.raises(LeaseLost):
        a.assert_leader()  # A wakes up: refuses to act
    broker = SimBroker(str(tmp_path / "broker.db"), clock=clk)
    broker.set_quote("XAUUSD", 2000, 2000.3)
    broker.send_order("i-b", "XAUUSD", Direction.LONG, 0.1, 1990, None, 20, tb)
    from sentinel.broker.base import BrokerError

    with pytest.raises(BrokerError, match="STALE_FENCE"):
        broker.send_order("i-a", "XAUUSD", Direction.LONG, 0.1, 1990, None, 20, ta)  # A bypasses its own check
    assert broker.open_position_count() == 1


def test_split_brain_without_broker_fence_shows_the_residual_risk(tmp_path):
    """Real MT5 has no fencing. If a stale leader skips its local lease check, the broker accepts it.
    This test documents (and pins) that limitation rather than hiding it."""
    clk = FakeClock(NOW)
    broker = SimBroker(str(tmp_path / "broker.db"), clock=clk, enforce_fencing=False)
    broker.set_quote("XAUUSD", 2000, 2000.3)
    broker.send_order("i-b", "XAUUSD", Direction.LONG, 0.1, 1990, None, 20, 2)
    broker.send_order("i-a", "XAUUSD", Direction.LONG, 0.1, 1990, None, 20, 1)
    assert broker.open_position_count() == 2


def test_stale_lock_and_clock_disagreement(tmp_path):
    ca, cb = FakeClock(NOW), FakeClock(NOW + 4)  # B's clock runs 4s fast
    a = Lease(str(tmp_path / "c.db"), "A", ttl=10, guard=5, clock=ca)
    b = Lease(str(tmp_path / "c.db"), "B", ttl=10, guard=5, clock=cb)
    assert a.acquire()
    ca.advance(4)
    cb.advance(4)
    assert not b.acquire()  # B sees expiry at NOW+10 vs its clock NOW+8: still held
    ca.advance(2)  # A at NOW+6 is within guard of expiry (NOW+10-5) -> renews instead of acting blind
    assert a.assert_leader()
    a.release()
    assert b.acquire()  # released lock is immediately available


def test_cycle_in_standby_when_not_leader(tmp_path, clock):
    b = make_system(tmp_path, clock)
    other = Lease(str(tmp_path / "coord.db"), "B", ttl=30, clock=clock)
    clock.advance(31)
    assert other.acquire()
    assert b.orch.cycle(clock.now())["status"] == "STANDBY"
    assert b.broker.open_position_count() == 0


# ------------------------------------------------------------------ persistence of halts / risk
def test_halts_and_daily_loss_survive_restart(tmp_path, clock):
    b = make_system(tmp_path, clock)
    b.comps.control.halt("risk", "operator test", scope="manual")
    b.journal.close()
    b2 = make_system(tmp_path, clock, broker=b.broker)  # new process, same journal + broker
    b2.comps.reconciler.run()
    ok, why = b2.comps.control.trading_permitted()
    assert not ok and "manual halt" in why


def test_restart_requires_reconcile_before_trading(tmp_path, clock):
    b = make_system(tmp_path, clock)
    b.comps.reconciler.run()
    assert b.comps.control.trading_permitted()[0]
    b.comps.control.startup("A", {})  # a restart
    assert not b.comps.control.trading_permitted()[0]


def test_daily_loss_is_derived_from_broker_after_restart(tmp_path, clock):
    from sentinel.risk import RiskEngine, RiskLimits

    broker = SimBroker(str(tmp_path / "b.db"), clock=clock)
    broker.set_quote("XAUUSD", 2000, 2000.3)
    broker.send_order("i1", "XAUUSD", Direction.LONG, 10.0, 1900, None, 20, 1)
    broker.set_quote("XAUUSD", 1984, 1984.3)  # unrealised loss ~ -16,000 on 10 lots
    r = RiskEngine(RiskLimits(), 100_000.0)  # brand-new engine: no local memory at all
    d = r.account_check(broker.account(), broker.deals_since(0), int(clock.now()))
    assert d.verdict.value == "HALT" and "daily loss" in d.reasons[0]


def test_clock_drift_vs_broker_halts(tmp_path, clock):
    b = make_system(tmp_path, clock)
    r = b.orch.cycle(NOW + 120)  # local clock says +120s, broker server time disagrees
    assert r["status"] == "HALTED" and "drift" in r["reason"]
    assert b.broker.open_position_count() == 0


def test_unresolved_intents_listing(tmp_path, clock):
    b = make_system(tmp_path, clock)
    b.broker.arm_fault("send", "timeout_after_fill")
    b.orch.cycle(NOW)
    assert len(unresolved_intents(b.journal)) == 1


def test_local_position_missing_at_broker_without_deal_halts(tmp_path, clock):
    b = make_system(tmp_path, clock)
    b.orch.cycle(NOW)
    ticket = b.broker.account().positions[0].ticket
    b.broker._db.execute("DELETE FROM positions WHERE ticket=?", (ticket,))  # vanished with no closing deal
    res = b.comps.reconciler.run()
    assert not res["clean"] and any(f["kind"] == "LOCAL_POSITION_MISSING_AT_BROKER" for f in res["findings"])
    assert not b.comps.control.trading_permitted()[0]


def test_lease_stolen_while_locally_still_valid_is_detected(tmp_path):
    ca, cb = FakeClock(NOW), FakeClock(NOW + 100)  # B's clock is far ahead (clock disagreement)
    a = Lease(str(tmp_path / "c.db"), "A", ttl=30, guard=2, clock=ca)
    b = Lease(str(tmp_path / "c.db"), "B", ttl=30, guard=2, clock=cb)
    assert a.acquire()
    assert b.acquire()  # B believes A's lease expired
    with pytest.raises(LeaseLost, match="another instance"):
        a.assert_leader()  # A's own expiry has not passed, but it must notice it lost the lease
