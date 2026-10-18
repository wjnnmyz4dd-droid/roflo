# DeskPilot — owner signing and delivery authorization: final repair

**Status: VERIFIED FIXED**

Date: 2026-10-18 · Platform: Linux x86_64 · No real external effect was
produced at any point in this work.

---

## 1. The defect, as measured

With `SOLVENT_OWNER_KEY` configured, **0 of 100** synthetic contracts
delivered. Without a key, the same 100 contracts behaved normally.

The cause was not in the Owner Channel and not in the Action Gate. Both
were correct. The business cycle, on reaching the delivery gate, built
its own approval object:

```python
approval(owner_identity=OWNER, job_id=job_id,
         action_class=ActionClass.C2_EXTERNAL_COMMUNICATION, max_cents=0)
```

That is the *unsigned development approval*. The Action Gate accepts one
only while nothing can leave the machine, and refuses it outright once an
Owner Channel exists — which is the right rule. So the moment a real
deployment configured a real key, every delivery, including routine T1
work, met a refusal that no owner could clear, because the cycle had no
way to offer the gate anything else.

The defect was therefore **the absence of a path for real consent**, not
a missing check. Nothing was ever delivered without authorization; the
system simply stopped.

## 2. What was changed

One function, `solvent/harness.py::_delivery_approval`, and the parameter
that reaches it.

```python
def _delivery_approval(s, job_id, artifact, approver):
    if approver is None:
        return approval(...)          # unsigned stand-in; the gate judges it
    try:
        return approver(job_id=job_id, artifact=artifact)
    except Exception as refusal:
        s.audit.record(event="delivery.approval_unavailable", ...)
        return None                   # a failure to ask is not a yes
```

`delivery_approver` is threaded through `run_business_cycle`,
`_run_business_cycle`, `_execute_job`, `_deliver_now` and
`resume_interrupted`. It is **supplied by the caller** — the owner's own
tooling, an app, a signed CLI on their machine. DeskPilot cannot call it
on the owner's behalf and holds no key with which to sign.

Three properties follow directly, and each is tested:

* **Consent is never manufactured.** With no approver the cycle offers
  the gate the same unsigned stand-in it always did, and the gate refuses
  it whenever a key exists. With a broken approver the cycle produces
  `None`, not a weaker approval.
* **No key, no change.** The no-key path is byte-for-byte what it was.
* **A configured key does not block routine T1 work.** The owner has two
  answers: sign per delivery, or set a bounded `preauthorization` for the
  action class. The gate checks the preauthorization first and never
  reaches `_delivery_approval` at all.

Nothing in the Owner Channel's verification was weakened. No signing key
appears in source, in a test fixture, in the audit log or in a commit.
Test keys are `secrets.token_hex(32)`, generated per test and removed
from the environment on cleanup.

## 3. What the approval is bound to

Every field below is inside the HMAC payload, so altering any one of them
invalidates the signature (`solvent/owner.py::SignedApproval.payload`):

| Bound to | Field | Enforced in |
|---|---|---|
| Replay prevention | `nonce` (single-use, recorded) | `verify` + atomic `consume` |
| Owner identity | `owner_identity` | `verify` (registered owners only) |
| Exact action | `action_class` | `verify` (scope check) |
| Job | `job_id` | `verify` (scope check) |
| Amount ceiling | `max_cents` | `verify` |
| Expiry | `expires_at` | `verify` |
| Which key signed it | `key_id` (HMAC fingerprint, not a hash of the key) | audit |

The deliverable itself is bound on the **review** path rather than the
delivery path: `independent.owner_review` takes the artifact and its
digest, so an owner review names the exact file. The delivery approval is
scoped to the job, and a job's current deliverable is a single registered
artifact in an append-only table.

Contract ID is carried as the job's client/contract reference on the job
record the approval names; it is not a separate signed field, and that is
stated here rather than claimed otherwise.

## 4. Tests

`tests_solvent/test_owner_signing_delivery.py` — 15 tests, all passing.

| Requirement | Test |
|---|---|
| No key configured | `WithoutAKeyNothingChanges.test_delivery_still_works_with_no_owner_channel` |
| Key configured, nothing else | `test_without_an_approver_or_preauthorisation_delivery_fails_closed` |
| Valid signed approval | `test_a_signed_approval_lets_delivery_through` |
| Bounded standing grant | `test_a_preauthorisation_lets_routine_delivery_through` |
| Missing approval | `test_no_approval_at_all` |
| Invalid signature | `test_a_forged_signature` |
| Forged approval | `test_a_forged_signature` |
| Modified after signing | `test_an_approval_altered_after_signing` |
| Wrong job | `test_an_approval_for_a_different_job` |
| Wrong action | `test_an_approval_for_a_different_action` |
| Expired | `test_an_expired_approval` |
| Replayed | `test_a_replayed_approval` |
| Owner tooling fails | `test_an_approver_that_raises_is_not_consent` |
| A failed ask yields nothing | `TheApprovalContractItself.test_a_failed_ask_yields_no_approval_at_all` |
| `verify` reports a spent approval | `TheApprovalContractItself.test_verify_itself_reports_an_approval_already_used` |
| Key never exposed | `TheKeyItselfIsNeverExposed.test_the_key_is_not_written_into_the_audit_log` |
| T1 work with signing on | `AConfiguredKeyMustNotBlockRoutineWork` (whole class) |
| T2 work with a valid approval | `test_four_repair_integration.AllFourHoldOnOneJob` |
| T2 work without one | `TheRepairsDoNotCancelEachOther.test_an_unsigned_delivery_is_still_refused` |
| Restart after approval | `test_four_repair_integration.AnInterruptionIsRecoveredWithAllFourInPlace` |

## 5. What mutation testing found, and what it changed

Seven mutants were added for this path. Two **survived** the first run,
and both were real findings about the *tests*, not the code:

* Making a broken approver return the unsigned stand-in instead of `None`
  did not fail the end-to-end test — because with a key configured the
  gate refuses the stand-in too. The control was being enforced by
  something else, so the test's name was not evidence.
* Removing `verify`'s already-used check did not fail the replay test —
  because `consume` claims the nonce atomically and refuses the loser.

Neither mutant produced an unauthorized delivery; both showed a control
that nothing pinned. Two unit tests were added
(`TheApprovalContractItself`) that pin each check directly, and the two
mutants were re-bound to them and re-run: both now **KILLED**. The
acceptance criteria were not changed; the tests were made to cover what
their names claimed.

## 6. Limits of this repair, stated plainly

* It is a shared-secret scheme. Anything that can read
  `SOLVENT_OWNER_KEY` can mint approvals. The key belongs outside the
  worker namespace. This is honest authentication of an approval, not
  proof of a human being.
* The synthetic approver used in tests and in the 100-opportunity
  acceptance run stands in for the owner's tooling. It is an explicit
  test action with an isolated per-run key; it is not evidence that a
  person approved anything, and nothing here should be read that way.
* No real external effect was enabled. Egress stayed `simulation_only`
  throughout.
