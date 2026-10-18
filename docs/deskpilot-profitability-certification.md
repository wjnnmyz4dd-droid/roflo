# DeskPilot — profitability guard certification

Date: 2026-10-18 · Platform: Linux x86_64 · CPython 3.11 · Branch
`claude/solvent-architecture-audit-8ot9et`

Two things were asked for here: independent verification that the
profitability guard a mutation run disabled is genuinely restored, and a
trace of the whole financial admission path to see whether anything else
can get round it. The first came back clean. The second found a second
way past the owner's floor, which is repaired below.

---

## 1. The restoration, verified independently

The incident: commit `de94386` was staged with `git add -A` while a
mutation run held `solvent/finder.py` patched. The committed line read
`if False:` where `Finder.decide` declines work that costs more than it
earns. Two pushed commits carried it; `df34ca6` restored it.

Three checks, each from a different angle, because "the report says it
was fixed" is not verification:

**The line itself.** `solvent/finder.py:1197` now reads:

```python
        if econ.profit_cents is not None and econ.profit_cents <= 0:
            return Verdict.DECLINE, (
                f"it would cost {(econ.cost_cents or 0) / 100:,.2f} to earn "
                f"{econ.revenue_cents / 100:,.2f}")
```

**The net diff.** `solvent/finder.py` does not appear in
`git diff --name-status 4819125 fcd241a` at all. The file at the branch
head is byte-identical to the file before the remediation round began:
`git diff 4819125 -- solvent/finder.py` is empty.

**A scan that knows nothing about the incident.** A script reads the
`Mutant` tables out of both runners — 388 mutants across
`tools/mutate_solvent.py` and `tools/mutate_installer.py` — and asks of
each target file whether the mutant's *original* text is gone and its
*replacement* present. That is the state a runner leaves between its two
writes, and it is what a commit taken from underneath one captures.

```
tools.mutate_solvent: 194 mutants
tools.mutate_installer: 194 mutants

388 mutants found their original anchor intact
```

Zero mutated, zero anchors missing. No production source is running
under mutation instrumentation. Script:
`/tmp/claude-0/-home-user-roflo/25de398b-b56b-53d8-b59f-f68119ec6f42/scratchpad/cert/scan_mutants.py`.
The same question is now asked by a test and by a pre-commit hook; see
`deskpilot-mutation-isolation-certification.md`.

## 2. Which layer is the authority

Worth stating plainly, because the mutation incident hit the layer that
is *not* the authority and it would be easy to over- or under-claim from
that.

| Layer | What it decides | Authority over admission |
|---|---|---|
| `FinancialGovernor.decide` | accept / escalate / reject on the money | **Yes. The one money gate.** |
| `Qualification` | maps the Governor's verdict onto a Decision | No — it owns no economics |
| `Finder._verdict` | what is worth *recommending* | No |

`solvent/qualification.py`'s own docstring: *"This module contains no
margin arithmetic, no floor, and no cost … never as a second opinion on
whether a job is profitable."* Verified: the module's text contains
`MARGIN_TOO_LOW` and none of `margin_floor`, `* 0.3` or `/ revenue`.
There is a test for it.

**The Governor is wired into the production workflow.** `harness.py:332`
calls `s.governor.decide` in the business cycle, and `orchestrator.py`
calls it at two more points. This was checked rather than assumed.

**The Finder is not.** It is constructed in exactly one place —
`cli.py:503` — and only to call `commercially_configured()` for a
readiness report. No module in the business cycle calls `assess`,
`rank` or `next_to_qualify`. That is consistent with its own docstring
("Triage order for qualification's inbox. Not a work queue… has no
authority over that one"), and it means the mutation incident disabled
a guard on a path the cycle does not currently take. It is reported
here as a finding rather than repaired, because wiring a second
economic opinion into the cycle is an architectural change nobody asked
for — but it matters for Phase 2, where recommending Upwork work is
exactly the Finder's job.

## 3. Defect F — an approval door above the floor

**Found by:** tracing the §2.5 bypass list against `_evaluate` rather
than reading the guard in isolation.

**Reproduction.** `_evaluate`'s checks are ordered, and the order used
to put the per-job cost ceiling *above* the margin floor. Both return
before the other is reached, so a job that trips both came back
`REQUIRES_APPROVAL` and its margin was never computed:

```
margin_floor=0.3  max_job_spend_cents=25000  exposure_cap=150000
cost=26000  revenue=28888  effective_cost_cents=43332
VERDICT: GovernorVerdict.REQUIRES_APPROVAL | estimated cost $433.32 exceeds per-job ceiling $250.00
```

That job loses $144.44 on $288.88 of revenue — a **negative** margin
against a 30% floor — and it was put to the owner as an ordinary
approval question. If the owner says yes, it is admitted. The owner's
own floor was never evaluated.

**Root cause.** Ordering. `REQUIRES_APPROVAL` asks the owner a question;
the floor is not a question. Checked after the two approval doors, the
floor can be answered *by* the owner rather than enforced *for* them.

**Repair.** The margin floor now sits above both approval doors and
below the exposure cap:

```
conformance → jurisdiction → preliminary → operating mode → revenue > 0
  → exposure cap (HIGH_RISK)
  → MARGIN FLOOR (MARGIN_TOO_LOW)
  → per-job cost ceiling (REQUIRES_APPROVAL)
  → owner-approval value threshold (REQUIRES_APPROVAL)
  → PROFITABLE
```

The exposure cap deliberately stays above the floor: a job refused for
breaching the shop's committed-but-unverified ceiling is refused as
*risk*, and that is the reason the owner needs to read, not the
arithmetic. There is a test pinning that too, so the new order is not
free to drift either way.

**Scope.** One block moved in `solvent/governor.py::_evaluate`. No
threshold changed, no verdict added or removed, no caller touched.

## 4. The regression tests

`tests_solvent/test_profitability_guard.py`, 24 tests. Every case the
brief named, plus the two that found the defect.

| Case asked for | Test | Result |
|---|---|---|
| Clearly profitable | `test_a_clearly_profitable_job_is_admitted` | PROFITABLE |
| Exactly at threshold | `test_a_job_exactly_at_the_threshold_is_admitted` | PROFITABLE — at the floor is not below it |
| Marginally profitable, under the floor | `test_a_marginally_profitable_job_below_the_floor_is_declined` | MARGIN_TOO_LOW |
| Zero profit | `test_a_zero_profit_job_is_declined` | MARGIN_TOO_LOW |
| Negative profit | `test_a_negative_profit_job_is_declined` | MARGIN_TOO_LOW |
| Missing cost estimate | `test_an_unresolved_pricing_jurisdiction_blocks_the_price` | PRICING_LOCATION_UNKNOWN — never priced as zero |
| Missing platform fee / fallback line | `test_a_preliminary_estimate_is_not_a_binding_quote` | NEEDS_INFORMATION |
| No revenue established | `test_no_revenue_is_not_a_free_job` | NEEDS_INFORMATION |
| Unexpected cost increase past the ceiling | `test_a_costly_job_under_the_floor_is_declined_not_escalated` | MARGIN_TOO_LOW **(was REQUIRES_APPROVAL)** |
| …that still clears the floor | `test_a_costly_job_that_clears_the_floor_still_asks_the_owner` | REQUIRES_APPROVAL — the repair did not swallow the control it reordered |
| Owner approves an unprofitable job | `test_a_high_value_job_under_the_floor_is_declined_not_escalated` | MARGIN_TOO_LOW — it never reaches the owner |
| Retry | `test_an_unprofitable_job_is_declined_however_often_it_is_asked` | MARGIN_TOO_LOW, five times |
| Risk outranks price | `test_the_exposure_cap_still_outranks_the_floor` | HIGH_RISK |
| Conformance outranks everything | `test_conformance_still_outranks_everything` | REJECT, price not evaluated |
| Caller cannot supply a floor | `test_the_floor_comes_from_policy_not_from_the_caller` | `decide` has no floor parameter |
| The floor can bind | `test_raising_the_floor_declines_what_the_old_floor_admitted` | PROFITABLE → MARGIN_TOO_LOW |
| The qualifier adds no economics | `TheQualifierAddsNoEconomicsOfItsOwn` | no arithmetic in the module |
| The Finder's guard, negative | `test_a_negative_profit_posting_is_declined` | DECLINE |
| The Finder's guard, zero | `test_a_zero_profit_posting_is_declined` | DECLINE |
| The Finder's guard, unknown price | `test_an_unknown_price_is_held_not_pursued` | HOLD |
| The Finder's guard is not a comment | `test_the_guard_is_not_a_comment` | the real condition is present, `if False:` is not |

## 5. The bypass list, answered

| Vector | Why it cannot get past the floor |
|---|---|
| Brain ranking | The Brain produces no verdict `decide` reads. Ranking reorders an inbox; admission is a separate call. |
| Opportunity scoring | `Finder._score` returns 0.0 for anything not PURSUE, and has no authority over `decide`. |
| Capacity replenishment | Capacity releases a slot. Filling it re-enters qualification, which re-enters `decide`. |
| Owner notification | Notification carries a verdict; it does not produce one. |
| Proposal preparation | Downstream of the verdict. |
| Recovery | Resumption requires a Governor grant that already exists — recovery never mints one. `_why_not_startable` requires it. |
| Retry | The verdict is a function of the figures; tested five times over. |
| Queue admission | `Qualification` maps the Governor's verdict and owns no economics; tested. |
| **Owner approval** | **Was a real vector. Repaired: the floor now sits above both approval doors.** |

## 6. What is in the cost

`quoting.predicted_operating_lines` builds the cost lines; the Governor
applies a calibration multiplier K from observed over/under-run and adds
`opportunity.upfront_cost_cents`. Already-spent proposal fees enter as
`upfront_cost_cents` on the opportunity — a sunk cost charged against
the job being considered, never credited back as recoverable. Revenue is
`net_budget_cents`, after platform fees, so a stated price eaten by fees
reads as earning nothing rather than as its headline figure.

Two gaps are stated rather than claimed closed, because DeskPilot has no
marketplace account to price them from yet: **per-proposal Connects
cost** and **the applicable freelancer service fee** are not modelled as
named cost categories. They are Phase 2 work and are listed in
`deskpilot-upwork-bid-economics.md`. Until then the per-contract and
portfolio-level acquisition costs the brief asks to be distinguished are
only partly distinguishable: `upfront_cost_cents` is per contract, and
nothing yet tracks a monthly acquisition budget across contracts.

## 7. Mutation coverage

Five mutants added for this surface, all killed:

| Mutant | What it breaks |
|---|---|
| `governor-the-margin-floor-is-not-applied` | work below the floor is admitted automatically |
| `governor-the-floor-sits-below-the-approval-door` | Defect F, as a mutant — restores the old ordering |
| `governor-no-stated-revenue-is-priced-as-free` | a job with no revenue is priced, and a zero divisor decides the margin |
| `governor-the-exposure-cap-is-advisory` | the shop commits past its own unverified ceiling |
| `finder-clearing-cost-by-nothing-is-worth-recommending` | a posting earning exactly its cost is recommended |

No historical coverage is claimed for the reordered code: every one of
these was run against the code as committed.

## 8. Status

**PROFITABILITY GUARD: PASS**, with one finding not repaired and stated
instead:

* The Finder's profitability triage is not reachable from the business
  cycle. The guard works; nothing in production calls it. This becomes
  material in Phase 2.

## 9. What this does not say

* It does not say DeskPilot is profitable. No real contract, no real
  revenue, no real cost has passed through any of this.
* It does not say the cost model is complete. Connects and service fees
  are not modelled; §6 says so.
* It does not say a calibrated K is a correct K. The multiplier is
  learned from observed runs, and the observations here are synthetic.
* Every measurement is from Linux. Nothing here is a Windows or
  MetaTrader 5 claim.
