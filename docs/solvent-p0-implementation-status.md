# Solvent P0 — Implementation Status

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

## Not built, deliberately

Discovery and marketplace automation (P1b, gated on OD-9), Owner Channel with
authenticated approval (P1, OD-5), Client Channel (P2), Analyst and controlled
self-growth (P3), voice and phone escalation (P4). The P0 question is whether one
real job can travel the whole architecture safely — not whether thousands can be
found.

## How to check any of this yourself

```bash
python3 -m unittest discover -s tests_solvent -t .   # 192 tests
python3 tools/verify_egress.py                       # measured egress enforcement
python3 -m solvent.cli doctor                        # measured readiness
python3 -m solvent.cli demo                          # the complete job, fixtures only
python3 -m solvent.cli laws                          # each law and where it lives
```
