"""§59-§62. The new surfaces, attacked.

Everything a source returns is attacker-authored, and a job title is the part
of it that gets rendered on the most pages. So the attacks here are aimed at
the path from a hostile listing to the owner's browser, and at the idea that
hiding a button is a way of withholding a permission.
"""

from __future__ import annotations

import json
import pathlib
import tempfile
import unittest

from solvent import sourceaccess as access
from solvent.discovery import Compliance, FixtureSource, Readiness
from solvent.errors import FailClosed
from solvent.harness import CSV_PROMOTION, OWNER, Solvent, provision_capability
from solvent.web import auth as web_auth
from solvent.web import server as web_server
from solvent.web.app import Request

PASSWORD = "TEST-ONLY-control-centre-password"

XSS = '<script>alert("pwned")</script>'
SQLI = "'; DROP TABLE opportunities;--"
TRAVERSAL = "../../../../etc/passwd"


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.db = str(pathlib.Path(self.dir.name) / "solvent.db")
        self.s = Solvent(self.db)
        provision_capability(self.s, CSV_PROMOTION, owner_identity=OWNER)
        self.arrange(self.s)
        self.s.store.close()
        self.centre = web_server.build(
            self.db, password_hash=web_auth.hash_password(PASSWORD),
            spool=str(pathlib.Path(self.dir.name) / "spool"))
        self.addCleanup(self.centre.read.close)

    def arrange(self, solvent):
        pass

    def register(self, solvent, name, postings):
        source = FixtureSource(name, list(postings))
        solvent.discovery.register_source(
            source, owner_identity=OWNER,
            readiness=Readiness.PERMITTED_AUTOMATION,
            compliance=Compliance.PERMITTED, determination="fixture")
        approved = solvent.policy.get("discovery", "approved_sources", default=[])
        solvent.policy.amend(
            {"discovery": {"approved_sources": sorted({*approved, name})}},
            OWNER, "approve")
        solvent.discovery.poll(name)

    def page(self, path="/sources"):
        login = self.centre.handle(Request(
            "POST", "/login", form={"password": PASSWORD}, source="10.0.0.1"))
        return self.centre.handle(Request(
            "GET", path,
            cookies={"solvent_session": login.set_session})).body.decode()


class AHostileListingCannotReachTheBrowser(Base):
    def arrange(self, solvent):
        self.register(solvent, "board", [{
            "ref": "x1", "title": XSS, "quoted_cents": 1000,
            "body": f"Contact {XSS} for details", "client_ref": XSS}])

    def test_the_script_tag_is_escaped_on_the_sources_page(self):
        body = self.page()
        self.assertNotIn("<script>alert", body)
        self.assertIn("&lt;script&gt;", body)

    def test_it_is_escaped_everywhere_it_appears(self):
        for path in ("/sources", "/", "/jobs", "/clients"):
            with self.subTest(path=path):
                self.assertNotIn("<script>alert", self.page(path))

    def test_the_listing_is_still_shown(self):
        """Escaped, not suppressed. The owner needs to see what was posted."""
        self.assertIn("&lt;script&gt;", self.page())


class SqlInjectionInAListingDoesNothing(Base):
    def arrange(self, solvent):
        self.register(solvent, "board", [
            {"ref": SQLI, "title": SQLI, "quoted_cents": 1000, "body": SQLI}])

    def test_the_table_still_exists(self):
        self.assertEqual(len(self.centre.read.opportunities()), 1)

    def test_the_text_is_stored_literally(self):
        self.assertEqual(self.centre.read.opportunities()[0]["title"], SQLI)

    def test_the_page_renders(self):
        self.assertIn("Work sources", self.page())


class ASourceUrlMayNotReachInside(unittest.TestCase):
    """§60. The Action Gate's allowlist is the real control; this is the layer
    that stops such a destination ever being proposed for it."""

    HOSTILE = (
        "https://127.0.0.1/admin", "https://localhost/", "https://[::1]/",
        "https://169.254.169.254/latest/meta-data/",
        "https://metadata.google.internal/computeMetadata/v1/",
        "https://10.0.0.1/", "https://192.168.1.1/", "https://172.16.0.1/",
        "file:///etc/passwd", "ftp://example.com/", "gopher://example.com/",
        "http://example.com/", "https://user:pass@example.com/",
        "https://0.0.0.0/", "https://224.0.0.1/",
    )

    def test_every_hostile_destination_is_refused(self):
        for url in self.HOSTILE:
            with self.subTest(url=url):
                self.assertTrue(access.url_problem(url), f"{url} was allowed")
                with self.assertRaises(FailClosed):
                    access.safe_url(url)

    def test_a_legitimate_destination_is_allowed(self):
        for url in ("https://example.com/jobs",
                    "https://api.example.com/v2/search?q=csv",
                    "https://sub.domain.example.co.uk/feed"):
            with self.subTest(url=url):
                self.assertEqual(access.url_problem(url), "")

    def test_the_refusal_says_which_problem(self):
        self.assertIn("loopback", access.url_problem("https://127.0.0.1/"))
        self.assertIn("metadata", access.url_problem("https://169.254.169.254/"))
        self.assertIn("private", access.url_problem("https://10.0.0.1/"))
        self.assertIn("scheme", access.url_problem("http://example.com/"))


class PermissionsAreEnforcedInTheBackend(Base):
    """§62. Not showing a button is not withholding a permission."""

    def arrange(self, solvent):
        self.register(solvent, "board", [
            {"ref": "a", "title": "Work", "quoted_cents": 1000, "body": "x"}])

    def test_the_website_cannot_grant_a_permission(self):
        """The control centre holds no authorities at all, so there is no
        endpoint to attack. Asserted structurally: if one is ever added, the
        grant path has to appear in this module."""
        source = pathlib.Path("solvent/web/app.py").read_text()
        self.assertNotIn("discovery.grant", source)
        self.assertNotIn("discovery.record_health", source)
        self.assertNotIn("set_credential_configured", source)

    def test_the_read_model_is_read_only(self):
        with self.assertRaises(Exception):
            self.centre.read._conn.execute(
                'UPDATE work_sources SET permissions = \'["SPEND_MONEY"]\'')

    def test_permissions_shown_are_the_permissions_stored(self):
        row = [r for r in self.centre.read.work_sources()
               if r["name"] == "board"][0]
        self.assertEqual(json.loads(row["permissions"]),
                         list(access.DEFAULT_PERMISSIONS))

    def test_no_consequential_permission_is_visible_as_granted(self):
        body = self.page()
        self.assertIn("none", body.lower())
        for permission in access.CONSEQUENTIAL:
            self.assertNotIn(permission.replace("_", " ").lower()
                             + "</div>", body)


class CredentialsAreNeverShown(Base):
    def arrange(self, solvent):
        self.register(solvent, "board", [
            {"ref": "a", "title": "Work", "quoted_cents": 1000, "body": "x"}])
        solvent.discovery.set_credential_configured(
            "board", True, owner_identity=OWNER)

    def test_the_page_says_configured_without_saying_what(self):
        body = self.page()
        self.assertIn("configured", body)

    def test_the_registry_stores_only_a_boolean(self):
        row = [r for r in self.centre.read.work_sources()
               if r["name"] == "board"][0]
        self.assertEqual(row["credential_configured"], 1)
        # Nothing resembling a secret is anywhere in the row.
        for value in row.values():
            text = str(value).lower()
            for marker in ("secret", "token", "api_key", "password"):
                self.assertNotIn(marker, text)


class TheCatalogueCannotBeMadeToClaimAutomation(unittest.TestCase):
    def test_the_page_does_not_call_a_listed_platform_connected(self):
        with tempfile.TemporaryDirectory() as d:
            db = str(pathlib.Path(d) / "solvent.db")
            Solvent(db).store.close()
            centre = web_server.build(
                db, password_hash=web_auth.hash_password(PASSWORD),
                spool=str(pathlib.Path(d) / "spool"))
            self.addCleanup(centre.read.close)
            login = centre.handle(Request("POST", "/login",
                                          form={"password": PASSWORD},
                                          source="10.0.0.1"))
            body = centre.handle(Request(
                "GET", "/sources",
                cookies={"solvent_session": login.set_session})).body.decode()
            self.assertIn("not been established", body)
            self.assertIn("not established", body)
            self.assertIn("Upwork", body)


if __name__ == "__main__":
    unittest.main()
