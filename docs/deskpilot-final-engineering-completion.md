# DeskPilot — final engineering completion

**Implementation round.** Three of the four authorised repairs are
implemented and verified; the fourth is not done and is reported as
such. No live financial transaction, no marketplace bidding, no real
contract, no customer delivery, no VPS deployment. Every existing
authority — Governor, Brain, Action Gate, worker/checker pairs,
incident supervisor, contracting engine, ledger, capacity controller —
was reused and wired, never replaced.

Date 2026-10-18. Baseline `bb828d5`, verified clean before editing.
Evidence: `docs/evidence/deskpilot-final-engineering-evidence.json`.

---

## Status

| # | repair | status |
|---|---|---|
| 1 | Four commitments per skill | **VERIFIED FIXED** |
| 2 | Independent CSV T2 verification | **VERIFIED FIXED** |
| 3 | Owner signing and delivery | **NOT FIXED** |
| 4 | Startup reconciliation | **PARTIALLY FIXED** |

**ENGINEERING STATUS: INCOMPLETE.** Three of four. I am not calling
this complete, because repair 3 was not attempted and repair 4 stops
short of resuming interrupted work.

## Repair 1 — four commitments per skill · VERIFIED FIXED

**Original failure.** Global commitment counting let one skill consume
every slot. Offering a backlog grouped by skill, csv-cleanup took all
20 and four of the five skills delivered nothing.

**Root cause, and it is not what the brief assumed.** The capacity
controller *already* counted reservations per `capability`, *already*
enforced a per-pipeline limit and a separate global ceiling, and
*already* carried an idempotency key against a duplicated acceptance.
The `capacity_reservations` table has had a `capability` column since
an earlier migration. None of it was reachable: **nothing called
`Capacity.reserve`.** Exactly the shape of the supervision defect.

**Repair.** `Qualification` takes a capacity handle and reserves a slot
for each selected candidate, keyed by capability with
`idempotency_key="commit:<job_id>"`. `Capacity.release_job(job_id)`
gives back every slot a settled job holds — idempotent, returning a
count, because the business cycle and startup reconciliation may both
decide the same job has settled after a crash. The cycle releases at
reconcile.

A job whose verification tier is unreachable used to sit in `ACCEPTED`
for ever holding a slot. *Unreachable* is not *not yet*, so it is now
failed explicitly: that escalates, releases the slot and leaves a
reason. Failing is the opposite of approving; nothing unverified ships
because of it.

**The one judgement call.** Where no capacity budget is configured the
reservation returns `NOT_CONFIGURED`, and that falls through to the
existing global ceiling rather than stopping the shop. An unset budget
and a full pipeline are different sentences, and the enum already
distinguishes them. This does not override a deliberate shutdown: that
is `operating_mode`, which `intake` refuses against before anything
reaches here.

**Acceptance, 20 opportunities per skill:**

| | before | after |
|---|---|---|
| total delivered | 1 | **52** |
| skills delivering nothing | 4 of 5 | **0** |
| peak in-flight per skill | — | **4** (ceiling respected) |
| cross-skill starvation | yes | **none** |

## Repair 2 — independent CSV T2 review · VERIFIED FIXED

**Original failure.** T2 refused every `.csv` deliverable; 34 of 100
contracts stopped at verification-eligibility.

**Root cause.** Worker and checker both parse with the standard
library's `csv`, so a differential review using it would be Solvent
reading its own output twice — which the source says in as many words.

**Dependency exception**, taken under the authorisation given:

| | |
|---|---|
| library | `clevercsv` |
| version | **0.8.5, pinned** |
| licence | MIT |
| size | 145 kB wheel |
| declared | `pyproject.toml` `[verification]` extra, beside the existing `openpyxl` and `python-docx` |
| imported | lazily, as those are, so the zero-third-party architecture guard still holds |

**Independence verified, not assumed.** `clevercsv.read.reader.__iter__`
constructs its own C `Parser`; it imports stdlib `csv` only to
interoperate with dialect objects (`csv.get_dialect`, `csv.Dialect`),
never to read a file. I checked the source before adopting it, because
a reader that wrapped `csv` would have looked independent and been
worthless. `pandas` was the only other installable candidate and is
vastly larger with unrelated surface.

**Nine tests**, covering what the brief listed: quoted delimiters,
embedded newlines, an inferred semicolon dialect, empty fields, a
correct deliverable passing, and fabricated, altered and unreadable
cases all failing closed. The reader is not told the delimiter — being
told it by the side under review would make the second opinion agree
by construction on exactly the question a delimiter bug gets wrong.

**Measured effect.** CSV jobs now reach independent review. Briefs
containing nothing the reader can recompute still refuse and ask for a
person — *"a second automated opinion would only be confirming that the
file parses"* — which is the existing fail-closed design, not a
regression and not a rubber stamp.

## Repair 4 — startup reconciliation · PARTIALLY FIXED

A `runtime.reconcile()` already existed and handled the hardest case
well: an external action started and never settled is **not** retried
and **not** assumed to have succeeded; the job is blocked on the owner.
I extended that function rather than writing a second one.

**Added.** A sweep for jobs found in `EXECUTING`, `VERIFYING` or
`READY_FOR_DELIVERY` after a restart — none of which is a resting
place, so a job found there was interrupted. Each is recorded as an
incident and blocked on the owner. A second sweep releases pipeline
slots held by jobs that are terminal, `COMPLETE`, or gone.

**Deliberately not resumed.** Re-running a checker is safe in itself,
but an interrupted job may also have had an external effect nobody
wrote down, and deciding to carry on with work already paid for is a
judgement. So this surfaces rather than guesses. Idempotent: a job
already `BLOCKED` is left alone, so a double restart reports nothing
the second time.

**Why PARTIALLY and not VERIFIED.** The brief asks for interrupted
verification to be restored where safe, and for crash tests at eleven
named lifecycle points. I implemented surfacing and capacity
reconciliation and tested five cases. **No automatic resume exists**,
and the eleven-point crash matrix was not re-run this round.

## Repair 3 — owner signing and delivery · NOT FIXED

Not attempted. The defect stands as measured at `3c7d6d0`:
`harness.py` mints an *unsigned* approval for the C2 delivery effect,
so configuring an Owner Channel makes the gate refuse every delivery,
T1 work included — 0 of 100 with a key set. It needs its own focused
change to the Action Gate authorisation flow and the full
twelve-case acceptance matrix the brief lists, and I would rather
leave it clearly undone than half-wire a security path.

## The sealed capacity examination

Changing `capacity.py` broke the seal, which is what the seal is for.
Handled as a recorded act, not a quiet re-freeze:

1. The **sixth paper was re-sat** against the changed source and passed
   in full — 13 tests, 32 subtests — so the change altered no answer.
2. The **first sitting is preserved** in the record, not overwritten.
3. Because the seed derives from `FROZEN_AT`, moving the date generates
   a **different paper**. The seventh sitting is a genuine second
   examination, and it passed **6 of 6**, with no duplicate commitment
   accepted and no obligation seconds lost across any restart.

## Test failures I caused, and how each was handled

Four, and none was fixed by weakening a test:

| failure | handling |
|---|---|
| capacity surface guard rejected `release_job` | added to the declared surface **with justification** — it answers no new question |
| sealed exam | re-sat and re-sealed as above |
| two tests asserting *".csv has no independent reader"* | the invariant is *"a format with no reader is refused"*, so the example moved to `.pdf`, **plus a positive csv test** so the move cannot hide a regression |
| `TheRuntimeIsNotAnAuthority` tripped on the word "approve" in my docstring | reworded the comment |

The last is the second time a prose guard has caught my own writing.
The capacity suite learned this and uses an AST; the runtime one still
scans text.

## Regression

| | before | after |
|---|---:|---:|
| passed | 4,728 | **4,749** |
| failed | 0 | **0** |
| skipped | 44 | 44 |
| subtests | 10,085 | 10,085 |

The 44 skips are the pre-existing egress-guard subprocess suite and are
**not counted as passed**.

## Mutation

**155 mutants, 155 killed, 0 survived, 0 NOT_APPLIED.** Run after all
three repairs, sequentially, with no other harness running; sources
verified byte-identical to the commit afterwards.

Unchanged from the pre-repair run, and that is what it needs to say:
no mutant survived on anything the repairs touched — the capacity
reservation path, release-on-settle, or the CSV provenance check. A
survivor there would have meant my own tests were not pinning the new
code, and would have been a gap to fix rather than a number to
report.

## What remains

1. **Repair 3**, untouched.
2. **Automatic resume** of interrupted verification (repair 4's second
   half).
3. **The eleven-point crash matrix** and the integrated 100-opportunity
   acceptance run after all four repairs.
4. ~~Mutation testing after these changes~~ — **done: 155/155 killed.**

MT5 coexistence remains **uncertified**: everything here is Linux.
