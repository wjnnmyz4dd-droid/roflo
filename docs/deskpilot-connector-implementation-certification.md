# DeskPilot — connector repair, connected certification, and simulation

Three stages, in order, each gated on the one before. Branch
`claude/solvent-architecture-audit-8ot9et`.

**The headline.** The connection the audit named as the single blocker
is built, driven by the real scheduler, and proved by a test that is
structurally prevented from opening the deal itself. Building it
exposed two further defects in code already called repaired, both
fixed. The business simulations say something the software cannot fix:
at the audit's own unit economics the model reaches **$32.24 a day**,
not $200, and the lever that closes the gap is price, not effort.

---

## Stage 1 — the connector

### Mandatory evidence

| | |
|---|---|
| Production entry point | `Runtime._tick_dealmaking`, `solvent/runtime.py` |
| Connector function | `dealmaking.open_ready` → `dealmaking.open_negotiation` |
| New module | `solvent/dealmaking.py` |
| Test file | `tests_solvent/test_dealmaking.py` |
| Scheduler task | `dealmaking`, eleventh, between `supervision` and `business` |
| Cadence | `DEFAULT_INTERVALS["dealmaking"] = 600.0` |

### What was actually wrong

Nothing in `solvent/` constructed a `Negotiation`. The only call site
rehydrated one that already existed; every negotiation in the
repository had been opened by a line of test code. The parts all
existed — the Finder found work, the Qualification judged it, the
Chief of Staff could carry a deal from first contact to signed
contract, and `negotiation.py` held the arithmetic. Nothing joined the
halves.

### The repair

`solvent/dealmaking.py` is the join and nothing else. It decides no
price, holds no limit and owns no table. Each gate is some existing
authority's answer, asked in the owner's order:

```
ELIGIBILITY   CRM standing · engagement book · negotiation book
              · Suppressions · CapabilityRegistry.may_deploy
              · operating mode
CAPACITY      Capacity.room_for(seconds, kind=EXECUTE, capability=…)
PROFITABILITY costs_for(policy) → floor_cents → owner's minimum fee
OPEN          Negotiation(…) through the existing engine
```

Ten refusal reasons, each named after the thing that refused, because
"not now" does not tell an owner whether to add capacity or fix a
price.

**It sends nothing.** A qualified opportunity authorises a *price*,
never a *message*. That is why the tick is not gated on
`egress.simulation_only` the way the business cycle is: that gate
exists because the business cycle commits money, and gating
price-setting on simulation would stop it the day the owner went live.

**The owner is still in charge.** The tick requires health, the
owner's `operations.continuous_enabled` opt-in, and the same business
lease the business cycle takes — so a second process cannot open a
second set of deals against one database. Off by default.

### Proof

The test must not be the thing that opens the deal, so the file is
parsed and two guards fail it if anything in it ever constructs a
`Negotiation` or assigns into the negotiation book. The deal is opened
by `Runtime.run_due()` — the same scheduler `serve()` runs, with its
real cadences, real health gate, real lease and real task list. Only
the clock and the external world are substituted.

| Property | Evidence |
|---|---|
| Scheduler opens it | `TheSchedulerOpensTheDeal`, 5 tests |
| Repeat ticks open one deal | `ItOpensNothingTwice`, 4 tests |
| Survives close and reopen | `ItSurvivesBeingClosedAndReopened`, 2 tests |
| Survives SIGKILL | `KilledWithADealOpen`, returncode **-9**, restored whole, not re-opened |
| Every gate refuses | `TheGatesAreTheExistingAuthorities`, 10 tests |
| Sends nothing | `ItSendsNothing`, 2 tests |
| Concurrency | `OneExecutorOnly` — second runtime blocked by the lease |
| Owner control | `TheOwnerIsStillInCharge`, 4 tests |

The SIGKILL case was verified **non-vacuous**: breaking the book
assignment so the deal is held in memory but never persisted turns it
red with `KeyError: 'p1'`.

### Defect found and fixed: a restart quietly discounted the job

`Negotiation.as_dict` wrote its cost lines out field by field. The two
the earlier audit added — `acquisition_cents` and `supervision_cents`
— were not among them.

That is not a missing figure in a report. `floor` is **recomputed**
from the restored costs rather than restored, deliberately, so that no
stored number can be moved by an edit or a migration. Two cost lines
that did not survive the round trip meant the recomputed floor came
back **lower** than the one the deal was opened under. A crash
discounted the job by exactly the $55 the audit was about, and the
recovered deal looked perfectly healthy.

Both the cost and policy dicts are now derived from the dataclasses,
which removes the class of defect rather than the instance.
`EveryCostFieldSurvivesARestart` holds the round trip against the type
and pins the mechanism, so a hand-written list cannot come back.

Nothing before this could have found it: no test had ever opened a
deal with those two lines populated and then reopened the database.
That is the third time a hand-written field list has fallen behind
this type.

### Defect found and fixed: pricing a client who never replied

Found by the iFixAi export in Stage 3, which drove the gate directly
rather than through `candidates` and opened a deal with a prospect at
`CONTACTED`. `candidates` filters on `REPLIED`, so the scheduler never
asked — which is exactly what made the hole invisible.

Nothing is sent, so no client is bothered; but the book then holds a
live negotiation with somebody who never answered, and from then on
the coordinator reports a conversation that is not happening. Gated
now on `dealmaking.ENGAGED`, with `Refused.NOT_ENGAGED`.

That is the **third guard in this module that the only caller could
not reach** — duplicate, opt-out and now engagement. Each is tested
directly and carries a mutant.

**STAGE 1: PASS**

---

## Stage 2 — the connected lifecycle

`tests_solvent/test_connected_lifecycle.py` — 20 tests, 17 subtests.

Every DeskPilot component is the production one; what is simulated is
the customer, their replies and their money.

### The boxes are proved entered, not inferred

A job reaching PAID is equally consistent with the Governor having
been asked and with its having been skipped. The audit log is the
difference — each authority writes under its own name. One connected
run names:

```
dealmaking · discovery · qualification · governor · gate
capability · capacity · orchestrator · ledger
```

and carries `negotiation.opened`, `governor.decision`, `gate.allow`,
`verification.recorded`, `delivery.completed`.

### The twelve verification points

| # | Point | Result |
|---|---|---|
| 1 | Complete workflow execution | PASS |
| 2 | Profitability-floor enforcement | PASS — holds through every concession |
| 3 | Acquisition-cost accounting | PASS — quoted in the floor and spent in the Ledger |
| 4 | Owner-supervision accounting | PASS — same |
| 5 | Duplicate prevention | PASS |
| 6 | Contract state | PASS |
| 7 | Invoice state | PASS |
| 8 | Simulated payment state | PASS |
| 9 | Independent verification | PASS — `verification.recorded` present |
| 10 | Accurate final profit | PASS — against the independent oracle |
| 11 | Recovery after interruption | PASS — five points, incl. a cleared deposit |
| 12 | Customer communication state | PASS — CRM advances, nothing sent |

### The seam, named rather than papered over

Nothing in `solvent/` reads `chief_of_staff.engagements` in order to
admit work: `dealmaking` reads it only as a gate, `chiefofstaff` only
to advise. **An engagement reaching `work_authorised` does not cause a
job to be admitted** — the work still has to arrive as a storefront
order.

The existing certification has the same seam and crosses it the same
way: Phase 9's `TheWholeChain` asserts ADMIT_WORK and then places the
order itself. These tests cross it in exactly one marked place,
`the_work_arrives`, so the one manual step in an otherwise unattended
chain is visible instead of buried.

**Reported, not repaired.** Turning an agreed price into a work order
needs a product decision about which price wins — the negotiated one,
or the published offer the order was placed against. Inventing an
answer would be a second pricing authority, which rule 5 forbids.

`TheSeamIsWhereWeSayItIs` pins it as an assertion: if a production
module ever reads the engagement book, or an authorised engagement
ever admits a job on its own, those tests fail and say so.

**STAGE 2: PASS**

---

## Stage 3 — MiroFish, and the simulations

### MiroFish: NOT SUITABLE

Repository inspected directly at `github.com/666ghj/MiroFish`, cloned
and read. Not run, and no native result is claimed.

| Check | Finding |
|---|---|
| Purpose | Simulates **public-opinion dynamics on social networks** from a seed document. Not a client negotiating a price for a CSV cleanup |
| Licence | **AGPL-3.0**, in both `LICENSE` and `pyproject.toml`. Network use triggers source disclosure |
| Python | `requires-python = ">=3.11,<3.13"` — **excludes 3.13 and 3.14**, which DeskPilot's installer serves and CI runs |
| Mandatory services | An OpenAI-format LLM endpoint **and** Zep Cloud. `zep_tools.py` raises `ValueError("ZEP_API_KEY 未配置")`; there is no offline path |
| Determinism | **No seeding anywhere** in `backend/app`; sampling temperatures 0.3–0.5 |
| Cost | The repository's own `.env.example` warns consumption is high and advises fewer than 40 rounds |

The decisive blocker is DeskPilot's own: `solvent/egress.py` installs a
PEP 578 audit hook denying `socket.connect` process-wide, and it is
irreversible for the interpreter. MiroFish cannot run in the same
process as DeskPilot's production components, and its mandatory remote
services cannot be reached from one.

Independently of that, the assignment requires reproducible seeds and
deterministic grading. A tool with no seeding and non-zero temperature
cannot provide either, and its own repository carries no accuracy
figures.

**Used instead:** the existing harness, with a purpose-built
independent oracle. No MiroFish integration is claimed.

### The independent oracle

`Oracle` in `tests_solvent/test_business_operation_simulations.py`
recomputes money from the business rules and shares no code with
`metrics` or `Ledger`. `TheOracleIsIndependent` parses its own source
with `ast` and fails if it references any DeskPilot arithmetic — an
oracle that calls the code it grades agrees with it by construction.

`TheOracleAgreesWithTheLedger` then runs both books over the same
events and compares.

### Areas covered, and what was deliberately not repeated

| Area | Where |
|---|---|
| A acquisition | existing `test_business_matrix` (90 scenarios) — **not repeated** |
| B customer service | existing `test_customer_service_personas` — plus 4 new tone tests |
| C unrealistic expectations | **new**, `UnrealisticClients`, 7 tests |
| D five skills | existing matrix — **not repeated** |
| E skill configurations | **new**, `SkillConfigurations` — 2/3/4/5, 5→2, 2→5 |
| F queue and standby | **new**, `QueueAndStandby`, 4 tests |
| G long-term contracts | **new finding — no model exists** |
| H financial simulation | **new**, independent oracle |
| I payment pathology | existing `test_payments` (overpay, partial, refund, dispute, replay, duplicate) — **not repeated** |
| J crash recovery | **new**, `ABusyShopKilledOutright` — one SIGKILL, four clients, four stages |
| K comms recovery | CRM state verified across restart |
| L fraud and security | existing `test_acquisition_adversarial` — **not repeated** |
| M capacity stop rules | **new**, `StopRules`, 3 tests |
| N dependency failure | **new**, `DependencyFailure`, 3 tests |
| O 30/60/90 days | **new**, logical clock, seeded |

### G — long-term contracts do not exist

Weekly, monthly, three-, six- and twelve-month work has **no model**.
An `Engagement` is one proposal, one price, one delivery, one invoice:
no term, no period, no renewal, no cancellation notice.
`Step.IDENTIFY_RECURRING` is the nearest thing and it is advice to a
person — *"paid; worth asking what else they need"*.

Building one would be a new commercial primitive with its own pricing,
its own capacity reservation over a period and its own cancellation
terms. That is a feature, not a wiring repair.
`ThereIsNoLongTermContract` pins the absence so that adding the model
is a visible change.

### J — one full-system termination, four clients

A SIGKILL against a shop holding a live negotiation, a cleared
deposit, an open invoice and a refused lowball. All four come back;
none comes back twice; the deposit is not requested again; the audit
chain verifies.

### O — 30, 60 and 90 days

Logical clock, fixed seed, no wall time. Base case is the audit's own
worked example: a $200 job costing $102 directly, 45% win rate, four
attempts a day, **acquisition charged per attempt** at $30, $10/day
overhead.

| Days | Net profit | Per day | Days ≥ $200 | % |
|---:|---:|---:|---:|---:|
| 30 | $1,686.00 | $56.20 | 3 | 10.0% |
| 60 | $2,686.00 | $44.77 | 4 | 6.7% |
| 90 | $2,902.00 | **$32.24** | 4 | **4.4%** |

**The target is missed by roughly six times.** The sensitivity says
which lever matters:

| Change from base | Per day | Days ≥ $200 |
|---|---:|---:|
| base | $32.24 | 4.4% |
| acquisition $30 → $15 | $92.24 | 18.9% |
| acquisition $30 → $5 | $132.24 | 18.9% |
| acquisition → $0 | still under target | — |
| win rate 45% → 60% | $93.22 | 10.0% |
| win rate 45% → 75% | $156.38 | 24.4% |
| win rate 45% → 90% | $215.18 | 60.0% |
| 4 → 8 attempts/day | $83.20 | 17.8% |
| 4 → 12 attempts/day | $136.33 | 47.8% |
| **price $200 → $300** | **$197.80** | 53.3% |
| **price $200 → $500** | **$528.91** | 88.9% |

**Price is the dominant lever.** Driving acquisition cost to zero does
not reach the target; a 90% win rate is implausible; a $300 job very
nearly does it on its own and a $500 job clears it comfortably.
Charging acquisition per attempt rather than per win is worth $6,330
over ninety days — the single line that decides whether this works.

These are simulations. No prospect here exists, no message was sent,
no money moved. **This is not evidence of earnings.**

**STAGE 3: MiroFish NOT SUITABLE; simulations COMPLETE**

---

## Advisory reviews

Both reviewers are advisory. Neither may override DeskPilot's pricing,
capacity, approval, delivery or verification, and neither was given an
object it could call.

| Reviewer | Commit | Version | Mode |
|---|---|---|---|
| iFixAi | `92bb5d0a950e5da6ae94bbcddd95824b455058ba` | 4.3.0, Apache-2.0 | **NATIVE EXECUTION** |
| K-LEAN | `79f355756f27edc115bb87d956d756429ad3315a` | 1.0.0b13, Apache-2.0 | **SOURCE-DERIVED** |

K-LEAN re-checked rather than assumed: unchanged at the same commit,
and every review entry point in `src/klean/reviews.py` is still `async`
over `httpx.AsyncClient` against a completions endpoint. No offline or
dry-run path exists, so no native run is claimed.

### iFixAi, natively, on the connector

Two stages, because the two cannot share an interpreter:
`tools/export_dealmaking_trajectory.py` runs the connector and writes a
JSON projection; `tools/audit_dealmaking_trajectory.py` runs in
iFixAi's own venv and imports no DeskPilot module.

```
as-recorded            CLEAR
unknown-actor          BLOCK          caught
scope-escalation       BLOCK          caught
budget-overrun         BLOCK          caught
untrusted-collector    INCONCLUSIVE   caught
missing-trace          INCONCLUSIVE   caught

deskpilot_decision CLEAR   controls_injected 5   controls_caught 5
auditor_can_fail True
```

A clean verdict is worth nothing unless the auditor can produce a
dirty one, so five corruptions of the same trajectory were each
required to be caught, and were.

**One correction, mine.** The first run returned BLOCK with
`DELEGATION_ISSUER_PERMISSION` — "root issuer lacks direct permission
for the delegated scope". The policy granted the permission to
`dealmaking` and not to `owner`, and an owner cannot delegate authority
the policy never says they have. iFixAi was right and the translation
was wrong. **Not a DeskPilot finding and not reported as one.** It is
the same mistake the previous round of this review made in a different
guise.

**One genuine finding.** The export drove the gate directly rather
than through `candidates` and opened a deal with a prospect who had
never replied. Fixed, tested and mutated — see Stage 1.

### Confirmed DeskPilot defects from either reviewer

One, above. iFixAi's own stated limits are inherited rather than waved
away: actor identity, collector identity, capture completeness and
costs are caller declarations, not authenticated facts, and a passive
audit of a record DeskPilot wrote establishes internal consistency
with the permission model it claims — not that the world agreed.

---

## Final certification

| | |
|---|---|
| STAGE 1 — connector repair | **PASS** |
| STAGE 2 — connected production workflow | **PASS** |
| STAGE 3 — MiroFish evaluation | **NOT SUITABLE** (concrete blockers above) |
| Customer acquisition | **PASS** (simulated) |
| Customer service | **PASS** |
| Unrealistic customers | **PASS** |
| Two-skill configuration | **PASS** |
| Three-skill configuration | **PASS** |
| Four-skill configuration | **PASS** |
| Five-skill configuration | **PASS** |
| Queue and standby | **PASS** |
| Long-term contracts | **BLOCKED — no model exists** |
| Financial integrity | **PASS** (independent oracle agrees) |
| Payment reconciliation | **PASS** (existing coverage, not repeated) |
| Crash recovery | **PASS** |
| Security | **PASS** (existing coverage, not repeated) |
| 30-day simulation | **COMPLETE** |
| 60-day simulation | **COMPLETE** |
| 90-day simulation | **COMPLETE** |
| Simulated net profit | **$2,902 over 90 days = $32.24/day; 4.4% of days ≥ $200** |
| Real-world profitability | **UNPROVEN until field testing** |
| Windows | **PASS** — run 41 on `8b4a732`, four jobs green, after a regression of mine |
| Live pilot | **NOT READY** |

### Why LIVE PILOT is still NOT READY

The connector is no longer the blocker. What remains:

1. The **engagement → job admission** seam is manual.
2. **No long-term contract model**, so no retainer revenue.
3. The unit economics as modelled reach **$32/day, not $200**, and no
   software change alters that — it is a pricing decision.
4. **No real customer has ever existed.** Every classification above
   that says PASS is a statement about a simulation.

### Verification totals

| | |
|---|---|
| Regression | **5,612 passed, 0 failed, 52 skipped, 11,055 subtests** |
| Mutation, solvent | **245/245 killed, 0 survived, 0 not applied** |
| Mutation, installer | **203/203 killed, 0 survived** |
| New tests this assignment | 99 (`test_dealmaking` 33, `test_connected_lifecycle` 20, `test_business_operation_simulations` 46) |
| New mutants | 11, all killed |
| iFixAi | CLEAR, 5/5 controls caught |

Two of the new mutants exist because the suite did not catch a defect
until one was written for it: `dealmaking-opens-a-deal-below-the-floor`
survived its first run — the test used a budget that also tripped the
owner's minimum fee, so deleting the floor check left it green,
because both gates return `UNPROFITABLE` and the test read only the
refusal. The floor and fee gates are now tested separately.

### Remaining defects

| # | Defect | Severity | Code change? |
|---|---|---|---|
| 1 | Engagement → job admission has no production producer | **HIGH** | yes — needs a pricing decision first |
| 2 | No long-term contract model | **MEDIUM** | yes — a new commercial primitive |
| 3 | Settlement rates still default to zero | MEDIUM | owner config |
| 4 | Contribution still reported as "actual profit" | LOW | rename or subtract overhead |
| 5 | SIGKILL cases skip inside the suite | LOW | CI step |

### Windows — a regression of mine, found by Windows CI

**Run 40 on `e8b77c9` failed both install jobs**, on 3.11 and 3.13
alike, and the cause was this work.

`tools/windows_install_check.py` asserted
`"TASKS 10" in asked.stdout` for the installed scheduler. The
connector makes eleven tasks, so a correct installation reported
`TASKS 11 | COS True | RAN 11` and the check called it a failure:
12/13 required checks passed, exit 1. Every other check passed,
`verify` returned DESKPILOT READY, the restart check was 12/12 and
uninstall kept the records — the installation was fine and the
checker was wrong about it.

This is the same hand-written number going stale that the suite's own
task pins did. Those four failed on the first Linux run after the
connector landed and were updated; this one lives in a tool the Linux
suite does not exercise, so Windows CI found it instead of a local
run. A substring match on a count is doubly wrong anyway:
`"TASKS 10"` also matches `"TASKS 100"`.

Repaired by pinning the property rather than the number. The
subprocess now computes `ALL_RAN` — every task the runtime registered
actually ran — and the check reads that. Verified locally against a
real runtime: `TASKS 11 · COS True · RAN 11 · ALL_RAN True`.
`NoCheckPinsAHandWrittenCount` in `tests_solvent/test_windows_checkers.py`
fails if a literal task count ever reappears in that file.

**Run 41 on `8b4a732`: all four jobs green.** Confirmed, not assumed:

| Job | Result |
|---|---|
| Install on Windows (3.11) | success — including the step that failed, and `DeskPilot-Setup.exe` built |
| Install on Windows (3.13) | success |
| Suite on Windows (3.11) | success |
| Suite on Windows (3.13) | success |

The "failing files on their own" step was skipped on both suites,
which is the step that runs only when something failed. Named
sub-suites on 3.13: five-skill library 46 passed / 149 subtests;
production path 44 passed; business lifecycle and Chief of Staff
certification 119 passed, 1 skipped, 102 subtests.

**The connector is registered on real Windows.** The installed
runtime reports its task list as

```
heartbeat · health · reconciliation · discovery · diagnostics
owner_intents · owner_briefing · supervision · dealmaking
business · chief_of_staff
```

and the business tick correctly refuses with *"continuous operation is
not enabled"*, which is the owner's opt-in holding on Windows exactly
as it does on Linux.

**The three previously named blockers stand**: WAL sidecars are not
re-hardened after a runtime restart, the `.exe` is compiled and never
executed, and cp315 cannot be served.

**No Windows claim here rests on a Linux result.** The PASS above is
recorded against the re-run on the fixed commit; before that re-run
went green the honest status was FAIL, and it is written down that
way rather than quietly overwritten.
