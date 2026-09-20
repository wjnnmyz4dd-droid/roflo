# Solvent — Owner Decision Register

Decisions only the owner can make. Each entry says exactly what it blocks and,
more importantly, **what it does not block** — so work continues around it.

Nothing here is guessed at, defaulted silently, or worked around. Where a safe
default exists it is stated and already in force.

**Status at this commit: the P0 architecture is built and tested. No real money,
no real client, no external effect, and no marketplace connection exists.**
External execution is fail-closed (`egress.simulation_only = true`, empty
allowlist), so none of the open decisions below can be tripped over accidentally.

---

## OD-1 — Which legal entity bears contractual responsibility? · **ANSWERED — identity fields remain**

**Decision (owner, this commit): the owner contracts under their own legal name.
No LLC is being formed at this time.**

Solvent has no legal identity of its own. It is software acting on the owner's
behalf and must never present itself as a company, a partnership, a licensed
professional or any other status the owner has not established. Recorded as
`governance.contracting.structure = INDIVIDUAL` through
`PolicyStore.record_contracting_structure`, which is owner-path only.

**What the decision does not include.** The owner's legal name, address, tax
reference and contact email are theirs to supply. They are not in this
repository and nothing infers them: each reads as
`OWNER_PROVISIONING_REQUIRED` until the owner records it, and
`contracting_party_for_documents()` refuses to complete a document without them.
Solvent generates no contract text today; that accessor exists so that if it
ever does, the party comes from the owner's configuration and from nowhere else.

**Decision made ≠ activation data complete.** Readiness now reports those as two
checks, because conflating them is how an answered decision stays "unanswered"
for want of data the decision never included.

<details><summary>Original entry (superseded)</summary>


**Question.** When Solvent accepts a job, which person or company is the
contracting party?

**Why required.** Acceptance is a legal act, not only a business one:
deliverables, liability, dispute exposure and tax reporting (W-9/1099 or local
equivalent) all attach to somebody. Solvent cannot accept work on behalf of an
undefined party, and no amount of engineering removes this from the critical path.

**Blocks.** Accepting any real job. Issuing any real invoice.

**Does NOT block.** Everything built so far: the full architecture, the complete
fixture job loop, all 192 tests, pricing, calibration, and any further internal
development.

**Safe default in force.** None assumed. `egress.simulation_only = true` means
nothing can reach a real client.

**Options.** (a) Name an existing entity. (b) Operate as a sole proprietor under
the owner's own name. (c) Form a new entity first.

**Recommendation.** (a) or (b) — whichever is already true today. This is a
recording decision, not a formation project, unless the owner wants liability
separation before the first job.

**Consequence.** Until answered, Solvent can prove the loop but cannot run a real
one.

</details>

---

## OD-2 — Which payment rail? · **ANSWERED — operational setup remains**

**Decision (owner, this commit): Stripe is the approved initial payment rail.**

Recorded as `payment.approved_rail = stripe` with
`verification_signal = stripe_webhook_signed_event`, through
`PolicyStore.approve_payment_rail`. Its operational state is
`APPROVED_BUT_NOT_ACTIVATED`: choosing a rail creates no credential, no webhook
secret and no allowlist entry, and readiness reports "chosen" and "operational"
as separate checks.

**What Stripe is not.** Not the Financial Governor, the Ledger, the Audit Log or
the Policy Store. A signed event proves who sent it and nothing else: amount,
currency, client, job, invoice, collection and payout are all checked separately,
and a client saying "I paid" — with or without a confirmation number — moves no
money. Collection is not payout; revenue counts what was collected, and a payout
event never touches it.

<details><summary>Original entry (superseded)</summary>


**Stripe adapter implemented and tested** (`solvent/payments.py`), verified
against Stripe's primary documentation: HMAC-SHA256 over `"{timestamp}.{raw_body}"`,
`v1` schemes only (ignoring others prevents a downgrade attack), constant-time
comparison, 5-minute replay tolerance, idempotency by event id, and refusal of an
empty signing secret — a published advisory describes exactly that bypass. The
rail cannot write the Ledger, move money, or touch the store; 29 tests cover it.

Customer payment and bank payout are tracked separately: `payout.paid` explicitly
leaves collected revenue unchanged. Test-mode money is recorded and excluded from
real revenue by construction.

**Still required from the owner:** create the webhook endpoint, subscribe to the
needed events, inject the `whsec_` secret in the Gate namespace, confirm payout
configuration, and exercise the path in test mode. See
`docs/solvent-first-revenue-activation.md` §3.

<details><summary>Original entry (superseded)</summary>

**Question.** Which rail collects client money, and does it emit an event
(webhook, API, reconciliation feed) that confirms payment arrived?

**Why required.** Two distinct reasons. Most consumer P2P services prohibit
business use on a personal account. And Solvent's entire economic loop is gated on
**T3 external verification** — without a verification signal, `PAID` is
unreachable, so profit cannot be computed and calibration never gets a sample.
A rail with no verification signal does not merely inconvenience Solvent; it makes
the loop unprovable.

**Blocks.** Real revenue. Real actual-profit figures. Real calibration samples.

**Does NOT block.** The fixture loop, which uses a `SIMULATED:`-prefixed
verification method that `Ledger.real_revenue_cents()` excludes by construction.

**Safe default in force.** Simulated rail only. Fixture money can never be
reported as revenue — enforced in code, not by convention.

**Options.** (a) A processor with webhooks (Stripe and similar). (b) Bank
transfer with manual reconciliation. (c) A marketplace's own escrow.

**Recommendation.** (a) if available: it gives a machine-verifiable T3 signal.
(b) works but each payment carries a weaker, manually-asserted verification, which
the Ledger records honestly and which must never feed autonomy evidence.

</details>

</details>

---

## OD-3 — Model commercial rights · **ANSWERED — hybrid policy, per-model clearance**

**Direction (owner, this commit): a hybrid model policy.** Prefer appropriately
licensed local or open-weight models for routine work; permit approved cloud
providers where the work genuinely needs more capability, subject to commercial
rights, cost, privacy and Policy.

**Hybrid is not blanket authorisation.** Approval is per artifact and keyed on
the content digest, with no family or prefix matching — within Qwen2.5 the 3B is
research-only while the 7B and 14B are Apache-2.0, so "it's a Qwen model"
answers nothing. `PolicyStore.approve_model_artifact` now records placement,
provider, commercial-use status, a privacy ceiling, a declared cost and
demonstrated capabilities alongside the existing licence evidence.

Selection prefers local, falls back only to independently approved models, and
refuses outright when none fits: an unapproved model is not a fallback. A
licence is not a capability and a capability is not a licence. The declared cost
is reported for the Financial Governor to rule on — model routing is not a
second economics authority.

<details><summary>Original entry (superseded)</summary>


**Artifact, read from `roflo.toml` rather than remembered:** backend `ollama`,
tag **`qwen2.5:14b-instruct`** — a **Q4_K_M GGUF quantisation**, model layer
`sha256:2049f5674b1e…` (8.99 GB), derived from `Qwen/Qwen2.5-14B-Instruct`.

**Finding: Apache-2.0, and the chain is closed.** The licence blob Ollama
packages inside that tag (`sha256:832dd9e00a68…`, 11,343 bytes) is
**byte-identical** to the publisher's own `LICENSE` file on Hugging Face — same
SHA-256, compared directly, not two web pages read separately. It is the Apache
License 2.0, "Copyright 2024 Alibaba Cloud". The prior gap — *does the Ollama tag
package those exact terms?* — is closed by hash equality, not by assertion.

Apache-2.0 permits commercial use, imposes **no term on model output**, carries
no acceptable-use policy (the HF repo has no such file and is not gated), no
field-of-use limit, no revenue share and no user-count threshold. Its §4 notice
duties attach to **redistribution**; Solvent consumes output.

**Why this is recorded per artifact and never per family.** Within Qwen2.5 the
licence differs by size: 0.5B/7B/14B/32B are Apache-2.0, **3B is `qwen-research`
— "FOR NON-COMMERCIAL PURPOSES ONLY"** — and 72B is the `qwen` licence with a
100-million-MAU threshold. A family-level clearance would have cleared the
research-only 3B for paid work. So Solvent keys clearance on the **content
digest**, which a tag cannot launder.

**Material conditions the owner inherits:**

1. **No trademark licence** (§6) — do not brand the service with Qwen or Alibaba
   marks.
2. **No warranty** (§7–8) — bad output is Solvent's liability, not the
   publisher's. This is what the verification tiers exist for.
3. **Redistribution changes the answer** — shipping weights or a fine-tune to a
   client triggers §4 notice duties. Consuming output does not.
4. **The clearance is for this digest.** A repointed tag, a different
   quantisation or a different size is uncleared until re-verified.

**Remaining owner action: record it.** `policy.approve_model_artifact(...)` needs
a registered owner identity, which is the owner's to supply. The exact call is in
`docs/solvent-model-rights-evidence.md` §10. Nothing was written to the
repository on the owner's behalf.

**Re-verified 17 September 2026:** all digests unchanged, the packaged licence
still hashes byte-identical to the publisher's, the family spread is unchanged.
The re-run also found and fixed an enforcement hole — `ROFLO_MODEL` overrides
`roflo.toml`, so a file-only check could report OD-3 cleared while the
research-only 3B was actually loaded.

**Full evidence chain, sources and stated limits:**
`docs/solvent-model-rights-evidence.md`.

<details><summary>Original entry (superseded)</summary>

**Question.** For each checkpoint used on paid work, does its licence permit
commercial use of the output?

**Why required.** Solvent sells work produced with model output. Open-weights
licences differ sharply: some community fine-tunes are restricted or simply
unlicensed. `roflo.toml` currently defaults to `qwen2.5:14b-instruct`.

**Blocks.** Selling output produced by an uncleared checkpoint.

**Does NOT block.** All development, all testing, and any work performed without
model assistance.

**Safe default in force.** None assumed; nothing has been sold.

**Recommendation.** Clear the specific checkpoints in use and record each result
in the Capability Registry, which is designed to hold that record.

</details>

</details>

---

## OD-4 — Will workers run with network isolation? · **PARTIALLY RESOLVED**

**Question.** Will Solvent's workers run inside a network namespace or container
with no route and no resolver, and with credentials held only by the Action Gate?

**Why required.** The in-process guard denies socket, DNS and process spawning,
but a `ctypes` call straight to libc bypasses any in-process hook. The OS layer is
what makes "no approved Gate path means no egress" true rather than aspirational.

**Measured on this host.** `tools/verify_egress.py` reports an unconfined process
reaching the network and the same probe under `unshare -n` failing with
`ENETUNREACH` at the kernel. **Isolation is available here.** What remains is the
owner's decision to *deploy that way*.

**Blocks.** Describing the Action Gate as fully enforced in production.

**Does NOT block.** Anything at all today: external execution is fail-closed
regardless.

**Recommendation.** Run workers under the namespace contract in
`docs/solvent-egress-deployment-contract.md` before enabling any real destination.

---

## OD-5 — How is the owner's identity authenticated? · **CLOSED IN CODE, NEEDS A KEY**

**Resolved by the Owner Channel** (`solvent/owner.py`). An approval is now an
HMAC-signed, scoped, single-use, expiring grant. Forgery, tampering with the
ceiling or the job, replay, a wrong key, and an approval this channel never
issued are all refused — each with a regression test. Once real external
execution is enabled, an unsigned approval is refused outright; the rule is tied
to the actual safety posture so it cannot be forgotten at the moment it starts to
matter.

**What remains is operational, not architectural:** provision
`SOLVENT_OWNER_KEY` outside the worker namespace, alongside the other
credentials. **No key means no approvals** — an unauthenticated system cannot
approve spending, which is the correct failure.

**Honest limit.** This is a shared-secret scheme: anything that can read the key
can mint approvals. It authenticates an *approval*, not a *human being*. That is
proportionate for first revenue and should be revisited before autonomy expands.

<details><summary>Original entry (superseded)</summary>

**Question.** How does Solvent know an approval genuinely came from the owner?

**Why required.** Found by attacking the implementation: originally *any* string
beginning `owner:` was accepted as the owner. That is now bound to a
policy-registered identity list, so a component cannot promote itself by choosing
a convincing name. But registration is **not authentication** — anything running
in-process under the registered name is still accepted.

**Blocks.** Treating any approval as cryptographically attributable. High-value
autonomous approval.

**Does NOT block.** P0, because every consequential path is either fail-closed or
exercised only against fixtures.

**Safe default in force.** Owner identities are registered at bootstrap and
changeable only by an already-registered owner. Approvals are scoped to one job
and one action class, single-use, and expire.

**Recommendation.** Bind identity to a device-held key in the P1 Owner Channel.
Until then, keep external execution fail-closed. **Phone and SMS remain
notification only — never authorisation.**

</details>

---

## OD-6 — What licence applies to this repository? · **NON-BLOCKING**

**Question.** There is no `LICENSE` file and no `license` field in
`pyproject.toml`, so by default all rights are reserved — while the README invites
people to clone it.

**Blocks.** Third-party contribution, reuse or distribution.

**Does NOT block.** The owner's own use, including commercial use.

**Recommendation.** Decide before anyone else touches the repository.

---

## OD-7 — Where does real pricing data come from? · **BLOCKING FOR REAL QUOTES**

**Question.** Which approved sources supply per-state labour, materials and market
rates?

**Why required.** Every geographic adjustment must resolve to a reference record;
a model may propose a lookup key but never supply a value. Today the only loaded
records are **fixtures** — marked `is_fixture`, sourced `FIXTURE`, and invented.
They exist to prove that CA, CT and NC price differently, not to price anything.

**Blocks.** Any binding quote for real work.

**Does NOT block.** The whole pricing mechanism, which is built and tested:
per-category jurisdiction binding, the five-tier hierarchy, staleness, conflict
resolution, the legal floor, and materiality.

**Safe default in force.** Unpriced material categories return
`PRICING_LOCATION_UNKNOWN` and refuse a binding quote. Stale records are treated
as absent, not as weak evidence.

**Recommendation.** Start with BLS or equivalent public wage data per state plus
the owner's own historical actuals, loaded through `PricingReference.ingest` with
provenance. Approve each source in Policy first.

---

## OD-8 — Should Solvent be added to the package build? · **NON-BLOCKING**

**Question.** `pyproject.toml` currently builds only `roflo*`. Solvent runs
today via `python -m solvent.cli` and its tests via `python -m unittest`, so
nothing is broken.

**Why it was left alone.** Changing the packaging of an existing project is the
owner's call, not a side effect of building a new subsystem.

**Recommendation.** If Solvent should be installable, add `solvent*` to
`[tool.setuptools.packages.find]` and a `solvent = "solvent.cli:main"` script
entry. One line each, no code change.

---

## OD-9 — Which marketplaces, and do their terms permit automation? · **NOT A P0 BLOCKER**

**Question.** For any marketplace Solvent might eventually use, do its Terms of
Service permit automated account operation?

**Why required.** Many platforms prohibit automated operation or bidding, or
require the contracted human to perform the work. Discovery of a source is not
permission to operate on it.

**Blocks.** P1b discovery automation only.

**Does NOT block.** P0 in any respect. P0 uses one manually inserted job and
connects to nothing.

**Recommendation.** Defer until the loop has been proven on a real job. Record a
written determination per platform before any adapter is written, and never
circumvent a platform protection.

---

## OD-10 — Can a client choose their own pricing jurisdiction? · **OPEN, DOCUMENTED**

**Question.** A marketplace posting states where the project is. Solvent prices
from that statement. What stops a client from naming a cheaper state?

**Why required.** Found while attacking the acquisition pipeline. It is a
*business* risk rather than a control bypass: nothing is circumvented, but a
misstated location produces a wrong price and a thinner margin than expected.

**Current behaviour, deliberate and tested.** The stated jurisdiction drives the
cost lines, and the resulting estimate error surfaces in the Governor's
calibration over time — an optimistic pattern raises K and tightens the gate
automatically. `test_adversarial.py` records this behaviour explicitly so it is
visible rather than assumed.

**Blocks.** Nothing today. All pricing is fixture-based and no quote is binding.

**Options.** (a) Accept it: the error is bounded per job and self-correcting in
aggregate. (b) Require owner confirmation of jurisdiction above a value
threshold. (c) Flag a mismatch between stated client location and stated project
location for review.

**Recommendation.** (a) until real jobs exist, then (b) — the threshold makes the
control proportionate to the exposure. Not worth building before there is a real
quote to protect.

---

## OD-11 — Which work source is researched first? · **NOT BLOCKING**

**Question.** Which platform gets the first completed research template in
`docs/solvent-work-source-research.md`?

**Why required.** A source cannot be marked automatable without a human reading
its current terms and recording the determination. Solvent refuses to guess, and
this document deliberately names no platform.

**Blocks.** Automated discovery against a real source. Nothing else.

**Does NOT block.** The entire acquisition pipeline, which runs today over a
fixture board and refuses most of what it finds.

**Researched on 15 September 2026** — see `docs/solvent-work-source-research.md`.

**Recommendation, now evidence-based:**

- **First source: direct / owner-sourced work via the manual bridge.** Already
  implemented. Blocked by nothing external.
- **First source worth automating: open-source bounties**, because payment is on
  merge and completion is verified by someone other than the worker. **Requires a
  per-repository AI-policy screen** — a substantial number of projects now ban or
  restrict AI-generated contributions (OpenJDK, GCC, Zig, NetBSD, GIMP, Gentoo,
  qemu; 37 of 120 surveyed ban outright). Clearing the platform is not enough.
- **Defer:** agent-native marketplaces. Right shape, unverified.
- **Do not use for autonomous operation:** general freelance marketplaces.
  Automated proposals are prohibited, there is no proposal API, and personal-
  performance requirements are something an AI operator structurally cannot meet.

**Owner action:** confirm the above from primary sources before any adapter is
built. Upwork's own pages returned 403 to automated fetch and were not worked
around, so that finding rests on secondary reporting.


---

## OD-12 — Which service does Solvent sell first? · **ANSWERED AND PROMOTED**

**Decision (owner): approve `csv-cleanup/1.0` as a proven capability, limited
strictly to the scope actually proven.** Recorded through the existing
capability registry; registrations are durable and append-only.

**Scope of the approval — nine certified checks:** `drop_exact_duplicates`,
`map_values`, `no_unauthorised_changes`, `normalise_dates`, `parses_as_csv`,
`preserve_columns`, `require_columns`, `row_reconciliation`, `trim_whitespace`.

**Explicitly outside it.** `rename_headers` and `sort_rows` were implemented but
their checks were not certified, so a job requiring either was refused
fail-closed. **Superseded by OD-14**, which certified and promoted both. Also
outside, and still outside: XLSX, PDF, report generation, analysis, any later
version, and unattended operation. CSV approval is CSV approval.

**Evidence relied upon**, re-verified against the current implementation
fingerprint: verifier CERTIFIED 386/386 with zero false accepts or rejects
across 15 defect classes, clean on both the surprise and development holdout
seeds, and 25/25 black-box scenarios with zero false completions.

See `docs/solvent-skill-inventory.md` for the authoritative inventory.

<details><summary>Original entry (superseded)</summary>


**Recommendation: spreadsheet / CSV cleanup with computed totals**, from a
deterministic sixteen-dimension scorecard (`solvent/services.py`) that weights
provability above rate. It scored 116.5; the backup, small coding tasks, scored
100.0. Its deliverable is machine-checkable — totals recompute, row counts
reconcile, the file opens — so verification is arithmetic rather than opinion.

The full contract is `services.SPREADSHEET_CLEANUP`, and it names no price: the
Governor prices every job. Selecting it does not make Solvent a spreadsheet
company; it registers through the existing Capability registry like any other.

**Owner action:** approve it and register the capability as proven.

<details><summary>Original entry (superseded)</summary>

**Question.** Which single capability is Solvent's first product?

**Why required.** The Capability Registry is deliberately almost empty: a
capability must be registered by the owner and marked proven, and Solvent cannot
register one for itself. Qualification refuses work whose needs no proven
capability covers — which is why the acquisition demo correctly rejects video
editing. Without one proven, sellable capability there is nothing to qualify
*for*.

**Blocks.** Accepting any real job.

**Does NOT block.** The whole pipeline, which runs today against a fixture
capability.

**Options.** (a) A narrow document or spreadsheet service — closest to what is
already modelled. (b) A coding service, which pairs with the bounty channel but
requires the per-repository AI-policy screen in OD-11. (c) Research or data
analysis.

**Recommendation.** (a) first: it is narrow, its output is machine-checkable
(totals reconcile, schema validates), which makes T1 verification cheap and
honest. Cheap verification matters more than headline rate for a first job,
because a job that cannot afford to be verified is one Solvent refuses.

**Consequence.** Until answered, `readiness` reports "a proven capability to
sell" as blocking, and it is right to.

</details>

</details>

---

## OD-14 — Promote `rename_headers` and `sort_rows`? · **ANSWERED — CONDITION MET**

**Decision (owner): add both to the promoted `csv-cleanup/1.0` scope if and only
if they pass the same technical evidence standard as the other nine; keep them
out and report why if they do not.** They passed. The promoted scope is now
**eleven** certified checks — the nine from OD-12 plus `rename_headers` and
`sort_rows`.

**They did not pass as they stood.** Certifying them meant building deliverables
that were wrong in ways nothing had been asked about yet, and three defects
surfaced. All three were in *already-certified* checks, exposed by the two new
operations rather than caused by them:

1. **A renamed column was invisible to every comparison made by name.** It
   shares no name between source and deliverable, so swapping it with the column
   beside it moved one of the client's other columns and
   `no_unauthorised_changes` — the check whose whole subject is "nothing else
   changed" — accepted it. Nine instances passed in one certification run. The
   plan's rename mapping is now an input to that check, and a source column
   absent from the deliverable under its agreed name is a failure.
2. **Authorising a rename authorised rewriting the column.** `rename_headers`
   granted `full` change to the column's *values*, so a deliverable could rename
   the header and replace everything beneath it. Renaming is now its own,
   narrower authorisation: the name may change, the values may not.
3. **Every check compared one column at a time.** A deliverable with every value
   present and every value on the wrong row — which is what sorting a column
   instead of sorting the rows produces — was accepted by the entire capability.
   `row_reconciliation` now compares rows as rows, projected onto the columns
   the job was not asked to change.

A fourth, found in the same pass, was a false *rejection*: protected columns were
compared against the source's distinct rows unconditionally, so a job nobody
asked to deduplicate was accused of altering data it had not touched.

**Evidence relied upon.** Verifier CERTIFIED on the generated battery — 526
trials in the certification stream, zero false accepts and zero false rejects,
across 18 CSV defect classes including five built specifically for these two
operations; clean on the development and surprise holdout seeds and on five
further seeds the battery had never used. Every check meets the evidence floor
(≥5 distinct defect instances per class, ≥10 distinct correct artifacts, no
missed class). 34/34 black-box scenarios with zero false completions, nine of
them new: ordering as text, stable ordering among equal keys, empty and
multi-column keys, embedded newlines, CRLF, a byte-order mark, non-ASCII header
names, a rename onto an existing name, a rename and a sort key that name absent
columns, and two traps — a column sorted instead of the rows, and the renamed
column moved. 21 mutants of the new controls, 21 killed.

**The version was held at `csv-cleanup/1.0`** on the owner's explicit
instruction. Note that the verifier implementation changed materially, so the
previous certification is invalid by fingerprint and a fresh one is required
before any delivery — that binding, not the version string, is what protects the
approval. There is no rule in the codebase requiring a version bump when scope
widens; if the owner wants one, it is a decision to record, not a fact to
discover.

**Still outside the scope.** Everything OD-12 excluded.

---

## OD-13 — Should Solvent check that a work source actually pays? · **NEW, NOT BLOCKING**

**Question.** Before committing labour to a source, should Solvent require
evidence that the source has actually paid anyone?

**Why it arose.** From the Penniless Agent playbook's first rule: *payment
evidence precedes work* — check for confirmed payouts, and reject boards with
"many hopeful submissions and zero merges". Solvent can reject a *client* for
`BAD_PAYMENT_HISTORY`, but performs no payment-reality check on a **source**.

**Blocks.** Nothing today: the manual bridge has no source risk, since the owner
knows the client.

**Recommendation.** Add it as a required field in the source determination when
the first real platform is researched — not before. It is a field in an existing
record, not a new component.

---

## Summary

| ID | Decision | Blocks real work? | Blocks development? |
| --- | --- | --- | --- |
| OD-1 | Legal contracting entity | **Answered** — identity fields to provision | No |
| OD-2 | Payment rail with verification | **Answered** — Stripe not yet activated | No |
| OD-3 | Model commercial licence | **Answered** — hybrid, per-artifact clearance | No |
| OD-4 | Network isolation at deploy | Production claim only | No |
| **OD-5** | **Authenticated owner identity** | **Closed in code; needs a key provisioned** | No |
| OD-6 | Repository licence | Third parties only | No |
| OD-7 | Real pricing sources | **Binding quotes** | No |
| OD-8 | Packaging | No | No |
| OD-9 | Marketplace terms | P1b only | No |
| OD-10 | Client-stated jurisdiction | No (documented risk) | No |
| OD-11 | First work source | Automated discovery only | No |
| **OD-12** | **First sellable capability** | **Answered — csv-cleanup/1.0 promoted** | No |
| OD-13 | Source payment-reality check | No (hardening) | No |
| OD-14 | Promote rename_headers and sort_rows | **Answered** — condition met, both promoted | No |

**Three decisions stand between a working architecture and a first real job:
OD-1, OD-2 and OD-12** — OD-3 is verified and needs only to be recorded. None is an engineering problem. Run
`python3 -m solvent.cli readiness` for the live list.
