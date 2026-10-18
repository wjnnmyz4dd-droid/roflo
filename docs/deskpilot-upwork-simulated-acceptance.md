# DeskPilot — Upwork simulated acceptance

Date: 2026-10-18 · Platform: Linux x86_64 · CPython 3.11 · Branch
`claude/solvent-architecture-audit-8ot9et`

Thirty-seven scenarios, every one the brief names, against synthetic
opportunities.

```
scenarios 37   passed 37   failed 0
real bids 0    real Connects spent 0
real customer messages 0    live submissions 0
```

Harness:
`/tmp/claude-0/-home-user-roflo/25de398b-b56b-53d8-b59f-f68119ec6f42/scratchpad/upw/simulate.py`
Result:
`/tmp/claude-0/-home-user-roflo/25de398b-b56b-53d8-b59f-f68119ec6f42/scratchpad/upw/simulated_acceptance.json`

The zeroes in the second row are not a convention of the harness. The
module it exercises has no HTTP client, no socket and no subprocess,
and a test asserts it.

---

## 1. Every scenario

### Competition

| Scenario | Outcome |
|---|---|
| Low competition | bid near the budget |
| High competition | bid driven down, still above the floor |
| Missing competition data | no guess at undercutting; falls back to the client's budget |

### Budgets

| Scenario | Outcome |
|---|---|
| High client budget | undercuts the estimated competition |
| Low client budget | the floor stands above it |

### Profitability

| Scenario | Outcome |
|---|---|
| Profitable low bid | contribution stated |
| Unprofitable low bid | negative contribution shown as negative, not hidden |
| Missing fee data | contribution **UNAVAILABLE**, not optimistic |
| Expensive Connects requirement | lowers profit per Connect |
| Poor client hiring history | **UNAVAILABLE** rather than invented |

### Skill match

| Scenario | Outcome |
|---|---|
| Strong skill match | priced |
| Weak skill match | not priced, and says why |
| Ranking | the unpriceable one sorts last |

### Owner approval

| Scenario | Outcome |
|---|---|
| Owner approval | binds to this recommendation, and verifies |
| Owner rejection | no approval, not eligible |
| Expired approval | refused |
| Changed bid after approval | refused as a different proposal |
| Duplicate approval | a consumed approval cannot be reused |
| Wrong action class | a communication approval does not authorise a proposal |

### Authentication and delivery

| Scenario | Outcome |
|---|---|
| Authentication failure (no key) | the channel approves nothing |
| Authentication failure (stranger) | refused |
| Notification failure | recorded `FAILED`, not swallowed |
| …and its consequence | the proposal stays unapproved; silence is not consent |

### The platform boundary

| Scenario | Outcome |
|---|---|
| Platform confirmation requirement | DeskPilot still does not submit |
| …and what the owner is told | "sign in to Upwork and submit it yourself" |

### Budget

| Scenario | Outcome |
|---|---|
| Monthly budget exhaustion | refused |
| Connects remaining | tracked, 100 purchased − 60 consumed = 40 |
| Per-skill allocation | five skills × 15 proposals inside budget |
| …and starvation | no skill spent nothing |
| Acquisition cost before a win | **UNAVAILABLE**, with the spend to date |
| Acquisition cost after a win | counts the losses too |

### Five-skill capacity

| Scenario | Outcome |
|---|---|
| Default per-skill job ceiling | as the constant defines |
| A complete budget at the default | accepted |
| A budget above the ceiling | refused, **and refused for that reason** |

### The boundary itself

| Scenario | Outcome |
|---|---|
| No network call in the module | confirmed by inspection |
| The Upwork submission client | prohibited |

## 2. Two scenarios that were passing for the wrong reason

Worth recording, because a green harness is only worth what its weakest
scenario is worth, and both of these were green before they were right.

**The capacity ceiling asked policy for a key that does not exist.**
`max_jobs_per_skill` is not a policy key, so
`policy.get(..., default=4)` returned the default this harness had
supplied and the assertion compared 4 against 4. It would have passed
with capacity deleted from the system. It now reads the real budget
through `Capacity.budget()` and the real `JOBS_PER_AGENT_DEFAULT` and
`JOBS_PER_AGENT_CEILING` constants.

**Having fixed that, the refusal was being read off the wrong
complaint.** `Budget.problem()` returns the first thing wrong, and the
synthetic policy had no `cpu_share`, so "a budget above the ceiling is
refused" was passing on a cpu_share message and would have passed with
the ceiling removed. It now sets a complete, valid budget first and
asserts the refusal *names the ceiling*.

The same failure shape has now appeared several times across this
engagement — a test whose name claims more than it checks, kept honest
by a sibling control. In each case the test was strengthened rather
than the criterion lowered.

## 3. What was deliberately not tested

* **Anything requiring an Upwork account.** No authentication was
  attempted against `mcp.upwork.com`, no client registered, no Connect
  bought or spent.
* **Submission.** There is no submission path to test. Its absence is
  asserted instead.
* **Real competition or fee figures.** They cannot be obtained; the
  scenarios exercise what DeskPilot does *without* them, which is the
  behaviour that matters today.

## 4. What this acceptance does not say

* It does not say DeskPilot can bid on Upwork. It says that if it ever
  may, the controls around the owner's decision behave as specified.
* It does not say the figures are right. It says they are labelled, and
  that unknown ones stop the arithmetic rather than flattering it.
* It does not say this is production readiness.
* Every measurement is from Linux. Nothing here is a Windows or
  MetaTrader 5 claim.
