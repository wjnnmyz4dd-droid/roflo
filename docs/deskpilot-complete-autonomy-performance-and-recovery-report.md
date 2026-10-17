# DeskPilot — complete autonomy, performance and recovery

**Audit, benchmark and simulation. Not a deployment.** No production
file was modified, no dependency installed, no browser automation run
against a live site, no bid submitted, no contract accepted, no client
contacted, no money moved, no storefront published, MT5 untouched.
Every customer, job and payment event below is simulated and labelled.

Date 2026-10-17. Branch `claude/solvent-architecture-audit-8ot9et`,
baseline verified clean at `d3ca090` before starting.
Evidence: `docs/evidence/complete-autonomy-certification-measurements.json`.

**Measured** means I ran it and the number is reproducible. **Judgment**
means I weighed facts. **Unresolved** means evidence could not be
obtained. Estimates are labelled as estimates wherever they appear.

---

## A. Executive verdict

**Is DeskPilot technically ready for autonomous freelance operations?**

**The machine is ready. The business is not, and the gap is not
technical.**

Measured today: 4,744 distinct tests pass with none genuinely skipped;
155 of 155 mutants killed; every one of the seven delivery-path faults
detected and stopped safely with zero wrong deliveries; correctness
held at every job size up to 50,000 rows; peak memory flat at ~34 MB
across 60 consecutive cycles.

Against that, three things stop a real dollar, and none is a defect:

1. **The payment rail cannot move money** (`money_can_move: False`).
2. **The shipped configuration commits to zero jobs**
   (`max_committed_jobs = 0`) — closed by design, pending an owner.
3. **No customer flow exists.** One platform permits automated reading;
   none permits submission.

One technical defect does belong on this list, found by re-testing a
gap this report's first version left open: **the incident recorder is
not wired into the pipeline**. Eight genuine failures produced zero
incidents and a health panel reading `HEALTHY` (section O.2). The
failures were all contained correctly — nothing shipped wrongly — but
nothing recorded them, so an unattended owner would see a calm
dashboard over a system that had just failed eight times. The fix is
wiring, not design (V.0).

The honest verdict: DeskPilot is a correct, well-verified job *factory*
with no orders on the floor, and a working alarm bell that is not
connected to anything. The remaining questions are one small wiring
job and then commercial.

---

## B. Five-skill tool readiness

All five capabilities carry an owner promotion and clear their
certification batteries. From the prior sweep's certification run:
csv-cleanup covers 12 certified checks, spreadsheet-automation 14,
document-processing 14, data-entry 16, report-builder 8.

**Measured here at realistic client scale** — csv-cleanup worker plus
its independent checker, seven-requirement brief, duplicates planted:

| rows | source | execute | verify | total | one-lane jobs/hr | peak RSS |
|---:|---:|---:|---:|---:|---:|---:|
| 1,000 | 51 KB | 0.066 s | 0.059 s | 0.125 s | 28,718 | 20 MB |
| 5,000 | 257 KB | 0.283 s | 0.303 s | 0.586 s | 6,148 | 30 MB |
| 20,000 | 1.0 MB | 1.207 s | 1.323 s | 2.529 s | 1,423 | 65 MB |
| 50,000 | 2.6 MB | 2.875 s | 4.289 s | 7.164 s | 503 | 130 MB |

Every check passed at every size. **The tools are sufficient for the
work advertised.**

What remains missing is unchanged and already documented: **PDF, legacy
`.doc`, RTF and OpenDocument are not read.** PDF is the commonest real
job in this market. That is a capability gap, not a tool defect, and
closing it needs an owner decision on dependencies.

## C. Five-checker readiness

**Yes — every checker can independently validate its worker**, and this
was established the hard way in the prior sweep: four of five pairs
were found sharing a parser or a reader and were separated. Structural
tests now parse imports to enforce it, so a docstring may discuss the
worker while the code cannot reach it.

One measurement worth carrying into the roadmap: **verification costs
as much as execution, and more as jobs grow** — 0.9× at 1,000 rows,
1.07× at 5,000, 1.10× at 20,000, **1.49× at 50,000**. Independent
verification is the dominant cost at scale, which is the right thing to
spend on but should be sized deliberately.

## D. Existing Brain architecture

There is no module called "Brain". What exists is three things, and
only one of them currently decides anything:

1. **`solvent/inference.py`** — routes *advisory* model calls
   (summarising an ambiguity, drafting a reply). Its own docstring is
   explicit: **the five commercial skills call no model at all.**
   Measured today: `LOCAL` **UNCONFIGURED**, `CLOUD` **UNCONFIGURED**,
   `delivery_path_uses_a_model: false`.
2. **`solvent/memory.py`** — BusinessMemory, tier-gated at the write:
   T0 self-report refused outright, economic claims require T3 external
   fact, every lesson must cite evidence.
3. **`qualification._client_factor`** — the only place a learned fact
   changes a decision today.

**This already satisfies the constraint in your opening line.** The
Brain is advisory; deterministic controls hold authority. That is not
a change to make, it is a property to preserve.

## E. Brain performance — does it measurably improve results?

**Partly, narrowly, and less than its surface area suggests.**

Measured: same candidates, same policy, memory populated versus empty.

| client | no memory | with memory | effect |
|---|---:|---:|---|
| paid on time (T3 verified) | 1.00 | **1.20** | +0.2 |
| raised a dispute | 1.00 | **0.40** | −0.6 |
| no history | 1.00 | 1.00 | neutral, correctly |

**2 of 3 decisions changed; a 3× ranking spread where there was none.**
So the Brain does measurably change which job gets taken. An unknown
client is not penalised, which is the right default.

Three honest limits:

- **It cannot improve execution at all.** The skills are deterministic;
  there is nothing for a model to make better, and that is why T1
  verification can exist.
- **The model layer contributes nothing today** — both routes
  unconfigured, so advisory drafting is unavailable.
- **One learned kind is never read in a decision.** `estimate_accuracy`
  is written and surfaced in metrics for display, but no estimator
  consumes it. A learning loop that does not close is not learning.

## F. Brain failure recovery — does it help?

**No, and it should not.** Recovery is deterministic: the orchestrator
stops, `supervision.py` escalates, the next cycle starts clean. Nothing
in the recovery path consults memory or inference, and nothing should —
a non-deterministic component deciding how to recover from a failure is
a second decision-maker in the worst possible place.

The Brain's contribution to resilience is indirect and real: a client
who disputed once is ranked lower, so some incidents are avoided rather
than recovered from.

## G. External Brain comparison

The prior sweeps' answer stands and was re-verified. The useful
capability DeskPilot lacks is **not** a bigger model — it is a closed
learning loop on estimates (section E) and a PDF reader (section B).
Both are small, specific, and neither needs an external brain.

## H. Browser-agent readiness

Settled in the two prior audits and unchanged: **Playwright MCP under a
restricted stdio facade, read-only tools, one worker.** The measured
basis — snapshot-to-file costs 42 response tokens at 120/120 fidelity;
stdio mode opens no listening socket; a facade blocked every unsafe
call; `--allowed-origins` does **not** constrain
`browser_run_code_unsafe`, which upstream has not fixed.

**Safe to integrate under the seven stated conditions; not yet
integrated, and blocked on the dependency decision.**

## I. Job acquisition readiness

Unchanged, and the constraint is contractual rather than technical:

- **Freelancehunt** — automated reading permitted (official API,
  determination 2026-10-15); **submission forbidden by name** (cl.
  3.1.11).
- **PeoplePerHour** — robots.txt permits listing pages and publishes
  job sitemaps; terms unread. **Unresolved.**
- **Contra** — crawl permission covers pages that do not hold the work;
  the board is behind a paid tier.
- **Upwork** — **unresolved**, and unresolvable by machine: every legal
  and developer page returns 403.
- **Fiverr, Freelancer, Guru** — forbidden or refusing machine reads.

**One channel permits discovery. None permits acquisition.**

## J. USD payment rails

**Implemented: the accounting. Missing: the money.**

The Ledger, invoicing, the seven payment states and the monitor all
exist and are tested; `confirmation_problem()` is default-deny with
`rail_event` as the only authoritative source. Measured state of the
shipped configuration: `operational_status: SELECTED`, `decided:
False`, `money_can_move: False`, no rail named.

**Blocker: an owner KYC action, not engineering.** Reported industry
timelines are days.

## K. Cryptocurrency payment rails

More built than the USD side. `tests_solvent/test_crypto_payments.py`
exercises address validation, checksum verification, confirmation
thresholds and the rule that a transaction which can still be replaced
is not money received. Mutants covering those controls are killed —
`an-unconfirmed-transaction-is-revenue`,
`somebody-elses-payment-counts-as-ours`, `a-mistyped-address-is-accepted`,
`an-unchecked-checksum-is-accepted` all die.

**Same blocker: no rail is connected, so `money_can_move` is false for
crypto too.** Conversion is modelled as a decision requiring a named
provider, not an action DeskPilot performs.

## L. Perfect-conditions simulation — maximum theoretical throughput

Two numbers, and the second is the real one.

**Orchestration overhead**, measured over 10 full business cycles:
0.049 s mean per cycle, 10 of 10 delivered. Extrapolated that is
73,469 cycles/hour — **a meaningless figure**, because the fixture is a
toy CSV. I record it only to say it is not the answer.

**Work-bounded throughput**, one lane, from section B: 6,148 jobs/hour
at 5,000 rows; 503 jobs/hour at 50,000 rows.

**Ceiling-bounded throughput, which is what actually binds.** Measured
from the code: `CERTIFIED_SKILL_COUNT = 5`,
`JOBS_PER_AGENT_CEILING = 4`, `GLOBAL_COMMITTED_CEILING = 20`, shipped
`max_concurrent_jobs = 3`.

> **Maximum: 20 committed jobs at any time, 3 running concurrently.**
> At realistic sizes the pipeline clears that in under a minute of CPU.
> Throughput is not the constraint and no amount of optimisation will
> make it one.

## M. Realistic business simulation

Under the stated assumptions — one permitted discovery source, no
submission anywhere, direct hire only, owner approval on jobs over
$200 — the realistic figure is **not throughput-limited at all**.

Realistic sustainable volume equals **whatever the owner's network and
one read-only board actually supply**, which is unmeasured and is
properly a demand question. Stated plainly: I can measure that the
factory handles 20 concurrent commitments comfortably. I cannot measure
demand, and inventing a jobs-per-day figure would be fabrication.

## N. Hourly earnings model — **ESTIMATE, not measurement**

Every figure here is arithmetic over stated assumptions. Nothing has
been earned.

Assumptions: a $180 fixed-price CSV cleanup (the storefront's own
offer shape); 5,000-row job; costs from the measured envelope.

| line | per job | basis |
|---|---:|---|
| gross revenue | $180.00 | assumed price |
| model/API cost | $0.00 | **measured** — skills call no model |
| compute | ~$0.0001 | **measured** 0.586 s CPU |
| platform fee | $0.00 direct / up to 20% via a marketplace | contractual |
| contribution per job | ~$180 direct | derived |

At 3 concurrent lanes and a 0.586 s cycle the *technical* hourly
ceiling is absurd ($1.1M/hr), which is exactly why it is not the
model. **The binding term is job arrival, and it is currently zero.**

> **Honest answer: contribution margin per job is near 100% because
> delivery costs almost nothing. Hourly profit is $0.00 until a
> customer exists and a rail can receive.** Fixed costs (VPS,
> electricity) are unchanged by this work.

## O. Twenty-four-hour mishap simulation

Faults injected at real authority methods via `tests_solvent/faults.py`
— which production code is structurally forbidden to import.

**Eleven seams exist. Eight are invoked on this path; three are not.**

| seam | invoked | result |
|---|---|---|
| intake | yes | detected, stopped safely, nothing delivered |
| requirements | yes | detected, stopped safely |
| artifact_registration | yes | detected, stopped safely |
| verification | yes | detected, stopped safely |
| delivery_gate | yes | detected, stopped safely |
| delivery | yes | detected, stopped safely |
| ledger | yes | detected, stopped safely |
| notification | yes (2 calls) | **delivery continued, failure audited** as `notify.failed` |
| customer_service | 0 on this path | **reachable; tested separately** — see below |
| skills_lab | **0 calls** | **unreachable from production** — no caller exists |
| incident_recording | **0 calls** | **unreachable from production** — see P0 below |

**Seven of seven delivery-path faults produced zero wrong deliveries.**

The notification case is correct behaviour, not a defect: `harness.py`
records `notify.failed` with `decision="CONTINUED"`, because losing a
verified, delivered job to a notifier outage would be worse.

### O.1 — The three seams, resolved (follow-up measurement)

The first pass called these three **untested**, which was honest but
incomplete: "never invoked during a clean cycle" is not a finding,
because a clean cycle has no incident to record. Re-run properly —
fault injected, then the resilience tables and health panel read:

- **customer_service — reachable and now tested.** On the client-message
  path (`ClientRelations.handle`) it is invoked exactly once and
  classified a complaint as `DEFECT`. Under fault it **raises rather
  than swallowing the client's message**, which is the correct
  fail-closed behaviour.
- **skills_lab — unreachable.** `skillslab.record_need` has **zero
  production callers**. Nothing can ever tell the skills lab that a
  capability gap was observed.
- **incident_recording — unreachable, and this is a P0.** Covered next.

### O.2 — P0: the incident recorder is not wired in

`resilience.record_incident` has exactly one production caller:
`harness.py:1620`, inside `supervised()`. **`supervised()` has zero
call sites in `solvent/`** — its only six callers are in
`tests_solvent/test_crash_matrix.py`. The production entry point,
`cli.py:161`, calls `run_business_cycle` bare.

Measured, fresh system per seam:

| | |
|---|---:|
| genuine failures driven through real authority methods | **8** |
| incidents recorded | **0** |
| owner escalations raised | **0** |
| resilience frames written | **0** |
| health panels still reading `HEALTHY` | **8** |

After each real failure `diagnostics.incidents()` returns
**`HEALTHY: no open incidents`** and `diagnostics.breakers()` returns
**`HEALTHY: all closed`**.

This is the failure shape that has recurred throughout this
engagement, in its purest form: **the dashboard reads an incident table
that the pipeline never writes to, so it reports health by
construction rather than by observation.** Unreadable reads as healthy.

**What this does not undermine:** containment still works. All seven
delivery-path faults were detected and stopped safely, nothing was
delivered wrongly, and the audit log does record the failures. It is
the incident, escalation and correlation layer that is unreached — the
system stops correctly but cannot tell anyone it did.

**A correction to my own first pass.** My harness initially scored the
four ancillary seams as "delivered wrongly" and three of them as
undetected. Both were wrong: delivering verified work despite an
ancillary outage is correct, and the three seams are never called in
this cycle, so injecting faults there proved nothing. Counting them as
failures — or as passes — would both have been false.

## P. Recovery report

| measure | value |
|---|---|
| mean detection | **0.035 s** |
| mean time to next successful cycle | **0.193 s** |
| wrong deliveries under fault | **0** |
| incidents **recorded** of 8 real failures | **0 — see O.2** |

Two caveats stated rather than buried. First, **none of these failures
was recorded as an incident or escalated to anyone** (section O.2):
the system contains the failure correctly and then forgets it. Second,
"recovery" here means **the next job succeeds**, not that the failed
job was repaired. The failed job stops
and is escalated; it does not resume. For a job already paid for that
distinction matters, and it is a roadmap item.

## Q. End-to-end certification

The full chain was exercised through `run_business_cycle`, the
production path the repository's own suite uses — not a replacement
harness.

- **148 tests / 391 subtests** across the six end-to-end suites: pass.
- **10 of 10** load cycles delivered, no failures.
- Duplicate detection, duplicate-acceptance prevention, replenishment,
  worker/checker handoffs, deadlines, communication approval, delivery
  correctness, payment-state transitions, restart recovery,
  notification deduplication and profit accounting are each covered by
  named tests in the existing suite, all passing.
- **Notification policy preserved and verified in code:** two immediate
  notifications (job obtained, payment received) and the 06:50 / 16:30
  America/New_York summaries, with urgent exceptions separate and
  deduplicated.

## R. MT5 resource protection

**NOT CERTIFIED. Measured on Linux; the VPS is Windows.** Saying
otherwise from these numbers would be exactly the claim your brief
forbids.

Measured envelope, business pipeline only (no browser):

| level | cycles | CPU/cycle | cores busy | peak RSS | disk cumulative |
|---|---:|---:|---:|---:|---:|
| idle | 0 | — | — | 26.8 MB | 0 |
| moderate | 5 | 1.175 s* | 0.92 | 33.8 MB | 2.3 MB |
| full | 20 | 0.116 s | 0.54 | 33.8 MB | 11.3 MB |
| full + mishaps | 20 | 0.115 s | 0.54 | 33.9 MB | 20.3 MB |
| multi-crash recovery | 15 | 0.106 s | 0.55 | 33.9 MB | 27.0 MB |

\* includes first-run import warmup.

Three findings:

- **Memory is flat at ~34 MB across 60 cycles.** No leak, no growth
  under mishaps or restarts.
- **Never saturates one core** (≤0.92, typically 0.55).
- **Disk grows monotonically — 2.3 → 27.0 MB**, roughly 0.45 MB per
  cycle. Unbounded, this is ~3 GB/year at 20 jobs/day. **No retention
  policy exists.** This is the one resource that does not self-limit.

Add a browser and the picture changes entirely: 740 MB (Playwright MCP
tree) to 1,188 MB (browser-use), measured previously. The browser, not
the pipeline, is the MT5 risk.

**Proposed ceilings and fail-closed behaviour** (recommendation, not
implemented): refuse to start work when free RAM < 1.5 GB; refuse to
start a browser when free RAM < 2 GB; cap the output directory at 1 GB
with oldest-first eviction; browser below-normal priority; one browser
worker, started per window and torn down. `capacity.RESOURCE_SIGNALS`
already carries `disk_free_bytes` and `memory_used_percent`, so the
mechanism exists and only the thresholds and the disk cap are missing.

## S. Adversarial testing

Run with the repository's existing framework. No replacement harness.

- **Full suite: 4,700 passed, 44 skipped** in the main run.
- All 44 skips had one cause — the egress guard prevents subprocess
  launch — and the skip message says to run that suite alone. **I did:
  44 passed, 0 failed.**
- **Total: 4,744 distinct tests passing, 0 genuinely untested.**
  Skipped was not counted as passed.
- Covered: regression, worker-checker independence, hostile files,
  prompt injection, crash, recovery, payment state, capacity,
  resource exhaustion, brain poisoning (T0 refused, economic learning
  requires T3, evidence mandatory).
- **First-run failures this phase: none.** No criteria were changed.

**Untested areas, named rather than omitted:** Windows behaviour
entirely; real-marketplace extraction. The three seams previously
listed here have since been resolved (section O.1): one is reachable
and now tested, two are unreachable from production and that is itself
the finding.

**A gap the green suite did not catch.** 4,744 tests pass, and none of
them notices that the incident recorder is unreachable — because
`test_crash_matrix.py` calls `supervised()` directly, supplying the
very wiring production lacks. A test that constructs the integration
it is meant to verify cannot detect its absence.

## T. Mutation testing

**155 mutants, 155 killed, 0 survived, 0 NOT_APPLIED.** Sources
verified restored clean afterwards. Run sequentially; no commit was
made while sources were altered. NOT_APPLIED was not counted as a kill.

Sealed examinations: the capacity exam's seal is intact and it was
re-sat and passed; the reading exam's first sitting (46/56, all ten
errors faults in the paper) remains on the record in the report that
set it, alongside the corrected 56/56.

## U. Independent advisory reviews

**Neither reviewer produced a finding about DeskPilot. Re-verified
today, both unchanged from the prior sweep.**

- **K-LEAN** (`calinfaja/K-LEAN`, `79f3557`) — **did not run.** The
  repository is archived ("the committee has adjourned") and
  `reviews.py` still hard-codes a LiteLLM proxy at `localhost:4000`
  federating four paid providers. No credentials exist here and none
  will be invented.
- **iFixAi** (`ifixai-ai/iFixAi`, `1559983`) — **executed natively in a
  prior sweep, but against its own mock fixture, not DeskPilot.** It
  audits a live AI assistant endpoint for behavioural failure.
  DeskPilot has no such endpoint: the five skills are deterministic and
  inference is unconfigured (section D). The tool and the subject do
  not meet.

**Findings attributable to either: none.** Not "none found" — none
sought, because neither can see this system. Any criteria I applied by
reading their source would be my own opinion wearing a borrowed name.

## V. Improvement recommendations

Six weaknesses, each with the sixteen fields requested, compressed to a
readable form. **No expensive addition is recommended where existing
code suffices**, and five of six need no new dependency.

### V.0 — P0: The incident recorder is unreachable from production
Problem: eight genuine failures produced zero incidents, zero
escalations, and a health panel reading `HEALTHY` every time. Root
cause: `record_incident` is only called from `supervised()`, and no
production module calls `supervised()` — `cli.py:161` runs the cycle
bare. Component: `resilience` ↔ `harness` ↔ `diagnostics`. Evidence:
section O.2 and `docs/evidence/incident-recording-reachability.json`.
Business impact: the owner cannot learn that anything went wrong from
the system itself; repeated failures never correlate into a pattern,
breakers never trip, and the dashboard actively reassures. For an
unattended business this is the difference between autonomy and
silence. Correction: wrap the cycle's stages in the existing
`supervised()` at the composition layer — the mechanism is already
written and tested, it is simply not called. Reuse: yes, entirely.
New dependency: no. Complexity: small — this is wiring, not design.
Operating cost: none. Performance: negligible. Profitability:
indirect but large; an unnoticed failure mode is an unbilled or
refunded job. Security: positive. Regression risk: medium — incidents
will start firing, so notification volume must be checked against the
owner's two-immediate-notifications rule before enabling. Verify: the
measurement in O.2, re-run — it must show 8 of 8 recorded and the
panel no longer `HEALTHY`. **Priority P0, and ahead of the disk
policy**: a full disk at least announces itself.

### V.1 — P0: Unbounded disk growth
Problem: artifacts and databases accumulate at ~0.45 MB/cycle with no
retention policy. Root cause: nothing owns deletion. Component:
storage/output directories. Evidence: 2.3 → 27.0 MB over 60 cycles
(section R). Business impact: a full disk on a VPS shared with MT5 is
an MT5 outage — the highest-consequence failure in this report.
Correction: a retention policy with a hard cap and oldest-first
eviction, wired to the existing `disk_free_bytes` signal. Reuse: yes,
`capacity.RESOURCE_SIGNALS` already measures it. New dependency: no.
Complexity: small. Operating cost: none. Performance: negligible.
Profitability: protects it. Security: none. Regression risk: low —
deleting the wrong artifact is the hazard, so evict by age and never
delete an artifact referenced by an open job. Verify: a soak test
asserting the cap holds and no referenced artifact is evicted.
**Priority P0.**

### V.2 — P1: The failed job is not resumed
Problem: recovery means "the next job succeeds"; the failed job stops
and is escalated. Root cause: no resume path. Component: orchestrator.
Evidence: section P. Impact: for a job already paid for, a stop is a
refund or a reputation cost. Correction: resume from the last committed
stage using the existing audit trail. Reuse: yes — the audit log
already records every stage. New dependency: no. Complexity: medium.
Cost: none. Performance: neutral. Profitability: protects revenue on
the jobs that matter most. Security: must not let resume skip
verification. Regression risk: medium. Verify: inject a fault
mid-cycle, resume, assert the checker still runs. **P1.**

### V.3 — P2: The skills lab cannot be told anything
Problem: `skillslab.record_need` has zero production callers, so an
observed capability gap (a PDF arriving, say) is never recorded for
the lab to act on. Root cause: the writing side was never wired, same
class of omission as V.0 but far lower consequence. Component:
`skillslab`. Evidence: section O.1. Impact: the system cannot
accumulate evidence about which missing capability costs it the most
work — the input that should drive the PDF decision in V.6. Correction:
call it from the point where an unsupported input is refused. Reuse:
yes, the recorder exists. New dependency: no. Complexity: small. Cost:
none. Performance: none. Profitability: indirect — it would turn V.6
from a judgment call into a measurement. Security: none. Regression
risk: low. Verify: refuse an unsupported file, assert a need is
recorded. **P2.**

*(The customer-service seam, previously grouped here as untested, is
reachable and now tested — section O.1. It behaves correctly.)*

### V.4 — P2: The estimate learning loop does not close
Problem: `estimate_accuracy` is learned and never read by an estimator.
Root cause: no consumer was built. Component: memory ↔ quoting.
Evidence: section E. Impact: estimates never improve, so pricing stays
static. Correction: feed verified estimate error back into the quote.
Reuse: yes, memory and quoting both exist. New dependency: no.
Complexity: medium. Cost: none. Performance: negligible. Profitability:
directly positive if estimates tighten. Security: must stay T3-gated so
a bad outcome cannot poison pricing. Regression risk: medium — pricing
is commercially sensitive. Verify: replay historical jobs and show
error narrowing. **P2.**

### V.5 — P2: Verification is the dominant cost at scale
Problem: verification reaches 1.49× execution at 50,000 rows. Root
cause: checkers re-read and recompute fully. Evidence: section B.
Impact: halves effective throughput on large jobs — currently
irrelevant, since throughput is not the constraint. Correction: none
yet. **Explicitly do not optimise this now**: independence is worth
more than speed, and the constraint is demand. Revisit only if a
measured queue forms. **P2, deferred.**

### V.6 — P3: PDF unreadable
Already costed in the prior audit. Blocked on the dependency decision,
not on work. **P3 until a customer asks for it, then P1.**

## W. Three optimization roadmaps

All figures are **estimates** except where marked measured.

### Plan A — minimum cost
Scope: **V.0 (wire the incident recorder)**, V.1 (disk retention) and
V.3 (skills-lab input). Effort: ~1–2 days. New monthly cost: **$0**.
Throughput change: none. Recovery improvement: large and
disproportionate to the effort — failures become visible, breakers
can trip, and the disk ceiling removes the one failure mode that
could take MT5 down with it. Profit change: none directly; protects
everything. Risks: low, with one to check — enabling incidents means
notifications start firing, which must be reconciled with the owner's
two-immediate-notification rule before it goes live. Prerequisites:
none. **Confidence: high** — all three reuse mechanisms already
written and tested.

### Plan B — balanced
Scope: Plan A plus V.2 (job resume) and V.4 (estimate loop), plus the
browser controller under the seven security conditions. Effort: ~2–3
weeks. New monthly cost: **$0** (Node and Playwright are installed
programs, not subscriptions). Throughput: unchanged — the ceiling is 20
committed jobs and demand is the binding term. Recovery: materially
better, because paid-for jobs resume rather than stop. Profit: positive
if estimates tighten; discovery widens the funnel. Risks: the browser
is the only new attack surface and the only material memory cost
(740 MB measured). Prerequisites: owner decision on external Node
execution. **Confidence: medium-high.**

### Plan C — maximum practical performance
**Not recommended, and the measurements say why.** Scaling beyond this
VPS would mean more workers, more browser capacity, separate
infrastructure. The measured position: one lane already does 6,148
jobs/hour at 5,000 rows, the ceiling is 20 committed jobs, and demand
is zero. Three browser workers were measured to buy **20% throughput
for 2.9× the memory**.

> **Spending on scale before demand exists would buy capacity that is
> already 300× unused.** Revisit when a measured queue persists for
> two weeks. Effort: weeks. Cost: $20–100/month. Confidence that it is
> currently unjustified: **high**.

## X. First-dollar readiness

**What prevents the first real paying assignment, in order:**

1. **No payment rail can receive money.** `money_can_move: False`,
   `decided: False`, no rail named. Owner KYC action.
2. **No customer.** One board permits reading; none permits
   submission; direct hire needs the owner to bring the client.
3. **The system is configured closed.** `max_committed_jobs = 0` —
   correct as a default, and an owner must change it.

**Nothing on that list is an engineering defect**, and nothing in this
audit changed it. The fastest credible route remains: one fixed-price
direct-hire job in a certified skill, through one working rail.

## Y. Owner decisions

1. Stand up one payment rail to the point a `rail_event` is observable.
2. Set `max_committed_jobs` above zero and confirm the concurrency
   limit.
3. Record the Freelancehunt determination and generate the API token.
4. Read Upwork's and PeoplePerHour's terms while signed in.
5. Decide on external Node execution for the browser controller.
6. Approve the resource ceilings in section R.
7. Approve a disk retention policy (P0).
8. Confirm fixed-price-first, deferring hourly work.

## Z. Final conclusion

**What DeskPilot can do today:** take a defined fixed-price job in five
certified skills, execute it deterministically, verify it with a
genuinely independent checker, deliver it correctly, invoice it, and
account for it — while refusing anything it cannot verify, stopping
safely under every delivery-path fault tested, and costing ~34 MB and
half a core.

**What remains theoretical:** every figure about money. No job has been
sold, no payment received, no customer exists. The hourly profit model
in section N is arithmetic over assumptions, and I have said so
wherever it appears.

**What should happen next:** wire the incident recorder, then the
disk policy — together Plan A, one to two days, no new cost — and
then the owner's payment and configuration decisions, then one real
job. The engineering is further ahead than the business, and more
engineering will not close that gap. But the one engineering item
that genuinely must land first is V.0: an autonomous business that
cannot report its own failures is not yet autonomous, however well it
contains them.

---

## The ten questions, answered

1. **Tool readiness** — Yes for the five advertised skills, measured
   correct to 50,000 rows. No for PDF, `.doc`, RTF, ODT.
2. **Brain effectiveness** — Measurably yes, but narrowly: it changes
   2 of 3 client rankings and nothing else. It cannot improve
   execution, the model layer is unconfigured, and one learned signal
   is never read.
3. **Browser readiness** — Yes, under the seven conditions, with
   evidence; not integrated, and it must stay read-only and one
   worker to protect MT5.
4. **Payment readiness** — Accounting fully implemented for USD and
   crypto, including the controls that refuse to call unconfirmed
   money revenue. **No rail connected; `money_can_move: False`.**
5. **Maximum performance** — Work-bounded: 6,148 jobs/hour at 5,000
   rows, 503 at 50,000, one lane. **Ceiling-bounded: 20 committed, 3
   concurrent.** The ceiling binds long before the compute.
6. **Realistic performance** — Demand-bounded and therefore
   **unmeasured**. The factory handles its ceiling comfortably; no
   customer flow exists to fill it.
7. **Profitability** — Contribution margin near 100% per job ($0 model
   cost measured, ~$0.0001 compute). **Actual hourly profit: $0.00**,
   and will remain so until a rail and a customer exist.
8. **Mishap recovery** — Containment is genuinely good: 7 of 7
   delivery-path faults detected in 0.035 s mean, stopped safely,
   zero wrong deliveries, next cycle succeeding in 0.193 s mean, and
   a client message that cannot be filed raises rather than
   vanishing. Reporting is not: **no failure becomes a recorded
   incident** (V.0), and the failed job stops rather than resumes
   (V.2). DeskPilot fails safely and silently; the silence is the
   defect.
9. **Autonomy** — **I withdraw the figure I first gave here.** I
   originally reported "8 of 8 incidents handled without owner
   intervention". The follow-up measurement shows **the incidents
   never fired at all**: 8 genuine failures, 0 recorded, 0 escalated,
   panel `HEALTHY` throughout (section O.2). The accurate statement
   is that DeskPilot **contained** 8 of 8 failures correctly — nothing
   was delivered wrongly — but **resolved** none of them, because
   nothing recorded that they happened. Autonomy over technical
   incidents cannot be scored until V.0 is wired; every commercial
   decision requires the owner by design.
10. **Improvements** — In order: **P0 wire the incident recorder**
    (V.0 — small, reuses code already written and tested, and until
    it is done every other reliability number is unobservable in
    production), P0 disk retention (an MT5 outage risk), P1 job
    resume, P2 the skills-lab input, P2 the estimate learning loop.
    Explicitly **not** scaling: capacity is already ~300× unused.

---

*No production file was modified. No dependency installed, no payment
provider integrated, no marketplace automation activated, no bid
submitted, no contract accepted, no client contacted, no money moved,
no storefront published, MT5 untouched, no experimental Brain change
promoted, and the Action Gate unchanged. No acceptance criterion was
altered after observing a result; the one place my own harness was
wrong is recorded in section O.*
