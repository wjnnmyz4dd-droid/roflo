# The Skills Lab

Where a new ability goes from "a client wanted something we cannot do" to "the
owner has approved it, on evidence". It is a **lifecycle around** Solvent's
capability registry, not a second authority over it.

---

## 1. Why this exists, in one paragraph

A lab that builds what it is told, from what it is told, is a machine for turning
confident wrongness into certified capability. Documentation is stale, models
assert things, clients claim things, and prose inside a supplied document can be
written to be believed. So most of the Lab is about being *stopped*.

---

## 2. Four gates before a line is written

**1. A need, with evidence.** Which job exposed the gap, and how many times. A
frequency claim with no job behind it is refused outright — "this happened nine
times" with nothing to point at is a number somebody liked.

**2. The overlap gate.** Does anything already do this? It runs before any work
and can conclude that **nothing needs building**. Three of its six verdicts
authorise no build at all:

| Verdict | Meaning |
|---|---|
| `EXISTING_SKILL_SUFFICIENT` | Already covered. Build nothing. |
| `UPDATE_EXISTING` | Same scope, reported defective. A fix. |
| `UPGRADE_EXISTING` | Wider or changed scope on something that exists. |
| `NEW_SKILL_REQUIRED` | Genuinely new. |
| `HUMAN_REQUIRED` | A person must do this. |
| `CANNOT_EXECUTE` | Not something Solvent can do at all. |

The gate catches the rename: asking for the same checks under a new name is still
an overlap, because two capabilities over the same checks have overlapping
authority and no rule about which decides.

A **defect** is different from a feature request, and must be described. A fix
has the same scope as the thing it fixes, so refusing covered scope outright
would mean a capability could never be repaired.

**3. Evidence that stands up.** Every claim the build relies on is recorded with
its source and a trust level, and starts `UNVERIFIED`. An unverified claim blocks
the build.

| Trust level | Can it carry a build on its own? |
|---|---|
| `OWNER_STATEMENT`, `MEASURED`, `PUBLISHER_PRIMARY`, `THIRD_PARTY` | Yes, once verified |
| `CLIENT_ASSERTION` | **No** — a fact about the client, not about the world |
| `MODEL_ASSERTION` | **No** |
| `UNSOURCED` | **No** |

The bottom three cannot be marked `VERIFIED` at all. Checking such a claim means
finding a better source that agrees, recorded as its own claim. Contradictory
claims are marked and **block** rather than being picked between — choosing
between contradictory sources is a judgement the Lab has no basis to make.

The defence is not lie detection. It is refusing to proceed on anything nobody
checked.

**4. The owner's permission to develop.** The registry already holds this, and
`LIMITED` means *build it and show me* — not *deploy it*.

---

## 3. Update, upgrade, new — and why the Lab decides which

| | What it is | Version |
|---|---|---|
| **UPDATE** | A fix. Scope unchanged. | patch: `1.0 → 1.0.1` |
| **UPGRADE** | Scope widened *or narrowed*. | minor: `1.0 → 1.1` |
| **NEW** | A capability that did not exist. | the owner names it |

**Classification is computed from the scope, never accepted from the caller.** An
update is meant to be cheap, so a scope expansion has every incentive to be
labelled one — which is how a capability grows without anybody approving it. A
declared `UPDATE` over a widened scope is recorded as an `UPGRADE`, and the
reason says so.

Narrowing is also not a fix: withdrawing a check is a changed promise, and a
client relying on the removed one is affected.

A breaking change is **major**, and nothing here decides that a change is
breaking — that is a judgement about clients, not about code.

---

## 4. What the Lab cannot do

- Write its own passing evidence, or mark its own claim verified.
- Certify itself. Certification is the registry's, from a real trial report.
- Promote anything. It records that the owner promoted, and refuses if the
  registry has not actually registered it.
- Offer an uncertified project to the owner. Asking you to approve something the
  evidence does not cover is asking you to *be* the evidence.
- Accept a certification short of the scope it specified.
- Skip a stage, or build without naming the code it produced.
- Write policy, the ledger, the audit chain or the capability registry. Its
  database authority covers four tables and nothing else.

---

## 5. Versions live forever

A version's record is append-only. A lifecycle change writes a **new row**; the
old one stays. So a rollback is explicable afterwards rather than a gap.

| Lifecycle | May a new job use it? |
|---|---|
| `ACTIVE` | Yes |
| `DEPRECATED` | Yes — a warning, not a withdrawal |
| `SUPERSEDED` | Yes |
| `REVOKED` | No |
| `RETIRED` | No. Owner only. |

**Rollback** withdraws a bad version and points new work at an earlier trusted
one. It does not touch jobs already running: a job verified against the version
it ran on stays verified, and rewriting that would make its evidence describe
code it never used. Rolling back onto a withdrawn version is refused — that would
put new work on something already judged unfit.

---

## 6. What you see, and what you decide

The Skills Lab page shows every project, its stage, the overlap verdict, the
evidence with each claim's status, and the full timeline. A project reaches you
only when it is **certified**, and then it waits.

Your decision is whether the capability should exist and be sold. The evidence
question has already been answered — by the battery, not by you.
