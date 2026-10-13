# DeskPilot: six platforms, and the terms that decide what work is worth

Three objectives this pass: replace the old source list with the six
candidates, integrate each one as far as legitimate access permits, and
resolve the four commercial configuration blockers. No deployment, no
VPS, no live account, no real money, simulation left on throughout.

---

## A. The six-platform interface

The catalogue was thirteen entries. It is nine now: the six marketplaces,
plus the owner's own three channels, which are not marketplaces and are
the only route that works today.

| retired | was | why it went |
|---|---|---|
| SAM.gov | public procurement, official API, needed a key | not a marketplace, and federal contracting is not what the five skills sell |
| Grants.gov | public procurement, official API, no key | grant funding is applied for, not sold; already excluded from the active interface by work type |
| State / local procurement portal | generic placeholder | never a real source, and a shape is not a platform |
| RFP / bid board | generic placeholder | same |

**What that cost, stated plainly.** SAM.gov and Grants.gov were the only
two catalogued sources with researched, working, official API access, and
Grants.gov needed no credential at all. Their adapters were real and
tested against a recorded live response. Nothing commercially reachable
was lost — `source_suitability` already refused both as out of scope for
client work, and a test has asserted since before this pass that a
healthy Grants.gov feed is never an active source — but the repository is
genuinely smaller in capability, and `git log` is where those adapters
are if they are ever wanted back.

**Removing them opened a hole, which is the interesting part.** Both
terms gates answered "no catalogue entry" with *yes*, reasoning that a
source nobody wrote terms for should not be held to a standard nobody
wrote down. That is right for an in-process fixture and for work the
owner typed in: neither contacts anybody. It is wrong the moment a source
has a host — and it meant deleting an entry would have made its source
*more* permitted than it was before. A real host with no catalogue entry
now fails closed, in both gates and in the poll path.

**And a second rule turned out never to have been enforced.** A fetching
source must declare a minimum poll interval. The rate limiter read a
missing interval as permission to poll freely, with a comment saying
exactly why it should not ("a gap in the registration rather than
permission to poll freely"). The test guarding it asserted that the two
shipped adapters each declared one — a property of those two classes, not
a rule. Retiring them would have left that test passing over an empty
list forever.

### What each platform permits

`solvent marketplaces` prints this, derived from the recorded findings
every time rather than stored, so a row cannot drift from the research
behind it.

| platform | public discovery | everything else | status |
|---|---|---|---|
| Upwork | **forbidden** (robots.txt, read 2026-10-13) | unread | UNVERIFIED |
| Fiverr | **forbidden** (robots.txt, read 2026-10-13) | unread | UNVERIFIED |
| Freelancer.com | **forbidden** (robots.txt, read 2026-10-13) | unread | UNVERIFIED |
| PeoplePerHour | unread — the fetch returned nothing | unread | UNVERIFIED |
| Guru | unread — the fetch returned 403 | unread | UNVERIFIED |
| Contra | **permitted** (robots.txt, read 2026-10-13) | unread | UNVERIFIED |

Seven activities are tracked separately for each, because permission for
one is not permission for another. Six of the seven are unread on every
platform, and unread is not permitted.

**So what was integrated?** The honest answer is: nothing automated, on
any of them, and the interface now says so per activity instead of per
platform. Contra's crawl permission is real and covers pages that do not
contain the work — its Opportunities board is behind Contra Pro, a paid
subscription, which makes reading it `DISCOVER_AUTHENTICATED` **and**
`SPEND_MONEY`. That is precisely the conflation the seven separate
permissions exist to prevent, and calling it an integration would be the
mistake this whole structure is built to stop.

What *is* available on all six is manual import: the owner reads a
listing and types it in. No automated request, nobody's permission
needed, and it goes through exactly the same qualification, pricing,
execution, verification and delivery as anything else.

---

## B. Payment readiness

| | state |
|---|---|
| **Invoice generation** | **built, and now refuses rather than degrades** |
| **Payment-provider configuration** | built, unconfigured — Stripe rail implemented, no key |
| **Payment confirmation** | built — signed webhook, replay-protected, no live event ever seen |
| **Reconciliation** | built — collected revenue excludes every `SIMULATED:` rail |
| **Refund / dispute handling** | built — REFUNDED reduces collected revenue, DISPUTED withdraws it |
| **Actual live-payment verification** | **none. No real payment has ever been processed** |

### The defect found here

`_write_invoice` printed `(contracting identity not recorded)` into the
From line and carried on. That produces a document that looks like a
bill, names nobody to pay, cannot be sent — and, worse, lets the pipeline
record an invoice stage it completed. Policy already knew which fields an
invoice needs (`legal_name`, `address`, `email`) and already had the
question `may_contract_for("INVOICE")`. Nothing asked it.

It asks now. Without an issuer the invoice refuses, and the business
cycle stops cleanly at `invoice` with the reason recorded rather than
raising: the work really was delivered and verified, and that is worth
keeping. Forty tests across twelve suites failed when this landed, every
one of them a rig that had been invoicing on behalf of nobody.

---

## C. The four configuration blockers

Identified from the system's own enumeration — `Finder.configuration_gaps()`
returns exactly these four on an unconfigured installation.

### 1. No minimum project fee

* **Exact blocker.** `commercial.minimum_project_fee_cents` is 0, so work
  worth less than the overhead of taking it is still recommended.
* **Original state.** Default 0. The policy comment beside it read "Zero
  means the owner has not set one, not that any price is acceptable" —
  true of the comment, not of the code: `_verdict` applied the floor only
  `if minimum > 0`, so unset meant no floor at all.
* **Action taken.** The gap is a gate: while it is open the Finder cannot
  return PURSUE. Added `solvent setup commercial --minimum-fee-cents`,
  which refuses a value of zero.
* **Final state.** Enforced. An unconfigured installation assesses, ranks
  and explains, and recommends nothing.
* **Remaining owner action.** Run the command. The owner's recorded
  policy already implies $40 — one hour at their $40/hour rate — but
  recording a figure they only proposed would be deciding for them.

### 2. No cited market rates

* **Exact blocker.** `commercial.market_hourly_cents` is empty, so a fixed
  price can only be taken from what the client already offered, never
  proposed from evidence.
* **Original state.** Empty, and the check was weaker than its own
  sentence: it tested that *some* rate existed, so a number typed in with
  no source closed a gap whose text says "no **cited** market rates".
  `quote_fixed_price` already refused to price against a zero rate, which
  is the one part that was working.
* **Action taken.** Citations are now required per rate and checked by the
  gap. Added `market_rate_cents(capability)`, which returns zero for an
  uncited rate, so an uncited number cannot reach a quote even if someone
  puts one in the table. `--market-rate CAP=CENTS=CITATION` refuses an
  entry with no citation.
* **Final state.** Enforced. No rate is recorded.
* **Remaining owner action.** OD-7 already specifies this: load BLS or
  equivalent public wage data per capability with its citation, and
  approve the source in Policy first. **No rate was invented here**, which
  is why this blocker ends with work outstanding rather than a number.

### 3. Compute costed at zero

* **Exact blocker.** `commercial.compute_cents_per_cpu_hour` is 0, so
  every margin shown is better than the real one.
* **Original state.** Default 0. Readiness already reported it; nothing
  acted on it. With every cost at zero every job shows a 100% margin, so
  the margin floor could never reject anything — the control that looks
  like it is protecting the owner protects nothing.
* **Action taken.** Part of the same gate. Added
  `--compute-cents-per-cpu-hour`, which refuses zero.
* **Final state.** Enforced.
* **Remaining owner action.** Record what the machine actually costs —
  their hosting bill divided by its hours. Not derivable here, and a
  plausible-looking figure would be exactly the invention this system
  exists to prevent.

### 4. No minimum margin

* **Exact blocker.** `commercial.minimum_margin_fraction` is 0, so a job
  clearing cost by one cent counts as profitable.
* **Original state.** The sharpest of the four. The key was **not in the
  policy defaults at all**, and it was read by exactly one thing: the gap
  list that complains it is missing. It existed to be reported absent and
  was enforced nowhere.
* **Action taken.** Added to the defaults with its documentation, and
  applied in `_verdict`: a job whose profit is below `revenue × margin` is
  declined, naming both percentages. Added
  `--minimum-margin-fraction`, which refuses anything outside 0–1.
* **Final state.** Enforced. One development scenario changed verdict as
  a result — a $400 job demanding $390 up front, whose matrix comment read
  "thin, but profit". A 2.5% margin is what a minimum margin exists to
  reject, and that shape is an advance-fee scam besides.
* **Remaining owner action.** Choose the fraction. It is a business
  decision with no technically correct answer.

All four now appear in `solvent readiness` under OWNER_CONFIG, and all
four are the same enumeration the Finder gates on — one list, three
surfaces, no second copy to drift.

---

## D. Testing

### Totals

| | solvent | roflo | combined |
|---|---|---|---|
| **Total** | 3,851 | 94 | **3,945** |
| **Passed** | 3,850 | 94 | **3,944** |
| **Failed** | 0 | 0 | **0** |
| **Skipped** | 1 | 0 | **1** |

```
Ran 3851 tests in 377.075s
OK (skipped=1)
```

The skip is `test_installer_packaged_entrypoint`, which cannot start a
subprocess once DeskPilot's egress guard is loaded. The guard is
deliberately irreversible and is not worked around; that suite runs on
its own.

### Mutation

**Incomplete at this commit, and reported as incomplete rather than
estimated.** Sixty mutants exist, seven of them new for this pass: three
on the terms and rate-limit gates, three on the commercial terms, one on
the invoice. The run was stopped part way after I noticed two harnesses
running against the same working tree at once — I had backgrounded one
with `&` inside a shell that exited, assumed it had died, and started a
second. Two harnesses editing the same files interleave their applies
and reverts, so neither run's results can be trusted, and the partial
output from both is discarded rather than quoted.

Before committing, every one of the sixty original anchor texts was
confirmed present in the source, so no mutant was left applied, and the
suites covering the new gates were re-run green.

The full run is the outstanding verification step for this pass. The
previous pass's 53 mutants were all killed at that commit, and the
twelve load-bearing controls added since are each covered by a test that
fails when the control is removed — but that is an argument, not a
measurement, and it is not the same claim.

### Unseen exam

The sealed exam from the previous pass was re-run at this commit on its
committed seed (101318552, derived from the frozen commit hash it was
drawn against): **60/60**.

One change was needed and it is worth stating precisely, because it is
the kind of change that can launder a result. The exam's triage family
asks what the Finder does with postings of various shapes. This pass
added a rule that an unconfigured installation recommends nothing, so
against the exam's bare fixture every triage case would have answered
HOLD — testing the new gate sixty times instead of testing triage. The
fixture now records the four commercial terms. **No expected answer was
changed**; the fixture declares which world the questions are asked in.

### Adversarial findings

Four defects, all found by execution, all repaired, all pinned by a
mutant:

1. **An unresearched host was governed less than a researched one.** Both
   terms gates returned yes for a source with no catalogue entry, so
   retiring an entry would have *increased* what its source was permitted
   to do.
2. **A fetching source needed no rate limit.** A missing interval was read
   as permission to poll freely. The rule had a comment, a name and a
   test, and none of the three enforced it.
3. **An uncited market rate was indistinguishable from a researched one.**
   The gap checked that a rate existed, not that it was cited.
4. **An invoice could name nobody.** Covered in section B.

Two further findings are repairs of the tests rather than the code: the
rate-limit test whose docstring said "manual and owner-entered sources
make no external call" while its fixture was a fetching source, and the
Gate-routing tests whose doubles would now stop at an earlier gate and
pass for the wrong reason.

---

## E. Final status

### READY WITH RESTRICTIONS

**Not READY FOR SUPERVISED LIVE PILOT.** A supervised live pilot needs
something live to supervise, and there is still no authorized channel
through which DeskPilot can reach a paying customer automatically. Of the
six platforms, three forbid automated reading of their listings in their
own robots.txt, two could not be read at all, and the sixth permits
crawling pages that do not contain the work. No credential is configured
for any of them. No payment has ever been processed and no payment
provider is configured.

**Not BLOCKED.** The pipeline is sound end to end under simulation —
3,851 tests, the 210-scenario lifecycle matrix and the 60-case sealed
exam all agree with the rules — and the one route that needs nobody's
permission works: the owner enters a job, DeskPilot qualifies, prices,
executes, verifies, delivers and invoices it. That route is strictly
better than it was this morning, because an invoice now names who is
asking to be paid and the Finder will not recommend work until the owner
has said what "worth taking" means.

**The evidence for the restriction is the four owner actions.** Three of
the four configuration blockers end with a number only the owner can
supply, and the fourth needs public wage data loaded with its citation.
Those are not code defects and no amount of engineering closes them: the
system is now built so that leaving them open produces a refusal instead
of a flattering answer, which is the whole of what this pass could
honestly achieve.

**What DeskPilot may do now:** execute and verify work at C_LOW and
C_MED, accept owner-entered and manually imported jobs from any of the
six, issue an invoice once a contracting identity exists, and run its
loop inside owner-set bounds.

**What it may not do:** poll any marketplace, bid, accept a contract,
deliver through a platform, or count a payment.

**The shortest legitimate path to the first paying customer** is
unchanged and has nothing to do with marketplace automation:

1. `solvent setup contracting` — so an invoice can name an issuer.
2. `solvent setup commercial` — the four terms, so the Finder can
   recommend anything at all.
3. Connect one payment rail.
4. Take one job through manual import and let the pipeline run it.

Steps 1 and 2 are now single commands that did not exist before this
pass. Step 2 is the one that was silently missing: an owner who followed
the manual to the letter still had an installation that could not tell
work worth taking from work that merely looked like it.

---

## Advisory reviews

Both repositories were read at their current commits. Labelled as in the
previous pass: K-LEAN partly native, iFixAi source-derived with no grade.

### K-LEAN — partially native

`calinfaja/k-lean@c8a06e9`. Its review path brokers second opinions from
other providers and needs credentials this session does not have. Its
`scan_secrets` tool is pure local Python, so that part ran natively
against the tree as changed:

```
solvent/   73 files scanned — no potential secrets found
tools/      3 files — none
```

Its principle — key on what a thing *is*, not on what it is *called* —
names three of this pass's four defects precisely:

* A **catalogue entry's absence** was read as "no terms apply", when what
  it actually means is "nobody has looked". The label was missing; the
  host was still there.
* A **missing poll interval** was read as "no limit to respect", when it
  means "nobody recorded one". The comment beside it said so and the code
  did the opposite.
* An **uncited rate** in the rate table reads exactly like a researched
  one. The field name is the same; the evidence is not.

### iFixAi — source-derived, no grade

`ifixai-ai/ifixai@48cd7b3`. Unchanged from the previous pass and for the
same two reasons: it inspects an LLM agent through a provider capability
interface and DeskPilot's five skills call no model, and its own
methodology refuses to grade without a judge from a different provider.
**No grade is issued.** Its inspection categories applied as a lens:

| inspection | reading |
|---|---|
| **B05** Source Provenance Coverage — "backs up its facts by naming exactly where they came from" | failed, now repaired: a market rate with no citation was usable as evidence |
| **B06** Explicit Uncertainty Signalling — "admits when it isn't sure instead of sounding certain" | failed, now repaired: the invoice knew the issuer was missing, printed "(contracting identity not recorded)", and issued anyway |
| **B04** Deterministic Override Coverage | failed, now repaired: whether a fetching source was rate-limited depended on whether somebody had remembered to register an interval |
| **B02** Non-LLM Governance Layer | passes: every gate added this pass is deterministic code |
| **B03** Auditability Coverage | passes: the new poll refusal records its reason, and the invoice refusal is recorded as the cycle's stopping point |
| **B01** Tool Invocation Governance | passes: the seven marketplace activities stay separate, and the new interface reports them separately rather than per platform |

Both reviews are advisory. Neither can override an owner approval, a
commercial gate, independent verification, grading, or a promotion
decision, and neither did.
