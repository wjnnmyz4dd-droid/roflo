# DeskPilot — complete business performance and financial forensic report

Commit `6dec9f7`. Branch `claude/solvent-architecture-audit-8ot9et`.
Evidence extraction and analysis only: no production logic was changed,
no feature added, no customer contacted, no payment processed.

---

## The finding that governs every other number in this report

**The 30/60/90-day "business simulations" were not business
simulations.** They were a six-parameter arithmetic model with no
DeskPilot component in it. The model has no customers, no jobs, no
skills, no ledger, no queue and no capacity. Its entire state per
simulated day is:

```python
Day(index, jobs_won, price_cents, direct_cost_cents,
    acquisition_cents, overhead_cents)
```

Verified by reading the dataclass at `dataclasses.fields(Day)`: six
fields, none of them a customer, a skill or a job record. `jobs_won` is
a scalar count produced by `rng.random() < 0.45`.

**The 2/3/4/5-skill configurations were never run as business
simulations at all.** `SkillConfigurations` in
`tests_solvent/test_business_operation_simulations.py` contains five
tests. Every one of them asserts about configuration plumbing —
`capacity.pipelines()` returns the right count, `committed_ceiling()`
rises, a disabled skill is refused. **No revenue, profit, customer or
job figure exists for any configuration.**

This is the distinction the assignment asked to be drawn explicitly. A
passing test that exercises a simulation function is not evidence that
a business simulation ran. In this case the tests passed and the
business simulation did not happen.

Everything in Sections C, D and parts of I and L below is therefore
reported as **ABSENT** rather than estimated. What *does* exist is a
large body of genuinely measured production-path evidence from earlier
rounds, and that is extracted in full.

---

## SECTION A — Evidence inventory

### A.1 Evidence classes used throughout

| Class | Meaning |
|---|---|
| **OBSERVED** | Counted from a real execution of DeskPilot's own components |
| **MEASURED** | Timed or totalled from a real execution, on this container |
| **MODEL** | Output of an arithmetic model containing no DeskPilot code |
| **PROJECTION** | Arithmetic over measured inputs; not an execution |
| **ASSUMPTION** | An input somebody chose; no observation behind it |
| **REAL-WORLD** | Evidence from an actual paying customer |

**There is no REAL-WORLD evidence of any kind in this repository.**
`revenue-target.json` records it directly: `bids_submitted: 0`,
`bids_won: 0`, `invoices_paid: 0`, basis *"OBSERVED: counted, and all
three are zero"*.

### A.2 Artefacts examined

| Artefact | Class | What it holds |
|---|---|---|
| `docs/evidence/financial-lifecycle-certification.json` | MEASURED | 100 contracts through the full financial lifecycle |
| `docs/evidence/busy-business-load-and-recovery.json` | MEASURED | 100 jobs across five skills; 24h accelerated run; replenishment |
| `docs/evidence/deskpilot-timing-summary.json` | MEASURED | Per-skill stage timings, 20 jobs each |
| `docs/evidence/deskpilot-per-job-stage-timings.csv` | MEASURED | Per-job stage detail |
| `docs/evidence/revenue-target.json` | MIXED | Owner inputs (OBSERVED), supply (REPRESENTATIVE), demand (ASSUMED what-if) |
| `docs/evidence/deskpilot-chief-of-staff.json` | MEASURED | Certification run; explicit `not_established` list |
| `tests_solvent/test_business_operation_simulations.py` | MODEL | The 30/60/90-day model and skill-config plumbing tests |
| New measurement, this assignment | MEASURED | Capacity behaviour at HEAD (§K) |

---

## SECTION B — Simulation execution verification

### B.1 Period simulations

| Period | Executed? | What actually ran |
|---|---|---|
| 30 days | Yes | MODEL. 120 attempts, no DeskPilot code |
| 60 days | Yes | MODEL. 240 attempts |
| 90 days | Yes | MODEL. 360 attempts |

**They are not three independent runs.** Verified directly:

```
Is 30d a prefix of 90d? True
Is 60d a prefix of 90d? True
```

They are one random sequence read at three points. The apparent
decline in daily profit across the three periods —
$56.20 → $44.76 → $32.24 — is **entirely a truncation artefact**. The
observed win rate in the same sequence drifts 47.50% → 44.58% →
41.39% by chance. The previous report presented these as three periods
and did not state that they share a sequence. There is no time trend
here to interpret.

### B.2 Skill-configuration simulations

| Configuration | Business simulation executed? | What exists |
|---|---|---|
| 2 skills | **No** | `pipelines()` returns 2; `committed_ceiling()` = 6 |
| 3 skills | **No** | 3 pipelines; ceiling 9 |
| 4 skills | **No** | 4 pipelines; ceiling 12 |
| 5 skills | **No** | 5 pipelines; ceiling 15 |
| 5 → 2 | **No** | Pipelines drop correctly |
| 2 → 5 | **No** | Pipelines restore correctly |

The configuration plumbing is verified and works. The **profitability
of each configuration was never simulated.**

### B.3 Stochastic replication

**One seed.** The previous report quoted seed 11 and did not say so.
See §H.3 for what the model does across seeds — it materially changes
the headline.

---

## SECTION C — Customer acquisition

**Status: ABSENT from the period model; partially OBSERVED elsewhere.**

The model contains no prospect, no outreach, no response and no
customer. Of the fifteen counts requested, the model can supply
exactly one — "opportunities", if one accepts that an *attempt* is an
opportunity.

| # | Metric | Value | Class |
|---|---|---|---|
| 1 | Prospects discovered | **no such entity in the model** | ABSENT |
| 2 | Qualified prospects | ABSENT | — |
| 3 | Rejected prospects | ABSENT | — |
| 4 | Outreach attempts | 360 over 90 days (4/day) — but nothing is "sent" | MODEL |
| 5 | Customer responses | ABSENT | — |
| 6 | Negotiations opened | ABSENT in the model; **0 ever, in production, before this programme's connector** | OBSERVED |
| 7 | Proposals submitted | ABSENT | — |
| 8 | Contracts accepted | 149 "wins" over 90 days | MODEL |
| 9 | Customers acquired | **no customer entity exists** | ABSENT |
| 10 | Repeat customers | ABSENT | — |
| 11 | Customers lost | ABSENT | — |
| 12 | Never responded | ABSENT | — |
| 13 | Rejected pricing | ABSENT (a "loss" is a coin flip, not a reason) | — |
| 14 | Rejected deadlines | ABSENT | — |
| 15 | Opportunities abandoned | 211 of 360 "lost" — with no reason attached | MODEL |

### C.1 Rates, with denominators stated

| Rate | Value | Denominator | Class |
|---|---|---|---|
| Qualification rate | — | no qualification step in the model | ABSENT |
| Response rate | — | nothing is sent | ABSENT |
| Negotiation rate | — | no negotiation in the model | ABSENT |
| Conversion rate | 149 / 360 = **41.39%** | attempts over 90 days | MODEL |
| CAC (per won job) | $10,800 / 149 = **$72.48** | total acquisition ÷ wins | MODEL |
| Cost per attempt | $30.00 | fixed input | ASSUMPTION |
| Cost per paying customer | — | no customer entity; **not the same as CAC per job** | ABSENT |
| Retention rate | — | no repeat business in the model | ABSENT |

Note the gap between the $30/attempt input and the **$72.48 acquisition
cost per won job**. The model charges acquisition per attempt, so every
lost attempt loads cost onto the wins. At a 41.39% win rate each win
carries 2.42 attempts.

### C.2 The 45% win rate — classification

The assignment asks which of four things this is. The answer is
unambiguous:

> **A. An input assumption.**

`grep` across the whole repository finds `0.45` in exactly one place:
the default argument of `a_month()` in my own test file. There is no
citation, no market source and no DeskPilot observation behind it. It
is not B (it is the *input* that generates the synthetic outcome, not
an outcome), not C, and not D.

**This is the sharpest methodological finding in this review.**
DeskPilot's own `solvent/revenuemodel.py` holds `bid_win_rate` as
`UNKNOWN` with the note *"no bid has ever been submitted, so no bid has
ever been won"*, and returns `None` for net profit rather than invent
one. `test_a_win_rate_is_never_defaulted` enforces it. **The production
system refuses to produce a profit figure without an owner-supplied win
rate. My simulation produced one anyway, by defaulting the exact input
production declares unknowable.**

Everything downstream of 45% — including $32.24/day — inherits that.

---

## SECTION D — Customer and job counts

**Status: ABSENT from the period model. MEASURED elsewhere.**

No per-configuration customer or job counts exist. What was genuinely
measured, in two independent production-path runs:

### D.1 Financial lifecycle, 100 contracts (MEASURED, 26.6 s wall)

| | |
|---|---|
| Contracts offered | 100 |
| Accepted | 100 |
| **Delivered** | **43** |
| Quoted value | $129,375.00 |
| Invoiced | $10,375.00 |
| Collected | $10,375.00 |
| Cost | $1,351.38 |
| Profit | $9,023.62 |
| **Real revenue** | **$0.00** |

Where the other 57 stopped:

| Stop | Count |
|---|---|
| Completed | 43 |
| `contract` | 13 |
| `verification-eligibility` | 34 |
| `independent-review` | 10 |

By value band — and this is the important row:

| Band | Contracts | Delivered | Quoted | Collected |
|---|---:|---:|---:|---:|
| small | 25 | 21 | $1,875 | $1,575 |
| standard | 25 | 22 | $10,000 | $8,800 |
| **large** | 25 | **0** | $30,000 | $0 |
| **premium** | 25 | **0** | $87,500 | $0 |

**$117,500 of quoted value — 91% of the book — delivered nothing**,
because the large and premium bands sit in a consequence tier
(C_CRITICAL) requiring T3 external-fact verification that this
installation cannot produce. That is the system correctly declining
work it cannot verify, and it is also the single largest revenue
constraint in the measured record.

### D.2 Busy-shop load, 100 jobs across five skills (MEASURED, 28.8 s)

| | |
|---|---|
| Jobs | 100 |
| Passed | 100 |
| Failed | 0 |
| **Delivered** | **87** |
| Invoiced | $34,800.00 |
| Operating cost | $4,515.30 |
| Contribution | $30,284.70 |

### D.3 Limitation classification

The assignment asks which constraint binds. On the measured evidence,
three apply and one does not:

| Classification | Verdict | Evidence |
|---|---|---|
| **CAPACITY LIMITED** | **YES, severely** | 24h accelerated run: ceiling 20, 100 offered, **7 delivered**, then nothing. Slots never return (§K) |
| **CONVERSION LIMITED** | **YES** | 57/100 contracts stopped before delivery; 34 on verification eligibility |
| **PRICING LIMITED** | **YES** | 91% of quoted value is in bands this installation cannot verify, so the high-value work is unsellable |
| **COST LIMITED** | No | Operating cost is 13% of invoiced in the load run; execution is milliseconds |
| **DEMAND LIMITED** | **Unknown, and in reality total** | Zero bids submitted, zero won, zero paid. There is no demand because nothing has ever been offered to anyone |

---

## SECTION E — Revenue

### E.1 The model (MODEL, seed 11)

The model has exactly one revenue concept. It does not distinguish
gross contract value, invoiced, collected or receivable — it credits
`price_cents` on the day a job is won.

| Period | "Revenue" | Per day | Per week |
|---|---:|---:|---:|
| 30 days | $11,400.00 | $380.00 | $2,660.00 |
| 60 days | $21,400.00 | $356.67 | $2,496.67 |
| 90 days | $29,800.00 | $331.11 | $2,317.78 |

**Breakdown by skill, customer or contract type is impossible** — the
model has none of those dimensions.

### E.2 Measured runs, where the distinction is real

| | Financial lifecycle | Busy-shop load |
|---|---:|---:|
| Gross contract value | $129,375.00 | — |
| Invoiced | $10,375.00 | $34,800.00 |
| Collected | $10,375.00 | — |
| Outstanding receivables | **$0.00** | — |
| Refunds | $0.00 | — |
| Net collected | $10,375.00 | — |
| **Real revenue** | **$0.00** | **$0.00** |

Invoiced equals collected exactly in the financial run
(`invoiced_minus_collected_cents: 0`) because the fixture settles
every invoice it issues. **That is a property of the fixture, not a
collection rate.** A real business has a collection lag and a bad-debt
rate; neither has ever been observed here.

**A material contradiction in the measured record.** The busy-shop
evidence reports $34,800 invoiced, but the same file's
`invoice_control` section records every delivered job stopping at the
invoice step with *"no invoice can be issued: INVOICE needs contracting
detail the owner has not provided"*, and states plainly: **"No invoice
was issued in ANY run: policy refuses... Gross figures above are quoted
amounts, not billed revenue."** The $34,800 is a quoted total labelled
`invoiced_cents`. It is not billed revenue and must not be read as
such.

---

## SECTION F — Direct costs

### F.1 Model inputs (ASSUMPTION)

| Line | Value | Class |
|---|---:|---|
| Direct cost per job | $102.00 | ASSUMPTION (the audit's worked example) |
| — of which execution | not separated | — |
| — verification | not separated | — |
| — revisions | not separated | — |
| — payment processing | not separated | — |

The model carries direct cost as **one undifferentiated number**. It
cannot answer the assignment's request to separate AI inference,
execution, verification, revisions, payment processing, job-specific
software and data processing. Those seven lines exist in
`negotiation.Costs` in production, and the model uses none of them.

### F.2 Measured operating cost

| Run | Jobs delivered | Operating cost | Per delivered job |
|---|---:|---:|---:|
| Financial lifecycle | 43 | $1,351.38 | **$31.43** |
| Busy-shop load | 87 | $4,515.30 | **$51.90** |

Both are **far below** the $102 the model assumes. The model's direct
cost is therefore conservative relative to what DeskPilot actually
measured — the pessimism in the headline figure is not in this line.

### F.3 Revisions and failures (MEASURED)

| Skill | Revision rate | Error rate |
|---|---:|---:|
| csv-cleanup | 0.000 | 0.000 |
| data-entry | 0.000 | 0.000 |
| document-processing | 0.000 | 0.000 |
| report-builder | 0.000 | 0.000 |
| spreadsheet-automation | 0.000 | 0.000 |

Zero revisions across 100 measured jobs. **This is a property of
deterministic skills operating on fixture inputs**, not evidence that
real clients will not ask for changes. The model separately assumes
$40/job of expected revisions inside its $102 — a cost that has never
been observed because no client has ever asked for a revision.

---

## SECTION G — Operating overhead

### G.1 What the model includes

| Category | In the model? | Value |
|---|---|---|
| VPS / hosting | **Folded into one number** | — |
| AI subscriptions | No | — |
| API subscriptions | No | — |
| Storage | No | — |
| Email infrastructure | No | — |
| Notification services | No | — |
| Monitoring | No | — |
| Software licences | No | — |
| **Owner supervision** | **No** | — |
| Administrative work | No | — |
| **Total overhead** | **$10.00/day, undifferentiated** | ASSUMPTION |

**$10.00 per day is the entire operating overhead in the $32.24
figure.** That is $300/month for a business running a VPS, AI API
calls, storage, monitoring and an owner's time.

### G.2 What was excluded from $32.24/day

Directly answering the assignment's question — the following were
**excluded entirely**:

1. **Owner supervision.** The earlier audit priced this at $25/job and
   added `supervision_cents` to `Costs` for exactly this reason. The
   period model does not use it. At 149 jobs that is **$3,725
   unaccounted over 90 days — $41.39/day**, which alone exceeds the
   reported profit.
2. AI subscription and API costs beyond the undifferentiated $102.
3. Storage, monitoring, email and notification infrastructure.
4. Any fixed monthly overhead. `fixed_monthly_overhead_cents` exists in
   policy and is read only by the advisory `revenuemodel`.

**Correcting only for owner supervision at the audit's own $25/job, the
90-day result goes from +$32.24/day to −$9.14/day.** The business as
modelled is loss-making once the owner's time is priced at the figure
the same programme chose.

### G.3 Cost per unit (MODEL, 90 days)

| Category | Total | Per day | Per completed job | Per month |
|---|---:|---:|---:|---:|
| Direct | $15,198.00 | $168.87 | $102.00 | $5,066.00 |
| Acquisition | $10,800.00 | $120.00 | $72.48 | $3,600.00 |
| Overhead | $900.00 | $10.00 | $6.04 | $300.00 |
| **Total** | **$26,898.00** | **$298.87** | **$180.52** | **$8,966.00** |

### G.4 Shared-overhead allocation between skills

**No allocation was performed, because the model has no skills.** The
question cannot be answered from the existing evidence. There is
consequently also no double counting — there is nothing to double
count.

---

## SECTION H — Reconstruction of $32.24/day

### H.1 The complete calculation (MODEL, seed 11, 90 days)

```
attempts (opportunities)        360        = 4/day × 90 days
wins (jobs accepted)            149        observed 41.39% of attempts
jobs completed                  149        the model completes every win
customers                       —          no customer entity exists

revenue            149 × $200 = $29,800.00
direct cost        149 × $102 = $15,198.00
acquisition        360 ×  $30 = $10,800.00
overhead            90 ×  $10 =    $900.00
                                 ----------
total cost                       $26,898.00
net profit                        $2,902.00
                                 ÷ 90 days
mean profit per day                  $32.24
```

Identity verified independently: `revenue − direct − acquisition −
overhead == oracle.profit_cents` → **True**.

### H.2 Distribution — the mean is misleading

| Statistic | 30 days | 60 days | 90 days |
|---|---:|---:|---:|
| Net profit | $1,686.00 | $2,686.00 | $2,902.00 |
| **Mean/day** | $56.20 | $44.76 | **$32.24** |
| **Median/day** | $66.00 | $66.00 | **$66.00** |
| Best day | $262.00 | $262.00 | $262.00 |
| Worst day | −$130.00 | −$130.00 | −$130.00 |
| Days ≥ $200 | 3 (10.0%) | 4 (6.7%) | 4 (4.4%) |
| **Loss-making days** | 10 (33.3%) | 24 (40.0%) | **42 (46.7%)** |
| p10 / p90 | −$120 / $252 | −$120 / $164 | −$130 / $164 |
| Std dev/day | $105.39 | $101.35 | $98.17 |
| **Min cumulative balance** | −$162.00 | −$162.00 | −$162.00 |
| Worst drawdown | −$194.00 | −$386.00 | −$386.00 |

**The median day earns $66 and the mean earns $32.24.** The gap is
the 46.7% of days that lose money — a zero-win day costs $130 (four
attempts at $30, plus $10 overhead) with no revenue. Reporting the
mean alone hides that nearly half of all days are negative.

### H.3 The reported figure is the 10th percentile of its own model

Across **200 seeds**, 90 days each:

| | Mean profit/day |
|---|---:|
| Minimum | $13.73 |
| **p10** | **$32.24** |
| Median | **$46.40** |
| Mean | $46.86 |
| p90 | $59.47 |
| Maximum | $71.44 |
| Std dev | $10.75 |

**Seed 11 — the one reported — sits at the 10th percentile.** The
model's central estimate is $46.40/day, not $32.24. The previous
report was pessimistic by roughly 30% and did not disclose that it was
quoting a single draw.

**The conclusion is unchanged and in fact strengthened: 0 of 200 seeds
reach $200/day.** The best seed reaches $71.44. The target is missed
under every draw of this model.

### H.4 Why the average was $32.24 — the limiting factor

Of the eight candidates the assignment lists:

| Candidate | Verdict |
|---|---|
| 1. Too few opportunities | **Contributing.** 4 attempts/day is the binding volume |
| 2. Low conversion | **No.** 41% is high for cold outreach; the model is generous |
| 3. Too few completed jobs | Symptom of 1 and 6, not a cause |
| 4. Excessive overhead | **No — the opposite.** $10/day is implausibly low |
| 5. High execution costs | **No.** Measured cost is $31–52/job against $102 assumed |
| 6. **Pricing** | **Yes, primary.** $200 − $102 = $98 gross per job, against $72.48 of acquisition loaded per win. Only **$25.52/job** survives |
| 7. Capacity restrictions | **Not in the model** — it has no capacity. In the real system this binds hard (§K) |
| 8. Combination | **Yes: pricing × attempt volume, with acquisition charged per attempt** |

The mechanism in one line: **each attempt costs $30 and returns
0.45 × $98 = $44.10 in expectation, so each attempt nets $14.10, and
four attempts a day net $56.40 less $10 overhead.** At the realised
41.39% the expectation falls to $40.56/attempt, netting $10.56, and
four of those less overhead is $32.24. That is the whole result.

### H.5 Status of the figure

**$32.24/day is a modelled projection, not an executed business
simulation.** No DeskPilot component produced it. It should be cited as
"the model's 10th-percentile outcome at an assumed 45% win rate", and
the model's median, $46.40, is the better central figure — while
noting §G.2, that correcting for owner supervision alone makes the
median negative too.

---

## SECTION I — Two-to-five skill comparison

### I.1 What exists

**No per-configuration business simulation was executed.** Of the
eighteen metrics the assignment requests per configuration, **zero**
are available as measured or simulated results.

What was MEASURED, at HEAD, this assignment:

| Configuration | Pipelines | `committed_ceiling` | Jobs per pipeline |
|---|---:|---:|---:|
| 2 skills | 2 | **6** | 3 |
| 3 skills | 3 | **9** | 3 |
| 4 skills | 4 | **12** | 3 |
| 5 skills | 5 | **15** | 3 |

`committed_ceiling = 3 × pipelines`, exactly.

### I.2 Projection from measured per-skill economics

**This is a PROJECTION over measured inputs, not a simulation.** It
combines each skill's measured delivery reliability from the 100-job
load run with the measured per-pipeline limit.

Configurations built **best-first** by measured reliability:

| Configuration | Pipes | Ceiling | Offered | Delivered | Delivery rate | Contribution |
|---|---:|---:|---:|---:|---:|---:|
| report + document | 2 | 6 | 40 | 40 | **100%** | $13,924 |
| + data-entry | 3 | 9 | 60 | 60 | **100%** | $20,886 |
| + spreadsheet | 4 | 12 | 80 | 80 | **100%** | $27,848 |
| + csv-cleanup | 5 | 15 | 100 | 87 | **87%** | $30,285 |

Same skills added **worst-first**:

| Configuration | Offered | Delivered | Delivery rate |
|---|---:|---:|---:|
| csv + spreadsheet | 40 | 27 | **68%** |
| + data-entry | 60 | 47 | 78% |
| + document | 80 | 67 | 84% |
| + report-builder | 100 | 87 | 87% |

### I.3 Did adding skills increase profitability?

**Contribution rises with skill count only because the harness offered
20 more jobs per added skill.** Per-job contribution is identical
($348) across all five, because the load fixture priced every skill the
same. **The comparison therefore measures offered volume, not skill
profitability**, and must not be read as "five skills earn more".

The one real signal: **adding csv-cleanup drops shop-wide delivery from
100% to 87%.** Which skills, and in which order, matters more than how
many.

---

## SECTION J — Individual skill profitability

All figures MEASURED. The 100-job load run priced every skill at $400
and the financial run at a common band structure, so **price,
acquisition and overhead do not vary by skill in any measured
evidence**. Differences below are in reliability and verification
burden only.

| Skill | Load: delivered | Financial: delivered | e2e mean | Checker mean | Checker ÷ worker | Revisions |
|---|---:|---:|---:|---:|---:|---:|
| report-builder | 20/20 | 10/20 | **51.7 ms** | **12.6 ms** | **1.79×** | 0.000 |
| document-processing | 20/20 | 10/20 | 65.0 ms | 22.8 ms | 2.58× | 0.000 |
| csv-cleanup | **7/20** | **3/20** | 88.3 ms | 17.3 ms | 1.96× | 0.000 |
| data-entry | 20/20 | 10/20 | 89.3 ms | 42.5 ms | 4.45× | 0.000 |
| spreadsheet-automation | 20/20 | 10/20 | **146.9 ms** | **73.7 ms** | **9.76×** | 0.000 |

### J.1 Rankings

| Basis | Ranking |
|---|---|
| **A. Net profit** | **Indistinguishable.** All five carry identical fixture price and cost; only csv-cleanup differs, by delivering less |
| **B. Profit per hour** | report-builder ≫ document-processing > csv-cleanup ≈ data-entry > spreadsheet-automation. All figures are absurd (millions/hour) because execution is milliseconds — see below |
| **C. Customer demand** | **Unknown for all five.** Zero demand evidence exists |
| **D. Reliability** | report-builder = document-processing = data-entry = spreadsheet (20/20) ≫ csv-cleanup (7/20) |
| **E. Risk-adjusted** | **report-builder**, then **document-processing** |

**Profit per hour is not a usable metric here.** Measured execution is
52–147 ms per job, giving notional throughputs of 34,000–120,000
jobs/hour on one lane. Machine time is not the constraint and ranking
on it is meaningless. `revenue-target.json` reaches the same conclusion
on representative inputs: the slowest skill still allows ~1,303
jobs/hour, while the target needs 1–2.

### J.2 Recommended initial two-skill configuration

> **PROVISIONAL: report-builder + document-processing.**

Grounds, all measured: highest delivery reliability (20/20 each);
lowest verification burden (1.79× and 2.58× against spreadsheet's
9.76×); fastest end-to-end (52 ms, 65 ms); zero revisions and zero
errors. This is the same pair the earlier audit recommended, now
supported by a second independent run.

**Labelled provisional** because no demand evidence exists for any
skill. The recommendation says which two DeskPilot executes most
reliably, not which two clients will buy.

**csv-cleanup should not be in a launch configuration**: it delivered
7/20 and 3/20 in two independent runs.

---

## SECTION K — Queue and standby performance

### K.1 The intended policy, verified

| Intended | Measured at HEAD |
|---|---|
| One active job per skill | Per-pipeline limit is **3**, not 1 |
| Three queued jobs per skill | **Confirmed**: `room: 3` per pipeline |
| Additional opportunities on standby | **Confirmed**: an unsubmitted order owes nothing (`owed_seconds` 0) |

### K.2 The capacity finding — measured at HEAD this assignment

Five orders, one per skill, driven through the real production path:

```
JOB CENSUS:              {'AWAITING_PAYMENT': 5}
OUTSTANDING RESERVATIONS: 5   (EXECUTE, 3600.0 units each)
owed_seconds:             18000.0
reconcile notes:          []
outstanding after reconcile: 5
owed_seconds after reconcile: 18000.0
real revenue cents:       0
```

**Every delivered job holds its capacity reservation indefinitely, and
reconciliation correctly declines to release it.**
`_reconcile_capacity` releases only jobs that are terminal or COMPLETE;
`AWAITING_PAYMENT` is neither, because the debt is real. The slot
returns only when a *verified* payment settles the job — and in
simulation no payment is ever real.

This reproduces, at HEAD, what two earlier measured runs recorded:

| Evidence | Result |
|---|---|
| `replenishment` (ceiling 4) | fill delivered 4; after-settle delivered **0**; settlement failed `AWAITING_PAYMENT: FailClosed` ×4. Verdict recorded: **"no capacity returned: the shop stays seized"** |
| `accelerated_24h` (ceiling 20) | 100 offered, **7 delivered in 24 h**, 80 REJECTED, 13 BLOCKED. Note recorded: *"the shop stops for good once the ceiling fills"* |
| `invoice_control` | every delivered job stops at invoice: *"INVOICE needs contracting detail the owner has not provided"* |

### K.3 The causal chain

```
owner has not recorded contracting details
   -> no invoice can be issued
   -> no payment can settle
   -> capacity reservation is never released
   -> the shop seizes after (3 x pipelines) jobs
   -> 7 jobs delivered in 24 hours, then nothing
```

**This is the most important business finding in the entire evidence
base, and the $32.24/day model is blind to it.** The model assumes
capacity recycles instantly and infinitely. The measured system stops
permanently after a dozen-odd jobs. Every projected figure in Section H
is therefore an *upper bound* that the real system cannot reach.

### K.4 Metrics requested

| Metric | Value | Class |
|---|---|---|
| Maximum queue depth | 3 per pipeline | MEASURED |
| Average queue depth | not recorded | ABSENT |
| Standby opportunities | unbounded; they owe nothing | MEASURED |
| Average waiting time | not recorded (harness is synchronous; `queue_wait_s` is 0 **by construction**, and the evidence file says so) | ABSENT |
| Jobs rejected due to capacity | 80 of 100 in the 24 h run | MEASURED |
| Jobs lost due to delays | not recorded | ABSENT |
| Deadline failures | not recorded | ABSENT |
| Overload events | 1 permanent seizure per run | MEASURED |
| Jobs recovered after crashes | 12/12 restart checks pass | MEASURED |

**Did queue limits reduce profitability? Yes, decisively** — but the
limit is not the defect. The defect is that slots never return. Raising
the queue limit would postpone the seizure, not prevent it, and the
evidence does not support recommending it.

---

## SECTION L — Customer service

**Status: largely ABSENT as business outcomes; the mechanisms are
tested.**

No simulation produced customer-service *counts*. What exists is
behavioural coverage in `test_customer_service_personas.py` (personas,
four corners, client isolation, scope creep, prompt injection, crash
and restart, loop and repetition) and four new tone tests added last
assignment.

| Metric | Value |
|---|---|
| Total simulated customer interactions | **ABSENT** as a count |
| Complaints / resolved / unresolved | ABSENT |
| Refund requests | ABSENT |
| Escalations | ABSENT as a count |
| Correct rejections | Verified behaviourally, not counted |
| Incorrect rejections / acceptances | **Never measured** |
| Repeat customer rate | **0 — no repeat client has ever existed** |

**Separating scripted from synthetic judgement:** every outcome is
scripted. `sentiment()` reads one message and reports a tone, and by
design nothing follows from it. There is no satisfaction model and no
satisfaction score anywhere in the system. Any "customer satisfaction"
figure in a configuration comparison would have to be invented, which
is why Section I reports none.

**Service failures that could cause real financial loss** — and these
are hypothetical, not observed: a deliverable that satisfies every
committed requirement but not what the client meant; a refund path that
has never been exercised against a real dispute; and an escalation
queue whose response time nobody has measured.

---

## SECTION M — Long-term contracts

**Verified: no recurring-contract results were produced by any model,
separate or otherwise.**

A repository-wide search for `retainer`, `recurring contract` and
`renewal` across tests and evidence returns exactly one file — the test
that *pins their absence*.

| Contract type | Contracts | Revenue | Costs | Profit | Cancellation | Renewal |
|---|---|---|---|---|---|---|
| Monthly retainers | **0** | — | — | — | — | — |
| Weekly recurring | **0** | — | — | — | — | — |
| Three-month | **0** | — | — | — | — | — |
| Six-month | **0** | — | — | — | — | — |
| Twelve-month | **0** | — | — | — | — | — |

The primitive does not exist in production: `Proposal` has no
`term_months`, `period`, `recurrence`, `renews_on`, `retainer_cents` or
`cancellation_notice_days`. `Step.IDENTIFY_RECURRING` is advice to a
person — *"paid; worth asking what else they need"*.

Nothing here is presented as implemented functionality, because nothing
is implemented.

---

## SECTION N — Payments and cash flow

### N.1 Measured

| Metric | Financial lifecycle run |
|---|---:|
| Simulated deposits | 20% fraction, verified in restart checks |
| Final invoices | 43 issued |
| Successful payments | 43 (`PAID`) |
| Failed payments | 0 |
| Late payments | **0 — no lag is modelled** |
| Partial payments | 0 in this run (the capability is tested separately) |
| Refunds | 0 |
| Chargebacks | 0 |
| Disputes | 0 |
| Outstanding balances | **$0.00** |
| **Real revenue** | **$0.00** |

Payment *pathology* is well covered as behaviour in
`tests_solvent/test_payments.py` (33 tests): overpayment refused as an
anomaly, underpayment recorded as partial not paid, refunds reduce
collected revenue, disputes withdraw it, duplicate events ignored,
replays refused, forged signatures refused. **None of it appears in any
business simulation**, so there are no cash-flow consequences to
report.

### N.2 Cash flow

**The model carries no cash-flow dimension** — it credits revenue on
the day a job is won, with no deposit, no collection lag and no
receivable. Its daily profit series is therefore also its cash series,
which is the most optimistic possible assumption.

Even so, the model shows a shortfall: **minimum cumulative balance
−$162.00**, reached early in all three periods, with a worst drawdown
of −$386.00 over 90 days. A business starting with no reserve would go
cash-negative in the first fortnight **in a model that assumes
instant payment**. With any realistic collection lag the shortfall is
larger and longer.

**Could the business be cash-negative while reporting accounting
profit? On this evidence, yes** — and the system already guards the
related error: `metrics` counts only *verified* collected revenue and
reports `actual_margin` as `None` rather than 0.0 when no real revenue
exists.

---

## SECTION O — 30/60/90-day results

Complete table at §H.2. Summary, all MODEL, seed 11:

| | 30 days | 60 days | 90 days |
|---|---:|---:|---:|
| Customers acquired | **n/a — no customer entity** | — | — |
| Jobs completed | 57 | 107 | 149 |
| Revenue | $11,400 | $21,400 | $29,800 |
| Total expenses | $9,714 | $18,714 | $26,898 |
| Net profit | $1,686 | $2,686 | $2,902 |
| Mean daily profit | $56.20 | $44.76 | $32.24 |
| Median daily profit | $66.00 | $66.00 | $66.00 |
| Best day | $262.00 | $262.00 | $262.00 |
| Worst day | −$130.00 | −$130.00 | −$130.00 |
| % days ≥ $200 | 10.0% | 6.7% | 4.4% |
| Max cash shortfall | −$162.00 | −$162.00 | −$162.00 |
| Customer churn | **n/a** | — | — |
| Operating overhead | $300 | $600 | $900 |

**Seed-to-seed variation** (200 seeds, 90 days) at §H.3: median
$46.40/day, range $13.73–$71.44, std dev $10.75. **No confidence
interval is offered for the single reported run**, because one run
supports none.

---

## SECTION P — Pricing sensitivity

### P.1 Conversion held constant at 45% — the previous report's basis

| Price | Median $/day (40 seeds) |
|---|---:|
| $150 | −$42.27 |
| $200 | $49.12 |
| $250 | $140.51 |
| $300 | $231.90 |
| $400 | $414.68 |
| $500 | $597.46 |

**This is the table behind the previous report's claim that "price is
the dominant lever". It holds conversion constant as price rises, which
is an assumption the previous report did not label.**

### P.2 Conversion falling with price — ASSUMPTION, explicitly labelled

Constant-elasticity: `win = 0.45 × (price/$200)^−e`.

| Price | e=0.5 win | $/day | e=1.0 win | $/day | e=1.5 win | $/day |
|---|---:|---:|---:|---:|---:|---:|
| $150 | 52.0% | −$28.13 | 60.0% | −$13.73 | 69.3% | $3.33 |
| $200 | 45.0% | $49.12 | 45.0% | $49.12 | 45.0% | $49.12 |
| $250 | 40.2% | $115.02 | 36.0% | $86.24 | 32.2% | $64.04 |
| $300 | 36.7% | $163.70 | 30.0% | $112.00 | 24.5% | **$69.10** |
| $400 | 31.8% | $257.40 | 22.5% | $141.51 | 15.9% | $62.04 |
| $500 | 28.5% | $338.76 | 18.0% | $170.71 | 11.4% | $57.94 |

**This overturns the previous report's headline recommendation.**

- At **e = 0.5**, price still works: the target is reached near $350.
- At **e = 1.0**, **no price reaches $200/day.** $500 tops out at
  $170.71.
- At **e = 1.5**, profit **peaks around $300 at $69.10 and then
  declines.** Raising price destroys the business.

The previous claim "**$300/job reaches $197.80/day**" is true only at
e = 0. I did not label that assumption, and I am correcting it here.

### P.3 Minimum combinations reaching $200/day

Required attempts per day, by price and win rate (closed form,
verified against the simulator at three points):

| Price | w=20% | w=30% | w=45% | w=60% | w=80% |
|---|---:|---:|---:|---:|---:|
| $150 | never | never | never | never | 25.0 |
| $200 | never | never | 14.9 | 7.3 | 4.3 |
| $250 | never | 14.6 | 5.7 | 3.6 | 2.4 |
| $300 | 21.9 | 7.1 | 3.6 | 2.4 | 1.6 |
| $400 | 7.1 | 3.5 | 2.0 | 1.4 | 1.0 |
| $500 | 4.2 | 2.3 | 1.4 | 1.0 | 0.7 |

**"never" means each attempt loses money however many are made** — no
volume rescues it.

Verified against the simulator:

| Combination | Measured median |
|---|---:|
| $300 @ 45% @ 5 attempts/day | $277.80/day |
| $250 @ 45% @ 8 attempts/day | $287.73/day |
| $200 @ 60% @ 12 attempts/day | $332.33/day |

**Break-even acquisition spend** — the most actionable number here:

| Price @ win rate | Max sustainable $/attempt | Currently |
|---|---:|---:|
| $200 @ 20% | $19.60 | **$30.00 — loss-making** |
| $200 @ 45% | $44.10 | $30.00 |
| $300 @ 20% | $39.60 | $30.00 |
| $300 @ 45% | $89.10 | $30.00 |

**None of these combinations is claimed to be achievable in the real
market.** No bid has ever been submitted.

---

## SECTION Q — Independent financial reconciliation

### Q.1 The model

Recomputed from the underlying day records, independently of the
`Oracle` that produced the original figure:

```
revenue − direct − acquisition − overhead == reported profit   -> True
$29,800 − $15,198 − $10,800 − $900 == $2,902                  -> True
```

**No discrepancy.** The arithmetic is correct.

### Q.2 The measured runs

The financial lifecycle run carries its own reconciliation block, which
I re-verified:

```
invoiced_minus_collected_cents: 0
profit_equals_collected_minus_cost: true
profit_cents_reported: 902362
collected_minus_cost_cents:  902362
real_revenue_is_zero: true
```

**No discrepancy.**

### Q.3 The three questions asked

| Question | Answer |
|---|---|
| Were acquisition costs charged to unsuccessful opportunities? | **Yes.** $30 is charged per *attempt*, not per win. 211 of 360 attempts won nothing and still cost $6,330 |
| Was owner supervision included? | **No.** Excluded entirely. At the audit's own $25/job this is $3,725 over 90 days — §G.2 |
| Was recurring overhead allocated correctly? | **There is nothing to allocate.** One undifferentiated $10/day, not apportioned to skills, jobs or customers |
| Double counting? | **None found.** Each cost appears once. The risk here is omission, not duplication |

### Q.4 The reconciliation that matters

Both books balance, and **both are balancing zero**. `real_revenue` is
$0.00 in every measured run, by the system's own design: `Ledger`
excludes any payment whose verification method carries the simulated
prefix. The reconciliation proves the arithmetic is sound. It proves
nothing whatsoever about earnings.

---

## SECTION R — Missing variables and limitations

### R.1 Proven gaps (measured or verified absent)

| Variable | Status |
|---|---|
| Customer lifetime value | **No customer entity exists.** Cannot be computed |
| CAC payback period | Computable only per job ($72.48); no repeat revenue to pay it back |
| Repeat business | **Zero.** No repeat client has ever existed |
| Customer concentration | `metrics` tracks it; no real client to concentrate on |
| Owner time | **Excluded from every figure.** §G.2 |
| Cash reserves | Not modelled. Min balance −$162 assumes none |
| Revision burden | Measured at 0.000 — a fixture property, not a forecast |
| Failed-job costs | Not modelled. The model completes every win |
| Capacity constraints | **Measured and severe** — §K. Absent from the model |
| Refund exposure | Mechanism tested; never simulated in a business run |
| Contract profitability | No contract primitive beyond single jobs |
| Sales-cycle length | **Never measured.** The model wins or loses instantly |
| Lead quality | No lead entity |
| Demand uncertainty | **Total.** Zero bids, zero wins, zero payments |

### R.2 Hypothetical risks (reasoned, not observed)

- **AI usage variability.** Costs are assumed flat per job; real token
  usage varies with input size, and three of five skills could not be
  scaled in the benchmark because their formats are zip containers,
  so those throughput figures overstate.
- **API price changes.** No sensitivity anywhere to provider pricing.
- **Collection lag and bad debt.** Assumed zero.
- **Client-meaning failures.** A deliverable can satisfy every
  committed requirement and still be wrong for the client.

### R.3 Limitations of this report

Every figure is from a simulation, a model or a projection. The most
important limitation is the one the system states about itself: **no
bid has ever been submitted, none won, none paid.**

---

## SECTION S — Top five findings

**1. The headline simulations were not business simulations.** The
30/60/90-day runs are a six-parameter arithmetic model with no
DeskPilot code, no customers, no jobs and no skills. The 2/3/4/5-skill
configurations were never run as business simulations at all — only
configuration plumbing was tested.

**2. The business seizes after about a dozen jobs, and the model
cannot see it.** Measured at HEAD: delivered jobs sit in
`AWAITING_PAYMENT` holding their capacity reservations; reconciliation
correctly refuses to release them; slots return only on verified
settlement, which never occurs because no invoice can be issued without
owner contracting details. An earlier 24-hour run delivered **7 jobs
and then stopped permanently**. Every figure in Section H is an upper
bound the real system cannot reach.

**3. The 45% win rate is an assumption the production system
deliberately refuses to make.** `revenuemodel.py` holds `bid_win_rate`
as UNKNOWN — *"no bid has ever been submitted"* — and returns `None`
rather than invent one. The simulation defaulted it to 0.45 and
produced a profit figure from it.

**4. "Price is the dominant lever" was conditional on an unlabelled
assumption.** Holding conversion constant, $500 yields $597/day. With
unit elasticity it yields $170.71 and **no price reaches $200/day**; at
e = 1.5 profit peaks near $300 and then falls. The earlier
recommendation to raise prices is not supported without a demand curve
nobody has measured.

**5. $32.24/day was the 10th percentile of a single seed, and owner
time was excluded.** The model's median across 200 seeds is $46.40/day.
But adding owner supervision at the programme's own $25/job turns the
90-day result from +$32.24/day into **−$9.14/day**. The two corrections
point in opposite directions and the second is larger.

---

## SECTION T — Recommended next business decisions

Ordered by evidence strength, not ambition.

**1. Record the owner contracting details.** This is a configuration
entry, not development. It is the first link in the chain that seizes
the shop: no contracting details → no invoice → no settlement → no
capacity returned → 7 jobs a day and then nothing. Nothing else in this
report matters until jobs can settle.

**2. Re-measure the 24-hour throughput once settlement works.** The
accelerated run should be repeated after (1). Until then the true
jobs-per-day ceiling is unknown, and it is the number that decides
whether $200/day is reachable at any price.

**3. Launch on report-builder and document-processing only.** Measured
20/20 delivery each, lowest verification burden (1.79× and 2.58×),
fastest end to end. Leave csv-cleanup out: 7/20 and 3/20 in two
independent runs.

**4. Record the real cost lines before quoting anything.** Owner
supervision, platform fee, payment fee and fixed monthly overhead are
all unrecorded, which is not the same as zero. On the measured
evidence, supervision alone is larger than the entire modelled profit.

**5. Get one real quote in front of one real prospect.** Every
classification in this report that says SIMULATED or UNPROVEN turns on
a single fact — no bid has ever been submitted. One real response is
worth more than another 200 seeds.

**Do not** raise prices on the strength of §P.1, and **do not** raise
queue limits on the strength of §K. Neither is supported once the
unlabelled assumption and the seizure are accounted for.

---

## Advisory review

| Reviewer | Availability verified | Mode |
|---|---|---|
| iFixAi `4.3.0` | **Yes — natively importable** in its own venv | Not applicable to this assignment |
| K-LEAN `1.0.0b13` | Verified unchanged; 8 `async` review entry points over `httpx` to a completions endpoint | **SOURCE-DERIVED** |

**iFixAi was available and was not run, and no native result is
claimed for it here.** It audits recorded *action trajectories* against
a permission policy — actors, approvals, delegations, budgets. It has
no capability bearing on financial reconciliation or business
analytics. Running it on a forensic accounting report would produce a
verdict about something this report is not. It was run natively last
assignment on the connector trajectory (CLEAR, 0 violations, 5/5
injected controls caught) and that result stands.

**K-LEAN remains source-derived**: every review entry point requires
model API access and there is no offline path, so no native run is
claimed.

In place of an advisory verdict on the arithmetic, the reconciliation
in §Q was performed by independent recomputation from the underlying
day records, separately from the `Oracle` that produced the original
figures. Neither reviewer has decision authority and neither was given
any.

---

## MANDATORY FINAL ANSWERS

**1. How many simulated customers did DeskPilot acquire?**
**None, and the question cannot be answered from the simulations as
run.** The period model has no customer entity. The measured runs used
synthetic client references, not customers: 100 contracts in the
financial run, 100 jobs in the load run. Real customers: **0**.

**2. How many jobs did it complete?**
**Model:** 149 over 90 days (57 at 30 days, 107 at 60).
**Measured:** 43 of 100 delivered in the financial lifecycle run; 87 of
100 in the busy-shop load run; **7 in the 24-hour accelerated run
before the shop seized**.

**3. How much simulated revenue did it generate?**
**Model:** $29,800 over 90 days. **Measured:** $10,375 collected in the
financial run (invoiced = collected, zero receivables); $34,800 quoted
in the load run, which the same file states was **never invoiced**.
**Real revenue in every run: $0.00.**

**4. What was the total simulated overhead?**
**$900 over 90 days** — and that is the entire operating overhead in
the model. It excludes owner supervision, AI subscriptions, storage,
monitoring, email and any fixed monthly cost.

**5. What was the overhead per day?**
**$10.00.** Implausibly low for a business running a VPS, AI APIs and
an owner's attention.

**6. What was the overhead per completed job?**
**$6.04** (= $900 ÷ 149). Adding acquisition gives $78.52/job; adding
direct cost gives $180.52/job.

**7. What was the simulated net profit?**
**$2,902 over 90 days = $32.24/day** at seed 11. The model's median
across 200 seeds is **$46.40/day**. Correcting for owner supervision at
$25/job: **−$9.14/day**.

**8. Why was profit only $32.24/day?**
Four attempts a day at $30 each cost $120. At the realised 41.39% win
rate they return 1.66 jobs × $98 gross = $162.24. Less $10 overhead
leaves $32.24. **The mechanism is that acquisition is charged per
attempt while margin is earned only per win**, so at $200 and ~41% each
attempt nets just $10.56. Secondarily, seed 11 is the 10th percentile —
the model's own central figure is $46.40.

**9. Which of the five skills was most profitable?**
**Indistinguishable on the measured evidence** — every skill carried
the same fixture price and cost, so contribution per delivered job is
identical ($348). On reliability and verification burden, which are the
only real differences measured, **report-builder** is strongest and
**csv-cleanup** clearly weakest (7/20 and 3/20 delivered).

**10. Which two-skill configuration performed best?**
**No two-skill configuration was ever simulated.** On measured per-skill
evidence the strongest pair is **report-builder + document-processing**
— provisional, because no demand evidence exists.

**11. Did three, four or five skills improve profit?**
**Unknown — never simulated.** The projection in §I shows contribution
rising with skill count only because 20 more jobs were offered per
skill added. The one real signal is negative: **adding csv-cleanup
drops shop-wide delivery from 100% to 87%.**

**12. Was the business demand-limited, capacity-limited or
cost-limited?**
**Capacity-limited and conversion-limited in the measured system;
demand-limited absolutely in reality; not cost-limited.** The 24-hour
run delivered 7 of 100 offered and then stopped permanently. 57 of 100
contracts never reached delivery. Operating cost is 13% of invoiced.
And no bid has ever been submitted to anyone.

**13. What job price and conversion rate would be required for
$200/day?**
At four attempts a day: **$500 @ 45%**, or **$400 @ 60%**. At higher
volume: **$300 @ 45% @ 3.6 attempts/day**, or **$250 @ 45% @ 5.7/day**.
**At $200 with a win rate at or below 30%, no volume suffices** — each
attempt loses money. All of this assumes acquisition stays at
$30/attempt; the break-even ceiling at $200 @ 45% is $44.10.

**14. What financial assumptions remain unproven?**
Every one. Specifically: the 45% win rate (an input nobody observed and
production refuses to default); the $200 price (never quoted to
anyone); the $102 direct cost (measured at $31–52, so conservative);
the $30 acquisition cost (never spent); the $10/day overhead (excludes
owner time); zero revisions (a fixture property); zero collection lag;
zero bad debt; and instant, infinite capacity recycling — which the
measured system contradicts outright.

**15. What is the smallest set of changes most likely to improve
profitability?**
**One configuration entry and one decision.** Record the owner
contracting details so invoices can issue, settlements can clear and
capacity can return — this alone moves the ceiling from 7 jobs/day to
whatever the machine can actually sustain. Then launch on
report-builder and document-processing only. No code change is required
for either.
