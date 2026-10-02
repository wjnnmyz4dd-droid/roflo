"""Full production pipeline replayed in PAPER mode over hourly bars, plus NO-TRADE counterfactuals.

Usage: pipeline_eval.py dev|holdout <workdir>
For each candidate, the stage that stopped it (or execution) is read from the journal and its
counterfactual outcome is simulated with the research trade model (same entry/stop/horizon rules).
"""

from __future__ import annotations

import bisect
import collections
import json
import pathlib
import statistics
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sentinel.research import edge  # noqa: E402
from sentinel.research.data import load  # noqa: E402
from sentinel.research.replay import run_replay  # noqa: E402
from sentinel.system import DEFAULT_COSTS  # noqa: E402

INSTR = ["XAUUSD", "USOIL", "USDJPY", "BTCUSD"]


def main(period, wd):
    bars = {}
    metas = {}
    for i in INSTR:
        bars[i], metas[i] = load(i, "1h", str(ROOT / "data"))
    first = min(b[0].ts for b in bars.values())
    last = min(b[-1].ts for b in bars.values())
    split = first + 365 * 86400
    start, end = (first + 120 * 86400, split) if period == "dev" else (split, last - 3600)  # 120d warm-up for history
    start -= start % 3600
    t0 = time.time()
    b, log = run_replay(wd, bars, 3600, start, end, mode="paper", retrieved_at=int(time.time()))
    elapsed = time.time() - t0
    j = b.journal
    idx = {i: [x.ts for x in bars[i]] for i in INSTR}
    cands = {e.payload["candidate"]["candidate_id"]: e.payload["candidate"] for e in j.events(type_="CANDIDATE")}
    by_stage = collections.defaultdict(list)
    reasons = collections.Counter()
    for e in j.events(type_="CANDIDATE_OUTCOME"):
        c = cands[e.payload["candidate_id"]]
        ins = c["instrument"]
        k = bisect.bisect_left(idx[ins], c["evidence"]["signal_bar_ts"])
        d = 1 if c["direction"] == "LONG" else -1
        sim = edge.simulate(bars[ins], k, d, c["invalidation_price"], c["horizon_bars"], DEFAULT_COSTS[ins])
        stage = f'{e.payload["stopped_at"]}:{e.payload["verdict"]}'
        if sim:
            by_stage[stage].append(sim[2])
        reasons[(stage, e.payload["reasons"][0][:90])] += 1
    deals = b.broker.deals_since(0)
    closed = [d for d in deals if d["kind"].startswith("OUT")]
    acct = b.broker.account()
    out = {
        "period": period, "start": start, "end": end, "cycles": len(log), "elapsed_s": round(elapsed, 1),
        "data_sha256": {i: metas[i]["sha256"] for i in INSTR},
        "statuses": collections.Counter(r["status"] for _, r in log),
        "journal_verified": j.verify(),
        "broker": {"balance": acct.balance, "equity": acct.equity, "open_positions": len(acct.positions),
                   "closed_trades": len(closed), "net_pnl_after_commission": round(sum(d["profit"] - d["commission"] for d in deals), 2),
                   "exit_kinds": collections.Counter(d["kind"] for d in closed)},
        "counterfactual_r_by_stage": {k: edge.stats(v) for k, v in sorted(by_stage.items())},
        "top_reasons": [[s, r, n] for (s, r), n in reasons.most_common(25)],
        "halts": [e.payload for e in j.events(type_="HALT")][:20],
    }
    path = ROOT / "reports" / f"pipeline_{period}.json"
    json.dump(out, open(path, "w"), indent=2, default=str)
    print(json.dumps({k: out[k] for k in ("cycles", "elapsed_s", "statuses", "broker")}, default=str))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
