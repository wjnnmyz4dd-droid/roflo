# Solvent — Authoritative Skill Inventory

**Repository** `wjnnmyz4dd-droid/roflo` · **branch** `claude/solvent-architecture-audit-8ot9et`
**As of commit** `e6d8907` · **1089 tests passing**

**Posture:** `simulation_only = true`, 0 real revenue, 0 external actions.

> **One number.** Solvent has **two** client-work skills. One is owner-promoted,
> one is not. Everything else in this repository is infrastructure, a proposal,
> a test artifact, or a name in a document.

---

## 1. The count

| Category | Count |
|---|---|
| **Client-work skills found** | **2** |
| Implemented | 2 |
| Tested | 2 |
| Technically certified | 2 |
| Certified with limits | 0 |
| Partially proven | 0 |
| Failed | 0 |
| Blocked | 0 |
| Untested | 0 |
| **Owner-promoted** | **1** (`csv-cleanup/1.0`) |
| Owner-not-promoted | 1 (`report-builder/1.0`) |
| Proposed but not implemented | 7 |
| Phantom / stale registry claims | 0 |
| Names in documentation with no implementation | 5 |
| Test-artifact capabilities (not skills) | 5 |
| Infrastructure modules excluded | 31 (of 35) |

**On overlap:** "implemented", "tested" and "technically certified" describe the
same two skills at increasing strength — they are not additive. "Owner-promoted"
and "owner-not-promoted" partition those same two. Everything below the
double rule is disjoint from the skill count.

---

## 2. The table

| Skill | Version | Implemented | Registered | Produces a client deliverable | Tested | Adversarially | Crash | Verifier certified | Technical status | Owner status | Ready for controlled trial |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **csv-cleanup** | 1.0 | yes | **yes** | yes — cleaned `.csv` | yes | yes | yes | **CERTIFIED** 386/386 | **TECHNICALLY_CERTIFIED** | **OWNER_PROMOTED** | capability yes; system no (see §6) |
| **report-builder** | 1.0 | yes | no | yes — `.md` report | yes | yes | yes | **CERTIFIED** 442/442 | **TECHNICALLY_CERTIFIED** | **OWNER_NOT_PROMOTED** | no — no owner decision |

**Evidence locations.** `solvent/csvwork.py` + `solvent/csvverify.py`;
`solvent/reportwork.py` + `solvent/reportverify.py`; batteries in
`solvent/certgen.py`; certification in `tests_solvent/test_certification_evidence.py`;
black-box scenarios in `tests_solvent/blackbox/`; promotion in
`tests_solvent/test_csv_promotion.py`.

---

## 3. csv-cleanup/1.0 in detail

### Exact proven scope — nine certified checks

`drop_exact_duplicates` · `map_values` · `no_unauthorised_changes` ·
`normalise_dates` · `parses_as_csv` · `preserve_columns` · `require_columns` ·
`row_reconciliation` · `trim_whitespace`

### Implemented but **outside** the approval

`rename_headers` and `sort_rows` are implemented operations whose checks the
certification battery never exercises. A job requiring either is **refused
fail-closed** at `record_verification` — *"solvent.csvverify.run is not
certified to decide 'sort_rows'"*.

They were deliberately left out of the promotion. Certifying them now would
have widened what the owner approved *after* they approved it.

### Contracts

- **Input:** `text/csv`. UTF-8 or UTF-8-BOM, LF or CRLF, quoted fields with
  embedded commas and newlines, large fields, unicode.
- **Output:** `text/csv`.
- **Rejects:** binary content, a file with no column names, an `.xlsx`.

### Ambiguity behaviour

A slashed date that could be read two ways (`05/10/2026`) is **not guessed**.
The job blocks on `BlockedOn.CLIENT`, a question is recorded and drafted, and
work resumes only on the client's answer — scoped to that job, never a global
rule. Dates that disambiguate themselves (`25/12/2026`) proceed without asking.

### Evidence relied upon — re-verified against the current fingerprint

| | |
|---|---|
| Verifier | `solvent.csvverify.run` @ `sha256:22783ed6…` |
| Certification | **CERTIFIED**, 386/386 trials, 0 false accepts, 0 false rejects |
| Defect classes | 15 |
| Development holdout | 272/272 |
| Surprise (unseen seed) | 274/274 |
| Black-box scenarios | 25/25 acceptable, **0 false completions** |
| Levels | L1, L2, L3, BOUNDARY, HOSTILE, TRAP, CLIENT, GAP |

### Known limitations

1. `rename_headers` and `sort_rows` implemented but uncertified, so refused.
2. An ambiguously written date blocks the job until the client answers.
3. Source binding assumes at least one column survives untouched; a job
   transforming every column would trip it.
4. Certification covers modelled defect classes; two previously unmodelled ones
   were found by deliberately inventing them.

---

## 4. report-builder/1.0 — **not promoted**

Renders supplied facts into a markdown report. It has no model and no
generation: a fact it was not given stays missing and is named as missing.

**Technical status:** CERTIFIED, 442/442 trials, 20 defect classes, 0 false
accepts or rejects, surprise battery clean.
**Owner status:** `OWNER_NOT_PROMOTED` — **unchanged by this pass, deliberately.**
It is not registered at all.

**To promote it** the owner would record a decision through the same registry
path used for CSV, bounded to its eight certified checks. Nothing in this pass
infers that approval.

---

## 5. Everything that is *not* a skill

### Proposed, never implemented — 7
From the service scorecard in `solvent/services.py`: `pdf_data_extraction`,
`document_formatting`, `research_summary`, `presentation_creation`,
`simple_coding_task`, `data_processing_automation`, `business_analysis`.
Ranking entries with no code, no owner approval to develop, no proof.

### Names in documentation with no implementation — 5
`render_xlsx`, `render_pdf`, `render_report`, `draft_outreach`, `translate_text`.
The first two appear in test scenarios as capability-*gap* triggers; all five
appear in `docs/solvent-all-capability-certification.md` describing a past
simulated owner-decision exercise. **None is claimed to exist**, so there are
zero phantom registry entries — but they are the names most likely to be
mistaken for skills, which is why they are listed.

### Test artifacts — 5, never production
`sloppy-summary/0.1` (bad worker, honest checks), `self-approving/0.1`
(approve-everything verifier), `surprise-victim` (weak verifier),
`minimal/0.1` and `forgetful/0.1` (construction probes). Each is installed for
one test and removed; a test asserts the registry is clean afterwards.

### Infrastructure — 31 modules, excluded
`ambiguity` `audit` `capability` `certgen` `checks` `cli` `clientrelations`
`content` `discovery` `egress` `errors` `feedback` `gate` `governor` `harness`
`ledger` `memory` `metrics` `orchestrator` `owner` `payments` `policy`
`pricing` `projection` `qualification` `readiness` `runtime` `services`
`store` `types` `verifiercert`

None produces a client deliverable. Only `csvwork.py` and `reportwork.py` write
one.

---

## 6. What remains before one controlled real CSV client

The capability is no longer the blocker. Five things are.

| Category | Blocker | Who resolves it |
|---|---|---|
| **Owner decision** | OD-11 — approve a first work source. *A supervised trial with a hand-fed client arguably does not need automated discovery, but the readiness gate requires a permitted non-fixture source.* | owner |
| **External setup** | OD-5 — provision `SOLVENT_OWNER_KEY`. No key, no approvals, so no external action. | owner |
| **Legal / administrative** | OD-1 activation — legal name, address, tax reference, email. Never inferred. | owner |
| **Payment** | OD-2 activation — Stripe credentials and webhook secret. Status `APPROVED_BUT_NOT_ACTIVATED`. | owner |
| **Technical** | OD-3 — the configured model artifact is not the cleared one in a live instance. | owner records the clearance |
| **Communication** | Nothing preauthorises outbound client messages; every reply and clarification is drafted and relayed by a human. | owner (Policy) |
| **Infrastructure** | None outstanding. | — |
| **Optional** | Certify `rename_headers` and `sort_rows` to widen the scope. | owner |

**Readiness now reports 7 of 13 checks ready, 5 blocking** — down from 6, because
"a proven capability to sell" is satisfied for the first time.
