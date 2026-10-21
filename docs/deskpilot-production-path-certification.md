# DeskPilot — the production execution path

Date: 2026-10-21 · Linux x86_64, CPython 3.11.17 · Branch
`claude/solvent-architecture-audit-8ot9et` · From `eef18ad`

The scheduler now calls the business cycle. It did not before, and the
reason it was reported as working is worth more than the fix.

---

## 1. Root cause

`Runtime._one_business_job`, as shipped:

```python
return run_business_cycle(self.solvent, source_name="",
                          owner_identity=self.solvent.policy.owner)
```

Two faults, either fatal on the first call:

* `PolicyStore` has no `owner` attribute. It never has. Ownership is a
  list, `governance.owner_identities`, and `is_owner` checks membership
  of it. → `AttributeError`.
* `run_business_cycle` declares `source_file` and `workdir` as
  keyword-only parameters **with no defaults**. → `TypeError`.

So the path from the ninth scheduled task to the business cycle had
never executed, not once.

## 2. Why it was certified anyway

Every test that exercised the business tick replaced the method. The
acceptance run that reported 100 opportunities and 53 deliveries said
so in its own docstring:

> `_one_business_job` is replaced with one that pulls the next
> synthetic contract off a queue.

The 53 deliveries were real work through the real downstream pipeline.
The link from the scheduler to that pipeline was a substitute. **A
function certified by swapping it out certifies the swap**, and the
harness could not detect this because the harness *was* the swap.

This is the third time in this engagement the same shape has appeared:
first machinery nothing called, then a `NameError` behind 22 tests that
read the function's source, now a method certified by replacement. Each
was caught by executing the thing rather than describing it.

## 3. The repair

| Argument | Where it now comes from |
|---|---|
| `owner_identity` | `governance.owner_identities`, the list `is_owner` checks. Raises `NoOwnerRegistered` rather than defaulting — a defaulted owner identity is a forged one. |
| `source_file` | the file the customer attached to their order, checked for existence **before admission**, because a job admitted against a missing file holds a capacity slot and fails at execution. |
| `workdir` | one per order, under a configurable work root beside the database rather than the system temporary directory. |

`None` is returned when the shop is empty. That is not a detail:
`Operator.run` stops with `NOTHING_TO_DO` on `None`, and returning a
"nothing happened" stand-in instead would read as a failed job — three
of which end the run.

Resolution lives in **`solvent/workorder.py`**, not in `operation.py`.
A test pins that module's imports to `errors` and `types` alone so the
loop deciding *whether to go round again* cannot grow opinions about
the work. The guard was right, so the code moved rather than the guard.

**No second authority.** Qualification triages, the Governor prices,
the Action Gate authorises delivery, the owner approves above the
threshold, Capacity bounds concurrency. All unchanged, all downstream,
none reimplemented.

## 4. Two things that existed but were never built

**The storefront.** `Solvent` constructed no `Storefront`, so the
inbound channel was reachable only from a test — the same unreached
machinery as `operation.py` itself. `Solvent` now builds one. Its work
source is deliberately **not** registered there: registering a source
is an owner act with a recorded determination, and doing it at
construction would be the process granting itself a channel.

**Terms an unattended run can verify against.** `run_business_cycle`
refuses to infer requirements from a posting, and it is right to: a
posting's body is attacker-authored prose, and a deliverable checked
against terms DeskPilot wrote for itself is checked against its own
invention. So unattended execution needs terms from somewhere real.

A published `ServiceOffer` is that somewhere — a standing contract with
fixed scope that the customer reads before ordering. It now carries
`requirements`: named checks that run against the deliverable, so what
the advert promises and what the checker recomputes are the same
sentence. `offer_problem` refuses to sell an offer that promises
nothing checkable, or that names a check its capability does not
register — which would otherwise fail verification *after the customer
had paid*.

Some checks both instruct and verify (`map_fields` says which column
goes where **and** checks that it did). A fixed offer cannot know a
customer's column names, so those terms take their parameters from the
customer's own structured answer. A prose answer is **refused, not
interpreted**: turning a sentence into a mapping is interpretation, the
five skills call no model, and an invented mapping would be verified
against the invention.

## 5. Three more defects, all found by running

**The Operator read the wrong kill switch.** It checked
`operations.halted`, a boolean nothing else consults, while the
documented halt is `operating_mode` — what the Action Gate honours via
`blocks_external`, what the orchestrator honours via `blocks_new_work`,
and what `set_operating_mode` writes. An owner pulling the halt stopped
those and not this loop, which kept taking work and met the halt as a
`FailClosed` thrown inside each job: counted as a failure, three in a
row, and the run ended looking like a malfunction instead of a stop
somebody asked for. Two switches, one of them ignored.

**The loop treated billing a customer as failure.** It judged progress
by `BusinessCycle.completed`, which means the cycle reached *payment*.
An unattended loop must not fabricate a payment, so every job it ran
correctly stopped at `AWAITING_PAYMENT`, counted as a failure, and
three in a row shut the business down — for the offence of invoicing.
Progress now means **delivered and invoiced**. Unpaid exposure stays
bounded by the Governor's `max_committed_unverified_cents`, which is
checked before each job is priced; this adds no new ceiling and widens
none.

**A productive run reported doing nothing.** `NOTHING_TO_DO` is also
how a run that did three jobs and then emptied the queue ends, and the
tick reported "no eligible opportunity" for both.

## 6. Acceptance

`tests_solvent/test_production_path.py` — 31 tests. Its rule, stated at
the top of the file: nothing may mock, replace or bypass
`_tick_business`, `_one_business_job`, `next_work_order`,
`Operator.run`, `run_business_cycle`, qualification, the Governor, the
Action Gate, the workers, the checkers or the audit log. Synthetic
*opportunities* are allowed; synthetic *internals* are not.

```
offered                  5 orders, one per skill
manual CLI cycles        0
scheduler ticks          5
delivered and invoiced   5
job states               AWAITING_PAYMENT × 5
audit chain              intact
simulation_only          true throughout
real revenue             0 cents
```

| Skill | Through the scheduler |
|---|---|
| csv-cleanup | delivered, invoiced |
| data-entry | delivered, invoiced |
| document-processing | delivered, invoiced |
| report-builder | delivered, invoiced |
| spreadsheet-automation | delivered, invoiced |

Delivery is authorised by the **owner's bounded standing grant**
(`C2_EXTERNAL_COMMUNICATION` set to `PREAUTHORIZED` with a ceiling),
not by anything the loop produces. With the grant withdrawn, the Action
Gate wants a signed approval and the job stops before delivery — tested.

## 7. The suite fails when the defect returns

`TheDefectCannotComeBack` reintroduces each original fault and requires
the acceptance to notice: the `policy.owner` read, the omitted
`source_file`/`workdir`, a no-op `_one_business_job`, a disconnected
tick, and a removed business task. A control test asserts the
unmodified path *does* deliver, without which the five injections would
prove nothing.

One of them initially injected nothing. Setting `_tick_business` after
`default_tasks()` has no effect, because `Task` holds the bound method
it was handed — the same shape of mistake as certifying a method by
replacing it, this time making a defect-injection test inject no
defect. It now replaces before registration, and the comment says why.

## 8. Windows

The first real `windows-latest` execution found a defect Linux cannot
produce: **WinError 32**, the process cannot access the file because it
is being used by another process. Deleting a file something still has
open is ordinary on POSIX and forbidden on Windows, so a temporary
database could not be removed.

`Store` now registers itself while open and is a context manager,
`Solvent` can `close()`, and a conftest fixture closes what each test
opened — snapshotting first, because the version without that closed a
class-scoped database after the first test of its class and the rest
failed with "Cannot operate on a closed database". Deliberately **not**
`ignore_cleanup_errors`: masking the error would have left the product
with no way to let go of its own database, which was the actual defect.

## 9. The seal

`solvent/store.py` is under the capacity exam's seal, which broke on
the next run exactly as intended. The eighth paper was re-sat against
the changed source **before** re-sealing: 13 tests, 32 subtests, every
level's delivered count unchanged. Letting go of a file alters no
answer about capacity. The ninth sitting is recorded and all eight
prior sittings are preserved unedited.

## 10. What this still does not say

* **Nothing about live operation.** Simulation is enforced in code and
  `continuous_enabled` is off by default.
* **Nothing about real money.** 0 cents, synthetic orders, a throwaway
  signing key.
* **Nothing about a marketplace.** The storefront is the inbound
  channel; no outbound board is reachable and none was contacted.
* A job rests at `AWAITING_PAYMENT`. The loop does not fabricate the
  payment that would move it on, and that is the correct end state for
  unattended operation.
