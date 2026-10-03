"""Dataset provenance + data-quality gate. Dirty data must not silently reach the evidence pipeline.

Verdicts:
  FAIL          - structural defects (invalid prices, out-of-order, unexplained gaps above threshold)
  PROXY_ONLY    - structurally usable but not broker data (no bid/ask, no symbol spec): research only
  BROKER_GRADE  - broker-sourced bars/ticks with spec metadata and spreads, all checks passed
"""

from __future__ import annotations

import hashlib
import math
import statistics
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sentinel.contracts import Bar


@dataclass
class DatasetProvenance:
    source: str
    source_kind: str  # "BROKER" | "PROXY"
    server: str | None
    symbol: str
    broker_symbol: str | None
    asset_class: str
    timeframe: str
    timezone: str
    first_ts: int | None
    last_ts: int | None
    bar_count: int
    tick_count: int | None
    download_ts: int
    data_hash: str
    corrections: list = field(default_factory=list)
    label: str = ""

    def __post_init__(self):
        if self.source_kind not in ("BROKER", "PROXY"):
            raise ValueError("source_kind must be BROKER or PROXY")
        if self.source_kind == "PROXY":
            self.label = "PROXY / RESEARCH-ONLY"


@dataclass
class QualityReport:
    verdict: str
    checks: dict
    reasons: list
    provenance: dict


def bars_hash(bars: list[Bar]) -> str:
    h = hashlib.sha256()
    for b in bars:
        h.update(f"{b.ts}|{b.open}|{b.high}|{b.low}|{b.close}|{b.volume}\n".encode())
    return h.hexdigest()


def _is_weekend_gap(a: int, b: int) -> bool:
    """Excused gap: spans a Saturday and is at most 4 days (weekend, or weekend + holiday)."""
    if b - a > 4 * 86400:
        return False
    t = a
    while t <= b:
        if datetime.fromtimestamp(t, UTC).weekday() == 5:
            return True
        t += 3600
    return False


def check(bars: list[Bar], prov: DatasetProvenance, bar_seconds: int, trades_weekends: bool, spreads: list[float] | None = None,
          spec_present: bool = False, max_gap_fraction: float = 0.02, outlier_mult: float = 15.0,
          daily_break_hours_utc: tuple = ()) -> QualityReport:
    """``daily_break_hours_utc``: hours at which the venue has a scheduled daily halt (e.g. CME Globex
    maintenance, 21:00 UTC in US summer time / 22:00 UTC in winter). A single missing bar starting at one
    of these hours is a session closure, not a data defect, and is counted separately."""
    checks: dict = {}
    reasons: list = []
    n = len(bars)
    bad_px = [b.ts for b in bars if not all(math.isfinite(x) and x > 0 for x in (b.open, b.high, b.low, b.close))]
    ohlc = [b.ts for b in bars if not (b.low <= min(b.open, b.close) <= max(b.open, b.close) <= b.high)]
    order = [bars[i].ts for i in range(1, n) if bars[i].ts <= bars[i - 1].ts]
    dups = n - len({b.ts for b in bars})
    checks["invalid_prices"] = len(bad_px)
    checks["ohlc_inconsistent"] = len(ohlc)
    checks["out_of_order_or_duplicate"] = len(order)
    checks["duplicate_timestamps"] = dups
    gaps, weekend_gaps, session_breaks = [], 0, 0
    for i in range(1, n):
        dt = bars[i].ts - bars[i - 1].ts
        if dt > bar_seconds * 1.5:
            gap_hour = datetime.fromtimestamp(bars[i - 1].ts + bar_seconds, UTC).hour
            if not trades_weekends and _is_weekend_gap(bars[i - 1].ts, bars[i].ts):
                weekend_gaps += 1
            elif bar_seconds == 3600 and dt == 2 * bar_seconds and gap_hour in daily_break_hours_utc:
                session_breaks += 1
            else:
                gaps.append((bars[i - 1].ts, bars[i].ts, dt // bar_seconds - 1))
    expected = (bars[-1].ts - bars[0].ts) // bar_seconds if n > 1 else 0
    missing = sum(g[2] for g in gaps)
    checks["missing_period_count"] = len(gaps)
    checks["missing_bars_outside_weekends"] = int(missing)
    checks["missing_fraction"] = round(missing / expected, 5) if expected else 0.0
    checks["weekend_gaps"] = weekend_gaps
    checks["scheduled_session_breaks"] = session_breaks
    checks["largest_gaps"] = sorted(gaps, key=lambda g: -g[2])[:5]
    wk = [b.ts for b in bars if datetime.fromtimestamp(b.ts, UTC).weekday() == 5]
    checks["saturday_bars"] = len(wk) if not trades_weekends else "n/a (24/7 market)"
    rets = [abs(math.log(bars[i].close / bars[i - 1].close)) for i in range(1, n) if bars[i].close > 0 and bars[i - 1].close > 0]
    med = statistics.median(rets) if rets else 0
    outl = [bars[i + 1].ts for i, r in enumerate(rets) if med > 0 and r > outlier_mult * med * 3]
    checks["return_outliers"] = len(outl)
    # timezone / DST: distribution of bar times of day (a daily feed whose bar time shifts by 1h at DST
    # transitions is exchange-session-stamped, not UTC-midnight bars)
    tods = {}
    for b in bars:
        t = b.ts % 86400
        tods[t] = tods.get(t, 0) + 1
    checks["distinct_bar_times_utc"] = len(tods) if bar_seconds >= 86400 else "n/a (intraday)"
    if bar_seconds >= 86400 and len(tods) > 1:
        ny = ZoneInfo("America/New_York")
        dst_linked = sum(1 for b in bars if (b.ts % 86400) == min(tods) and datetime.fromtimestamp(b.ts, ny).dst())
        checks["dst_shifted_session_stamps"] = dst_linked > 0
    checks["spreads"] = "UNAVAILABLE (no bid/ask)" if spreads is None else {
        "median": statistics.median(spreads), "p99": sorted(spreads)[int(0.99 * len(spreads))] if spreads else None,
        "nonpositive": sum(s <= 0 for s in spreads)}
    checks["symbol_spec"] = "present" if spec_present else "UNAVAILABLE (not broker metadata)"
    if bad_px or order or dups:
        reasons.append("structural defects: invalid prices / ordering / duplicates")
    if checks["missing_fraction"] > max_gap_fraction:
        reasons.append(f"missing bars {checks['missing_fraction']:.2%} > {max_gap_fraction:.0%} outside weekends")
    if ohlc:
        reasons.append(f"{len(ohlc)} OHLC-inconsistent bars present (must be corrected and recorded, not silently dropped)")
    if spreads is not None and any(s <= 0 for s in spreads):
        reasons.append("non-positive spreads")
    if reasons:
        verdict = "FAIL"
    elif prov.source_kind == "PROXY" or spreads is None or not spec_present:
        verdict = "PROXY_ONLY"
        reasons.append("not broker data: no bid/ask spreads and/or no symbol specification")
    else:
        verdict = "BROKER_GRADE"
    return QualityReport(verdict, checks, reasons, asdict(prov))
