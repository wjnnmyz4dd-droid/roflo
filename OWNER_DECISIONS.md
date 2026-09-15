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

## OD-1 — Which legal entity bears contractual responsibility? · **BLOCKING FOR REAL WORK**

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

---

## OD-2 — Which payment rail, and does it expose a verification signal? · **BLOCKING FOR REAL WORK**

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

---

## OD-3 — Are the model checkpoints licensed for commercial output? · **BLOCKING FOR REAL WORK**

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

## Summary

| ID | Decision | Blocks real work? | Blocks development? |
| --- | --- | --- | --- |
| OD-1 | Legal contracting entity | **Yes** | No |
| OD-2 | Payment rail with verification | **Yes** | No |
| OD-3 | Model commercial licence | **Yes** | No |
| OD-4 | Network isolation at deploy | Production claim only | No |
| OD-5 | Authenticated owner identity | High-value autonomy | No |
| OD-6 | Repository licence | Third parties only | No |
| OD-7 | Real pricing sources | **Binding quotes** | No |
| OD-8 | Packaging | No | No |
| OD-9 | Marketplace terms | P1b only | No |

**Three decisions (OD-1, OD-2, OD-3) stand between a proven architecture and a
first real job.** None is an engineering problem.

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

## OD-12 — Which service does Solvent sell first? · **BLOCKING FOR REAL WORK**

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

---

## Summary (revised)

| ID | Decision | Blocks real work? | Blocks development? |
| --- | --- | --- | --- |
| OD-1 | Legal contracting entity | **Yes** | No |
| OD-2 | Payment rail with verification | **Yes** | No |
| OD-3 | Model commercial licence | **Yes** | No |
| OD-4 | Network isolation at deploy | Production claim only | No |
| **OD-5** | **Authenticated owner identity** | **Closed in code; needs a key provisioned** | No |
| OD-6 | Repository licence | Third parties only | No |
| OD-7 | Real pricing sources | **Binding quotes** | No |
| OD-8 | Packaging | No | No |
| OD-9 | Marketplace terms | P1b only | No |
| OD-10 | Client-stated jurisdiction | No (documented risk) | No |
| OD-11 | First work source | Automated discovery only | No |
| **OD-12** | **First sellable capability** | **Yes** | No |

**Four decisions stand between a working architecture and a first real job:
OD-1, OD-2, OD-3 and OD-12.** None is an engineering problem. Run
`python3 -m solvent.cli readiness` for the live list.
