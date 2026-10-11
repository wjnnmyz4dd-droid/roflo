# The control centre was telling the owner things that were not true

**Scope:** every read the owner control centre performs, audited against the
schema and the authorities that actually write the data.
**Method:** execution, not reading. Every projection method was run against a
real database, and every policy path was compared with the code that writes it.
**Outcome:** four defects, all confirmed by execution, all fixed. Two of them
misreported safety state in the reassuring direction.

This is a companion to `solvent-architecture-audit.md`, which audited a
different thing at a different time. Nothing here supersedes it.

---

## 1. Why this was not caught by the test suite

The control centre had 137 passing tests. It still displayed a halted business
as running. The reason is worth stating plainly, because it is the same shape as
the two defects before it in this project:

`ReadModel.rows` caught every `sqlite3.OperationalError` and returned `[]`, on
the reasoning — correct, as far as it goes — that a fresh database has no tables
yet and an empty section is better than an error page. What it also absorbed was
`no such column`. A query naming a column its table does not have therefore
returned nothing, forever, silently.

Tests then asserted on what those methods returned. What they returned was
consistently empty, so the tests agreed with the defect. One test went further
and *certified* it: `test_nothing_external_happened` asserted the egress
allowlist was empty, which was true only because the read was broken.

The lesson, recorded because it keeps recurring here: a swallowed exception
converts a defect into a plausible answer, and a plausible answer is what a
test is least able to tell from a correct one.

---

## 2. The defects

### 2.1 Every policy read on every page returned its default

`ReadModel.policy_value` ran:

```sql
SELECT value FROM policy_current WHERE key = 'document'
```

`policy_current` is `(id, version)`. It holds a pointer; the document lives in
`policy_versions.doc`. The query named two columns that do not exist, the error
was swallowed, and every caller received the default it had passed in.

Measured, on a database where the owner had halted the business and left
simulation:

| Fact | Truth | What the owner was shown |
| --- | --- | --- |
| Operating mode | `HALT` | `NORMAL` |
| Halted | yes | no |
| Simulation only | `False` | `True` |
| Egress allowlist | `['api.stripe.com']` | `[]` |
| Contracting structure | `INDIVIDUAL`, name and address recorded | nothing recorded |

The simulation row is the dangerous one. The page told the owner that nothing
real could happen, on a system where real effects were permitted.

**Fixed:** the document is read through the join the PolicyStore itself uses.

### 2.2 The operating mode was read from a path nothing writes

`posture()` read `("operating", "mode")`. `PolicyStore.set_operating_mode`
writes top-level `operating_mode`. So even with 2.1 fixed, HALT read as NORMAL.

The default was also wrong in direction: `"NORMAL"`, where
`PolicyStore.operating_mode` defaults to `HALT`. A projection that cannot read
the mode was telling the owner the business was running.

**Fixed:** the right key, and the authority's own fail-safe default.

### 2.3 The verification section of every job page was empty

`verification(job_id)` queried `verification_evidence` for a `job_id` column.
The column is `subject_ref`, which is what `record_verification` is given the
job id as. The one page whose purpose is telling the owner whether the work was
checked showed nothing, for every job, always.

**Fixed:** `subject_ref`.

### 2.4 The readiness page asked for fields nothing returns

Three separate cases, all reading ACTION REQUIRED against work the owner had
done:

* step 2 read a top-level `contracting` key; the data is under
  `governance.contracting`;
* step 3 read `models.approved`; the data is under `governance.approved_models`;
* step 1 read `setup["owner_key"]`, a field `owner_setup()` has never returned,
  so it was `None` on every render.

Steps 2 and 3 now come from `owner_setup()`, which reads the paths the
PolicyStore writes — one source rather than two that disagree.

Step 1 is different, and the fix is not a path. The owner key lives in the
runtime's environment, which the web process cannot read and should not. There
are only two honest answers: `PROVEN`, when an owner approval exists, because
nothing can mint one without the key; and `UNKNOWN`, when this page cannot see.
The step now says *cannot tell from here* rather than accusing the owner of not
having done it, and the all-clear banner cannot be reached past an unchecked
step.

### 2.5 A misnamed field, which is how 2.4 happened

`owner_setup()` returned a field called `approved_models` that held the single
production artifact. Anybody looking for "the cleared models" would not find
them there — which is presumably why the readiness page went looking under
`models.approved`. It now returns `production_model` (what this deployment
runs) and `cleared_models` (every artifact the owner has cleared), named for
what they are.

---

## 3. What was changed, and what was deliberately not

**Changed:** the four queries and paths above; `rows()` now re-raises anything
that is not a missing table; the same swallow in `discovery.py` narrowed the
same way, where the query was already correct and the risk latent.

**Not changed:** no authority, no classification, no gate, no approval rule. The
control centre still cannot write to the database, still holds no owner key, and
still cannot complete a consequential action. These were reporting defects, and
the fix for a reporting defect is to report correctly.

**One test was rewritten rather than kept passing.**
`test_nothing_external_happened` asserted an empty allowlist, which was an
artefact of the defect. A host being permitted is not an external event, and the
rehearsal legitimately records one. The claim now rests on
`action_requests.simulated`, which the Gate sets on every action it allowed —
evidence that nothing was carried out for real, rather than a field that
happened to be empty.

---

## 4. The guard that was missing

`EveryProjectionQueryMatchesTheRealSchema` executes every method on `ReadModel`
against a real schema. SQLite validates table and column names when it prepares
a statement, so this finds a wrong name whether or not any row would match. It
covers 55 methods today and fails if that number drops.

It would have caught 2.1 and 2.3 on the day they were written. It does not catch
2.2 or 2.4, which name columns that exist and paths that are merely wrong — those
were found by hand, by comparing all 17 policy reads against their writers, and
are held shut by the posture and setup-page tests instead.

Eight mutants cover this work in the permanent battery. One of them is worth
keeping in mind: mutating the policy read to return nothing at all does **not**
fail the HALT test, because the fail-safe default is HALT. A dead read looks
correct from the safe direction. The mutant is pinned to the simulation test
instead, where the truth and the default differ.

---

## 5. Still true, and still a limitation

Nothing in this round addressed the two pages that cover overlapping ground —
`/wizard` ("Getting started") and `/setup` ("Owner setup") both report setup
state, now from one projection but still as two lists. That is a duplication of
presentation, not of authority, and it is recorded here rather than fixed.
