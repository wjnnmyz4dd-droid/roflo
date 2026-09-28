"""Reaching the owner without handing anything to whoever holds their phone.

Three properties, and the third is the one that makes the other two safe.

**It reaches them.** A business that only tells its owner things on a website
tells them nothing while they are asleep. Actionable events go to a text;
urgent ones that nobody acknowledges become a phone call.

**It does not exhaust them.** A health check that runs every minute must not
send a thousand identical texts. An owner who learns to ignore the channel has
the same protection as an owner who was never notified, minus the battery.

**It grants nothing.** Receiving a text is not approving anything, and there is
deliberately no reply-to-approve path: SMS is an unauthenticated channel that
anyone who can spoof a sender can write to, and a "reply YES to release the
payment" flow would put the business behind a sender-id check.
"""

from __future__ import annotations

import os
import pathlib
import tempfile
import unittest

from solvent import notify
from solvent.errors import FailClosed
from solvent.harness import OWNER, Solvent

#: Obviously not a real number, and never sent to: every provider in these
#: tests records and dispatches nothing.
TEST_PHONE = "+15550000000"


class Base(unittest.TestCase):
    def setUp(self):
        previous = os.environ.get(notify.PHONE_ENV)
        os.environ[notify.PHONE_ENV] = TEST_PHONE
        self.addCleanup(
            lambda: (os.environ.__setitem__(notify.PHONE_ENV, previous)
                     if previous is not None
                     else os.environ.pop(notify.PHONE_ENV, None)))
        self.s = Solvent()
        self.n = self.s.notifier
        self.at = [1000.0]

    def clock(self):
        return self.at[0]

    def sms(self):
        return self.n._providers[notify.SMS]

    def voice(self):
        return self.n._providers[notify.VOICE]

    def raise_(self, severity=notify.ACTION_REQUIRED, **kwargs):
        base = dict(event="test.event", severity=severity,
                    summary="something happened", clock=self.clock)
        return self.n.notify(**{**base, **kwargs})


class SeverityDecidesTheChannel(Base):
    def test_routine_information_does_not_reach_a_phone(self):
        """Alert fatigue is the failure mode that disables the whole channel."""
        self.raise_(notify.INFO)
        self.assertEqual(self.sms().sent, [])
        self.assertEqual(self.voice().sent, [])

    def test_something_actionable_sends_a_text(self):
        self.raise_(notify.ACTION_REQUIRED)
        self.assertEqual(len(self.sms().sent), 1)
        self.assertEqual(self.voice().sent, [])

    def test_something_urgent_sends_a_text_immediately(self):
        self.raise_(notify.URGENT)
        self.assertEqual(len(self.sms().sent), 1)

    def test_something_critical_calls_as_well(self):
        self.raise_(notify.CRITICAL)
        self.assertEqual(len(self.sms().sent), 1)
        self.assertEqual(len(self.voice().sent), 1)

    def test_an_unknown_severity_is_refused(self):
        with self.assertRaises(FailClosed):
            self.raise_("EXTREMELY_URGENT")

    def test_policy_can_change_the_routing(self):
        """The routing is a business decision, not a constant in a module."""
        self.s.policy.amend(
            {"notifications": {"routing": {notify.INFO: [notify.WEBSITE,
                                                         notify.SMS]}}},
            OWNER, "the owner wants informational texts")
        self.raise_(notify.INFO)
        self.assertEqual(len(self.sms().sent), 1)


class DeliveryIsReportedHonestly(Base):
    def test_accepted_for_sending_is_sent_and_not_delivered(self):
        """Most providers confirm only that they took the request. Showing
        DELIVERED for that tells the owner something nobody established."""
        notification = self.raise_(notify.URGENT)
        self.assertEqual(self.n.notification(notification)["state"], notify.SENT)

    def test_delivered_is_recorded_only_when_a_provider_confirms_it(self):
        self.n._providers[notify.SMS] = notify.RecordingProvider(
            notify.SMS, confirms_delivery=True)
        notification = self.raise_(notify.URGENT)
        self.assertEqual(self.n.notification(notification)["state"],
                         notify.DELIVERED)

    def test_a_provider_that_refuses_is_recorded_as_failed(self):
        self.n._providers[notify.SMS] = notify.RecordingProvider(
            notify.SMS, accept=False)
        notification = self.raise_(notify.URGENT)
        self.assertEqual(self.n.notification(notification)["state"], notify.FAILED)

    def test_no_provider_configured_fails_rather_than_vanishing(self):
        self.n._providers = {}
        notification = self.raise_(notify.URGENT)
        row = self.n.notification(notification)
        self.assertEqual(row["state"], notify.FAILED)
        self.assertIn("provider", row["detail"])

    def test_no_phone_number_fails_rather_than_vanishing(self):
        os.environ.pop(notify.PHONE_ENV, None)
        notification = self.raise_(notify.URGENT)
        row = self.n.notification(notification)
        self.assertEqual(row["state"], notify.FAILED)
        self.assertIn("phone", row["detail"])

    def test_a_failed_notification_is_still_visible_to_the_owner(self):
        self.n._providers = {}
        self.raise_(notify.CRITICAL)
        self.assertTrue(self.n.unresolved(),
                        "a notification that could not be sent disappeared")


class TheTextToCallEscalation(Base):
    def test_an_acknowledged_notification_is_never_called_about(self):
        notification = self.raise_(notify.URGENT)
        self.n.acknowledge(notification)
        self.at[0] += notify.DEFAULT_ESCALATE_AFTER_SECONDS * 4
        self.assertEqual(self.n.due_for_escalation(clock=self.clock), [])
        self.assertEqual(self.voice().sent, [])

    def test_an_unacknowledged_urgent_becomes_due_after_the_interval(self):
        self.raise_(notify.URGENT)
        self.assertEqual(self.n.due_for_escalation(clock=self.clock), [])
        self.at[0] += notify.DEFAULT_ESCALATE_AFTER_SECONDS + 1
        self.assertEqual(len(self.n.due_for_escalation(clock=self.clock)), 1)

    def test_escalating_places_the_call(self):
        notification = self.raise_(notify.URGENT)
        self.at[0] += notify.DEFAULT_ESCALATE_AFTER_SECONDS + 1
        self.assertEqual(self.n.escalate(notification, clock=self.clock),
                         notify.ESCALATED)
        self.assertEqual(len(self.voice().sent), 1)

    def test_escalating_something_acknowledged_is_refused_as_noise(self):
        notification = self.raise_(notify.URGENT)
        self.n.acknowledge(notification)
        with self.assertRaises(FailClosed):
            self.n.escalate(notification, clock=self.clock)

    def test_a_failed_call_leaves_the_notification_unresolved(self):
        """The website must still show it. A critical alert that failed to
        reach anybody and then reported itself handled is the worst outcome."""
        self.n._providers[notify.VOICE] = notify.RecordingProvider(
            notify.VOICE, accept=False)
        notification = self.raise_(notify.URGENT)
        self.at[0] += notify.DEFAULT_ESCALATE_AFTER_SECONDS + 1
        self.n.escalate(notification, clock=self.clock)
        self.assertEqual(self.n.notification(notification)["state"],
                         notify.FAILED)
        self.assertIn(notification, [n["id"] for n in self.n.unresolved()])

    def test_the_interval_comes_from_policy(self):
        self.s.policy.amend({"notifications": {"escalate_after_seconds": 60}},
                            OWNER, "the owner wants to be called sooner")
        self.raise_(notify.URGENT)
        self.at[0] += 61
        self.assertEqual(len(self.n.due_for_escalation(clock=self.clock)), 1)

    def test_an_informational_notification_never_becomes_a_call(self):
        self.raise_(notify.INFO)
        self.at[0] += notify.DEFAULT_ESCALATE_AFTER_SECONDS * 10
        self.assertEqual(self.n.due_for_escalation(clock=self.clock), [])


class NoNotificationStorm(Base):
    def test_a_health_check_running_every_minute_sends_one_text(self):
        for minute in range(120):
            self.at[0] += 60
            self.raise_(notify.URGENT, dedupe_key="firewall-unsafe")
        self.assertEqual(len(self.sms().sent), 1)

    def test_deduplication_is_by_thing_not_by_wording(self):
        self.raise_(notify.URGENT, summary="the firewall is unsafe",
                    dedupe_key="firewall-unsafe")
        self.raise_(notify.URGENT, summary="firewall posture is not recorded",
                    dedupe_key="firewall-unsafe")
        self.assertEqual(len(self.sms().sent), 1)

    def test_a_genuine_escalation_still_gets_through(self):
        """Deduplication that suppressed a worsening situation would be worse
        than the storm it prevents."""
        self.raise_(notify.ACTION_REQUIRED, dedupe_key="disk")
        self.raise_(notify.CRITICAL, dedupe_key="disk")
        self.assertEqual(len(self.sms().sent), 2)
        self.assertEqual(len(self.voice().sent), 1)

    def test_a_new_occurrence_after_acknowledgement_is_a_new_notification(self):
        first = self.raise_(notify.URGENT, dedupe_key="disk")
        self.n.acknowledge(first)
        second = self.raise_(notify.URGENT, dedupe_key="disk")
        self.assertNotEqual(first, second)
        self.assertEqual(len(self.sms().sent), 2)

    def test_different_things_are_not_deduplicated_together(self):
        self.raise_(notify.URGENT, dedupe_key="disk")
        self.raise_(notify.URGENT, dedupe_key="firewall")
        self.assertEqual(len(self.sms().sent), 2)


class ItSaysAsLittleAsPossible(Base):
    """A text is read off a lock screen and backed up to whatever syncs it."""

    def test_the_body_names_what_happened_and_where_to_look(self):
        notification = self.raise_(notify.URGENT, event="audit.chain_broken")
        body = self.n.notification(notification)["body"]
        self.assertIn("audit.chain_broken", body)
        self.assertIn("control centre", body)

    def test_a_secret_in_the_summary_never_reaches_the_message(self):
        canary = "canary-owner-key-never-real-0123456789abcdef"
        previous = os.environ.get("SOLVENT_OWNER_KEY")
        os.environ["SOLVENT_OWNER_KEY"] = canary
        self.addCleanup(
            lambda: (os.environ.__setitem__("SOLVENT_OWNER_KEY", previous)
                     if previous is not None
                     else os.environ.pop("SOLVENT_OWNER_KEY", None)))
        notification = self.raise_(notify.URGENT,
                                   summary=f"signing with {canary} failed")
        row = self.n.notification(notification)
        self.assertNotIn(canary, row["body"])
        self.assertNotIn(canary, row["summary"])
        self.assertNotIn(canary, str(self.sms().sent))

    def test_the_message_is_short_enough_to_be_one_text(self):
        notification = self.raise_(notify.URGENT, summary="x" * 900)
        self.assertLessEqual(len(self.n.notification(notification)["body"]), 280)

    def test_a_phone_number_is_only_ever_displayed_masked(self):
        self.assertEqual(notify.mask("+1 (555) 867-5309"), "***-***-5309")
        self.assertEqual(notify.mask(""), "")
        self.assertEqual(notify.mask("12"), "")

    def test_the_number_is_never_written_to_policy(self):
        import json

        self.assertNotIn(TEST_PHONE, json.dumps(self.s.policy.doc, default=str))

    def test_the_number_is_never_written_to_the_audit_log(self):
        import json

        self.raise_(notify.CRITICAL)
        self.assertNotIn(TEST_PHONE, json.dumps(
            [dict(e) for e in self.s.audit.events()], default=str))


class ANotificationGrantsNothing(Base):
    def posture(self):
        return {
            "policy": self.s.policy.version,
            "mode": self.s.policy.operating_mode.value,
            "simulation": self.s.policy.get("egress", "simulation_only",
                                            default=True),
            "capabilities": sorted(c.name for c in self.s.capability.capabilities()),
            "ledger": len(self.s.store.raw_readonly("SELECT * FROM ledger_entries")),
        }

    def test_raising_one_changes_nothing_governed(self):
        before = self.posture()
        for severity in notify.SEVERITIES:
            self.raise_(severity, dedupe_key=severity)
        self.assertEqual(self.posture(), before)

    def test_acknowledging_one_changes_nothing_governed(self):
        notification = self.raise_(notify.CRITICAL)
        before = self.posture()
        self.n.acknowledge(notification)
        self.assertEqual(self.posture(), before)

    def test_acknowledging_is_not_approving(self):
        """It records that a person is aware. Anything that follows still goes
        through whichever authority owns it."""
        notification = self.raise_(notify.CRITICAL, event="owner.approval_needed")
        self.n.acknowledge(notification)
        self.assertEqual(self.s.owner.outstanding(), [])

    def test_there_is_no_reply_to_approve_path(self):
        """SMS is unauthenticated. A reply-to-approve flow would put the
        business behind a sender-id check."""
        source = pathlib.Path("solvent/notify.py").read_text().lower()
        for forbidden in ("def receive", "def on_reply", "def handle_reply",
                          "reply_to_approve", "def inbound"):
            self.assertNotIn(forbidden, source)

    def test_the_module_cannot_command_an_authority(self):
        import ast

        tree = ast.parse(pathlib.Path("solvent/notify.py").read_text())
        imported = {node.module for node in ast.walk(tree)
                    if isinstance(node, ast.ImportFrom) and node.module}
        for forbidden in ("policy", "ledger", "governor", "gate", "capability",
                          "orchestrator"):
            self.assertNotIn(forbidden, imported)


class ItSurvivesARestart(unittest.TestCase):
    def test_notifications_and_acknowledgements_both_survive(self):
        previous = os.environ.get(notify.PHONE_ENV)
        os.environ[notify.PHONE_ENV] = TEST_PHONE
        self.addCleanup(
            lambda: (os.environ.__setitem__(notify.PHONE_ENV, previous)
                     if previous is not None
                     else os.environ.pop(notify.PHONE_ENV, None)))
        with tempfile.TemporaryDirectory() as tmp:
            path = str(pathlib.Path(tmp) / "solvent.db")
            before = Solvent(path)
            acknowledged = before.notifier.notify(
                event="a", severity=notify.URGENT, summary="one")
            unresolved = before.notifier.notify(
                event="b", severity=notify.CRITICAL, summary="two")
            before.notifier.acknowledge(acknowledged)
            before.store.close()

            after = Solvent(path)
            self.assertEqual(after.notifier.notification(acknowledged)["state"],
                             notify.ACKNOWLEDGED)
            self.assertIn(unresolved,
                          [n["id"] for n in after.notifier.unresolved()])
            self.assertNotIn(acknowledged,
                             [n["id"] for n in after.notifier.unresolved()])


class AcknowledgingDoesNotEraseADeliveryFailure(Base):
    """Found by the invariant review: acknowledging moved the row to
    ACKNOWLEDGED, and the count of notifications a channel could not carry —
    which the diagnostics page reads — silently went to zero. The owner reading
    it on the website is a legitimate acknowledgement; the SMS channel being
    broken is a separate fact and it stays true afterwards."""

    def test_a_failed_send_is_still_counted_after_acknowledgement(self):
        self.n._providers = {}
        notification = self.raise_(notify.CRITICAL)
        self.assertEqual(len(self.n.delivery_failures()), 1)
        self.n.acknowledge(notification)
        self.assertEqual(self.n.notification(notification)["state"],
                         notify.ACKNOWLEDGED)
        self.assertEqual(len(self.n.delivery_failures()), 1,
                         "acknowledging erased the record of a broken channel")

    def test_a_successful_send_is_not_counted_as_a_failure(self):
        self.raise_(notify.URGENT)
        self.assertEqual(self.n.delivery_failures(), [])

    def test_diagnostics_reports_the_broken_channel_after_acknowledgement(self):
        from solvent.diagnostics import Diagnostics, DEGRADED

        self.n._providers = {}
        notification = self.raise_(notify.CRITICAL)
        self.n.acknowledge(notification)
        self.s.policy.amend(
            {"notifications": {"owner_phone_mask": "***-***-0000",
                               "sms_provider": "example"}},
            OWNER, "configured, and still not working")
        self.assertEqual(Diagnostics(self.s).notifications().state, DEGRADED)
