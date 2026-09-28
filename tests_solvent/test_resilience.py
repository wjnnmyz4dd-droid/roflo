"""Incident intelligence, attacked: does it remember truthfully and stay powerless?

Two properties, and they pull in opposite directions.

**It must remember enough to be useful.** A failure Solvent cannot recognise the
second time is a failure it will keep having, so the fingerprint has to survive
the details that differ between runs, and the record has to carry root cause,
fix and how the fix was proven.

**It must remember nothing it should not.** A crash record is written at the
moment of least care, by code already handling something unexpected, and it is
then read by the website, copied into backups, and shown to whoever is
investigating. It is the classic way a secret escapes the one place it was kept.

And underneath both: **diagnosis is not permission**. Nothing here may promote a
capability, change a rule, authorise spending or permit an external effect, and
a breaker opening must never become a door opening.
"""

from __future__ import annotations

import pathlib
import tempfile
import unittest

from solvent import resilience as res
from solvent.errors import FailClosed
from solvent.harness import OWNER, Solvent

#: Planted secrets. Shaped like the real thing and obviously not real: each is
#: the word "canary" plus what it is pretending to be, so a grep hit in a
#: database, a log or a backup can never be mistaken for a live credential.
CANARIES = {
    "SOLVENT_OWNER_KEY": "canary-owner-key-never-real-0123456789abcdef",
    "SOLVENT_STRIPE_SECRET": "canary-stripe-secret-never-real-9876543210",
    "SOLVENT_OWNER_PHONE": "+15550000000",
}


class TheRedactorActuallyRedacts(unittest.TestCase):
    """Every shape a secret arrives in, and proof the test can tell."""

    def test_a_planted_secret_would_be_visible_without_the_redactor(self):
        """The canary for this whole class.

        Without it, every assertion below passes if ``redact`` starts returning
        the empty string, or if the secret never reached the text in the first
        place. This asserts the input genuinely contains what the others claim
        was removed."""
        text = f"signing failed with {CANARIES['SOLVENT_OWNER_KEY']}"
        self.assertIn(CANARIES["SOLVENT_OWNER_KEY"], text)
        self.assertNotEqual(res.redact(text, environ=CANARIES), "")

    def test_an_environment_secret_is_removed_wherever_it_appears(self):
        """Even with nothing around it calling it a secret."""
        for name, value in CANARIES.items():
            with self.subTest(name=name):
                text = f"the operation failed near {value} and then stopped"
                cleaned = res.redact(text, environ=CANARIES)
                self.assertNotIn(value, cleaned)
                self.assertIn("[REDACTED]", cleaned)

    def test_a_labelled_secret_is_removed(self):
        for text in ("api_key=abcdef123456", "password: hunter2xyz",
                     "webhook_secret = whsec_abcdefgh",
                     "private_key: MIIEvQIBADANBg"):
            with self.subTest(text=text[:20]):
                self.assertIn("[REDACTED]", res.redact(text, environ={}))

    def test_a_bearer_token_is_removed_and_not_just_the_word_bearer(self):
        """This leaked. The label rule matched "Authorization: Bearer" and took
        "Bearer" as the value, so the word was redacted and the token was left
        in the record. Order of the rules is the fix."""
        token = "abcdefghijklmnopqrstuvwxyz012345"
        cleaned = res.redact(f"Authorization: Bearer {token}", environ={})
        self.assertNotIn(token, cleaned)

    def test_stripe_shaped_credentials_are_removed(self):
        for text in ("sk_live_ABCDEFGH1234", "rk_test_ABCDEFGH1234",
                     "whsec_ABCDEFGH1234"):
            with self.subTest(text=text):
                self.assertNotIn(text, res.redact(text, environ={}))

    def test_long_hex_is_removed_because_that_is_what_a_raw_key_looks_like(self):
        key = "3b8f1c2d4e5a6b7c8d9e0f1a2b3c4d5e6f708192"
        self.assertNotIn(key, res.redact(f"digest {key}", environ={}))

    def test_a_phone_number_is_removed(self):
        self.assertNotIn("555", res.redact("called +1 (555) 123-4567", environ={}))

    def test_an_ordinary_identifier_survives(self):
        """Redaction that destroys the diagnostic protects nothing useful."""
        text = "job_ab12 failed in orchestrator.mark_verified"
        self.assertEqual(res.redact(text, environ={}), text)

    def test_a_short_environment_value_is_not_substring_matched(self):
        """Redacting every occurrence of a two-character value would destroy
        the text without protecting anything."""
        text = "the row count was 42 and the state was ok"
        self.assertEqual(res.redact(text, environ={"SOLVENT_OWNER_KEY": "42"}),
                         text)


class Fingerprints(unittest.TestCase):
    def print_(self, **kwargs):
        base = dict(component="payments", operation="ingest",
                    error_class="TimeoutError", signature="timed out after 30s")
        return res.fingerprint(**{**base, **kwargs})

    def test_the_same_fault_fingerprints_the_same_way(self):
        self.assertEqual(self.print_(), self.print_())

    def test_a_volatile_detail_does_not_make_a_new_failure_class(self):
        """"timed out after 30s" and "after 45s" are one fault. Treating them as
        two meant a slow dependency read as a new failure class every time."""
        self.assertEqual(self.print_(signature="timed out after 30s"),
                         self.print_(signature="timed out after 45s"))

    def test_ids_paths_and_addresses_do_not_reach_the_fingerprint(self):
        self.assertEqual(
            self.print_(signature="job_ab12 failed at /var/lib/a.db (0x1f)"),
            self.print_(signature="job_cd34 failed at /var/lib/b.db (0x2e)"))

    def test_a_different_fault_in_the_same_place_is_a_different_class(self):
        self.assertNotEqual(self.print_(signature="timed out"),
                            self.print_(signature="certificate verify failed"))

    def test_the_same_message_from_a_different_component_is_different(self):
        self.assertNotEqual(self.print_(), self.print_(component="model"))

    def test_the_same_message_from_a_different_exception_is_different(self):
        self.assertNotEqual(self.print_(), self.print_(error_class="ValueError"))

    def test_a_secret_in_the_signature_does_not_reach_the_fingerprint(self):
        """A fingerprint that varied with a secret would be an oracle for it."""
        import os

        previous = os.environ.get("SOLVENT_OWNER_KEY")
        os.environ["SOLVENT_OWNER_KEY"] = CANARIES["SOLVENT_OWNER_KEY"]
        self.addCleanup(lambda: (os.environ.__setitem__("SOLVENT_OWNER_KEY", previous)
                                 if previous is not None
                                 else os.environ.pop("SOLVENT_OWNER_KEY", None)))
        with_secret = self.print_(
            signature=f"failed using {CANARIES['SOLVENT_OWNER_KEY']}")
        redacted = self.print_(signature="failed using [REDACTED]")
        self.assertEqual(with_secret, redacted)


class TheIncidentRecord(unittest.TestCase):
    def setUp(self):
        self.s = Solvent()
        self.r = self.s.resilience

    def fail_once(self, **kwargs):
        base = dict(component="payments", operation="ingest",
                    error_class="TimeoutError", signature="timed out after 30s",
                    severity=res.S_SERIOUS, trigger="webhook ingest")
        return self.r.record_incident(**{**base, **kwargs})

    def test_a_first_failure_is_a_new_class(self):
        incident = self.r.incident(self.fail_once())
        self.assertEqual(incident["status"], res.DETECTED)
        self.assertEqual(incident["recurrence_count"], 1)
        self.assertEqual(incident["related_to"], "")

    def test_the_same_failure_again_is_recorded_as_a_recurrence(self):
        first = self.fail_once()
        second = self.r.incident(self.fail_once(signature="timed out after 61s"))
        self.assertEqual(second["status"], res.RECURRED)
        self.assertEqual(second["recurrence_count"], 2)
        self.assertIn(first, second["related_to"])

    def test_history_is_kept_rather_than_replaced_by_a_counter(self):
        """"this is the fourth time" is established by the three earlier cases."""
        for _ in range(4):
            self.fail_once()
        self.assertEqual(len(self.r.incidents(component="payments")), 4)

    def test_a_secret_in_the_trigger_never_reaches_the_table(self):
        import os

        previous = os.environ.get("SOLVENT_OWNER_KEY")
        os.environ["SOLVENT_OWNER_KEY"] = CANARIES["SOLVENT_OWNER_KEY"]
        self.addCleanup(lambda: (os.environ.__setitem__("SOLVENT_OWNER_KEY", previous)
                                 if previous is not None
                                 else os.environ.pop("SOLVENT_OWNER_KEY", None)))
        incident = self.fail_once(
            trigger=f"signing with {CANARIES['SOLVENT_OWNER_KEY']} failed",
            symptoms=f"key {CANARIES['SOLVENT_OWNER_KEY']} rejected")
        row = self.r.incident(incident)
        self.assertNotIn(CANARIES["SOLVENT_OWNER_KEY"], row["trigger"])
        self.assertNotIn(CANARIES["SOLVENT_OWNER_KEY"], row["symptoms"])
        self.assertIn("[REDACTED]", row["trigger"])

    def test_a_secret_never_reaches_the_audit_log_either(self):
        import os, json

        previous = os.environ.get("SOLVENT_OWNER_KEY")
        os.environ["SOLVENT_OWNER_KEY"] = CANARIES["SOLVENT_OWNER_KEY"]
        self.addCleanup(lambda: (os.environ.__setitem__("SOLVENT_OWNER_KEY", previous)
                                 if previous is not None
                                 else os.environ.pop("SOLVENT_OWNER_KEY", None)))
        self.fail_once(trigger=f"using {CANARIES['SOLVENT_OWNER_KEY']}")
        blob = json.dumps([dict(e) for e in self.s.audit.events()], default=str)
        self.assertNotIn(CANARIES["SOLVENT_OWNER_KEY"], blob)

    def test_an_unknown_severity_is_refused(self):
        with self.assertRaises(FailClosed):
            self.fail_once(severity="VERY_BAD_INDEED")

    def test_an_incident_reaches_the_audit_log(self):
        self.fail_once()
        events = [e for e in self.s.audit.events()
                  if e["event"] == "resilience.incident"]
        self.assertEqual(len(events), 1)


class AFixIsNotBelievedUntilItIsProven(unittest.TestCase):
    def setUp(self):
        self.s = Solvent()
        self.r = self.s.resilience
        self.incident = self.r.record_incident(
            component="orchestrator", operation="mark_verified",
            error_class="FailClosed", signature="no deliverable registered")

    def test_a_fix_with_no_root_cause_is_a_guess(self):
        with self.assertRaises(FailClosed) as caught:
            self.r.record_fix(self.incident, fix="restarted the worker")
        self.assertIn("root cause", str(caught.exception))

    def test_verifying_requires_naming_the_evidence(self):
        self.r.record_root_cause(self.incident, root_cause="an unstamped artifact")
        self.r.record_fix(self.incident, fix="stamp it")
        with self.assertRaises(FailClosed) as caught:
            self.r.verify_fix(self.incident, verification_ref="   ")
        self.assertIn("evidence", str(caught.exception))

    def test_a_restart_is_not_a_verification(self):
        """A process that came back up has demonstrated it can start, which is
        the one thing nobody doubted."""
        self.r.record_recovery(self.incident, action="restart",
                               result="process came back up")
        self.assertEqual(self.r.incident(self.incident)["status"], res.RECOVERING)
        self.assertNotEqual(self.r.incident(self.incident)["status"], res.VERIFIED)

    def test_a_lesson_is_only_kept_once_the_fix_is_verified(self):
        self.r.record_root_cause(self.incident, root_cause="an unstamped artifact")
        self.r.record_fix(self.incident, fix="stamp it",
                          prevention="a test that fails without the stamp")
        print_ = self.r.incident(self.incident)["fingerprint"]
        self.assertIsNone(self.r.lesson_for(print_),
                          "an unverified fix was offered as a lesson")
        self.r.verify_fix(self.incident,
                          verification_ref="tests_solvent/test_capability_gate.py")
        lesson = self.r.lesson_for(print_)
        self.assertIsNotNone(lesson)
        self.assertEqual(lesson["fix"], "stamp it")

    def test_a_fix_cannot_be_verified_before_it_is_recorded(self):
        with self.assertRaises(FailClosed):
            self.r.verify_fix(self.incident, verification_ref="something")

    def test_the_same_failure_after_a_verified_fix_is_a_regression(self):
        self.r.record_root_cause(self.incident, root_cause="an unstamped artifact")
        self.r.record_fix(self.incident, fix="stamp it")
        self.r.verify_fix(self.incident, verification_ref="a regression test")
        again = self.r.record_incident(
            component="orchestrator", operation="mark_verified",
            error_class="FailClosed", signature="no deliverable registered")
        correlation = self.r.correlate(self.r.incident(again)["fingerprint"])
        self.assertTrue(correlation.regression)
        self.assertIn("regression", correlation.summary)
        self.assertEqual(correlation.prior_fix, "stamp it")

    def test_an_unseen_failure_says_so_rather_than_guessing(self):
        correlation = self.r.correlate(res.fingerprint(
            component="nothing", operation="nothing", error_class="Never"))
        self.assertFalse(correlation.seen_before)
        self.assertEqual(correlation.summary, "NEW FAILURE CLASS")
        self.assertEqual(correlation.prior_fix, "")

    def test_the_timeline_records_every_step_and_overwrites_none(self):
        self.r.record_root_cause(self.incident, root_cause="cause")
        self.r.record_fix(self.incident, fix="fix")
        self.r.verify_fix(self.incident, verification_ref="evidence")
        stages = [row["stage"] for row in self.r.timeline(self.incident)]
        self.assertEqual(stages, [res.DETECTED, res.FIX_PENDING, res.FIXED,
                                  res.VERIFIED])


class TheFlightRecorder(unittest.TestCase):
    def setUp(self):
        self.s = Solvent()
        self.r = self.s.resilience

    def test_it_reconstructs_what_solvent_was_doing(self):
        for step, checkpoint in (("intake", "JOB_INTAKEN"),
                                 ("commit_requirements", "BASELINE_COMMITTED"),
                                 ("execute", "")):
            self.r.frame(trace_id="t1", component="orchestrator",
                         operation=step, state="EXECUTING", checkpoint=checkpoint)
        frames = self.r.frames("t1")
        self.assertEqual([f["operation"] for f in frames],
                         ["intake", "commit_requirements", "execute"])

    def test_the_last_durable_checkpoint_is_the_one_a_restart_resumes_from(self):
        self.r.frame(trace_id="t1", component="o", operation="a",
                     checkpoint="BASELINE_COMMITTED")
        self.r.frame(trace_id="t1", component="o", operation="b")
        self.assertEqual(self.r.last_checkpoint("t1"), "BASELINE_COMMITTED")

    def test_a_trace_with_no_checkpoint_says_so_rather_than_guessing(self):
        self.r.frame(trace_id="t2", component="o", operation="a")
        self.assertEqual(self.r.last_checkpoint("t2"), "")

    def test_frames_are_bounded_per_trace(self):
        """An unbounded diagnostic log becomes the disk-pressure incident it
        exists to help diagnose."""
        for index in range(res.FRAMES_PER_TRACE + 25):
            self.r.frame(trace_id="t3", component="o", operation=f"step{index}")
        self.assertLessEqual(len(self.r.frames("t3")), res.FRAMES_PER_TRACE)

    def test_the_newest_frames_are_the_ones_kept(self):
        for index in range(res.FRAMES_PER_TRACE + 5):
            self.r.frame(trace_id="t4", component="o", operation=f"step{index}")
        operations = [f["operation"] for f in self.r.frames("t4")]
        self.assertIn(f"step{res.FRAMES_PER_TRACE + 4}", operations)
        self.assertNotIn("step0", operations)

    def test_a_secret_never_reaches_a_frame(self):
        self.r.frame(trace_id="t5", component="payments", operation="sign",
                     decision=f"used {CANARIES['SOLVENT_STRIPE_SECRET']}",
                     detail={"key": CANARIES["SOLVENT_STRIPE_SECRET"]})
        blob = str(self.r.frames("t5"))
        self.assertNotIn(CANARIES["SOLVENT_STRIPE_SECRET"], blob)


class CircuitBreakersContainAndDoNotPermit(unittest.TestCase):
    def setUp(self):
        self.s = Solvent()
        self.r = self.s.resilience
        self.at = [1000.0]

    def clock(self):
        return self.at[0]

    def test_a_healthy_component_is_callable(self):
        self.assertTrue(self.r.may_call("model", clock=self.clock)[0])

    def test_repeated_failure_opens_the_breaker(self):
        for _ in range(res.DEFAULT_FAILURE_THRESHOLD):
            self.r.failure("model", why="provider 503", clock=self.clock)
        allowed, why = self.r.may_call("model", clock=self.clock)
        self.assertFalse(allowed)
        self.assertIn("contained", why)

    def test_one_failure_does_not(self):
        self.r.failure("model", why="one blip", clock=self.clock)
        self.assertTrue(self.r.may_call("model", clock=self.clock)[0])

    def test_success_closes_it_again(self):
        for _ in range(res.DEFAULT_FAILURE_THRESHOLD):
            self.r.failure("model", clock=self.clock)
        self.r.success("model", clock=self.clock)
        self.assertTrue(self.r.may_call("model", clock=self.clock)[0])
        self.assertEqual(self.r.breaker("model")["state"], res.CLOSED)

    def test_after_the_cooldown_exactly_one_canary_is_allowed(self):
        for _ in range(res.DEFAULT_FAILURE_THRESHOLD):
            self.r.failure("model", clock=self.clock)
        self.at[0] += res.DEFAULT_COOLDOWN_SECONDS + 1
        allowed, why = self.r.may_call("model", clock=self.clock)
        self.assertTrue(allowed)
        self.assertIn("canary", why)
        self.assertEqual(self.r.breaker("model")["state"], res.HALF_OPEN)

    def test_a_failed_canary_reopens_the_breaker_and_restarts_the_cooldown(self):
        for _ in range(res.DEFAULT_FAILURE_THRESHOLD):
            self.r.failure("model", clock=self.clock)
        self.at[0] += res.DEFAULT_COOLDOWN_SECONDS + 1
        self.r.may_call("model", clock=self.clock)
        self.r.record_canary("model", passed=False, detail="still 503",
                             clock=self.clock)
        self.assertEqual(self.r.breaker("model")["state"], res.OPEN)
        self.assertFalse(self.r.may_call("model", clock=self.clock)[0])

    def test_a_passing_canary_restores_the_component(self):
        for _ in range(res.DEFAULT_FAILURE_THRESHOLD):
            self.r.failure("model", clock=self.clock)
        self.at[0] += res.DEFAULT_COOLDOWN_SECONDS + 1
        self.r.may_call("model", clock=self.clock)
        self.r.record_canary("model", passed=True, clock=self.clock)
        self.assertEqual(self.r.breaker("model")["state"], res.CLOSED)
        self.assertEqual(self.r.breaker("model")["failure_count"], 0)

    def test_opening_a_breaker_never_names_a_fallback(self):
        """A breaker says stop hammering this. It cannot say use that instead:
        choosing a different provider is a permission question, and nothing
        here can answer one."""
        for _ in range(res.DEFAULT_FAILURE_THRESHOLD):
            self.r.failure("model", clock=self.clock)
        breaker = self.r.breaker("model")
        self.assertNotIn("fallback", breaker)
        self.assertNotIn("alternative", str(breaker).lower())

    def test_an_open_breaker_changes_nothing_governed(self):
        before = {
            "policy": self.s.policy.version,
            "mode": self.s.policy.operating_mode.value,
            "allowlist": self.s.policy.get("egress", "allowlist", default=[]),
            "simulation": self.s.policy.get("egress", "simulation_only",
                                            default=True),
            "capabilities": sorted(c.name for c in self.s.capability.capabilities()),
        }
        for component in ("model", "payments", "sms"):
            for _ in range(res.DEFAULT_FAILURE_THRESHOLD + 2):
                self.r.failure(component, clock=self.clock)
        self.assertEqual({
            "policy": self.s.policy.version,
            "mode": self.s.policy.operating_mode.value,
            "allowlist": self.s.policy.get("egress", "allowlist", default=[]),
            "simulation": self.s.policy.get("egress", "simulation_only",
                                            default=True),
            "capabilities": sorted(c.name for c in self.s.capability.capabilities()),
        }, before)

    def test_opening_and_closing_are_on_the_audit_record(self):
        for _ in range(res.DEFAULT_FAILURE_THRESHOLD):
            self.r.failure("model", why="provider 503", clock=self.clock)
        self.r.success("model", clock=self.clock)
        events = [e["event"] for e in self.s.audit.events()
                  if e["event"].startswith("resilience.breaker")]
        self.assertEqual(events, ["resilience.breaker_opened",
                                  "resilience.breaker_closed"])


class ItHoldsNoAuthority(unittest.TestCase):
    """Diagnosis is not permission, proved by what it cannot reach."""

    def setUp(self):
        self.s = Solvent()

    def test_it_owns_only_its_own_tables(self):
        from solvent.store import TABLE_OWNER

        owned = {t for t, who in TABLE_OWNER.items() if who == "resilience"}
        self.assertEqual(owned, {"incidents", "incident_events",
                                 "incident_lessons", "flight_frames",
                                 "circuit_breakers"})

    def test_it_cannot_write_to_another_authoritys_table(self):
        connection = self.s.store.for_authority("resilience")
        for table in ("policy_versions", "ledger_entries", "audit_log",
                      "capability_registrations", "action_requests"):
            with self.subTest(table=table):
                with self.assertRaises(Exception):
                    connection.execute(f"DELETE FROM {table}")

    def test_an_incident_history_cannot_be_rewritten(self):
        incident = self.s.resilience.record_incident(
            component="c", operation="o", error_class="E")
        connection = self.s.store.for_authority("resilience")
        for statement in ("UPDATE incident_events SET detail = 'nothing happened'",
                          "DELETE FROM incident_events"):
            with self.subTest(statement=statement[:20]):
                with self.assertRaises(Exception):
                    connection.execute(statement)
                    connection.commit()
        self.assertTrue(self.s.resilience.timeline(incident))

    def test_a_verified_lesson_cannot_be_edited(self):
        """Against a real row. An UPDATE matching nothing succeeds trivially,
        which is how an append-only test passes against an empty table."""
        incident = self.s.resilience.record_incident(
            component="c", operation="o", error_class="E")
        self.s.resilience.record_root_cause(incident, root_cause="cause")
        self.s.resilience.record_fix(incident, fix="the real fix")
        self.s.resilience.verify_fix(incident, verification_ref="a test")
        print_ = self.s.resilience.incident(incident)["fingerprint"]
        self.assertIsNotNone(self.s.resilience.lesson_for(print_),
                             "no lesson to protect, so this proves nothing")

        connection = self.s.store.for_authority("resilience")
        for statement in ("UPDATE incident_lessons SET fix = 'something else'",
                          "DELETE FROM incident_lessons"):
            with self.subTest(statement=statement[:22]):
                with self.assertRaises(Exception):
                    connection.execute(statement)
                    connection.commit()
        self.assertEqual(self.s.resilience.lesson_for(print_)["fix"],
                         "the real fix")

    def test_the_module_never_imports_an_authority_it_could_command(self):
        import ast

        source = pathlib.Path("solvent/resilience.py").read_text()
        imported = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        for forbidden in ("policy", "ledger", "governor", "gate", "capability",
                          "orchestrator", "skillslab"):
            self.assertNotIn(forbidden, imported,
                             f"resilience imports {forbidden}; it would be able "
                             "to command what it is only allowed to observe")


class ItSurvivesARestart(unittest.TestCase):
    def test_incidents_breakers_and_lessons_all_survive(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(pathlib.Path(tmp) / "solvent.db")
            before = Solvent(path)
            incident = before.resilience.record_incident(
                component="payments", operation="ingest",
                error_class="TimeoutError", signature="timed out")
            before.resilience.record_root_cause(incident, root_cause="dns")
            before.resilience.record_fix(incident, fix="retry with backoff")
            before.resilience.verify_fix(incident, verification_ref="a test")
            for _ in range(res.DEFAULT_FAILURE_THRESHOLD):
                before.resilience.failure("model")
            print_ = before.resilience.incident(incident)["fingerprint"]
            before.store.close()

            after = Solvent(path)
            self.assertEqual(after.resilience.incident(incident)["status"],
                             res.VERIFIED)
            self.assertEqual(after.resilience.breaker("model")["state"], res.OPEN)
            self.assertIsNotNone(after.resilience.lesson_for(print_))
            self.assertTrue(after.audit.verify_chain()[0])
