# DeskPilot — commercial readiness report

Every number here was produced by running the thing it describes. Per-scenario
results for all 90 skill scenarios are in `docs/deskpilot-scenario-results.txt`.

---

## G. Final classification

# READY WITH RESTRICTIONS

DeskPilot completes the entire freelance workflow in simulation — discover,
qualify, price, owner approval, contract, budget, execute, independently
verify, deliver, invoice, collect, reconcile, audit — for all five cleared
capabilities, and scores **45/45 on a final exam whose seed it had never
seen** (42/45 before the three defects that exam found were fixed).

It is **not** ready for a controlled first-revenue pilot, and the system
itself says so: `assert_may_attempt_first_real_job` refuses today. What is
missing is not engineering. It is five owner inputs and two real limits:

| Restriction | Kind | Who clears it |
|---|---|---|
| No market pricing evidence recorded | owner input | you |
| No operating costs recorded (OD-16) | owner input | you |
| No minimum project fee (OD-17) | owner input | you |
| No `SOLVENT_OWNER_KEY` (OD-5) | owner input | you |
| No payment rail | owner input | you |
| **No job above $1,000 can be delivered** | engineering limit | see F |
| Nothing has run on Windows | environment | needs a Windows machine |

Not "READY FOR CONTROLLED FIRST-REVENUE PILOT", because a pilot means quoting
a real person, and DeskPilot cannot produce a defensible quote until you
record what a human hour is worth and what running DeskPilot costs. It
refuses to invent either.

Not "NEEDS IMPROVEMENT" or "BLOCKED": the pipeline is built, joined, verified
and exercised end to end, and every remaining item is named, enforced and
actionable.

---

## B. Five-skill results — 90 scenarios

Method: each scenario is a complete job through the whole business pipeline.
The source file and the output that is correct for it are built **in the same
step, before the worker runs**, by `solvent.certgen`. The worker never sees
the answer; the grader never asks the worker what it did. A scenario passes
only when the delivered file is the file that was correct for that input.

Some scenarios pass by **asking instead of delivering**. A source containing
`05/10/2026` has no single right answer, and a system that delivered one
would be guessing. Those are graded on the question.

| Matrix | Seed | Result |
|---|---|---|
| Development | 20260920 — what the library was built against | **45/45** |
| Final exam | 30411277 — used nowhere else in the repository | **45/45** (first run 42/45) |

Nine scenarios per capability in each matrix. Full per-scenario listing —
id, expected outcome, pass/fail, source size, requirement count, execution
time — in `docs/deskpilot-scenario-results.txt`.

### What the exam found

**The exam's first run was 42/45. That is the number that means something.**
Three failures, all in code that was already certified and cleared for client
work, all fixed and each now pinned by a mutant:

1. **`consolidate_sheets` refused correct work.** A quarter consisting
   entirely of blank rows contributes nothing once `drop_empty_rows` has run.
   The check called that a lost worksheet and blocked delivery. It also could
   not resolve a renamed column: the brief says `Quantity`, the source sheet
   still says `Units`.
2. **`10/10/2025` was refused as ambiguous.** Both readings are the tenth of
   October. The rule read the *shape* — both components twelve or under —
   rather than the readings, so a job stopped and asked a question with one
   possible answer.
3. (A third, `27-Jan-2026` parsing, turned out to be inert — see F.)

Two more were found by the development matrix before the freeze:

4. **A document *conversion* job could never be delivered.** The deliverable's
   extension was a fixed per-capability constant, so a brief saying "convert
   to .md" produced `deliverable.attempt1.json`; the worker refused the
   contradiction, correctly, and nothing could get past it.
5. **The business cycle never ran ambiguity detection.** A client who wrote
   an ambiguous date got "R4 still failing" after three correction attempts
   instead of a plain question.

---

## C. Pricing results

### The model

Three numbers that were one, which was the central defect:

* **human-equivalent hours** — what a human would take. A *value* measure,
  derived from a counted unit times a cited standard. It is the basis of a
  fixed price and it is never billed as time.
* **AI execution time** — what actually happened. Measured. The only thing an
  hourly contract may be billed for.
* **operating cost** — what was actually spent: compute at the owner's
  recorded rate, model API, platform cut, processor fee. Not a labour rate.

The Financial Governor used to compute DeskPilot's cost as hours × a human
market wage. A job taking half a second of CPU arrived with **$294 of "cost"
against $400 of revenue and a 26% margin**. That was not conservative, it was
the wrong quantity. A human wage is what the *client* avoids paying.
**DeskPilot's cost has no geography.**

### Verified market sources

**NONE. Nothing is shipped, deliberately.**

Every commercial value defaults to zero or empty. A work standard without a
citation is refused at construction; a fixed-price quote with no market rate
is refused. A shipped default here would be an invented rate, and an invented
rate is what a client gets quoted.

I searched for citable benchmarks. What came back was secondary aggregators
disagreeing with each other about the same BLS release. I did not bake any of
it in. **What you must supply:** for each capability, an hourly rate and a
per-unit work standard, each with a source, a URL, a retrieval date and a
methodology. The BLS OEWS tables are the defensible starting point (data
entry keyers are SOC 43-9021).

### Worked figures — measured, TEST-ONLY inputs

Standard: 0.15 min/row + 10 min setup. Market $65/h. Discount 40%. Costs:
$0.50/CPU-hour, 10% platform, 2.9% + $0.30 payment.

| Rows | Human-equivalent | Human cost | Fixed price | AI execution | Operating cost | Profit | Margin | Client keeps |
|---|---|---|---|---|---|---|---|---|
| 200 | 0.67 h | $43.33 | $50.00 (minimum fee) | 0.021 s | $6.75 | $43.25 | 86.5% | $0.00 |
| 2,000 | 5.17 h | $335.83 | $201.50 | 0.069 s | $26.29 | $175.21 | 87.0% | $134.33 |
| 20,000 | 50.2 h | $3,260 | $1,956 | — | — | — | — | **undeliverable, see F** |

### Fixed-price conversion results

Same 2,000-row job, 4.8 seconds of work, $65/h agreed, 15-minute increment:

| | |
|---|---|
| Hourly would bill | **$16.25** (0.25 h — the minimum increment) |
| Fixed price | **$201.50** |
| Uplift | **$185.25** |
| Recommendation | convert, and the reason says why |

The system's own words: *"the work took 4.8s, less than one 15-minute billing
increment, so an hourly invoice of $16.25 would be rounding rather than time.
Quote the outcome instead: $201.50 against a human's $335.83."*

It is **advice**. It cannot re-price an agreement already made, and a test
asserts the quote it was handed came back unchanged.

### Billing compliance

The one rule that is not a trade-off:

* `hours_billable` takes the measurement **and nothing else**. There is no
  parameter that could carry the human-equivalent figure in — a test asserts
  that against the function signature, because a function that *accepts* the
  wrong number is one refactor away from returning it.
* `refuse_fabricated_hours` raises on any padded figure, and names the
  human-equivalent one when that is what was passed: *"That is the
  human-equivalent figure. It is what the work is worth, not what was worked,
  and it may be quoted as a fixed price but never billed as time."*
* Rounding to an agreed increment is allowed and **capped at a quarter hour**.
  Six seconds billed as an hour is the same lie by another route.
* An hourly invoice prints **Worked** and **Billed** on separate lines. 4.8
  seconds billed as a quarter hour is a quarter hour of *billing*, and the
  client is entitled to see which is which.
* A fixed-price invoice shows the human comparison explicitly labelled *"For
  context, not a charge"*, and computes no billable hours at all.

### Pricing failures

| Failure | Behaviour |
|---|---|
| No market rate | quote refused |
| Work standard with no citation | refused at construction |
| Discount outside 0–1 | refused |
| Hourly contract with no agreed rate | cycle stops at invoice; **the work is still delivered** |
| Billing increment over 15 min | refused as dishonest rounding |
| Job priced below its own fees | reported as a loss, not hidden |

### Two consequences, recorded rather than hidden

* With honest costs almost everything clears a 30% margin floor, so
  `MARGIN_TOO_LOW` stops being the control that keeps DeskPilot away from
  work not worth doing. That job belongs to the **minimum project fee**,
  which asks a different question. The two were conflated while the cost
  model was wrong.
* **With every cost at zero, the margin floor compares revenue against
  nothing and cannot reject any job.** Zero is an unconfigured deployment,
  not a cheap one. Now reported as OD-16.

---

## D. Customer service — 30 scenarios

All 30 are recorded, classified, routed and given a prepared reply. **Nobody
is dropped**, including the ones being told no. No persona moves anything
governed; no persona extracts a promise.

| # | Persona | Classification | Routed to |
|---|---|---|---|
| 1 | delighted | ACCEPTED | — |
| 2 | polite defect report | AMBIGUOUS | OWNER |
| 3 | angry and correct | AMBIGUOUS | OWNER |
| 4 | angry and mistaken | AMBIGUOUS | OWNER |
| 5 | abusive | HOSTILE_OR_ABUSIVE | OWNER |
| 6 | legal threat | HOSTILE_OR_ABUSIVE | OWNER |
| 7 | fraud allegation | HOSTILE_OR_ABUSIVE | OWNER |
| 8 | confused | AMBIGUOUS | OWNER |
| 9 | scope creep, politely | NEW_SCOPE | REQUALIFY |
| 10 | scope creep, entitled | NEW_SCOPE | REQUALIFY |
| 11 | refund demand | REFUND_REQUEST | OWNER |
| 12 | chargeback threat | DISPUTE | OWNER |
| 13 | billing error | PAYMENT_ISSUE | OWNER |
| 14 | unsafe request | UNSAFE_REQUEST | OWNER |
| 15 | owner impersonation | GOVERNANCE_DEMAND | OWNER |
| 16 | prompt injection | AMBIGUOUS | OWNER |
| 17 | chases an unpaid invoice | PAYMENT_CHASE | OWNER |
| 18 | asks to move the deadline earlier | SCHEDULE_CHANGE | OWNER |
| 19 | asks for a discount | PRICE_REQUEST | OWNER |
| 20 | asks for a rush and will pay more | SCHEDULE_CHANGE | OWNER |
| 21 | says the file will not open | FORMAT_PROBLEM | OWNER |
| 22 | says we sent the wrong file | DELIVERY_PROBLEM | OWNER |
| 23 | cancels mid-job | CANCELLATION | OWNER |
| 24 | asks how the work was done | AMBIGUOUS | OWNER |
| 25 | asks for the raw working files | AMBIGUOUS | OWNER |
| 26 | sends a one-word reply | AMBIGUOUS | OWNER |
| 27 | sends the same message twice | AMBIGUOUS | OWNER |
| 28 | **asks to settle up off-platform** | **GOVERNANCE_DEMAND** | OWNER |
| 29 | asks for a reference for another client | AMBIGUOUS | OWNER |
| 30 | asks who they are contracting with | AMBIGUOUS | OWNER |

**#28 is the one that matters most** and it is the one most likely to be sent
in good faith. "To save the platform fee, can I just pay you directly?" is
classified as a governance demand and referred to the owner — it is a
compliance question, not a service one.

**Honest reading:** before this session, 19 of 30 landed on AMBIGUOUS, which
is safe and useless — an owner opening a queue of identical AMBIGUOUS rows
has been handed the triage, not relieved of it. Four commercial
classifications were added and two existing ones wired up (they were in the
routing table and nothing ever produced them). **Twelve remain AMBIGUOUS**,
which is honest: they are open-ended questions a person should read.

Every one still routes to the owner. None of this lets DeskPilot answer a
client about money, dates or cancellation.

---

## E. Job platforms

### Verified working sources

| Source | Status | Evidence |
|---|---|---|
| `owner_entered` / `client_inquiry` | **WORKING** | proven end-to-end through the governed cycle on a persisted database, across a process restart |

That is the complete list.

### Unresolved restrictions

| Source | Access method | Adapter | Blocker |
|---|---|---|---|
| `sam_gov` | OFFICIAL_API | implemented | free api.data.gov key + a written owner determination |
| `grants_gov` | PUBLIC_FEED | implemented | written owner determination (grants are not freelance work) |
| Upwork, Fiverr, Freelancer, PeoplePerHour, Guru, Contra | **UNDETERMINED** | none | whether automated access is permitted is a reading of a legal document about a specific account at a specific time |
| `state_local_procurement`, `rfp_board` | UNDETERMINED | none | same |

A source cannot be marked PERMITTED without a written owner determination —
`register_source` refuses. No marketplace is claimed to work, and a wrong
guess costs the account permanently.

**A `FixtureSource` does not survive a process restart** (it rebuilds empty),
so only the Inserted Work door is usable across a restart today. Automated
discovery needs a real adapter.

### Recommended first two sources

1. **`owner_entered`** — available today, needs no credential and nobody's
   permission. Terms arrive owner-confirmed, which is what lets them bind.
2. **`sam_gov`** — the only catalogued source with an official API *and* an
   implemented adapter.

---

## F. Technical testing

| | |
|---|---|
| **Commit** | `1af9d28` on `claude/solvent-architecture-audit-8ot9et` |
| **Files changed** | 36 (+6,231 / −275) since `c5d7100` |
| **Tests passed** | **3,520** |
| **Tests failed** | **0** |
| **Tests skipped** | 2 |
| **Mutations applied** | **194** |
| **Mutations killed** | **194** — no survivors, none unapplied, none invalid |

### Defects fixed (12 this session)

Pipeline integration (found by executing the chain):

1. A capability `need` meant a *service name* to Discovery and a *check name*
   to the registry — **every discovered opportunity was rejected for a gap
   that did not exist**.
2. `_execute_job` walked a job to ACCEPTED itself: illegal from ACCEPTED, and
   from AWAITING_OWNER_APPROVAL it was the pipeline answering a question put
   to a person. It also entered EXECUTING with no Governor budget grant.
3. A posting's `need` was recorded MANDATORY with no check, so
   `commit_requirements` refused every checklist after it, permanently.
4. `confirm()` rebuilt a requirement from id and text alone, silently dropping
   its check, acceptance, params and criticality.
5. A plan refusal forced the job backwards to QUALIFYING and crashed.
6. Planners were handed scope they cannot carry out; three of five capabilities
   refused whole jobs over it.

Pricing:

7. The Governor costed DeskPilot's own work at a human market wage.

Found by the matrices:

8. Document conversion jobs could never be delivered (fixed extension).
9. The business cycle never ran ambiguity detection.
10. A false clarification question for a brief that already pinned it.
11. `10/10/2025` refused as ambiguous when both readings are one day.
12. `consolidate_sheets` refused correct work after a legitimate row removal.

### Security findings

**K-LEAN** (keys on what a secret *looks like*, not what a field is called) —
one finding, fixed: `record_verification` had redacted since a verifier was
caught quoting a client's text into the database; `audit.record` beside it —
same table, same exposure — redacted nothing, and a posting's title and
reference do reach it.

**The first fix was wrong and the regression caught it.** It redacted
`result` too, and `result` is where Solvent keeps its evidence: an artifact
digest looks exactly like a raw key, so a SOURCE artifact registered as
`a9f[REDACTED]d8a… 104 bytes` — a record that can no longer say which file was
worked on. **A control that eats the evidence is worse than the exposure it
closes.** `result` is left alone; a mutant restores the over-redaction.

**iFixAi** (did the control refuse for the *right* reason?) — no new defect;
one weak test found and split. The chain test starts with several things
wrong and fixes one at a time, requiring the refusal to move further down the
pipeline each time — qualify, owner-approval, contract, deliver, payment,
complete — and requiring no two defects to be caught by the same control.

Both directions of the delivery/payment boundary are pinned: a delivered job
is not a paid job, and money arriving does not retrospectively verify work.

One change was made and then **reverted**: teaching `date_readings`
named-month layouts. Its mutant survived; testing directly showed the verifier
behaves identically with and without it. Inert, so removed rather than kept as
unpinned code with a mutant I would have had to quietly drop.

### Recovery results

Unchanged and re-verified: the audit chain verifies after every cycle; a job
survives the process that did it (COMPLETE state read back from a reopened
database); a refused brief creates no job; a cycle that stops leaves nothing
behind it.

### Performance results

A complete governed cycle, measured:

| Source rows | Source size | Full cycle |
|---|---|---|
| 100 | 3 KB | 0.11 s |
| 1,000 | 31 KB | 0.16 s |
| 10,000 | 315 KB | 0.49 s |
| 50,000 | 1.5 MB | 2.31 s |

About **0.05 s per 1,000 rows** at scale. No model is called and no network is
touched, so the marginal cost of a job is CPU seconds.

**Nothing waits on purpose.** The only `sleep` in the codebase is the
runtime's tick cadence. A test reads the delivery path's source and asserts it
contains no sleep call, because a delay short enough to pass a timing
assertion is still a delay somebody put there.

### The $1,000 ceiling — a limit, not a defect

A job whose value exceeds the C_HIGH consequence threshold ($1,000) requires
`T2_INDEPENDENT_REVIEW` before delivery. **Nothing in the pipeline produces
T2 evidence** — the verifiers record `T1_DETERMINISTIC`. So a job priced above
$1,000 is executed, verified, and then correctly refused at the gate.

Found while producing the pricing table above: the 20,000-row job prices at
$1,956 and cannot be delivered.

This is the control working as designed — high-consequence work should not
ship on deterministic checks alone. But it is a hard revenue ceiling and it
was undocumented. Your options: keep jobs under $1,000; raise the threshold
deliberately (it is policy, and raising it is a decision about risk, not a
configuration tweak); or supply independent review for large jobs.

---

## Owner actions, in order

1. **Record your operating costs** — platform cut, payment processor fee,
   compute cost per CPU-hour. Until then the margin floor rejects nothing.
2. **Record a market rate and a work standard per capability**, each with a
   source, URL and retrieval date. Until then no binding quote exists.
3. **Set a minimum project fee** — the smallest job worth the overhead.
4. **Set `SOLVENT_OWNER_KEY`** — until then approvals are unsigned, which the
   gate permits only while simulation is on.
5. **Decide about the $1,000 ceiling.**
6. Then: a payment rail, and a written determination for any automated source.

Steps 1–3 are what stand between DeskPilot and a defensible quote. The system
refuses to guess any of them, which is why it cannot start without you.
