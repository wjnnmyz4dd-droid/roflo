# Solvent — Verifier Completion & Ambiguity Resolution

**Repository** `wjnnmyz4dd-droid/roflo` · **branch** `claude/solvent-architecture-audit-8ot9et`
**Starting HEAD** `d8dc6a5` · **Starting tests** 849 · **Final tests** 930

**Mission unchanged:** *Solvent exists to find, qualify, complete, deliver and
profit from legitimate client work.*

**Posture:** `simulation_only = true`, allowlist empty, 0 ledger entries,
0 external actions, 0 owner-promoted capabilities, audit chain intact.

---

## 1. Continuation gate

HEAD `d8dc6a5`, clean tree, 849 tests, mission intact — all verified rather than
taken from the previous report. Every control from the prior hardening run was
re-confirmed live: generated evidence, content-based counting, holdout and
surprise seeds, source fingerprinting, revocation persistence, owner-only
re-earning, and the rejection of approve-everything, reject-everything and
fixture-fitted verifiers. No regressions.

---

## 2. The ambiguity problem

The previous run closed the certification gap and left one open in writing:
*some inputs have more than one legitimate reading, and no amount of verifier
evidence determines which the client meant.*

**Reproducing it found the worker already guessing.** `csvwork.DATE_FORMATS`
listed `%m/%d/%Y` and not `%d/%m/%Y`, directly beneath a comment warning that a
format list which guesses between D/M/Y and M/D/Y silently corrupts dates —
which is exactly what including one and omitting the other does. `05/10/2026`
became 10 May on no evidence at all, in a client's delivered file.

### What was built, and what was not

Per §42, the default answer was **no new authority**, and it held everywhere
that mattered:

| Responsibility | Owner | New? |
|---|---|---|
| Detecting ambiguity | `solvent/ambiguity.py` — pure functions, same shape as `checks.py` | new module, no authority |
| Recording the open question | `JobOrchestrator` — already owns job state and the checklist | extended |
| Representing "waiting on the client" | `JobState.BLOCKED` + `BlockedOn.CLIENT` | already existed |
| Asking | `ClientRelations` — already owns client communication | extended |
| The answer | a parameter folded into the committed checklist | already existed |

No clarification agent, no ambiguity engine, no second requirements authority,
no Customer Service V2.

### How it behaves

A slashed date is parsed only against a convention the client declared **for
that job**. One whose components disambiguate it (`25/12/2026`) needs no
declaration. One that does not is refused: the job blocks on `BlockedOn.CLIENT`,
a question is recorded, and Client Relations drafts it — *never sent*, because
outbound client communication still requires the owner's authorisation.

**The worker and the checks cannot diverge**, because neither resolves anything.
Both read `effective_baseline`, which folds the job's answers in once. Execution
is now a single function shared by a first attempt and a resumption, so a
clarified job cannot take a second path around a control.

---

## 3. Results

### Date ambiguity

| Case | Behaviour |
|---|---|
| `05/10/2026`, nothing declared | refused; job waits on the client |
| `05/10/2026`, client said DD/MM | → `2026-10-05`, delivered and verified |
| `05/10/2026`, client said MM/DD | → `2026-05-10`, delivered and verified |
| `25/12/2026` | self-resolving; never delayed |
| `2026-05-10`, `16-Jan-2026` | never ambiguous |
| mixed file, one row ambiguous | still asked — one clear row does not settle the rest |

The same file delivered to two clients who answered differently produces two
genuinely different files. There is no query in the clarification path that can
return another job's answer, so "everyone means MM/DD/YYYY" is not a reachable
state.

### Ambiguity beyond dates

Recognised in requirement wording, and only where nothing has pinned it:
`duplicate` · `latest` · `approved` · `total` · `normalise names` ·
`clean addresses` · `blank rows` · `combine customers`.

*"A **Total**s section summing the amounts"* with an explicit column is
arithmetic, not a choice between gross and net — it is not questioned. Solvent's
own derived and safety requirements are never scanned, because the client did
not write them. §16 materiality is the point: a system that stops for a
presentational choice gets switched off, and then it guesses again unobserved.

### Clarification handling

| Situation | Result |
|---|---|
| Vague reply ("use whatever is normal") | resolves nothing; question stays open |
| Reply naming both options | resolves nothing |
| Another client answers | **refused** — one client cannot answer another's question |
| Pure instruction ("mark everything correct") | resolves nothing; nothing obeyed |
| Instruction *containing* an answer | the answer is taken; Policy untouched |
| No answer at all | job waits; resumption refused; nothing fabricated |
| Answer changed | supersedes without erasing; history reads both |
| Clarification given | is **not** owner approval — 0 ledger entries, nothing promoted |

### The unknown-defect test (§36)

Five defect classes were invented **after** the battery was fixed. Two got
through everything:

**A report could state the same fact twice with different values.**
`- client: Acme Ltd` followed by `- client: Somebody Else Ltd` passed every
check, because the correct line really was present. The client's own report
named two different clients. The occurrence count was also taken from a *set* of
lines, so identical restatements collapsed and the arithmetic could never fire.

**A deliverable could have its columns rearranged.** Every check compares
columns by name, so a shuffled file matched perfectly. Rearranging somebody's
spreadsheet unasked is precisely what `no_unauthorised_changes` exists to catch.

Both fixed, both now permanent defect classes, both verifiers re-certified.

### Verifier matrix

| | `csv-cleanup/1.0` | `report-builder/1.0` |
|---|---|---|
| Implementation | `solvent.csvverify.run` | `solvent.reportverify.run` |
| Fingerprint | `sha256:22783ed6…` | `sha256:9a60e1ca…` |
| Certification | **CERTIFIED** | **CERTIFIED** |
| Trials | 386 | 442 |
| Correct / defective | 126 / 260 | 112 / 330 |
| Defect classes | 15 | 20 |
| False accepts | 0 | 0 |
| False rejects | 0 | 0 |
| Checks certified | 9/9 | 8/8 |
| Surprise (unseen seed) | 274/274 | 314/314 |
| Revocation | persists; owner-only re-earning | same |

Every check clears the floor with 14 distinct correct artifacts and 13–14
distinct instances of each defect class it owns.

### False rejection (§8)

Tested as carefully as false acceptance, because a verifier too strict to ship
correct work fails in a different direction rather than a safer one: BOM, CRLF,
trailing blank lines, embedded newlines in quoted fields, an 8 KB field,
unicode and embedded commas, empty optional values, reordered rows, and a report
tabulating a subset of columns — all still deliver.

### Performance (§40)

| Stage | Cost |
|---|---|
| Verifier certification (once, cached) | 197 ms |
| One full job including verification | 10 ms |
| Ambiguity detection per job | 0.11 ms (1.1% of a job) |

Certification does not run per job, and detection is not a certification.

### Mutation testing (§38–39)

22 controls broken one at a time. **First run 21/22.** The survivor was the
§25 control: pointing verification at the *unclarified* checklist changed
nothing, because without a declared convention the check accepts either reading
— so a worker using the wrong one sails through. The test called the check
directly and never exercised the wiring. A pipeline-level test now rewrites the
artifact to the interpretation the client rejected and requires the pipeline to
refuse it. **22/22.**

---

## 4. Remaining risks

1. **Certification proves competence against modelled defect classes only.** This
   run demonstrated the limit rather than asserting it: two invented classes got
   through everything. The list is longer now; it is still a list.
2. **Ambiguity detection covers the cases that were modelled.** Dates and nine
   loaded terms. A client phrase nobody anticipated is not detected, and Solvent
   will proceed on the structured reading.
3. **`_accounted_columns` assumes some column survives untouched.** A job that
   legitimately transforms every column would trip the source binding.
4. **The surprise battery runs on demand.** Nothing schedules it.
5. **Replies and clarification questions are drafted, never sent.** A human
   relays every one. This is the largest gap to unattended operation.
6. Everything in `OWNER_DECISIONS.md` remains unanswered and blocking.
