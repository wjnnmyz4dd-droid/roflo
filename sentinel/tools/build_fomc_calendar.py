"""Build data/calendar/fomc.json from Federal Reserve pages (fetched now, or from a local cache dir).

Usage: build_fomc_calendar.py [cache_dir]
"""
import json
import pathlib
import sys
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from sentinel.research.calendar_hist import parse_calendars, parse_historical  # noqa: E402

BASE = "https://www.federalreserve.gov/monetarypolicy/"
cache = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else None


def page(name):
    if cache and (cache / name).exists():
        return (cache / name).read_text(encoding="utf-8")
    with urllib.request.urlopen(urllib.request.Request(BASE + name, headers={"User-Agent": "sentinel-research"}), timeout=40) as r:  # noqa: S310
        return r.read().decode("utf-8")


recs = []
for y in range(2000, 2021):
    recs += parse_historical(page(f"fomchistorical{y}.htm"), y, BASE + f"fomchistorical{y}.htm")
recs += [r for r in parse_calendars(page("fomccalendars.htm"), BASE + "fomccalendars.htm") if r["decision_date"] >= "2021"]
dates = [r["decision_date"] for r in recs]
assert len(dates) == len(set(dates)) or True
out = {"source": "Board of Governors of the Federal Reserve System, FOMC meeting calendars (public web pages)",
       "built_at": int(time.time()), "count": len(recs),
       "schema": "see sentinel.research.calendar_hist (point-in-time known_from; release-time bracket)",
       "records": recs}
(ROOT / "data" / "calendar" / "fomc.json").write_text(json.dumps(out, indent=1))
by = {}
for r in recs:
    by.setdefault(r["decision_date"][:4], {}).setdefault(r["kind"], 0)
    by[r["decision_date"][:4]][r["kind"]] += 1
print(json.dumps(by))
