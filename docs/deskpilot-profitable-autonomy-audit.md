# DeskPilot — can it run as a profitable autonomous business?

An evidence-first audit. Nothing here was rebuilt; the question was
what remains unproven. Every figure below was measured during this
audit against commit `723039b` unless it says otherwise.

**The short answer.** The machinery is in much better shape than the
business model. DeskPilot recovers from crashes, refuses duplicate
payments, keeps its authorities separate, survives a coordinator that
throws, and now installs and runs on Windows. What it has never done
is open a negotiation by itself — and that is not a gap in testing, it
is a gap in the code.

---

## Phase 1 — the evidence baseline

| | |
|---|---|
| Commit | `723039b` |
| Branch | `claude/solvent-architecture-audit-8ot9et` |
| Working tree | clean |

The historical claims, checked against artifacts rather than carried
forward:

| Claim | Measured now | Verdict |
|---|---|---|
| 5,383 regression tests passing | **5,499 passed, 0 failed, 50 skipped, 10,926 subtests** (5,549 collected) | superseded upward |
| 234 mutations killed | **234/234 solvent, 203/203 installer**, 0 survived | confirmed, and the installer battery was not in the claim |
| 110 Chief of Staff certification tests | **111 collected, 110 passed, 1 skipped** | accurate — see the skip below |
| Thirteen interruption recovery points | 10 tests, **13 subtests**, all passing | confirmed |
| A genuine process termination test | **exists, and does not run in the suite** | see Defect 3 |
| Five-industry end-to-end simulations | `TheEightClients` A–H, `AcquisitionAcrossTheFive` | confirmed, simulated |
| Persistent CRM and contract recovery | `InterruptedAtEveryStage`, durable `_Book` | confirmed |

### Evidence matrix

| Concern | Existing test | Execution path | Result | Remains unproven |
|---|---|---|---|---|
| Per-job profit arithmetic | `Ledger.actual_profit` | real Ledger | **PROVEN** | — |
| Full cost stack at pricing | `test_full_cost_floor` (new) | real `floor_cents` | **FAILED → repaired** | whether production ever supplies the lines |
| Business-wide loss detection | `business_metrics` | real Ledger + metrics | **PROVEN** | fixed overhead is not subtracted |
| Discovery → negotiation join | none | — | **UNTESTED: no production producer** | the whole upper lifecycle |
| Recovery and duplicates | `InterruptedAtEveryStage` | real close/reopen | **PROVEN** | — |
| Process termination | `KilledMidLifecycle` | real SIGKILL | **PARTIALLY PROVEN** | skipped in the suite |
| Coordinator failure isolation | `test_a_coordinator_that_cannot_be_read…` | real scheduler | **PROVEN** | — |
| Authority separation | `TheArchitectureIsWhatItClaims` | source scan | **PROVEN** | — |
| Skill on/off | `may_deploy`, 6 call sites | real Finder/Application | **PARTIALLY PROVEN** | in-flight jobs on disable |
| Windows | run 35, four jobs | real Windows | **PROVEN** | the `.exe` is unexecuted |
| Real customers | none | — | **UNTESTED by construction** | everything |

---

## Phase 2 — end-to-end profitability

**The cost model exists in two places and they do not agree.**

At pricing time, `negotiation.Costs` carried seven lines: service, AI,
verification, expected revisions, platform fees, payment processing,
risk allowance. At settlement time, `quoting.operating_cost` computes
four: compute, model API, platform fee, payment fee.

Priced against the audit's worked example:

```
a $200 job          what the floor could see
  execution    $35    service_cents            yes
  verification $20    verification_cents       yes
  revisions    $40    expected_revision_cents  yes
  acquisition  $30    --                       NO
  payment fees  $7    payment_processing       yes
  supervision  $25    --                       NO
  ----------------
  true cost   $157    representable: $102
```

Measured, not reasoned:

* the floor came out at **$145.71**
* at $200 it believed contribution was **$98.00**; the truth is **$43.00**
* **overstatement $55.00 — 128% high**
* **a job priced at $146 cleared the owner's authorised profit floor
  and lost $11**

The assignment asked whether DeskPilot would report $165 or $200. It
reports $98 — better than both naive failures, and still wrong by a
third of the cost stack.

**A guard existed and could not fire.**
`test_pricing_and_negotiation.TheFloorIsArithmetic.test_every_cost_line_moves_the_floor`
was written for exactly this risk — its docstring reads *"A fee nobody
counted is the one that eats the margin."* It could not catch this
one, because it mapped each breakdown key to a field through a table
written by hand: it could check that every line **present** moves the
floor, and was blind to a line that was **absent**. It now reads the
fields off the dataclass, so a line added later is covered the moment
it is added. That is the seventh verification control this programme
has found reporting honestly in a place where nothing it could say
would have helped.

**This is Defect 1 and it is repaired.** `Costs` gained
`acquisition_cents` and `supervision_cents`; both reach `total_cents`
and `breakdown()`. `tests_solvent/test_full_cost_floor.py` holds the
whole stack rather than the two fields: every dataclass field must
reach the total and appear in the breakdown, and no price clearing the
floor may lose money. Reverting the repair fails 16 of them.

**Estimated versus observed.** With default policy every settlement
rate is zero, so a delivered job records **zero cost and a 100%
margin**. DeskPilot already knows this and says so — `readiness.py`
reports *"every operating cost is zero, so the Governor's margin floor
compares revenue against nothing and cannot reject a job."* That is an
honest check, and it is narrower than its name: it inspects four keys
and passes while acquisition, verification, revisions and supervision
are all unpriced.

---

## Phase 3 — customer acquisition

Measured on a fresh Solvent: `simulation_only=True`,
`real_revenue_cents=0`. Nothing has ever been sent to anyone.

| Step | Status |
|---|---|
| 1 Discover prospects | SYNTHETIC OPPORTUNITY |
| 2 Research businesses | SYNTHETIC |
| 3 Identify needs | SYNTHETIC |
| 4 Qualify | SIMULATED, real `Qualification` |
| 5 Select skill | SIMULATED, real selection |
| 6 Calculate profitable offers | **no production producer** |
| 7 Personalised outreach | SIMULATED, never sent |
| 8 Suppression and consent | **PROVEN** — survives restart |
| 9 Track responses | SIMULATED |
| 10 Negotiate | **no production producer** |
| 11 Convert to jobs | SIMULATED |

REAL PUBLIC PROSPECT: none. REAL CLIENT RESPONSE: none. REAL PAID
CUSTOMER: none. Customer acquisition is **UNPROVEN**, and no amount of
further simulation will change that classification.

---

## Phase 4 — full system integration, and the missing connection

The lower half is proven as one connected run:
`test_production_path.AllFiveSkillsGoThrough` takes all five skills
through scheduler → Operator → Governor → Action Gate → worker →
independent checker → delivery → invoice with nothing substituted, and
`TheWholeChain` joins the Chief of Staff's ADMIT_WORK to the engine
actually admitting it.

The upper half is not. **Nothing in `solvent/` constructs a
`Negotiation`.**

* the only `Negotiation(...)` call in the package is inside
  `negotiation_from_dict`, which rehydrates one that already existed
* the Chief of Staff exposes `next_step`, `pipeline`, `escalations`
  and `performance` — it *advises on* deals, it does not *open* them
* a negotiation enters the book only by direct assignment to
  `chief_of_staff.negotiations[...]`, and every such assignment in the
  repository is in a test
* the CLI has no negotiation command; the scheduler never mentions
  one; the control centre only displays counts

**The exact missing connection is QUALIFICATION → NEGOTIATION.** The
Finder produces qualified opportunities and the Chief of Staff can
carry a deal from first contact to signed contract, but the step that
turns the first into the second is performed by the test harness.

This reframes Defect 1: the cost-floor repair is correct and in the
right place, and it is **latent** until something in production opens
a deal.

---

## Phase 5 — autonomous decision making

`ItCoordinatesTheWholeLifecycle` walks seventeen states in one sequence
and is explicit about its own limits: *"It does not prove the
coordinator performs any of them, because it performs none."* Each step
is carried out by the authority that owns it.

What is proven: the coordinator names the correct next action at every
state, reading the real CRM, the real suppression list and real deal
objects; it recognises insufficient information (13 of 100 contracts in
the timing run stopped on client ambiguity rather than guessing); and
it escalates rather than deciding above the owner's threshold.

What is not: autonomy cannot be measured end to end while step 6 and
step 10 have no production producer. **PARTIAL.**

---

## Phase 6 — financial controls and risk

The assignment's hardest question — every job passes its margin and the
business still loses money — is **answered correctly**. Measured:

```
ten jobs, each $100 collected against $60 cost
  per job            +$40   40% margin, each clears any sane floor
  business-wide     +$400
add $600 of acquisition that belongs to no job
  business-wide     -$200   margin -20%
```

`metrics.business_metrics` uses `Ledger.total_costs_cents()`, which
counts operating spend attached to no job, against **real** collected
revenue only. Simulated money is excluded by name. A delivered,
unpaid job contributes cost and no revenue. **PROVEN.**

Two gaps remain. `fixed_monthly_overhead_cents` is read only by the
advisory `revenuemodel`, so the dashboard figure is contribution, not
net operating profit. And nothing yet records acquisition spend, so
the mechanism that would catch this is correct and unfed.

---

## Phase 7 — quality and delivery

Verification is where DeskPilot's time goes — checker ÷ worker runs
from 1.79× (report-builder) to **9.76×** (spreadsheet-automation), and
38% of measured checker time is the audit write recording the
verification rather than the verification.

Objective correctness is well covered: requirements are committed
before work starts, re-verified by recomputation from the artifact, and
a second opinion is required above C_HIGH. Customer satisfaction is
not the same thing, and the architecture knows it — `ambiguity.py`
stops and asks rather than guessing, which is what produced the 13
ambiguity stops.

**Untested:** a deliverable that satisfies every committed requirement
and still fails what the client meant. That failure mode needs a real
client to exist.

---

## Phase 8 — recovery and duplicate prevention

The specific scenario the assignment names — contract accepted, deposit
cleared, crash, restart — is covered and passing:

* `test_an_acceptance_cannot_be_reused_after_a_restart`: a second
  acceptance raises `FailClosed("already accepted")`
* `test_a_payment_event_replayed_after_a_restart_is_not_counted_twice`
* `test_the_floor_is_recomputed_after_a_restart_not_restored`

Confirmed on Windows too: run 35's restart check is 12/12 across four
separate interpreters with real closes, including that `work_authorised`
is recomputed from restored payments rather than restored.

**PROVEN**, with one qualification in Defect 3.

---

## Phase 9 — agent coordination

| Question | Answer |
|---|---|
| Responsibilities overlap? | No. `TheArchitectureIsWhatItClaims` asserts each authority class is defined exactly once |
| Two components deciding the same thing? | No. Exactly two modules read `margin_floor` — `policy.py` declares it, `governor.py` decides on it |
| Is the Chief of Staff a single point of failure? | **No, measured.** A coordinator whose `pipeline()` raises produces one error line and the other nine scheduled tasks still run |
| Can it exceed its authority? | It holds **zero** references to `capability`; it cannot approve, deploy or retire anything |

No additional management agent is warranted. The existing recovery
mechanism is demonstrably sufficient.

---

## Phase 10 — profit per skill

| skill | worker ms | checker ms | total ms | checker÷worker |
|---|---:|---:|---:|---:|
| report-builder | 7.1 | 12.6 | 51.7 | 1.79× |
| document-processing | 8.8 | 22.8 | 65.0 | 2.58× |
| csv-cleanup | 8.8 | 17.3 | 88.3 | 1.96× |
| data-entry | 9.5 | 42.5 | 89.3 | 4.45× |
| spreadsheet-automation | 7.6 | 73.7 | 146.9 | 9.76× |

Revision rate 0.000, error rate 0.000 across all five.

**These numbers do not support a launch decision, for two reasons.**

First, execution is measured in milliseconds. Compute is not the cost
of this business; acquisition and supervision are, and those are the
two lines nothing measures.

Second, the delivery counts are stale. Of 100 contracts, 47 never
delivered — 13 on client ambiguity and **34 on the CSV T2 blocker**.
That blocker is resolved on an installed Windows DeskPilot, where run
35 confirmed the twelve vendored readers usable for csv, docx,
markdown, md and xlsx. The run that produced the table above was taken
on a host without them, where `independent.available()` still reports
no automated producer.

**Recommendation: PROVISIONAL.** On the evidence available,
**report-builder** and **document-processing** — cheapest total path,
lowest verification multiple, no dependence on the reader that was
blocked. Spreadsheet-automation delivered 20/20 but spends nearly ten
times as long verifying as producing, which is the wrong shape for a
first service. No demand evidence exists for any of them.

The $200/day target implies, at a $150 job, between one and two
delivered jobs per day. Throughput is not the constraint; having a
customer is.

---

## Phase 11 — skill on/off switches (architecture review only)

Mostly already present.

| Desired | Today |
|---|---|
| Independent switch per skill | `CapabilityRegistry` decisions are per name: APPROVED / LIMITED / DENIED, plus skillslab ACTIVE / DEPRECATED / RETIRED |
| Owner controls activation | `decide()` requires an owner identity |
| Disabled skills get no new jobs | `may_deploy` is consulted by the Finder **before bidding** (`finder.py:719, 960`) and by `application.py` before delivering |
| Existing commitments preserved | **UNVERIFIED** — `may_deploy` is checked at bid and delivery time; no test covers disabling a skill with a job in flight |
| Reactivation later | Yes; decisions are append-only records |
| CoS may recommend, not change | **Structurally guaranteed** — zero `capability` references |

`LIMITED` is already the "build but do not sell" switch. The only real
gap is the in-flight question.

---

## Phase 12 — Skeptic Rule

| Claim | Supporting | Contradicting | Confidence | Next decisive test |
|---|---|---|---|---|
| "DeskPilot is autonomous" | 17 coordination states; scheduler ticks ten tasks; correct refusals | **no production code opens a negotiation**; two lifecycle steps have no producer | **LOW for the full cycle, HIGH below qualification** | wire qualification → negotiation and run the cycle unattended |
| "DeskPilot is profitable" | arithmetic correct; portfolio loss detected | floor was blind to 35% of the worked cost stack; every settlement rate zero by default | **UNPROVEN** | price and deliver one job with all six cost lines populated |
| "DeskPilot can acquire clients" | suppression, consent, qualification all real and durable | nothing has ever been sent; steps 6 and 10 absent | **UNPROVEN** | one real outreach, owner-sent, under supervision |
| "DeskPilot can negotiate" | eight client behaviours A–H against production arithmetic | every negotiation in the repository was opened by a test | **SIMULATED** | one negotiation opened by production code |
| "DeskPilot completes work reliably" | 5,499 tests; five skills through the real path; 0.000 error rate | delivery measured under a blocker since resolved | **HIGH** | re-measure on an installed tree |
| "DeskPilot recovers from crashes" | 13 interruption subtests; 12/12 on Windows; real SIGKILL | the SIGKILL case does not run in the suite | **HIGH** | run it in its own interpreter in CI |
| "DeskPilot is ready for deployment" | Windows green; installer tested | no customer, no revenue, no negotiation producer | **NOT READY** | — |

---

## Phase 13 — Autonomous Rule: the ranked gaps

1. **No production producer for negotiations.** Financial impact: total
   — the business cannot start. Likelihood: certain. Smallest fix: one
   function that turns a qualified opportunity into an open
   `Negotiation` with a cost stack and a market band, called from the
   existing scheduler tick. No new component; the Finder, the
   Qualification and the Chief of Staff all already exist.
2. **Acquisition and supervision were unpriced.** Repaired in `Costs`;
   still needs a producer to populate them and policy keys to default
   them.
3. **Settlement rates default to zero.** Every delivered job reads 100%
   margin until the owner records four numbers. Readiness already says
   so; extend its `cost_keys` to the full stack.
4. **The dashboard reports contribution as "actual profit."** Subtract
   `fixed_monthly_overhead_cents` or rename the field.
5. **The SIGKILL test does not run.** Give it its own CI step.

---

## Phase 14 — Top 1% Rule

What a disciplined operator would demand before scaling, and whether
it exists:

| Demand | Status |
|---|---|
| Unit economics with every line | **now representable**, not yet produced |
| Customer acquisition cost per won job | **absent** — no CAC anywhere |
| Pricing discipline | floor is one number, one authority, recomputed not restored |
| Capacity management | eight limits, sealed exam, tenth paper 6/6 |
| Cash collection | invoiced ≠ collected ≠ real, enforced in three places |
| Measurement integrity | **exemplary** — simulated revenue is excluded by name, margin is `None` rather than 0.0 when undefined |
| Retention | `clientrelations` exists; no repeat client has existed |
| Risk-adjusted profitability | `risk_allowance_cents` exists and is unused |

**What could make this lose money even if the software is correct:**
acquisition cost per won job is unmeasured, and on these numbers it is
the largest line. At $30 acquisition against a $43 contribution, a win
rate below ~40% makes the business negative however well it executes.

**What we should stop doing:** measuring execution speed. Worker time
is 7–9 ms across all five skills. It is not the constraint and never
will be.

**What evidence would change the strategy:** one real client paying one
real invoice. Every classification above that says SIMULATED turns on
that single fact.

---

## Phase 16 — Windows status

Separate workstream, current as of run 35 on `c0a429d`:

| | |
|---|---|
| Suite 3.11 | **PASS** — 16,469 cases, 53 skipped, exit 0 |
| Suite 3.13 | **PASS** — 16,473 cases, 53 skipped, exit 0 |
| Install 3.11 / 3.13 | **PASS** — 17 steps 0 failed, 13/13 checks, 12/12 restart |
| Installer | `DeskPilot-Setup.exe` built, 25,077,329 bytes |
| Blockers | WAL sidecars not re-hardened after a runtime restart; the `.exe` is compiled and never executed; cp315 cannot be served |

Full detail in `docs/deskpilot-windows-certification.md`. No Windows
claim here rests on a Linux result.

---

## Phase 17 — final report

**A. Proven.** Per-job profit arithmetic. Business-wide loss detection
including job-less overhead. Crash recovery and duplicate prevention,
on Linux and Windows. Authority separation. Coordinator failure
isolation. Five skills through the real production path. Windows
installation and both suites. Mutation: 437 mutants across two
batteries, 0 survived.

**B. Partially proven.** Autonomous decision quality — correct below
qualification, unexercised above it. Skill on/off — switches exist and
are enforced before bidding; in-flight behaviour untested. Process
termination — real, passing, and not run in the suite.

**C. Not proven.** Customer acquisition. Negotiation by production
code. Any profitability involving real money. Customer satisfaction as
distinct from objective correctness.

**D. Actual architectural defects.**

| # | Defect | Severity | Impact | Code change? |
|---|---|---|---|---|
| 1 | `Costs` had no acquisition or supervision line; a $146 job cleared the floor while losing $11 | **HIGH** | every price authorised on an understated floor | **done** |
| 2 | No production producer for negotiations | **HIGH** | the business cannot acquire a client unaided | yes — one function |
| 3 | SIGKILL recovery test skipped in the suite | MEDIUM | the strongest recovery claim is not in the headline number | CI step only |
| 4 | Settlement rates default to zero | MEDIUM | 100% margin on every delivered job | owner config + wider readiness check |
| 5 | Contribution reported as "actual profit" | LOW | overstates by fixed overhead | rename or subtract |

**E. End-to-end profitability.** UNPROVEN. The arithmetic is now
complete and correct; no production path populates it, and no real
money has moved.

**F. Customer acquisition readiness.** UNPROVEN, by construction.

**G. Autonomous decision quality.** Good where exercised. The
coordinator refuses correctly, escalates rather than guessing, and
recognises missing information.

**H. Financial risk.** The portfolio control works. The largest
unmeasured risk is acquisition cost per won job.

**I. Quality and delivery.** Strong on objective correctness. Untested
on requirement mismatch.

**J. Recovery and duplicate prevention.** The strongest area.

**K. Agent coordination.** Clean. No new agent needed.

**L. Profitability by skill.** Not yet measurable — execution cost is
negligible and the costs that matter are unmeasured.

**M. Top two launch skills.** **PROVISIONAL: report-builder and
document-processing.**

**N. Skill on/off readiness.** Mostly present; `LIMITED` is the switch.

**O. Top 1% gaps.** No CAC. No overhead in the headline figure. No
demand evidence.

**P. Windows.** PASS on both interpreters, three named blockers.

**Q. Five highest-priority next actions.**

1. Wire qualification → negotiation. One function, existing parts.
2. Populate acquisition and supervision on every quote; add policy
   defaults.
3. Record acquisition spend against the business, won or lost, so CAC
   becomes measurable.
4. Have the owner record the four settlement rates, and widen the
   readiness check to the full stack.
5. Run one supervised job end to end with every cost line populated,
   and compare quoted contribution against settled contribution.

### Final classification

| | |
|---|---|
| ARCHITECTURE | **PARTIAL** — sound, with one missing connection |
| BUSINESS AUTONOMY | **PARTIAL** — proven below qualification, absent above it |
| PROFITABILITY | **UNPROVEN** — arithmetic complete, never exercised with real money |
| CUSTOMER ACQUISITION | **UNPROVEN** |
| WINDOWS | **PASS**, with three named blockers |
| LIVE PILOT | **NOT READY** — action 1 is the blocker |
