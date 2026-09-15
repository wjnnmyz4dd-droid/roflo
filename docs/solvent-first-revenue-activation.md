# Solvent — First Revenue Activation

**Research date: 15 September 2026.** What stands between this commit and one real
paid job, and exactly how to close each item.

Run `python3 -m solvent.cli readiness` for the live list. This document explains
the findings behind it.

---

## 1. Stripe — findings from primary documentation

Verified against Stripe's own webhook documentation, not from memory.

| Fact | Detail |
| --- | --- |
| Signature header | `Stripe-Signature: t=<ts>,v1=<sig>,v0=<sig>` |
| Signed payload | `"{timestamp}.{raw_body}"` — timestamp, a literal `.`, then the **raw** body |
| Algorithm | HMAC-SHA256, keyed with the endpoint's `whsec_` secret |
| Scheme handling | **"To prevent downgrade attacks, ignore all schemes that aren't `v1`."** Stripe sends a deliberately fake `v0` on test events |
| Comparison | Constant-time, explicitly required |
| Replay protection | Timestamp is inside the signed payload; libraries default to a **5-minute** tolerance. **"Don't use a tolerance value of 0"** — it disables the recency check |
| Secret rotation | Multiple concurrent `v1` signatures during a roll; any one matching is valid |
| Raw body | Must not be re-parsed or re-serialised, or verification fails |
| Duplicates | "Webhook endpoints might occasionally receive the same event more than once." Guard by logging **event IDs** |
| Ordering | **Not guaranteed.** Do not use `created` to order or to detect duplicates |
| Customer paid | `payment_intent.succeeded`, `checkout.session.completed`, `invoice.payment_succeeded` |
| Money in bank | `payout.paid` / `payout.failed` — a **different event** |

A published advisory (`GHSA-xff3-5c9p-2mr4`) describes a bypass where an **empty
signing secret** makes verification vacuously succeed. `StripeRail` refuses an
empty secret outright, and a test asserts it.

Sources: [Stripe webhooks](https://docs.stripe.com/webhooks) · [signature troubleshooting](https://docs.stripe.com/webhooks/signature) · [empty-secret advisory](https://github.com/advisories/GHSA-xff3-5c9p-2mr4)

## 2. Architecture — Stripe is a rail, never an authority

```
Stripe  →  StripeRail (verify signature only)  →  VerifiedPaymentEvent
                                                        ↓
                                        Ledger.apply_payment_event()
                                        (the Ledger decides what it means)
```

`solvent/payments.py` **never imports the store, never writes a table, and has no
method that could move money** — both asserted by tests. There is no Stripe
Governor, Stripe Ledger or Stripe Policy. The rail establishes that an event is
genuine; the Ledger decides whether it is revenue.

### Customer payment is not a bank payout

Two different facts, two different fields, deliberately never merged:

| Event | What changes | What does not |
| --- | --- | --- |
| `payment_intent.succeeded` | `PaymentState` → `PAID`/`PARTIALLY_PAID`; collected revenue | payout state |
| `payout.paid` | `PayoutState` → `PAID` | **collected revenue is explicitly unchanged** |

Conflating them would let Solvent report cash it does not have.

### What the Ledger refuses, each with a test

no job attribution · a job with no payment record · currency mismatch ·
amount above the invoice (an overpayment is an anomaly) · a non-positive amount
on a payment or refund (a negative "refund" would inflate revenue) · a duplicate
event id · an unhandled event type. Refusals are recorded exactly like acceptances, so a
refused event is as visible as an applied one.

**Test-mode money is recorded and is never revenue.** A genuine signature over a
test payment still carries `livemode: false`, so the verification method is
marked and `real_revenue_cents()` excludes it by construction.

## 3. OD-2 status: **ENGINEERING_READY**, not operationally ready

**Engineering ready:** Solvent can accept a cryptographically verified Stripe
event and update financial state correctly, without bypassing Ledger, Audit,
Policy or Gate. 29 tests cover it.

**Not operationally ready.** The owner must still:

1. Confirm the Stripe account exists and which mode it will run in.
2. Create a webhook endpoint and obtain its `whsec_` secret.
3. Subscribe **only** to the needed event types (Stripe's own guidance).
4. Provide the secret via environment injection, in the Gate namespace — never in
   the database, never in the repository, never reachable by a worker.
5. Confirm bank payout configuration if `payout.*` events are wanted.
6. Exercise the path in test mode end to end.

**No charge was created, no invoice sent, and no Stripe setting altered.**

## 4. OD-3 — production model commercial rights

**Configured model** (from `roflo.toml`, not assumed): backend `ollama`, model
`qwen2.5:14b-instruct`.

**Finding: the upstream Qwen2.5-14B-Instruct model card states Apache-2.0**, which
permits commercial use. Apache-2.0 requires the licence and notice of
modifications to be carried on **redistribution**; Solvent consumes output rather
than redistributing weights. Apache-2.0 places no restriction on model output and
has no monthly-active-user threshold.

**Status: CONDITIONAL — one owner check remains.** I verified the upstream model
card. I did **not** verify that the `ollama` tag `qwen2.5:14b-instruct` packages
those exact Apache-2.0 weights rather than a re-quantisation under different
terms. That is a one-look confirmation, and until it is done OD-3 stays open.

Source: [Qwen2.5-14B-Instruct model card](https://huggingface.co/Qwen/Qwen2.5-14B-Instruct)

## 5. OD-12 — what Solvent sells first

A deterministic scorecard (`solvent/services.py`) rates eight candidates across
sixteen dimensions. The weights favour **provability over rate**, because for a
first service the goal is proving the loop, not maximising revenue — and a job
that cannot be verified cheaply is one the Governor refuses anyway.

| Score | Service |
| --- | --- |
| **116.5** | **Spreadsheet / CSV cleanup with computed totals** |
| 100.0 | Small, well-specified coding tasks |
| 98.5 | PDF → structured data extraction |
| 92.0 | Business document formatting |
| 91.0 | Recurring data-processing automation |
| 81.5 | Research summaries |
| 73.5 | Slide deck creation |
| 70.5 | Business analysis |

**SERVICE #1: spreadsheet cleanup.** Its deliverable is machine-checkable —
totals recompute, row counts reconcile, the file opens — so T1 verification is
arithmetic rather than opinion. Scope is stateable in a sentence, liability is
low, and it needs no paid tool.

**BACKUP: small coding tasks.** Tests make verification equally objective and it
pairs with the bounty channel, but shipped code carries more liability and many
projects now restrict AI-generated contributions.

**Deferred:** research summaries and business analysis score lowest precisely
because they are hard to verify and a wrong answer carries real liability — a
hallucination is invisible to a deterministic check.

The full contract is `services.SPREADSHEET_CLEANUP`: accepted inputs,
deliverables, conformance rules, supported formats, explicitly unsupported
requests (tax filing, valuation, legal review, macros), verification tiers,
pricing *inputs*, risk factors, scope boundaries, rejection conditions and
owner-approval triggers. **It names no price.** The Governor prices every job.

**Selecting a first service does not make Solvent a spreadsheet company.** The
service registers through the existing Capability registry, which is built to
hold more.

## 6. Inspiration gap audit

Repository identity was verified before any assessment.

| Project | Verified | Notes |
| --- | --- | --- |
| **The Penniless Agent** | Yes — multiple deployments of a published playbook | Zero-capital agent commerce, crypto-oriented |
| **Personhood / agentic trust fabric** | Yes | Personhood-backed identity and escrow for agent-to-agent commerce |
| **"Money Agent"** | **No** | No project of that name identified. `adambossy/penny` is personal finance analysis, unrelated. **INSUFFICIENT_EVIDENCE** |

**No code was copied and no dependency added.** Architectural inspiration only.

| Idea | Classification | Where it lands |
| --- | --- | --- |
| **"Payment evidence precedes work"** — check a board has actually paid before committing labour | **USEFUL_MISSING_IDEA → EXTEND_EXISTING** | Solvent has `BAD_PAYMENT_HISTORY` as a rejection reason but performs no payment-reality check on a *source*. This belongs in the source determination in `Discovery`, alongside the terms finding. Recorded as OD-13 rather than built speculatively |
| Re-fetch an artifact from the public side and confirm it validates | ALREADY_EXISTS | The T1 tier already requires a deterministic check; the service contract names reconciliation explicitly |
| Fee arithmetic before moving funds; unlock thresholds | ALREADY_EXISTS | Governor includes platform and payment fees; `minimum_value_cents` refuses work too small to be worth evaluating |
| Check KYC/identity requirements before working | EXTEND_EXISTING | Already a field in the work-source research template |
| Receive-only wallets, zero secrets | NOT_RELEVANT (crypto) — principle ALREADY_EXISTS | Credentials live only in the Gate namespace |
| Never mass-post; contribute only where standing exists | ALREADY_EXISTS | Source compliance rules forbid spam and ToS evasion |
| Personhood-backed identity | **NOT_RELEVANT for P0** | Solvent's counterparty is a human client paying by card, not an agent economy. Revisit if agent-native marketplaces are pursued |
| Agent-to-agent escrow | DUPLICATE / NOT_RELEVANT | Stripe already provides the settlement guarantee for the chosen path |
| A "Penniless Agent" / "Money Agent" component | **DO_NOT_ADD** | Would duplicate the Governor, Ledger and Discovery at once |

## 7. Bootstrap mode — a preference, not a prohibition

Implemented as a bounded ranking multiplier in `Qualification`, which already owns
ranking. Between two otherwise equal jobs the one needing no cash up front wins;
a clearly superior job still wins despite needing some. The penalty is clamped so
it can never become a disguised veto, and **it cannot reject anything** — the
Governor remains the only authority that decides whether work is worth doing.
Five tests cover exactly that boundary.

## 8. The first real job acceptance contract

`Solvent.assert_ready_for_real_job()` raises unless every precondition holds, and
lists each blocker with its category. It refuses today, including on the
fail-closed posture itself — a job that cannot be delivered or paid is not a job
worth starting.

## 9. Exact steps to the first real paid job

**Owner decisions (roughly an hour, no engineering):**

1. **OD-1** — name the contracting person or entity. Recorded as
   `governance.legal_entity`.
2. **OD-12** — approve spreadsheet cleanup as Service #1 and register the
   capability as proven.
3. **OD-3** — confirm the ollama tag packages the Apache-2.0 Qwen weights. Record
   as `governance.model_commercial_rights`.
4. **OD-11** — for a first job, the answer is the manual bridge; no platform
   research is needed yet.

**Owner configuration:**

5. Generate an owner signing key and provide it as `SOLVENT_OWNER_KEY` in the
   Gate namespace. Never commit it. Without it, approvals fail closed.
6. **OD-2** — create the Stripe webhook endpoint, subscribe to the needed events,
   and inject the `whsec_` secret the same way. Record
   `payment.approved_rail` and `payment.verification_signal`.
7. Set First-Revenue Mode caps: maximum job spend, maximum committed-unverified
   exposure, margin floor. Solvent refuses to invent these.

**Then, and only then:**

8. Owner finds one small real job and enters it through the manual bridge.
9. Confirm `assert_ready_for_real_job()` passes.
10. Enable external execution for the specific allowlisted destinations needed —
    a deliberate, reversible owner act, not a default.
11. Run the job. Verify. Deliver. Let Stripe confirm payment.
12. Read the actual profit from the Ledger, and let the Governor take its first
    real calibration sample.

**Step 8 is the whole point. Steps 1–7 are paperwork; step 11 is the business.**
