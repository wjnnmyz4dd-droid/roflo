# DeskPilot — recovering a job interrupted at the start of the work (finding A)

**Status: VERIFIED FIXED**

Date: 2026-10-18 · Platform: Linux x86_64 · No real external effect.

---

## 1. Reproduction

From the eleven-point crash matrix: killing the process inside
`orchestrator.begin_execution` leaves the job in `ACCEPTED`, holding its
pipeline slot.

Before the repair, a restart did nothing with it:

* `runtime.reconcile` treats `EXECUTING`, `VERIFYING` and
  `READY_FOR_DELIVERY` as mid-flight. `ACCEPTED` is not in that set.
* `resume_interrupted` resumes `EXECUTING` and `VERIFYING`. `ACCEPTED` is
  not a recompute in progress.
* `TIMEOUTS_MINUTES[ACCEPTED]` is 120, and `escalate_overdue` only
  escalates — two hours later, and to a person.

So the slot stayed taken and nothing would ever start the job.

## 2. Why ACCEPTED is not simply added to the resumable set

`ACCEPTED` is a weaker position than `EXECUTING`. A job reaches it by
being admitted and committed, which happens whether or not anything was
then attempted. Resuming every `ACCEPTED` job would start work the
scheduler had not chosen to start, and would spend compute the Governor
may never have authorised.

So the repair asks for **durable evidence that execution had really
begun**, and the evidence is two records written by two different
authorities:

| Record | Authority | What its absence means |
|---|---|---|
| A live budget grant for the job | Governor | the work was never authorised; starting it spends an unbudgeted compute |
| A `SOURCE` artifact on the job | orchestrator | the job cannot say which file it was given, so the work cannot be redone from what was kept |

Either missing → the job is **escalated to the owner**, explicitly, with
the reason. Never left quiet, never guessed at.

`solvent/governor.py` gained `grant_for(job_id)`, a read-only accessor
that ignores revoked grants: withdrawn funding is not permission to
restart.

## 3. What was changed

`solvent/harness.py`:

```python
RESUMABLE            = (JobState.EXECUTING, JobState.VERIFYING)
RESUMABLE_ON_EVIDENCE = (JobState.ACCEPTED,)
CONSIDERED            = RESUMABLE + RESUMABLE_ON_EVIDENCE
```

plus `_why_not_startable(s, job_id)`, which returns the reason or `""`.

The evidence is checked **before** the attempt is recorded, so a job
escalated for want of evidence has not been tried and does not burn one of
the three attempts a genuinely recoverable job gets.

Everything else is unchanged and still applies to the new state: the same
`_execute_job` a first attempt runs, the same delivery gate, the same
bound of three attempts counted from the append-only audit log, the same
escalation to the owner when they are exhausted.

## 4. Tests

`tests_solvent/test_accepted_recovery.py` — **12 tests**.

* the job is picked up, and does not stay in `ACCEPTED`;
* after the whole restart path (`recover_after_restart`) **nothing** is
  left in `ACCEPTED`;
* a job the Governor never funded is escalated, not started;
* a job with no source on record is escalated, and the reason says so;
* an escalated job is still there afterwards — never discarded;
* attempts are bounded, and the bound survives repeated restarts;
* a crash *during* recovery still leaves the attempt recorded and an
  incident filed, so the next restart counts it;
* two recoveries do not invoice twice and do not take a second slot;
* the audit chain verifies afterwards.

## 5. Mutation

| Mutant | Verdict |
|---|---|
| `recovery-never-looks-at-an-accepted-job` | KILLED |
| `recovery-starts-work-the-governor-never-funded` | KILLED |
| `recovery-starts-work-whose-source-nobody-recorded` | KILLED |
| `recovery-counts-a-revoked-grant-as-authorisation` | KILLED |

## 6. Limits

Nothing currently sets `budget_grants.revoked`. The filter in `grant_for`
is a guard for the day revocation is wired, and its test says so rather
than implying a behaviour that exists today.
