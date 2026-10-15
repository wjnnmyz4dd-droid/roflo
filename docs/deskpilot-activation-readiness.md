# DeskPilot: activation readiness

Classifications used throughout, exactly as the owner defined them:

| Term | Means |
|---|---|
| **LIVE VERIFIED** | It has been done, for real, and there is evidence |
| **IMPLEMENTED, AUTHENTICATION REQUIRED** | The code works; it needs a credential |
| **IMPLEMENTED, NOT LIVE VERIFIED** | The code works; it has never run against the real thing |
| **UNSUPPORTED** | No legitimate route exists |
| **REQUIRES PLATFORM APPROVAL** | The platform must grant something first |
| **UNKNOWN** | Nobody has established it |

Nothing below is **LIVE VERIFIED**. No bid has been submitted, no
contract accepted, no money moved, nothing deployed to the VPS, and
MetaTrader 5 was not touched.

---

## D. Owner account setup

### The six platforms

| Platform | Discovery | Bid submission | What blocks it |
|---|---|---|---|
| **Freelancer** | **REQUIRES PLATFORM APPROVAL** | **REQUIRES PLATFORM APPROVAL** | Agreement forbids automated API access without express written permission |
| **Upwork** | REQUIRES PLATFORM APPROVAL | REQUIRES PLATFORM APPROVAL | Registered application + OAuth credential |
| **Fiverr** | UNSUPPORTED | UNSUPPORTED | No public seller API was found |
| **PeoplePerHour** | UNKNOWN | UNKNOWN | No public API; search disallowed in robots.txt |
| **Guru** | UNSUPPORTED | UNSUPPORTED | Answers automated requests with HTTP 403 |
| **Contra** | UNSUPPORTED | UNSUPPORTED | Its public API lists freelancers *to clients* |

All six are **IMPLEMENTED, NOT LIVE VERIFIED** for manual import: the
owner brings a posting and DeskPilot does everything after it.

---

### D1. Freelancer — the agreement says no

**Status: REQUIRES PLATFORM APPROVAL.** Revised 2026-10-15. The
previous revision of this section said the blocker was a document
nobody had read. It has now been read, and it forbids what we were
preparing to do.

> **Section 33, Access and Interference:** "You agree that you will
> not use any robot, spider, scraper or other automated means to
> access the Website via any means, **including for the avoidance of
> doubt access to our API or application programming interface, for
> any purpose without our express written permission**."
> — https://www.freelancer.com/about/terms, read 2026-10-15

Three things follow, and one of them against the earlier reading in
this document:

- **HTTP 200 is not permission.** The endpoint answers without a
  credential and publishes its own rate limits. A platform can answer
  a request it has told you not to make.
- **robots.txt is not the governing document.** It does not disallow
  `/api/`, and the agreement does — the agreement is stricter and
  says so explicitly.
- **It covers reading, not only bidding.** Automated discovery is
  forbidden by the same clause as automated bidding.

The catalogue now records DISCOVER, DISCOVER_AUTHENTICATED and
SUBMIT_BID as **FORBIDDEN** with that clause as the citation, and the
access method as `UNSUPPORTED_AUTOMATION`. The derived status is
**BLOCKED — PLATFORM RESTRICTION**.

**The eleven feed samples recorded in this document were themselves
that access.** They were taken on 2026-10-14, before the agreement
was read, which was the mistake. The measurements are kept — deleting
them would not unmake the requests — and no further sampling is
legitimate.

#### What the route looks like if permission is granted

Recorded now so that the day it arrives the work is small and
reviewed. All of this is **DOCUMENTED** at developers.freelancer.com,
read 2026-10-15:

| Piece | Value |
|---|---|
| Place a bid | `POST /api/projects/0.1/bids/` |
| Body | `{"project_id", "bidder_id", "amount", "period", "milestone_percentage"}` |
| Auth | OAuth2 authorization code at `accounts.freelancer.com/oauth/token` |
| Header | `freelancer-oauth-v1: <access_token>` |
| Register an app | `accounts.freelancer.com/settings/develop` |
| Sandbox | `freelancer-sandbox.com`, config at `/developer-user-config/` |

**No submission adapter has been written.** The instruction was to
implement one *if authorized*, and it is not. Writing a bid-sending
path against a platform that forbids it in writing would be building
the one thing the rest of this system exists to prevent.

#### Owner steps

1. **Write to Freelancer and ask for express written permission**, for
   your account, naming what you want to do: read active projects via
   `/api/projects/0.1/projects/active/`, and submit bids via
   `POST /api/projects/0.1/bids/`. Section 33 is the clause to cite;
   ask them to reply in writing.
2. **Keep their reply.** It is the evidence the catalogue needs.
3. **Record it** with `solvent setup work-source`, which is where the
   determination lives. The access method moves to
   `APPROVED_INTEGRATION`.
4. **Only then** is there anything to build, and it is roughly a day:
   the parsing is done and tested, the gates exist.

If they decline, or do not reply, Freelancer is closed to automation
and the manual route in section G is the whole plan.

### D2. Upwork — an API exists, behind an application

**Status: REQUIRES PLATFORM APPROVAL.** Re-verified 2026-10-14: a POST
to `api.upwork.com/graphql` without a credential returns HTTP 401
`{"message":"Authentication failed"}`. The endpoint is real and the
door is shut, which is a platform asking for registration rather than
refusing machines.

Note that Upwork's `robots.txt` **disallows** `/jobs/`,
`/*/jobs/search*` and `/api*` to crawlers, while offering the GraphQL
endpoint under credentials. Reading the job pages is forbidden; the
API is the route they intend.

1. **Create an Upwork account** at `https://www.upwork.com/` if you do
   not have one. A freelancer account is the relevant kind.
2. **Register an API application** at
   `https://www.upwork.com/developer/` — "Create a new application".
   You will be asked for a callback URL for the OAuth 2.0
   authorization-code flow.
3. **Record the application's key and secret.** These are credentials;
   do not put them in a browser form or a file in the repository.
4. **Read the Upwork API terms of service** attached to that
   application and decide whether automated job search is permitted
   for your account.
5. **Record the determination** with `solvent setup work-source`.
6. **Place the credential** where the runtime can read it, as an
   environment variable on the VPS. DeskPilot never stores a
   credential in its database and the control centre cannot read one.

**No Upwork adapter has been written.** Steps 1–6 unblock the
permission; the code to call the GraphQL API does not exist yet, and
writing it against an endpoint nobody can reach would be writing
against a guess.

---

### D3. Fiverr — **UNSUPPORTED**

No public seller API was found on the date recorded. Fiverr is also
seller-led rather than brief-led: clients buy listed packages rather
than posting briefs to bid on, so the acquisition shape does not match
what DeskPilot does.

**Owner action: none available.** Creating an account would let you
list services manually; it would not give DeskPilot a route. If you
want to sell there, do it through the storefront path and bring the
resulting jobs in by manual import.

---

### D4. PeoplePerHour — **UNKNOWN**

`robots.txt` was read successfully on 2026-10-14 (a blank response the
day before was a transport failure, not an absent file — the two are
different facts and only one is about the platform). It says
`Disallow: /*?`, which shuts every URL carrying a query string. The
site's search works through query strings, so search is out. Listing
pages without a query string are not disallowed. No public API was
found.

1. **Create an account** at `https://www.peopleperhour.com/` if you
   want to work there.
2. **Ask their support whether an API exists** for your account type.
   No public documentation was found, so this cannot be established by
   reading.
3. **Record whatever they tell you** with `solvent setup work-source`.

Until an API is established, this platform is manual import only.

---

### D5. Guru — **UNSUPPORTED**

`robots.txt` returned HTTP 403 to an automated request on 2026-10-13
and again on 2026-10-14 from a different user agent. `guru.com/api`
returned 403 as well. Two refusals a day apart from different agents
are not a transient failure: it is a site declining to be read by a
machine, consistently.

**Owner action: none, and specifically do not work around it.** The
correct response to a 403 is to stop. You can use Guru manually and
bring jobs in by import.

---

### D6. Contra — **UNSUPPORTED** for this purpose

The only one of the six whose `robots.txt` does not disallow its
listing pages. A read-only public API exists at
`contra.com/public-api/` behind an `X-API-Key`; it returned 401
unauthenticated, and it covers **expert profiles and programs** —
helping clients find freelancers, which is the opposite direction from
what DeskPilot needs.

1. If you want to be *found* on Contra, create an account at
   `https://contra.com/` and complete a profile. That is inbound
   marketing, not a DeskPilot integration.
2. There is nothing to connect: an API that lists freelancers to
   clients does not supply DeskPilot with briefs to bid on.

---

## E. Five skill benchmarks

Measured on this Linux container, seven runs per skill, on inputs from
`solvent.certgen` — which builds a source file and the output that is
correct for it in the same step, from a seed used nowhere else in the
repository. Run `python3 tools/benchmark_skills.py` to reproduce.

| Skill | Execution (median) | Verification (median) | End to end (median) | Runs |
|---|---|---|---|---|
| csv-cleanup | 0.9 ms | 1.3 ms | 58.6 ms | 3 |
| spreadsheet-automation | 2.43 ms | 46.5 ms | 135.8 ms | 7 |
| document-processing | 1.87 ms | 12.1 ms | 85.1 ms | 7 |
| data-entry | 1.56 ms | 13.9 ms | 103.0 ms | 7 |
| report-builder | 0.56 ms | 0.67 ms | 58.7 ms | 7 |

P95s: execution 0.67–4.05 ms, verification 0.79–53.3 ms, end to end
68–166 ms.

Two things worth reading off this table. **Verification costs more
than execution in every case** and twenty times more for spreadsheets,
so anything sized on execution alone is sized wrong. And
**csv-cleanup has three end-to-end runs rather than seven** because
four of its seven generated cases are deliberately ambiguous — dates
like 05/10/2026 that denote two different days — and the correct
outcome for those is a question to the client, not a delivery. Timing
those as completed jobs would have measured how fast DeskPilot stops.

### Which skills may bid

**All five are blocked. None is eligible for autonomous bidding.**

| Skill | Bidding | Client runs | Benchmark runs |
|---|---|---|---|
| csv-cleanup | **blocked** | 0 | 7 |
| spreadsheet-automation | **blocked** | 0 | 7 |
| document-processing | **blocked** | 0 | 7 |
| data-entry | **blocked** | 0 | 7 |
| report-builder | **blocked** | 0 | 7 |

`Finder.bid` refuses any capability with no measured execution
history, and **benchmark runs deliberately do not count**. This was
the most tempting thing in the whole pass to get wrong: recording
seven runs per skill looks exactly like the way to unblock bidding.

It is not. csv-cleanup takes 0.7 ms on a generated 605-byte file. A
bidder that treated that as history would promise to clean a client's
200,000-row export in under a millisecond, and the deadline work this
system has had done to it exists precisely to stop that.
`ExecutionHistory.from_store` therefore excludes rows tagged
`BENCHMARK` from the history that prices and promises work, while
still showing them to the owner.

**A skill becomes biddable by having been done for a client.** That is
the chicken-and-egg at the centre of this system, and the only way
through it is manual import: bring in a job by hand, let DeskPilot do
it, and the measurement that produces is real history.

---

## F. Capacity control

### The three-job default and four-job maximum

**Demonstrated, not described.** `tests_solvent/test_capacity.py`,
class `ThreeJobsByDefaultAndFourAtMost`:

- An install where the owner has set everything *except* the job limit
  gets **3**, and the fourth job offered is refused.
- Configured to **4**, four are admitted and the fifth is refused.
- Configured to **5**, the gate refuses *everything* with
  `NOT_CONFIGURED`: "a limit of 5 jobs at once is above the 4 this work
  agent may hold; the default is 3 and four is the most that can be
  configured".
- More than four execution workers is refused the same way.

The ceiling is in `solvent/types.py`, not in policy, and **the control
centre cannot move it**: the web form's bound is
`JOBS_PER_AGENT_CEILING` itself. A limit that can be raised in a
settings field is one that gets raised the first week demand appears.

Two findings here, both from mutation testing. The default constant
was **dead code** — `capacity.py` held a 3 and the policy table held
its own 3 beside it, and only the policy one was read, so changing the
constant changed nothing. Both now derive from one definition. And the
test for the default **did not test the default**: the shared fixture
amends the budget with a limit of 3, which looks like the default
working and is actually the fixture.

Separately, `max_concurrent_jobs` existed in *two* policy sections
with different meanings, and the web let the owner set the
qualification one to **50**. The gate would still have refused at 3, so
no extra work could get through — but the form would have told the
owner they had raised a limit they had not. Both are now clamped to the
same ceiling.

### Simultaneous-offer testing

Deterministic simulation against the real capacity gate on a virtual
clock, at the four-job budget. The gate, the ledger and the
reservations are the real code; the jobs are generated. 1 to 500
simultaneous offers, seven conditions.

| Offers | 1 | 5 | 10 | 25 | 50 | 100 | 250 | 500 |
|---|---|---|---|---|---|---|---|---|
| Accepted | 1 | 5 | 10 | 22 | 34 | 61 | 127 | 241 |
| Delivered | 1 | 5 | 10 | 22 | 34 | 61 | 127 | 241 |
| Max concurrency | 1 | 4 | 4 | 4 | 4 | 4 | 4 | 4 |
| P95 completion (s) | 130 | 654 | 654 | 781 | 725 | 819 | 819 | 831 |
| Peak utilisation | 0.05 | 0.72 | 0.77 | 0.93 | 0.93 | 0.93 | 0.93 | 0.93 |

Accepted equals delivered at every level under every condition tested:
clean, sudden spike, slow execution, customer revisions, marketplace
outage, duplicate submissions, and restart during execution and during
contract acceptance.

**First workload level at which the system becomes unreliable: none,
up to 500 offers, under any condition tested.** The criteria were fixed
in `tools/loadsim_solvent.py` before the first level ran — a job
accepted and not delivered, a duplicate commitment, a budget exceeded,
a deadline missed, an obligation lost across a restart, a leaked
reservation, concurrency beyond the limit — and that they can fire is
shown rather than claimed: an `optimistic_deadlines` flag restores the
old deadline arithmetic and trips the deadline criterion at level 10.

**Duplicate events: 109 replayed marketplace acceptances, 0 became a
second obligation.**

The weak case, reported because it is real: under a **sudden spike**
where all 500 offers arrive at once, only **14 are accepted** and the
rest expire unread. Nothing breaks and nothing is promised that cannot
be delivered, but a burst is mostly wasted.

### Restart testing

**16 restarts across the restart conditions; 0 obligation-seconds
lost.** Reservations are written inside a transaction that takes
SQLite's write lock, so a reboot mid-execution or mid-contract-
acceptance finds the ledger exactly as it was. A replayed acceptance
after a restart is still recognised as a duplicate.

---

## G. Website interface

### Completed operator controls

27 routes behind a session. The control centre **writes nothing to
DeskPilot's database** — it writes intents to a spool directory that
the runtime reads under its own authorities, so a compromised control
centre is not a compromised business.

| Area | Route | What the operator can do |
|---|---|---|
| Marketplace connections | `/sources` | See every platform, what is permitted per activity, rate limits, and what each one is waiting on |
| Bidder settings | `/bidder` | **New.** Capacity limits in force, what is owed now, which skills may bid and why not, what DeskPilot charges; lower any limit; stop taking work |
| Job operations | `/jobs`, `/job` | Every job, its state, what was agreed, verification evidence, deliverables, client conversation, timeline |
| Money | `/money` | Payments, rail events, invoices |
| Approvals | `/approvals` | Everything waiting on an owner signature |
| Skills | `/skills`, `/capabilities` | The library, certifications, proposals, promotions |
| Safety | `/security`, `/incidents`, `/breakers`, `/audit` | Refused external actions, incidents, circuit breakers, the audit chain |
| Setup | `/setup` | What is unconfigured and what to type |

The `/bidder` form **tightens only**. Lowering a limit can only make
DeskPilot take on less, which is safe enough to apply from a browser
without a signature. Raising one is a promise to do more work than a
moment ago and needs an owner approval. An increase arriving on the
safe path is **refused by name rather than clamped** — clamping would
write a number the owner did not ask for and report success.

### Missing features

1. **No credential entry anywhere, by design.** API keys are
   environment variables on the VPS. The control centre cannot read or
   write one, which is deliberate and is also why D2 step 6 is a
   terminal step.
2. **No bid composition or submission screen.** There is nothing to
   submit through — see K.
3. **No live capacity graph.** `/bidder` shows current state, not
   history over time.
4. **Raising a capacity limit has no web path to completion.** The
   request can be made from the browser; the approval must be signed
   with the owner key, which this process does not hold.
5. **No marketplace credential test button.** Nothing to test against.

---

## H. Payments

### Stripe — IMPLEMENTED, NOT LIVE VERIFIED

`StripeRail` is complete: webhook signature verification with a
constant-time compare, a replay window, event-kind handling, and
reconciliation against the invoice by a job-id metadata key. It is
tested against fixture deliveries, including wrong-secret, stale,
replayed and tampered payloads.

- **Payment rail state on a fresh install: unset.** Not SELECTED, not
  CONFIGURED.
- **No Stripe account is connected.** No API key, no webhook secret.
- **No real payment has ever been observed.**
  `Ledger.real_revenue_cents()` returns 0 and does so by construction —
  simulated money carries a `SIMULATED:` prefix and is excluded from
  revenue by the accessor itself.
- Webhooks arrive through a **spool directory**, not a listening
  socket: the egress contract denies `socket.bind` for the whole
  interpreter.

### Cryptocurrency — IMPLEMENTED, NOT LIVE VERIFIED

`CryptoRail` supports Ethereum, Base, Polygon and Bitcoin, with
per-network confirmation minimums (12/30/60/3). Address validation is
real: BIP-173/350 bech32 checksums are verified for Bitcoin.

- **The owner's receiving address is blank**, as asked. There is no
  default and no placeholder.
- **DeskPilot cannot create a wallet** and has no code that could.
- **No transaction has been observed.** A receipt counts only at the
  network's confirmation minimum.
- **Crypto-to-USD conversion is OFF.** The three states are OFF,
  ADVISORY (record what a conversion *would* be) and AUTHORISED (the
  owner has named a provider). It cannot reach AUTHORISED without a
  provider recorded.

**EIP-55 checksum validation for Ethereum-style addresses is not
implemented**: it needs Keccak-256, which is not in the Python
standard library and is not `sha3_256`. Addresses are validated for
shape and length but a mistyped character in an Ethereum address will
not be caught by a checksum. State it as a limitation rather than
assume a typo will be noticed.

---

## I. Commercial configuration

Remaining owner decisions, in the order they block things.

| Decision | Current | Why it matters |
|---|---|---|
| **Capacity budget** (`max_owed_seconds`) | **0** | Zero means "take nothing on". The gate refuses everything until this is set. |
| **Minimum project fee** | 0 | Zero means the owner has not set one, not that any price is acceptable. |
| **Minimum margin fraction** | 0 | Enforced in `Finder._verdict`; unset means no margin floor. |
| **Platform / payment fees** | 0 | Every margin shown is better than the real one until these are recorded. |
| **Compute cost per CPU-hour** | 0 | Zero does not mean compute is free; it means nobody recorded it. |
| **Fixed monthly overhead** | 0 | The VPS at roughly $98/month goes here; net profit is flattered until it does. |
| **Market rates with citations** | empty | A rate with no citation is unusable: the point of a market rate is that it is evidence rather than preference. |
| **Contracting identity** | unset | An invoice cannot be issued without one; the business cycle stops at the invoice gate. |
| **Platform job label → capability mapping** | empty | "Data Entry" on a job board is a taxonomy string, not a statement about this system's skills. |

Already set: hourly rate **$40.00/hour**, job limit **3**, deadline
safety factor **2.0**.

The $40/hour rate is now a **floor** rather than a price-list entry. A
job that will occupy DeskPilot for sixteen hours cannot be sold for
$600 however well it clears the minimum fee and the margin. Short jobs
still floor at the minimum project fee, so speed remains the
advantage: a two-minute job has an hourly floor of about a dollar and
the owner keeps the difference between what the work is worth and how
long it took.

---

## J. Test evidence

| Measure | Result |
|---|---|
| **Tests passed** | **4,332** |
| **Tests failed** | **0** |
| **Tests skipped** | **1** |
| **Total** | 4,333 — 4,239 in `tests_solvent`, 94 in `tests` |
| **Mutation, first run** | 87/88 killed, 0 survived, **1 NOT_APPLIED** |
| **Mutation, final run** | **300/300 killed, 0 survived, 0 not applied** — 106 in the Solvent harness, 194 in the installer harness |
| **Unseen exam** | 6 questions, **6 passed**, 0 failed — three times, on three different papers |

### Mutation, honestly

The first full run after the capacity work returned **87/88 with one
`NOT_APPLIED`** — a stale anchor my own edits had made ambiguous.
`NOT_APPLIED` is not a kill; it was re-anchored and then killed. A
later full regression caught a second ambiguous anchor the same way,
in a mutant that had been silently testing nothing since the capacity
applier started checking its bounds with the same line as the
work-preference applier.

**Nine mutants survived their first run across this pass, and every
one was a defect in a test rather than a false alarm.** The pattern
each time was a check whose success did not depend on the thing it was
checking:

- Two controls indistinguishable from each other, so a mutant
  disabling either hid behind the other.
- A mutant aimed at the wrong half of a two-part guard: the in-process
  lock and `BEGIN IMMEDIATE` protect different things, and a threaded
  test can only see the first.
- A ranking test whose expensive job did not fit in any budget, so
  arrival order gave the same answer as ranking.
- A test of a default that the shared fixture had already overridden.
- A constant that nothing read.

### Unseen exam

Levels 7/13/37/71/149/307 against a development sweep that used
1/5/10/25/50/100/250/500; budgets from ranges the tests do not use;
stress conditions combined in pairs where development tested them
singly. The seed derives from the freeze timestamp rather than the
environment, so the paper cannot be reshuffled until it gives a better
mark.

It was sat **three times**, because the seal worked twice. After each
sitting, `finder.py` changed — once for job selection, once for the
hourly floor — and `finder.py` is one of the four modules the seal
covers. Each time the remedy was a new freeze over the finished code
and a fresh paper, not a narrower seal. All three records are kept
side by side. The third sitting shows markedly lower acceptance,
because the exam's budgets can no longer ask for more than four
concurrent jobs.

### Adversarial findings

Eighteen attacks in `tests_solvent/test_capacity_adversarial.py`. Four
found real defects that would each have let DeskPilot promise work it
could not deliver:

1. **A race between the gate and its own ledger.** `room_for` read,
   decided, and `reserve` then wrote, with a window in between. Two
   threads both read 500 s owed against a 600 s budget, both found room
   for 80 s, and both took it — 660 s owed, and the gate refused
   neither. Fixed with a transaction that holds the lock across the
   whole decision and takes SQLite's write lock.
2. **Admission that bought only half the job.** A job admitted with
   room for its execution could finish executing and find no room left
   to be *verified* — and because execution reservations are held until
   delivery, nothing ever freed up. Every worker ended up holding room
   for work it was not allowed to finish. Admission is now
   all-or-nothing across execution, verification and revision;
   throughput at 500 offers went from 103 delivered to 321.
3. **Deadlines promised from the middle of the distribution** — the
   median times a fixed factor, which missed deadlines from ten
   concurrent jobs upwards once runs spread to four times the median.
   Now floored at the measured tail.
4. **Deadlines that ignored verification.** A client's deadline is for
   delivery, which is after checking.

Four more defects were found by other means in the same pass: two
resource limits that **failed open** when the host would not answer
(found by reading the gate against iFixAi's silent-failure
inspection — and notable because the fix passed 162 existing tests
without one of them noticing), a pressure signal **read in the wrong
direction** so free disk reported FULL at 32 GB and OK at half a
gigabyte, an overload scenario that never overloaded, and a money
statement that was a cent out.

### Unresolved defects

**None known in the work of this pass.**

Two standing limitations, stated because they bound what the evidence
proves:

1. **The AST scanner** used by structural tests matches identifiers and
   whole string literals exactly, so it would not catch a forbidden
   name embedded inside a longer string. It exists because four tests
   this pass failed by grepping a module's text and finding a forbidden
   word inside a docstring explaining that the module does not do that
   thing.
2. **Every performance figure is from this Linux container**, not the
   Windows VPS, and on generated inputs. On the target host CPU and
   memory are not measurable at all through anything installed.

---

## K. Autonomous operation verdict

> Can DeskPilot now autonomously discover, qualify, bid, execute,
> independently verify, submit, and reconcile legitimate paid work
> through at least one verified marketplace?

# PARTIALLY IMPLEMENTED

Link by link, for Freelancer — the only platform with a verified
machine route, now known to be a route its agreement closes:

| Step | State |
|---|---|
| **Discover** | **REQUIRES PLATFORM APPROVAL** — adapter parses the real response; the agreement forbids automated API access without express written permission |
| **Qualify** | IMPLEMENTED, NOT LIVE VERIFIED — triage, ranking and the capacity gate all work |
| **Bid** | **BLOCKED** — no skill has client history, so `Finder.bid` refuses every capability |
| **Execute** | IMPLEMENTED, NOT LIVE VERIFIED — all five skills measured end to end |
| **Independently verify** | IMPLEMENTED, NOT LIVE VERIFIED — T0–T3 tiers; T2 readers installed and working |
| **Submit** | **UNSUPPORTED** — no bid-submission transport exists for any platform |
| **Reconcile** | IMPLEMENTED, NOT LIVE VERIFIED — invoice, payment matching and ledger reconciliation run end to end against fixtures |

It is not "owner activation required", and the distinction matters.
Two links are missing code, not permission:

- **Submission.** `Applications.submit` takes an injected transport and
  no platform transport exists, so every submission resolves to
  `MANUAL_SUBMISSION_REQUIRED`. Even a fully permitted bid would be
  prepared for the owner to paste in.
- **Bidding** is blocked by a chicken-and-egg that no owner action
  resolves directly: a skill needs client history to be bid on, and
  gets client history by doing client work.

What *is* only waiting on the owner is the whole commercial
configuration and the capacity budget. Discovery on Freelancer is no
longer in that list: it waits on Freelancer, not on us.

**The shortest legitimate path to a first paying customer does not go
through any marketplace integration.** It is manual import: the owner
finds one job themselves, brings it in, and DeskPilot qualifies,
prices, executes, verifies, delivers, invoices and reconciles it. That
path is blocked by nothing external, and it is also what produces the
first real execution measurement — which is what unblocks bidding.

---

## L. OWNER — HERE IS EXACTLY WHAT YOU NEED TO DO

Only actions that need you, in the order that unblocks the most.

### 1. Set the capacity budget — 2 minutes, unblocks everything

Nothing runs until this is set: `max_owed_seconds` is 0 and zero means
"take nothing on".

- Open the control centre, go to **Bidder**.
- The job limit is already **3**. Leave it unless you want 4.
- You cannot raise `max_owed_seconds` from the browser (raising is a
  consequential change). At the terminal:

  ```
  solvent setup capacity --max-owed-seconds 14400 \
                         --max-concurrent-jobs 3 \
                         --cpu-share 0.5 \
                         --revision-headroom-fraction 0.4
  ```

- `--max-owed-seconds` is the seconds of work you are willing to owe
  at once. 14400 is four hours.
- `--cpu-share` is the fraction of the VPS DeskPilot may use. **This
  machine also runs MetaTrader 5 and DeskPilot cannot see MT5's
  load**, so set it low. The command refuses anything above 1.0 and
  will tell you why.
- `--revision-headroom-fraction` holds room per job for the
  verification and revisions that follow it. Without it, three
  delivered jobs can fill the budget and leave no room for the first
  revision.
- The command refuses more than four jobs or workers, and refuses a
  deadline safety factor below 1. Run
  `solvent setup capacity` with no arguments to be told it recorded
  nothing rather than silently doing so.

### 2. Record your contracting identity — 5 minutes

Without it no invoice can be issued and the business cycle stops at
the invoice gate.

- At the terminal: `solvent setup contracting`.
- Terminal-only on purpose: it carries your legal name and address,
  and that does not go through a file the web process wrote.

### 3. Record your commercial terms — 10 minutes

At the terminal, `solvent setup commercial`:

- **Minimum project fee** — you proposed $40. Enter `4000` (cents).
- **Minimum margin fraction** — you proposed 30%. Enter `0.30`.
- **Fixed monthly overhead** — the VPS at roughly $98. Enter `9800`.
- **Platform fee fraction** and **payment fee fraction / flat** — from
  whichever platform and payment provider you actually use.
- **Compute cost per CPU-hour** — from your VPS bill.

Your hourly rate of $40.00 is already recorded and is now enforced as
a floor on the hours a job occupies.

### 4. Do one job by hand — the actual first customer

This is the step that matters most and the only one blocked by nothing.

- Find one small job yourself — a contact, a marketplace you already
  use, anywhere.
- Enter it with `solvent setup work-source` (source `owner_entered`) or
  import the client's file.
- Let DeskPilot qualify, price, execute, verify and deliver it.
- **Approve the quote when asked.** DeskPilot will not price-and-send
  without you.

This produces the first real execution measurement, which is what
unblocks autonomous bidding for that skill. Repeat until a skill has
five client runs and its estimate stops being provisional.

### 5. Connect Stripe — 20 minutes, before the first invoice

- Create or sign in at `https://dashboard.stripe.com/`.
- **Developers → API keys** — take the secret key.
- **Developers → Webhooks → Add endpoint.** DeskPilot does not listen
  on a socket; it reads deliveries from a spool directory, so point
  Stripe at whatever forwards into that directory on your VPS and take
  the **signing secret**.
- Put both in the runtime's environment on the VPS, never in the
  control centre and never in the repository.
- Then advance the payment rail: `solvent setup stripe`. It will
  not reach LIVE_VERIFIED until a real payment has actually been seen.

### 6. Enter a cryptocurrency receiving address — 5 minutes, optional

Only if you want to be paid in crypto. The field is blank and DeskPilot
will not create a wallet.

- Use a wallet **you** control. Copy the receiving address.
- At the terminal: `solvent setup crypto` — enter the network
  (ethereum, base, polygon or bitcoin) and the address.
- Bitcoin addresses are checksum-validated. **Ethereum-style addresses
  are not** (that needs Keccak-256, which is not in the standard
  library), so check an Ethereum address character by character
  yourself.
- Leave conversion OFF unless you have a provider to name.

### 7. Ask Freelancer for written permission — unblocks everything there

**Changed 2026-10-15.** This step used to be "read their API terms".
They have been read. Section 33 forbids automated access to the API,
including reading, without Freelancer's **express written
permission**. There is nothing further to read; there is something to
request.

- Write to Freelancer support or their API contact from your account.
- Name exactly what you want: read active projects via
  `/api/projects/0.1/projects/active/`, and submit bids via
  `POST /api/projects/0.1/bids/`.
- Cite section 33 of the user agreement and ask for their answer in
  writing.
- When they reply, record the determination with
  `solvent setup work-source` and keep the reply.
- Until then DeskPilot will not poll them, and that refusal is now
  enforced by the catalogue rather than by anyone remembering.

Do not treat the API answering without a credential as permission. It
answers; the agreement still says not to ask.

### 8. Register an Upwork application — 1 hour, optional

Do this only if you want a second source later. There is no Upwork
adapter yet, so this unblocks permission, not capability.

- `https://www.upwork.com/developer/` → create an application.
- Record the key and secret as environment variables on the VPS.
- Read the API terms; record the determination with
  `solvent setup work-source`.

### 9. Approve a supervised live pilot — when 1–5 are done

- Real execution is off. Turning it on is terminal-only and is the
  single change that converts a simulation into a business that can
  spend money and contact strangers: `solvent setup capability`.
- Do it **after** at least one job has gone end to end in simulation
  and you have read the audit trail for it.
- Keep the job limit at 3 for the pilot.

### What you do *not* need to do

- Create a Fiverr, Guru or Contra integration. There is no legitimate
  route on any of them and none is coming from DeskPilot's side.
- Enter any credential into the website. It cannot read them and will
  not ask.
- Raise the job limit past 4. It is not a settings field.
