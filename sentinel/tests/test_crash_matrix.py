"""Kill the process (os._exit, no cleanup) at every lifecycle boundary, restart, verify invariants."""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCEN = ROOT / "tools" / "crash_scenario.py"

OPEN_POINTS = [
    "cycle_start", "after_candidate", "during_validation", "after_arbiter", "before_risk", "after_risk",
    "before_permit", "exec_after_intent_persisted", "exec_before_broker_send",
    "exec_after_broker_ack_before_persist", "after_submit_before_outcome",
]
EXIT_POINTS = ["exit_before_close", "exec_close_before_send", "exec_close_after_send_before_persist"]


def run(wd, phase, crash=None, fault=""):
    env = dict(os.environ)
    env.pop("SENTINEL_CRASH_AT", None)
    if crash:
        env["SENTINEL_CRASH_AT"] = crash
    p = subprocess.run([sys.executable, str(SCEN), str(wd), phase] + ([fault] if fault else []),
                       capture_output=True, text=True, env=env, timeout=120)
    return p


def invariants(wd, early=False):
    p = run(wd, "restart_early" if early else "restart")
    assert p.returncode == 0, p.stderr[-2000:]
    line = [x for x in p.stdout.splitlines() if x.startswith("INVARIANTS ")][0]
    return json.loads(line.split(" ", 1)[1])


def check(inv, expect_positions=None):
    assert inv["journal_ok"]
    assert inv["dup_client_ids"] == 0, "duplicate order for one intent"
    assert inv["unauthorized_in_deals"] == [], "broker fill without a persisted, permitted intent"
    assert inv["unresolved"] == [], "an intent with unknown outcome survived reconciliation"
    assert inv["orphans"] == [], "broker position unknown to the journal"
    assert inv["pnl_mismatch"] == [] and inv["closed_not_journaled"] == [], "P&L not equal to broker truth"
    assert inv["permitted"][0], inv["permitted"]
    if expect_positions is not None:
        assert inv["positions"] == expect_positions


@pytest.mark.slow
@pytest.mark.parametrize("point", OPEN_POINTS)
def test_crash_during_entry(tmp_path, point):
    p = run(tmp_path, "open", crash=point)
    assert p.returncode == 137, f"crash point {point} never reached: {p.stdout[-500:]} {p.stderr[-1500:]}"
    inv = invariants(tmp_path, early=True)
    check(inv)
    # At most one position for the opportunity: a crash before send leaves the intent NOT_EXECUTED and the
    # restarted process may legitimately re-decide (new candidate, new permit); after send, the original
    # intent is CONFIRMED and risk refuses a second position on the instrument.
    assert inv["in_deals"] <= 1
    assert inv["positions"] == inv["in_deals"]


@pytest.mark.slow
@pytest.mark.parametrize("point", EXIT_POINTS)
def test_crash_during_exit(tmp_path, point):
    assert run(tmp_path, "open").returncode == 0
    p = run(tmp_path, "exit", crash=point)
    assert p.returncode == 137, p.stderr[-1500:]
    inv = invariants(tmp_path)
    check(inv)
    closed = point == "exec_close_after_send_before_persist"
    assert inv["positions"] == (0 if closed else 1) or inv["positions"] == 0  # restart's own exit rule may close it


@pytest.mark.slow
def test_crash_after_timeout_that_actually_filled(tmp_path):
    p = run(tmp_path, "open", crash="after_submit_before_outcome", fault="timeout_after_fill")
    assert p.returncode == 137
    inv = invariants(tmp_path, early=True)
    check(inv, expect_positions=1)
    assert inv["in_deals"] == 1


@pytest.mark.slow
def test_crash_during_partial_fill_records_broker_volume(tmp_path):
    p = run(tmp_path, "open", crash="exec_after_broker_ack_before_persist", fault="partial:0.5")
    assert p.returncode == 137
    inv = invariants(tmp_path, early=True)
    check(inv, expect_positions=1)
    assert inv["resolved_volumes"] == inv["broker_volumes"]  # the reconciled volume is what the broker filled


@pytest.mark.slow
def test_crash_during_learning_is_idempotent(tmp_path):
    assert run(tmp_path, "open").returncode == 0
    assert run(tmp_path, "exit").returncode == 0  # closes via horizon rule
    assert run(tmp_path, "learn", crash="learning_after_fact").returncode == 137
    assert run(tmp_path, "learn").returncode == 0
    from sentinel.journal import Journal

    mem = Journal(str(tmp_path / "memory.db"))
    assert mem.verify()[0]
    facts = [e.payload["journal_seq"] for e in mem.events(type_="FACT")]
    assert len(facts) == len(set(facts)) >= 1


@pytest.mark.slow
def test_crash_during_promotion_evaluation(tmp_path):
    assert run(tmp_path, "promote", crash="promotion_before_record").returncode == 137
    p = run(tmp_path, "promote")
    assert p.returncode == 0, p.stderr[-1500:]
    stages = json.loads([x for x in p.stdout.splitlines() if x.startswith("STAGES")][0].split(" ", 1)[1])
    assert stages == ["RESEARCH_TEST", "BACKTEST", "LEAKAGE_CHECK"]


@pytest.mark.slow
def test_manual_halt_survives_crash_and_restart(tmp_path):
    assert run(tmp_path, "open").returncode == 0
    from sentinel.control import ControlState
    from sentinel.journal import Journal
    from sentinel.runtime import FakeClock

    sys.path.insert(0, str(ROOT / "tests"))
    from conftest import NOW

    j = Journal(str(tmp_path / "journal-A.db"))
    ControlState(j, FakeClock(NOW)).halt("risk", "operator investigating", scope="manual")
    j.close()
    p = run(tmp_path, "restart")
    inv = json.loads([x for x in p.stdout.splitlines() if x.startswith("INVARIANTS")][0].split(" ", 1)[1])
    assert inv["permitted"][0] is False and "manual halt" in inv["permitted"][1]
