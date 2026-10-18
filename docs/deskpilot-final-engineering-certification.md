# DeskPilot — final engineering certification

Date: 2026-10-18 · Platform: Linux x86_64 · CPython 3.11 · Branch
`claude/solvent-architecture-audit-8ot9et` · Base commit `fcd241a`

This round's certification. The previous round's is preserved as
`deskpilot-final-engineering-certification-round-four.md` rather than
overwritten.

**No real external effect was produced at any point.** Nothing was
deployed, no real bid was submitted, no real client was contacted, no
real payment was processed, no Connect was spent, and no Upwork account
was authenticated against.

---

## 1. Phase 1 acceptance

| Gate | Result |
|---|---|
| All applicable regression tests pass | **PASS** — 4,987 passed, 0 failed, 46 skipped, 10,156 subtests |
| Sealed examination: reading | **PASS** — resealed, re-set, re-sat, 56/56, seal intact |
| Sealed examination: capacity | **PASS** — resealed, re-set, re-sat, 6/6, seal intact |
| Profitability guard | **PASS** — restored, independently verified, and a second route past the floor closed |
| Mutation isolation | **PASS** — isolation by default, claim with hash verification, scan, pre-commit hook |
| The five previous repairs (A–E) | **PASS** — integrated acceptance clean after the Governor reordering |
| No mutant survived | **PASS** — 212/212 killed |
| No mutant unapplied | **PASS** — 0 not applied, 0 harness |
| No critical unresolved security defect | **PASS** |

**PHASE 1: COMPLETE.**

## 2. Repository integrity, verified independently

Not taken on the previous report's word. Three angles:

* **The line.** `solvent/finder.py:1197` reads
  `if econ.profit_cents is not None and econ.profit_cents <= 0:`.
* **The net diff.** `solvent/finder.py` does not appear in
  `git diff --name-status 4819125 fcd241a` at all;
  `git diff 4819125 -- solvent/finder.py` is empty.
* **A scan that knows nothing about the incident.** Reading the `Mutant`
  tables out of both runners and asking of each target file whether the
  *original* is gone and the *replacement* present:

```
tools.mutate_solvent: 212 mutants
tools.mutate_installer: 194 mutants
406 mutants found their original anchor intact
```

Zero mutated, zero anchors missing. No production source is running
under mutation instrumentation.

A sweep of every removed line in the round's diff found exactly two
guard-shaped removals, both replaced by stricter logic: the
`check_preserve_columns` comparison (Finding D) and the
`resume_interrupted` state test (Finding A, which gained a
durable-evidence requirement).

## 3. Defect F — an approval door above the floor

**Found by** tracing the bypass list against `FinancialGovernor._evaluate`
rather than reading the restored guard in isolation.

The ordered checks put the per-job cost ceiling *above* the margin
floor. Both return before the other is reached, so a job tripping both
came back `REQUIRES_APPROVAL` with its margin never computed:

```
margin_floor 0.30   max_job_spend_cents 25000
direct cost 26000 → effective cost 43332   revenue 28888
true margin −50.0%  (a loss of $144.44 on $288.88)
verdict before: REQUIRES_APPROVAL      verdict after: MARGIN_TOO_LOW
```

The owner was being asked to approve a job that loses money, as an
ordinary approval, with their own floor never evaluated.

**Repaired** by moving the floor above both approval doors and below the
exposure cap:

```
conformance → jurisdiction → preliminary → mode → revenue > 0
  → exposure cap (HIGH_RISK)
  → MARGIN FLOOR (MARGIN_TOO_LOW)
  → per-job cost ceiling (REQUIRES_APPROVAL)
  → owner-approval threshold (REQUIRES_APPROVAL)
  → PROFITABLE
```

Risk deliberately still outranks price: a job breaching the
committed-but-unverified cap is refused as *risk*, which is the reason
the owner needs to read. A test pins that, so the order cannot drift
either way.

24 tests in `test_profitability_guard.py`; five mutants, all killed.
Full record: `deskpilot-profitability-certification.md`.

## 4. Mutation isolation

A mutation run owns the files it patches, and nothing enforced that
until now. Three defences, because each covers a case the others miss:
isolation (both runners patch a copy of the working tree by default), a
claim (pre-run hashes that must be checked back, with the claim left on
disk if they are not), and a scan (original-absent-and-replacement-
present, needing no cooperation from the runner). A pre-commit hook
reads the **index**, not the working tree, because the index is what
captured the last one.

It was proven in anger during this round: a battery was killed at a
harness time limit, with no chance to run its `finally`, and the working
tree came back clean — no claim, no mutant. It did leak its temporary
copy, which is now reaped.

22 tests; five mutants, all killed. Full record:
`deskpilot-mutation-isolation-certification.md`.

## 5. The sealed examinations

Both resealed and sat by the procedure the exams document. Before any
digest moved, each paper that had been set was re-marked against the
changed source — reading 56/56 identical to the first sitting, capacity
6/6 with every delivered count identical to the seventh. Then the freeze
moved, so the seed moved, so both are new papers.

| Exam | Digest moved | New sitting | Seal |
|---|---|---|---|
| reading | `solvent/csvverify.py` | 56/56, seed 3726124977 | intact |
| capacity | `solvent/store.py` | 6/6, seed 3726124977 | intact |

All seven earlier capacity sittings untouched. The reading exam's first
sitting is preserved twice — in the module and as
`docs/evidence/reading-exam-first-sitting.json` — because it previously
lived only in a file each sitting overwrites. No test case changed, no
assertion weakened, no expected result altered; four new tests enforce
the preservation. Full record:
`deskpilot-sealed-examination-resolution.md`.

## 6. The five previous repairs, re-verified

Integrated acceptance re-run after the Governor reordering:

| | |
|---|---|
| Offered / delivered | 100 / 52 |
| Crashes injected | 5 — worker execution, verification, delivery ack, invoice creation, recovery |
| Stranded jobs | 0 |
| Delivered but unbilled | 0 |
| Duplicate invoices | 0 |
| Fabricated payments | 0 |
| Unauthorized deliveries | 0 (53 delivery events, 53 consumed owner approvals) |
| Skills delivering nothing | none |
| Audit chain | intact |

Identical to the pre-change run, which is the result: the reordering
changed nothing for work that clears the floor.

## 7. Phase 2 — Upwork integration preparation

| Item | Status |
|---|---|
| Official integration | **PARTIAL** — the MCP server is verified to exist; its capabilities are not |
| Job discovery | **UNVERIFIED** |
| Competition data | **UNVERIFIED** |
| Proposal submission | **UNVERIFIED**, and treated as not permitted |
| Owner approval | **PASS** |
| SMS notifications | **PASS** |
| Profitability model | **PASS**, with named gaps |
| Simulated acceptance | **PASS** — 37/37 |
| iFixAi review | **NATIVE EXECUTION** — CLEAR, 0 violations, 3/3 controls caught |
| K-LEAN review | **SOURCE-DERIVED** |

Verified from Upwork's own infrastructure: `mcp.upwork.com` publishes
the MCP OAuth discovery documents unauthenticated, names the resource
`Upwork MCP`, and returns 401 on the resource. Verified from
`robots.txt`: automated browsing of job search is disallowed.
Everything else is UNVERIFIED because every Upwork documentation page
returns 403 to this container, and the brief said not to scrape
restricted pages.

So the implementation is built on what is verified: no submission path
exists, five of the inputs the brief asks for are reported
`UNAVAILABLE`, and an unknown cost makes the contribution unknown
rather than larger. Records:
`deskpilot-upwork-official-integration-review.md`,
`deskpilot-upwork-approval-architecture.md`,
`deskpilot-upwork-bid-economics.md`,
`deskpilot-upwork-simulated-acceptance.md`,
`deskpilot-ifixai-klean-final-review.md`.

## 8. Regression and mutation

```
python3 -I -m pytest tests_solvent -q
4987 passed, 46 skipped, 8 warnings, 10156 subtests passed in 498.53s

python3 tools/mutate_solvent.py
isolated: patching a copy of the working tree
212/212 killed  (0 survived, 0 not applied or harness)
```

The 46 skips are counted as skips. Mutation coverage added this round:
five for the financial admission control, five for mutation isolation,
eight for the Upwork bidding module — eighteen in all, every one run
against the code as committed. No historical coverage is claimed for
anything written this round.

Three mutants survived a first run and each was killed by strengthening
a test, never by weakening a mutant:

* `preserve-forgives-any-difference-it-cannot-explain` (earlier round)
* `invoice-recovery-trusts-the-job-state-alone` (earlier round)
* `upwork-the-ranking-floats-unknowns-to-the-top` — bound to a test the
  sibling sort keys already satisfied; re-pointed at the case where the
  rule and the other keys disagree

Two were `NOT_APPLIED` in an earlier run because this engagement's own
new code made their anchors ambiguous. A mutant that cannot be placed is
a coverage gap, not a pass: both were re-anchored and the new sites got
mutants of their own.

## 9. Remaining findings, not claimed as fixed

1. **The Finder's profitability triage is not reachable from the
   business cycle.** It is constructed only in `cli.py:503` for a
   readiness report; nothing in `harness.py` or `runtime.py` calls
   `assess`, `rank` or `next_to_qualify`. The authority that admits work
   — `FinancialGovernor.decide` — is properly wired, so this is not a
   hole in admission today. It becomes material in Phase 2, where
   recommending work is exactly the Finder's job. Reported rather than
   repaired: wiring a second economic opinion into the cycle is an
   architectural change nobody asked for.
2. **Connects cost and the Upwork service fee are not modelled cost
   categories**, so an Upwork opportunity's net contribution is
   `UNAVAILABLE` by construction.
3. **No monthly acquisition budget is tracked across contracts**, so
   per-contract and portfolio-level acquisition costs are only partly
   distinguishable.
4. **`BidBudget` is not persisted.** A table would have to go in
   `solvent/store.py`, which is under seal; resealing to store figures
   for a channel that cannot be used is the wrong trade today.
5. **`.md` deliverables whose brief has nothing recomputable** still
   block on the owner. The correct refusal, and a real limit.
6. **An hourly job delivered by recovery still needs a person.**
7. **Omission cannot be established for a converted document** from
   flattened source text; only provenance can.
8. **openpyxl would share expat with DeskPilot's own reader if lxml were
   absent.** lxml is present here.
9. **`budget_grants.revoked` is never set.**

## 10. What this certification does not say

* **Not production readiness.** Passing synthetic tests is a different
  claim, and nothing here was deployed.
* **Not permission to use Upwork.** Nobody has read Upwork's terms or
  its MCP tool list, and that is the blocker — not engineering.
* **Not a revenue result.** 0 cents of real revenue.
* **Not evidence a person approved anything.** Every owner approval came
  from a test approver with a throwaway key.
* **Not a Windows or MetaTrader 5 claim.** Linux x86_64, CPython 3.11.
