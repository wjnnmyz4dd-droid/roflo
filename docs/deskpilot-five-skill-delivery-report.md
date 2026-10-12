# DeskPilot — delivery report: the five-skill library

Scope: the work asked for in the master completion prompt's Phase 7 (five
commercial skills), its certification through the existing Skills Lab, and the
regression, mutation, adversarial, K-LEAN and iFixAi review process over that
work.

Every line below is classified as one of:

- **IMPLEMENTED AND VERIFIED** — built, and verified by something that would
  have failed if it were wrong.
- **IMPLEMENTED BUT WINDOWS-UNVERIFIED** — built and tested on Linux; no
  Windows machine has run it.
- **PARTIALLY IMPLEMENTED** — some of it works and the rest is named.
- **BLOCKED** — cannot proceed without something this environment does not
  have.
- **NOT IMPLEMENTED** — not built.

## 1. The five skills

| # | Capability | Classification |
|---|---|---|
| 1 | `csv-cleanup/1.0` | IMPLEMENTED AND VERIFIED |
| 2 | `report-builder/1.0` | IMPLEMENTED AND VERIFIED |
| 3 | `spreadsheet-automation/1.0` | IMPLEMENTED AND VERIFIED |
| 4 | `data-entry/1.0` | IMPLEMENTED AND VERIFIED |
| 5 | `document-processing/1.0` | IMPLEMENTED AND VERIFIED |

Each performs actual work. Each has a worker and a verifier that does not
import it, so the two either agree or disagree meaningfully. None uses a
model: every figure is computed by arithmetic in its own module from values in
the input, by a rule a requirement named.

Two of the five existed before this session. Three were built in it, and so
were two format layers (`.xlsx` and `.docx`) from the standard library alone,
because Solvent imports nothing else and the Windows installer's virtual
environment has no packages in it.

## 2. Certification through the Skills Lab

**IMPLEMENTED AND VERIFIED.** All five verifiers reach CERTIFIED through the
production path (`harness.ensure_verifier_certified`) on a real database, with
no false accepts and no false rejects. 63 checks are credited. A third seed
nothing was tuned against ambushes each one.

A check is credited only when it was right in both directions over enough
distinct evidence: ten distinct correct artifacts accepted, every defect class
it was shown caught, five distinct instances of each class. A check nobody put
a defect in front of is not certified however many correct artifacts it
approved.

Each new skill went through the lifecycle: need, overlap gate, evidence,
specified, built, certified. It stops there.

### What certification does not mean

It does not mean a verifier is correct. A defect class nobody modelled is not
in the battery. The honest reading is "met the stated evidence requirements".

One check of sixty-four can never be certified by any evidence:
`spreadsheet-automation.number_format`. A display format changes how a figure
is shown and not what it is, so there is nothing to recompute and nothing a
wrong answer would look like. It is recorded in `harness.UNCERTIFIABLE` with
that reason, excluded from `certifiable_scope`, and a promotion that claims it
is refused.

## 3. Promotion — one of five

**IMPLEMENTED AND VERIFIED**, and the answer is deliberately not five.

| Capability | Owner decision |
|---|---|
| `csv-cleanup` | APPROVED — cleared for client work |
| `report-builder` | LIMITED — build and test only |
| `spreadsheet-automation` | LIMITED — build and test only |
| `data-entry` | LIMITED — build and test only |
| `document-processing` | LIMITED — build and test only |

The four LIMITED capabilities are technically certified and cannot be used for
client delivery: delivery asks the registry for permission and there is no
promotion to show it. Clearing any of them is your act. Nothing in this
codebase performs it, and a test asserts that.

## 4. Testing

**IMPLEMENTED AND VERIFIED.**

| | |
|---|---|
| Full suite | **3357 pass, 2 skips** |
| Tests for this library | 343 across seven files |
| Certification trials | 2485, all correct |
| Surprise-battery trials | 3152 from a third seed, all correct |
| Black-box scenarios | 115 total, 54 of them new |
| Mutation battery | **174 of 174 killed** — no survivors, none unapplied, none invalid |

A mutant counts as killed only when the baseline was green, the mutation
actually reached the file, the *designated* test newly failed, and the failure
went away on revert. Twenty-four of the 174 are for this library: each
restores a defect found while building it or breaks a check a client's
deliverable rests on.

Every capability faces the black-box matrix: ordinary work at three complexity
levels, boundaries, hostile input, false-success traps and real client
behaviour, graded against ground truth written before any run. The guard that
requires this was already in the suite and failed the moment the three new
capabilities were registered, which is the guard working.

## 5. Advisory reviews

**IMPLEMENTED AND VERIFIED.** Both are advisory: neither can override owner
authority, a security control, capability certification, a platform
restriction, a financial control, the runtime's execution authority, or the
requirement that a capability be promoted before it is sold.

**K-LEAN** found one path worth closing. A verifier explaining a failure
quotes the client's own text — which is what makes the explanation useful —
and that string went into the `verification_evidence` table unredacted, which
the control centre reads and every backup copies. A credential inside a
client's contract would have landed there. The redactor already existed and is
now applied at the write, because a secret that reached the table is already
stored.

The deliverable is deliberately untouched: redacting a client's file would be
altering their data without authorisation, and a data-entry exception whose
values had been removed would not tell them which record failed.

**iFixAi** produced the chain tests — each brief is given several defects,
fixed one at a time, and the refusal has to move each time — and found the
weakest test in this work, where a mutation survived because the test asserted
a FAIL plus two loose substrings that a *different* branch also produced.

## 6. Defects found and fixed

Thirteen, listed because the count is the useful part of a report like this.

Found by integration, not by either unit suite:

1. `sheetwork.plan` read a requirement field the orchestrator does not supply,
   so the spreadsheet skill would have refused every requirement in the live
   pipeline while its 54 tests passed.
2. The pipeline's verification loop never handed verifiers the brief, so every
   property of a whole job answered UNVERIFIABLE and blocked delivery with no
   visible cause.
3. `Finding` objects lacked the fields the pipeline reads, so every
   certification trial returned "verifier returned no usable outcome" — an
   ERROR counted against verifiers that were perfectly correct.
4. `select_columns` moved the delivered columns and left the formulas behind,
   so a computed column arrived with correct numbers and no formulas and the
   verifier refused a reasonable job.

Found by the generated battery:

5. `row_reconciliation` counted rows and called it reconciling; a correct
   deliverable from a different job passed it.
6. `consolidate_sheets` could only ever say yes; a job that stacked one
   quarter twice instead of two satisfied reconciliation and is now caught.
7. `require_fields` checked one direction, so a field the document plainly
   states, delivered as "not stated", passed.
8. `no_invented_content` ignored the tables, so a fabricated line item in an
   invoice extraction passed.
9. `opens_as_workbook` was listed as checkable with no implementation, so a
   requirement naming it was answered UNVERIFIABLE.

Found by running the two halves of a capability against each other:

10. The `.docx` Markdown writer escaped a pipe inside a cell and the reader did
    not unescape it, so a client's "x | y" came back as two columns.
11. The worker accepted `1e5` as a decimal where the verifier did not;
    delivering the string `1e5` in a column a client's schema calls a decimal
    is not a figure anybody typed.
12. `no_invented_values` claimed multiset containment and implemented set
    membership.

Found by reading for a crash rather than a wrong answer:

13. A template naming the same field twice made the verifier raise, because
    each placeholder became a capture group with the same name.

Two more are worth naming as near-misses rather than defects: the Skills Lab's
overlap gate compared check names over one global namespace, so a skill that
trims whitespace in a worksheet looked like a widening of the one that trims
whitespace in a CSV; and the ambiguity detector asked which total was wanted
about a requirement that named the column and the aggregation explicitly.

## 7. The Control Centre

**IMPLEMENTED AND VERIFIED.** The Capabilities page carries a skill library
section with two separate columns: what the evidence shows, and what you
decided. Before it existed the page showed one row — correctly, since one
capability is promoted — and under a heading reading "Capabilities" that said
the library was one skill.

Nothing on the page is hard-coded. A sixth capability would appear because the
registry gained one.

## 8. The Windows installer

**IMPLEMENTED BUT WINDOWS-UNVERIFIED.**

| | |
|---|---|
| File | `windows/installer/dist/DeskPilot-Setup.exe` |
| Source HEAD | `16b32bd`, from a clean tree |
| Size | 1,620,163 bytes |
| Installer SHA-256 | `bb1525a66c6ad4d5a3783d11d12e4aeffe321589d0df75a948e57f25fd44dad2` |
| Payload | `DeskPilot-certified-16b32bd.zip` |
| Payload SHA-256 | `41ced03f6de19293014fa2f3b7db525419132b4f105e1188d6d4f9acfcb64e63` |
| Code signature | none |
| Windows execution | not verified |

Verified by extracting the compiled executable rather than by reading the
build script. The embedded payload's SHA-256 matches the payload built beside
it; all eighteen engine modules are byte-identical to the repository's; and
the five capability names were read from the *shipped* `checks.py` and
`harness.py` syntax trees rather than by searching for strings.
`harness.UNCERTIFIABLE` in the shipped payload names the one check no evidence
can certify, so the limit travels with the build rather than living only in
this report.

A repair keeps the database, the owner signing key, the control-centre
password hash, the API keys, the settings and the backups. The four new
capabilities arrive **undecided**: installing this does not promote anything,
and a capability already promoted stays promoted.

The previous build (`18f35c8a…`, HEAD `a5bdfc0`) is marked superseded in
`windows/installer/ARTIFACTS.md` with the reason — not defective, but its
payload is Solvent before this library and before the audit redaction.

**Do not install this on the VPS yet.** You asked for the package to stay
under development, and nothing here changes that. No Windows machine has run
it.

## 9. What was not done

- **MetaTrader**: untouched. Nothing in this session's work goes near a
  trading terminal, a trading account, an Expert Advisor, a broker connection
  or a scheduled task other than DeskPilot's own two.
- **Windows verification**: NOT IMPLEMENTED, and it cannot be from here. No
  Windows machine has run any of this. The Windows isolation boundary rests on
  a model of NTFS semantics that was wrong twice during development; the
  installer's own probe settles it on your machine and nothing else can.
- **Code signing**: BLOCKED. No code-signing certificate exists in this
  environment and none ever did. The installer is unsigned and Windows will
  show an unknown-publisher warning.
- **Phase 9 (AI model setup in the browser) and Phase 10 (notifications and
  approvals)**: both have working surfaces from earlier sessions, and neither
  was re-examined in this one. They are IMPLEMENTED and **not re-verified
  this session**, which is a different claim from verified.
- **Promotion of the four LIMITED capabilities**: NOT IMPLEMENTED, by design.
  It is your decision.
