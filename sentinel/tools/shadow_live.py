"""SHADOW run on live data: fresh Yahoo hourly bars + live Forex Factory calendar + real clock.

There is no execution service (shadow mode), and the account is a simulated 100k account.
Writes reports/shadow_live.json. Usage: shadow_live.py <workdir>
"""

from __future__ import annotations

import collections
import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sentinel.news import fetch_forex_factory  # noqa: E402
from sentinel.research.data import fetch, load  # noqa: E402
from sentinel.runtime import SystemClock  # noqa: E402
from sentinel.system import DEFAULT_COSTS, ReplayMarket, build  # noqa: E402

INSTR = ["XAUUSD", "USOIL", "USDJPY", "BTCUSD"]


def main(wd):
    wd = pathlib.Path(wd)
    now = int(time.time())
    bars, metas = {}, {}
    for i in INSTR:
        metas[i] = fetch(i, "1h", now - 200 * 86400, now, str(wd / "data"))
        bars[i], _ = load(i, "1h", str(wd / "data"))
    clock = SystemClock()
    cal = fetch_forex_factory(now)
    market = ReplayMarket(bars, 3600, "yahoo-live", now)
    b = build(str(wd / "sys"), clock, market, INSTR, mode="shadow", account_start_utc=now - 86400, news_snapshot=cal)
    for i in INSTR:
        last = bars[i][-1]
        half = DEFAULT_COSTS[i].spread / 2
        b.broker.set_quote(i, last.close - half, last.close + half, last.ts + 3600)  # quote time = bar close (Yahoo is delayed)
    res = b.orch.cycle(int(clock.now()))
    j = b.journal
    out = {
        "run_at_utc": now, "mode": "shadow", "execution_service_present": b.comps.execution is not None,
        "cycle_result": res, "calendar": {"source": cal.source, "events": len(cal.events), "malformed": cal.malformed,
                                           "coverage": [cal.coverage_start, cal.coverage_end]},
        "last_bar_ts": {i: bars[i][-1].ts for i in INSTR},
        "data_sha256": {i: metas[i]["sha256"] for i in INSTR},
        "outcomes": [e.payload for e in j.events(type_="CANDIDATE_OUTCOME")],
        "news_decisions": [e.payload["decision"]["reasons"] for e in j.events(type_="NEWS_DECISION")],
        "broker_send_calls": [c for c in b.broker.calls if c.startswith("send")],
        "journal_verified": j.verify(),
        "event_types": collections.Counter(e.type for e in j.events()),
    }
    (ROOT / "reports" / "shadow_live.json").write_text(json.dumps(out, indent=2, default=str))
    print(json.dumps({k: out[k] for k in ("cycle_result", "execution_service_present", "broker_send_calls", "journal_verified")}, default=str))


if __name__ == "__main__":
    main(sys.argv[1])
