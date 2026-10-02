"""Subprocess body for crash tests. Usage: crash_scenario.py <workdir> <phase> [fault]

phase: open    -> one cycle that should open a position (crash may be injected via SENTINEL_CRASH_AT)
       exit    -> advance past the horizon so the exit path runs (crash may be injected)
       restart -> fresh process: reconcile + cycle, then print invariants as JSON
       learn   -> run post-mortem learning; promote -> record promotion stages
"""
import json
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]

from conftest import NOW, make_system  # noqa: E402

from sentinel.execution import unresolved_intents  # noqa: E402
from sentinel.journal import Journal  # noqa: E402
from sentinel.runtime import FakeClock  # noqa: E402

wd, phase = pathlib.Path(sys.argv[1]), sys.argv[2]
fault = sys.argv[3] if len(sys.argv) > 3 else ""
T = {"restart_early": NOW + 2 * 3600, "open": NOW, "exit": NOW + 11 * 3600, "restart": NOW + 12 * 3600, "learn": NOW + 13 * 3600, "promote": NOW + 13 * 3600}[phase]
clock = FakeClock(T)
b = make_system(wd, clock)
if phase == "restart_early":
    phase = "restart"
if phase in ("exit", "restart", "learn"):
    # market moved a little; keep position alive (above stop) unless the exit rule closes it
    last = b.broker.quote("XAUUSD")
    b.broker.set_quote("XAUUSD", last.bid + 1, last.ask + 1, T)
if fault:
    kind, _, arg = fault.partition(":")
    b.broker.arm_fault("send" if phase == "open" else "close", kind, arg)
if phase in ("open", "exit", "restart"):
    res = b.orch.cycle(T)
    print("CYCLE", json.dumps(res, default=str))
if phase == "learn":
    from sentinel.learning import Memory

    m = Memory(Journal(str(wd / "memory.db"), clock=clock), b.journal, clock)
    m.postmortem()
if phase == "promote":
    from sentinel.promotion import PromotionPipeline, STAGES

    pp = PromotionPipeline(Journal(str(wd / "promotion.db"), clock=clock), "fp")
    try:
        pid = pp.submit({"proposal_id": "p1"})
        h = pp.register_criteria(pid, {"x": 1})
    except Exception:
        h = pp._criteria_hash("p1")
    done = pp.passed_stages("p1")
    for st in STAGES[len(done):3]:
        pp.record("p1", st, True, {}, h)
    print("STAGES", json.dumps(pp.passed_stages("p1")))
if phase == "restart":
    j = b.journal
    deals = b.broker.deals_since(0)
    ins = [d for d in deals if d["kind"] == "IN"]
    outs = [d for d in deals if d["kind"].startswith("OUT")]
    persisted = {e.payload["intent"]["intent_id"] for e in j.events(type_="INTENT_PERSISTED")}
    permits = {e.payload["intent"]["intent_id"] for e in j.events(type_="PERMIT_ISSUED")}
    closed_j = {e.payload["ticket"]: e.payload["profit"] for e in j.events(type_="POSITION_CLOSED")}
    known_open = {}
    for e in j.events(type_="ORDER_ACKED"):
        known_open[e.payload["ack"]["ticket"]] = 1
    for e in j.events(type_="ORDER_RESOLVED"):
        if e.payload["outcome"] == "CONFIRMED":
            known_open[e.payload["ticket"]] = 1
    acct = b.broker.account()
    out = {
        "journal_ok": j.verify()[0],
        "in_deals": len(ins),
        "dup_client_ids": len(ins) - len({d["client_id"] for d in ins}),
        "unauthorized_in_deals": [d["client_id"] for d in ins if d["client_id"] not in persisted or d["client_id"] not in permits],
        "unresolved": unresolved_intents(j),
        "orphans": [p.ticket for p in acct.positions if p.ticket not in known_open],
        "pnl_mismatch": [d["ticket"] for d in outs if d["ticket"] in closed_j and abs(closed_j[d["ticket"]] - d["profit"]) > 1e-9],
        "closed_not_journaled": [d["ticket"] for d in outs if d["ticket"] not in closed_j],
        "permitted": b.comps.control.trading_permitted(),
        "positions": len(acct.positions),
        "resolved_volumes": [e.payload.get("filled_volume") for e in j.events(type_="ORDER_RESOLVED")],
        "broker_volumes": [d["volume"] for d in ins],
    }
    print("INVARIANTS", json.dumps(out, default=str))
