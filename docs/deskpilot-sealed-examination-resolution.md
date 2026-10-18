# DeskPilot — sealed examination resolution

Date: 2026-10-18 · Platform: Linux x86_64 · CPython 3.11 · Branch
`claude/solvent-architecture-audit-8ot9et`

Two sealed examinations were invalidated by the previous remediation
round. Both are now resealed and sat, by the procedure the exams
themselves document, with every earlier sitting preserved.

Nothing was bypassed, no digest was updated silently, neither
examination was disabled, and no expected result was altered to force a
pass. What follows is the whole record, in the order the work was done.

---

## 1. The examinations, and why they refused to mark

| Exam | Module | What it examines |
|---|---|---|
| `tests_solvent/readingexam.py` | 8 modules | Whether the characters a client saved come back unchanged, whether the assumption used to read them is declared truthfully, and whether an unreadable file is refused rather than guessed at |
| `tests_solvent/capacityexam.py` | 4 modules | Whether the shop stays honest under load: nothing accepted goes undelivered, no budget exceeded, no duplicate commitment, no deadline missed, no obligation lost across a restart, no reservation leaked, concurrency inside its limit |

Each seals the SHA-256 of every module it examines. Two of those modules
changed in the previous round, under this engagement's explicit
authorisation:

| Exam | Module | Change | Why |
|---|---|---|---|
| reading | `solvent/csvverify.py` | `check_preserve_columns` now compares protected values against the transformations the same brief authorised, per column | Finding D: a brief that said "trim Name" and "preserve Name" failed correct work, because the comparison was against the source as it *arrived* |
| capacity | `solvent/store.py` | new append-only `billing_obligations` table | Finding B: a crash between delivery and invoicing left a delivered job with nothing on file saying what was owed |

Both seals broke, and both exams refused to mark themselves. That is the
mechanism working. `readingexam`'s own docstring states the rule:

> A broken seal is not a failure, it is an invalidation: reseal, re-set
> and re-sit.

## 2. Was each change legitimate?

Reviewed independently of the claim that they were.

**`solvent/csvverify.py`.** The change is confined to
`check_preserve_columns` and three new module-level helpers. It widens
one comparison and narrows nothing:

* an exact match still passes without consulting the brief at all;
* a difference is re-tested only under normalisations the brief
  authorised *for that column*, and only ones whose effect can be
  reproduced exactly (`trim_whitespace` is the single entry);
* a column the brief both protects and rewrites is UNVERIFIABLE, which
  blocks delivery exactly as a FAIL does;
* a padding-only difference with no brief to authorise it is
  UNVERIFIABLE, not a pass — not knowing is not a pass;
* anything else still FAILS.

Nothing in the reading path (`solvent/tabular.py`, the encoding ladder,
the delimiter detection) was touched, and `tabular.py`'s digest is
unchanged.

**`solvent/store.py`.** One `CREATE TABLE IF NOT EXISTS`, the table
registered in `APPEND_ONLY` and mapped to the `ledger` authority.
Additive: no existing table, column, trigger or authority mapping
changed. The append-only triggers that forbid UPDATE and DELETE apply to
it like every other ledger table.

Neither change touches a control either examination is built to
measure. That made answer-neutrality the *expected* result below — it
was measured rather than assumed.

## 3. Independent verification of the invariants

Before any digest moved, each paper that had been set was re-marked
against the changed source. The freeze was untouched, so the seed was
untouched, so these were the same questions in the same order.

**Reading — the paper of `2026-10-16T05:01:03Z`, seed 2994720811:**

```
56 questions, 56 correct, 0 wrong
24 readable papers, 32 damaged papers
```

Identical to the first sitting in every field. No answer moved.

**Capacity — the paper of `2026-10-18T00:00:00Z`, seed 403507301:**

```
6 questions, 6 passed, 0 failed
level   7, spike+slow_execution                       4 accepted,   4 delivered
level  13, revisions+delayed_verification            10 accepted,  10 delivered
level  37, outage+failed_submissions                 25 accepted,  25 delivered
level  71, payment_delays+spike                      11 accepted,  11 delivered
level 149, slow_execution+revisions                  13 accepted,  13 delivered
level 307, delayed_verification+failed_submissions   42 accepted,  42 delivered
```

Every level's delivered count identical to the recorded seventh
sitting. No criterion tripped. The new table changed no answer this
paper asks about.

Evidence scripts:
`/tmp/claude-0/-home-user-roflo/25de398b-b56b-53d8-b59f-f68119ec6f42/scratchpad/cert/resit_reading.py`
and
`/tmp/claude-0/-home-user-roflo/25de398b-b56b-53d8-b59f-f68119ec6f42/scratchpad/cert/resit_capacity.py`.

## 4. Was there an authorized resealing mechanism?

No tool exists. The repository's authorized procedure is the one written
in the exam modules themselves and demonstrated four times in
`capacityexam.SITTINGS`:

1. re-sit the paper that was set against the changed source, and show it
   still passes in full;
2. move `FROZEN_AT`, which moves `SEED`, which generates a genuinely
   different paper rather than a re-mark of the same questions;
3. update the digests to the reviewed source;
4. sit the new paper and record its real result beside the old ones,
   never over them.

That is the procedure followed here.

## 5. The new baseline manifest

| Exam | Module | Sealed digest before | Sealed digest now |
|---|---|---|---|
| reading | `solvent/csvverify.py` | `6f343293…d30d8413` | `5d52279c…9e7d468a48` |
| reading | the other seven modules | unchanged | unchanged |
| capacity | `solvent/store.py` | `fa72817a…474a77f3b9` | `ff99fcb1…f779eeb1ed70e5a` |
| capacity | the other three modules | unchanged | unchanged |

Full values, verified to match the files exactly:

```
reading   solvent/csvverify.py
          5d52279c2f961ad99755a68ec5fd30cd95a45acce7c643cfd5c76a9e7d468a48
capacity  solvent/store.py
          ff99fcb1a19356fc6665f52a390872d4bf4893dac26e3c2e2f779eeb1ed70e5a
```

Exactly one digest moved in each exam. Every other module under seal
still hashes to the value it was frozen at.

| Exam | `FROZEN_AT` before | `FROZEN_AT` now | Seed before | Seed now |
|---|---|---|---|---|
| reading | `2026-10-16T05:01:03Z` | `2026-10-18T16:40:00Z` | 2994720811 | 3726124977 |
| capacity | `2026-10-18T00:00:00Z` | `2026-10-18T16:40:00Z` | 403507301 | 3726124977 |

The seeds moved, so both papers are new papers.

## 6. The new sittings

**Reading, second sitting** — `2026-10-18T16:40:00Z`, seed 3726124977:

```
56 questions, 56 correct, 0 wrong
24 readable, 32 damaged
```

**Capacity, eighth sitting** — `2026-10-18T16:40:00Z`, seed 3726124977:

```
6 questions, 6 passed, 0 failed
level   7, spike+slow_execution                            5 accepted,   5 delivered, peak 0.88
level  13, delayed_verification+revisions                  8 accepted,   8 delivered, peak 0.963
level  37, failed_submissions+outage                      16 accepted,  16 delivered, peak 0.308
level  71, spike+payment_delays                           11 accepted,  11 delivered, peak 0.292
level 149, slow_execution+revisions                       15 accepted,  15 delivered, peak 0.411
level 307, delayed_verification+failed_submissions       160 accepted, 160 delivered, peak 0.888
```

No criterion was tripped at any question.

Both are recorded in the modules — `readingexam.SITTINGS`,
`capacityexam.SITTINGS` — so a future reader can see what each paper
scored without re-running anything.

## 7. What was preserved

* **`capacityexam`**: all seven earlier sittings are untouched. The
  eighth was added beside them; none was edited.
* **`readingexam`**: the first sitting had no module record — the exam
  kept it only in `docs/evidence/reading-exam.json`, which the test
  requires to hold the *current* sitting. Two things were done rather
  than one:
  * the original evidence file was copied to
    `docs/evidence/reading-exam-first-sitting.json` before the new
    sitting was written, so the original file still exists under its own
    name with its original contents;
  * `readingexam.FIRST_SITTING` now records it in the module, following
    the capacity exam's pattern, so the record no longer depends on a
    file each sitting overwrites.
* **No test case was changed.** The paper generators, the marking, the
  brief, the unseen corpora and the criteria are all exactly as they
  were.
* **No expected behaviour was altered.** No assertion was weakened,
  relaxed, skipped, deleted or marked `xfail`.
* **No seal was disabled.** Both exams still refuse to mark themselves
  if a module under seal moves — which is now checked by a test that
  deliberately corrupts a digest and requires the check to fail
  (`test_the_seal_would_notice_a_change`).

## 8. What was added

`tests_solvent/test_reading_exam.py` gained what the capacity exam
already had, because preservation that nothing checks is a convention
rather than a guarantee:

* `test_the_current_seal_has_a_recorded_sitting`
* `test_two_sittings_are_recorded_because_the_seal_broke_once`
* `test_the_first_sittings_own_evidence_file_still_exists` — asserts the
  preserved file's `frozen_at`, `seed` and marks
* `test_the_second_paper_is_not_the_first_paper` — asserts the freeze
  *and* the seed both moved, so a reseal cannot quietly become a re-mark

## 9. Status

| Examination | Status |
|---|---|
| `tests_solvent/readingexam.py` (reading) | **PASS** — resealed, re-set, re-sat, 56/56 |
| `tests_solvent/capacityexam.py` (capacity) | **PASS** — resealed, re-set, re-sat, 6/6 |

Both seals are intact against the current source, both papers are new
papers, both sittings are real and recorded, and every earlier sitting
survives.

## 10. What this does not say

* It does not say the changed modules are correct because the exams
  passed. The exams measure what they measure: reading fidelity and
  behaviour under load. Finding D's repair is certified in
  `deskpilot-transform-order-certification.md` and Finding B's in
  `deskpilot-invoice-recovery-certification.md`, on their own evidence.
* It does not say a passing sealed paper is production readiness.
* Every measurement is from Linux. Nothing here is a Windows or
  MetaTrader 5 claim.
