"""Data-quality gate: proxy data never passes as broker-grade; gaps / bad prices fail."""

from __future__ import annotations

import dataclasses

import pytest
from conftest import trending_bars

from sentinel.dataquality import DatasetProvenance, bars_hash, check


def prov(kind="PROXY", bars=()):
    return DatasetProvenance("test", kind, None, "XAUUSD", None, "metal", "1h", "UTC", None, None, len(bars), None, 0,
                             bars_hash(list(bars)))


def crypto_bars(n=500):
    return trending_bars(n=n)  # continuous hourly series


def test_proxy_is_labelled_and_never_broker_grade():
    b = crypto_bars()
    r = check(b, prov("PROXY", b), 3600, trades_weekends=True, spreads=[0.3] * len(b), spec_present=True)
    assert r.verdict == "PROXY_ONLY" and r.provenance["label"] == "PROXY / RESEARCH-ONLY"


def test_broker_data_with_spec_and_spreads_is_broker_grade_and_without_is_not():
    b = crypto_bars()
    assert check(b, prov("BROKER", b), 3600, True, spreads=[0.3] * len(b), spec_present=True).verdict == "BROKER_GRADE"
    assert check(b, prov("BROKER", b), 3600, True, spreads=None, spec_present=True).verdict == "PROXY_ONLY"
    assert check(b, prov("BROKER", b), 3600, True, spreads=[0.3] * len(b), spec_present=False).verdict == "PROXY_ONLY"
    assert check(b, prov("BROKER", b), 3600, True, spreads=[0.0] * len(b), spec_present=True).verdict == "FAIL"


def test_gaps_and_invalid_prices_fail():
    b = crypto_bars()
    gappy = b[:200] + b[260:]
    assert check(gappy, prov("BROKER", gappy), 3600, True, [0.3] * len(gappy), True).verdict == "FAIL"
    bad = list(b)
    bad[100] = dataclasses.replace(bad[100], low=-1.0)
    assert check(bad, prov("BROKER", bad), 3600, True, [0.3] * len(bad), True).verdict == "FAIL"
    rev = list(b)
    rev[50], rev[51] = rev[51], rev[50]
    assert check(rev, prov("BROKER", rev), 3600, True, [0.3] * len(rev), True).verdict == "FAIL"


def test_unknown_source_kind_rejected():
    with pytest.raises(ValueError):
        prov("SCRAPED")
