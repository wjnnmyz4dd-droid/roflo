"""DeskPilot is the product; Solvent is the engine. Both names are correct.

These tests pin the boundary in both directions. A branding pass that only
asserts the new name is half a test: the risk is not that the product name
fails to appear, it is that someone later "finishes the job" by renaming an
internal contract and breaks a running installation. So the retained names
are asserted as deliberately as the changed ones.
"""

from __future__ import annotations

import pathlib
import unittest

from solvent import branding
from solvent.branding import ENGINE, PRODUCT

ROOT = pathlib.Path(__file__).resolve().parent.parent


class TheTwoNames(unittest.TestCase):

    def test_the_product_name_is_spelled_exactly_one_way(self):
        self.assertEqual(PRODUCT, "DeskPilot")

    def test_the_engine_keeps_its_name(self):
        self.assertEqual(ENGINE, "Solvent")

    def test_they_are_not_the_same_name(self):
        self.assertNotEqual(PRODUCT.lower(), ENGINE.lower())

    def test_no_spaced_or_hyphenated_spelling_is_introduced(self):
        """"Desk Pilot" and "Desk-Pilot" are different products as far as a
        reader is concerned. Catch them anywhere in the package or tests."""
        bad = ("Desk Pilot", "Desk-Pilot", "DESK PILOT", "deskPilot", "Deskpilot")
        offenders = []
        for path in list((ROOT / "solvent").rglob("*.py")) + \
                    list((ROOT / "tests_solvent").rglob("*.py")):
            if path.name == "test_branding.py":
                continue
            text = path.read_text()
            for spelling in bad:
                if spelling in text:
                    offenders.append(f"{path.relative_to(ROOT)}: {spelling!r}")
        self.assertEqual(offenders, [])


class TheEngineNamesThatMustNotMove(unittest.TestCase):
    """Each of these is a contract with something outside the source: a disk
    path, a cookie a browser already holds, an environment variable in a
    deployed unit file, a key inside a payment provider's records. Renaming
    one is a migration, and a migration is not a branding decision."""

    def test_the_python_package_is_still_solvent(self):
        import solvent
        self.assertEqual(solvent.__name__, "solvent")

    def test_the_cli_program_name_is_still_solvent(self):
        from solvent.cli import build_parser
        self.assertEqual(build_parser().prog, "solvent")

    def test_the_session_cookie_name_is_unchanged(self):
        """A browser already holding this cookie must stay signed in across
        the rename."""
        source = (ROOT / "solvent" / "web" / "server.py").read_text()
        self.assertIn("solvent_session=", source)
        self.assertNotIn("deskpilot_session", source.lower())

    def test_the_owner_key_environment_variable_is_unchanged(self):
        from solvent import owner
        self.assertEqual(owner.KEY_ENV, "SOLVENT_OWNER_KEY")

    def test_the_runtime_paths_are_unchanged(self):
        from solvent import runtime as rt
        self.assertTrue(rt.DEFAULT_DB.startswith("/var/lib/solvent/"))

    def test_the_branding_module_warns_against_a_blanket_rename(self):
        """The module docstring is the thing a future agent reads before
        deciding how brave to be. If it stops saying this, the guard is gone."""
        self.assertIn("Do not run a repository-wide replacement",
                      branding.__doc__)


class TheOwnerFacingSurfaces(unittest.TestCase):

    def test_the_browser_title_and_header_carry_the_product_name(self):
        from solvent.web.render import page
        html = page("Jobs", "<p>x</p>", csrf="t").decode()
        self.assertIn(f"· {PRODUCT}</title>", html)
        self.assertIn(f'<span class="brand">{PRODUCT}</span>', html)
        self.assertNotIn(f"· {ENGINE}</title>", html)

    def test_an_owner_text_message_says_who_it_is_from(self):
        """§10. A text arrives on a lock screen with no context but its first
        word. Nothing asserted this before, so nothing would have noticed if
        the sender identity had been dropped entirely."""
        from solvent.notify import Notifier
        body = Notifier._body(None, event="Owner action required",
                              summary="A job is waiting.")
        self.assertTrue(body.startswith(f"{PRODUCT}: "), body)

    def test_a_text_message_still_carries_no_detail(self):
        """Branding must not become an excuse to widen what SMS says."""
        from solvent.notify import Notifier
        body = Notifier._body(None, event="Owner action required",
                              summary="A job is waiting.")
        self.assertIn("open the control centre", body)
        self.assertLessEqual(len(body), 280)


if __name__ == "__main__":
    unittest.main()


class EveryOwnerFacingRoute(unittest.TestCase):
    """§21. Walk every route in the navigation, on a populated system.

    A branding pass is exactly the kind of change that renders fine on the two
    pages someone happened to open and leaves a stale name, or a traceback, on
    the twenty they did not. So the sweep is driven from :data:`render.NAV`
    itself: a page added later is swept without anyone remembering to add it.
    """

    @classmethod
    def setUpClass(cls):
        import tempfile
        from solvent.harness import CSV_PROMOTION, OWNER, Solvent, provision_capability
        from solvent.web import auth as web_auth
        from solvent.web import server as web_server

        cls._dir = tempfile.TemporaryDirectory()
        db = str(pathlib.Path(cls._dir.name) / "solvent.db")
        s = Solvent(db)
        provision_capability(s, CSV_PROMOTION, owner_identity=OWNER)
        s.store.close()
        cls.centre = web_server.build(
            db, password_hash=web_auth.hash_password(cls.PASSWORD),
            spool=str(pathlib.Path(cls._dir.name) / "spool"))

    PASSWORD = "TEST-ONLY-control-centre-password"

    @classmethod
    def tearDownClass(cls):
        cls.centre.read.close()
        cls._dir.cleanup()

    def fetch(self, path):
        from solvent.web.app import Request
        login = self.centre.handle(Request(
            "POST", "/login", form={"password": self.PASSWORD}, source="10.0.0.1"))
        return self.centre.handle(Request(
            "GET", path, cookies={"solvent_session": login.set_session}))

    def test_every_route_renders(self):
        from solvent.web.render import NAV
        broken = []
        for path, label in NAV:
            try:
                response = self.fetch(path)
            except Exception as exc:                       # pragma: no cover
                broken.append(f"{path} ({label}) raised {exc!r}")
                continue
            if response.status != 200:
                broken.append(f"{path} ({label}) -> HTTP {response.status}")
        self.assertEqual(broken, [])

    def test_every_route_is_branded_as_the_product(self):
        from solvent.web.render import NAV
        unbranded = []
        for path, label in NAV:
            html = self.fetch(path).body.decode()
            if f'<span class="brand">{PRODUCT}</span>' not in html:
                unbranded.append(f"{path} has no {PRODUCT} header")
            if f"· {PRODUCT}</title>" not in html:
                unbranded.append(f"{path} has no {PRODUCT} browser title")
        self.assertEqual(unbranded, [])

    def test_no_route_shows_the_engine_name_except_where_it_is_meant_to(self):
        """The engine name on a working page is a stale reference. Exactly one
        page is exempt: Diagnostics, which discloses the engine on purpose
        (§7). The exemption list was deliberately narrowed to that single page
        after checking what each route actually renders — a wider list would
        have made this test agree with whatever the pages happened to say.

        Lowercase ``solvent`` is not matched, because the setup pages name the
        `solvent setup …` commands the owner really types."""
        from solvent.web.render import NAV
        deliberate = {"/diagnostics"}
        stale = []
        for path, label in NAV:
            if path in deliberate:
                continue
            html = self.fetch(path).body.decode()
            if ENGINE in html:
                where = html[max(0, html.find(ENGINE) - 70):html.find(ENGINE) + 70]
                stale.append(f"{path} ({label}): …{where}…")
        self.assertEqual(stale, [])

    def test_the_diagnostics_page_names_the_engine_on_purpose(self):
        html = self.fetch("/diagnostics").body.decode()
        self.assertIn(f"Engine: {ENGINE}", html)

    def test_the_dashboard_is_the_product_command_centre(self):
        self.assertIn(f"{PRODUCT} Command Centre", self.fetch("/").body.decode())

    def test_navigation_is_intact_on_every_route(self):
        from solvent.web.render import NAV
        missing = []
        for path, _ in NAV:
            html = self.fetch(path).body.decode()
            for link, label in NAV:
                if f'href="{link}"' not in html:
                    missing.append(f"{path} is missing the {label} link")
        self.assertEqual(missing, [])
