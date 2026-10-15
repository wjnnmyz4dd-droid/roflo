# DeskPilot — final autonomy and direct-hire report

Engine: Solvent. Branch `claude/solvent-architecture-audit-8ot9et`.

Everything below is measured, read from a primary source, or marked
unknown. Where a figure comes from this Linux container rather than
the owner's Windows VPS, it says so, because the two are not the same
machine and the difference is the reason concurrency is capped at all.

Nothing in this sweep submitted a bid, accepted a contract, moved
money, created an account, or deployed anything.

---

## A. Executive verdict

**IMPLEMENTED — ACTIVATION REQUIRED.**

Not *live autonomy verified*: no client has ever paid DeskPilot, no
bid has ever been submitted, and the payment rail has never confirmed
a real receipt. Simulation cannot establish any of those and this
report does not pretend otherwise.

Not *blocked by external restrictions* either, and that is the change
in this sweep. The previous answer was that no marketplace permitted
automated acquisition. That is still true **of bidding** and is no
longer the whole picture: the prohibition every agreement shares is
on *submitting proposals*, and none of them forbids being hired. The
direct-hire channel is implemented, tested and open; the competitive
channel is frozen shut with a clause number against it.

What remains between here and a first legitimate dollar is owner
action and one external permission, listed exactly in sections T–V.

---

## B. Current repository state

| | |
|---|---|
| Branch | `claude/solvent-architecture-audit-8ot9et` |
| Commits this sweep | 9 |
| Working tree | clean at each commit; no uncommitted work |
| Tests | 4,642 passing, 1 skipped (runs clean standalone) |

New modules: `acquisition.py`, `channelhealth.py`, `paymentmonitor.py`,
`supervision.py`. Substantially changed: `capacity.py`, `briefing.py`,
`marketplaces.py`, `sourcecatalog.py`, `runtime.py`, `qualification.py`,
`cli.py`, `types.py`, `policy.py`, `store.py`, `web/intents.py`.

New test files: direct hire, Freelancehunt, payment monitor, capacity
reconciliation, supervision and channels, VPS protection,
representative benchmarks, acquisition adversarial.

---

## C. Existing capabilities preserved

Nothing working was rebuilt. Preserved and still passing:

- Five certified deterministic skills that call no model.
- Verification tiers T0–T3 with independent checking and evidence.
- The Action Gate and egress guard — which caught a `platform.platform()`
  call in my own benchmark tool this sweep.
- Probation, authority separation, the append-only audit chain, the
  kill switch, crash recovery, the owner channel and control centre.
- The sealed capacity exam and both mutation suites.

---

## D. Direct-hire marketplace findings

The decisive reading, quoted from the primary source.

**Freelancehunt User Agreement, clause 3.1.11:**

> "The use of tools for mass mailing of private messages or automatic
> proposal submission on projects is prohibited."

That forbids this system's bidding, by name. It does not forbid
reading, and it does not forbid being hired.

**Freelancer.com user agreement, section 33** forbids automated API
access for any purpose without express written permission — broader,
and it settles every activity at once.

| Platform | Discovery | Bid submission | Direct hire | Status |
|---|---|---|---|---|
| Freelancehunt | **Permitted** | **Forbidden** (3.1.11) | invitation visible, acceptance unread | OFFICIAL API |
| Freelancer | Forbidden (§33) | Forbidden (§33) | unread | BLOCKED |
| Upwork | Forbidden (robots) | Unread | unread | UNVERIFIED |
| Fiverr | Forbidden (robots) | Unread | unread | UNVERIFIED |
| Contra | Permitted, wrong pages | Unread | unread | UNVERIFIED |
| PeoplePerHour | Undetermined | Unread | unread | UNVERIFIED |
| Guru | Unread | Unread | unread | UNVERIFIED |

Freelancehunt publishes an official API 2.0. `GET /v2/projects`
answers **unauthenticated** and returned ten live projects on
2026-10-15. One request was made; the fixture is replayed from disk
since.

**A correction I owe this report.** I initially took `401` on
`/v2/services` as evidence that endpoint exists. It is not: a path
that certainly does not exist returns `401` too, because the gateway
authenticates before routing. Only `/v2/projects` is confirmed public.

---

## E. Acquisition channel implemented

`solvent/acquisition.py` classifies work by **how it arrived**:

- `DIRECT_CLIENT` — the owner's own client. No platform, no permission
  needed. Open.
- `DIRECT_INVITATION` — a posting addressed to one freelancer.
  Freelancehunt marks these `is_personal`. Discovery permitted;
  acceptance still gated, because discovering a posting and agreeing
  to do the work are different permissions.
- `COMPETITIVE_BID` — an open posting. **Frozen.**

`solvent/marketplaces.py::FreelancehuntProjects` reads the official
API. A test asserts the class has no `bid`, `submit`, `apply`, `send`,
`accept` or `propose` in its surface.

`StandingAuthority` is the owner's pre-agreed yes — bounded per
capability by value, notice and count — which is what "no manual job
management during normal operation" actually requires.

**Practical blocker found and closed:** every Freelancehunt budget is
in hryvnia. A permitted integration produced five postings and zero
prices. The owner now records a rate with a mandatory citation
(`--exchange-rate UAH=2.4="<source>"`); a rate multiplies every price
off the platform, so a guessed one makes every quote a guess.

---

## F. Competitive bidding freeze

The freeze **adds** a gate and removes none. `channel_problem()`
consults the source catalogue first, so a platform that forbids
something still forbids it and the stricter answer wins.

Verified by test: Freelancehunt still reads FORBIDDEN for
`SUBMIT_BID` and PERMITTED for `DISCOVER`; no standing grant can
unfreeze bidding, including one hand-edited to list the competitive
channel.

No existing safety control was removed in this sweep. Two were
*added* (the freeze, and the global committed ceiling) and two
silently broken ones were repaired — see section H.

---

## G. Five-worker operating model

Measured, not asserted: **20 committed, 5 executing, a sixth worker
refused.**

Admission and starting are separate. Admission fills a pipeline;
`start()` occupies a worker. They had to be separate, because if
admission occupied a worker then a full pipeline of four would read as
four busy workers and the fifth skill would never get one.

Worker/checker separation: `max_execution_workers` and
`max_verification_workers` are independent limits, enforced at
different points — execution at `start()`, verification at
reservation, because nothing "starts" a check the way a worker picks
up a job.

---

## H. Five rolling pipelines and the 4→2→4 cycle

`pipelines()` reports per skill: committed, executing, target,
replenish_at, needs_replenishment, room. Replenishment is reported
per skill and asks independently — a test asserts one full pipeline
does not silence another skill's request.

**The eight-limit audit found two conflicting global limits still in
place, and both were failing *shut* in places nobody was watching:**

1. `qualification._at_capacity()` counted every job in the shop and
   compared against the **per-pipeline** ceiling of four. Four
   csv-cleanup jobs stopped the Finder qualifying anything, in any
   skill, while the model targets twenty. The five queues could never
   have filled.
2. The control centre's forms clamped `max_execution_workers` at four.
   The command line had this exact bug and was fixed a sweep earlier;
   the fix had not reached the form.

`GLOBAL_COMMITTED_CEILING` is now derived from skills × per-pipeline
ceiling so the two cannot drift.

---

## I. The ten advanced autonomy requirements

| | Requirement | State | Evidence |
|---|---|---|---|
| 6.1 | Commitment control | Implemented | idempotency keys, `store.transaction()`, 8-thread race test produces 1 obligation |
| 6.2 | Deadline intelligence | Implemented | `deadline_problem()` divides owed work by *effective* workers; representative benchmarks replace median-execution estimates |
| 6.3 | Client communication | Partial | `clientrelations.py` prepares replies from records; quarantine on all client text; **no autonomous sending** — permission undetermined everywhere |
| 6.4 | Independent verification | Implemented | tiers T0–T3, `independent.py`, evidence required, checker independence measurable |
| 6.5 | Real profit accounting | Implemented | collected / invoiced-unpaid / projected kept apart; unreadable figures are None |
| 6.6 | Crash recovery | Implemented | crash matrix; worker-crash test reopens the store and the job is still RUNNING with its worker occupied |
| 6.7 | Channel health | **New** | `channelhealth.py`; untested channel is UNCERTAIN and paused |
| 6.8 | Self-improvement | Implemented | `ExecutionHistory` with `NOT_CLIENT_WORK` excluding benchmarks; no adaptive path writes policy |
| 6.9 | Profit prioritisation | Implemented | `value_density`, `choose`, counterparty record carried uninterpreted |
| 6.10 | Operations supervision | **New** | `supervision.py`; detects stalls, names the owning authority, fixes nothing |

6.3 is the honest "partial": the machinery exists, and no agreement
read so far establishes that automated client messaging is permitted,
so nothing sends.

---

## J. Payment monitoring

`solvent/paymentmonitor.py` is read-only. A test asserts its surface
contains no `pay`, `transfer`, `withdraw`, `refund`, `convert`,
`charge`, `capture`, `payout`, `settle` or `retry`.

Seven states projected from the Ledger, which remains the single
authority. `PAYMENT_FAILED` was genuinely missing and was added.

**Confirmation comes from a verified rail event and nothing else.**
`confirmation_problem()` refuses an email, a client message, a
marketplace thread, a PDF receipt, a screenshot, an owner assertion
and a model judgement, each by name.

**Slow payment does not stop production.** An unpaid invoice holds no
capacity — asserted directly. Clients past the owner's overdue
threshold produce a recommendation against more work, never an
enforcement.

A hazard the end-to-end run exposed and closed: a simulated payment
reads as `PAYMENT_CONFIRMED`, because "simulated" is not one of the
owner's seven states. `real()` is the question anything reporting
money must ask, and every money figure in the module already asks it.

---

## K. Notifications

Exactly two routine per job.

**JOB OBTAINED** carries source, skill, client and job id, contract
value, deadline, worker and queue position — and *requires a contract
reference*, raising without one. That is how "do not send this merely
because a bid was submitted" is enforced rather than described.

**PAYMENT RECEIVED** carries gross, fees, net, provider and confirmed
timestamp, and requires a reference to the provider evidence.

A latent bug surfaced here: the job-obtained call site referenced
`opportunity`, a name never in that scope, hidden on the right of an
`or` that always short-circuited. The first zero-valued cycle would
have lost the notification silently.

---

## L. Scheduled summaries

06:50 and 16:30 **America/New_York**, with the real transition table.
10:50 UTC is the morning slot in July and 11:50 UTC in January.

Dedupe is keyed on the **owner's** date, so a run just after midnight
UTC does not file the evening summary under tomorrow and send it
twice. A host with no tz database falls back to UTC and says so in
the summary rather than silently sending at the wrong hour.

---

## M. VPS resource protection

`tools/inspect_host.py` measures and refuses to infer. On this
container: 15 readings measured, 4 unmeasurable.

**Unmeasurable, and reported as such:** load average and per-process
CPU (absent on Windows), disk I/O throughput, and MetaTrader 5's
footprint — not observable without enumerating another application's
processes, which this system does not do on a machine it is a guest
on.

Nothing is inferred from the $98/month price. A price is a billing
fact, not a core count.

`effective_workers()` reduces concurrency automatically: a measured
resource at FULL drops to one worker, TIGHT halves it, and **a limit
the owner set that the host will not report also drops to one**.
Never to zero — zero cannot be told apart from halted. Throttling
bounds what may *start*, never what is running.

The degradation ladder was computed and **nothing read it** until this
sweep. The discovery tick reads it now and backs off.

A tree-wide guard forbids `kill`, `killpg`, `SIGKILL`, `taskkill`,
`TerminateProcess`, `OpenProcess`, `psutil` and `process_iter` across
all of `solvent/`.

**These are container measurements. The owner's VPS has never run this
code.**

---

## N. Representative benchmarks

The owner objected that millisecond figures from a few-hundred-byte
file are not evidence about client jobs. Confirmed by measurement.

| Skill | Input | Execute p50 | Verify p50 | Label |
|---|---|---|---|---|
| csv-cleanup | 944 B | 1.2 ms | 1.6 ms | SYNTHETIC |
| csv-cleanup | 92 KB | 61.5 ms | 119.8 ms | REPRESENTATIVE |
| data-entry | 553 B | 0.7 ms | 14.9 ms | SYNTHETIC |
| data-entry | 69 KB | 53.9 ms | **2,709.8 ms** | REPRESENTATIVE |

**data-entry verification scales worse than linearly and dominates
everything else.** 126× the input for about 180× the verification,
with the curve steepening. This is the number a delivery promise must
be derived from.

Three skills could not be scaled: `.xlsx` and `.docx` are zip
containers and report-builder takes a single JSON object. Those rows
stay SYNTHETIC and say so.

**Revenue.** The $80/hour target needs 1–2 delivered jobs an hour. On
representative inputs the slowest skill allows about **1,303 jobs an
hour** on one worker — down from the synthetic figure's 23,000, a 17×
correction. The conclusion holds; the margin is now believable.

Demand remains entirely unobserved: no bid submitted, none won, none
paid. At a 10% win rate and $40 a job the target needs 480 bids a day,
on platforms where bidding is forbidden. **That gap, not the machine,
is what stands between DeskPilot and $80/hour.**

---

## O. Adversarial testing

A keyword audit found 31 of the 40 required scenarios already
attacked. The nine missing were added as attacks, not checklist
entries: duplicate acquisition, duplicate contract acceptance,
checker overload, worker crash, invoice duplication, forged payment
confirmation, malicious attachment, marketplace API failure,
replenishment races.

All 25 new adversarial tests pass. Existing suites: 66 capacity
adversarial, 56 probation adversarial, the crash matrix, failure
injection.

---

## Q. K-LEAN and iFixAi

Full detail in `docs/evidence/advisory/README.md`.

**iFixAi — executed natively, but not against DeskPilot.** Cloned at
`1559983`, installed from source, `ifixai 4.2.0`, run as
`ifixai run --provider mock --suite all --eval-mode deterministic
--no-telemetry` with `DO_NOT_TRACK=1`. Telemetry was disabled
deliberately: it sends a persistent install id by default and sending
anything outward about this session is an effect nobody asked for.

The scorecard it produced reads `Provider: mock`, `Fixture: NimbusForge
Deploy Copilot` — a demo fixture bundled with the tool. **The 60% /
grade D is about that mock.** Reporting it as a DeskPilot finding
would be fabrication.

It could not be pointed at DeskPilot for two reasons. iFixAi audits a
live AI assistant *endpoint* for behavioural failures; it is not a
static analyser of a Python codebase. And DeskPilot has no model
endpoint to point it at — the five certified skills are deterministic
and call no model, which is a designed property. 40 of 60 inspections
errored with "judge pipeline required" in any case, needing a second
LLM as judge with credentials that do not exist here.

**Findings about DeskPilot from iFixAi: none.** Not "none found" —
none sought, because the tool and the subject do not meet.

**K-LEAN — did not run.** Cloned at `79f3557`. The repository is
archived; its own README says "the committee has adjourned".
`src/klean/reviews.py` hard-codes `LITELLM_URL =
"http://localhost:4000"` and federates a review across OpenAI,
Gemini, DeepSeek and Moonshot through a LiteLLM proxy. That needs API
keys for those providers. There are none here, and inventing them or
reaching for the owner's is not something this sweep will do.

**Nothing in this report is a K-LEAN finding.**

---

## R. Sealed examination

The seal records the SHA-256 of `capacity.py`, `finder.py`,
`revenuemodel.py` and `store.py` at freeze time and refuses to mark
itself against anything else. `capacity.py` and `store.py` changed in
this sweep, so the exam stopped — correctly — and was resealed after
implementation finished rather than repeatedly against a moving
target. Every prior sitting is preserved in
`tests_solvent/capacityexam.py`; none is rewritten on a later run.

---

## S. End-to-end lifecycle

`tools/lifecycle_evidence.py` runs the whole chain once with a
timestamp on every stage. Full trail in
`docs/evidence/lifecycle-trail.json`.

| Stage | Status |
|---|---|
| Discovery → Acquisition channel → Qualification | LIVE |
| Pricing → Commercial approval → Capacity approval | LIVE |
| Acceptance → Skill queue → Worker execution | LIVE |
| Independent verification → Revision → Delivery | LIVE |
| Invoice | LIVE |
| **Payment confirmation** | **SIMULATED** |
| Capacity release → Replenishment → Notifications | LIVE |

**The first stage that is not live is payment confirmation**, and it
is the only one. `state=PAYMENT_CONFIRMED, simulated=True; no verified
rail event exists because no rail is connected.` Capacity released to
zero in flight afterwards and the pipeline asked for replenishment,
which is the loop closing.

---

## T. First-dollar readiness

| # | Question | Answer |
|---|---|---|
| 1 | Discover a genuine paying opportunity? | **Live verified** — Freelancehunt `/v2/projects` returned ten real listings |
| 2 | Acquire without competitive bidding? | **Implemented** — direct-hire channel; no proposal is submitted |
| 3 | Does the channel permit unattended operation? | **Partly.** Owner-entered: yes. Freelancehunt invitations: discovery yes, acceptance unread |
| 4 | Accept a real contract under standing authority? | **Implemented**, simulation verified |
| 5 | Assign the correct worker? | **Simulation verified** — one job per worker, per skill |
| 6 | Can the worker finish? | **Simulation verified** on generated inputs at client-plausible sizes |
| 7 | Can the checker independently approve? | **Simulation verified** — T0–T3 with evidence |
| 8 | Deliver the result? | **Simulation verified** |
| 9 | Invoice correctly? | **Simulation verified** — needs contracting identity |
| 10 | Verify payment? | **Not implemented live** — no rail connected. The blocker |
| 11 | Replenish? | **Simulation verified** — 4→2→4 per skill |
| 12 | Recover from a crash? | **Simulation verified** — obligations survive, workers stay occupied |
| 13 | Run continuously without disturbing MT5? | **Owner activation required** — never run on the VPS |
| 14 | Operate without routine intervention? | **Implemented** — standing authority; unproven in life |
| 15 | Do all this profitably? | **No commercial evidence.** Zero revenue, zero paid invoices |

**The honest summary: every internal stage is implemented and
simulation-verified. The two that are not are payment confirmation,
which needs a connected rail, and profitability, which needs
customers.**

---

## U. Owner activation checklist

Only what is necessary, with commands that exist and were run while
writing this.

```
# 0. See where you are
solvent setup check
solvent readiness

# 1. Who you contract as — without this no invoice can issue
solvent setup contracting --owner <IDENTITY> --structure INDIVIDUAL \
    --legal-name "<NAME>" --email <EMAIL> --address "<ADDRESS>"

# 2. Capacity: five pipelines, five workers
solvent setup capacity --owner <IDENTITY> \
    --max-owed-seconds 7200 --max-concurrent-jobs 3 --cpu-share 0.25 \
    --max-execution-workers 5 --max-verification-workers 5 \
    --max-open-bids 0 \
    --skill csv-cleanup --skill spreadsheet-automation \
    --skill document-processing --skill data-entry \
    --skill report-builder \
    --pipeline-target 3 --replenish-at 2

# 3. Commercial terms, fees included
solvent setup commercial --owner <IDENTITY> \
    --hourly-rate-cents 4000 --minimum-fee-cents 4000 \
    --minimum-margin-fraction 0.3 \
    --compute-cents-per-cpu-hour <MEASURED> \
    --platform-fee-fraction 0.1 --platform-fee-minimum-cents 500 \
    --payment-fee-fraction 0.029 --payment-fee-flat-cents 30 \
    --fixed-monthly-overhead-cents 9800

# 4. Only if using Freelancehunt — a rate with its source
solvent setup commercial --owner <IDENTITY> \
    --exchange-rate UAH=<CENTS>="<WHERE THIS CAME FROM>"

# 5. The supervised channel
solvent setup work-source --owner <IDENTITY> --name owner_entered

# 6. Standing authority — what may be taken without asking
solvent setup standing --owner <IDENTITY> --capability csv-cleanup \
    --max-value-cents 30000 --min-deadline-hours 48 --max-jobs 4

# 7. Probation, so a skill with no history may be quoted
solvent setup probation --owner <IDENTITY> --capability csv-cleanup \
    --max-jobs 2 --max-value-cents 30000 --min-deadline-hours 48

# 8. Phone, runtime paths
solvent setup notifications --owner <IDENTITY> --sms-provider <P>
solvent setup runtime --owner <IDENTITY> \
    --heartbeat-path <PATH> --owner-intent-spool <PATH>

# 9. Rehearse, then read the messages, then start
solvent cycle --owner <IDENTITY> --source owner_entered \
    --capability csv-cleanup --file <FILE> --brief "<ASK>" \
    --workdir <DIR> --accept-quote --simulate-payment
python3 tools/show_owner_briefings.py
python3 tools/inspect_host.py        # before trusting any limit above
solvent run --always-on
```

`--max-open-bids 0` in step 2 is deliberate: bidding is frozen, so
outstanding offers should be zero.

---

## V. Remaining blockers

**Missing code:** none for the direct-hire path. Autonomous client
messaging (6.3) is deliberately unbuilt — no agreement permits it.

**Missing credentials:**
- Stripe account and keys — blocks real payment confirmation
- An owner account on any marketplace
- SMS/voice provider — the four messages compose correctly and go nowhere
- Freelancehunt API token (optional; discovery works unauthenticated)

**Missing permission:**
- Automated bid submission: **forbidden everywhere read.** Freelancehunt
  3.1.11 by name; Freelancer §33 broadly. Only Freelancehunt could
  change by asking in writing.
- Automated contract acceptance on Freelancehunt: **unread.** This is
  the single highest-value unknown — it is what separates
  discover-and-notify from unattended direct hire.
- Upwork API terms, PeoplePerHour, Guru: unread.

**Missing infrastructure:** the VPS has never run this code. Windows
is untested. `test_installer_packaged_entrypoint` is the only suite
that skips, and only because the egress guard blocks subprocesses —
it passes standalone.

**Missing commercial configuration:** everything in section U, plus
`compute_cents_per_cpu_hour`, which needs a real measurement.

**Missing live evidence:** zero bids, zero wins, zero invoices paid,
zero client files processed, zero hours on the target hardware.

---

## W. Production-readiness verdict

**Safe to run continuously on the actual VPS: NOT ESTABLISHED, and
not because of a known fault.**

In favour: the concurrency model is bounded and measured; unreadable
telemetry reduces workers rather than being read as healthy; nothing
in the package can signal another process; the degradation ladder is
now obeyed; obligations survive restarts with workers still occupied;
the kill switch survives reboots.

Against, and decisive: **this code has never run on that machine.**
Every measurement is from a Linux container with no MetaTrader 5 on
it. The two signals that matter most for a shared Windows host — load
average and per-process CPU — are precisely the ones unmeasurable
there. DeskPilot would be running beside MT5 with its CPU budget
honoured by worker limits the owner set rather than by anything it
can observe.

That is a defensible design and it is not evidence. The recommended
first step is `solvent run --always-on` with `--max-execution-workers
1` and `--cpu-share` low, watched for a day, before anything resembling
the five-worker model is switched on.

---

## What this sweep changed, in one paragraph

DeskPilot can now legitimately discover real work from a platform
whose agreement permits it, classify that work by how it arrived,
refuse the channel every agreement forbids, accept direct-hire work
inside bounds the owner set in advance, run five skills at one job
each with the shop's limits enforced as eight separate numbers,
notice when something stops moving, tell the owner twice a day in
their own timezone, and confirm payment only on evidence a client
cannot forge. It still cannot prove it can earn money, because it
never has.
