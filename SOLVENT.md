# Solvent

## SOLVENT EXISTS TO FIND, QUALIFY, COMPLETE, DELIVER, AND PROFIT FROM LEGITIMATE CLIENT WORK.

That sentence is the product. Everything else in this repository exists to
protect it.

All adaptive, analytical, learning, strategic and growth capabilities are in
service of that mission. **They are not the mission themselves.**

---

## The business loop

```
DISCOVER → INGEST → UNDERSTAND → QUALIFY → CAPABILITY MATCH → CONFORMANCE
→ PRICE → PROFITABILITY → RISK → CAPACITY → OPPORTUNITY COST → PERMISSION
→ ACCEPT/CLAIM → PLAN → ASSIGN CAPABILITIES → EXECUTE → SUPERVISE
→ DETECT GAPS → VERIFY → QUALITY CONTROL → DELIVER → INVOICE/PAYMENT
→ VERIFY COLLECTION → ACTUAL P&L → LEARN → REPEAT
```

Said plainly:

> Find good work. Know whether you can do it. Know whether it is worth doing.
> Get the right permission. Do the work well. Verify it. Deliver it. Get paid.
> Know the real profit. Learn. Do it better next time.

## What Solvent is not

Solvent is **not** primarily a generic adaptive AI, a self-improvement
experiment, an AI board of directors, a business consultant, a
project-management application, or a general autonomous operating system.

Some of those may one day be *capabilities Solvent sells*. None of them is the
mission. **If a change makes Solvent cleverer without making it better at
finding, doing and getting paid for work, it is off-mission.**

The correct hierarchy for growth is:

```
FIND WORK → DO WORK → GET PAID → MEASURE RESULT → LEARN
→ IDENTIFY A REPEATED PROFITABLE GAP → PROPOSE IMPROVEMENT
→ OWNER APPROVAL → TEST → DEPLOY
```

Not: expand continuously and hope the expansion eventually earns.

## The primary business metric

**Verified profitable client work.**

Opportunities discovered, jobs accepted and revenue invoiced can all rise while
the business loses money. Only collected revenue from an externally verified
payment, minus actual recorded costs, means anything happened. Simulated money is
excluded from that figure in code, not by convention.

## The distinction that is easy to lose

**Solvent is the AI business operator. The legal entity is the legal wrapper.**

Solvent finds and does the work. A person or company signs the contract, issues
the invoice, receives the money and carries the tax and liability. Restoring
Solvent's mission does not remove the need to know who is contracting — that is
OD-1, and it stays.

## The authorities, and why they exist

Eight authorities, each owning exactly one consequential decision so that no two
components can decide the same thing. They are infrastructure, not the product.

| Authority | The one question it answers |
| --- | --- |
| **Discovery** | Which candidate opportunities exist, from which approved sources? |
| **Qualification & Ranking** | Accept, reject or escalate — and of the acceptable, which first? |
| **Capability & Conformance** | Can Solvent actually do this, and does it fit? |
| **Financial Governor** | Is this specific spend financially acceptable? |
| **Policy Store** | May Solvent do this class of thing at all? |
| **Action Gate** | May this specific external action leave Solvent? |
| **Job Orchestrator** | What state is this job in, and may it advance? |
| **Ledger** | What money actually moved? |
| **Audit Log** | What happened, and on whose authority? |

The always-on runtime (`solvent/runtime.py`) is deliberately **not** in that
table. It starts the process, ticks tasks on a schedule and stops cleanly; it
answers no business question. systemd owns process supervision, the Job
Orchestrator owns job supervision, and the runtime owns neither.

Discovery finds work and **can never take it**. Qualification decides what is
worth taking and **owns no economics**. The Governor is the only place economics
are computed. That separation is what makes automating the search for work safe
before automating the decision to take it.

## The laws

```
DISCOVERY ≠ QUALIFICATION ≠ CAPABILITY ≠ PROFITABILITY ≠ PERMISSION
```

- ONE RESPONSIBILITY. ONE AUTHORITY. NO DUPLICATION. NO OVERLAP.
- FAIL CLOSED when consequential authority is uncertain.
- CONFORMANCE BEFORE PRICE — a non-conforming option is not a cheap option, it is
  not an option.
- LOCATION BEFORE BINDING PRICE — unknown material jurisdiction refuses to quote.
- GOVERNOR BEFORE SPEND. POLICY BEFORE PERMISSION. GATE BEFORE EXTERNAL EFFECT.
- VERIFY BEFORE LEARN — an agent reporting COMPLETE does not prove SUCCESSFUL.
- PROVE BEFORE EXPANSION.
- Solvent may **recommend** its own growth. Solvent may **never authorise** it.
- Owner governance remains supreme.

## Saying no is a capability

Solvent is not optimised for the number of jobs. It is optimised for good jobs.
Every refusal is recorded with its reason, and the distribution of those reasons
is a first-class business metric. Solvent will decline for: missing capability,
non-conforming fit, insufficient information, unsupported jurisdiction, margin
below the floor, excessive risk, no capacity, missing permission, prohibited
action, bad payment history, a source not permitted for automation, or simply
because a better opportunity is competing for the same capacity.

## Where the business is today

| Stage | Status |
| --- | --- |
| Simulated job, end to end | **Working** — `python3 -m solvent.cli demo` |
| Acquisition cycle over a fixture board | **Working** — `python3 -m solvent.cli acquire` |
| Owner-entered opportunity through the full pipeline | **Working** — the manual bridge |
| Authenticated owner approval | **Working** — signed, scoped, single-use; needs a key provisioned |
| First-Revenue Mode | **Available** — one source, one job, owner-set caps |
| **Any client-work capability** | **None** — audited 19 Sep 2026; Solvent cannot open a file or run a model. See `docs/solvent-capability-audit.md` |
| 24/7 operation on an always-on host | **Working** — `solvent run --always-on`; see `docs/solvent-always-on-deployment.md` |
| Crash and reboot recovery | **Working** — an unsettled external action blocks its job rather than repeating it |
| Manually sourced **real** job | **Blocked on OD-1, OD-2, OD-12** (OD-3 verified; owner records it) |
| Controlled discovery against a real platform | Researched; see `docs/solvent-work-source-research.md` |
| Owner-approved first platform | Not started |
| Limited automated discovery | Not started |
| Earned autonomy | Not started; requires verified performance evidence |

No platform has been contacted for work, no account exists, no proposal has been
sent, no opportunity in this repository is real, and no money has moved.
Public documentation was read as research; two primary sources returned 403 to an
automated fetch and were **not** worked around.

Run `python3 -m solvent.cli readiness` for the live list of what is blocking.

## Reading order for a new developer or agent

1. This file — the mission.
2. `docs/solvent-p0-architecture-contract.md` — the approved authority contracts.
3. `docs/solvent-work-source-research.md` — how a work source becomes usable.
4. `OWNER_DECISIONS.md` — what is blocked and, more usefully, what is not.
5. `solvent/qualification.py` — where the business decision actually happens.
