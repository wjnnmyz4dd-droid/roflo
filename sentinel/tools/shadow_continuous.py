"""Continuous SHADOW. No execution service exists in this process (shadow mode).

  live   <workdir> <minutes> <poll_s>  real clock; Yahoo hourly proxy bars; live Forex Factory calendar.
                                       EXECUTED on PROXY / RESEARCH-ONLY data (no broker feed available).
  replay <workdir> <days>              FakeClock stepping hourly through stored proxy bars (last <days>),
                                       point-in-time FOMC calendar (federalreserve.gov). SIMULATED shadow.
Writes reports/shadow_<mode>.json.
"""
import bisect
import collections
import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sentinel.journal import Journal  # noqa: E402
from sentinel.news import fetch_forex_factory  # noqa: E402
from sentinel.research.calendar_hist import snapshot_at  # noqa: E402
from sentinel.research.data import fetch, load  # noqa: E402
from sentinel.runtime import FakeClock, SystemClock  # noqa: E402
from sentinel.shadow_runner import ShadowConfig, ShadowRunner  # noqa: E402

INSTR = ("XAUUSD", "USOIL", "USDJPY", "BTCUSD")


def report(r, wd, mode, extra):
    j = Journal(str(wd / "sys" / "journal-A.db"))
    outcomes = [e.payload for e in j.events(type_="CANDIDATE_OUTCOME")]
    out = {"mode": mode, "provenance": "PROXY / RESEARCH-ONLY", **extra,
           "ops_events": collections.Counter(e.type for e in r.ops.events()),
           "candidates": len(outcomes),
           "outcomes_by_stage": collections.Counter(f"{o['stopped_at']}:{o['verdict']}" for o in outcomes),
           "counterfactual_summary": r.counterfactual_summary(),
           "order_events_in_journal": sum(1 for _ in j.events_of_types(("INTENT_PERSISTED", "ORDER_SENT"))),
           "journal_verified": j.verify()[0], "ops_verified": r.ops.verify()[0], "cf_verified": r.cf.verify()[0]}
    (ROOT / "reports" / f"shadow_{mode}.json").write_text(json.dumps(out, indent=1, default=str))
    print(json.dumps(out, default=str)[:1500])


def live(wd, minutes, poll):
    def bars_fn():
        now = int(time.time())
        out = {}
        for i in INSTR:
            fetch(i, "1h", now - 120 * 86400, now, str(wd / "data"))
            out[i], _ = load(i, "1h", str(wd / "data"))
        return out

    r = ShadowRunner(str(wd), ShadowConfig(INSTR), bars_fn, fetch_forex_factory, SystemClock())
    end = time.time() + minutes * 60
    steps = []
    while time.time() < end:
        try:
            s = r.step()
        except Exception as e:  # noqa: BLE001 - a crash of the loop is recorded; the loop continues
            s = {"status": "LOOP_ERROR", "error": repr(e)[:300]}
            r.ops.append("LOOP_ERROR", "shadow_runner", s)
        steps.append({"t": int(time.time()), **{k: v for k, v in s.items() if k != "result"},
                      "cycle": s.get("result", {}).get("status") if isinstance(s.get("result"), dict) else None})
        print(json.dumps(steps[-1], default=str), flush=True)
        time.sleep(poll)
    report(r, wd, "live", {"minutes": minutes, "poll_s": poll, "steps": steps})


def replay(wd, days):
    allbars = {i: load(i, "1h", str(ROOT / "data"))[0] for i in INSTR}
    end = max(b[-1].ts for b in allbars.values()) + 3600
    start = end - days * 86400
    clock = FakeClock(start)
    cal = json.loads((ROOT / "data" / "calendar" / "fomc.json").read_text())["records"]
    idx = {i: [b.ts for b in v] for i, v in allbars.items()}

    def bars_fn():
        now = int(clock.now())
        return {i: allbars[i][:bisect.bisect_right(idx[i], now - 3600)] for i in INSTR}

    r = ShadowRunner(str(wd), ShadowConfig(INSTR, account_start_utc=start), bars_fn, lambda now: snapshot_at(cal, now),
                     clock)
    statuses = collections.Counter()
    while clock.now() < end:
        statuses[r.step()["status"]] += 1
        clock.advance(3600)
    r.bars = {i: allbars[i] for i in INSTR}
    r.resolve_counterfactuals()
    report(r, wd, "replay", {"days": days, "start": start, "end": end, "step_statuses": statuses,
                             "news_scope": "FOMC only (point-in-time); other releases not covered"})


if __name__ == "__main__":
    wd = pathlib.Path(sys.argv[2])
    if sys.argv[1] == "live":
        live(wd, float(sys.argv[3]), float(sys.argv[4]))
    else:
        replay(wd, int(sys.argv[3]))
