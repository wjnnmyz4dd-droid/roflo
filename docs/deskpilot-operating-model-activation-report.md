# DeskPilot — operating model, measured capacity, and what still blocks activation

Sections C to J of the owner's sweep. Engine: Solvent.

Everything below is either measured on this machine, read out of the
repository, or marked as unknown. Where a number could not be obtained
it says so instead of carrying an estimate, and where a measurement
was taken somewhere other than the owner's Windows VPS it says that
too, because the two are not the same machine and the difference is
the entire reason the owner caps concurrency.

Nothing in this sweep submitted a bid, accepted a contract, moved
money, created an account, or deployed anything.

---

## C. The marketplace decision

### The decision

**No marketplace is selected for automated bid submission, because none
of the six permits it.** The route DeskPilot will use for its first
customer work is the supervised owner-entered route, which needs
nobody's permission because it makes no external call at all.

This reverses the plan's first two steps. The plan was: verify whether
Freelancer officially permits automated bid submission, then implement
the submission adapter if authorized. Step one was done and the answer
was no, so step two does not happen. Implementing the adapter anyway
would have produced a component whose only purpose is to do a thing
the agreement forbids.

### What the official evidence says, platform by platform

| Platform | Public job discovery | Automated bid submission | Operational status |
|---|---|---|---|
| Freelancer.com | **Forbidden** | **Forbidden** | BLOCKED — PLATFORM RESTRICTION |
| Upwork | **Forbidden** | Unread | UNVERIFIED |
| Fiverr | **Forbidden** | Unread | UNVERIFIED |
| Contra | **Permitted**, but not of the pages with jobs on them | Unread | UNVERIFIED |
| PeoplePerHour | Undetermined | Unread | UNVERIFIED |
| Guru | Unread | Unread | UNVERIFIED |

"Unread" and "Undetermined" are not "allowed". The system treats them
as refusals, and so does this table.

**Freelancer.com.** Section 33 of the user agreement, "Access and
Interference", forbids automated API access *for any purpose* without
express written permission. This is the clause that decides it, and it
decides both activities: the documented bid endpoint
(`POST /api/projects/0.1/bids/`) exists and is published at
`developers.freelancer.com`, and publishing an endpoint is a statement
about capability, not a grant of permission. The agreement is the
stricter document and the stricter document wins.

I have to record something against myself here. Eleven samples were
taken from Freelancer's active-projects feed before that clause was
read. That sampling was the very access Section 33 covers.
`tools/observe_freelancer_feed.py` is marked DO NOT SAMPLE AGAIN and
the fixture it produced is replayed from disk; no further request has
been made.

**Upwork.** `robots.txt` disallows `/jobs/` and `/*/jobs/search*`, so
the public route is closed. It also disallows `/api*` to crawlers and
offers a GraphQL API under credentials instead — a sanctioned route
whose own terms nobody has read. Its developer documentation returned
HTTP 403 to an automated fetch, which is the platform declining to be
read by a machine, and is not a basis for anything.

**Fiverr.** `robots.txt` disallows `/search/`, `/search_results/` and
`/gigs/search`. It is also seller-led rather than brief-led, so even
with permission the acquisition shape would not be a bid board.

**Contra** is the interesting one, and the lesson in it is worth more
than the platform. Its `robots.txt` disallows only `/@internal` and two
redirect parameters: no job path, no search path, no crawl delay. That
makes it the single candidate among the six for a permitted automated
read, and the catalogue records the permission as genuinely PERMITTED.

And the permission applies to the wrong pages. Contra's Opportunities
board — the part that actually lists work — is behind Contra Pro, a
paid subscription. Reading it is therefore authenticated discovery and
a spend of the owner's money, not the public discovery its robots.txt
allows. A system that modelled "Contra" as one permission would have
concluded it was cleared to go and found nothing there. This is
exactly the conflation that splitting access into seven separate
activities exists to prevent, and it is the first time the split has
caught something.

**PeoplePerHour** and **Guru** have not been determined. PeoplePerHour's
`robots.txt` was read and is ambiguous for the paths that matter;
Guru's has not been read at all. Neither is recorded as permitting
anything, because an unread document is not a permissive one.

### What credentials or approvals remain

| What is missing | For what | Who can supply it |
|---|---|---|
| Express written permission under §33 | Any Freelancer automation, discovery included | Freelancer.com, in writing, to the owner |
| Upwork API credentials **and** a reading of the API terms | Authenticated discovery on Upwork | Upwork; the terms are the owner's to read |
| A Contra Pro subscription **and** a reading of Contra's terms of service | Reaching the Opportunities board at all | Contra, paid; the owner decides whether it is worth it |
| A reading of PeoplePerHour's and Guru's terms | Determining anything about either | The owner |
| An owner account, verified, on whichever platform is chosen | Any submission, by any route | The owner, in person |

None of these is a code change. All five are outside the repository,
and no amount of implementation moves any of them.

### What this leaves working

Manual import is available on all six platforms and needs nobody's
permission, because it is the owner looking at a page in their own
browser and handing DeskPilot the posting. That is the route the
supervised first-customer workflow uses, and it is implemented, tested
end to end, and ready.

---

## D. Can five workers operate simultaneously?

Yes, measured — with a stated caveat about where it was measured.

### What was measured

`tools/worker_concurrency.py` runs real end-to-end business cycles: a
real file produced by a real skill, verified by the real checks,
through the real capacity gate and the real SQLite store. Nothing is
stubbed and nothing is slept. It runs the same jobs twice — once
sequentially on one worker, once across five workers at the same time
— and compares.

The run recorded in `docs/evidence/worker-concurrency.json`:

| | workers | wall clock | completed | median latency | slowest | peak RSS |
|---|---|---|---|---|---|---|
| sequential | 1 | 10.52 s | 17/20 | 99.6 ms | 187.5 ms | 33.6 MB |
| parallel | 5 | 3.04 s | 17/20 | 285.6 ms | 463.6 ms | 42.6 MB |

- **Wall-clock speedup: 3.46×** in this run; 3.0–3.5× across repeats.
- **Per-job slowdown under contention: 2.87×** in this run; 2.9–3.4×
  across repeats.
- **Marginal memory for four extra workers: about 9 MB**, or roughly
  2.2 MB each.
- **Errors under contention: none.** The three jobs that did not
  complete are ambiguous by design — the generated case is one where
  the correct outcome is a question to the client, not a delivery —
  and they did not complete in the sequential run either.

Five workers are therefore three times faster in wall clock at three
times the per-job latency, which is what a shared interpreter and a
shared disk predict. They do not interfere to the point of failure and
they do not deadlock.

### Can each maintain four committed jobs?

Yes, and the gate was asked rather than the diagram:

```
committed per skill: {'csv-cleanup': 4, 'spreadsheet-automation': 4,
                      'document-processing': 4, 'data-entry': 4,
                      'report-builder': 4}
total committed: 20   workers started: 5   a sixth worker started: False
```

Twenty jobs committed, five running, and a sixth worker refused. The
refusal is the part that matters: a system that could hold twenty jobs
but also start twenty workers would be uncontrolled concurrency on a
machine shared with MetaTrader 5.

This required the capacity gate to be rebuilt around the operating
model, because it could not express it. It counted jobs globally, so
four csv-cleanup jobs filled the whole shop and the other four skills
starved. Reservations now carry a capability, counting is per skill,
and admission is separated from starting: admission fills a pipeline,
`start()` occupies a worker. They had to be separate, because if
admission occupied a worker then a full pipeline of four would read as
four busy workers and the fifth skill would never get one.

### What the resource measurements do **not** establish

The measurements above were taken in a Linux container. The owner's
VPS runs Windows, alongside MetaTrader 5, on different hardware with a
different filesystem. Specifically:

- **Peak memory** comes from `resource.getrusage`, which does not exist
  on Windows. The tool returns `None` there and reports the figure as
  unavailable rather than extrapolating.
- **CPU utilisation** is not measurable on the target host through
  anything currently installed on it. The configured `cpu_share` is a
  budget the owner sets, not a measurement the system takes.
- **MetaTrader 5 was not running** during any of this, and it is the
  reason the concurrency cap exists at all.

Treat the figures as a floor on cost and a ceiling on speed. They
establish that the five-worker model works and roughly what it costs
on commodity hardware. They do not establish what it costs on the
owner's machine, and nothing in this repository can.

---

## E. Measured completion times, by skill

Seven runs per skill, medians, measured on this host by actually doing
the work. Inputs are generated by `solvent.certgen` from a seed used
nowhere else in the repository.

| Skill | execution | verification | end to end | p95 end to end | source |
|---|---|---|---|---|---|
| csv-cleanup | 0.64 ms | 1.33 ms | 86.8 ms | 110.3 ms | 605 B |
| spreadsheet-automation | 2.00 ms | 49.64 ms | 156.8 ms | 207.6 ms | 2,963 B |
| document-processing | 0.95 ms | 8.48 ms | 77.9 ms | 91.4 ms | 2,192 B |
| data-entry | 0.75 ms | 18.81 ms | 112.3 ms | 141.0 ms | 538 B |
| report-builder | 0.16 ms | 0.66 ms | 68.8 ms | 91.6 ms | 490 B |

csv-cleanup's end-to-end figure is over three runs rather than seven.
Four of its seven generated cases are ones where the correct outcome
is a question to the client rather than a delivery, and timing those
would measure how fast DeskPilot stops, which is a different number.
Execution and verification are over all seven in every row.

"End to end" is the whole business cycle — triaged, qualified, priced,
approved, executed, verified, delivered, reconciled — not just the
skill running.

Two things stand out and both are real. Verification costs between two
and twenty-five times what execution costs, because verification runs
every check the requirements ask for while execution runs once. And
the business cycle costs far more than either, because committing,
recording and reconciling a job is most of the work when the work
itself takes a millisecond.

### Synthetic versus real

**Every figure above is synthetic. The count of real client jobs is
zero.**

| | count |
|---|---|
| Jobs executed for a paying client | 0 |
| Bids submitted | 0 |
| Invoices paid | 0 |
| Benchmark runs on generated inputs | 7 per skill |

This is enforced, not merely stated.
`ExecutionHistory.NOT_CLIENT_WORK` excludes `BENCHMARK` rows from the
history that prices and promises work, and `Finder.bid` refuses to bid
on a capability with no measured client runs. The reason is in the
numbers: csv-cleanup takes 0.64 ms on a generated 605-byte file, and a
bidder that believed that figure would promise to clean a client's
200,000-row export in under a millisecond. A generated 605-byte CSV is
not a client's export, and these timings do not become a delivery
promise without client history behind them.

So the five benchmarks establish that all five skills run correctly on
this host and roughly what they cost to run. They establish nothing
about how long a real job takes.

---

## F. What the $80/hour gross revenue target would require

The owner has named a gross revenue target of $80 per operating hour.
`tools/revenue_target.py` inverts the revenue model to ask what would
have to be true, and reports the supply and demand halves separately
because only one of them is knowable.

### What the target comes to

| Over | Hours per month | Gross per month |
|---|---|---|
| An eight-hour working day | 176 | $14,080 |
| Every hour the VPS is billed for | 730 | $58,400 |

The difference is four-fold and the target does not say which is
meant. Both are stated rather than one being assumed.

### What it needs, per job

At the owner's configured rate of $40/hour with a $40 minimum fee:

| If a job is billed | Gross | Jobs/hour for the target | Jobs/day |
|---|---|---|---|
| one billed hour | $40.00 | 2.0 | 48 |
| two billed hours | $80.00 | 1.0 | 24 |

### Can the machine supply that?

Comfortably, and this is the finding. The slowest measured skill
allows roughly **23,000 end-to-end jobs an hour on a single worker**.
The target needs one or two. Machine capacity exceeds the requirement
by about four orders of magnitude.

**The target is not a capacity problem.** No amount of further
optimisation, additional workers or faster skills moves it.

### What it needs on the demand side

This is where the target actually lives, and there are **zero
observations**: no bid submitted, none won, no invoice paid. Nothing
in this repository can tell anyone whether work exists at the owner's
price. What can be stated is the arithmetic, per what-if win rate —
and every row below is a what-if, not an estimate:

| Win rate (**assumed**) | Bids/day at $40/job | Bids/day at $80/job |
|---|---|---|
| 2% | 2,400 | 1,200 |
| 5% | 960 | 480 |
| 10% | 480 | 240 |
| 20% | 240 | 120 |
| 50% | 96 | 48 |

Read that table as a requirement, not a forecast. At a 10% win rate
and $40 a job, the target needs 480 bids a day — on platforms where
automated bidding is forbidden and the supervised route is the owner
hand-feeding one posting at a time. That gap, not the machine, is what
stands between DeskPilot and $80/hour.

### What is still unrecorded

The net figures above equal the gross because the cost inputs are
unset, and unset is not zero:

`minimum_project_fee_cents`, `platform_fee_fraction`,
`platform_fee_minimum_cents`, `payment_fee_fraction`,
`payment_fee_flat_cents`, `fixed_monthly_overhead_cents`.

Until the owner records them with `solvent setup commercial`, every
net figure is the gross figure wearing a different label. For scale:
Freelancer's published fee is 10% or $5, whichever is greater, which
takes $5 from a $40 job — 12.5%, not 10%.

---

## G. The four owner notifications

All four are implemented, wired, and verified by running them. Two
defects were found in the process, both in work committed earlier in
this same sweep, and both found by running the code rather than
reading it.

| Notification | Event | Fires when | Verified |
|---|---|---|---|
| Job obtained | `business.job_obtained` | the cycle records committed requirements | yes |
| Payment received | `business.payment_received` | the job transitions to PAID | yes |
| Morning summary | `business.morning_summary` | 06:50 local, ±30 min grace | yes |
| Evening summary | `business.evening_summary` | 16:30 local, ±30 min grace | yes |

`tools/show_owner_briefings.py` prints the actual messages, composed by
the real code and read back out of the notifier. The owner can read
the words before switching anything on.

### What the owner actually receives

> **Job obtained for csv-cleanup from owner_entered: $40.00 agreed. It
> is in the pipeline and a worker will take it when one is free.**

> **SIMULATED payment of $40.00 recorded for demo-1. No money moved and
> this is not revenue.**

> **06:50 summary. 1 thing(s) need you.**
> - 2 notification(s) not acknowledged
> - collected revenue: $0.00
> - no real payment has been received; any money in the ledger is simulated and is not counted here
> - pipelines: csv-cleanup 0/4, data-entry 0/4, document-processing 0/4, report-builder 0/4, spreadsheet-automation 0/4
> - and 2 more line(s); open the control centre

The payment message is the one these tests are strictest about. A
simulated payment is announced as simulated, in those words, with "no
money moved and this is not revenue" in the body — because a summary
is exactly where simulated money would quietly become reported
revenue.

### Defect 1 — nothing called the summaries

`briefing.py` deliberately contains no scheduler: a module with its own
timer is a second daemon, and a test asserts it has no `sleep`,
`Timer`, `Thread`, `cron`, `schedule` or `asyncio` in it. That left the
question of what *does* call it, and for a while the answer was
nothing. A summary function nobody calls is the same as no summary.

Fixed: the always-on runtime registers `owner_briefing` on a
five-minute cadence. Five minutes is inside the half-hour grace
window, so a missed tick still sends, and the dedupe key is the date
plus the slot, so asking often does not mean telling often. Three
tests now cover registration, the configured cadence, and the cadence
being inside the grace.

### Defect 2 — the summary was cut by the database column

The notifier stores a summary at 400 characters, which is deliberate —
it is a database column, and the message it composes for a phone is
shorter still and deliberately incomplete. What that did to a full
summary was not deliberate. The stored text ended:

> …no real payment has bee

Mid-word, in the one sentence in the system that most needs to arrive
intact.

Fixed: shortening now happens in `Summary.text()`, where the meaning of
the lines is known rather than at the column, where it is not. Whole
lines are dropped from the back; what needs the owner is never
dropped; the last line says how many were left out. The money lines
moved ahead of the pipeline census for the same reason — a summary
that lost "no real payment has been received" while keeping a pipeline
census would be flattering by omission.

---

## H. What still blocks activation

Separated by who can fix it, because the five categories have nothing
in common except that they all block.

### H1. Code defects

**None outstanding.** Every defect found in this sweep was fixed inside
it:

| Defect | Status |
|---|---|
| Capacity counted jobs globally, so one skill starved the other four | fixed |
| `reservation_for` searched HELD only, making a running job unfindable | fixed |
| `due(None)` raised instead of returning "nothing is due" | fixed |
| Nothing called the two summaries | fixed |
| The stored summary was cut mid-word by the column | fixed |
| `solvent setup capacity` capped workers at the job ceiling, refusing the five-worker model | fixed |
| `platform.platform()` shells out and was refused by the egress guard | fixed |

### H2. Missing credentials

| Blocker | Blocks |
|---|---|
| No marketplace account, on any platform | every external route |
| No Stripe account or keys | real payment receipt |
| No crypto receipt address recorded | the crypto rail |
| No owner phone number recorded | the four notifications reaching a phone rather than the local store |

### H3. Marketplace permissions

| Blocker | Blocks |
|---|---|
| Freelancer §33 — no express written permission | all Freelancer automation, discovery included |
| Upwork robots.txt disallows the job paths; API terms unread | Upwork discovery |
| Fiverr robots.txt disallows the search paths | Fiverr discovery |
| Contra's permission covers pages without jobs on them; the board needs paid Contra Pro | Contra discovery of actual work |
| PeoplePerHour and Guru terms unread | any determination about either |

**No platform currently permits automated bid submission.** That is one
blocker, not five, and it is the one that matters.

### H4. Owner configuration

Unset, and unset is not zero:

| Setting | Command |
|---|---|
| Capacity budget, pipelines and workers | `solvent setup capacity` |
| Commercial terms and fees | `solvent setup commercial` |
| Contracting identity | `solvent setup contracting` |
| Owner-entered work source | `solvent setup work-source` |
| Probation grants per capability | `solvent setup probation` |
| Owner phone (a mask, never the number) | `solvent setup notifications` |
| Runtime paths | `solvent setup runtime` |

`solvent setup check` prints one line per item and is the fastest way
to see what is left.

### H5. Unverified live behaviour

These are not blockers that can be cleared by work; they are things
nobody can know until the system runs for real:

- No bid has been submitted, so the win rate is unknown.
- No invoice has been paid, so the collection rate is unknown.
- No client has sent a file, so no completion time reflects real input.
- No job has been disputed, so the dispute rate is unknown.
- The Windows VPS has never run this code, so its CPU and memory cost
  there is unknown and currently unmeasurable.

---

## I. Test evidence

### Regression

| | count |
|---|---|
| Tests run | 4,424 |
| Passed | 4,423 |
| Failed | 0 |
| Skipped in the combined run | 1 |
| Skipped suite run on its own | 44 tests, all passed |

The single skip is `test_installer_packaged_entrypoint`, and the reason
is itself a control working. DeskPilot's egress guard is installed in
the interpreter that runs the suite, and it refuses to start any
subprocess outside an Action Gate window. Those 44 tests need a
subprocess to exercise the packaged entry point. The guard is
deliberately irreversible and is not worked around, so the suite skips
itself with instructions to be run on its own — which it was, and all
44 passed.

The same guard caught something new this sweep: `platform.platform()`
shells out to name the processor, and the guard refused it. That is a
subprocess outside the gate whoever started it, so the measurement
tool composes its platform string by hand instead.

### Mutation

Two suites, run to completion.

| Suite | Mutants | Killed | Survived | Not applied |
|---|---|---|---|---|
| `tools/mutate_solvent.py` | 132 | **132** | 0 | 0 |
| `tools/mutate_installer.py` | 194 | **194** | 0 | 0 |

Fourteen mutants are new this sweep, covering the per-skill pipelines,
the worker-occupying `start()`, the two summaries and the four new
command-line controls:

- the job limit counted across all skills (one skill starves four)
- a worker takes a second job
- a full pipeline counts as busy workers
- more workers than the owner allows
- work in progress stops counting
- replenishment never asks
- a simulated payment announced as money
- a late run sends the morning summary at midnight
- nothing calls the summary
- the summary is cut by the column
- what needs you is shortened away
- the money line is the one dropped
- an uncertified skill gets a pipeline
- the command caps workers at the job ceiling
- a fee above one is recorded as a fraction
- unrecorded fees pass in silence

### First-run defects in the mutation protocol

Five mutants survived or failed to apply on their first run, and
**every one was a defect in the test, not in the mutant** — the same
shape each time, which is now the single most reliable finding in this
whole engagement:

**A check whose success does not depend on the thing it is checking.**

| Mutant | Why the test did not notice |
|---|---|
| `work-in-progress-stops-counting` | aimed at a test that read `executing()`, which queries RUNNING directly and never touches the constant being mutated |
| `capacity-zero-worker-limit-means-unlimited` | six jobs in one pipeline, so admission refused four of them before the worker fallback was ever reached; "at most two started" was true whether the fallback was two or ten thousand |
| `the-summary-is-cut-by-the-column` | asserted the stored summary was under 400 characters, which a summary the column had cut also is |
| `the-money-line-is-the-one-dropped` | the test case did not overflow, so nothing was dropped and nothing could be dropped wrongly |
| `more-workers-than-the-owner-allows` | not applied: the mutated line appears twice in the file |

Two of those tests had already failed to catch the same mutant once
before. `test_a_worker_limit_of_zero_does_not_open_the_gate` is now on
its third version and carries the history of both failures in its
docstring, along with a guard that fails the test if admission ever
starts doing the refusing again.

Three tests now assert their own preconditions for this reason: that
six jobs were admitted, that the summary actually overflowed, that
something actually got dropped. A test that silently stops exercising
its subject is worse than no test, because it reports green.

### The sealed capacity exam

Sat a fifth time, and the second time the seal has done its job rather
than merely held.

The seal records the SHA-256 of `capacity.py`, `finder.py`,
`revenuemodel.py` and `store.py` at the moment of freezing, and the
exam refuses to mark itself against anything else. The per-skill
pipeline work changed two of those four, so the exam stopped. The
remedy is not to widen the seal: it is to freeze the finished code and
sit the paper again. The seed derives from the freeze timestamp, so a
new freeze is a new paper rather than a re-sit.

**Fifth sitting, 2026-10-15, seed 4193036655 — six questions, six
passed, zero failed.**

| Question | Stress pair | Accepted | Delivered | Peak utilisation |
|---|---|---|---|---|
| level 7 | slow execution + spike | 7 | 7 | 0.477 |
| level 13 | delayed verification + revisions | 9 | 9 | 0.811 |
| level 37 | failed submissions + outage | 25 | 25 | 0.422 |
| level 71 | payment delays + spike | 32 | 32 | 0.825 |
| level 149 | revisions + slow execution | 46 | 46 | 0.975 |
| level 307 | delayed verification + failed submissions | 46 | 46 | 0.352 |

No criterion was tripped at any question: nothing accepted went
undelivered, no budget was exceeded, no duplicate commitment was
created, no deadline was missed, no obligation was lost across a
restart, no reservation leaked, and concurrency stayed inside the
limit throughout.

Acceptance is higher than the third and fourth papers because the
four-job ceiling is now per skill rather than across the shop. That is
the operating model working, not a loosened limit.

All five sittings are preserved in `tests_solvent/capacityexam.py`.
None is rewritten on a later run: the point of a first run is that
there is only one of them.

### Repairs made in this sweep

| What was wrong | What it would have cost |
|---|---|
| Capacity counted jobs globally | one busy skill starves the other four; the five-queue model is unreachable |
| `reservation_for` searched HELD only | a running job cannot be found, so it cannot be consumed or released |
| The execution-worker limit was moved and the verification one quietly dropped with it | unbounded checking on a machine shared with MetaTrader 5 |
| `due(None)` raised `AttributeError` | a scheduler that crashes on a missing clock sends nothing for the rest of the day |
| Nothing called the summaries | the owner is never told anything on a schedule |
| The stored summary was cut mid-word | "no real payment has bee" |
| `setup capacity` capped workers at the job ceiling | the command refuses the architecture the system is built for |
| `setup commercial` could not record any fee | every net and margin figure is the gross figure relabelled |

### Remaining risks

1. **No real client has ever used this.** Every timing, every rate and
   every completion figure comes from generated inputs. The first real
   client file is the first genuine test.
2. **The target host has never run it.** Measurements are from a Linux
   container; the VPS is Windows with MetaTrader 5 alongside, and its
   CPU and memory cost is unmeasurable through anything installed on
   it.
3. **Demand is entirely unknown.** No bid submitted, none won, none
   paid. The $80/hour target depends completely on this, and nothing
   in the repository can reduce the uncertainty.
4. **Three platforms are undetermined rather than cleared.**
   PeoplePerHour, Guru and Upwork's API terms have not been read.
   Undetermined is not permitted, and the system treats it that way,
   but it is still three unknowns.
5. **The notifications reach a local store, not a phone.** No SMS or
   voice provider is configured, so the four messages are composed and
   recorded correctly and go nowhere until one is.

---

## J. The activation checklist

Every command below exists in this repository and was run while
writing this. None of them configures a feature that has not been
implemented, and none of them submits a bid, accepts a contract, moves
money or deploys anything.

Two things to know before starting:

- `--owner` must be a **registered owner identity**, not a username.
  An unregistered identity is refused by the policy store — the
  refusal names the registered ones — which is the authority
  separation working rather than a bug.
- `--db` defaults to `/var/lib/solvent/solvent.db`. Pass it explicitly
  if the installation lives elsewhere.

### Step 0 — see where you are

```
solvent setup check
solvent readiness
solvent doctor
```

`setup check` prints one line per thing the owner configures.
`readiness` prints what blocks the first real paid job. `doctor`
reports measured enforcement — what is actually enforced, as opposed
to what is documented.

### Step 1 — record who you contract as

```
solvent setup contracting \
    --owner <IDENTITY> \
    --structure INDIVIDUAL \
    --legal-name "<NAME>" \
    --email <EMAIL> \
    --address "<ADDRESS>"
```

`--structure` is one of `INDIVIDUAL`, `LLC`, `CORPORATION`,
`PARTNERSHIP`. Add `--tax-reference-provisioned` to record that a tax
reference exists; it takes no value, because DeskPilot stores presence
and never the number.

Without this no invoice can be issued, and the business cycle stops at
the invoice gate rather than delivering.

### Step 2 — record the capacity budget and the five pipelines

```
solvent setup capacity \
    --owner <IDENTITY> \
    --max-owed-seconds 7200 \
    --max-concurrent-jobs 3 \
    --cpu-share 0.25 \
    --max-open-bids 4 \
    --max-execution-workers 5 \
    --max-verification-workers 5 \
    --skill csv-cleanup \
    --skill spreadsheet-automation \
    --skill document-processing \
    --skill data-entry \
    --skill report-builder \
    --pipeline-target 3 \
    --replenish-at 2
```

Notes on the numbers, none of which this system chooses for you:

- `--max-concurrent-jobs` is **per skill pipeline**. Three is the
  default and four is the ceiling. Five pipelines at three is fifteen
  jobs committed.
- `--max-execution-workers 5` is one worker per skill. Six is refused:
  there are five skills.
- `--cpu-share` is low on purpose. This machine also runs MetaTrader 5
  and the whole of it is not DeskPilot's to take.
- `--replenish-at` must be below `--pipeline-target`, or the command
  records it and immediately reports it as a loop rather than a rule.

The command prints the pipelines back:

```
capacity budget recorded
  [OK ] 7200s owed per pipeline, 3 job(s) per skill in flight,
        5 execution worker(s), 25% of a machine shared with MetaTrader 5
  [OK ] pipelines: csv-cleanup 0/3, data-entry 0/3, document-processing 0/3,
        report-builder 0/3, spreadsheet-automation 0/3
```

### Step 3 — record the commercial terms, fees included

```
solvent setup commercial \
    --owner <IDENTITY> \
    --hourly-rate-cents 4000 \
    --minimum-fee-cents 4000 \
    --minimum-margin-fraction 0.3 \
    --compute-cents-per-cpu-hour <WHAT THE VPS COSTS PER CPU HOUR> \
    --platform-fee-fraction 0.1 \
    --platform-fee-minimum-cents 500 \
    --payment-fee-fraction 0.029 \
    --payment-fee-flat-cents 30 \
    --fixed-monthly-overhead-cents 9800
```

The five fee settings are what makes a net figure mean anything. Until
they are recorded, every margin the system reports is the gross figure
wearing a different label, and the command now says so explicitly when
they are missing. The platform figures above are Freelancer's
published terms — "10% or $5, whichever is greater" — and should be
replaced with whatever the chosen platform actually charges.

A market rate, if you record one, must come with its citation:

```
solvent setup commercial --owner <IDENTITY> \
    --market-rate csv-cleanup=4500="<WHERE THIS NUMBER CAME FROM>"
```

A rate with no citation is refused, because it cannot be told apart
from one somebody invented.

### Step 4 — register the supervised work source

```
solvent setup work-source --owner <IDENTITY> --name owner_entered
```

`--determination` has a default that already says the right thing, and
is worth overriding only if the source is not literally the owner
typing in terms they found themselves.

This authorises **one hand-fed opportunity at a time**. It makes no
external call, holds no credentials, and does not authorise browsing,
bidding, claiming, outreach or scraping. It is the only acquisition
route currently available, because no marketplace permits an automated
one.

### Step 5 — grant probation for the first jobs

```
solvent setup probation --owner <IDENTITY> \
    --capability csv-cleanup \
    --max-jobs 2 --max-value-cents 30000 --min-deadline-hours 48
```

Without client history the Finder refuses to quote at all, which is
correct and is also a deadlock for a system that has never worked.
Probation resolves it the only honest way: a bounded, owner-granted
permission to produce a **recommendation**, never a submittable bid,
which graduates automatically after five client runs.

Revoke with `--revoke`. Revocation zeroes and marks the grant rather
than deleting it, because the policy merge means an empty dictionary
cannot remove a key — a trap found and fixed in an earlier sweep.

### Step 6 — record where the runtime's files live

```
solvent setup runtime --owner <IDENTITY> \
    --heartbeat-path <PATH> --owner-intent-spool <PATH>
```

### Step 7 — record that an owner phone exists

```
solvent setup notifications --owner <IDENTITY> \
    --sms-provider <PROVIDER> --escalate-after-minutes 30
```

A mask is recorded, never the number. **Until a provider is
configured, the four notifications are composed and stored correctly
and go nowhere.**

### Step 8 — check what is left

```
solvent setup check
solvent readiness
solvent marketplaces
```

`marketplaces` prints what each platform permits, activity by
activity. Expect it to say that automated bidding is permitted
nowhere; that is the current truth, not a misconfiguration.

### Step 9 — run the whole thing on a hand-fed job, in simulation

```
solvent cycle --owner <IDENTITY> \
    --source owner_entered \
    --capability csv-cleanup \
    --file <PATH TO THE CLIENT FILE> \
    --brief "<WHAT THE CLIENT ASKED FOR>" \
    --workdir <SCRATCH DIRECTORY> \
    --accept-quote \
    --simulate-payment
```

This takes one opportunity from an approved source through to
reconciled profit. `--simulate-payment` is what keeps it a rehearsal:
the payment is recorded with the `SIMULATED:` prefix,
`Ledger.real_revenue_cents()` excludes it, and the owner's
notification says "No money moved and this is not revenue" in those
words.

### Step 10 — see the four notifications before switching anything on

```
python3 tools/show_owner_briefings.py
```

Prints the actual composed messages, including both summaries. Read
them before the first one arrives at 06:50.

### Step 11 — start the service

```
solvent run --always-on
```

The runtime registers seven timed tasks — heartbeat, health,
reconciliation, discovery, diagnostics, owner intents, and
`owner_briefing` every five minutes, which is what sends the 06:50 and
16:30 summaries inside their half-hour grace windows.

Stop taking new work at any time without dropping work in flight:

```
solvent halt --owner <IDENTITY> --reason "<WHY>"
solvent resume --owner <IDENTITY> --reason "<WHY>"
```

The kill switch survives restarts.

### Step 12 — the control centre, if wanted

```
solvent web --host 127.0.0.1 --port 8000
solvent web-exposure --host <HOST>   # would this binding expose it?
solvent web-password                 # prompts; no echo
```

The control centre holds no authorities by construction. It writes
owner intents to a spool, and the runtime's `owner_intents` task
drains them every thirty seconds; a consequential intent still needs
an approval the website cannot mint.

### What this checklist deliberately does not include

- **Any marketplace credential.** No platform permits automated
  bidding, so there is nothing to configure and no command that would
  configure it.
- **Any live bid or contract.** There is no command that submits one,
  by design.
- **Deployment to the VPS.** Out of scope for this sweep and untested
  on Windows.
- **`solvent setup stripe`** beyond recording evidence. It advances the
  payment rail state with evidence, never with a credential; real
  Stripe keys are the owner's to hold and are not stored here.

---

## Where this leaves the business

DeskPilot can now, verifiably:

- hold five independent skill pipelines of up to four jobs each, with
  one worker per skill and a sixth refused;
- run five workers simultaneously at a measured 3× wall-clock speedup
  and 3× per-job latency, with no errors and about 2 MB of memory per
  extra worker;
- execute, verify, deliver, invoice and reconcile all five skills on
  generated inputs, measured end to end;
- price work under a bounded probation grant as a recommendation, not
  a bid;
- tell the owner four things on time, with simulated money labelled as
  simulated;
- refuse, with a citation, every automated route into every one of the
  six marketplaces.

It still cannot win work, and the reason is not in the code. One
platform's agreement forbids it in writing, three have not been read,
one blocks the paths that matter, and the one that permits crawling
keeps its jobs behind a paywall. The supervised route — the owner
hands DeskPilot a posting, DeskPilot does the job — is implemented,
tested end to end, and ready to run today.

The next milestone is the same one it was: a legitimate bid-submission
route. The only honest way to get one is to ask a platform for it in
writing.
