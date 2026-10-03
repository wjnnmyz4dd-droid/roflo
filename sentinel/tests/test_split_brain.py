"""Split-brain: fenced send ledger (in-process) and real multi-process races (EXECUTED on one host).

The broker here has fencing DISABLED, like MT5, so only Sentinel's lease + ``Lease.record_send``
stand between a stale leader and the broker. Two-host tests are NOT EXECUTED (single container).
"""

from __future__ import annotations

import json
import os
import pathlib
import signal
import sqlite3
import subprocess
import sys
import time

import pytest
from conftest import NOW, make_system

import sentinel.execution as execution
from sentinel.broker.sim import SimBroker
from sentinel.lease import Lease

ROOT = pathlib.Path(__file__).resolve().parents[1]
ACTOR = ROOT / "tools" / "fence_actor.py"


# ------------------------------------------------------------------ in-process
def test_execution_refuses_send_when_lease_lost_between_check_and_send(tmp_path, clock, monkeypatch):
    broker = SimBroker(str(tmp_path / "broker.db"), clock=clock, enforce_fencing=False)
    b = make_system(tmp_path, clock, broker=broker)
    thief_clock = type(clock)(clock.now() + 1000)  # B's clock far ahead: it believes A expired
    thief = Lease(str(tmp_path / "coord.db"), "B", ttl=30, clock=thief_clock)
    real = execution.crashpoint

    def steal(label):
        if label == "exec_before_broker_send":
            assert thief.acquire()
        real(label)

    monkeypatch.setattr(execution, "crashpoint", steal)
    r = b.orch.cycle(NOW)
    assert r["candidates"][0]["verdict"] == "REJECTED"
    rej = next(b.journal.events(type_="ORDER_REJECTED")).payload
    assert rej["error"].startswith("fence:")
    assert not [c for c in broker.calls if c.startswith("send")]  # nothing reached the broker
    assert broker.open_position_count() == 0


def test_reconciler_sees_other_instance_in_flight_send_then_late_fill_is_manual(tmp_path, clock):
    from sentinel.contracts import Direction

    a = Lease(str(tmp_path / "coord.db"), "A", ttl=30, clock=clock)
    assert a.acquire()
    tok = a.record_send("late-x")  # A recorded its send, then froze before calling the broker
    clock.advance(31)
    broker = SimBroker(str(tmp_path / "broker.db"), clock=clock, enforce_fencing=False)
    b = make_system(tmp_path, clock, instance="B", broker=broker)
    res = b.comps.reconciler.run()
    f = [x for x in res["findings"] if x["kind"] == "IN_FLIGHT_SEND_BY_OTHER_INSTANCE"]
    assert f and f[0]["owner"] == "A" and not res["clean"]
    assert not b.comps.control.trading_permitted()[0]
    halts = [e.payload["scope"] for e in b.journal.events(type_="HALT")]
    assert halts[-1] == "reconcile"
    # A wakes and its (already-fenced) send lands late: residual risk, caught as foreign -> operator
    broker.send_order("late-x", "XAUUSD", Direction.LONG, 0.01, 1990.0, None, 20, tok)
    clock.advance(5)
    res = b.comps.reconciler.run()
    assert any(x["kind"].startswith("FOREIGN") for x in res["findings"])
    assert [e.payload["scope"] for e in b.journal.events(type_="HALT")][-1] == "manual"


# ------------------------------------------------------------------ multi-process (real OS processes, real clock)
def spawn(wd, owner, seconds, pause="", **env):
    e = dict(os.environ)
    e.update({k: str(v) for k, v in env.items()})
    return subprocess.Popen([sys.executable, str(ACTOR), str(wd), owner, str(seconds)] + ([pause] if pause else []),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=e)


def events(proc, timeout=60):
    out, err = proc.communicate(timeout=timeout)
    assert "Traceback" not in err, err[-2000:]
    return [json.loads(x) for x in out.splitlines() if x.startswith("{")]


def wait_for(proc_out_path_or_pred, timeout=15.0):
    end = time.time() + timeout
    while time.time() < end:
        if proc_out_path_or_pred():
            return True
        time.sleep(0.05)
    return False


def ledger(wd):
    db = sqlite3.connect(str(wd / "coord.db"))
    try:
        rows = db.execute("SELECT intent_id, owner, token, ts FROM sends").fetchall()
    except sqlite3.OperationalError:  # schema not created yet by the actor
        rows = []
    db.close()
    return rows


def broker_clients(wd):
    db = sqlite3.connect(str(wd / "broker.db"))
    cols = [r[1] for r in db.execute("PRAGMA table_info(positions)")]
    col = "client_id" if "client_id" in cols else cols[1]
    rows = [r[0] for r in db.execute(f"SELECT {col} FROM positions")]
    db.close()
    return rows


def assert_fence_invariants(wd):
    rows = ledger(wd)
    owners_by_token: dict[int, set] = {}
    for _, owner, token, _ in rows:
        owners_by_token.setdefault(token, set()).add(owner)
    assert all(len(v) == 1 for v in owners_by_token.values()), owners_by_token  # one writer per fencing epoch
    recorded = {r[0] for r in rows}
    unfenced = [c for c in broker_clients(wd) if c not in recorded]
    assert not unfenced, unfenced  # every order that reached the broker passed the fence
    # ledger order == token order: no send recorded under an older epoch after a newer one
    by_ts = sorted(rows, key=lambda r: r[3])
    toks = [r[2] for r in by_ts]
    assert toks == sorted(toks), toks
    return rows


def _sent_vs_ledger(evs, rows):
    """Every send was fenced; a ledger row without SENT is an in-flight attempt whose broker call errored
    (e.g. the shared sim-broker DB was busy under load) - legitimate, and it must have been reported."""
    sent = {e["intent"] for e in evs if e["event"] == "SENT"}
    recorded = {r[0] for r in rows}
    assert sent <= recorded
    assert len(recorded - sent) <= sum(e["event"] == "ERROR" for e in evs), (recorded - sent, evs[-5:])


def test_mp_two_processes_racing(tmp_path):
    pa, pb = spawn(tmp_path, "A", 5), spawn(tmp_path, "B", 5)
    ea, eb = events(pa), events(pb)
    rows = assert_fence_invariants(tmp_path)
    owners = {r[1] for r in rows}
    assert len(owners) >= 1 and rows
    _sent_vs_ledger(ea + eb, rows)


def test_mp_leader_kill9_then_standby_takes_over_after_ttl(tmp_path):
    pa = spawn(tmp_path, "A", 30)
    assert wait_for(lambda: any(r[1] == "A" for r in ledger(tmp_path)) if (tmp_path / "coord.db").exists() else False)
    pa.kill()  # SIGKILL: no release, no cleanup
    pa.wait()
    killed_at = time.time()
    pb = spawn(tmp_path, "B", 5)
    eb = events(pb)
    rows = assert_fence_invariants(tmp_path)
    b_sends = [e for e in eb if e["event"] == "SENT"]
    assert b_sends, eb
    assert min(e["t"] for e in b_sends) >= killed_at  # B never overlapped A
    assert min(r[2] for r in rows if r[1] == "B") > max(r[2] for r in rows if r[1] == "A")


def _paused(proc):
    # the actor SIGSTOPs itself; /proc state 'T' = stopped
    try:
        return open(f"/proc/{proc.pid}/stat").read().split(")")[1].split()[0] == "T"
    except FileNotFoundError:
        return False


def test_mp_leader_frozen_before_fenced_record_is_refused_on_wake(tmp_path):
    pa = spawn(tmp_path, "A", 30, pause="before_record", SENTINEL_ONE_SHOT=1)
    assert wait_for(lambda: _paused(pa))
    pb = spawn(tmp_path, "B", 4, SENTINEL_ONE_SHOT=1)
    eb = events(pb)
    assert any(e["event"] == "SENT" for e in eb), eb
    os.kill(pa.pid, signal.SIGCONT)
    ea = events(pa)
    assert any(e["event"] == "REFUSED" and "fenced send refused" in e["reason"] for e in ea), ea
    assert not [e for e in ea if e["event"] == "SENT" and e["intent"] == "A-1"]
    assert "A-1" not in broker_clients(tmp_path)
    assert_fence_invariants(tmp_path)


def test_mp_leader_frozen_after_fenced_record_sends_late_and_is_detectable(tmp_path):
    """RESIDUAL RISK (documented, not hidden): a leader frozen after the fenced record still sends on wake.
    The new leader can see the in-flight ledger row and halt (in-process test above covers the halt)."""
    pa = spawn(tmp_path, "A", 30, pause="after_record", SENTINEL_ONE_SHOT=1)
    assert wait_for(lambda: _paused(pa))
    time.sleep(2.6)  # past A's ttl
    b = Lease(str(tmp_path / "coord.db"), "B", ttl=2.0, guard=0.5)
    assert b.acquire()
    inflight = b.sends_by_others(since=0)
    assert [r[0] for r in inflight] == ["A-1"]
    os.kill(pa.pid, signal.SIGCONT)
    ea = events(pa)
    assert any(e["event"] == "SENT" and e["intent"] == "A-1" for e in ea)
    assert "A-1" in broker_clients(tmp_path)  # the late send landed: MT5 has no broker-side fence


def test_mp_coordination_store_unavailable_means_no_send(tmp_path):
    Lease(str(tmp_path / "coord.db"), "init", ttl=1)  # create schema
    hold = sqlite3.connect(str(tmp_path / "coord.db"), isolation_level=None)
    hold.execute("BEGIN EXCLUSIVE")  # partition: nobody can read or write the coordination store
    try:
        pa = spawn(tmp_path, "A", 3, SENTINEL_DB_TIMEOUT=0.3)
        out, err = pa.communicate(timeout=60)
    finally:
        hold.execute("ROLLBACK")
        hold.close()
    ea = [json.loads(x) for x in out.splitlines() if x.startswith("{")]
    assert not [e for e in ea if e["event"] == "SENT"]
    # fail-closed either way: the actor dies at startup or every cycle errors; it never sends
    assert "database is locked" in err or any(e["event"] == "ERROR" for e in ea)
    assert not (tmp_path / "broker.db").exists() or not broker_clients(tmp_path)


@pytest.mark.parametrize("skew", [+5.0, -5.0])
def test_mp_clock_disagreement(tmp_path, skew):
    pa = spawn(tmp_path, "A", 5)
    pb = spawn(tmp_path, "B", 5, SENTINEL_CLOCK_SKEW=skew)
    ea, eb = events(pa), events(pb)
    assert_fence_invariants(tmp_path)
    # with a skewed clock a takeover can happen early, but every stale attempt is refused by the fence
    _sent_vs_ledger(ea + eb, ledger(tmp_path))


def test_mp_simultaneous_creation_of_coordination_store(tmp_path):
    """D5 regression: N processes creating the lease store at the same instant must all start."""
    code = ("import sys, time; sys.path.insert(0, %r); from sentinel.lease import Lease; t0 = float(sys.argv[3]); "
            "time.sleep(max(0, t0 - time.time())); "
            "Lease(sys.argv[1] + '/coord.db', sys.argv[2], ttl=2, busy_timeout=10); print('OK')") % str(ROOT / "src")
    failures = []
    for rnd in range(10):
        wd = tmp_path / f"r{rnd}"
        wd.mkdir()
        t0 = time.time() + 1.0  # all processes released at the same instant
        ps = [subprocess.Popen([sys.executable, "-c", code, str(wd), f"o{k}", str(t0)], stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True) for k in range(12)]
        outs = [p.communicate(timeout=60) for p in ps]
        failures += [e[-200:] for o, e in outs if o.strip() != "OK"]
    assert not failures, failures[:3]
