"""MEMORY PRESSURE and CPU STARVATION (EXECUTED in a single Linux container).

Memory: the process caps its own address space (RLIMIT_AS) and fills it with ballast at a lifecycle
boundary, so the rest of the cycle runs out of memory. Whatever happens (fail-closed halt, crash),
a fresh process must restore every safety invariant.
CPU: fence actors compete while CPU burners oversubscribe every core; the fence invariants must hold.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

import pytest
from test_crash_matrix import SCEN, check
from test_split_brain import assert_fence_invariants, events, spawn

PRESSURE_POINTS = ["after_candidate", "before_risk", "before_permit", "exec_after_intent_persisted",
                   "exec_before_broker_send", "exec_after_fenced_record_before_send", "after_submit_before_outcome"]


def run(wd, phase, **env):
    e = {k: v for k, v in os.environ.items() if not k.startswith("SENTINEL_")}
    e.update(env)
    return subprocess.run([sys.executable, str(SCEN), str(wd), phase], capture_output=True, text=True, env=e, timeout=180)


@pytest.mark.slow
@pytest.mark.parametrize("point", PRESSURE_POINTS)
def test_memory_pressure_during_entry_then_restart_invariants(tmp_path, point):
    p = run(tmp_path, "open", SENTINEL_MEM_PRESSURE_AT=point)
    assert "PRESSURE_APPLIED" in p.stdout, (p.stdout[-800:], p.stderr[-1500:])
    cyc = [x for x in p.stdout.splitlines() if x.startswith("CYCLE ")]
    if cyc:  # survived the allocation failure: it must have failed CLOSED
        res = json.loads(cyc[0].split(" ", 1)[1])
        assert res["status"] == "HALTED" and "MemoryError" in res["reason"], res
    r = run(tmp_path, "restart_early")
    assert r.returncode == 0, r.stderr[-2000:]
    inv = json.loads([x for x in r.stdout.splitlines() if x.startswith("INVARIANTS ")][0].split(" ", 1)[1])
    check(inv)
    assert inv["in_deals"] <= 1 and inv["positions"] == inv["in_deals"]
    print(point, "rc", p.returncode, cyc[0][:200] if cyc else "no cycle result (process died)")


@pytest.mark.slow
def test_cpu_starvation_fence_holds_under_oversubscription(tmp_path):
    n = (os.cpu_count() or 2) * 4
    burners = [subprocess.Popen([sys.executable, "-c", "while True: pass"]) for _ in range(n)]
    try:
        time.sleep(0.5)
        pa = spawn(tmp_path, "A", 8)
        pb = spawn(tmp_path, "B", 8)
        ea, eb = events(pa, timeout=120), events(pb, timeout=120)
    finally:
        for b in burners:
            b.kill()
            b.wait()
    rows = assert_fence_invariants(tmp_path)
    assert rows
    print("cpu-starved sends", len(rows), "refusals", sum(e["event"] == "REFUSED" for e in ea + eb))
