"""Mutation testing of critical safeguards: break a guard, the suite MUST fail.

Each mutation is applied to a fresh copy of src/ and the full test suite runs
against it. A surviving mutation means a guard can be removed without any test
noticing. Usage: python tools/mutate.py [--json out.json]
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]

M = [
    ("risk.py", "        if sum(p.instrument == spec.instrument for p in pos) >= L.max_positions_per_instrument:",
     "        if False:", "risk: per-instrument position cap removed"),
    ("risk.py", "        if day_pnl <= -L.internal_daily_loss_pct * self.initial:", "        if False:", "risk: internal daily-loss halt removed"),
    ("risk.py", "        if spread > L.max_spread_to_stop_fraction * dist:", "        if False:", "risk: spread guard removed"),
    ("risk.py", "        budget_trade = L.risk_per_trade_pct * snap.equity", "        budget_trade = 10 * L.risk_per_trade_pct * snap.equity", "risk: position size x10"),
    ("risk.py", "        if streak >= L.loss_streak_halt:", "        if False:", "risk: loss-streak halt removed"),
    ("risk.py", "        if age > L.max_snapshot_age_s or age < -5:", "        if False:", "risk: stale broker snapshot accepted"),
    ("risk.py", "            return math.inf  # unknown downside: treated as unbounded", "            return 0.0", "risk: stop-less position treated as zero risk"),
    ("compliance.py", "            day_base = self.balance_at(snap.balance, deals, midnight)", "            day_base = snap.balance",
     "compliance: daily base = current balance (not midnight CE(S)T)"),
    ("compliance.py", "        if snap.equity <= lim[\"daily_limit\"] + buf or snap.equity <= lim[\"max_limit\"] + buf:", "        if False:",
     "compliance: safety buffer removed"),
    ("compliance.py", "        if p[\"review\"].get(\"status\") != \"APPROVED\" and not self.cfg.simulation:", "        if False:",
     "compliance: unreviewed profile allowed outside simulation"),
    ("compliance.py", "        if not (vf <= wall < vu):", "        if False:", "compliance: expired profile accepted"),
    ("compliance.py", "            if e.scheduled_utc - nr[\"window_before_s\"] <= now <= e.scheduled_utc + nr[\"window_after_s\"]:", "            if False:",
     "compliance: FTMO restricted-news window ignored"),
    ("compliance.py", "            hwm = max(hwm, self.balance_at(snap.balance, deals, int(day.astimezone(UTC).timestamp())))", "            pass",
     "compliance: trailing max-loss high-water mark not tracked"),
    ("compliance.py", "        if projected <= floor + buf:", "        if False:", "compliance: worst-case pre-trade check removed"),
    ("news.py", "        if now - s.fetched_at > p.max_staleness_s:", "        if False:", "news: stale calendar accepted"),
    ("news.py", "            if dt.tzinfo is None:\n                raise ValueError(f\"no timezone offset: {date_s}\")", "            if dt.tzinfo is None:\n                dt = dt.replace(tzinfo=UTC)",
     "news: naive timestamps guessed as UTC"),
    ("news.py", "            ts = int(dt.astimezone(UTC).timestamp())", "            ts = int(dt.replace(tzinfo=UTC).timestamp())", "news: offset dropped (the audited repo's bug)"),
    ("news.py", "        if hits:", "        if False:", "news: blackout hits ignored"),
    ("news.py", "        if not (s.coverage_start <= now < s.coverage_end):", "        if False:", "news: query outside coverage accepted"),
    ("execution.py", "                unique=[(\"permit\", permit.permit_id), (\"intent\", intent.intent_id),", "                unique=[",
     "execution: permit/intent uniqueness removed"),
    ("execution.py", "            self._control.halt(\"execution\", f\"order outcome unknown for {intent.intent_id}: {e}\", scope=\"reconcile\")",
     "            return self.submit(permit) if False else self._broker.send_order(intent.intent_id, intent.instrument, intent.direction, intent.volume, intent.stop_loss, intent.take_profit, intent.max_slippage_points, token)",
     "execution: blind resend on timeout"),
    ("execution.py", "            verify_permit(self._key, permit, now)", "            pass", "execution: permit not verified"),
    ("execution.py", "        ok, why = self._control.trading_permitted()\n        if not ok:", "        ok, why = True, 'mut'\n        if not ok:",
     "execution: control state ignored"),
    ("reconcile.py", "            if ins:", "            if not ins:", "reconcile: resolution inverted"),
    ("reconcile.py", "                                 \"blocking\": self._policy == \"halt\"})", "                                 \"blocking\": False})",
     "reconcile: foreign exposure not blocking"),
    ("reconcile.py", "                findings.append({\"kind\": \"LOCAL_POSITION_MISSING_AT_BROKER\", \"ticket\": ticket, \"blocking\": True})", "                pass",
     "reconcile: missing positions ignored"),
    ("arbiter.py", "            return out(ArbiterVerdict.REJECT, \"R3_invalid\",", "            return out(ArbiterVerdict.APPROVE_FOR_RISK_REVIEW, \"R3_invalid\",",
     "arbiter: INVALID approved"),
    ("arbiter.py", "        if absent:", "        if False:", "arbiter: incomplete validator checks accepted"),
    ("arbiter.py", "        if now >= c.expires_at:", "        if False:", "arbiter: expired candidates accepted"),
    ("arbiter.py", "        if v.data_sources and c.provenance.dataset_hash not in v.data_sources:", "        if False:", "arbiter: data mismatch accepted"),
    ("authority.py", "            if not decision_signature_ok(self._verify[name], name, d):", "            if False:", "authority: decision signatures not checked"),
    ("authority.py", "    if now > permit.expires_at:", "    if False:", "authority: expired permits accepted"),
    ("authority.py", "        if risk.approved_volume is None or intent.volume > risk.approved_volume + 1e-12:", "        if False:",
     "authority: volume above risk approval accepted"),
    ("control.py", "        if last_clean_reconcile is None or last_clean_reconcile < last_startup:", "        if False:",
     "control: trading without reconcile after restart"),
    ("control.py", "            if h.payload[\"scope\"] == \"manual\" and not any(r > h.seq for r in resumes):", "            if False:", "control: manual halts ignored (1st check)"),
    ("lease.py", "        if owner != self.owner or token != self.token:", "        if False:", "lease: lost leadership not detected"),
    ("validator.py", "        if future:", "        if False:", "validator: future bars accepted"),
    ("validator.py", "            if i < next_free:\n                continue", "            if False:\n                continue", "validator: overlapping analogs counted"),
    ("validator.py", "        if bad:\n            return done(ValidationVerdict.INVALID, [\"corrupted input data\"])", "        if False:\n            return done(ValidationVerdict.INVALID, [\"corrupted input data\"])",
     "validator: corrupted data accepted"),
    ("finder.py", "        closed = [b for b in bars if b.ts + bar_seconds <= now]", "        closed = list(bars)", "finder: unfinished bars used (look-ahead)"),
    ("orchestrator.py", "        if abs(drift) > c.max_clock_drift_s:", "        if False:", "orchestrator: clock drift ignored"),
    ("system.py", "    if not ok:  # tampered / corrupted history", "    if False:  # tampered / corrupted history", "startup: journal integrity not enforced"),
    ("learning.py", "        if kind == \"FACT\":\n            raise MemoryRejected", "        if False:\n            raise MemoryRejected", "learning: FACT injectable directly"),
    ("learning.py", "        if not ok:\n            raise MemoryRejected(f\"trading journal integrity failure", "        if False:\n            raise MemoryRejected(f\"trading journal integrity failure",
     "learning: tampered journal accepted"),
    ("promotion.py", "            self._j.append(\"HOLDOUT_OPENED\", \"promotion\", {\"proposal_id\": pid}, correlation_id=pid, unique=[(\"holdout\", pid)])",
     "            self._j.append(\"HOLDOUT_OPENED\", \"promotion\", {\"proposal_id\": pid}, correlation_id=pid)", "promotion: holdout reuse allowed"),
    ("authority.py", "            if d.subject != intent.candidate_id:", "            if False:", "authority: decisions not bound to candidate"),
    ("authority.py", "        if risk.detail.get(\"direction\") != intent.direction.value or risk.detail.get(\"stop\") != intent.stop_loss \\", "        if False and risk.detail.get(\"direction\") != intent.direction.value or risk.detail.get(\"stop\") != intent.stop_loss \\",
     "authority: direction/stop binding removed"),
    ("execution.py", "                        (\"decisions\", permit.decisions_hash), (\"candidate\", intent.candidate_id)],", "                        ],",
     "execution: one-decision-set-one-intent removed"),
    ("journal.py", "CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events\nBEGIN SELECT RAISE(ABORT, 'journal is append-only'); END;", "",
     "journal: append-only trigger removed"),
]


def run_one(mut) -> dict:
    fname, old, new, desc = mut
    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td)
        shutil.copytree(ROOT / "src", td / "src")
        shutil.copytree(ROOT / "tests", td / "tests")
        shutil.copytree(ROOT / "tools", td / "tools")
        shutil.copytree(ROOT / "provenance", td / "provenance")
        shutil.copy(ROOT / "pyproject.toml", td / "pyproject.toml")
        target = td / "src" / "sentinel" / fname
        text = target.read_text()
        if old not in text:
            return {"mutation": desc, "file": fname, "status": "NOT_APPLIED"}
        target.write_text(text.replace(old, new, 1))
        t0 = time.time()
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        env.pop("SENTINEL_CRASH_AT", None)
        p = subprocess.run([sys.executable, "-m", "pytest", "-x", "-q", "-p", "no:cacheprovider", "tests"],
                           cwd=td, capture_output=True, text=True, env=env, timeout=900)
        failed = p.returncode != 0
        killer = ""
        for line in p.stdout.splitlines():
            if line.startswith("FAILED "):
                killer = line.split(" ")[1]
                break
        return {"mutation": desc, "file": fname, "status": "KILLED" if failed else "SURVIVED",
                "killed_by": killer, "seconds": round(time.time() - t0, 1)}


def main():
    out = [run_one(m) for m in M]
    for r in out:
        print(f"{r['status']:12} {r['mutation']:70} {r.get('killed_by', '')}")
    killed = sum(r["status"] == "KILLED" for r in out)
    applied = sum(r["status"] != "NOT_APPLIED" for r in out)
    print(f"\nkilled {killed}/{applied} applied mutations ({len(out) - applied} not applied)")
    if "--json" in sys.argv:
        json.dump(out, open(sys.argv[sys.argv.index("--json") + 1], "w"), indent=2)


if __name__ == "__main__":
    main()
