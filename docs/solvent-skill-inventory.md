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
| **Owner-promoted** | **1** (`csv-cleanup/1.0`, eleven certified checks) |
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

### Exact proven scope — eleven certified checks

`drop_exact_duplicates` · `map_values` · `no_unauthorised_changes` ·
`normalise_dates` · `parses_as_csv` · `preserve_columns` · `rename_headers` ·
`require_columns` · `row_reconciliation` · `sort_rows` · `trim_whitespace`

### What changed, and why it is still eleven and not thirteen

OD-12 promoted nine. `rename_headers` and `sort_rows` were implemented but
their checks had never been shown wrong work, so a job requiring either was
refused fail-closed. **OD-14** authorised promoting them on condition they meet
the same standard. They do now — after three defects found in the attempt were
fixed, all of them in checks that were *already* certified (see §5).

Nothing else moved. A check with no certification behind it is still refused,
and every operation outside this list is still outside it.

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
| Verifier | `solvent.csvverify.run` @ `sha256:dd27d3bbf8f424e72dbfd11e418710cd` |
| Certification | **CERTIFIED**, 526/526 trials, 0 false accepts, 0 false rejects |
| CSV defect classes | 21 |
| Development holdout | 524/524 |
| Surprise (unseen seed) | 526/526 |
| Five further unseen seeds | clean on all five |
| Black-box scenarios | 34/34 acceptable, **0 false completions** |
| Levels | L1, L2, L3, BOUNDARY, HOSTILE, TRAP, CLIENT, GAP |
| Mutation testing of the new controls | 21 mutants, 21 killed |

The fingerprint changed because three checks were hardened. That invalidates the
previous certification by construction — which is the binding working, not a
problem — and a fresh certification runs before any delivery.

### Known limitations

1. An ambiguously written date blocks the job until the client answers.
2. Source binding assumes at least one column survives untouched; a job
   transforming every column would trip it. Row-level binding needs **two**
   such columns, and falls back to the weaker per-column comparison below that.
3. Date-to-row binding needs a column whose values are unique and unchanged on
   both sides. Without one, a date moved onto the wrong row is caught only when
   the day it landed on is not a reading of some other row's date.
4. Certification covers modelled defect classes. Five of the twenty-one were
   added only because these two operations were certified, and three real
   defects came with them — the list is a record of what has been thought of,
   not a proof that nothing else exists.
5. Sorting is textual and stable, by design. A client who means numeric order
   will get `10` before `9`; nothing detects that they meant something else.

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
| **Legal / administrative** | OD-1 activation — **legal name and email** to name the contracting party. Address is needed only to issue an invoice; a tax reference only to connect a payment rail, and it is recorded as present, never as a number. Never inferred. | owner |
| **Payment** | OD-2 activation — Stripe credentials and webhook secret. Rail status `SELECTED`. | owner |
| **Technical** | OD-3 — the configured model artifact is not the cleared one in a live instance. | owner records the clearance |
| **Access** | OD-5 — no `SOLVENT_OWNER_KEY`, so every consequential approval fails closed. A key shorter than 32 bytes, a placeholder, or a value with no randomness in it is refused. | owner provisions it outside this repository |
| **Work source** | OD-11 — no non-fixture source is registered and approved. The owner-entered manual source needs registering; it makes no external call. | owner |
| **Communication** | Nothing preauthorises outbound client messages; every reply and clarification is drafted and relayed by a human. | owner (Policy) |
| **Infrastructure** | None outstanding. | — |

**Readiness reports 8 of 13 checks ready, 5 blocking**, with the contracting
structure and the proven capability now satisfied. Supplying the owner's name
and email clears a sixth.
