"""Research data acquisition with provenance (Yahoo Finance chart API).

These are PROXIES: continuous futures (GC=F, CL=F), spot FX (JPY=X) and an
exchange composite (BTC-USD). They are not the broker's CFD prices; spreads,
session times and rollover differ. Every file is stored with its SHA-256 and
retrieval time; analyses cite the hash.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.request

from sentinel.contracts import Bar

SYMBOLS = {"XAUUSD": "GC=F", "USOIL": "CL=F", "USDJPY": "JPY=X", "BTCUSD": "BTC-USD"}
URL = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?period1={p1}&period2={p2}&interval={iv}"


def fetch(instrument: str, interval: str, p1: int, p2: int, outdir: str) -> dict:
    sym = SYMBOLS[instrument]
    url = URL.format(sym=sym, p1=p1, p2=p2, iv=interval)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310
        raw = r.read()
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, f"{instrument}_{interval}.json")
    with open(path, "wb") as f:
        f.write(raw)
    meta = {"instrument": instrument, "proxy_symbol": sym, "interval": interval, "url": url,
            "retrieved_at": int(time.time()), "sha256": hashlib.sha256(raw).hexdigest(), "file": os.path.basename(path)}
    with open(path + ".meta.json", "w") as f:
        json.dump(meta, f, indent=2)
    return meta


def load(instrument: str, interval: str, datadir: str) -> tuple[list[Bar], dict]:
    path = os.path.join(datadir, f"{instrument}_{interval}.json")
    raw = open(path, "rb").read()
    meta = json.load(open(path + ".meta.json"))
    if hashlib.sha256(raw).hexdigest() != meta["sha256"]:
        raise ValueError(f"data file {path} does not match recorded hash")
    j = json.loads(raw)["chart"]["result"][0]
    q = j["indicators"]["quote"][0]
    bars = []
    dropped = 0
    for i, ts in enumerate(j["timestamp"]):
        o, h, low, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
        if None in (o, h, low, c) or min(o, h, low, c) <= 0 or not (low <= min(o, c) and h >= max(o, c)):
            dropped += 1
            continue
        bars.append(Bar(instrument, int(ts), float(o), float(h), float(low), float(c), float(q["volume"][i] or 0)))
    bars.sort(key=lambda b: b.ts)
    dedup = []
    for b in bars:
        if dedup and b.ts == dedup[-1].ts:
            dropped += 1
            continue
        dedup.append(b)
    meta = dict(meta, bars=len(dedup), dropped=dropped)
    return dedup, meta
