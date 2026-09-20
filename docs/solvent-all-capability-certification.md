# Solvent — All-Capability Certification

**Scope.** Every client-work capability Solvent actually has, tested to the same
standard, with hidden ground truth and no grading on a curve.

**Posture throughout, verified at the end:** `egress.simulation_only = true`,
egress allowlist empty, 0 ledger entries, 0 proven capabilities registered, audit
chain intact. No real client, proposal, bid, message, invoice, charge, refund,
bank action, purchase or deployment occurred.

---

## 1. What Solvent could actually do at the start

Discovered from the repository, not assumed.

| Capability | Status at start | Evidence |
|---|---|---|
| `csv-cleanup/1.0` | Implemented, 8 operations, 11 independent checks | `solvent/csvwork.py`, `solvent/csvverify.py` |
| everything else | **Not implemented** | no executor, no checks |

"spreadsheet" appearing in `harness.py` is a demo fixture label, not an
implementation. **Exactly one client-work capability existed.**

---

## 2. Capability growth, exercised end to end

Four owner decisions were replayed against detected gaps. All four were
pre-committed before the run and are **TEST_ONLY** — they grant nothing, and a
separate test proves Policy refuses every harness identity.

| Decision | `may_develop` | `may_deploy` | Observed |
|---|---|---|---|
| APPROVED | True | False | gap queued, development permitted, deployment still needs evidence |
| DENIED | False | False | registration refused for `proven=False` and `proven=True`; re-proposing refused |
| LIMITED | True | False | scope recorded and shown |
| REVOKED | True → False | False | history preserved as `['APPROVED', 'DENIED']` |

`report-builder/1.0` was then built under the APPROVED path.

---

## 3. The second capability

`report-builder/1.0` turns supplied facts into a document. It has **no model and
no generation**: there is no mechanism by which it can produce a number, a name
or a date that did not come from the source. A fact that is missing stays missing
and is named as missing.

That is a real limitation and it is the point. *"Write me a report about our
sales"* is not this capability. *"Here are the figures; lay them out under these
headings, total them, and tell me what is absent"* is.

---

## 4. Defects found

Each was found by a trap or a probe, not by reading the code. Each is fixed,
pinned by a regression test, and confirmed by a mutation probe.

### 4.1 CRITICAL — facts matched as substrings

`check_facts_rendered` asked `"client: Acme Corp" in text`. That is true of a
document reading `client: Acme Corporation`. The first trap battery shipped
exactly that report and called it verified.

Whole-line comparison now, in four places. **This was the third appearance of
this bug class** — after the scope matcher (nine rephrasings) and the feedback
classifier (a bank-redirect demand). The comment in the code says so, because
the pattern matters more than the instance.

### 4.2 CRITICAL — a disclosed absence could be filled in

A `missing` section's entire content is an absence. Nothing checked it:
`report_section` is verified by *"the document opens"*, and the invented-facts
check reads **numbers**. So `- approved_by` becoming `- approved_by: J. Rivera`
passed both paths and was delivered.

Solvent now derives `R-DISCLOSED` for itself on any job naming fields it might
have to report as absent. Same failure shape as 4.1 — *figures protected, words
trusted* — one section over.

### 4.3 HIGH — every report complaint read as Solvent's own defect

`feedback.investigate` did `from . import csvverify` unconditionally. Recomputing
a **report's** checklist therefore returned `UNVERIFIABLE` for every requirement,
and the method concluded the artifact was defective and its own verifier had
missed something. A correct report plus an out-of-scope request produced a false
self-accusation — fail-closed in direction, wrong in substance, and it would have
fed the self-correction path a fabricated lesson.

Which library re-checks which work is now read off the artifact's recorded
`capability_version`, through one registry (`solvent/checks.py`). That registry is
a dispatch table, not an authority: it decides nothing about whether work may
ship. Work no library can re-check is reported as **unknown** and routed to a
human — never as pass, never as fail.

### 4.4 HIGH — a capability could run with no safety net and say nothing

`ServiceCapability.derive` defaulted to `None`. `derive` carries the requirements
a client would never think to ask for — *"no figure that nobody supplied"*. A
capability that simply omitted it ran unprotected, silently. Found by building a
deliberately defective capability, which then delivered a report containing a
total of `999.99` appearing in no source.

`derive` is now a required field. Deriving nothing is still allowed, but it has
to be written down (`nothing_derived`).

---

## 5. Certification results

**52 black-box scenarios** across both capabilities, through one runner, one
judge and one set of isolation checks. A capability graded by its own bespoke
suite is a capability graded on its own terms.

| | `csv-cleanup/1.0` | `report-builder/1.0` |
|---|---|---|
| Scenarios | 25 | 27 |
| Unacceptable outcomes | 0 | 0 |
| False completions | 0 | 0 |
| Traps caught | 3/3 | 5/5 |
| Levels covered | L1 L2 L3 BOUNDARY HOSTILE TRAP CLIENT GAP | same |
| Crash battery | passed | passed |

The judge reads the delivered file itself and compares **values, not bytes**.
Solvent's own report of success is never the evidence.

### Crash certification for the new capability

No exemption for being new. Modelled with `KeyboardInterrupt`, which
`except Exception` deliberately does not catch — the durable signature a SIGKILL
leaves. (`subprocess` is unavailable: the egress guard denies `subprocess.Popen`,
and that refusal is a control worth keeping.)

- a document on disk after a crash is **not** a delivered job
- no `PASS` evidence survives a crash that preceded it
- an unregistered document cannot be delivered after restart
- the audit chain survives
- a crash mid-delivery leaves the outcome `ATTEMPTING`; recovery blocks on the
  owner and **performs no external action of its own**
- a truncated document does not satisfy the checklist

### The deliberately bad capabilities

| Capability | Verifier | Outcome |
|---|---|---|
| `sloppy-summary/0.1` | honest | refused — invented total named in the escalation |
| `self-approving/0.1` | approves everything it does | **delivered** by Solvent's pipeline; caught by the harness's independent ground truth |

The second result is recorded plainly rather than hidden. Solvent's pipeline asks
the capability's own verifier, so a self-certifying capability *does* get
delivered. What stops it is that "delivered" was never the certification
standard: the black-box judge recomputes against the source, scores it a false
completion, and **one false completion blocks promotion outright** — no pass rate
offsets it.

### Multi-capability project

A cleaned CSV becomes a report's source. The uncleaned value never reaches the
report, the checklists do not merge, and two distinct verified artifacts are
recorded under two capability versions. The second stage cannot invent a total
for a column the first stage did not produce.

### Mutation probes — 7/7 killed

| Control broken | Tests that failed |
|---|---|
| facts matched as substrings again | 13 |
| absent-fact disclosure always passes | 8 |
| invented figures always pass | 8 |
| report capability derives nothing | 19 |
| `R-VERBATIM` never derived | 8 |
| every capability re-checked with the CSV library | 6 |
| unknown checks no longer block commit | 12 |

---

## 6. Promotion

Measured from an actual run, never typed in:

```
report-builder/1.0 — 27/27 fixtures, 0 false completion(s),
levels L1, L2, L3, 8 checks  →  promotion_verdict: True
```

**It is not promoted.** Registration is an owner act requiring an owner identity,
and no test may hold one. A fresh Solvent has **zero** proven capabilities, and a
test asserts that nothing in this repository promotes it.

Per the standing rule — *one false completion blocks promotion until fixed and
re-proven* — the two CRITICAL defects were fixed and the **complete** battery,
including the crash tests, was re-run clean afterwards.

---

## 7. Residual risks, stated rather than resolved

1. **An owner may register an unproven capability directly.** `register()` requires
   evidence only when a capability was developed under an owner *decision*; one the
   owner registers with no proposal behind it is treated as their own judgement
   about their own business. This is deliberate and documented in the code. It is
   also the one path by which an unproven capability can enter the registry.
2. **Solvent's pipeline trusts the capability's own verifier.** Shown above by
   `self-approving/0.1`. The defence is that promotion requires externally
   measured evidence, not that the pipeline second-guesses the verifier.
3. **`report-builder` cannot write.** It renders supplied facts. Any client asking
   for analysis, narrative or forecasting gets a refusal, correctly — but that is
   a much narrower service than "report writing" sounds like.
4. Everything blocking real revenue in `OWNER_DECISIONS.md` remains blocking.

---

## 8. Capabilities named, as required

- **Failed:** none in the final run.
- **Not tested:** none. Both implemented capabilities face the full matrix, and a
  test fails if a capability exists in `CAPABILITIES` without black-box coverage
  at every level.
- **Not actually implemented:** `render_xlsx`, `render_pdf`, `render_report` (as a
  generative capability), `draft_outreach`, `translate_text`. Each is refused, and
  each reaches the owner's queue as a proposal rather than an opaque error.
