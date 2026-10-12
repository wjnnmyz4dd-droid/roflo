# DeskPilot — business pipeline: forensic audit, integration and hardening

Method: **execution, not reading.** The pipeline was audited by attempting a
complete freelance transaction with the APIs that existed and recording where
it stopped. It stopped six times. Each stop was a real defect, each is fixed
below, and each now has a mutant that restores it.

The headline finding is one sentence: **every stage existed and worked, and
nothing joined them, so no discovered opportunity could ever have been
delivered.** The suites were green throughout, because each half was tested
against itself.

## 1. Repository

| | |
|---|---|
| Commit | `d311825` (the pipeline join) and its successor on this branch |
| Branch | `claude/solvent-architecture-audit-8ot9et` |
| Tests | **3439 pass, 2 skips, 0 failures** |
| Mutation | **189 of 189 killed** — no survivors, none unapplied, none invalid |

## 2. Component status

Classified from what was executed this session, not from what is implemented.

### VERIFIED

| Component | Evidence |
|---|---|
| Owner setup | `solvent setup capability` run against a real database file; records read back in a separate process |
| Commercial skill registry | all five APPROVED and proven, read back from `registered_capabilities` |
| Platform rules | a source cannot be marked PERMITTED without a written owner determination; every marketplace is UNDETERMINED and says so |
| Qualification | **repaired**; now reaches a Governor-priced verdict on a discovered posting |
| Contract intake | **new**; the agreed checklist is committed through the same call the hand-fed path uses |
| Job execution | **repaired**; now reachable from the business path, under a budget grant |
| Independent verification | every requirement recomputed from source and deliverable by a verifier that does not import the worker |
| Delivery | through the Action Gate, recorded simulated, refused when the destination is not allowlisted |
| Payment reconciliation (simulated) | Ledger marks it SIMULATED; `real_revenue_cents` stays at zero |
| Profit tracking | collected revenue less recorded cost, read back from the Ledger |
| Audit journal | hash-chained, verifies after every cycle; **repaired** — now redacts client-authored text |

### PARTIAL

| Component | What works | What does not |
|---|---|---|
| Job discovery | the Inserted Work door persists and feeds the full pipeline; polling works in-process | a `FixtureSource` is rebuilt empty after a restart, so only owner-entered work survives a process boundary; no marketplace adapter is permitted |
| Pricing | the engine computes cost, margin, fees and a calibrated estimate | **only FIXTURE rates exist.** Three states, three made-up hourly rates. No real market evidence has been ingested |
| Invoicing | the agreed price is recorded as a receivable and reconciled | **no invoice document is produced.** Nothing exists to send a direct client |
| Proposal generation | `applications.prepare` builds and digests one; submission is refused for want of a researched route | never exercised inside the business cycle |

### IMPLEMENTED — NOT VERIFIED

| Component | Why not verified |
|---|---|
| Control Centre | read model exercised on Linux; no Windows run |
| Runtime | suites pass; no Windows service has ever started |
| Notifications | providers are mocks; no credential, nothing sent |
| Backups | installer-side; never restored on Windows |
| Installer | built and extracted; no Windows machine has run it |

### BLOCKED

| Component | Blocker |
|---|---|
| Windows security isolation | NTFS ACL behaviour cannot be established from Linux. The model was wrong twice during development; only the installer's own probe on the real machine settles it |
| Code signing | no certificate exists in this environment |

### MISSING

| Component | Note |
|---|---|
| Client-facing invoice | see PARTIAL above — the receivable is tracked, the document does not exist |
| Real market pricing evidence | the engine is ready; the data is not there |

## 3. Defects discovered and corrected

All six were found by running the chain, none by reading code.

1. **A `need` meant two different things.** Discovery matched a posting's
   `needs` against capability *names*; `capability._judge` matched the same
   field against `covers`, which holds *check* names. A posting needing
   `csv-cleanup` was told "no registered capability covers: csv-cleanup" by a
   Solvent whose csv-cleanup was registered, proven and cleared for client
   work. **Every discovered opportunity was rejected for a gap that did not
   exist.** Both readings now resolve; the capability behind either still has
   to be proven.

2. **The service pipeline approved its own work.** `_execute_job` walked every
   job QUALIFYING → ACCEPTED → EXECUTING, skipping only the state it was
   already in. From ACCEPTED that is an illegal transition, so a governed job
   could not be executed at all. From AWAITING_OWNER_APPROVAL it would have
   gone straight to ACCEPTED under the initiator `execution` — the pipeline
   answering a question put to a person. It also entered EXECUTING directly,
   bypassing `begin_execution`, whose only job is to refuse work without a
   Governor budget grant.

3. **A posting's need made every discovered job impossible to commit.**
   Qualification recorded it as a MANDATORY requirement naming no check, so
   `commit_requirements` refused every checklist that came after it —
   permanently, since nothing removes it. Recorded ADVISORY now: scope, not an
   acceptance criterion.

4. **`confirm` discarded the requirement it confirmed.** It rebuilt the object
   from id and text alone, silently dropping the check, the acceptance
   criterion, the params and the criticality. Vouching for a requirement is
   not an edit of what it says.

5. **A plan refusal crashed a governed job.** It forced the job back to
   QUALIFYING before blocking, which is illegal from EXECUTING, so the job
   raised out of the pipeline instead of stopping on a blocker the owner could
   see.

6. **Planners were handed scope they cannot carry out.** Three of the five
   refused the whole job over the check-less scope requirement. The
   verification loop had skipped those since it was written; the planners had
   not. Instructions are now the terms that name a check; the brief handed to
   verifiers stays whole.

Plus one found by the K-LEAN gate, below.

## 4. New operations

Composition only. No new authority, nothing re-implemented.

`harness.run_business_cycle` — thirteen stages: **discover, qualify, price,
owner-approval, contract, budget, execute, verify, deliver, invoice, payment,
reconcile, audit.** Returns a `BusinessCycle` saying how far it got and why it
stopped. A cycle that stops is not an error: a job the Governor finds
unprofitable, or one whose owner has not answered, has reached its correct
outcome.

Two owner decisions bound it and **neither is supplied by Solvent**:

* `owner_accepts_quote` — the answer when the Governor asks. Without it the
  job sits in AWAITING_OWNER_APPROVAL.
* `simulate_payment` — whether to inject a simulated payment. The Ledger marks
  it, and it cannot become revenue.

`solvent cycle` — the owner's entry point, both decisions defaulting to off.
It is listed in the CLI's no-external-action guard with its review written
out: discovery through the Inserted Work door or Discovery's own permission
machinery; delivery through the Action Gate; payment simulation-only with no
rail and no credential.

The **contract** stage is new and deliberate. A posting's `needs` name a
service and its body is attacker-authored prose; neither is terms. The agreed
checklist is supplied or the cycle stops, because a deliverable verified
against terms Solvent invented for itself is verified against nothing.

## 5. Independent verification results

Every capability's deliverable is recomputed from the source by a verifier
that does not import the worker. Through the business cycle, each produces
work that is checked rather than asserted:

| Capability | Proof in the suite |
|---|---|
| `csv-cleanup` | the duplicate is gone, the padding is gone, two rows remain |
| `report-builder` | the rendered total is 450.50 |
| `spreadsheet-automation` | the computed total cell is 302.0 |
| `data-entry` | the record with no client is held, and the exception file says why |
| `document-processing` | the absent purchase order reads NOT FOUND IN SOURCE rather than being invented |

The negative control: the same cycle with one row quietly appended to the
deliverable after the worker wrote it. Verification fails, delivery is
refused, and the Action Gate allows nothing.

## 6. Test results

| | |
|---|---|
| Full regression | **3439 pass, 2 skips, 0 failures** |
| `tests_solvent/test_business_cycle.py` | 55 tests |
| Scope | happy path, five capabilities, owner-decision refusals, budget refusals, contract refusals, sabotage, payment refusals, CLI entry point, both advisory gates |

## 7. Mutation results

**189 of 189 killed.** No survivors, none unapplied, none invalid. A kill
counts only when the baseline was green, the mutation reached the file, the
*designated* test newly failed, and the failure went away on revert.

Twelve are new this session, each restoring one of the defects above:

| Mutant | Restores |
|---|---|
| `need-matched-against-check-names-only` | every discovered opportunity rejected |
| `execution-answers-the-question-put-to-the-owner` | the pipeline approving its own work |
| `confirming-a-requirement-rewrites-it` | the requirement silently emptied |
| `a-service-name-recorded-as-a-blocking-term` | every discovered job uncommittable |
| `the-cycle-executes-without-being-selected` | work taken past the capacity limit |
| `work-starts-without-money-authorised` | execution with no budget grant |
| `the-worker-is-handed-scope-it-cannot-carry-out` | three of five capabilities refusing |
| `simulated-money-booked-as-real-revenue` | fixture money presented as earnings |
| `the-journals-payload-keeps-the-clients-secrets` | a credential in a posting title stored |
| `the-journals-reasons-keep-the-clients-secrets` | a credential in a job reference stored |
| `redaction-eats-the-journals-own-evidence` | an artifact digest destroyed by its own control |

Two of these are worth naming. One was written, run, and **survived** on the
first attempt: the redaction test proved the `payload` path and not the `why`
path, so the test was split in two and both now kill. The other exists because
the redaction fix itself broke something — see K-LEAN below.

## 8. Performance

Measured, on this container, for a complete governed cycle including
discovery, qualification, pricing, approval, contract, execution,
verification, delivery, invoicing, payment and reconciliation:

| Source rows | Source size | Full cycle |
|---|---|---|
| 100 | 3 KB | 0.11 s |
| 1,000 | 31 KB | 0.16 s |
| 10,000 | 315 KB | 0.49 s |
| 50,000 | 1.5 MB | 2.31 s |

About **0.05 s per 1,000 rows** at scale. Clearing all five skills on a fresh
database costs ~10.5 s once, because it generates the certification evidence.

No model is called and no network is touched, so there is no per-job API cost
to recover: the marginal cost of a job is CPU seconds.

## 9. Certification and commercial approval status

Unchanged by this work, and re-verified after it.

| Capability | Certification | Commercial |
|---|---|---|
| `csv-cleanup/1.0` | CERTIFIED, 11 of 11 | APPROVED |
| `report-builder/1.0` | CERTIFIED, 8 of 8 | APPROVED |
| `spreadsheet-automation/1.0` | CERTIFIED, **14 of 15** | APPROVED on 14 |
| `data-entry/1.0` | CERTIFIED, 16 of 16 | APPROVED |
| `document-processing/1.0` | CERTIFIED, 14 of 14 | APPROVED |

`spreadsheet-automation.number_format` remains uncertifiable and unclaimed. A
job resting on it is refused at delivery exactly as before.

## 10. K-LEAN findings

Advisory. Keys on what a secret *looks like*, not what a field is called.

**One finding, fixed.** `record_verification` has redacted its raw output
since a verifier was caught quoting a client's text into the database.
`audit.record` beside it — same table, same exposure — redacted nothing. And
client-authored text does reach it: a posting's title travels in the audit
`payload` for `job.intake`, and its reference travels in the `why` under which
the owner's acceptance of the quote is recorded. A credential in either was
stored verbatim, in a table the Control Centre reads and every backup copies.

Now redacted at the write — in `why` and the payload's strings, and
deliberately nowhere else. The client's own deliverable is still untouched:
redacting their file would be altering their data without authorisation.

**The first version of this fix was wrong, and the regression caught it.** It
redacted `result` as well, and `result` is where Solvent keeps its own
evidence. The redactor treats a long hex run as a raw key, which is exactly
what an artifact digest looks like, so a SOURCE artifact's registration came
back as `a9f[REDACTED]d8a… 104 bytes` — a record that can no longer say which
file was worked on. A model-licence record, whose entire purpose is to carry
the digest of the approved weights, failed the same way and is what surfaced
it. A control that eats the evidence is worse than the exposure it closes.
`result` is left alone, and a mutant restores the over-redaction so this
cannot come back.

## 11. iFixAi findings

Advisory. Asks whether a control refused for the *right* reason, and whether
satisfying one quietly unlocks the rest.

**No new defect; one weak test found and split.** The chain test starts with
several things wrong and fixes exactly one at a time, requiring the refusal to
move further down the pipeline on each fix — qualify, owner-approval,
contract, deliver, payment, complete — and requiring no two defects to be
caught by the same control. A stage the refusal skips past is a stage that was
never a control.

Both directions of the delivery/payment boundary are pinned: a delivered job
is not a paid job, and money arriving does not retrospectively verify the
work.

The weak test was this session's own redaction test, which asserted over the
whole journal and so could not say which field had saved it. Split in two.

## 12. Recommended first two job sources

Ranked for a first legitimate paid job, not for volume.

**1. `owner_entered` / `client_inquiry` — the Inserted Work door.** Available
today, needs no credential, no API and nobody's permission. It is the only
channel proven end-to-end through the governed cycle on a persisted database,
and the only one that survives a process restart. Terms arrive owner-confirmed,
which is what lets them bind a commitment; a scraped posting's prose cannot.
The first dollar does not require an API.

**2. `sam_gov` — US federal contract opportunities.** The only catalogued
source with an OFFICIAL_API access method **and** an adapter already
implemented. It needs a free api.data.gov key and a written owner
determination recorded against the source. `grants_gov` is the no-credential
alternative (PUBLIC_FEED, adapter implemented) but grants are not freelance
work, so it ranks below.

**Not recommended:** every freelance marketplace in the catalogue — Upwork,
Fiverr, Freelancer, PeoplePerHour, Guru, Contra. All are UNDETERMINED, none
has an adapter, and whether automated access is permitted is a reading of a
legal document about a specific account at a specific time. A wrong guess
costs the account permanently. Use them by hand, through door 1.

## 13. Required credentials

| Credential | For | Blocking? |
|---|---|---|
| `SOLVENT_OWNER_KEY` | authenticated owner approvals | **Yes, before live operation.** Without it approvals are unsigned, which the gate permits only while simulation is on |
| `SOLVENT_STRIPE_WEBHOOK_SECRET` | verifying real payment events | Yes, for collecting real money |
| Stripe account | the payment rail | Yes, for direct clients |
| api.data.gov key | `sam_gov` discovery | No — only for source 2 |
| Notification provider credential | email / SMS / call | No — approvals work without it |

No credential is in this repository and none is needed to run the cycle in
simulation.

## 14. Required owner approvals

| Decision | Where | Status |
|---|---|---|
| Clear each capability for client work | `solvent setup capability` | **Done** — all five APPROVED |
| Accept the Governor's quote, per job | `solvent cycle --accept-quote` | Per job, by you |
| Record a written compliance determination per source | `discovery.register_source` | Needed for any automated source |
| Leave simulation mode | terminal only | **Not done, and should not be yet** |
| Ingest real pricing evidence | `pricing.ingest` | Needed before a real quote |

## 15. Windows verification blockers

Unchanged, and not resolvable from here:

1. **No Windows machine has run any of this.** Linux simulations do not
   establish NTFS ACL correctness, and the ACL model was wrong twice during
   development.
2. **Neither service has ever started on Windows.** The runtime and Control
   Centre are registered by the installer; nothing has observed them run.
3. **The installer is unsigned.** No code-signing certificate exists here, so
   Windows will show an unknown-publisher warning.
4. **Backup restoration has never been exercised on Windows.**

The installer's own probe settles 1 and 2 on the real machine. Nothing in this
container can.

## 16. What prevents the first legitimate paid job today

Ranked. Only blockers, no feature suggestions.

**1. No real pricing evidence.** The Governor prices every job from
`FIXTURE_RATES` — three states, three invented hourly rates. Quote a real
client from that and the number is fiction. *Fix: ingest real rate evidence
for the jurisdictions you will work in, with source and date.*

**2. Simulation mode is on, and should stay on until 3 and 4 are done.** No
deliverable can actually reach a client. This is the correct state.

**3. No owner key is configured.** Until `SOLVENT_OWNER_KEY` is set, approvals
are unsigned. The gate accepts unsigned approvals only while simulation is on
and refuses them the moment real execution is enabled — so this blocks going
live, by design, and nothing needs building to enforce it.

**4. No payment rail.** `SOLVENT_STRIPE_WEBHOOK_SECRET` and a Stripe account,
or an agreement to be paid through a platform that invoices for you.

**5. No client-facing invoice exists.** The receivable is tracked internally
and nothing is produced to send. On a marketplace the platform invoices and
this does not bite; for a direct client it does.

**6. Nothing has run on Windows.** The pipeline is proven on Linux only.

**7. No automated source is permitted.** Not a blocker for the first job —
door 1 needs no permission — but it is the ceiling on volume afterwards.

**The shortest honest path to the first paid job:** ingest real rates for one
jurisdiction, take one job by hand through `solvent cycle` with
`--accept-quote` and no `--simulate-payment`, deliver the file to the client
yourself from the path the cycle prints, and invoice them yourself. That path
needs no credential, no platform permission and no Windows deployment — the
cycle does the work, verifies it, and refuses to deliver anything that fails
its own checks. Everything above is about automating the two ends of it.

## 17. Remaining limitations

* `policy.is_owner` checks registration, not authentication (OD-5). With an
  owner key configured, approvals are HMAC-signed, nonce'd, replay-protected
  and expiring — but the identity itself is still a registration. The
  authentication is terminal access to the machine.
* A `FixtureSource` does not survive a restart; only the Inserted Work door
  does. Automated discovery across a process boundary needs a real adapter.
* Pricing is fixture data. Everything the Governor says about profit is
  arithmetic on invented rates until that changes.
* Simulated money is contained, but containment is a property of the Ledger's
  verification method. It is pinned by a mutant; it is not a type.
* The Control Centre, the runtime, notifications, backups and the installer
  are implemented and unverified on the platform they will run on.
