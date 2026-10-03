"""News attribution on the FROZEN phase-1 L1 baseline (no parameter changes, nothing promoted).

For each cadence the Finder-only and Finder+Validator+Arbiter variants are replayed twice:
without and with the point-in-time FOMC gate (central-bank window: 60 min before .. 30 min after,
same as NewsEngine's BlackoutPolicy). Reports what the gate removed and what the removed trades
would have earned (counterfactual). Also reports holding-period exposure: trades whose holding
window contained an FOMC release (the decision-time gate does not see these).
Data is PROXY / RESEARCH-ONLY. Development period is primary; the holdout period is shown for
information only (L1 is already failed and frozen; nothing here selects anything).
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tools")]

from run_research import INSTR, SPLIT_D, data, hourly_split  # noqa: E402

from sentinel.finder import FinderParams  # noqa: E402
from sentinel.research import edge  # noqa: E402
from sentinel.research.calendar_hist import blocked_at  # noqa: E402
from sentinel.system import DEFAULT_COSTS  # noqa: E402

CAL = json.loads((ROOT / "data" / "calendar" / "fomc.json").read_text())["records"]
H_BARS = FinderParams().horizon_bars


def gate(t):
    return bool(blocked_at(CAL, t))


def releases_in(a, b):
    return [r for r in CAL if r["kind"] == "scheduled" and a <= r["window_utc"][0] <= b]


def main():
    out = {"provenance": "PROXY / RESEARCH-ONLY (Yahoo proxies); calendar: federalreserve.gov FOMC pages, point-in-time",
           "scope": "FOMC only; NFP/CPI/other releases NOT covered (sources blocked)", "results": {}}
    D, H = data("1d"), data("1h")
    h_split, _ = hourly_split(H)
    for cad, src, bs, split in (("daily", D, 86400, SPLIT_D), ("hourly", H, 3600, h_split)):
        for period, s, e in (("development", None, split), ("holdout_info_only", split, None)):
            for variant, kw in (("finder", {}), ("finder+validator+arbiter", {"use_validator": True, "use_arbiter": True})):
                base, gated, removed, exposed = [], [], [], []
                for ins in INSTR:
                    bars, _ = src[ins]
                    b = edge.run(bars, ins, DEFAULT_COSTS[ins], start_ts=s, end_ts=e, bar_seconds=bs, **kw)
                    g = edge.run(bars, ins, DEFAULT_COSTS[ins], start_ts=s, end_ts=e, bar_seconds=bs, skip=gate, **kw)
                    gk = {(t.signal_ts, t.direction) for t in g}
                    base += [t.r for t in b]
                    gated += [t.r for t in g]
                    removed += [t.r for t in b if (t.signal_ts, t.direction) not in gk and gate(t.signal_ts + bs)]
                    exposed += [t.r for t in b if releases_in(t.signal_ts + bs, t.signal_ts + bs * (H_BARS + 1))]
                out["results"][f"{cad}/{period}/{variant}"] = {
                    "baseline": edge.stats(base), "with_fomc_gate": edge.stats(gated),
                    "removed_by_gate": {"n": len(removed), "mean_r": round(sum(removed) / len(removed), 4) if removed else None,
                                        "sum_r": round(sum(removed), 3)},
                    "held_through_fomc_not_gated": edge.stats(exposed),
                }
                print(cad, period, variant, json.dumps(out["results"][f"{cad}/{period}/{variant}"]["removed_by_gate"]),
                      "base n", len(base), "gated n", len(gated), flush=True)
    (ROOT / "reports" / "news_attribution.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
