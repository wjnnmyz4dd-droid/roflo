# 90-day adversarial business simulation — prerequisite blocker report

Commit `50fdd1d`. Branch `claude/solvent-architecture-audit-8ot9et`.

**Verdict: PREREQUISITE ABSENT. STOPPED.**

The assignment states: *"Prompt 1 must have certified the
production-path simulation environment. If that certification is
absent, stop and report the blocker. Do not substitute an arithmetic
model."*

That certification is absent. No arithmetic model has been substituted
and no simulation results are reported. This document is the blocker
report the instruction asks for.

The deliverable `docs/deskpilot-90-day-business-simulation.md` has
deliberately **not** been created. Writing a file under that name
containing NOT EXECUTED in most of its 30 sections would misrepresent
itself in a directory listing.

---

## 1. What Prompt 1 actually certified

| Stage | Verdict | What it covers |
|---|---|---|
| Stage 1 — connector repair | PASS | Production opens a negotiation; 33 tests; SIGKILL recovery |
| Stage 2 — connected production workflow | PASS | **One** client, **one** job, discovery → settled invoice |
| Stage 3 — MiroFish | NOT SUITABLE | Declined with concrete blockers |

Stage 2 certified a **single connected run**. It did not certify a
simulation *environment* — something that can run N business days
while preserving customer, contract, job, invoice and payment records
across them. The Stage 2 report said so in its own words, naming a
seam it crossed manually.

Prompt 1's own Stage 3 simulations were the arithmetic model, and the
forensic report at `docs/deskpilot-complete-business-performance.md`
established that they contained no DeskPilot component. Substituting
that model again is exactly what this assignment forbids.

---

## 2. Blocker 1 — no multi-day business simulation environment exists

A repository-wide search for any multi-day business construct returns
**nothing**:

```
grep -rln "simulated_day|business_day|for day in range|advance_day|day_index"
        solvent/ tests_solvent/ tools/
→ (no matches)
```

There is no simulated business calendar, no day loop, no per-day
record, and no mechanism to carry a customer, a contract or a
receivable from one simulated day to the next.

`Runtime` accepts an injected `clock`, but that clock drives **task
cadence** — when `heartbeat` or `business` is next due. It is not a
business calendar. `ChiefOfStaff.next_step(today=...)` accepts a date
string, so a date *can* be threaded in, but nothing threads one, and
no component ages a receivable, expires a proposal or renews anything.

**Tests 1, 2 and 9 cannot run at all.** They are defined entirely in
terms of 30/60/90 simulated days.

---

## 3. Blocker 2 — the shop seizes, so a 90-day run ends on day one

Measured at HEAD (re-confirmed; full trace in the forensic report):

```
JOB CENSUS:                   {'AWAITING_PAYMENT': 5}
OUTSTANDING RESERVATIONS:      5   (EXECUTE, 3600.0 units each)
owed_seconds:                  18000.0
reconcile notes:               []
outstanding after reconcile:   5
real revenue cents:            0
```

The causal chain, every link measured:

```
owner has not recorded contracting details
  → no invoice can be issued
  → no payment can settle
  → the capacity reservation is never released
  → the shop seizes after (3 × pipelines) jobs
```

Two earlier measured runs recorded the same thing independently:
`replenishment` — *"no capacity returned: the shop stays seized"*; and
`accelerated_24h` — 100 jobs offered, **7 delivered, then nothing for
the rest of the day**.

A 90-day simulation against this system would deliver between 6 and 15
jobs in total — on day one — and then record 89 empty days. That is
not a measurement of business performance; it is a measurement of one
unset configuration value.

**This blocker is a configuration entry, not a code defect.** It is the
first recommendation in the forensic report and remains the single
highest-value action available.

---

## 4. Blocker 3 — contracts do not become jobs by themselves

Confirmed current: `TheSeamIsWhereWeSayItIs` and
`ThereIsNoLongTermContract` both pass (5 tests, 6 subtests), which
means the seam they pin is still open.

Nothing in `solvent/` reads `chief_of_staff.engagements` in order to
admit work. An engagement reaching `work_authorised` does not cause a
job to be admitted; the work must separately arrive as a storefront
order. Stage 2 crossed this in one marked place, `the_work_arrives`,
precisely so it would stay visible.

An unattended multi-day simulation cannot cross it. Every simulated
day would need a human — or a test harness — to hand the shop the work
its own contracts had already agreed. That is the harness running the
business, which is the defect this programme spent Stage 1 removing.

---

## 5. Blocker 4 — four domain models the tests require do not exist

| Required by | Model | Status |
|---|---|---|
| Test 7 | Long-term contracts | **Absent.** `Proposal` has no `term_months`, `period`, `recurrence`, `renews_on`, `retainer_cents` or `cancellation_notice_days` |
| Test 4 | Outreach message variants | **Absent.** `Draft` is a passive value object; subject and body are supplied by the caller. Zero variant, personalisation or A/B machinery |
| Test 12 | Competitors | **Absent.** `upworkbid.py` has `competing_proposals` and `competing_bids_cents` as *input* `Figure`s a marketplace would supply. There is no competitor model and no mechanism relating competition to conversion |
| Test 13 | Market conditions | **Absent.** No demand level, surge, or cost-inflation model anywhere |
| Tests 3, 4, 5 | Customer response behaviour | **Absent.** No model decides whether a fictional customer replies, accepts or objects |

Test 4 deserves a specific warning. It asks which outreach strategy
performed best. With no message generator and no response model, I
would be writing both messages *and* deciding which one won. The
"result" would be my own preference laundered through a report. That
is fabrication, and it is the failure mode the Evidence Rule exists to
prevent.

---

## 6. Per-test executability against production today

| Test | Status | Blocker |
|---|---|---|
| 1 Skill configurations | **BLOCKED** for business outcomes | §2. Plumbing already verified: ceiling = 3 × pipelines |
| 2 Time periods | **BLOCKED** | §2, §3 |
| 3 Customer acquisition | **PARTIAL** | Connector opens deals; no outreach send, no response model |
| 4 Customer psychology | **BLOCKED** | §5. Any result would be fabricated |
| 5 Customer service | **PARTIAL** | Personas tested; resolution time and retention need §2 |
| 6 Unrealistic expectations | **EXECUTABLE** | `UnrealisticClients`, 7 tests, already passing |
| 7 Contract types | **BLOCKED** | §5 |
| 8 Queue and standby | **PARTIAL** | Limits measurable; utilisation and waiting time need §2, §3 |
| 9 Financial performance | **BLOCKED** multi-day; measurable per run | §2 |
| 10 Crash and recovery | **EXECUTABLE** | `ABusyShopKilledOutright`: 4 clients, 4 stages, real SIGKILL |
| 11 Recovery timelines | **PARTIAL** | Feasible; needs five distinct injected crashes with traces |
| 12 Competition | **BLOCKED** | §5 |
| 13 Market conditions | **BLOCKED** | §5 |
| 14 Fraud and security | **EXECUTABLE** | `test_acquisition_adversarial` + persona injection coverage |
| 15 Autonomy | **PARTIAL** | Decisions and escalations countable per run; supervision time never measured |
| 16 Economic realism | **EXECUTABLE** as analysis | Largely delivered in the forensic report |
| 17 Independent failure discovery | **EXECUTABLE** | Several already found; see §8 |
| 18 Financial reconciliation | **EXECUTABLE** per run | Both books reconcile; both balance zero |

**5 executable, 5 partial, 8 blocked.** The eight blocked tests include
every one that produces the numbers the final report and the mandatory
comparison table are built from.

---

## 7. The mandatory comparison table, filled honestly

| Metric | 2 skills | 3 skills | 4 skills | 5 skills |
|---|---|---|---|---|
| Customers | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED |
| Jobs accepted | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED |
| Jobs completed | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED |
| Revenue | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED |
| Direct costs | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED |
| Acquisition costs | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED |
| Overhead | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED |
| Net profit | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED |
| Profit per day | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED |
| Profit per skill | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED |
| Customer complaints | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED |
| Refunds | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED |
| **Capacity ceiling** | **6** | **9** | **12** | **15** |
| Capacity utilisation | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED |
| Owner interventions | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED |
| Crashes recovered | NOT EXECUTED per config | — | — | — |

The one real row is MEASURED at HEAD this assignment:
`committed_ceiling = 3 × pipelines`, per-pipeline limit 3.

---

## 8. Failure modes found while checking the prerequisite

Test 17 is mandatory and does not depend on the simulation
environment, so it was performed.

### 8.1 Two capacity controls disagree about the same job

**Evidence.** `qualification.OCCUPIES_CAPACITY` lists seven states and
**excludes** `AWAITING_PAYMENT`. But a `Capacity` *reservation* for
that job is still outstanding, and `_reconcile_capacity` releases only
terminal or COMPLETE jobs, so the reservation persists. Measured: five
awaiting-payment jobs, five reservations, 18,000 owed seconds, while
qualification's committed count would not count them.

**Severity.** MEDIUM. **Business impact:** the shop-wide job count and
the seconds budget give different answers about how full the shop is;
one of them will admit work the other thinks there is no room for.

**Reproduction.** Drive five orders through the production path, then
compare `capacity.outstanding()` against
`qualification.OCCUPIES_CAPACITY` membership.

**Recommended fix.** None yet — this needs an owner decision about
whether an unpaid delivered job occupies the shop. Both answers are
defensible and the code currently holds both.

### 8.2 A quoted total is labelled as invoiced revenue

**Evidence.** `busy-business-load-and-recovery.json` reports
`invoiced_cents: 3480000`. The same file's `invoice_control` section
records every delivered job stopping at the invoice step, and states:
*"No invoice was issued in ANY run... Gross figures above are quoted
amounts, not billed revenue."*

**Severity.** MEDIUM. **Business impact:** $34,800 reads as billed
revenue in a field named `invoiced_cents` when nothing was billed. Any
later reader summing that field overstates revenue by its entirety.

**Recommended fix.** Rename the field to `quoted_cents` in the
evidence writer. Not done here: this assignment is not authorised to
change tooling, and the file is a historical record.

### 8.3 The simulation defaulted an input production refuses to default

Carried forward from the forensic report because it is the governing
methodological failure: `revenuemodel.py` holds `bid_win_rate` as
UNKNOWN — *"no bid has ever been submitted"* — and returns `None`
rather than invent one. The arithmetic model defaulted it to 0.45.
**Severity: HIGH** for any conclusion drawn from that model.

---

## 9. Minimal path to open the gate

Ordered by dependency, not ambition. Items 1 and 2 are the gate; 3–5
are what the assignment additionally requires.

| # | Action | Kind | Unblocks |
|---|---|---|---|
| 1 | Record the owner contracting details | **Configuration** | §3. Invoices issue, payments settle, capacity returns |
| 2 | Re-measure 24-hour throughput after (1) | Measurement | Establishes the real jobs/day ceiling |
| 3 | Join authorised engagement → job admission | **Code**, needs a pricing decision first | §4. Unattended multi-day operation |
| 4 | Build a business-day simulation harness | **New component** | §2. Tests 1, 2, 9 |
| 5 | Add contract-term, competitor and demand models | **New domain models** | §5. Tests 4, 7, 12, 13 |

Item 1 is a settings entry and costs nothing. Item 3 requires the
owner to decide which price governs when a negotiated figure and a
published offer disagree — a product decision, not an engineering one.
Items 4 and 5 are substantial new work and are explicitly *not*
proposed here, because this assignment forbids introducing speculative
architecture and I have no authorisation to build them.

---

## 10. MiroFish

**Not integrated. Not run. No runtime participation.**

Unchanged from the Stage 3 evaluation: AGPL-3.0;
`requires-python ">=3.11,<3.13"` excludes the interpreters DeskPilot
ships on; mandatory Zep Cloud and an LLM endpoint with no offline
path; no seeding anywhere and sampling temperatures of 0.3–0.5. And
DeskPilot's own `egress.py` denies `socket.connect` process-wide and
irreversibly, so it cannot run in the same interpreter as the
production components.

---

## 11. Advisory review

| Reviewer | Availability | Mode | Applied? |
|---|---|---|---|
| iFixAi 4.3.0 | Verified natively importable | Trajectory audit | **No** — nothing was executed to audit |
| K-LEAN 1.0.0b13 | Verified unchanged; 8 async entry points over `httpx`, no offline path | SOURCE-DERIVED | **No** |

Neither was run, and no advisory verdict is claimed. iFixAi audits
recorded action trajectories; this assignment produced no trajectory
because it did not execute. Running either on a blocker report would
be theatre.

---

## 12. The final business questions

All eighteen are answered the same way, and honestly:

> **NOT EXECUTED.** No simulation ran, because the environment it
> requires does not exist.

The questions that *can* be answered from existing evidence — customers
acquired (0), real revenue ($0.00), which skills are most reliable
(report-builder, document-processing), what caused the largest losses
(acquisition charged per attempt against a $98 gross margin), and what
would most improve profitability (record the contracting details) —
are already answered with full evidence in
`docs/deskpilot-complete-business-performance.md`. They are not
repeated here, and none of them came from a 90-day simulation.

---

## Summary

The simulation was not run and no results are reported. Four
structural blockers stand between the current system and the
assignment as written: no multi-day environment, a shop that seizes
after a dozen jobs, contracts that do not become jobs, and four
missing domain models.

The cheapest of the four — recording the owner contracting details — is
a configuration entry, and it is the one that would turn a 90-day
simulation from a measurement of an unset setting into a measurement
of a business.
