"""§49, §92: the website may not show a green light Solvent has not earned.

Every test here breaks something in the backend and then reads the page. The
failure mode being attacked is specific and it is the most dangerous one a
control centre has: a status that is *reassuring and wrong*. An owner who is
told the firewall is fine stops checking the firewall.

The pages are read the way a browser reads them — through :class:`ControlCentre`
with a real session — and asserted on rendered text, because a read model that
returns the right value into a page that prints something else is exactly the
bug this file exists to catch.
"""

from __future__ import annotations

import os
import pathlib
import re
import tempfile
import unittest

from solvent import notify
from solvent import resilience as res
from solvent.harness import CSV_PROMOTION, OWNER, Solvent, provision_capability
from solvent.web import auth as web_auth
from solvent.web import server as web_server
from solvent.web.app import Request

PASSWORD = "TEST-ONLY-control-centre-password"

#: Pages added by this pass, checked as a set so a new one cannot be added
#: without deciding whether it is reachable anonymously.
NEW_PAGES = ("/incidents", "/incident", "/diagnostics", "/breakers",
             "/notifications", "/recommendations", "/wizard")


def text_of(response) -> str:
    """Rendered text with the markup removed, as a reader would see it."""
    stripped = re.sub(r"<[^>]+>", " ", response.body.decode())
    return re.sub(r"\s+", " ", stripped)


def after_heading(body: str, heading: str, marker: str) -> str:
    """Text following ``marker``, searched *after* ``heading``.

    Needed because the navigation names every page, so searching the whole
    document for "Capabilities" finds the nav link rather than the table row —
    and a test that reads the nav is a test that passes whatever the page says.
    """
    start = body.find(heading)
    assert start != -1, f"{heading!r} is not on the page"
    offset = body.find(marker, start)
    assert offset != -1, f"{marker!r} does not appear after {heading!r}"
    return body[offset:]


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.db = str(pathlib.Path(self.dir.name) / "solvent.db")
        self.spool = pathlib.Path(self.dir.name) / "spool"
        self.solvent = Solvent(self.db)
        self.arrange(self.solvent)
        self.solvent.store.close()
        self.centre = web_server.build(
            self.db, password_hash=web_auth.hash_password(PASSWORD),
            spool=str(self.spool))
        self.addCleanup(self.centre.read.close)

    def arrange(self, solvent):
        """Override to break something before the website reads it."""

    def page(self, path, **query):
        response = self.centre.handle(Request(
            "POST", "/login", form={"password": PASSWORD}, source="10.0.0.1"))
        cookies = {"solvent_session": response.set_session}
        return text_of(self.centre.handle(
            Request("GET", path, query=query, cookies=cookies)))


class AnUnrecordedFirewallCannotReadAsSafe(Base):
    def test_diagnostics_says_unsafe(self):
        body = self.page("/diagnostics")
        self.assertIn("Network firewall", body)
        self.assertIn("unsafe", body)

    def test_it_does_not_say_the_firewall_is_fine(self):
        body = self.page("/diagnostics")
        window = after_heading(body, "what it means", "Network firewall")[:60]
        self.assertNotIn("healthy", window)

    def test_it_says_what_to_run(self):
        self.assertIn("firewall.sh", self.page("/diagnostics"))


class AFirewallRecordedAsOpenCannotReadAsSafe(Base):
    def arrange(self, solvent):
        solvent.policy.record_network_posture(
            owner_identity=OWNER, default_deny=False,
            note="recorded as not applied")

    def test_diagnostics_says_unsafe(self):
        body = self.page("/diagnostics")
        window = after_heading(body, "what it means", "Network firewall")[:60]
        self.assertIn("unsafe", window)
        self.assertNotIn("healthy", window)


class AnOpenBreakerCannotReadAsHealthy(Base):
    def arrange(self, solvent):
        for _ in range(res.DEFAULT_FAILURE_THRESHOLD):
            solvent.resilience.failure("model", why="provider returned 503")

    def test_the_breaker_page_shows_it_open(self):
        body = self.page("/breakers")
        self.assertIn("model", body)
        self.assertIn("open", body)

    def test_diagnostics_does_not_call_it_healthy(self):
        body = self.page("/diagnostics")
        window = after_heading(body, "what it means", "Circuit breakers")[:120]
        self.assertIn("degraded", window)
        self.assertNotIn("all closed", window)

    def test_the_recommendation_says_it_was_handled_automatically(self):
        body = self.page("/recommendations")
        self.assertIn("contained", body)
        self.assertIn("automatically handled", body)


class AFailedNotificationCannotReadAsDelivered(Base):
    def arrange(self, solvent):
        solvent.notifier._providers = {}
        solvent.notifier.notify(event="firewall.unsafe",
                                severity=notify.CRITICAL,
                                summary="the host firewall is unrecorded")

    def test_the_page_shows_it_failed(self):
        body = self.page("/notifications")
        self.assertIn("failed", body)

    def test_it_never_claims_delivery(self):
        body = self.page("/notifications")
        self.assertNotIn("delivered", body)

    def test_diagnostics_reports_the_delivery_failure(self):
        self.assertIn("could not be delivered", self.page("/diagnostics"))


class SentIsNotDelivered(Base):
    def arrange(self, solvent):
        os.environ[notify.PHONE_ENV] = "+15550000000"
        self.addCleanup(lambda: os.environ.pop(notify.PHONE_ENV, None))
        solvent.notifier.notify(event="disk.pressure", severity=notify.URGENT,
                                summary="disk is filling")

    def test_a_provider_that_only_accepted_it_reads_as_sent(self):
        """Most providers confirm only that they took the request. Showing
        DELIVERED for that tells the owner something nobody established."""
        body = self.page("/notifications")
        self.assertIn("sent", body)
        self.assertNotIn("delivered", body)


class AnUnprovisionedCapabilityCannotReadAsAvailable(Base):
    def test_diagnostics_says_nothing_can_be_delivered(self):
        body = self.page("/diagnostics")
        # Bounded to this row: the next row begins at "Incidents", and a
        # window that ran past it would read the following check's state.
        window = after_heading(body, "what it means", "Capabilities")
        window = window[:window.find("Incidents")]
        self.assertIn("owner action required", window)
        self.assertNotIn("healthy", window)

    def test_the_wizard_says_the_step_is_outstanding(self):
        body = self.page("/wizard")
        window = after_heading(body, "what it is for", "A proven capability")[:220]
        self.assertIn("action required", window)


class AProvisionedCapabilityReadsAsAvailable(Base):
    def arrange(self, solvent):
        provision_capability(solvent, CSV_PROMOTION, owner_identity=OWNER)

    def test_diagnostics_names_it(self):
        body = self.page("/diagnostics")
        window = after_heading(body, "what it means", "Capabilities")[:120]
        self.assertIn("healthy", window)
        self.assertIn("proven", window)

    def test_the_wizard_marks_the_step_done(self):
        body = self.page("/wizard")
        window = after_heading(body, "what it is for", "A proven capability")[:220]
        self.assertIn("done", window)


class ABrokenAuditChainCannotReadAsIntact(Base):
    def arrange(self, solvent):
        solvent.audit.record(event="filler", authority="test",
                             initiator="test", why="a row to corrupt")
        # Reach past the authority boundary the way a tampering attacker would:
        # straight at the file. The trigger stops an UPDATE, so the chain is
        # broken by appending a row whose prev_hash does not follow.
        import sqlite3

        solvent.store.close()
        connection = sqlite3.connect(self.db)
        connection.execute(
            "INSERT INTO audit_log(ts,event,authority,initiator,job_id,why,"
            "input_ref,decision,permission,financial_authorization,"
            "external_effect,result,verification_ref,payload,prev_hash,hash) "
            "VALUES('2026-01-01','forged','policy','attacker',NULL,'forged',"
            "'','','','','','','','{}','deadbeef','deadbeef')")
        connection.commit()
        connection.close()

    def test_diagnostics_reports_the_chain_as_failed(self):
        body = self.page("/diagnostics")
        window = after_heading(body, "what it means", "Audit chain")[:200]
        self.assertIn("failed", window)
        self.assertIn("does not verify", window)

    def test_it_tells_the_owner_to_stop(self):
        self.assertIn("stop work", self.page("/diagnostics"))


class AnIncidentIsShownWithItsRealStatus(Base):
    def arrange(self, solvent):
        incident = solvent.resilience.record_incident(
            component="payments", operation="ingest", error_class="TimeoutError",
            signature="timed out after 30s", severity=res.S_SERIOUS,
            trigger="webhook ingest")
        solvent.resilience.record_root_cause(incident, root_cause="dns failure")
        self.incident = incident

    def test_an_unfixed_incident_does_not_read_as_fixed(self):
        body = self.page("/incidents")
        self.assertIn("payments", body)
        self.assertIn("fix pending", body)

    def test_the_detail_page_says_the_fix_is_not_verified(self):
        body = self.page("/incident", id=self.incident)
        self.assertIn("not verified", body)
        self.assertIn("not proven by the symptom stopping", body)

    def test_a_new_failure_class_says_so_rather_than_inventing_a_history(self):
        body = self.page("/incident", id=self.incident)
        self.assertIn("new failure class", body)

    def test_an_unknown_incident_id_does_not_invent_one(self):
        self.assertIn("No such incident", self.page("/incident", id="inc_nope"))


class ARecurrenceIsShownAsARecurrence(Base):
    def arrange(self, solvent):
        for _ in range(3):
            solvent.resilience.record_incident(
                component="model", operation="call", error_class="TimeoutError",
                signature="timed out")

    def test_the_list_shows_the_recurrence_count(self):
        self.assertIn("3×", self.page("/incidents"))

    def test_the_detail_says_it_has_happened_before(self):
        rows = self.centre.read.incidents(component="model")
        body = self.page("/incident", id=rows[0]["id"])
        self.assertIn("earlier incident", body)
        self.assertNotIn("new failure class", body)

    def test_repeated_failure_becomes_a_recommendation(self):
        self.assertIn("keeps failing", self.page("/recommendations"))


class TheNewPagesAreNotAWayIn(Base):
    def test_every_new_page_redirects_an_anonymous_visitor(self):
        for path in NEW_PAGES:
            with self.subTest(path=path):
                response = self.centre.handle(Request("GET", path))
                self.assertEqual(response.status, 303)
                self.assertEqual(response.body, b"")

    def test_no_new_page_writes_anything(self):
        before = (len(self.centre.read.incidents()),
                  len(self.centre.read.notifications()),
                  len(self.centre.read.breakers()),
                  len(self.centre.read.audit(limit=10 ** 9)))
        for path in NEW_PAGES:
            self.page(path)
        self.assertEqual(
            (len(self.centre.read.incidents()),
             len(self.centre.read.notifications()),
             len(self.centre.read.breakers()),
             len(self.centre.read.audit(limit=10 ** 9))), before)

    def test_a_hostile_filter_is_escaped_on_every_new_page(self):
        hostile = "<script>alert('xss')</script>"
        for path in NEW_PAGES:
            with self.subTest(path=path):
                for key in ("id", "status", "component", "fingerprint"):
                    body = self.page(path, **{key: hostile})
                    self.assertNotIn(hostile, body)

    def test_no_new_page_shows_a_secret(self):
        canary = "canary-owner-key-never-real-0123456789abcdef"
        previous = os.environ.get("SOLVENT_OWNER_KEY")
        os.environ["SOLVENT_OWNER_KEY"] = canary
        self.addCleanup(
            lambda: (os.environ.__setitem__("SOLVENT_OWNER_KEY", previous)
                     if previous is not None
                     else os.environ.pop("SOLVENT_OWNER_KEY", None)))
        for path in NEW_PAGES:
            with self.subTest(path=path):
                body = self.page(path)
                self.assertNotIn(canary, body)
                self.assertNotIn("SOLVENT_OWNER_KEY=", body)

    def test_the_phone_number_is_only_ever_shown_masked(self):
        os.environ[notify.PHONE_ENV] = "+15558675309"
        self.addCleanup(lambda: os.environ.pop(notify.PHONE_ENV, None))
        body = self.page("/notifications")
        self.assertNotIn("5558675309", body)
        self.assertNotIn("8675309", body)


class TheWizardIsHonestAboutWhatIsNeededYet(Base):
    def test_a_payment_rail_is_not_a_blocker_for_a_first_trial(self):
        """Sending an owner to configure Stripe before they have delivered
        anything is how a setup guide stops being followed."""
        body = self.page("/wizard")
        window = after_heading(body, "what it is for", "Payment rail")[:220]
        self.assertIn("not required yet", window)

    def test_work_sources_are_not_a_blocker_either(self):
        body = self.page("/wizard")
        window = after_heading(body, "what it is for", "Work sources")[:220]
        self.assertIn("not required yet", window)

    def test_it_counts_only_the_steps_that_block_a_first_job(self):
        self.assertIn("stand between here and a first supervised job",
                      self.page("/wizard"))

    def test_it_never_asks_for_a_secret_on_the_page(self):
        body = self.page("/wizard")
        self.assertIn("goes in at a terminal", body)
        self.assertNotIn("<input", self.centre.handle(Request(
            "GET", "/wizard",
            cookies={"solvent_session": self.centre.handle(Request(
                "POST", "/login", form={"password": PASSWORD},
                source="10.0.0.1")).set_session})).body.decode()
            .split("Getting started")[-1])
