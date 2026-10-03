"""Run the data-quality gate over every research dataset. Writes reports/data_quality.json."""
import json, pathlib, sys
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from sentinel.dataquality import DatasetProvenance, bars_hash, check  # noqa: E402
from sentinel.research.data import load  # noqa: E402

CLASSES = {"XAUUSD": "metal", "USOIL": "energy", "USDJPY": "forex", "BTCUSD": "crypto"}
out = {}
for ins, ac in CLASSES.items():
    for iv, sec in (("1d", 86400), ("1h", 3600)):
        bars, meta = load(ins, iv, str(ROOT / "data"))
        prov = DatasetProvenance("yahoo-chart-api", "PROXY", None, ins, meta["proxy_symbol"], ac, iv, "UTC (epoch)",
                                 bars[0].ts, bars[-1].ts, len(bars), None, meta["retrieved_at"], meta["sha256"],
                                 [f"dropped {meta['dropped']} rows at load (null / non-positive / OHLC-inconsistent / duplicate)"])
        brk = (21, 22) if ins in ("XAUUSD", "USOIL") else ()  # CME Globex daily maintenance (GC=F, CL=F proxies)
        rep = check(bars, prov, sec, trades_weekends=(ins == "BTCUSD"), daily_break_hours_utc=brk)
        out[f"{ins}_{iv}"] = {"verdict": rep.verdict, "reasons": rep.reasons, "checks": rep.checks, "provenance": rep.provenance}
        print(f"{ins:7} {iv}: {rep.verdict:11} missing={rep.checks['missing_fraction']:.3%} outliers={rep.checks['return_outliers']} dropped={meta['dropped']} sat={rep.checks['saturday_bars']}")
(ROOT / "reports" / "data_quality.json").write_text(json.dumps(out, indent=2, default=str))
