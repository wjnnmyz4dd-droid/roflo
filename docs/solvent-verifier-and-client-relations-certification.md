# Solvent — Verifier Certification + Client Relations

**Repository** `wjnnmyz4dd-droid/roflo` · **branch** `claude/solvent-architecture-audit-8ot9et`
**Starting HEAD** `d63dd0a` · **Starting tests** 673 · **Final tests** 798

**Mission unchanged:** *Solvent exists to find, qualify, complete, deliver and
profit from legitimate client work.*

**Posture, verified at the end:** `simulation_only = true`, egress allowlist
empty, 0 ledger entries, 0 proven capabilities, audit chain intact. No real
client, message, proposal, bid, invoice, charge, refund, bank action, purchase,
public review reply or deployment occurred.

One correction to the stated baseline: in a fresh Solvent **neither** capability
is registered or proven, `csv-cleanup/1.0` included. Registration is a runtime
owner act and no persisted state holds one.

---

## Part 1 — The self-approving verifier

### Reproduction

A test capability was built whose verifier returns PASS unconditionally, given
work with two planted defects the verifier was never told about: a total of
`999.99` against a true total of `450.50`, and an `approved_by` field filled
with `J. Rivera` when the source supplied no approver.

| | Result |
|---|---|
| Solvent's pipeline | **delivered**, `AWAITING_PAYMENT` |
| Capability's verifier | 6 × PASS |
| Guardian (`record_verification`) | accepted all 6 |
| Independent recomputation | 3 × FAIL |

It is worse than the previous report recorded. Each of the six rows written to
the permanent audit log carried
`method: "report_no_invented_facts recomputed from source and deliverable"`.
**Nothing had been recomputed.** The evidence record was false in the one place
that is supposed to be true.

`verifier_identity` was `qc`, `executor_identity` was `worker`, so the existing
independence control was *satisfied* — those are labels the caller chooses, not
facts about the code that ran.

### Root cause

`record_verification` enforced two things: **who may attest** (verifier ≠
executor above C_HIGH) and **what was attested** (requirement id + artifact
digest). Both assume the attester is competent, and nothing established that.
`VERIFIER EXISTS ≠ VERIFIER IS TRUSTWORTHY`.

### The architecture, and why it is not a second Guardian

`record_verification` remains the single chokepoint and gained one more
condition. There is no `guardian_v2`, no second verifier, no parallel delivery
gate. Two pieces support it:

- **`solvent/verifiercert.py` — measures, decides nothing.** It builds artifacts
  whose correctness it already knows, hands them to a verifier with no
  indication of which trial is which, and reports what came back. It never
  verifies client work, never touches a job, never records evidence, never
  authorizes a delivery.
- **`CapabilityRegistry` — judges.** The registry already owns
  *what-is-proven-on-what-evidence*; a verifier's competence is the same
  question about a different subject, so it lives there rather than in a new
  authority.

Evidence above T0 must now name the verifier **implementation** — derived from
the callable (`module.qualname`), never typed — and that implementation must be
certified to decide that check for that capability version. The audit log asks
the registry through an injected collaborator, so neither module imports the
other.

States: `UNTESTED` · `CERTIFIED` · `CERTIFIED_WITH_LIMITS` · `FAILED` · `REVOKED`.

### Hidden ground truth

The verifier is handed a source path and an output path. It is never told which
trial it is in, whether the artifact should pass, or what the expected answer
is. Trials are generated fresh per run into a temporary directory with
unremarkable filenames, so it cannot recognise them by path.

**Both directions disqualify.** Approve-everything fails on false accepts;
reject-everything fails on false rejects. They are counted separately rather
than averaged, because one ships wrong work while reporting success and the
other can never ship correct work — different failures needing different fixes.

**A check is certifiable only if the battery tried it in both directions.**
Tested only on defective work a reject-everything stub scores perfectly; tested
only on correct work, so does a rubber stamp.

**The battery is chosen by the checks a verifier claims to decide**, not by the
capability's name. The first attempt at this blocked the malicious verifier for
the wrong reason — "no battery exists for `self-approving/0.1`" — which a
capability could evade simply by being called something new.

### Defect injection results

| Capability | Verifier | State | Trials | Good / Bad | False accepts | False rejects | Checks |
|---|---|---|---|---|---|---|---|
| `csv-cleanup/1.0` | `solvent.csvverify.run` | **CERTIFIED** | 19/19 | 9 / 10 | 0 | 0 | 9 |
| `report-builder/1.0` | `solvent.reportverify.run` | **CERTIFIED** | 28/28 | 10 / 18 | 0 | 0 | 8 |

CSV defects injected: duplicate remains · row dropped · protected total changed
· total changed by one cent · date with day and month transposed · required
column missing · not-a-CSV · whitespace not trimmed · region unmapped ·
unauthorised transformation.

Report defects injected: fact altered · **similar-but-not-identical name** ·
fact omitted · fact qualified after a correct value · number invented ·
number changed · total wrong · total plausibly wrong by 50p · plausible
approver invented · absence silently dropped · row missing from table · section
missing · **fact under the wrong heading** · empty document · table column
renamed · total section stating nothing.

### Two real defects the batteries found immediately

**`parses_as_csv` accepted a file of NUL bytes.** This is the SYSTEM_SAFETY
requirement carried by *every* cleanup job — the one guaranteeing the client can
open what they were sent — and "parses" meant only that `csv.reader` had not
raised, which it does at almost nothing. It now rejects binary control bytes,
a header with no column names, and an empty result from a source that had rows.

**`report_section` was registered but aliased to `check_opens_as_text`.** A
requirement reading *"an Overview section naming the client and period"* was
satisfied by any document with any heading at all. It is now a real check, which
also catches a fact rendered under the wrong heading — correct content in the
wrong place, which a document-wide search cannot see.

### Retest (§11)

| | Before | After |
|---|---|---|
| Certification | *(did not exist)* | **FAILED**, 10/28 trials, 18 false accepts |
| Blocked because | — | it is **wrong**, not merely untested |
| Evidence recorded | 6 × PASS | **0 rows** |
| Delivered | yes | no |
| Refusal audited | no | yes, `decision=FAILED` |
| Caught by | external harness only | **Solvent itself** |

Degenerate verifiers: approve-everything → FAILED (false accepts);
reject-everything → FAILED (false rejects); a verifier that raises → FAILED
(every trial ERROR); a partially-wrong verifier → CERTIFIED_WITH_LIMITS, trusted
only on the checks it got right and refused on the one it did not.

### Scoping and revocation

Certification is per `(verifier_ref, capability_version, check)`. A verifier
proven on `csv-cleanup/1.0` is refused for `report-builder/1.0` ("never been
tested"), and being right about duplicate rows is not evidence about dates.

Revocation requires a reason on the record, stops the verifier immediately, does
not touch the earlier `CERTIFIED` row (history reads `['CERTIFIED', 'REVOKED']`),
and is scoped — revoking the CSV verifier does not stop the report verifier,
which did nothing wrong.

---

## Part 2 — Client relations

### Source-first audit

| Responsibility | Already owned by |
|---|---|
| Feedback intake, classification | `ClientFeedback.receive` / `_classify` |
| Defect investigation | `ClientFeedback.investigate` |
| Revision, closing | `ClientFeedback.open_revision` / `close` |
| Learning gate | `BusinessMemory.learn` |
| Owner approvals | `OwnerChannel` |
| External effects | `ActionGate` |

**Genuinely unowned:** conversation assembly, sentiment, response preparation,
escalation, loop limits, resolution tracking.

### Extended, not duplicated

`feedback.py` gained three classifications that were genuinely absent —
`GENERAL_QUESTION`, `REFUND_REQUEST`, `HOSTILE_OR_ABUSIVE` — plus dispute
recognition. Categories that already existed were reused: `ACCEPTED` *is*
positive feedback, `DEFECT` *is* a defect report, `UNSAFE_REQUEST` *is* a
prohibited request. Two names for one concept is how routing tables drift apart.

`solvent/clientrelations.py` owns one table (`client_responses`) and calls the
feedback authority for everything the feedback authority already does.

### Authority boundaries

May: listen · classify · explain · route · request clarification · prepare
replies · track resolution.
May not — verified by test, and by storage-level table ownership: change Policy ·
change the Governor · change price · issue refunds · move money · change bank
details · approve scope · promote capabilities · override the Guardian · mark
defective work correct · mark correct work defective · rewrite requirements ·
erase audit history · grant owner approval.

### The three rules

**Facts come from records.** *"I definitely asked for the totals to be
recalculated, it was obviously included"* is answered by quoting the
requirements table. Replies are assembled from the committed checklist, the
artifact identity, the verification report and the job state — there is no
generation and no room for one, so a reply cannot promise unauthorised work or
concede a point the evidence does not support.

**Sentiment is a signal.** It is computed from the message alone — it cannot see
the job — and *after* the investigation has run. A test asserts that ordering in
the source, because it is the control: tone cannot reach the evidence.

**A reply is an external effect.** Nothing is sent without the Action Gate and
an owner approval. Nothing preauthorises one today, so **Solvent drafts client
replies and does not send them.**

### Results

| Scenario | Result |
|---|---|
| Normal / satisfied | recognised; verification record untouched |
| Dissatisfied, **client correct** | defect confirmed from recomputation, failing requirements quoted, client not asked to prove it |
| Dissatisfied, **client wrong** | no false defect; named as a change, not a correction |
| Difficult / "Karen" | professional, factual, non-retaliatory; no governance surrendered |
| Changes mind (×3) | committed checklist unchanged |
| Scope creep (×3) | artifact digest unchanged — no unrequested work |
| Refund / dispute / threat | escalated; 0 ledger entries |
| Reviews (10 shapes) | classified and routed; no reply pressures, compensates or retaliates; no publish method exists |
| Prompt injection (cells, report facts, message bodies, reviews) | stayed data; policy version unchanged |
| Manipulation (10 demands) | nothing governed changed; none became an admission of fault |
| Concurrent clients (7) | only the real defect confirmed; no reply named another client |
| Client isolation (`john-smith` / `john-smythe`) | no leakage in conversation, replies or response rows |
| Crash / restart (6 points) | no duplicate reply, no lost complaint, no lost escalation, chain intact |
| Memory poisoning | 0 memory facts written; relations has no write access |
| Capability growth | no "yes we can"; denial not rephrased; job still refused |

**The four corners (§47):** good artifact + furious client → not a defect. **Bad
artifact + delighted client → still a defect.** Bad + angry → confirmed from
evidence. Five-star review on broken work → still broken.

**Combined with the verifier attack (§48):** a capability with an
approve-everything verifier is refused before any client says anything, and
neither *"Looks perfect"* nor *"This is wrong"* changes that.

### One defect found while testing

**A payment dispute matched nothing and did not escalate**, although `DISPUTE`
was already a classification and §35 names it an escalation trigger. Severity:
medium — it routed to the owner as `AMBIGUOUS`, so nothing unsafe happened, but
a time-sensitive chargeback was filed alongside ordinary confusion. Fixed with
dispute recognition ahead of refund recognition: once a chargeback exists, what
Solvent thinks about the refund is no longer the only thing happening.

---

## Mutation testing

18 controls broken one at a time. **First run: 8/18 killed.** Nine controls
could be deleted with no test noticing, and one mutant was badly constructed.

Several survivors were *behaviourally* redundant — a FAILED verifier is also
certified for no checks, so deleting the FAILED branch changed no outcome — but
redundancy nobody has verified is indistinguishable from a control that does
nothing.

The sharpest finding was the loop limit. The suite appeared to cover it, but its
probe messages differed only by a digit and the repeat detector strips digits,
so the **repeat** control was carrying the test and the **limit** could be
deleted freely. Ten genuinely distinct messages separate them.

The last survivor was artifact binding: `passes_for_artifact` was tested
directly, but nothing tested that `verification_satisfied` *uses* it — so a
correction could inherit the passes of the artifact it was correcting.

**After adding 23 tests: 18/18 killed.**

---

## Scorecards

### Verifiers

| | `csv-cleanup/1.0` | `report-builder/1.0` |
|---|---|---|
| Verifier | `solvent.csvverify.run` | `solvent.reportverify.run` |
| Certification | **CERTIFIED** | **CERTIFIED** |
| Good artifacts | 9 | 10 |
| Bad artifacts | 10 | 18 |
| Subtle traps | 7 | 6 |
| False accepts | 0 | 0 |
| False rejects | 0 | 0 |
| Requirement coverage | 9/9 checks | 8/8 checks |
| Artifact binding | digest-scoped, tested at the gate | same |
| Cross-job isolation | passed | passed |
| Crash behaviour | passed | passed |
| **Final status** | **CERTIFIED** | **CERTIFIED** |

### Client relations

All rows below passed for **both** capabilities: normal · satisfied ·
dissatisfied-correct · dissatisfied-incorrect · difficult · changing ·
scope-creep · ambiguous · refund · review · prompt-injection · abandonment ·
crash/recovery · cross-client isolation · false self-blame · false client-blame ·
escalation · resolution. **Final status: ready for supervised use, replies
drafted not sent.**

---

## Remaining risks

1. **Replies are drafted, never sent.** Nothing preauthorises outbound client
   communication, so a human still relays every reply. Deliberate, and the
   single largest gap between this and an autonomous service.
2. **A small battery can certify by luck.** Each check has a handful of trials.
   A verifier right by chance on both directions of one check is certified for
   it. Both-directions is the structural half of the fix; more trials per check
   is the rest, and is not done.
3. **An owner may register an unproven capability directly.** Documented in the
   code; still the one path an unproven capability can enter the registry.
4. **`report-builder` cannot write.** It renders supplied facts. Any client
   asking for analysis, narrative or forecasting is refused — correct, but a far
   narrower service than "report writing" sounds like.
5. **Sentiment is regex-based.** Sarcasm, understatement and non-English reads as
   NEUTRAL. It changes only wording, so the blast radius is tone.
6. Everything blocking real revenue in `OWNER_DECISIONS.md` remains blocking.

## Status

- **`report-builder/1.0` technical status:** certified, its verifier CERTIFIED on
  8/8 checks, full battery re-run clean under the strengthened system.
- **`report-builder/1.0` owner-promotion status:** **NOT PROMOTED.** Unchanged
  and deliberately untouched by this work.
- **Technically ready for a controlled client trial?** Yes for the work and the
  verification; **no for unattended operation**, because replies require a human
  to relay them and the owner decisions in `OWNER_DECISIONS.md` are unanswered.
