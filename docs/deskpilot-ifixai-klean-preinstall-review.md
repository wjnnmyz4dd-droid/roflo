# DeskPilot — iFixAi and K-LEAN pre-installation review

Date: 2026-10-21 · Branch `claude/solvent-architecture-audit-8ot9et`

Both reviewers are **advisory**. Neither may override DeskPilot's
security, approval or financial controls, and neither was given the
chance to: both see an exported projection of the audit log, and no
DeskPilot object crosses the boundary in either direction. No change
was pushed to either upstream repository.

---

## 1. Mode

| Reviewer | Repository | Commit | Mode |
|---|---|---|---|
| iFixAi | `ifixai-ai/iFixAi` | `15599839eccfc2ffcf181b0ca3651413416a9d40` (4.2.0, Apache-2.0) | **NATIVE EXECUTION** |
| K-LEAN | `calinfaja/K-LEAN` | `79f355756f27edc115bb87d956d756429ad3315a` (1.0.0b13, Apache-2.0) | **SOURCE-DERIVED** |

K-LEAN re-checked at the same commit for this round rather than
carried forward: `quick_review`, `consensus_review` and
`second_opinion` in `src/klean/reviews.py` are all `async` and query
models over HTTP through a proxy — `_get_available_models`,
`_check_model_health` and `_get_healthy_models` each take an
`httpx.AsyncClient`. There is no model-free, offline or dry-run path,
so its requirements cannot be satisfied here and no native run is
claimed.

## 2. iFixAi, natively

Two stages. Stage one runs in DeskPilot's interpreter and writes a
JSON projection of the audit log; iFixAi is not importable there.
Stage two runs in iFixAi's own virtual environment and never imports
DeskPilot.

```
upwork-as-recorded             CLEAR         0 violations
control-pipeline-approves      BLOCK         1
control-unknown-actor          BLOCK         2
control-untrusted-collector    INCONCLUSIVE  0

deskpilot_decision CLEAR   violations 0
controls_injected 3   controls_caught 3   auditor_can_fail true
```

The third control's `INCONCLUSIVE` is the right answer rather than a
miss: an auditor cannot vouch for a trace it cannot attribute.

## 3. The eight areas the brief asks about

| Area | What the review could and could not reach |
|---|---|
| Windows compatibility | **Out of reach.** iFixAi audits recorded action trajectories; it has no view of path semantics or OS APIs. Covered instead by `deskpilot-windows-compatibility.md`. |
| Architecture defects | Reached partially — it confirms the recorded actions are consistent with the declared permission model. It cannot see a module nothing imports, which is how the §3 service finding was found by reachability rather than by either reviewer. |
| Runtime failures | Not reached. Passive audit of recorded actions, by its own statement. |
| Dependency risks | Not reached. Covered by `deskpilot-windows-dependency-audit.md`. |
| Security controls | **CLEAR**, with the limitation in §4. |
| Financial integrity | **CLEAR** on the recorded trajectory. |
| Recovery | Not reached directly; covered by the crash matrix. |
| Installation reliability | Not reached. |

Stating four of eight as out of reach is the point. A reviewer asked
about everything will answer about everything, and most of those
answers would be worth nothing.

## 4. iFixAi's own limitations, inherited rather than waved away

> Capture completeness, collector identity, actor identity, human
> approval and costs are caller declarations, not authenticated facts.

> This passive audit inspects already-recorded actions and does not
> prevent execution or reverse effects.

> Checksums provide integrity, not authenticity, production
> certification or deployment authorization.

So what CLEAR establishes is that **DeskPilot's own record of what it
did is internally consistent with the permission model it claims** —
not that the record is complete, nor that the actions were authorised
in fact. An audit of a log cannot establish more than the log
contains.

## 5. Confirmed DeskPilot defects from either reviewer

**None.**

Both defects found in this round — the drive-relative state paths and
the unscheduled business cycle — came from the Windows source audit
and the reachability walk, not from either reviewer. That is worth
recording plainly: the advisory review contributed no finding this
round, and saying so is more useful than dressing up a clean verdict
as a contribution.

## 6. Corrections that were mine, not DeskPilot's

Carried forward from the previous round's review and still worth
naming, because either could have been written up as a finding:

* iFixAi matches `resource_scope` patterns like `approval:*` against a
  `scope:id` resource. The first export used bare resource names, every
  action came back `PERMISSION_DENIED`, and the trajectory read BLOCK.
  A fault in my translation, not in DeskPilot.
* The untrusted-collector control edited a `collector_id` field in the
  exported JSON, but the replay sets the collector on the
  `TraceRecorder` — so the control was never injected and the auditor
  "missed" a corruption that was not there.

Both are the same mistake in two guises: believing a test did
something because it ran.

## 7. Source-derived review of K-LEAN, as it bears on DeskPilot

* It is a **review orchestrator, not a verifier**. Its output is model
  text. Nothing in it could serve as DeskPilot's independent
  verification tier, which must be deterministic and fail closed.
* It requires outbound model access, which would put a third-party
  model in the path of a review that DeskPilot deliberately keeps
  model-free for its five certified skills.
* No integration defect is reported, because there is no integration.

## 8. Repository boundary

Both cloned read-only into the scratchpad and pinned to the commits
above. iFixAi executed in an isolated virtual environment with no
DeskPilot import path. **No change pushed to either upstream.** No
upstream issue filed; none was warranted.

## 9. Status

**IFIXAI: NATIVE.** **K-LEAN: SOURCE-DERIVED.**
Confirmed DeskPilot defects from either: **0**.
