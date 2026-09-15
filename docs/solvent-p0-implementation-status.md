# Solvent P0 — Implementation Status

> **Solvent exists to find, qualify, complete, deliver and profit from legitimate
> client work.** The authorities below are infrastructure protecting that mission,
> not the mission itself. See `SOLVENT.md`.

Honest classification of every P0 requirement. The distinction that matters:

| Level | Meaning |
| --- | --- |
| **DESIGNED** | Written down. Nothing runs. |
| **IMPLEMENTED** | Code exists and runs. |
| **TESTED** | Behaviour is asserted by tests that fail when it breaks. |
| **ENFORCED** | Cannot be bypassed by ordinary means — the database, the kernel, or the interpreter refuses. |
| **PRODUCTION-READY** | Enforced *and* its external preconditions are resolved. |

**Nothing in Solvent is PRODUCTION-READY**, because three owner decisions (OD-1
legal entity, OD-2 payment rail, OD-3 model licence) stand between the
architecture and a real job. That is a statement about preconditions, not about
the code.

---

## The six P0 authorities

| Authority | Status | Enforcement mechanism |
| --- | --- | --- |
| **Policy Store** | ENFORCED | Owner-only writes bound to a policy-registered identity list; versioned and append-only; unreadable policy fails closed to HALT |
| **Audit Log** | ENFORCED | SQLite triggers abort UPDATE and DELETE — proven against a raw second connection; SHA-256 hash chain detects tampering that gets past them |
| **Ledger** | ENFORCED | Append-only; `PAID` unreachable without an external verification method; simulated payments carry a marker excluded from `real_revenue_cents()` |
| **Financial Governor** | ENFORCED | Sole writer of budget grants; every spend checked; an estimate may only decide its own job; a grant may only be spent by its own job |
| **Job Orchestrator** | ENFORCED | Transition table refuses illegal moves; `BLOCKED` requires a named blocker; delivery and completion are impossible without verification evidence and Ledger-recorded payment |
| **Action Gate** | ENFORCED in-process, deployment pending (OD-4) | Interpreter-level audit hook denies egress; only the Gate opens a window; OS isolation verified available but not yet the deployment posture |

## The laws

| Law | Status | Where |
| --- | --- | --- |
| One responsibility, one authoritative owner | **ENFORCED** | `store.TABLE_OWNER` + `AuthorityConnection`; a cross-authority write raises |
| No component grants itself authority | **ENFORCED** | Owner registry; architectural test asserts no authority calls `amend` or `register` |
| Governor before spend | **ENFORCED** | No path to a paid external call bypasses `authorize_spend` |
| Gate before external effect | **ENFORCED** (in-process) | Audit hook; 6 bypass attempts tested |
| Policy before permission | **ENFORCED** | Gate consults Policy for every external class |
| Conformance before price | **TESTED** | First check in `governor._evaluate`; non-conforming options removed before ranking |
| Location before binding price | **TESTED** | Material unresolved jurisdiction ⇒ `PRICING_LOCATION_UNKNOWN` |
| Unknown fails closed | **TESTED** | `FailClosed` raised at each gate; unknown conformance, jurisdiction, action class and cost source all refuse |
| Verify before learn | **ENFORCED** | T0 refused; economic learning requires T3; verifier ≠ executor enforced at write |
| Learning cannot alter governance | **ENFORCED** | Memory owns one table; no write path to Policy, Ledger, Audit, or calibration |
| External content has no authority | **TESTED** | Unconfirmed requirements cannot gate a commitment; interpolation yields a marker |
| Solvent may not authorise its own growth | **ENFORCED** | Capability registration requires a registered owner |
| No silent stalls | **TESTED** | Every non-terminal state has a timeout; timeouts escalate, never advance |

## New P0 requirements from the contract

| Requirement | Status | Evidence |
| --- | --- | --- |
| State-level pricing baseline | **TESTED** | CA/CT/NC produce three materially different prices from three reference records |
| Extensible to finer geography | **TESTED** | Hierarchical path; a city record outranks its state record; lookup generalises upward |
| Jurisdiction from the job, not the owner | **TESTED** | `LocationSignals` has no owner field; asserted by test |
| Per-category jurisdiction binding | **TESTED** | CT client + CA project + NC worker stays three bindings |
| Unknown location fails closed | **TESTED** | Materiality test; prevailing wage material at any size |
| No LLM-invented multipliers | **ENFORCED** | Every adjustment resolves to a reference record id or the category is UNKNOWN |
| Stale data detectable | **TESTED** | Past `max_age` ⇒ treated as absent |
| Conflicting data resolved conservatively | **TESTED** | Higher cost taken, spread recorded |
| Legal floor is a floor | **IMPLEMENTED** | Tier-1 violation raises rather than substituting |
| Estimator loophole closed | **TESTED** | Learning has no write path; K derived from Ledger; tightening immediate, loosening rate-limited; stratified by job class; exposure capped |
| Kill switch at two chokepoints | **TESTED** | Governor (spend) and Gate (egress); enforced mid-job |
| Verification proportional to consequence | **TESTED** | Four tiers; C_LOW accepts a deterministic check, C_HIGH demands an independent verifier |
| Verification cost priced before acceptance | **TESTED** | Verification line enters the estimate; a job that cannot afford verification is rejected at qualification |
| roflo containment | **DESIGNED** | One process per (model, privacy class), selected by URL; `model` field never trusted |

## Vulnerabilities found by attacking the implementation, and fixed

| # | Attack | Outcome |
| --- | --- | --- |
| A1 | Price a trivial job, use that estimate to approve an expensive one | **FIXED** — an estimate may only decide its own job |
| A2 | Spend job A's authorised budget on job B's work | **FIXED** — grants are bound to their job, checked at the Gate too |
| A3 | Declare a job complete with no money collected | **FIXED** — completion reads the Ledger instead of believing its caller |
| A5 | Craft a jurisdiction containing the path separator to forge a reference key | **FIXED** — separator rejected inside a component |
| A7 | Mint an owner by choosing a convincing name | **MITIGATED** — bound to a policy-registered identity list; true authentication is OD-5 |

Three further defects were found by the tests during construction: calibration
measured error against an estimate including an unspent verification allowance
(the estimator loophole in a third form), the degradation threshold compared
against the wrong baseline, and the spend anomaly check was unreachable dead code.
All three are fixed and carry regression tests.

## The acquisition mission

| Requirement | Status | Evidence |
| --- | --- | --- |
| Opportunity Discovery exists as a product capability | **TESTED** | `solvent/discovery.py`; `solvent acquire` finds a board and refuses most of it |
| Discovery cannot accept, bid, claim or spend | **ENFORCED** | No such method exists; asserted by test |
| A source is polled only when Policy approves **and** a human determination permits it | **ENFORCED** | Both checked per poll; either missing yields nothing |
| Readiness ladder prevents claiming marketplace capability without evidence | **ENFORCED** | `PERMITTED` is refused below `PERMITTED_AUTOMATION`, and requires a recorded determination |
| Remote fetches pass the Action Gate | **TESTED** | Gate denial yields no candidates; a gateless Discovery refuses to fetch |
| No anti-bot circumvention, CAPTCHA bypass or impersonation | **BY CONSTRUCTION** | `PROHIBITED` sources cannot be polled; no such code exists |
| Postings are untrusted; structured offer terms are distinguished from prose | **TESTED** | Scraped prose always needs human confirmation; a body is context, never a requirement |
| Qualification owns the one accept/reject/escalate verdict | **TESTED** | `solvent/qualification.py` |
| Qualification owns **no** economics | **ENFORCED** | Architectural test asserts the module contains no margin, cost or calibration arithmetic |
| Solvent can say no, with a recorded reason | **TESTED** | 15 rejection reasons; distribution is a business metric |
| Ranking orders acceptable work best-first | **TESTED** | Higher-value work outranks lower |
| Opportunity cost defers weaker work | **TESTED** | Deferred as `BETTER_OPPORTUNITY_AVAILABLE`, not as failure |
| Every candidate is judged before any is committed | **TESTED** | Assess-then-commit: the best job wins, not the first seen |
| Ranking never overturns the Governor | **TESTED** | A rejected job is unrankable at any score |
| Duplicate postings cannot become duplicate commitments | **TESTED** | Deduplicated per source by reference or content key |
| Business metrics lead with verified profitable work | **IMPLEMENTED** | `solvent metrics`; simulated money excluded by construction |
| Marketplace integration | **RESEARCH_REQUIRED** | No platform contacted, none named, no terms asserted |

### Acquisition attacks found and fixed

| # | Attack | Outcome |
| --- | --- | --- |
| B1 | Poll a board twice and bid twice on one posting | **FIXED** — deduplicated per source |
| B2 | Promote a Governor-rejected job through ranking | **Already blocked** — rejected work is unrankable |
| B3 | Forge an ACCEPT with no Governor verdict | **Already blocked** — nothing to commit without one |
| B4 | Have Discovery write another authority's table | **Already blocked** — connection cannot |
| B5 | Keep polling after Policy revokes approval | **Already blocked** — checked per poll |

A hostile board is a standing test: its highest-paying posting carries an
injection payload, ranks first on price and is selected — and still cannot
proceed. Its value triggers owner approval, its instructions change no policy,
its capability claim is refused by the registry, and its asserted labour rate is
ignored in favour of reference records.

## Not built, deliberately

Marketplace adapters for any real platform (gated on OD-9 and OD-11), Owner Channel with
authenticated approval (P1, OD-5), Client Channel (P2), Analyst and controlled
self-growth (P3), voice and phone escalation (P4). The P0 question is whether one
real job can travel the whole architecture safely — not whether thousands can be
found.

## How to check any of this yourself

```bash
python3 -m unittest discover -s tests_solvent -t .   # 251 tests
python3 -m solvent.cli acquire                       # find work, refuse most of it
python3 -m solvent.cli metrics                       # business metrics
python3 tools/verify_egress.py                       # measured egress enforcement
python3 -m solvent.cli doctor                        # measured readiness
python3 -m solvent.cli demo                          # the complete job, fixtures only
python3 -m solvent.cli laws                          # each law and where it lives
```
