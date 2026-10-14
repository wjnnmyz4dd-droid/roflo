# DeskPilot: what it can find, what it can finish, and the gap between them

The owner's concern for this pass was one sentence: DeskPilot should not
accept Job A merely because it appeared first, and should not take on
more work than it can complete. Everything below is written against
that, and the shortest honest summary is this.

**DeskPilot can currently find nothing automatically, and it can
complete work it has never been paid for.** The capacity machinery
built this pass is sound and tested, and it is sitting in front of a
pipeline with no inflow. The constraint on this business is not
capacity. It is that no marketplace has ever been read, no bid has
ever been sent, and no invoice has ever been paid.

Throughout: *simulated* means a deterministic model on a virtual clock,
*measured* means this system timed itself doing something, and *live*
means a real client paid real money. There is no live anything.

---

## A. How many jobs can DeskPilot consistently find?

**Zero per day, automatically, on every one of the six marketplaces.**
This is not a throughput figure that came out low; it is the absence of
a figure, because discovery has never run.

| Marketplace | Automated discovery | Adapter written | robots.txt finding for DISCOVER |
|---|---|---|---|
| Upwork | no | no | **FORBIDDEN** — `Disallow: /jobs/`, `/*/jobs/search*`, `/api*` |
| Fiverr | no | no | **FORBIDDEN** |
| Freelancer | no | no | UNDETERMINED |
| PeoplePerHour | no | no | UNDETERMINED |
| Guru | no | no | no finding recorded at all |
| Contra | no | no | PERMITTED |

Read on 2026-10-13 and unchanged since. Two platforms forbid automated
reading of their listings in their own robots.txt. Two are undetermined.
Guru has no recorded finding, which the system treats as "nobody has
looked" and refuses, not as "no terms apply". Contra permits it — and
there is no adapter written for Contra, so the one platform that allows
automated discovery is not being read either.

**There is therefore no observed discovery rate for any marketplace, and
this report will not estimate one.** A jobs-per-day number here would be
invented, and an invented inflow figure is the number every other figure
in a business plan is multiplied by.

What does work on all six is manual import: the owner reads listings
themselves and brings them in. Job inflow today is `owner_entered`,
`file_import` and `client_inquiry` — three sources that all require a
person.

A note on what "FORBIDDEN" means here: it is the platform's own
machine-readable statement about crawling, which is a narrower document
than its terms of service. Several activities DeskPilot would eventually
need — authenticated discovery, bidding, messaging, accepting contracts,
delivering through the platform — are recorded as UNDETERMINED on every
platform, because establishing them requires reading each platform's
terms, which is the owner's to do and has not been done.

---

## B. How many jobs can DeskPilot complete?

Three different answers, and conflating them is how this question
usually gets answered wrongly.

**Live: zero.** No job has been completed for a paying client.
`Ledger.real_revenue_cents()` returns 0 and does so by construction —
simulated money carries a `SIMULATED:` prefix and is excluded.

**Measured, on this machine:** the five certified skills are
deterministic and call no model, so they are fast. The throughput
harness records a median `csv-cleanup` execution of **24 ms over a
500-row file**, and twelve complete business cycles — intake through
delivery — in **0.58 s, about 48 ms per job**. That is real measurement,
on this Linux container, against synthetic 500-row files. It is not a
measurement of the Windows VPS, and a real client's file may be far
larger. Only `csv-cleanup` has accumulated measurements; the other four
skills are certified but have no recorded execution history.

**Simulated, against the real capacity gate:** with four execution
workers and a job mix averaging roughly four minutes of work,
**54–60 jobs per virtual hour**. The gate, the ledger and the
reservations in that simulation are the real code; the jobs are
generated.

The gap between 48 ms per job measured and four minutes per job
simulated is deliberate. The measured figure is the skill executing. The
simulated figure assumes jobs that take minutes, because a real client
job is not a 500-row test file, and sizing capacity against the test
harness would be sizing it against the easiest case anyone has tried.

**The binding constraint on completion is not any of these.** It is that
`Finder.bid` refuses to bid on a capability with no measured history,
and a fresh installation has none. DeskPilot cannot bid on work it has
never done. The first jobs must arrive by manual import, be executed,
and be measured, before bidding on that capability is possible at all.

---

## C. How many jobs can it safely accept at the same time?

As many as fit inside the budget the owner sets, and not one more. Six
numbers govern it, all owner-set, all zero until configured, and zero
means "take nothing on" rather than "no limit":

| Setting | What it bounds |
|---|---|
| `max_owed_seconds` | total work promised and not yet delivered |
| `max_concurrent_jobs` | jobs in flight at once, whatever their size |
| `max_execution_workers` | jobs executing at once |
| `max_verification_workers` | jobs being checked at once |
| `max_open_bids` | contingent obligations outstanding |
| `revision_headroom_fraction` | room held for the revisions a job will attract |

Capacity is counted in **seconds of expected work, never in jobs**. A
ten-second CSV tidy and a four-hour report are not interchangeable
units, and a limit counted in jobs lets four hours through a gate meant
for ten seconds.

Accepting a job reserves **the whole job** — execution, the verification
that is not optional, and the revision the client is entitled to ask for
— in one transaction, all or nothing. This is the single most important
property here, and it was not true until this pass: see D.

In the simulated budget (3,600 s owed, 8 jobs, 4 execution workers,
40% revision headroom), peak concurrency never exceeded 4 at any
workload up to 500 offers, and peak utilisation stayed at or below 0.98.

### Limitations worth stating plainly

- **CPU and memory cannot be measured on the target host.** DeskPilot's
  diagnostics read `/proc/meminfo` and `os.getloadavg()`, neither of
  which exists on Windows. So the owner's CPU share is **not enforced by
  sampling the machine** — it is honoured by running no more workers
  than the owner allowed. The share is a declaration, not a measurement,
  and `Capacity.pressure()` reports CPU and memory as `UNMEASURED`
  rather than as healthy. Free disk, database growth and DeskPilot's own
  ledger are measurable on Windows and are used.
- **MT5 is protected by arithmetic, not by observation.** Nothing in the
  capacity module reads, signals, or names a trading process; a CPU
  share above 1.0 is refused as a configuration error. But DeskPilot
  cannot see MT5's load, so if MT5 needs more of the machine, DeskPilot
  will not notice — the owner must set the share conservatively.
- **The budget is a guess until the skills are measured.** Converting
  "how many jobs" into "how many seconds" needs execution history, which
  for four of the five skills does not exist yet.

---

## D. What happens under overload?

**The gate refuses, and says which limit it hit.** At 500 simultaneous
offers in the clean sweep there were 6,047 refusals, every one
`NO_ROOM`, each naming the number it ran into.

**Refused offers are not lost work — they are work never taken.** This
is the distinction the whole pass turns on. Acceptance falls as offers
rise; completion of what was accepted does not.

| Offers | 1 | 5 | 10 | 25 | 50 | 100 | 250 | 500 |
|---|---|---|---|---|---|---|---|---|
| Accepted | 1 | 5 | 10 | 23 | 35 | 67 | 136 | 270 |
| Delivered | 1 | 5 | 10 | 23 | 35 | 67 | 136 | 270 |
| Acceptance rate | 1.00 | 1.00 | 1.00 | 0.92 | 0.70 | 0.67 | 0.54 | 0.54 |
| Max concurrency | 1 | 4 | 4 | 4 | 4 | 4 | 4 | 4 |
| P95 completion (s) | 130 | 654 | 654 | 781 | 725 | 725 | 826 | 819 |

Every accepted job was delivered at every level, under every stress
condition tested: clean, sudden spike, slow execution, delayed
verification, failed submissions, customer revisions, payment delays,
marketplace outage, and restart during execution, bid submission and
contract acceptance. Eleven conditions × eight levels.

**The first workload level at which the system becomes unreliable: none
up to 500 offers, under any condition tested.** The criteria were fixed
in `tools/loadsim_solvent.py` before the first level ran, and they are
about promises kept, not jobs won: a job accepted and not delivered, a
duplicate commitment, a budget exceeded, a deadline missed, an
obligation lost across a restart, a leaked reservation, or concurrency
beyond the limit. That the criteria can actually fire is shown rather
than asserted — an `optimistic_deadlines` flag restores the pre-fix
deadline arithmetic and trips the deadline criterion at level 10.

Specific behaviours measured:

- **Duplicates.** 127 replayed marketplace events across all runs; **0**
  became a second obligation.
- **Restart.** 24 restarts across the three restart conditions; **0**
  obligation-seconds lost.
- **Queue.** Offers that do not fit wait and expire (15 simulated
  minutes). Queue depth peaked at 36–46 in the gradual-arrival runs.
- **Spike — the weak case.** With all 500 offers arriving at once, only
  **17 were accepted and 483 expired unread**. Nothing broke and nothing
  was promised that could not be delivered, but a burst of work is
  mostly wasted: DeskPilot works through what it took and the rest ages
  out. If real demand arrives in bursts, throughput is far below the
  gradual-arrival figures.

---

## E. How does it choose the most profitable jobs?

Two mechanisms, and until this pass only the first existed.

**The gate decides whether there is room.** `Capacity` answers exactly
one question and owns no other decision — it cannot approve a bid, price
work, permit an external effect or reach a marketplace, and there are
tests over the module's parsed identifiers that say so.

**The Finder decides which jobs are worth the room.**
`Finder.value_density` is expected profit per second of capacity a job
would consume. A job worth twice as much that takes three times as long
is the worse job, and comparing prices alone picks it. `Finder.choose`
orders by that density and fills the available room from the top.

This is a **greedy approximation to a knapsack, and deliberately not the
optimum**. There is a test that demonstrates it being beaten by the
exact answer, so that nobody reads it as optimal. The exact answer
requires the whole set of offers in advance; a marketplace never gives
you that — offers arrive one at a time and expire. Choosing well among
the ones visible now is the implementable version of choosing well.

A job with no measured duration or no stated price has **no density
rather than a low one**: it is set aside with a reason, not ranked below
everything. Ties break on opportunity id, so the same offers always
produce the same choice.

Pricing itself is unchanged and sits where it did: a bid floor of
cost ÷ (1 − margin), the owner's minimum margin fraction enforced, and
a refusal to quote where the inputs are not evidence.

---

## F. What is the realistic revenue potential?

`solvent/revenuemodel.py` answers this, and its most useful behaviour is
refusing to.

Every input carries a basis: `OBSERVED`, `SIMULATED`, `ASSUMED` or
`UNKNOWN`. Capacity is knowable — workers, measured job times, the
owner's budget and rates. Demand is not. Three inputs have no observed
value and cannot have one yet:

- **bid win rate** — no bid has ever been submitted, so none has been won
- **collection rate** — no invoice has ever been paid
- **dispute rate** — no job has been sold, so none has been disputed

**With those unknown, the model returns no revenue figure at all.** Not
a conservative one, not a range: `None`, with a line saying which input
is missing and that a number there would be invented.

Supplied with explicit what-ifs the model produces a conditional
statement whose shape is the point. The four demand scenarios are
expressed as multiples of *winnable work against capacity*, so no
scenario has to invent a number of jobs that exist in the world:

- **LOW (0.25×)**, **NORMAL (0.75×)**, **HIGH (1.0×)**, **OVERLOAD (3.0×)**

and the model's structural finding is that **net profit is identical at
HIGH and OVERLOAD**. Past the point where capacity binds, extra demand
earns nothing and is reported as jobs turned away. Revenue here is
capacity-bound, not demand-bound.

The statement separates **contribution profit** (collected revenue less
marketplace fees, payment fees, incremental compute and other direct
per-job costs) from **net profit** (contribution less fixed monthly
overhead — the roughly $98/month VPS, recorded as policy, not assumed).
Unpaid work is reported as `uncollected_cents` and never as revenue, and
fees are taken from what is collected rather than from what is billed.
Every money line is whole cents and the statement reconciles exactly.

**Modelled figures never enter the collected total.** The collected
figure in the model comes from the Ledger and is zero.

The owner's supplied numbers — $40/hour, a $40 minimum project fee, ~$98
a month of VPS, a 30% margin — are configuration the model reads. The
hourly rate is explicitly labelled as what the owner charges, not
evidence of what the market pays.

---

## G. What remains before live operation?

Ordered by what blocks what.

1. **Nothing can be found automatically.** Six marketplaces, no adapter
   for any, two forbidding discovery in robots.txt, one permitting it
   with no code to use it. Until this changes, inflow is manual.
2. **The terms have not been read.** Authenticated discovery, bidding,
   messaging, accepting contracts and delivering through a platform are
   UNDETERMINED everywhere. These are owner decisions on documents only
   the owner can accept, and the system refuses each activity until
   recorded.
3. **Four of the five skills have no measured execution history**, and
   `Finder.bid` refuses to bid without one. Those skills must be run and
   measured — through manual-import jobs — before they can be bid on.
4. **The capacity budget is unset.** Every value defaults to zero, which
   means "take nothing on". Someone must decide how much of a machine
   shared with MT5 DeskPilot may have, and that decision cannot be
   derived from the hardware without claiming the whole VPS.
5. **No payment has ever been received.** Stripe and crypto receipt
   support exist and are tested against fixtures; no real money has
   moved in either direction.
6. **CPU and memory are unmeasurable on the target host.** Either the
   owner accepts that the CPU share is honoured by worker count alone,
   or monitoring the owner must choose and install is added.
7. **Burst demand is mostly wasted** (17 of 500 taken). If inflow turns
   out to be bursty, either the offer window or the worker budget needs
   revisiting — but only with real arrival data, which does not exist.

Nothing in this list is a defect in the capacity work. They are the
conditions under which the capacity work becomes relevant.

---

## H. Testing evidence

**Regression: 4,244 tests, 4,243 passed, 0 failed, 1 skipped.**
4,150 in `tests_solvent` and 94 in `tests`. The five certified skills,
the Job Finder, the Bidder, the six marketplace integrations, Stripe,
crypto receipt support, owner approvals, independent verification,
commercial controls, audit history and Windows compatibility all still
pass. MT5 was not touched and nothing was deployed.

**New this pass: 226 tests** — 38 capacity invariants, 43 owner
scenarios, 23 backpressure, 66 adversarial, 28 revenue model, 16 job
selection, 12 sealed exam.

**Mutation: 94 mutants, 94 killed, 0 survived, 0 not applied** on the
final full run — 69 existing plus 25 added for the capacity gate, the
deadline promise, job selection and the revenue model.

First-run outcomes, reported as they happened rather than as they
ended up: the first full run after the capacity work returned 87/88
killed with one `NOT_APPLIED`, a stale anchor my own edits to
`finder.py` had made ambiguous. `NOT_APPLIED` is not a kill; it was
re-anchored and then killed.

Four mutants survived their first run, and **all four were defects in my
tests rather than false alarms**:

- Two controls were indistinguishable. The worker limit falls back to
  the concurrent-job limit when unset, so both refused at three: a
  mutant ignoring the job limit hid behind the worker check, and one
  making the worker fallback unlimited hid behind the job check.
- A mutant aimed at the wrong half of a guard. `store.transaction()`
  holds an in-process lock *and* issues `BEGIN IMMEDIATE`; the lock
  serialises threads and `BEGIN IMMEDIATE` serialises other processes.
  The threaded test could only ever pin the first.
- The ranking test listed the big job first, and it did not fit in any
  of the test's budgets — so taking offers in arrival order gave the
  same answer as ranking them.

**Sealed unseen exam: six questions, six passed, twice.** Levels
7/13/37/71/149/307 against a development sweep that used
1/5/10/25/50/100/250/500; budgets from ranges the tests do not use;
stress conditions combined in pairs where development tested them
singly. The seed derives from the freeze timestamp rather than the
environment, so the paper cannot be reshuffled until it gives a better
mark.

It was sat twice because the seal worked. After the first sitting,
`Finder.choose` was added — after the exam, not during it — and the
seal broke on the next run because `finder.py` is one of the four
modules it covers. The remedy taken was a new freeze over the finished
code and a second sitting; because the seed follows the freeze, that
was a different paper rather than a re-sit. Both records are kept.

### Defects found and fixed

Four of these would have let DeskPilot promise work it could not
deliver. All were found by testing, not by reading.

1. **A race between the gate and its own ledger.** `room_for` read,
   decided, and `reserve` then wrote, with a window between. Two threads
   both read 500 s owed against a 600 s budget, both found room for
   80 s, and both took it — 660 s owed, and the gate refused neither.
   Fixed with `store.transaction()`; verified with 2 and 8 concurrent
   bidders.
2. **Admission that bought only half the job.** A job admitted with room
   for its execution could finish executing and find no room left to be
   verified — and since execution reservations are held until delivery,
   nothing freed up. In simulation every worker ended up holding room
   for work it was not allowed to finish and the run never terminated.
   `Capacity.admit` now takes execution, verification and revision in
   one transaction. Throughput at 500 offers rose from 103 delivered to
   321.
3. **Deadlines promised from the middle of the distribution.** The
   promise was the median times a safety factor; with runs spreading to
   four times the median that missed deadlines from ten concurrent jobs
   upwards. It is now floored at the measured tail — a 90th percentile
   with ten runs or more, the slowest observed run with fewer.
4. **Deadlines that ignored verification.** A client's deadline is for
   delivery, which is after checking. The promise now includes
   downstream work, and omitting that figure falls back to the owner's
   revision headroom rather than to zero.
5. **Two limits that failed open.** A diagnostics call that raised was
   caught and turned into "no problem", so an owner-set disk floor or
   memory ceiling that could not be checked read as satisfied. The
   unreadable-books path already refused; these did the opposite. Found
   by reading capacity against iFixAi's silent-failure inspection, and
   notable because the change passed 162 existing tests without one of
   them noticing — so it came with five tests and two mutants.
6. **A pressure signal read in the wrong direction.** Free disk was
   scored as a ceiling, so 32 GB free against a 1 GB floor read FULL
   while half a gigabyte read OK — backwards for the one resource that
   is measurable on the target host.
7. **Revenue model: an overload scenario that never overloaded.** At a
   15% win rate, "three times the offers" was half the capacity, so
   modelled profit rose with demand for ever.
8. **Revenue model: a statement a cent out.** Gross, collected and
   uncollected were each rounded independently.

### Unresolved

No known defect in the capacity work is unresolved. The open items are
the seven in section G, none of which is a bug.

One limitation of the testing itself, stated because it bounds what the
structural tests prove: the AST scanner in `fixtures.source_names`
matches identifiers and whole string literals exactly, so it would not
catch a forbidden name embedded inside a longer string. It exists
because four tests this pass failed by grepping a module's text and
finding a forbidden word inside a docstring explaining that the module
does not do that thing — the scanner drops docstrings and carries no
comments, which is the failure mode it was built for.

---

## Advisory reviews

Both advisory, neither able to override owner approvals, commercial
gates, independent verification, grading or promotion decisions. Neither
was given any such authority and nothing below changed a control.

**K-LEAN** (`calinfaja/k-lean@79f3557`) is archived upstream, and its
review path brokers second opinions from other model providers, needing
credentials this session does not have. Its `scan_secrets` tool is local
Python and ran natively over the tree:

```
solvent/        75 files scanned — no potential secrets found
tools/           4 files        — none
tests_solvent/ 153 files        — 11 findings
```

All eleven are deliberate fixtures — `correct-horse-battery-staple`,
`sk_live_ABCDEFGHIJKLMNOP1234`, `watcher-shared-secret-not-a-credential`
— used to check that redaction catches those shapes. No real credential
is in the tree.

**iFixAi** (`ifixai-ai/ifixai@d86ba61`) audits agents whose decisions
come from a model. The capacity gate calls no model and its decision is
arithmetic over a ledger, so most of its taxonomy — hallucination rate,
prompt injection, fact consistency, instruction adherence — is
structurally inapplicable, and saying otherwise would be dressing an
inapplicable tool as a clean bill of health. Four categories do apply
and were used as a reading lens:

- **B16, silent failure** — found defect 5 above, the two limits that
  failed open. The most valuable thing either reviewer produced.
- **B22, decision reproducibility** — now tested: identical ledger state
  gives an identical decision across 25 calls and across a restart.
- **B11, system controllability** — now tested: the budget is adjustable
  at runtime without a redeploy.
- **B04, deterministic override** — now tested: no model can reach the
  admission decision.

---

## What this pass did not do

No live bid was submitted. No contract was accepted. No money moved. No
wallet was created. Simulation stayed enabled. Nothing was deployed to
the VPS and MetaTrader 5 was not touched, read, signalled or named by
any running code. No new agent was created: the Bidder remains part of
the Job Finder, and capacity is a reservation ledger rather than a
thirteenth authority.
