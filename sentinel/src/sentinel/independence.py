"""Measured (not assumed) independence between Finder and Validator."""

from __future__ import annotations

import math


def jaccard(a, b) -> float:
    a, b = set(a), set(b)
    return len(a & b) / len(a | b) if a | b else 0.0


def phi(x: list[bool], y: list[bool]) -> float:
    n11 = sum(a and b for a, b in zip(x, y))
    n10 = sum(a and not b for a, b in zip(x, y))
    n01 = sum((not a) and b for a, b in zip(x, y))
    n00 = sum((not a) and (not b) for a, b in zip(x, y))
    d = math.sqrt((n11 + n10) * (n01 + n00) * (n11 + n01) * (n10 + n00))
    return (n11 * n00 - n10 * n01) / d if d else math.nan


def point_biserial(flags: list[bool], values: list[float]) -> float:
    n = len(values)
    if n < 3:
        return math.nan
    g1 = [v for f, v in zip(flags, values) if f]
    g0 = [v for f, v in zip(flags, values) if not f]
    if not g1 or not g0:
        return math.nan
    m = sum(values) / n
    sd = math.sqrt(sum((v - m) ** 2 for v in values) / n)
    if sd == 0:
        return math.nan
    return (sum(g1) / len(g1) - sum(g0) / len(g0)) / sd * math.sqrt(len(g1) * len(g0) / n**2)


def measure(finder_features, validator_features, same_data: bool, records: list[dict]) -> dict:
    """records: [{"validator_valid": bool, "r": float}] for Finder candidates with realised outcome."""
    fj = jaccard(finder_features, validator_features)
    valid = [bool(r["validator_valid"]) for r in records]
    rs = [float(r["r"]) for r in records]
    finder_wrong = [x < 0 for x in rs]
    validator_wrong = [(v and x < 0) or ((not v) and x > 0) for v, x in zip(valid, rs)]
    e_phi = phi(finder_wrong, validator_wrong)
    info = point_biserial(valid, rs)
    parts = [fj, 1.0 if same_data else 0.0]
    if math.isfinite(e_phi):
        parts.append(max(0.0, e_phi))
    score = 1.0 - max(fj, max(0.0, e_phi) if math.isfinite(e_phi) else 1.0)
    return {
        "shared_feature_jaccard": round(fj, 3),
        "shared_data": same_data,
        "error_phi": round(e_phi, 3) if math.isfinite(e_phi) else None,
        "validator_information_pbis": round(info, 3) if math.isfinite(info) else None,
        "n": len(records),
        "valid_rate": round(sum(valid) / len(valid), 3) if valid else None,
        "mean_r_valid": round(sum(x for v, x in zip(valid, rs) if v) / max(1, sum(valid)), 4),
        "mean_r_invalid": round(sum(x for v, x in zip(valid, rs) if not v) / max(1, len(valid) - sum(valid)), 4),
        "independence_score": round(score, 3),
    }
