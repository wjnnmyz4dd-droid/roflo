# DeskPilot: production connector repair

What a shop needs in order to run by itself is not another authority.
It is the wiring between the ones it already has. This report is about
eight places where that wiring was missing or wrong, what each one did
to a client, and what was built or changed to close it.

Everything below was executed. Where a number appears it came out of a
run on this machine, and the file that produced it is named so the run
can be repeated. Where something was *not* run, it says so.

**Baseline.** The assignment quotes commit `2bb8210` ("Stop at the
prerequisite gate: the simulation environment does not exist"). That
commit is an ancestor of the branch head this work started from, but it
is eleven commits behind it: the branch had already moved on to
`ed6dad3`. The work below was done on `ed6dad3` and the defects were
re-confirmed against it rather than taken from the older report.

**Nothing real happened.** Every client is fictional, every payment is a
signed webhook this repository writes into a spool, and
`solvent/egress.py` installs a PEP 578 audit hook that denies
`socket.connect`, `getaddrinfo`, `gethostbyname`, `sendto`, `bind`,
`subprocess.Popen`, `os.system`, `os.exec*` and `os.posix_spawn`
process-wide and irreversibly. `simulation/profile.py` deliberately
omits the tax reference so `PolicyStore.may_contract_for("PAYMENT_RAIL")`
stays blocked. `Ledger.real_revenue_cents()` reads `0` at the end of
every run in this report, and that is asserted, not observed.

---

## 1. Exact original defects

Eight, each confirmed by reproducing it before anything was changed.

**D1 — an accepted engagement never became a job.** The chain
`ACCEPTED NEGOTIATION → AUTHORIZED CONTRACT → JOB ADMISSION` had no
code joining its second link to its third. `Engagement` reached
`WORK_AUTHORISED` and stopped there; nothing read it.

**D2 — a part payment closed the job.** `JobOrchestrator.complete`
checked `collected > 0`. A client who paid one cent of a $290 invoice
got their job marked COMPLETE, and the remaining $289.99 stopped being
asked for.

**D3 — the relationship layer had no door.** `CRM.discover` had no
caller anywhere in `solvent/`: no scheduled task, no CLI command, no web
route. The CRM, the negotiation engine, the engagement and three
scheduled tasks sat behind it, reachable only from test code. The
connectors above them worked and had nothing to work on.

**D4 — `dealmaking` only looked at opportunities a job already
existed for.** `candidates()` swept `OpportunityStatus.ACCEPTED`, which
is written in exactly one place — `qualification.select` — which also
mints a job and applies the Governor's verdict. So the precondition for
*quoting* a client was a commitment to *doing* their job.

**D5 — every customer order was lost on restart.** `Storefront._orders`
was a plain dict in memory. After a restart the shop had no record of
any order, and the duplicate-order guard — which asks that dict whether
it has seen the reference before — failed *open*: it would accept the
same order again.

**D6 — a crash during qualification leaked an execution slot
invisibly.** The job stayed in `QUALIFYING`, its 3,600-second EXECUTE
reservation survived the restart, and `runtime.reconcile` did not look
at `QUALIFYING` jobs at all, so nothing ever noticed.

**D7 — `discovery.poll("storefront")` failed after every restart.**
Discovery stores a source's *name* durably and its *object* in memory.
Only `ManualSource` was rehydrated on load, so the storefront source
came back as a registered name with nothing behind it.

**D8 — the work directory was named after the order, and Windows
refused it.** Found only by Windows CI, and only once D1 was fixed:
an engagement-backed order is `eng-client:northstar`, the colon is
NTFS's alternate-data-stream separator, and `os.makedirs` raised
`NotADirectoryError` [WinError 267]. Thirteen tests failed on Windows
and passed on Linux. The same line was also an unguarded boundary: an
order id can carry a client's own reference, so `..` or a separator in
one chose a path outside the work root.

Two further defects from the previous round of this work are carried
here because their fixes are load-bearing for everything below: the
recovery path billed the invoice **gross of the deposit** (one route
billed 28,515 where the other billed 22,812 for the same job), and
nothing drained the payment spool, so the books never learned that
money had arrived.

---

## 2. Root causes

They are not eight separate mistakes. They are four.

**A missing door, mistaken for a missing decision.** D3 and D4 are the
same cause seen twice: the system could decide everything about a
client and had no way to be told one existed. The temptation is to make
the system invent the facts — a company name, an industry, a contact —
and that would be a forged record in the one file whose purpose is to
say what is actually known about a real person. The fix is a door the
owner types through, not a generator.

**State that looks durable and is not.** D5 and D7 are the same cause:
an object held in a dict, read as though it were a row. Both failed
only across a restart, which is exactly when nobody is watching, and D5
failed *open*, which is worse than failing at all.

**A platform assumption nobody had to make.** D8 alone. An
identifier was used as a filename because on the machine the code was
written on it worked.

**A control that cannot see the state it governs.** D1, D2 and D6. The
engagement knew work was authorised and nothing asked it; the
orchestrator knew money had arrived and never asked how much was owed;
the reconciler knew about stranded jobs and its list of "stranded" did
not include the state a crash most often leaves one in.

---

## 3. Files changed

New production code:

| File | Lines | What it is |
| --- | --- | --- |
| `solvent/clientintake.py` | 199 | The CRM door: one owner act records a prospect, their evidence, their contact, their standing and their enquiry, or records nothing. |

Changed production code:

| File | What changed |
| --- | --- |
| `solvent/orchestrator.py` | `complete()` refuses a short payment and says what is still owed. |
| `solvent/settlement.py` | The sweep does not even offer a part-paid job to `complete()`. |
| `solvent/discovery.py` | New `attach(source)` binds a live source object to a row that already exists, so a restart can rehydrate something other than a `ManualSource`. |
| `solvent/dealmaking.py` | `candidates()` sweeps `DISCOVERED` as well as `ACCEPTED`. |
| `solvent/storefront.py` | Orders are durable: `Order.prepaid_cents`, `_restore()`, `_save()`, a `durable` property, and `report_progress`. |
| `solvent/store.py` | New `storefront_orders` table, owned by `storefront` in `TABLE_OWNER`. |
| `solvent/runtime.py` | `_INTERRUPTED` widened to INTAKE, QUALIFYING, ACCEPTED, EXECUTING, VERIFYING, READY_FOR_DELIVERY. `AWAITING_OWNER_APPROVAL` is deliberately **not** in it: a job waiting on a person is not a stalled job. |
| `solvent/harness.py` | Attaches the storefront source to Discovery; gives the Storefront the store; the recovery invoice nets off the deposit. |
| `solvent/contracting.py` | Carries the pipeline's result back to the engagement and the order. |
| `solvent/cli.py` | `solvent client add` — the door, exposed. |
| `solvent/workorder.py` | `workdir_name()` — a directory name safe on every filesystem, and a boundary against an order id that chooses its own path. |

Simulation and tooling:

| File | What it is |
| --- | --- |
| `simulation/world.py` | A written-in client now goes through `record_enquiry`, so the run has no act without a production counterpart. |
| `simulation/rail.py` | The simulated provider's `refund` and `dispute` deliveries. |
| `tools/export_transaction_trajectory.py` (134) | Stage one of the iFixAi review: runs the real transaction, exports a JSON projection. |
| `tools/audit_transaction_trajectory.py` (290) | Stage two: iFixAi's own evaluator, in its own interpreter. |
| `tools/crash_recovery_evidence.py` (169) | Eight kills, eight reopenings, eight traces, then the assertions. |
| `tools/capacity_accounting.py` (192) | The four counters over two runs: clients pay, and nobody ever pays. |
| `tools/mutate_solvent.py` | New mutants over the repaired lines. |

New tests:

| File | Tests | What it holds |
| --- | --- | --- |
| `tests_solvent/test_connected_transaction.py` | 16 | §5 — one client, contract to receipt, nothing inserted. |
| `tests_solvent/test_money_pathologies.py` | 18 | Prompt, late, partial, non-payment, refund, dispute, duplicate, restart. |
| `tests_solvent/test_crash_recovery.py` | 10 | Eight kill points, in their own interpreters. |
| `tests_solvent/crash_child.py` | — | The process that gets killed, as readable code rather than a string. |
| `tests_solvent/test_clientintake.py` | 16 | The door, and everything the owner has not said. |
| `tests_solvent/test_operation.py` (added to) | 7 | The work directory name: Windows-illegal characters, traversal, stability, collision. |

---

## 4. Production connections implemented

The chain the assignment names, with the component that owns each
transition and nothing between them:

```
  the owner types what a client said
        │   solvent/clientintake.py  → CRM + Discovery, one act
        ▼
  DISCOVERED opportunity
        │   scheduled task `dealmaking`  → negotiation opened at the
        │   Governor's floor; nothing is sent
        ▼
  ACCEPTED NEGOTIATION
        │   scheduled task `contracting` → proposal on the published
        │   offer's terms, at the agreed price
        ▼
  AUTHORIZED CONTRACT            (client signs, pays a deposit)
        │   scheduled task `contracting` → storefront order carrying
        │   offered_cents and prepaid_cents
        ▼
  JOB ADMISSION
        │   `workorder.next_work_order` → `run_business_cycle`
        ▼
  CAPACITY RESERVATION → EXECUTION → INDEPENDENT QA → DELIVERY
        │
        ▼
  INVOICE  (agreed price less the deposit)
        │   simulated provider → spool → scheduled task `payments`
        │   → production rail verifies the signature → Ledger
        ▼
  PAYMENT
        │   solvent/settlement.py → Orchestrator.complete
        ▼
  RECONCILIATION → CAPACITY RECOVERY
```

**The scheduler admits the job, not a harness.** In
`test_connected_transaction.py` the admission step is
`self.runtime.run_due()` and the assertion is made on
`self.s.storefront.order(...)` afterwards. `test_15` walks the test
file's own parse tree and fails if it contains a call to `set_status`,
`_transition`, `mark_verified`, `apply_verdict`, `set_payment_state`,
`open_payment`, `record_billing_obligation`, `complete`, `block`,
`submit_to_pipeline` or `place`. A test that arranges the state it then
reports is not evidence, and this is the check that keeps it honest.

---

## 5. Pricing authority preserved

No second pricing authority was created. The negotiated price is set
once, by the negotiation engine against the Governor's floor, and every
later step reads it:

* `contracting.propose` copies `deal.current_cents` into the proposal,
  and `test_04` asserts `engagement.proposal.price_cents ==
  deal.current_cents` with the message *"the contract repriced the
  agreement"*.
* Admission copies the proposal price into the order, and `test_07`
  asserts `order.offered_cents == engagement.proposal.price_cents`.
* The business cycle reads the order, and `test_08` asserts
  `cycle.quoted_cents == engagement.proposal.price_cents`.
* The invoice is the agreed price less what was already collected, and
  `test_08` asserts `cycle.prepaid_cents + cycle.due_cents ==
  cycle.invoiced_cents`.
* At the end, `test_14` asserts `deposit + collected == agreed` — the
  client paid the contract, once.

Measured on the run captured in
`docs/evidence/connected-transaction-trace.txt`:

```
CONTRACT      proposed at 29067c over 3 days
DEPOSIT       5814c cleared (20%)
ADMISSION     order eng-client:northstar at 29067c, 5814c already paid
INVOICE       29067c less 5814c deposit = 23253c due
PAYMENT       23253c verified by the rail
BOOKS         5814c deposit + 23253c balance = 29067c agreed; real revenue 0c
```

---

## 6. Fictional contracting configuration

Recorded in `simulation/profile.py` and written through the **existing**
authority — `PolicyStore.record_contracting_structure` — so the
owner-identity check, the field validation and the audit record all
happen exactly as they would for a real owner.

| Field | Value |
| --- | --- |
| Legal name | Northstar Business Solutions LLC |
| Owner | Alex Morgan |
| Email | billing@northstar.invalid |
| Address | 100 Simulation Plaza, Test City, CT 00000 |
| Structure | LLC |
| Tax reference | **deliberately omitted** |

The omission is the control. `may_contract_for("INVOICE")` needs a legal
name, an address and an email, and now passes. `PAYMENT_RAIL`
additionally needs a tax reference, so it stays blocked and no run can
drift into believing it may connect a real one. `.invalid` is reserved
by RFC 2606 and cannot resolve. The reason string recorded in the audit
log begins `SIMULATION ONLY:` and says in full that this is not a real
company and no payment rail is provisioned.

Production refuses to invent any of this, and that is correct:
`PolicyStore.contracting_party` says in its own docstring *"There is no
code path here that produces a name nobody provided"*. The consequence,
before this module existed, was that no invoice could be issued in any
run, so nothing ever settled, so no capacity ever came back.

---

## 7. Invoice and payment evidence

The invoice is a document a client reads, so the evidence is the
document. `test_09` opens the file the run wrote and asserts it names
the issuer and shows the deduction:

```
Northstar Business Solutions LLC
... deposit, paid before work began ...
232.53
```

The payment is a signed webhook. `simulation/rail.py` writes a Stripe
delivery into the spool with a real HMAC over the body, computed with
the real clock — a simulated date would make every payment look like a
nine-month-old replay to the rail's own tolerance check. The production
`payments` task reads the spool, the production `StripeRail` verifies
the signature, and the production Ledger decides what the event means.
The verification method recorded against the payment is:

```
SIMULATED:stripe:payment_intent.succeeded:pi_evt_northstar
```

`SIMULATED:` is written by the Ledger, not by the test, and
`real_revenue_cents()` is `0` afterwards — asserted in `test_14` with
the message *"simulated money must never read as revenue"*.

`simulation/rail.tamper` writes a delivery signed with the wrong secret,
so a run can show the verification refusing one rather than asserting
that it would.

---

## 8. Capacity accounting, before and after

**Before.** Five delivered jobs stuck in `AWAITING_PAYMENT`, five
reservations still active, **18,000 owed seconds**, and a shop that
eventually stopped accepting work. Capacity was returned by
*settlement* rather than by *delivery*, so a client who paid late held
a production slot they were not using and a client who never paid held
one for ever.

**Said plainly: that repair is not in this commit range.** It landed
earlier on this branch, in `6400061` ("Free capacity when a job owes
money, not work"), which is one of the eleven commits between the
baseline the assignment quotes and the head this work started from. So
the 18,000 seconds were not re-measured here — measuring them again
would mean reverting a fix in order to watch it fail, and the figure
is quoted from where it was taken. What this section adds is the
*other* half, which had not been measured: that the separation holds
over a long run, including one where nobody pays at all.

**After.** `tools/capacity_accounting.py` runs the same thirty
simulated business days twice over the same clients under the real
scheduler — once where the simulated provider pays, once with no spool
at all so that nobody ever does. Full output:
`docs/evidence/capacity-accounting.txt`.

| | A. capacity | B. commitments | C. receivables | D. exposure |
| --- | --- | --- | --- | --- |
| clients pay | 0s owed, 0 reservations | 6 engagements, 18 orders open | 735,332c over 23 invoices | 130,391c |
| nobody pays | 0s owed, 0 reservations | 6 engagements, 20 orders open | 818,192c over 18 invoices | 146,260c |

Jobs at the end of each run:

```
clients pay   {'AWAITING_PAYMENT': 16, 'COMPLETE': 7}
nobody pays   {'AWAITING_PAYMENT': 18}
room for the next job, both runs:
              yes -- 0s owed, 3600s requested, 72000s allowed
real revenue, both runs: 0c
```

Read the four columns across: the two runs differ in **C** and **D**
and not in **A**. Money arriving changes what is owed and what is at
risk; it does not change how much work the shop can do. That is the
separation the assignment asks for, and it is the repair.

**What was not forgiven.** The unpaid run ends with 818,192c
outstanding over 18 invoices and not one of them written off. Nothing
in either run records a settlement the rail did not deliver, and
`real_revenue_cents()` is `0` in both.

**What the snapshot does not show.** The four counters are read at the
end of each simulated day, when no job is mid-execution, so the `0s
owed` column means *every slot came back*, not *no slot was ever
taken*. The other half is in the crash traces, where a job killed
mid-flight is caught holding `owed 3600.0` (§10), and in `test_13` of
the connected transaction, which asserts the slot is out during
execution and back afterwards.

**Where the shop does eventually stop.** Not on capacity. The unpaid
run's exposure reaches 146,260c against the owner's
`max_committed_unverified_cents` ceiling of 500,000c, and when that
ceiling is reached the Governor returns `HIGH_RISK` with the reason
naming the figure. `test_the_exposure_ceiling_is_what_stops_the_shop_not_capacity`
drives it to that point deliberately and asserts both halves: new work
is refused, and the refusal is about money rather than about time.

---

## 9. Non-payment behaviour

`tests_solvent/test_money_pathologies.py`, 18 tests, each reading the
four counters from the authority that owns them.

| Case | Behaviour | Test |
| --- | --- | --- |
| Prompt payment | Job closes, slot already back. | `WhenTheyPay::test_promptly` |
| Late payment | Same close, days later; the slot never waited for it. | `test_late` |
| Partial payment | Job stays AWAITING_PAYMENT. **Two independent guards**: the Orchestrator refuses `complete()` directly, and the sweep does not offer the job at all. | `test_partially`, `test_the_orchestrator_refuses_a_part_payment_when_asked_directly`, `test_the_sweep_does_not_even_offer_a_part_paid_job` |
| The rest, afterwards | The balance arriving later closes it. | `test_the_rest_afterwards` |
| Duplicate notification | The same `event_id` twice moves nothing. | `test_twice_over` |
| Non-payment | The shop keeps working. The receivable stays. | `WhenTheyDoNot::test_never_paying_does_not_stop_the_shop` |
| What *does* stop it | The owner's exposure ceiling — a different control, which says so, and which lifts when somebody pays. | `test_the_exposure_ceiling_is_what_stops_the_shop_not_capacity` |
| Refund | Collected revenue is reduced; the job stays closed and the books say why. | `test_a_refund_reduces_what_was_collected`, `test_a_refunded_job_stays_closed_and_the_books_say_why` |
| Chargeback / dispute | Withdrawn pending the outcome. | `test_a_dispute_withdraws_the_money_pending_the_outcome` |
| Forged refund | Moves nothing. | `test_a_forged_refund_moves_nothing` |
| Restart mid-reconciliation | Idempotent; a payment applied before a restart closes the job after it. | `WhenItRestartsMidWay`, 3 tests |

**On chargeback and dispute being one row.** The assignment lists refund,
chargeback and dispute as three cases. DeskPilot models two outcomes,
`EventKind.REFUNDED` and `EventKind.DISPUTED`, because the provider
sends two: a chargeback *arrives* as `charge.dispute.created`, which
`solvent/payments.py:179` maps to `DISPUTED`. `simulation/rail.dispute`
says so in its docstring — *"The client has charged back"*. Adding a
third ledger state for a word the rail does not have would be inventing
a distinction the money does not make.

**Nothing was forgiven and nothing was fabricated.** No test in this
file writes off an invoice, and none records a settlement that the rail
did not deliver. The part-payment guard was added, not relaxed:
`Orchestrator.complete` now raises `FailClosed` with the text *"A part
payment is not a settled invoice, and closing the job would stop the
rest being asked for"*.

---

## 10. Crash-recovery traces

Eight points, one process each, killed with the most abrupt stop the
platform has — `SIGKILL` on POSIX, `TerminateProcess` via `os.kill` on
Windows. Not an exception: an exception unwinds the stack and runs
`finally` blocks, which is the one thing a real crash does not do. The
kill is injected **inside** the production call stack by replacing one
production function with a wrapper that calls the real one and then
dies, so the interruption lands between two writes rather than at a
convenient boundary.

Full traces: `docs/evidence/crash-recovery-traces.txt`. Three that show
the repairs working:

**Killed at capacity reservation.** The job is left in `QUALIFYING`
holding a 3,600-second slot. Before the widening of `_INTERRUPTED` this
was invisible; now recovery sees it:

```
BEFORE jobs        : [('job_d1b9ecb5', 'QUALIFYING')]
BEFORE orders      : [('eng-client:northstar', 'IN_QUALIFICATION')]
BEFORE reservations: [('job_d1b9ecb5', 'EXECUTE')] owed 3600.0
notes: ['job_d1b9ecb5d40c4435 was stranded in QUALIFYING; blocked on the owner']
AFTER  jobs        : [('job_d1b9ecb5', 'BLOCKED')]
```

The slot is **not** released, and that is the right answer: the work is
unfinished, and the assignment's rule is *do not release an active
execution reservation while the associated work is unfinished*. The job
is blocked on the owner, which is where an interrupted job that nobody
can account for belongs.

**Killed during execution.** Recovery finishes the job, bills it, and
only then returns the slot — net of the deposit:

```
notes: ['job_5c3a0f41b0314821 was delivered by recovery and never priced;
         invoiced pay_eb5f7885bb384f5b at the agreed fixed price less 5703 already paid',
        'released 1 slot(s) held by job_5c3a0f41b0314821, which is AWAITING_PAYMENT']
AFTER  receivables : [('job_5c3a0f41', 22812, 0)]
AFTER  reservations: [] owed 0
```

28,515 agreed − 5,703 deposit = 22,812 invoiced. Before the repair this
route billed the full 28,515 while the other billed 22,812 for the same
job.

**Killed at payment receipt**, i.e. between the Ledger's two writes. The
receivable survives at its full amount with nothing collected, and the
slot comes back because the work is delivered:

```
BEFORE receivables : [('job_ad28711b', 22812, 0)]
BEFORE reservations: [('job_ad28711b', 'EXECUTE')] owed 3600.0
notes: ['released 1 slot(s) held by job_ad28711b56074066, which is AWAITING_PAYMENT']
AFTER  receivables : [('job_ad28711b', 22812, 0)]
AFTER  reservations: [] owed 0
```

The assertions behind the traces are in
`tests_solvent/test_crash_recovery.py`, which checks, at every point: no
duplicate job for one order, no duplicate invoice or receivable, no
payment counted twice, no accepted contract lost, no slot held by
finished work, no slot released from unfinished work, no receivable
dropped. Result:

```
10/10 cases passed in their own interpreter
```

One process per case is not a convenience. The egress guard is
irreversible, so the first test to open a database denies
`subprocess.Popen` for the rest of the interpreter — and `pytest` in one
process therefore *skips* every case after the first, which looks like a
pass and is not one. `tools/crash_recovery_evidence.py` exists to make
that failure mode impossible to report as success, and the test module
skips loudly with the command to run instead.

**One thing the traces show that is not a defect.** The order is still
`IN_QUALIFICATION` after settlement in the last trace, because
`reconcile` closes jobs and the customer-facing status is carried by the
`contracting` tick, which the trace runner does not run. In the full
transaction it does run, and the order reaches `CLOSED` with the
customer status `Complete.` (`test_12`).

---

## 11. Regression results

**Targeted, over everything this work touched** — the CLI, contracting,
client intake, the money pathologies, the connected transaction,
dealmaking, the storefront, discovery, the runtime and the mutant
anchors:

```
287 passed, 1 skipped, 380 subtests passed in 42.04s
```

**Full suite, before the Windows fix:**

```
1 failed, 5826 passed, 62 skipped, 11242 subtests passed in 713.65s
```

The one failure was `test_cli.NoCommandSellsWork`, and it is the test
working: it pins the list of subcommands so that adding one *fails*,
and somebody has to say out loud what the new one does. `client` is
now on the list with its review written next to it — it contacts
nobody, it refuses anybody the policy does not recognise as the owner,
and the standing it sets makes the prospect eligible for `dealmaking`,
which records a price and sends nothing.

**Full suite on the final tree**, after the Windows fix:

```
5837 passed, 62 skipped, 11268 subtests passed in 627.75s (0:10:27)
```

**Mutation sweep, all 289 mutants** (`tools/mutate_solvent.py`, full
output in `docs/evidence/mutation-sweep.txt`):

```
289/289 killed  (0 survived, 0 not applied or harness)
```

The zero in the second column is the one that matters. `NOT_APPLIED`
means the mutant's anchor text has moved on and nothing was tested —
counting those as kills is how a mutation report becomes a decoration,
and `tests_solvent/test_mutant_anchors.py` exists to make a stale
anchor fail the suite rather than inflate this number.

Eight mutants are new in this work: three over the part-payment
guards (one per layer, because a single mutant was covered by the
other guard and survived), three over `workdir_name`, and two over
the write-in-flight grace period, one per edge. One of the
`workdir_name` three was retargeted rather than kept: see §12.

---

## 12. Windows results

Windows CI is `.github/workflows/windows-preinstall.yml`: four jobs,
Python 3.11 and 3.13, install and full suite on each. It was in scope
because this work added a thirteenth scheduled task (`payments`) and a
table to the store.

**It found a real defect, and the defect was already there.** Run 51,
on the baseline `ed6dad3` and before any of this work, failed both
Suite jobs with thirteen tests that pass on Linux — every one of them
`NotADirectoryError: [WinError 267] The directory name is invalid:
'...\work\eng-client:csv-cleanup'`. That is D8 in §1, and it is the
one defect in this report that no amount of running tests on Linux
would have surfaced. Both Install jobs passed, on both versions.

The fix is `82b8239`. **Run 53, on that commit, was still in progress
when this paragraph was committed**; its conclusion is reported in the
commit that follows this one, with the per-job detail. The fix is
already pinned on Linux by seven tests and three mutants, but a fix
for a Windows defect is not proven by a Linux run and is not claimed
as proven here.

---

## 13. Remaining limitations

**K-LEAN was not run, and no K-LEAN finding is claimed.** Its review
capability is multi-model consensus through a LiteLLM proxy at
`localhost:4000` fronting OpenAI, Gemini, DeepSeek and Moonshot. No
proxy is running, no key exists, and the egress guard would deny the
connection regardless. The part of it that works offline is a knowledge
database, not a reviewer. Written up in
`docs/evidence/klean-not-applicable.txt`.

**iFixAi reviewed a projection, not the system.** It was given names,
actions, resources and amounts in a JSON file, and no object it could
call. Its own stated limits hold: actor identity, collector identity and
costs in that file are caller declarations, not authenticated facts. A
CLEAR verdict says the recorded trajectory satisfies the policy as
written; it does not say the policy is the right one.

**The client's facts come from the owner, and nothing checks them.**
`clientintake` refuses a missing field, a non-owner, an unknown source,
a skill the shop does not sell, a duplicate and an opted-out prospect.
It cannot tell whether the company exists. That is the correct boundary
— the alternative is a system that invents them — but it means the CRM
is exactly as accurate as what was typed into it.

**An interrupted job goes to the owner, not back to work.** A crash
during qualification or verification leaves the job BLOCKED with its
slot held. Nothing resumes it automatically. For an unattended shop this
is a slot that stays out until a person looks, and the conservative
choice was taken deliberately: the alternative is a system that decides
on its own to re-run work it cannot account for.

**The exposure ceiling is the only brake on an unpaid pile.** A shop
whose clients never pay keeps working until
`max_committed_unverified_cents` is reached, and then refuses new work
as HIGH_RISK. That is one number in policy. There is no ageing of
receivables, no per-client limit and no escalation before the ceiling.

**`simulation/` is not production.** It builds a real `Solvent` through
`solvent.harness` and ticks it with a real `Runtime`, and it decides
nothing on DeskPilot's behalf — `unbacked_acts` is `0` and asserted. But
the clients, the calendar and the payment provider in it are fiction,
and no figure from a run is a business forecast.

**The 90-day business simulation of the preceding assignment is still
outstanding.** `simulation/audit.py` — the independent grader — and the
30/60/90-day runs were not reached in this session. Nothing here
conflicts with them; the components they need are the ones repaired
above.

---

## 14. Exact commit hash

The repair is two commits on
`claude/solvent-architecture-audit-8ot9et`:

| Commit | What it carries |
| --- | --- |
| `05d4c845fc63037213abb5be15febd3af13f357e` | Connect an accepted contract to the work, and the money to the books — D1 to D7, the connectors, the tests, the evidence. |
| `82b823910652ac1558af56f9ef17d55bd117f078` | Stop naming a work directory after something a filesystem may refuse — D8, found by Windows CI. |

Baseline for both: `ed6dad3`, itself eleven commits after the
`2bb8210` the assignment quotes.

This report is committed after them, so its own hash is the commit
that adds this file.

---

## Advisory review

### iFixAi — **RUN**, version 4.2.0

Two stages, because the two systems cannot share an interpreter:
DeskPilot installs the egress guard, and the auditor must import no
DeskPilot module.

Stage one (`tools/export_transaction_trajectory.py`) runs the whole
transaction and writes a JSON projection. Stage two
(`tools/audit_transaction_trajectory.py`) builds an `AuditPolicy` in
iFixAi's vocabulary and runs `audit_trajectory`, which states of itself
that it resolves no tool or provider and makes no network call.

The invariant under review is the assignment's own: *the final
authorized contract price should be the single source of truth*.
Expressed as a trajectory that is a trace budget — every cent the
transaction moves is charged against the authorized price, so a second
pricing authority anywhere between the contract and the receipt shows up
as an overrun rather than as a number somebody has to notice.

A clean verdict is worth nothing unless the auditor can produce a dirty
one, so the same policy was run against seven corrupted copies of the
same trajectory. The first corruption is not hypothetical: billing the
invoice gross of the deposit is a defect this repair actually found.

```
as-recorded            CLEAR
billed-gross           BLOCK          caught   budget/BUDGET_EXCEEDED
second-price           BLOCK          caught   budget/BUDGET_EXCEEDED
unapproved-admission   BLOCK          caught   approval/APPROVAL_REQUIRED
unknown-actor          BLOCK          caught   delegation/DELEGATION_BINDING
scope-escalation       BLOCK          caught   delegation/DELEGATION_BINDING
untrusted-collector    INCONCLUSIVE   caught   COVERAGE UNTRUSTED_COLLECTOR
missing-trace          INCONCLUSIVE   caught   COVERAGE CAPTURE_INCOMPLETE

deskpilot_transaction CLEAR   controls_injected 7   controls_caught 7
auditor_can_fail True
```

Full output: `docs/evidence/ifixai-transaction-review.txt`.

### K-LEAN — **NOT RUN**

See §13 and `docs/evidence/klean-not-applicable.txt`. No K-LEAN verdict
is claimed anywhere in this report. One earlier K-LEAN finding is
honoured in `solvent/audit.py` — redaction applied at the write rather
than at each caller — and that is a past finding being kept, not an
execution in this session.

---

## Mandatory final verdict

**Withheld until the two runs named in §11 and §12 finish.** Six of
the seven items have their execution evidence already and the seventh
— the Windows half of CRASH RECOVERY — does not. The standing rule is
*do not declare PASS without execution evidence*, so no verdict is
recorded in this commit; it is recorded in the one that follows, when
the full suite, the mutation sweep and Windows run 53 have all
reported.
