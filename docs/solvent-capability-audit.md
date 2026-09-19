# Solvent — Real Capability Audit

**19 September 2026.** One question: *what can this implementation actually do
for a paying client today?*

Baseline: `claude/solvent-architecture-audit-8ot9et` @ `1a205c5`, working tree
clean, **445 tests passing**. Mission unchanged.

---

## The finding, first

**Solvent has zero client-work capability. It cannot open a file, cannot run a
model, and has never produced a deliverable of any kind.**

This is not a gap in polish. There is no code path from a client's input to a
client's output. What exists — and it is substantial, tested and enforced — is
the *business* around that work: finding it, judging it, pricing it, governing
it, accounting for it, and surviving a crash while doing so.

Solvent today is **a business operating system with no worker attached.**

### The demonstration

A representative job was put through Solvent's own manual bridge:

> *"Clean this sales CSV: remove duplicate records, normalise the date fields,
> normalise region names, preserve original values, and give me regional totals
> that reconcile to the source."*

with a real fixture — 10 rows, 2 exact duplicates, 3 date formats
(`2026-01-15`, `01/16/2026`, `17-Jan-2026`), inconsistent region casing.

| Quote | Result |
| --- | --- |
| $180 | `REJECT` — margin below floor |
| $600 | `ACCEPT` — "clears the floor and conforms", state `ACCEPTED` |
| $2,000 | `NEEDS_OWNER_APPROVAL` — above threshold |

**The pipeline terminates at `ACCEPTED`.** There is no execution step. The
fixture's MD5 was identical before and after; the file was never opened.

Then the full demo — the only path that reaches delivery:

```
execution started under grant $291.00
ACT: spend authorised and booked; incurred $105.20
verified at T1_DETERMINISTIC by an identity distinct from the executor
delivered (simulated=True)
payment verified (SIMULATED) and job completed
final state : COMPLETE
profit      : 79480 cents, margin 0.883
files created by that completed job: 0
```

**A job reached `COMPLETE` with $794.80 of booked profit, an 88% margin, and a
recorded deterministic verification — having produced nothing.**

To Solvent's credit, the containment held: `real_revenue` stayed `$0.00`, the
payment carried the `SIMULATED:` prefix, and `simulated=True` throughout. The
simulation is honest about being a simulation. But the *shape* of a false
completion is fully present in the architecture, and §4 below shows it is not
confined to the demo.

---

## 1. Business infrastructure (present, tested — but nobody pays for it)

| Component | Evidence | Status |
| --- | --- | --- |
| Policy Store, versioned, owner-only | `policy.py`, 12 tests | **Working** |
| Financial Governor | `governor.py`, 23 tests | **Working** |
| Action Gate + write-ahead intent | `gate.py`, 19 tests | **Working** |
| Ledger + Stripe verification | `ledger.py`/`payments.py`, 41 tests | **Working** |
| Audit log, hash-chained | `audit.py`, 8 tests | **Working** |
| Job Orchestrator, 12 states | `orchestrator.py`, 14 tests | **Working** |
| Discovery + Qualification + ranking | 39 tests | **Working** (fixture sources) |
| Owner Channel, signed single-use | `owner.py` | **Working** |
| Location-aware pricing | `pricing.py`, 18 tests | **Working** |
| 24/7 runtime, crash recovery | `runtime.py`, 47 tests | **Working** |
| Egress control | `egress.py`, 13 tests | **Working** |

**None of these is a service a customer buys.** A client does not pay for an
audit log. This column is real work and it is not revenue.

## 2. Client-work capability inventory

Every candidate service family, audited against source:

| Service family | Classification | Evidence |
| --- | --- | --- |
| Spreadsheet / CSV cleanup | **DECLARED_BUT_UNPROVEN** | `services.SPREADSHEET_CLEANUP` is a *contract* — accepted inputs, deliverables, scope bounds. Zero implementation. No `csv`, `openpyxl`, `pandas` import anywhere |
| Data extraction / PDF → structured | **MISSING** | No PDF library, no parser |
| Document formatting / conversion | **MISSING** | No `docx`, `pptx`, no converter |
| Report / content creation | **MISSING** | No generator, no model call |
| Research / summarization | **MISSING** | No retrieval, no model call |
| Coding / code review / debugging | **MISSING** | No subprocess, no test runner, no patch tooling |
| Data analysis / reconciliation | **MISSING** | No numeric code outside money arithmetic |
| Presentation creation | **MISSING** | — |
| Marketing / business documents | **MISSING** | — |
| File conversion | **MISSING** | — |

**Proven: none. Partially proven: none.**

### The three searches that settle it

```
imports of roflo, or any model call, in solvent/     -> NONE
subprocess / os.system / exec                        -> NONE (only egress.py, blocking them)
network client (httpx/requests/urllib)               -> NONE outside egress.py
file I/O for a deliverable                           -> NONE (one open(), reading roflo.toml)
```

`capability.py` is a **registry of claims**, not an executor: its five public
methods are `register`, `capabilities`, `assess`, `conforming_options`,
`may_commit`. A "proven capability" in Solvent means *the owner asserted it*, not
*Solvent demonstrated it*.

**No table can hold a deliverable.** The only artifact-shaped columns in the
entire schema are `audit_log.result` and `verification_evidence.raw_output` —
both free text.

## 3. What Solvent *is* good at: refusing

This is a real capability, and it is the strongest result in the audit.

Fifteen adversarial job requests against the service contract:

| Category | Result |
| --- | --- |
| In-scope CSV / XLSX work | Accepted (correct) |
| Unsupported formats — PDF, DOCX, PPTX | **Refused**, named |
| Licensure — tax filing, audit opinion, legal review | **Refused**, named |
| Out of scope — financial advice, forecast, valuation | **Refused**, named |
| Unsupported technology — VBA macro | **Refused** |
| Ambiguous — no stated request | **Refused**: *"no stated request to conform to"* |
| Price and deadline pressure | Correctly **not** a conformance question — handled by the Governor and owner-approval threshold, with existing tests |

Unknown conformance fails closed. A posting cannot claim a capability, set its
own price, or talk its way past the owner — all covered by existing adversarial
tests.

### But the scope guard is naive, and it leaks

`ServiceDefinition.conforms()` matches **literal substrings**. Nine ordinary
rephrasings of already-barred work were put to it. **Nine were accepted:**

| Barred phrase | Rephrasing that passed |
| --- | --- |
| `tax filing` | "prepare the filing for our taxes" |
| `audit opinion` | "give us an opinion for the audit" |
| `legal review` | "review the legal implications" |
| `financial advice` | "advise us financially on the numbers" |
| `forecast` | "project next year's revenue" |
| `valuation` | "value the company from these figures" |
| `macro` | "automate this with Visual Basic" |

Plus two that no keyword list would catch — *"tell us what these numbers mean for
our strategy"*, *"which of these customers should we drop"* — both advisory work
the contract means to exclude.

This is latent today because nothing executes. It becomes live the day something
does.

## 4. Completion and verification: what it actually checks

This is the most important section for what comes next.

**Who creates completion criteria?** Nobody. There is no checklist.

**What does verification check?** The **tier rank of an attestation**, and
nothing else:

```python
def verification_satisfied(self, job_id):
    required = self.required_tier(job_id)      # from consequence tier
    best = self._audit.best_tier(job_id)       # highest tier attested
    return best.rank >= required.rank
```

**What is "evidence"?** Whatever the caller passes. `record_verification` takes
`verdict: bool` and `raw_output: str` as **parameters**. It never opens, parses,
recomputes or compares anything — because there is nothing to open.

The demo's "deterministic verification" is three string literals:

```python
method="recomputed category totals and validated the file opens",
verdict=True,
raw_output="totals reconcile; 12 rows; schema valid",
```

No totals were recomputed. No file was opened. There are no 12 rows.

**Can requirements silently disappear?** **Yes, and they do.** Requirements are
stored in their own table and used for conformance *at qualification*. The
verification path mentions them **zero times**. Demonstrated directly:

```
requirements recorded: 3
work actually performed: NONE
verification_satisfied -> True: T1_DETERMINISTIC satisfies T1_DETERMINISTIC
job state after delivery: AWAITING_PAYMENT

*** 3 client requirements. 0 met. 0 checked. Delivered. ***
```

**Requirements and verification never meet.** That is the gap, stated exactly.

## 5. Guardian analysis — what already exists

Something Guardian-shaped **does** exist, and it should be extended rather than
replaced.

`AuditLog.record_verification` enforces, at write time:

- T0 self-report is not verification above `C_LOW`
- at `C_HIGH`+, **verifier identity must differ from executor identity**
- a verifier identity is mandatory

Probed directly:

| Case | Result |
| --- | --- |
| `C_LOW`, executor == verifier | accepted (correct — low consequence) |
| `C_HIGH`, executor == verifier | **refused**: *"verifier must differ from executor"* |
| Any tier, non-existent subject, fabricated output | **accepted** |

**So it governs *who may attest*, not *what was checked*.** The independence
half is real and enforced. The substance half does not exist.

Two further limits worth naming: the identities are **string labels in one
process**, not separate execution contexts — `"worker"` and `"qc"` are
conventions, not isolation. And nothing binds evidence to an artifact, so a
verdict can reference a job that does not exist.

**Recommendation for the next phase: EXTEND EXISTING.** Do not build a second
Guardian. `record_verification` is already the chokepoint and already refuses
self-attestation; what it needs is a requirement checklist to check against and
an artifact to check.

## 6. Customer correction baseline (inspection only)

| Situation | What happens today |
| --- | --- |
| Deliverable fails verification | `record_delivery` raises `FailClosed` — *"refusing to deliver unverified work"*. This works |
| Client requests a revision | **Nothing exists.** No revision, rework, remediation or correction mechanism. `revision` appears in the codebase only as a *risk score* in the service scorecard |
| Client says a requirement was missed | **Nothing exists**, and nothing would detect it — requirements are never checked (§4) |
| Client is dissatisfied | **Nothing exists.** No complaint or dissatisfaction concept |
| Job cannot be corrected | **Nothing exists.** No escalation-to-owner-for-rework path |
| Refund | Ledger applies a verified Stripe `charge.refunded`, reduces collected revenue, sets `REFUNDED`. **Financial only** |
| Dispute | Ledger applies `charge.dispute.created`, withdraws collected revenue pending outcome. **Financial only** |

**`JobState.COMPLETE: frozenset()`** — no transitions out. A completed job is
architecturally unreopenable. So money and work state diverge: a refund can be
recorded against a job that can never be revised.

The money side of dissatisfaction is handled. The **work** side does not exist.

## 7. Complexity ceiling and repeatability

**Not measurable.** Both require a capability to measure, and there is none.

Reporting a ceiling or a completion rate here would be inventing numbers. Sample
size is **zero** for every service family. The honest ladder:

| Level | Status |
| --- | --- |
| L1 simple → L5 advanced | **All outside current capability.** The ceiling is not low; there is no floor |

## 8. Job-fit matrix

| Job type | Capability | Verifiability | Owner involvement | Complexity ceiling | Autonomy readiness |
| --- | --- | --- | --- | --- | --- |
| Spreadsheet / CSV cleanup | **DECLARED_BUT_UNPROVEN** | Would be objective (arithmetic) — nothing to verify yet | Total: the owner would do 100% of the work | None | **CAPABILITY_MISSING** |
| PDF → structured data | MISSING | Would be checkable | Total | None | **CAPABILITY_MISSING** |
| Small coding tasks | MISSING | Would be objective (tests) | Total | None | **CAPABILITY_MISSING** |
| Document formatting | MISSING | Subjective | Total | None | **DO_NOT_OFFER** |
| Research / summaries | MISSING | Poor — hallucination is invisible to a deterministic check | Total | None | **DO_NOT_OFFER** |
| Business analysis / advice | MISSING | Poor, and highest liability | Total | None | **DO_NOT_OFFER** |
| Anything requiring licensure | Correctly refused | — | — | — | **DO_NOT_OFFER** |

**Nothing qualifies for `READY_FOR_CONTROLLED_TRIAL` or even
`OWNER_REVIEW_REQUIRED`** — those presuppose output to review.

## 9. Routine job definition

Against the thirteen criteria, **no current job type qualifies**, and it fails on
the first: *proven capability*. Four of the thirteen are already satisfied
(approved pricing rules, risk level, spending limits, data/privacy class) and
three are structurally ready (delivery mechanism through the Gate, legal check,
ambiguity refusal).

Spreadsheet cleanup is the **nearest** candidate — deliberately chosen because
its verification is arithmetic rather than opinion — but it needs an
implementation and a measured pass rate before "routine" means anything.

## 10. Marketplace fit

**Solvent should currently search for nothing.** Applying proven capabilities to
marketplace categories yields an empty set, and a search that found a job Solvent
could not do would be worse than no search.

The earlier work-source research stands as preparation. No marketplace access was
attempted for this audit; demand research is premature while supply is zero.

## 11. Security and governance findings

Nothing that corrupts the audit. Three findings, all ordered for the next phase:

| # | Finding | Severity today | Severity once work exists |
| --- | --- | --- | --- |
| **1** | Verification evidence is caller-supplied and bound to no artifact | Low — nothing to verify | **Critical** — false completion |
| **2** | Requirements are never checked against evidence before delivery | Low | **Critical** — silent scope loss |
| **3** | Scope guard is literal substring matching; 9/9 rephrasings passed | Low | **High** — licensure work accepted |

No fix was applied: none of these distorts the audit's results, and §21 makes
this phase observational. They are listed in the order they should be closed.

## 12. External dependencies

**Zero third-party Python packages** — verified by AST walk against
`sys.stdlib_module_names`. The only external dependencies are the ones the owner
has not yet provisioned: a model runtime, a Stripe account, a signing key, an
always-on host.

## 13. Recommended scope for the next phase

The audit changes the ordering the next phase should use. A Guardian that
verifies nothing is not useful; **the capability must come first, and it should
arrive with its verifier.**

1. **Implement one capability end to end** — CSV/spreadsheet cleanup, stdlib
   `csv` only. Deliberately the narrowest useful thing.
2. **Make the requirement checklist real.** Requirements already exist and are
   already provenance-gated; `verification_satisfied` must additionally require
   that every committed requirement has a passing check.
3. **Bind evidence to an artifact.** `record_verification` should take a
   computed result over a named artifact, not a caller's string.
4. **Extend the existing verifier; do not add a Guardian.**
   `record_verification` is already the chokepoint and already enforces
   independence. It needs substance, not a sibling.
5. **Replace substring scope matching** with something a rephrasing cannot walk
   past — or make refusal the default and accept only recognised shapes.
6. **Then** revision handling: a `COMPLETE` job cannot currently be reopened, and
   that decision needs revisiting deliberately, not by accident.

Customer-facing revision, refund automation and dispute handling should come
**after** there is work to revise.
