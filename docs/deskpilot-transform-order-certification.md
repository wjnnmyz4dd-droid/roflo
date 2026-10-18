# DeskPilot — transformation order and protected columns (finding D)

**Status: VERIFIED FIXED**

Date: 2026-10-18 · Platform: Linux x86_64 · No real external effect.

---

## 1. Reproduction

```python
source  = "Name,Email,Amount\n  Acme Ltd ,a@acme.test,100.00\n"
          "Acme Ltd,a@acme.test,100.00\nBeta Co,b@beta.test,250.50\n"
output  = "Name,Email,Amount\nAcme Ltd,a@acme.test,100.00\n"
          "Beta Co,b@beta.test,250.50\n"       # trimmed, then deduped
check_preserve_columns(source, output, {"columns": ["Name", "Email"]})
```

```
RESULT:  FAIL
DETAIL:  'Name' changed: 1 value(s) lost or altered (e.g. ['  Acme Ltd ']),
         1 appeared (e.g. ['Acme Ltd'])
COMPUTED: {'Name': {'source': 3, 'expected': 2, 'output': 2}}
```

Correct work, failed.

## 2. Root cause

`csvwork` runs its operations in a **fixed order**, not the order the brief
lists them: `trim_whitespace` is rank 2, `drop_exact_duplicates` is rank 5.
So protected values have legitimately been trimmed by the time the
duplicates go.

`check_preserve_columns` got the *row count* right — its `_key` helper
already normalises whitespace when deciding which source rows are
distinct, so `expected: 2` matched `output: 2`. What it got wrong was the
*values*: `expected` was built from the **raw** cells of those distinct
rows, and compared byte-for-byte against output the brief had authorised
trimming.

The check was comparing against the source as it *arrived* rather than as
the agreed transformations *leave it*.

## 3. The repair

`solvent/csvverify.py`. The check now reads the committed brief, which
already travels with every check as `params["requirements"]` — the seam
existed; nothing new had to be threaded through, and the client's
committed checklist is not mutated.

Four branches, in order:

| Situation | Verdict |
|---|---|
| Values match exactly | PASS, without consulting the brief |
| They differ, and the normalisations **this brief authorised for this column** explain it | PASS, with `normalised_by` in the computed evidence |
| They differ only by padding and there is **no brief** to read | **UNVERIFIABLE** — blocks delivery exactly as FAIL does |
| The difference survives normalisation | **FAIL**, as before |
| The brief both protects and rewrites the column | **UNVERIFIABLE**, naming the contradiction |

The widening is deliberately narrow:

* the only modelled normalisation is `trim_whitespace` → `str.strip()`,
  which is what the worker's own operation list promises
  ("leading/trailing only; never inside a value");
* authorisation is **per column**: trimming `Email` does not license
  trimming `Name`;
* a trim requirement naming no columns covers them all, which is how the
  worker reads it too;
* authorisation grants the pass; a separate diagnostic probe only decides
  how an *unauthorised* difference is described.

## 4. Tests

`tests_solvent/test_transform_order.py` — **21 tests**. Every "this should
now pass" case is paired with the nearest thing that must still fail.

Passing: trim-then-dedupe with trimming authorised; a trim requirement
naming no columns; already-identical duplicates; empty values; a
whitespace-only value becoming empty; mixed types as text.

Still failing: the same output with no trim authorised; trimming a column
the requirement did not name; a dropped column; an unauthorised row
deletion; a fabricated value; a modified value; **a changed value in the
trimmed column**; a dropped row in the trimmed column; a case change;
internal whitespace rewritten; a numeric reformat; malformed or missing
output.

End-to-end: `test_four_repair_integration.py` previously had to use exact
duplicates to dodge this interaction. Its fixture now uses
whitespace-only duplicates — the real ordering — and the comment says why.

## 5. Mutation

| Mutant | Verdict |
|---|---|
| `preserve-normalisation-is-not-scoped-to-its-column` | KILLED |
| `preserve-accepts-padding-changes-with-no-brief-to-read` | KILLED |
| `preserve-ignores-a-brief-that-contradicts-itself` | KILLED |
| `preserve-forgives-any-difference-it-cannot-explain` | KILLED |

The last one **survived its first run**, and the finding was about the
tests: every "must still fail" case changed a column the brief had *not*
authorised trimming for, so a repair that simply stopped checking any
trim-authorised column passed them all. Two tests were added
(`test_a_changed_value_in_the_trimmed_column_is_still_caught`,
`test_a_dropped_row_in_the_trimmed_column_is_still_caught`) and the mutant
is now killed. The acceptance criteria were not changed.

## 6. Limits

`case normalisation where authorized` was tested and found to be
**unauthorisable**: csv-cleanup has no case-normalising operation in
`csvwork.OPERATIONS`, so a case change is never licensed and must fail.
That is asserted rather than assumed.
