# DeskPilot — independent advisory review: iFixAi and K-LEAN

Date: 2026-10-18 · Platform: Linux x86_64 · Neither repository was
modified; no upstream change was proposed or pushed.

**Standing of these reviewers.** Advisory. Nothing in this document
overrides the Action Gate, owner approval, independent verification, the
financial or risk controls, job admission, delivery authorisation or any
existing security boundary. Where a reviewer and DeskPilot disagree, the
disagreement is recorded and reproduced; it does not change a control.

---

## 1. Source verification

| | iFixAi | K-LEAN |
|---|---|---|
| Repository | `ifixai-ai/iFixAi` | `calinfaja/K-LEAN` |
| Accessible | yes, cloned | yes, cloned |
| Commit inspected | `15599839eccfc2ffcf181b0ca3651413416a9d40` (2026-10-15) | `79f355756f27edc115bb87d956d756429ad3315a` (2026-10-10) |
| Files | 802 | 169 |
| License | Apache-2.0 | Apache-2.0 |
| Package / version | `ifixai` 4.2.0 | `kln-ai` 1.0.0b13 |
| Runtime | Python ≥ 3.10 | Python ≥ 3.9 |
| Status | active | **archived by its author** ("read-only now. the committee has adjourned.") |
| Core dependencies | pydantic, pyyaml, jsonschema, click, aiohttp, python-dateutil, json-repair, rich, questionary | click, rich, pyyaml, httpx, psutil |
| Needs model APIs | **no**, for the mode used here | **yes**, for its only review mode |

## 2. Capability mapping

| Capability | iFixAi | K-LEAN |
|---|---|---|
| Code defect detection | no (it audits agent behaviour, not source) | yes, but only via external LLMs |
| Architecture review | no | via external LLMs |
| Regression analysis | no | no |
| Workflow correctness | **yes** — recorded-trajectory audit | no |
| Failure-mode identification | partial — policy violations in recorded actions | via external LLMs |
| Security review | partial — permission, delegation, approval and budget rules | via external LLMs |
| Test generation | no (for this codebase) | no |
| State-machine verification | partial — authority paths and delegation depth | no |
| Data-integrity verification | partial — capture digests and citation identity | no |

### iFixAi — **NATIVE EXECUTION**

`docs/trajectory-assurance.md` describes a recorded-trajectory auditor
that "audits a retained sequence of agent actions against a business
policy … without a model or service connection." That is the one mode
that applies to DeskPilot, whose five certified skills call no model at
all, and it runs offline.

### K-LEAN — **SOURCE-DERIVED**

Its single review path is `src/klean/reviews.py`, which posts to a
LiteLLM proxy at `http://localhost:4000/chat/completions` and asks
several third-party models for a grade and a verdict. There is no
model-free review mode: `src/klean/tools/` are agent primitives (`read`,
`grep`, `run_tests`, `search_knowledge`) that a model calls and that
produce no findings on their own, and the remaining CLI commands
(`install`, `uninstall`, `status`, `doctor`, `init`) manage K-LEAN's own
Claude Code integration.

Native execution was therefore **not possible and not attempted**: it
needs a running LiteLLM proxy and third-party model API keys this session
does not hold, calling them would be a real external action outside the
scope of this assignment, and the repository is archived read-only.

## 3. Independent review performed

### 3.1 iFixAi — native, offline, against a real DeskPilot run

Two stages, deliberately in separate interpreters:

1. **Export** (DeskPilot's interpreter; iFixAi is installed in a separate
   venv and is not importable here). Six synthetic contracts were run
   through the real business cycle with owner signing configured. The
   append-only audit log was projected to JSON — one record per *action*,
   with actor, action, resource, money authorised and approval used.
   Narrating events were not exported: an auditor cannot be asked to
   judge a log line. **10 actions exported; audit chain verified.**

2. **Audit** (iFixAi's venv; it never imports DeskPilot). DeskPilot's own
   rules were restated in iFixAi's vocabulary — `PermissionRule` per
   role, a trace `BudgetRule`, four actors — and `audit_trajectory` was
   run.

**Result: `CLEAR`. 10 checked actions, 0 violations, 0 coverage issues.**

The recorded shape, twice over: `fund` → `approve` → `consume_approval` →
`deliver` → `invoice`.

**A clean verdict from an auditor that cannot fail is worthless**, so the
same policy was run against three deliberately corrupted copies:

| Injected breach | iFixAi verdict | Code |
|---|---|---|
| a bookkeeper performing a delivery | BLOCK | `PERMISSION_DENIED` |
| a delivery to a resource outside the permitted scope | BLOCK | `PERMISSION_DENIED` |
| spending past the trace budget | BLOCK | `BUDGET_EXCEEDED` |

**3 of 3 caught.** The `CLEAR` on DeskPilot's own trajectory is therefore
a real result.

**iFixAi's own stated limitations**, inherited and reported rather than
glossed: capture completeness, collector identity, actor identity, human
approval and costs are *caller declarations, not authenticated facts*;
the audit is passive and prevents nothing; recorded edges are declared
lineage, not physical causality; integer costs are not billing proof.
This is precisely why the review is advisory and cannot stand in for
DeskPilot's Owner Channel, which authenticates an approval with an HMAC
over every field that matters.

A further honest limit of this bridge: the replay re-emits actions
DeskPilot already took through iFixAi's recorder with a no-op callable.
It is a faithful projection of the audit log, not an instrumented live
run, and iFixAi's documentation is explicit that the recorder does not
prove the arguments match the callable.

### 3.2 K-LEAN — source-derived

K-LEAN's design thesis is that a single reviewer misses what a second
would catch, and that the value is in *surfacing disagreement* rather
than in consensus. Applying that lens to DeskPilot asks a question worth
asking: **are DeskPilot's "independent" readers actually independent, or
do they share an implementation and therefore agree by construction?**

Checked, and reproduced:

| Format | DeskPilot's own reader | The independent reader | Share a parser? |
|---|---|---|---|
| xlsx | `zipfile` + `xml.etree.ElementTree` | openpyxl (lxml here) | no |
| docx | `zipfile` + `xml.etree.ElementTree` | python-docx (lxml) | no |
| csv | standard library `csv` | CleverCSV (own C parser) | no |
| md | line-prefix scan in `reportverify` | markdown-it-py (CommonMark) | no |

**Finding (advisory, not a defect):** openpyxl falls back to
`xml.etree.ElementTree` when lxml is absent. In that configuration it
would share the underlying expat parser with DeskPilot's own `xlsx.py`.
The OOXML *interpretation* layers — where the bugs that matter live —
remain separately implemented, so the independence claim degrades rather
than breaks. Recorded here because the claim should be stated with its
conditions. lxml is present in this build (`openpyxl.xml.functions.LXML`
is `True`), so the strong form holds today.

## 4. Finding matrix

| # | Finding | Source | Independently reproduced | Status |
|---|---|---|---|---|
| 1 | DeskPilot's recorded trajectory violates no permission, approval, delegation or budget rule | iFixAi, native | yes — and the auditor was shown to catch 3 injected breaches | **no defect** |
| 2 | Actor identity and approvals in an imported capture are declarations, not authenticated facts | iFixAi, stated limitation | yes — read in the tool's own output | **not a DeskPilot defect**; DeskPilot authenticates approvals itself via HMAC |
| 3 | openpyxl would share expat with DeskPilot's reader if lxml were absent | K-LEAN lens, source-derived | yes — verified against the installed packages | **advisory**; condition recorded, strong form holds in this build |
| 4 | K-LEAN cannot review this codebase here | K-LEAN, source inspection | yes — no model-free path exists in its source | **not a defect**; capability unavailable |

**Confirmed DeskPilot defects attributable to either reviewer: 0.**
Nothing was fixed on their account because nothing needed fixing on their
account. The defects repaired in this round were found by DeskPilot's own
crash matrix and acceptance run, and are documented separately.

## 5. Repository boundary

Both repositories were cloned read-only and run (iFixAi) or read
(K-LEAN) in isolation. Neither was modified. No issue was filed, no
branch pushed, no upstream change proposed. iFixAi is installed in a
dedicated virtual environment outside the project so that nothing
third-party is importable from DeskPilot's own interpreter.

## 6. Reproduction

```
scratchpad/xreview/export_trajectory.py   # stage one, DeskPilot's interpreter
scratchpad/xreview/run_ifixai.py          # stage two, iFixAi's venv
scratchpad/xreview/deskpilot_actions.json # the exported trajectory
scratchpad/xreview/ifixai_result.txt      # the verdicts above
```

These are scratch harnesses, not product code, and are not committed:
they are bound to a temporary directory and a venv outside the
repository. Their output is reproduced above and in
`docs/evidence/deskpilot-final-remediation.json`.
