# Sentinel

Sentinel is a fail-closed trading-system safety core with an adversarial qualification suite. It is
built around one rule: **no component may both decide and act**.

- **Research and simulation only.** No path to real capital. The MT5 adapter refuses every account
  that is not an owner-allow-listed DEMO account, and it has only been tested against a fake terminal.
- The architecture and authority matrix are in [ARCHITECTURE.md](ARCHITECTURE.md).
- The full results are in [QUALIFICATION_REPORT.md](QUALIFICATION_REPORT.md) (phase 1) and
  [SENTINEL_NEXT_PHASE_REPORT.md](SENTINEL_NEXT_PHASE_REPORT.md) (phase 2).

## Layout

```
src/sentinel/         safety core (stdlib only) + research/ (numpy-free harness)
  policy/             FTMO profiles as versioned data, citing hashed source pages
tests/                unit, adversarial, chaos, crash-matrix (subprocess kill) suites
tools/                crash scenario driver, mutation runner, research and replay drivers
provenance/           FTMO rule pages and a calendar snapshot, as retrieved (sha256 in the policies)
data/                 Yahoo proxy bars (+ .meta.json with sha256 and retrieval time)
research/             PREREGISTRATION.md (committed before any P&L was computed)
reports/              generated evidence (JSON + logs)
```

## Reproduce

```bash
cd sentinel
python -m venv .venv && . .venv/bin/activate && pip install pytest
python -m pytest -q                              # full suite incl. crash matrix (~15 s)
python tools/mutate.py --json reports/m.json     # mutation testing of safeguards (~10 min)
python tools/run_research.py dev                 # development-period edge study
python tools/run_research.py extras              # leakage probes, null model, independence, sensitivity
python tools/pipeline_eval.py dev /tmp/pipe      # full pipeline paper replay + no-trade counterfactuals
python tools/shadow_live.py /tmp/shadow          # one live SHADOW cycle (network; no execution path)
python tools/shadow_continuous.py replay /tmp/sr 120     # continuous SHADOW replay + counterfactual journal
python tools/shadow_continuous.py live /tmp/sl 120 300   # continuous SHADOW on live proxy data
python tools/run_phase2.py devval                # phase-2 lineages L2/L3 (development + validation only)
python tools/news_attribution.py                 # point-in-time FOMC gate attribution on frozen L1
python tools/data_quality.py                     # data-quality verdicts for the stored datasets
# run_research.py holdout has ALREADY been run once; the promotion journal refuses a second opening.
```
