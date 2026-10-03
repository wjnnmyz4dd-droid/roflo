"""FTMO adversarial cases added in phase 2: swaps, commissions, exact-boundary floating losses,
restart during a restricted window, profile expiry mid-session / clock rollback, operator resume
cannot override compliance. Profiles remain UNREVIEWED and simulation-only."""

from __future__ import annotations

from conftest import NOW, WALL, make_system
from test_compliance_news import GOLD, _cal_with, engine, snap

from sentinel.contracts import GateVerdict
from sentinel.runtime import FakeClock, cet_midnight_utc


def test_swap_charged_at_midnight_counts_conservatively():
    e = engine(start=NOW - 5 * 86400)
    m = cet_midnight_utc(NOW)
    # a -300 swap booked exactly at CE(S)T midnight is treated as TODAY's flow -> higher day base -> stricter floor
    lim = e.limits(snap(99_700, 99_700), [{"ts": m, "profit": 0.0, "commission": 0.0, "swap": -300.0}], NOW)
    assert lim["day_base"] == 100_000 and lim["daily_limit"] == 95_000
    lim2 = e.limits(snap(99_700, 99_700), [{"ts": m - 1, "profit": 0.0, "commission": 0.0, "swap": -300.0}], NOW)
    assert lim2["daily_limit"] == 94_700


def test_accumulated_swaps_and_commissions_can_cause_halt():
    e = engine(start=NOW - 5 * 86400)
    m = cet_midnight_utc(NOW)
    deals = [{"ts": m + 60 * k, "profit": 0.0, "commission": 400.0, "swap": -600.0} for k in range(5)]  # -5,000 today
    bal = 100_000 - 5_000
    assert e.evaluate_account(snap(bal, bal), deals, NOW).verdict is GateVerdict.HALT


def test_commission_is_part_of_worst_case():
    e = engine(start=NOW - 60)
    # equity 99,000; floor 95,000; buffer 1,250 -> room 2,750. Gold 1 lot, 10.00 stop = 1000 ticks*1.0 +20 slip
    eq = 99_000
    ok = e.evaluate_trade(snap(1e5, eq), [], NOW, GOLD, 2.6, 2000.0, 1990.0, 0.0, 0.0)
    assert ok.verdict is GateVerdict.ALLOW  # 2.6 * 1020 = 2,652 < 2,750
    blocked = e.evaluate_trade(snap(1e5, eq), [], NOW, GOLD, 2.6, 2000.0, 1990.0, 40.0, 0.0)
    assert blocked.verdict is GateVerdict.BLOCK  # + 2*40*2.6 = 208 commission crosses


def test_floating_loss_exact_boundaries():
    e = engine(start=NOW - 60)
    assert e.evaluate_account(snap(1e5, 95_000.0), [], NOW).verdict is GateVerdict.HALT  # == limit is a breach
    assert e.evaluate_account(snap(1e5, 95_000.01), [], NOW).verdict is GateVerdict.BLOCK
    assert e.evaluate_account(snap(1e5, 96_250.0), [], NOW).verdict is GateVerdict.BLOCK  # == buffer edge
    assert e.evaluate_account(snap(1e5, 96_250.01), [], NOW).verdict is GateVerdict.ALLOW
    one = engine("ftmo-1step-challenge-standard", start=NOW - 60)
    assert one.evaluate_account(snap(1e5, 97_000.0), [], NOW).verdict is GateVerdict.HALT  # 3% daily on 1-step


def test_profile_expiry_mid_session_and_clock_rollback_halt():
    wall = FakeClock(WALL.now())
    e = engine(wall=wall, start=NOW - 60)
    assert e.evaluate_trade(snap(1e5, 1e5), [], NOW, GOLD, 0.1, 2000, 1990, 5, 0).verdict is GateVerdict.ALLOW
    wall.advance(40 * 86400)  # past valid_until: rules must be re-verified
    d = e.evaluate_trade(snap(1e5, 1e5), [], NOW, GOLD, 0.1, 2000, 1990, 5, 0)
    assert d.verdict is GateVerdict.HALT and "validity window" in d.reasons[0]
    back = engine(wall=FakeClock(WALL.now() - 400 * 86400), start=NOW - 60)  # host clock rolled back
    assert back.evaluate_account(snap(1e5, 1e5), [], NOW).verdict is GateVerdict.HALT
    assert back.evaluate_close(GOLD, NOW).verdict is GateVerdict.ALLOW  # an expired profile never traps a close


def test_restart_during_restricted_window_still_blocks(tmp_path, clock):
    cal = _cal_with("Non-Farm Employment Change", "USD", NOW + 60)
    b = make_system(tmp_path, clock, profile="ftmo-2step-account-standard", calendar=cal)
    r1 = b.orch.cycle(NOW)
    assert b.broker.open_position_count() == 0
    b.journal.close()
    b2 = make_system(tmp_path, clock, profile="ftmo-2step-account-standard", calendar=cal, broker=b.broker)  # restart
    r2 = b2.orch.cycle(NOW + 30)
    assert b2.broker.open_position_count() == 0
    reasons = str(r1) + str(r2)
    assert "restricted" in reasons or "news" in reasons


def test_operator_resume_cannot_override_a_compliance_breach(tmp_path, clock):
    b = make_system(tmp_path, clock)
    b.orch.cycle(NOW)  # opens a position
    q = b.broker.quote("XAUUSD")
    b.broker.set_quote("XAUUSD", q.bid - 80, q.ask - 80, clock.now())  # deep floating loss
    clock.advance(60)
    b.orch.cycle(int(clock.now()))
    assert not b.comps.control.trading_permitted()[0]
    b.comps.control.resume("operator", "operator says go")  # manual bypass attempt
    clock.advance(60)
    n_before = len([d for d in b.broker.deals_since(0) if d["kind"] == "IN"])
    r = b.orch.cycle(int(clock.now()))
    n_after = len([d for d in b.broker.deals_since(0) if d["kind"] == "IN"])
    assert n_after == n_before, r  # no new exposure while the FTMO floor is breached
    assert r["status"] == "HALTED" and "FTMO limit breached" in str(r["reason"])
