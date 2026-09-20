# Solvent — Verifier Certification Hardening

**Repository** `wjnnmyz4dd-droid/roflo` · **branch** `claude/solvent-architecture-audit-8ot9et`
**Starting HEAD** `43b2884` · **Starting tests** 798

**Mission unchanged:** *Solvent exists to find, qualify, complete, deliver and
profit from legitimate client work.*

**Posture, verified:** `simulation_only = true`, egress allowlist empty, 0 ledger
entries, 0 proven capabilities, audit chain intact.

---

## 1. The risk, reproduced

The previous report recorded a residual risk in one sentence: *"a small battery
can certify by luck."* Reproducing it was considerably worse than that.

Twelve deliberately weak verifiers were built from the brief's list. **Eleven
earned `CERTIFIED_WITH_LIMITS`.** Per-check scoping worked — each was excluded
from the checks it tampered with — so that much held.

Then four *fixture-fitted* verifiers were built, whose entire implementation is a
list of the battery's own fixtures:

| Verifier | Its entire logic | Old result |
|---|---|---|
| `last-row-dupe-only` | a duplicate counts only if it is the final row | **CERTIFIED** 19/19 |
| `total-is-not-999` | a total is correct if it is not `999.99` | **CERTIFIED** 28/28 |
| `approver-is-not-rivera` | an approver is invented only if named `J. Rivera` | **CERTIFIED** 28/28 |
| `name-is-not-corporation` | a name is wrong only if it is `Acme Corporation` | **CERTIFIED** 28/28 |

All four earned **full certification** and all four were **trusted for the exact
check they were faking**.

The root cause is not the numbers. It is that a fixed, small, public battery
measures *familiarity with the battery*. A verifier only had to recognise those
twenty artifacts.

---

## 2. What changed

The architecture that passed last time is preserved: `record_verification` is
still the single chokepoint, the capability registry is still the authority for
what is proven on what evidence, and `verifiercert` still measures without
deciding. No Guardian V2, no second registry, no AI judge.

What changed is **what counts as evidence**.

### Generated, not fixed (`solvent/certgen.py`)

Three properties make it evidence rather than volume.

**The answer is generated before the question.** Clean rows are invented first,
so the correct output is known *by construction*; the messy source is then
derived from it by adding duplicates, padding, alternative date formats and
case changes. The worker never produces both the artifact and the oracle, so a
verifier agreeing with the worker is not thereby agreeing with the truth.

**Each bad case carries exactly one defect, at a position and value the
generator chose.** A verifier is not asked *"is this the wrong total I showed you
last time?"* but *"is this total wrong?"*, about a figure it has never seen.

**Three disjoint recorded seeds** — development (`20260920`), certification
(`81174903`), surprise (`55219648`). Verified to share no case and no source.

### The evidence floor (`CapabilityRegistry`)

Derived from what it takes to defeat hardcoding, not chosen to make a test pass:

| Floor | Value | Why |
|---|---|---|
| `MIN_INSTANCES_PER_DEFECT` | 5 | five distinct randomised instances is where hardcoding costs more than implementing the check |
| `MIN_GOOD_INSTANCES` | 10 | ten materially different correct artifacts separates a working check from a stuck one |
| class coverage | all | a check credited for the classes it happened to catch, while missing one it was shown, is "lucky" under another name |

Evidence is counted **by artifact content**, not by trial name: twenty copies of
two fixtures count as two.

### Binding to the implementation (§22–23)

Certification records a SHA-256 fingerprint of the verifier's **module source and
its own source**. An edited implementation is a different subject and starts
again. A spoofed name earns nothing: registering under `solvent.csvverify.run`
with different code is refused.

---

## 3. Defects found

### In the real verifiers — all the same family: checking shape, not substance

**`normalise_dates` verified the format and never the day.** `2026-05-10`
rewritten as `2026-10-05` passed: a perfectly well-formed ISO date, a client's
order five months wrong. It now checks that each date denotes a day the source
actually supplied, permissively where the source is genuinely ambiguous.

**`report_rows_tabulated` counted rows without reading them.** Another job's
report passed whenever the row counts matched. It now compares each tabulated
column's values against the source as a multiset.

**`drop_exact_duplicates` and `row_reconciliation` compared counts alone.** Found
only by the surprise seed, where two cases happened to have 18 distinct rows
each. They now require at least one column whose values match the source's
exactly as a multiset — true of any untouched column in a real cleanup, false of
a foreign file.

### In the new machinery

**The fingerprint hashed only the module**, so two lambdas in one test file
shared a fingerprint and silently shared a cached measurement. Caught by its own
test.

**Revocation was self-healing.** A revoked verifier was re-certified by the next
job, because the revocation record carried no fingerprint so re-certification
looked warranted. Re-earning trust after revocation is now an owner act with a
reason, and the revocation stays on the record.

### In my own ground truth

The first generated battery marked a transposed date BAD even when the source
wrote it ambiguously — where both readings are legitimate and nobody knows the
answer. A battery must not assert a truth that does not exist, so transposition
defects are now built only from unambiguously supplied dates.

The first version of the `rows_tabulated` fix demanded that *every* supplied
value appear in the table, which refused a client's legitimate request for two
of six columns. Corrected to compare per tabulated column.

---

## 4. Results

### The adversaries, before and after

| Verifier | Before | After | Trusted for the check it fakes? |
|---|---|---|---|
| `fitted-last-row-dupe` | CERTIFIED 19/19 | CERTIFIED_WITH_LIMITS | **no** |
| `fitted-total-not-999` | CERTIFIED 28/28 | CERTIFIED_WITH_LIMITS | **no** |
| `fitted-not-rivera` | CERTIFIED 28/28 | CERTIFIED_WITH_LIMITS | **no** |
| `fitted-not-corporation` | CERTIFIED 28/28 | CERTIFIED_WITH_LIMITS | **no** |
| `weak-substring-facts` | CERTIFIED_WITH_LIMITS | CERTIFIED_WITH_LIMITS | **no** |
| `weak-presence-only` | CERTIFIED_WITH_LIMITS | CERTIFIED_WITH_LIMITS | **no** |
| `weak-ignores-source` | CERTIFIED_WITH_LIMITS | CERTIFIED_WITH_LIMITS | **no** |
| `weak-rounding-tolerant` | CERTIFIED_WITH_LIMITS | CERTIFIED_WITH_LIMITS | **no** |
| `approve-everything` | FAILED | FAILED | no |
| `reject-everything` | FAILED | FAILED | no |

Each keeps certification for the checks it honestly delegates — partial success
does not become blanket trust, and it does not become blanket distrust either.

### Verifier scorecard

| | `csv-cleanup/1.0` | `report-builder/1.0` |
|---|---|---|
| Verifier | `solvent.csvverify.run` | `solvent.reportverify.run` |
| Certification | **CERTIFIED** | **CERTIFIED** |
| Trials | 372 | 414 |
| Correct artifacts | 126 | 112 |
| Defective artifacts | 246 | 302 |
| Defect classes | 14 | 18 |
| False accepts | 0 | 0 |
| False rejects | 0 | 0 |
| Checks certified | 9/9 | 8/8 |
| Surprise battery | passed | passed |
| Fingerprint | recorded | recorded |

Defect classes for CSV: ROW_LOSS · ROW_ADDITION · PROTECTED_VALUE_CHANGE ·
PROTECTED_VALUE_ONE_DIGIT · WRONG_DATE_CONVERSION · DATE_TRANSPOSED · RAGGED_ROW
· BINARY_CONTENT · MISSING_COLUMN · WHITESPACE_REMAINS · UNMAPPED_VALUE ·
UNAUTHORIZED_TRANSFORMATION · WRONG_SOURCE · CROSS_JOB.

For reports: OMISSION · ALTERATION · SIMILAR_NAME · SUBSTRING_CONFUSION ·
VALUE_QUALIFIED · FABRICATED_ABSENCE · ABSENCE_DROPPED · WRONG_NUMBER ·
TOTAL_DIGIT_SWAP · UNSUPPORTED_INFERENCE · INVENTED_NUMBER · ROW_OMITTED ·
MISSING_SECTION · WRONG_SECTION · EMPTY_DOCUMENT · NO_SECTIONS · WRONG_SOURCE ·
CROSS_JOB.

---

## 5. Mutation testing

17 controls broken one at a time. **First run: 11/17.** Six survivors, and they
were the most useful part of the exercise. **After the fixes below: 17/17.**

Of the six:

- Two were *my* badly-built mutants, not gaps.
- One was **dead code** — a branch guarding a case `checks_passed()` already
  excludes. Removed rather than left pretending to be a control.
- One was a **real weakness**: evidence was counted by trial name, not content,
  so near-duplicates would have counted as independent evidence. Fixed.
- Two were **untested controls**. The sharpest: against the generated
  adversaries, the both-directions rule alone already refuses every one, so
  weakening the floor changed nothing and no test noticed. The floor exists for
  a different case — a verifier right about *everything it was shown*, where
  what it was shown was four examples — and that has to be tested with a small
  battery or not at all. Likewise the cross-job binding in
  `row_reconciliation`: the generated cases usually differ in row count, so the
  count comparison rejected them for the wrong reason and the binding was never
  exercised. Both now have deliberate tests.
- One more surfaced on the second pass: the floor tests called the rule
  directly, so removing its *call* from `certify_verifier` left every one of
  them passing. A control that is correct and never reached is not a control.

---

## 6. Remaining risks

1. **An ambiguously-written date can still be read either way.** `05/10/2026`
   with no stated convention is two days, and Solvent picks one. The verifier
   correctly refuses to assert which is right. The real fix is for the *job* to
   ask the client, which is not built.
2. **A defect class nobody thought of is not covered.** The battery tests the
   classes listed for each capability. Certification proves competence against
   *those*, and says nothing about a class not on the list.
3. **`_accounted_columns` assumes some column survives untouched.** A job that
   legitimately transforms every column would be refused by the source binding.
   No such job exists today; it would need the authorised-columns set passed in.
4. **The surprise battery is only run on demand.** Nothing schedules it, so a
   verifier that degrades between runs is caught at the next explicit audit.
5. **Replies are still drafted, never sent**, and everything in
   `OWNER_DECISIONS.md` remains blocking.
