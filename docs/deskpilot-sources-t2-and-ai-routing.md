# DeskPilot: one usable source, T2 on Windows, and an evidence-based AI route

Written after executing the system. Where something could not be executed,
it says so rather than estimating.

Commits in this sweep:

| commit | what |
|---|---|
| `5f319d8` | source suitability and the active interface; T2 dependency packaging |
| `60adade` | cloud backend, inference routing, three-way benchmark |
| `(this)` | redact source failure reasons; the consolidated report |

---

## D. The thirteen blockers — exact status

| # | blocker | status now | evidence |
|---|---|---|---|
| 1 | Contracting identity | **OPEN — owner** | Invoice still renders `(contracting identity not recorded)`. The machinery is complete: `CONTRACTING_PURPOSES["INVOICE"]` requires `legal_name`, `address`, `email`; a readiness check reports the shortfall; `solvent setup` records it. Nothing can infer these. |
| 2 | Operating-cost rates | **OPEN — owner** | Verified working when set: with the owner's rates a real cycle posts PLATFORM_FEES 4000 and PAYMENT_FEES 1190 and reports 87.0% margin. Shipped default is zero, which `configuration_gaps` reports as "every margin shown is better than the real one". |
| 3 | Minimum project fee | **OPEN — owner** | Unset by default and reported as a gap, not treated as "any price". |
| 4 | Minimum margin | **OPEN — owner** | Unset. A $10 profit on a $400 job needing $390 up front still reads PURSUE. |
| 5 | Cited market rates / work standards | **OPEN — owner** | `WorkStandard.__post_init__` refuses a blank citation, so none can be recorded without evidence. Fixed prices can be accepted, never proposed. |
| 6 | `HOURLY_MINIMUM` per source | **OPEN — owner** | Permission exists, never granted by registration. Requires reading each platform's terms. |
| 7 | **Package the differential readers** | **RESOLVED (code) — Windows verification outstanding** | Declared as a `verification` extra; `solvent` added to the packages list (the console script pointed into a package setuptools excluded); the installer self-check probes the target machine and reports PASS/WARN honestly; readiness check OD-18 surfaces it before a job is taken. |
| 8 | A source that sells the work the five skills do | **OPEN — unchanged, now visible** | No such adapter exists. What changed is that the interface no longer implies otherwise: see section below. |
| 9 | `SAM_GOV_API_KEY` | **OPEN — external** | Reported `NEEDS_CREDENTIAL` and excluded from the active interface rather than shown as healthy. |
| 10 | Payment rail with a verification signal | **OPEN — external** | Unchanged. Without the signal `PAID` is unreachable from a real payment. |
| 11 | Terms review per marketplace | **OPEN — platform** | Unchanged. No consequential permission is granted by registration. |
| 12 | Windows verification | **OPEN — hardware** | Nothing in this sweep ran on Windows. Blocker 7's fix is specifically a Windows behaviour change that has **not** been observed on Windows. Linux results do not establish Windows correctness and none is claimed. |
| 13 | Real-world pilot evidence | **OPEN — needs a customer** | Simulation enabled throughout. No client, no payment, no marketplace interaction. |

**Two resolved in substance (7 in code, 8 in honesty), eleven open.** Of the
eleven, six are the owner's to enter, two external, one per-platform, one
needs a Windows host, one needs a customer. None is waiting on more code in
this repository.

### Blocker 8, restated honestly

The audit ran against the real registrations:

```
source          work_type        verdict            why
demo_board      UNDECLARED       FIXTURE            invented postings
grants_gov      GRANT_FUNDING    OUT_OF_SCOPE       healthy, and not client work
owner_entered   OWNER_DECLARED   ACTIVE
sam_gov         CONTRACT_WORK    NEEDS_CREDENTIAL   SAM_GOV_API_KEY unset
```

**One active source.** Suitability asks four questions — permitted,
reachable, enabled, and *carrying work the certified skills are paid for* —
because a source can pass the first three and still be no use. A federal
grants feed answering every request in 200ms is HEALTHY and always will be;
every field on that row is true and the row as a whole is a lie.

Nothing was deleted. A source's determination, its permission grants and
every opportunity it produced are the record of a decision somebody made.
`active_sources()` is the interface, `source_report()` is the audit,
`sources()` still returns all of them.

---

## E. Testing

### Totals

`python3 -m unittest discover -s tests_solvent`, whole repository, after the
K-LEAN repair below:

```
Ran 3649 tests in 339.420s
OK (skipped=2)
```

| | |
|---|---|
| **Total** | **3649** |
| **Passed** | **3647** |
| **Failed** | **0** |
| **Skipped** | **2** |

76 of those are new in this sweep: source audit and routing (17), T2
packaging (10), inference routing (24), routing adversarial (14), three-way
benchmark (7), discovery leak probe (4).

An earlier run of 3645 passed without the leak suite, which had been written
after that run started and therefore was not collected. Its three failures
were real and are reported under Security findings; the 3649 above is the
run that includes them, repaired.

### Mutation

```
31/31 killed  (0 survived, 0 not applied or harness)
```

| battery | mutants | killed |
|---|---|---|
| T2 independent review | 8 | 8 |
| Job Finder | 11 | 11 |
| Customer service | 4 | 4 |
| Owner authentication | 1 | 1 |
| Inference routing (new) | 7 | 7 |
| **Total at the freeze** | **31** | **31** |

A thirty-second mutant was added with the K-LEAN repair and killed
separately: `discovery-failure-reason-unredacted`. **32/32.**

`NOT_APPLIED` is reported as its own outcome and never counted as a kill.

### Unseen final exam — first run

Seed drawn at random **after** the code was frozen, recorded before the run,
executed once. Nothing was tuned in response to it.

```
seed 62552890: 45/45 passed
  csv-cleanup              9 passed, 0 failed
  data-entry               9 passed, 0 failed
  document-processing      9 passed, 0 failed
  report-builder           9 passed, 0 failed
  spreadsheet-automation   9 passed, 0 failed
```

### Repaired retest results

Four defects were repaired during this sweep, each re-tested:

| defect | found by | retest |
|---|---|---|
| `solvent` absent from the packaged modules while the `solvent` console script pointed into it | reading the packaging for blocker 7 | pinned by a test asserting both appear |
| Three benchmark and routing fixtures missing the egress allowlist | executing them | all pass |
| **Credentials leaking into `work_sources.failure_reason` and the audit log** | K-LEAN probe | repaired and re-tested: 4/4 pass, and a mutant removing the redaction is killed |
| `Chunk` had nowhere to carry token usage, so a cloud call's cost could only be estimated from text length | writing the cost accounting | usage is carried and the cost is measured |

### Three-way benchmark

Identical source, requirements and price; three complete, permitted route
configurations; the only difference is the route.

| route | wall | execution | invoiced | op cost | profit |
|---|---|---|---|---|---|
| LOCAL | 0.072 s | 0.047 s | $400.00 | $51.90 | $348.10 |
| CLOUD | 0.083 s | 0.052 s | $400.00 | $51.90 | $348.10 |
| HYBRID | 0.089 s | 0.050 s | $400.00 | $51.90 | $348.10 |

**Identical cost and profit on all three**, including the run where the
cloud route was genuinely configured, permitted, priced and reachable. Zero
tokens on every route.

The reason is the finding: **the five commercial skills call no model.**
`sheetwork` says so in its own docstring ("Nothing is generated, inferred")
and `docwork` says it too. That is not an oversight — it is why T1
verification can exist. A verifier can recompute a sum from the client's
source twice and get the same answer; it cannot recompute a generated
sentence.

Where routing does cost money: one advisory call of 4,000 in / 1,000 out is
**$0.00 local, $2.70 cloud** at the configured rates.

### What the benchmark does not measure

**Live inference latency.** No local model server is running on this machine
and no cloud credential is configured, and this sweep may not download
models or buy credits. Numbers for "how fast is a 7B locally versus a hosted
model" would be invented, so they are absent.

Two tests assert those conditions still hold, so the stated limitation fails
loudly the day it stops being true rather than quietly surviving into a
report.

### Security findings

**One new finding, in existing code, found by probing rather than reading.**

`solvent/discovery.py` used `redact` exactly nowhere. When a source's
response cannot be parsed, the exception text is written verbatim to
`work_sources.failure_reason` — rendered by the Control Centre — and to the
audit log, which is append-only and by construction cannot be redacted
afterwards. An exception raised while reading an HTTP response routinely
carries a slice of that response, and an API that echoes the request back in
its error body puts a credential there.

Proven, not inferred: a source whose `parse` raised with a Stripe-shaped key
in the message put that key, verbatim, into both tables.

The routing layer added this sweep was probed for the same class and is
clean: no key reaches the policy table (only whether one was provisioned),
the route report, or the backend's own error text.

### Performance

| measurement | result |
|---|---|
| `csv-cleanup`, 500 rows (median of 12) | 42 ms |
| `csv-cleanup`, 2,000 rows | 47–52 ms |
| 12 complete governed cycles, 500 rows each | 0.70 s — 59 ms/job |
| Full governed cycle, 2,000 rows | 72–89 ms |

No `time.sleep` or `asyncio.sleep` anywhere in `solvent/`.

---

## F. Final readiness classification

### READY WITH RESTRICTIONS

For a supervised, owner-sourced, fixed-price first job. Not for unattended
operation, and not yet on Windows.

**The evidence for "ready".** The pipeline runs end to end and was executed,
not inspected: fifteen stages from discovery to reconciled profit, 3649
tests passing, 32 of 32 mutants killed, and an unseen exam drawn after the
freeze passing 45/45 first time. A $2,000 job that could not be delivered
two sweeps ago now completes with an independent second opinion that
recomputes the client's figures from their own source and catches a
sabotaged total. Twelve consecutive jobs reconcile in 0.70 seconds with
nothing sleeping anywhere in the business path. With the owner's rates
recorded, a real cycle produces real books: $400.00 invoiced, $51.90 cost,
$348.10 profit, 87.0% margin.

**The evidence for "with restrictions".** Eleven of thirteen blockers are
open, and four of them mean the system cannot presently issue a valid
invoice or state a true margin — no contracting identity, no operating-cost
rates, no minimum fee, no minimum margin. All four are the owner's to enter
and none can be inferred.

Two restrictions deserve naming because they are easy to misread as solved:

**Blocker 7 is resolved in code and unverified on Windows.** The dependency
is declared, the installer probes for it, and readiness reports it. None of
that has been observed on a Windows machine, and the change is specifically
about Windows behaviour. Linux results do not establish Windows correctness.

**Blocker 8 is unchanged; only the honesty improved.** There is still no
source that sells what the five skills do. What changed is that the
interface no longer implies there is: one active source, which is the owner
entering work by hand. That is a real route to a first paid job, and it is a
bridge rather than a product.

### On the AI routing decision

The sweep was asked for an evidence-based choice between local, cloud and
hybrid. The evidence says the choice does not affect the thing it was
expected to affect:

**Delivery cost is identical on all three routes, because the five
commercial skills call no model.** That is a design property worth
defending, not a gap to close — deterministic work is why a verifier can
recompute a client's figures and why T1 means anything. A test now fails if
any deterministic module can reach a model at all.

So the recommendation is **LOCAL**, and the reasoning is short: cloud buys
nothing on the delivery path, costs $2.70 per advisory call at the
configured rates, and sends prompts to a third party. Hybrid is worth
configuring only once advisory volume is high enough that local outages
matter, and its fallback is explicitly flagged so a paid call is never
silent.

That conclusion would change if the advisory surfaces grew — ambiguity
summaries, client drafts, requirement suggestions — or if a future
capability genuinely needed generation. It would not change the rule that
generated text must not reach a deliverable.
