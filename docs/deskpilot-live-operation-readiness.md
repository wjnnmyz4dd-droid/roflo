# DeskPilot: from a governed pipeline to live operation

Frozen at `0609ff98` for the sealed exam. Repairs after the exam are listed
with the first-run score they came from; the first-run score is not revised.

Scope of this pass: verify the six marketplaces again, decide whether to
grow the skill set, make claiming a governed act on the existing authority
rather than a second agent, and establish what it would take to run
unattended. No deployment, no VPS, no live account, no real money.

---

## C. Skill expansion

### Considered

Eight candidates are recorded in `solvent/skillcandidates.py`, each with a
verifiability class, because verifiability decides whether a skill can be
certified at all:

| candidate | verifiability | why that class |
|---|---|---|
| `pdf-table-extraction` | DETERMINISTIC | the table either matches the page or it does not |
| `file-format-conversion` | DETERMINISTIC | round-trip comparison answers it |
| `data-validation` | DETERMINISTIC | a rule either holds on a row or it does not |
| `invoice-generation` | DETERMINISTIC | arithmetic and a template |
| `web-scraping` | EXTERNAL_FACT | correctness depends on a page nobody controls |
| `copywriting` | JUDGEMENT | no checkable right answer |
| `data-analysis-and-insight` | JUDGEMENT | the insight is the deliverable |
| `translation` | JUDGEMENT | fluency is not machine-checkable here |

### Implemented

**None.** The rule the owner set is that a skill is built when there is
evidence of paying demand, and the threshold is three real opportunities
for it. The evaluation run at this commit says:

```
Build nothing yet. 4 candidate(s) could be certified if demand appeared;
none has reached 3 real opportunities, and no real opportunity has been
recorded at all.
```

The four are the deterministic ones. The three JUDGEMENT candidates could
not be certified even with demand, because DeskPilot's certification
requires a verifier that can fail the work, and there is no such verifier
for "is this good copy". `web-scraping` is excluded for a second reason:
its correctness depends on sites whose terms DeskPilot would have to
honour individually.

### Tested and certified

The five existing skills remain the whole certified set: `csv-cleanup`,
`spreadsheet-automation`, `document-processing`, `data-entry`,
`report-builder`. All five are deterministic and call no model, which is
why they can be verified at T1 without a second opinion and why their
operating cost is CPU rather than tokens.

Expanding here would have been the easy thing to report and the wrong
thing to do: a sixth skill with no customer is not progress toward a first
customer.

---

## D. Testing

### Totals

| | solvent | roflo | combined |
|---|---|---|---|
| **Total** | 3,843 | 94 | **3,937** |
| **Passed** | 3,842 | 94 | **3,936** |
| **Failed** | 0 | 0 | **0** |
| **Skipped** | 1 | 0 | **1** |

The roflo suite had not been run in this container at all: it needs
pytest, pytest was not installed, and the loader error that produced
looked like six missing modules rather than a result. Installed it and
found a real regression — `test_registry_lists_all_backends` had been
failing since the `cloud` backend was added in an earlier pass, because
that test is the registry's inventory and nobody had added the new entry
to it. Fixed. MetaTrader 5 coverage is inside that suite and passes.

The single skip is `tests_solvent.test_installer_packaged_entrypoint`,
and it skips for a reason worth keeping: DeskPilot's egress guard is
installed in the running interpreter, so no subprocess can be started.
The guard is deliberately irreversible and is not worked around. That
suite runs on its own:

```
python3 -m unittest tests_solvent.test_installer_packaged_entrypoint
```

```
Ran 3843 tests in 369.897s
OK (skipped=1)
```

One more defect belongs here, in the exam rather than the code: the
first full regression after the exam was written **failed**, because the
exam read its seed from an environment variable and a regression run
sets none. An exam that only runs when somebody remembers a variable is
an exam that stops running. The seed is now committed, derived in the
file from the frozen commit hash, with the variable left as an override;
the assertion checks the committed seed still matches that derivation,
so the sealed exam cannot quietly become a different exam.

### Mutation

Fifty-three mutants, each breaking one load-bearing control. Twelve are
new this pass: four on claiming, four on the operating loop, two on the
terms gates, two on what reaches the append-only log.

```
52/53 killed  (0 survived, 1 not applied or harness)
```

**No survivors.** The one `NOT_APPLIED` was my error, not a result: the
anchor text for `a-failing-transport-logs-its-credential` appeared twice
in the file, so the mutant could not be applied uniquely. A
`NOT_APPLIED` is never counted as a kill. Re-pointed at a unique snippet
and run again:

```
1/1 killed  (0 survived, 0 not applied or harness)
```

So **53/53**, with the correction stated rather than folded into the
batch number.

Each mutant is applied once, the designated test must *newly* fail, and
the failure must disappear on revert. The harness deletes every
`__pycache__` and runs with `-B`: CPython compares source mtimes at
one-second granularity, so without that a mutant applied and reverted
within the same second can be masked by a stale `.pyc` — which makes the
report wrong in both directions at once.

### Unseen exam

Sixty sealed cases in five families, drawn from a seed derived from the
frozen commit hash itself (`0x0609ff98` = 101318552) so the seed could not
have been chosen to flatter the result. The grader lives in
`tests_solvent/lifecycleexam.py` and computes every expected answer from
the rules; nothing under test is asked what the right answer is.

**First run: 45/60.** That is the score.

Of the fifteen disagreements, four were real defects and eleven were
faults in my exam:

| case(s) | disagreement | adjudication |
|---|---|---|
| `bounds-02` | owner set 0 jobs per run; the loop ran 1 | **code defect** |
| `triage-06/10/11` | posting naming no budget declined as "below the $40.00 minimum" | **code defect** |
| `revenue-04`…`11` (8) | answers were running totals | **exam bug** — one ledger shared across twelve cases |
| `triage-08` | expected DECLINE for an offsite-payment posting | **exam wrong** — a risk reaches a person; `test_08_a_suspicious_payment_arrangement_reaches_a_person` has asserted HOLD since long before this exam |
| `claim-03` | expected a tier refusal at $1,500 | **exam wrong** — C_HIGH needs T2, and T2 *is* producible wherever an owner channel exists |
| `permission-07` | asked about a fixture registered under a marketplace's name | **exam wrong** — and adjudicating it found a real defect anyway (below) |

**Retest after repair: 60/60.** Same seed, same cases.

### Defects found this pass

Six, all repaired, all now pinned by a mutant. Each is listed with what
found it, because "the exam found it" and "I found it while arguing with
the exam" are different claims:

| # | defect | found by |
|---|---|---|
| 1 | credential in the append-only attempt log | failure injection |
| 2 | redactor keyed on field names, not shapes | following finding 1 |
| 3 | owner's zero jobs widened to one | sealed exam (`bounds-02`) |
| 4 | zero spend ceiling meant no ceiling | reading the code around finding 3 |
| 5 | polling ignored the robots.txt finding | adjudicating `permission-07` |
| 6 | unstated price declined as too cheap | sealed exam (`triage-06/10/11`) |

**1. A failing transport wrote its credential into the append-only log.**
An HTTP client's exception quotes the request it was making, and the
request carries the source's token. `submission_attempts` is append-only,
so a secret that lands there cannot be scrubbed — only read again. Both
call sites now redact, as discovery already did for its parse failures.

**2. The redactor knew field names, not credentials.** Its docstring says
it keys on what a secret looks like "because the field a secret arrives in
is whatever the caller happened to call it" — but its catch-all rule was a
list of field names. `?token=` matched; `?key=` did not. Stripe was the
only issuer with a shape rule. Added shapes for GitHub, Slack, AWS,
Anthropic, Google and JWTs, plus a labelled rule for
`key|auth|credential|sig|session` that fires only when the value looks
like a credential, so `key=customer_id` still survives and the diagnostic
is still worth reading.

**3. An owner's zero jobs per run became one.** `owner_bounds` did
`max(1, int(... or 1))`, which turned an explicit 0 into 1 twice over. The
one thing that class exists to prevent is a limit being widened, and it
was widening the owner's own. A zero now stops the run and says so.

**4. A spend ceiling of zero meant no ceiling.** `Bounds` documents its
default as "no spending"; the loop read 0 as a sentinel for unlimited.
Because `effective_bounds` takes the *minimum* of owner and caller, an
owner who had set no ceiling erased the caller's. Zero now means zero, and
a run that commits nothing is still free to continue.

**5. Polling ignored the platform's own answer.** The robots.txt findings
gated `source_suitability`, which decides what the *interface shows*. They
did not gate `_may_poll`, which decides what the code *does* —
`DISCOVER` is not in `GOVERNED_BY_TERMS`, so `may()` did not cover it
either. A catalogued source whose robots.txt disallows automated reading
could be polled as long as somebody had marked it PERMITTED. Both now ask.

**6. A posting that named no price was declined as too cheap.** Treating
an unstated budget as `$0` produced a refusal that states a figure the
posting never gave, and on a real board a large share of listings give
none. Unknown revenue is now a HOLD — a person asks what the budget is.
Deliberately keyed on the quoted figure rather than the net: a stated
price eaten by platform fees really does earn nothing, and that stays a
decline.

Two of the tests written for finding 1 **passed before the fix existed**,
because the Action Gate refused before the transport ever ran. They were
proving nothing. Both now assert the transport was reached first.

### Failure-recovery findings

| injected failure | behaviour |
|---|---|
| transport raises mid-accept | records `UNKNOWN_REMOTE_STATE`, surfaces the attempt in `unresolved()`, and **refuses to try again by itself** — an obligation nobody can see is worse than none |
| source answers with nonsense (`{}`, a login page, `None`, `[]`, an unknown status) | none of six bodies was read as an accepted contract |
| cycle raises inside the operating loop | the exception propagates; the loop does not retry a cycle that raised |
| cycle returns a malformed result | counted as a failure, not as a success with zero revenue |
| policy unreadable | the run refuses; **zero** jobs run while it cannot read its own limits |
| permission withdrawn between bid and acceptance | refused at the first gate |
| halt engaged mid-run | the next iteration does not start |

Every run records why it stopped, under every one of these.

---

## E. Commercial viability

Real and simulated are reported separately and are not combined anywhere
in this section.

### Real

| | |
|---|---|
| **Real opportunities discovered** | **0** |
| **Real collected revenue** | **0 cents** |
| **Actual operating costs** | **0 cents** |

No marketplace is an active source; none has been polled; no proposal has
been sent; no contract has been accepted; no invoice has been issued to a
real client; no money has moved in either direction. The actual operating
cost is zero because nothing was spent: the five skills call no model, no
paid API was invoked, no platform fee was incurred. The only resource
consumed was local CPU.

`real_revenue_cents()` excludes anything whose verification method starts
with `SIMULATED:`, so the zero above is structural rather than an absence
of records.

### Simulated

| | |
|---|---|
| **Simulated opportunities** | 360 scenarios execute at this commit: 210 lifecycle (new this pass) + 60 sealed exam (new this pass) + 90 Finder triage (earlier pass, still green) |
| **Simulated invoiced** | $400.00 per benchmark cycle |
| **Simulated operating cost** | $51.90 (10% platform fee, 2.9% + $0.30 payment fee) |
| **Simulated contribution** | $348.10 |

The $51.90 is modelled fees on a simulated $400 invoice, not money spent.
Compute cost on the local route is $0.0000; the same advisory call priced
against a cloud provider is $2.70, which is why routing matters only if a
skill ever needs a model — none of the five does.

Measured, not simulated: a `csv-cleanup` execution has a median of 22 ms
over 500 rows, and twelve such jobs run in 0.61 s. That is throughput
evidence. It is not revenue evidence.

---

## F. Blockers to live operation

Each is a thing that must be true before DeskPilot can earn, with what is
missing stated exactly.

1. **No marketplace permits automated access.** Of the six, Upwork,
   Fiverr and Freelancer forbid automated reading of listings in their own
   robots.txt (read 2026-10-13); PeoplePerHour returned nothing and Guru
   returned 403, so neither has a finding at all, and an unread clause is
   not permission. Contra permits crawling its public pages — and keeps
   its Opportunities board behind Contra Pro, a paid subscription, which
   makes reading it `DISCOVER_AUTHENTICATED` **and** `SPEND_MONEY`, two
   permissions nobody has granted.
2. **No contracting identity.** Invoices cannot be issued, so no amount of
   completed work becomes collectable.
3. **No payment rail.** Every payment in the system is `SIMULATED:`, and
   the ledger is built so those can never be counted.
4. **No credentials for any source.** Authenticated discovery, proposals,
   acceptance and platform delivery are all unreachable without them, and
   they are the owner's to obtain.
5. **The storefront is unpublished.** Five offers exist with scopes,
   intake questions, revision policies and turnaround estimates;
   `published=False`, and publishing needs an account the owner creates.
6. **T3 is unproducible.** Work above $5,000 needs it. Nothing here makes
   it, so such work is refused at qualification rather than discovered at
   delivery.
7. **Continuous operation needs ceilings the owner has not set.** It is
   off by default, and now that zero means zero, a run with no configured
   spend ceiling may commit nothing.

None of these is a code defect. Every one of them is an authorization,
an identity, or a credential that only the owner can supply.

---

## G. Classification

### READY WITH RESTRICTIONS

Not READY FOR BOUNDED AUTONOMOUS OPERATION. That classification requires
actual evidence for every operational and authorization gate, and four of
them have none: there is no permitted route to a single real opportunity,
no contracting identity, no payment rail, and no credential for any
source. Simulation cannot establish any of those, and 360 simulated
opportunities do not become evidence by being numerous.

Not READY FOR SUPERVISED LIVE PILOT either, for one plain reason: a
supervised live pilot needs something live to supervise. There is no
authorized channel through which DeskPilot can currently reach a paying
customer. The restriction is not a safety margin — it is the absence of a
route.

Not BLOCKED, because the pipeline itself is demonstrably sound end to end
under simulation: 210 lifecycle scenarios and 60 sealed cases agree with
the rules, every gate refuses for the right reason, and the six defects
this pass found were found by execution and are each pinned by a mutant.

**What the restrictions are.** DeskPilot may: execute and verify work at
C_LOW and C_MED, accept owner-entered jobs, run its storefront unpublished,
and run its loop with ceilings the owner sets. DeskPilot may not: poll any
marketplace, bid, accept a contract, deliver through a platform, issue an
invoice, or count a payment.

**The shortest legitimate path to the first paying customer** has not
changed and has nothing to do with marketplace automation:

1. The owner registers a business identity so an invoice can exist.
2. The owner connects one payment rail.
3. The owner takes one job through the owner-entered channel — the one
   path needing no platform's permission — and DeskPilot executes,
   verifies and delivers it under the existing gates.
4. That one real job produces the first real opportunity record, the
   first real revenue, and the first real evidence that any of this works
   outside a fixture.

Everything in this pass makes step 3 safe. Nothing in it can substitute
for steps 1 and 2.

---

## Advisory reviews

Both repositories were cloned and read at their current commits. Neither
can be run natively here, for different reasons, and both reviews are
labelled accordingly.

### K-LEAN — partially native

`calinfaja/k-lean@c8a06e9`. K-LEAN brokers second opinions from other
providers (NanoGPT, OpenRouter); with no credentials, its review path
cannot run. Its `scan_secrets` tool is pure local Python, so **that part
was executed natively** against this repository:

```
solvent/   74 files scanned — no potential secrets found
roflo/     15 files — none
tools/      3 files — none
deploy/    10 files — none
windows/   61 files — none
```

The only hits anywhere are deliberate fixtures in `tests_solvent/`.

Its security-auditor agent's own pattern list — `AKIA[0-9A-Z]{16}`,
`gh[pousr]_[a-zA-Z0-9]{36,}`, Bearer, Basic, PEM — independently
corroborates finding 2 above: shape rules for named issuers are the thing
a field-name list cannot replace. Solvent's redactor now carries them.

### iFixAi — source-derived, no grade

`ifixai-ai/ifixai@48cd7b3`. Two reasons it cannot be run natively, and the
first matters more than the second:

1. iFixAi inspects an **LLM agent's** behaviour through a provider
   capability interface. DeskPilot's five skills call no model. There is
   no agent here for it to inspect.
2. Its own methodology says "an agent should not grade itself" and refuses
   without a judge from a different provider. There are no provider
   credentials here at all.

**No grade is issued**, which is iFixAi's own rule rather than a
concession. Its inspection categories were applied as a review lens, and
they name this pass's findings precisely:

| inspection | reading |
|---|---|
| **B06** Explicit Uncertainty Signalling | failed, now repaired: the Finder sounded certain about a budget the posting never gave (finding 6) |
| **B04** Deterministic Override Coverage | failed, now repaired: the owner's `0` silently became `1` (finding 3) |
| **B30** Malicious Deployer Rules — "refuses rules that quietly switch off its own protections" | failed, now repaired: a zero ceiling switched off the spend limit entirely (finding 4) |
| **B02** Non-LLM Governance Layer | passes by construction: every gate is deterministic code, not an instruction to a model |
| **B03** Auditability Coverage | passes: every refusal records its reason, including the new poll refusal |
| **B05** Source Provenance | passes: `TermsFinding` refuses to exist without a citation |
| **B12** Prompt Injection Blocking | passes: a posting issuing instructions reaches a person and commands nothing |

Both reviews are advisory. Neither can override an owner approval, a
commercial gate, independent verification, grading, or a promotion
decision, and neither did.
