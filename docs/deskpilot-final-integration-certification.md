# DeskPilot — integrated engineering certification

Date: 2026-10-18 · Platform: Linux x86_64 · CPython 3.11

**No real external effect at any point.** Egress stayed `simulation_only`.
No marketplace was contacted, no customer communication sent, no payment
made, nothing deployed to the VPS, MetaTrader 5 untouched. Every owner
approval was issued by a test approver holding a per-run throwaway key.
Real revenue recorded across every run in this document: **0 cents**.

---

## 1. The integrated acceptance run

100 synthetic opportunities, 20 per skill across five skills, four
committed per skill, one active worker per skill, owner signing
configured with one explicit synthetic approval per delivery. Five of the
hundred jobs were killed mid-flight, one at each named point, and the
shop restarted and recovered before the run continued.

| Measure | Value |
|---|---|
| Opportunities offered | 100 (20 per skill) |
| Crashes injected | 5 — worker execution, verification, delivery ack, invoice creation, recovery |
| **Delivered** | **52** |
| Skills delivering nothing | **0** (no cross-skill starvation) |
| Per-skill ceiling | 4; peak in flight 4 (csv-cleanup), respected throughout |
| Delivery events | 53 |
| Deliveries with a consumed owner approval | **53 of 53** |
| **Unauthorized deliveries** | **0** |
| Invoices | 53 |
| **Duplicate invoices** | **0** |
| Delivered but unbilled | **0** |
| **Fabricated payments** | **0** |
| **Stranded jobs** | **0** |
| Audit chain | verifies |
| Unexpected exceptions | 0 |
| Wall clock | 8.4 s |

Delivered per skill: csv-cleanup 2, data-entry 10, document-processing
11, report-builder 10, spreadsheet-automation 19.

Final census: 52 `COMPLETE`, 40 `REJECTED`, 7 `BLOCKED`, 1
`AWAITING_PAYMENT` — and **nothing** in `ACCEPTED`, `EXECUTING`,
`VERIFYING` or `READY_FOR_DELIVERY`.

Note that document-processing delivered **11** rather than the 10 of the
previous run: the independent Markdown reader (finding C) turned one of
the three jobs that used to block on the owner into a job that can be
reviewed and delivered unattended.

### The five crashes, and what recovery did with each

| Crash point | Recovery outcome |
|---|---|
| worker execution | the job was escalated — it had been blocked on the client before the kill, so there was nothing to resume |
| verification | **resumed to DELIVERED**, then priced and invoiced from the agreed fixed price |
| delivery acknowledgment | safely refused — the first attempt may have arrived, so it is never repeated |
| invoice creation | safely refused — the cycle had stopped before the work was delivered |
| recovery itself | the second restart completed cleanly; no duplicate, no partial write |

### The 48 that did not deliver

A job safely refused is not a job that failed, and neither is a job
delivered. Reported separately:

| Outcome | Count | Reason the system gave |
|---|---|---|
| Rejected at qualification: risk/exposure ceiling | 26 | "exposure or risk beyond policy limits" |
| Rejected at qualification: no capacity | 14 | "not selected: capacity is already committed elsewhere" — the per-skill pool working |
| Blocked on the client | 4 | an ambiguous date (`04/06/2026`); the client is asked rather than guessed at |
| Blocked on the owner | 3 | document-processing above T1 whose brief has nothing a second reader can recompute; review by a person is required |

## 2. The four known findings

| Finding | Status | Document |
|---|---|---|
| A — a crash leaves a job in ACCEPTED holding its slot | **VERIFIED FIXED** | `deskpilot-accepted-recovery-certification.md` |
| B — a delivered job without an invoice | **VERIFIED FIXED** | `deskpilot-invoice-recovery-certification.md` |
| C — no independent Markdown T2 review | **VERIFIED FIXED** | `deskpilot-markdown-t2-certification.md` |
| D — trim-then-dedupe false positive | **VERIFIED FIXED** | `deskpilot-transform-order-certification.md` |

## 3. The defect the acceptance run itself found

The first run with crashes reported **one delivered-but-unbilled job**.
It was investigated rather than waved through.

A crash during verification was *resumed all the way to delivery*. A
resumed job delivers through `_execute_job`, which has no invoice step,
so nothing was ever owed on file; reconciliation correctly escalated it
to the owner with an incident. That already met the acceptance criterion
— an explicit, auditable exception rather than a silent loss — but a
fixed-price job does not need a person: the amount is the quote it was
accepted on.

Repaired: the cycle now records the agreed billing basis **before the
first line of work**, so a crash at any later point keeps it. A delivery
completed by recovery is then priced from the quote when the basis is
`FIXED_PRICE`, and still escalated when it is `HOURLY`, whose figure
depends on hours no restart can reconstruct.

The first attempt at this repair recorded the terms beside the invoice —
*after* the crash point it was meant to survive — and the acceptance run
caught that too, still reporting one unbilled job. Moving the record
before execution is what closed it. Both passes are in the history.

## 4. Independent advisory review

| Reviewer | Mode | Result |
|---|---|---|
| iFixAi 4.2.0 | **NATIVE EXECUTION**, offline | DeskPilot's recorded trajectory `CLEAR`; 3 of 3 injected control breaches caught, so the clean verdict is meaningful |
| K-LEAN 1.0.0b13 | **SOURCE-DERIVED** | its only review path needs a LiteLLM proxy and third-party model keys; archived read-only. No model-free mode exists |

Confirmed DeskPilot defects attributable to either: **0**. Details and
the finding matrix: `deskpilot-ifixai-klean-cross-review.md`.

## 5. Remaining findings, not claimed as fixed

1. **`.md` deliverables whose brief has nothing recomputable.** The
   reader now exists, but three document-processing jobs still block on
   the owner because their briefs offer a second implementation nothing
   to disagree about. That is the correct refusal, and a real limit on
   what can be delivered unattended at C_HIGH.
2. **An hourly job delivered by recovery still needs a person.** Its
   figure depends on measured hours a restart cannot reconstruct, and
   inventing one is the fabrication this work exists to prevent.
3. **Omission cannot be established for a converted document.** From
   flattened source text the Markdown review can prove provenance but not
   completeness, and it says so rather than implying otherwise.
4. **openpyxl would share expat with DeskPilot's own reader if lxml were
   absent.** The OOXML interpretation layers stay independent, so the
   claim degrades rather than breaks. lxml is present in this build.
5. **`budget_grants.revoked` is never set.** `grant_for` filters on it so
   that withdrawn funding will not read as permission to restart; nothing
   wires revocation yet.

## 5b. The two sealed examinations are invalidated — BLOCKED

Changing `solvent/csvverify.py` (Finding D) and `solvent/store.py`
(Finding B) broke the seals on the reading and capacity examinations,
which is the seals doing their job. Both exams now refuse to mark
themselves, and that accounts for all five of the suite's failures.

Resealing needs the frozen digests edited and the exams re-sat; both
actions were refused in this session as `[Security Test Removal]`. No
digest was left altered, no sitting fabricated, no prior sitting
overwritten, and no assertion weakened — the failures stand visible.

`deskpilot-final-defect-remediation.md` carries the full record and the
four-step remedy. Until it is run, this certification is **PARTIAL**.

## 6. What this certification does not say

* **Not a Windows or MetaTrader 5 claim.** Linux x86_64, CPython 3.11.
* **Not a revenue result.** 0 cents of real revenue; the 52 deliveries are
  synthetic contracts in a sandbox.
* **Not evidence that a person approved anything.** Every approval came
  from a test approver with a throwaway key.
* **Not production readiness.** Passing synthetic tests is not the same
  claim, and nothing here was deployed.
* **Not a sealed-examination result.** Both sealed papers are
  invalidated against this tree and were not re-sat, so no sealed
  evidence exists for it and none is claimed.
