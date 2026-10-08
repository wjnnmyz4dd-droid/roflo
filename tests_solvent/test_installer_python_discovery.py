"""Finding the Python the owner already has.

These exist because of a reported failure: an owner with a working Python
3.13.12 on their VPS ran the installer and was told it would install Python.
The cause was not the version — `Python313` is the directory name for every
3.13.x patch release, and 3.13 clears the 3.11 floor. The cause was the
*search*. It looked in four fixed directories and nowhere else, and python.org
installs **per-user by default**, into
``%LOCALAPPDATA%\\Programs\\Programs\\Python``, with "Add python.exe to PATH"
**unticked by default**. A perfectly good interpreter was therefore invisible.

So the tests below are organised by *how* an interpreter can be findable, and
each asserts that route works on its own. One route at a time matters: a test
that supplies the registry and PATH together passes even if only one of them is
consulted, which is how the original defect survived a green suite.

The last class is the regression test proper — the owner's machine, as
reported, with nothing else helping.
"""

from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "windows" / "installer"))

from tests_solvent.installer_fake_host import FakeWindows  # noqa: E402

from deskpilot_installer import python_runtime as pr  # noqa: E402
from deskpilot_installer.report import Outcome  # noqa: E402

#: The python.org default location for a per-user install.
PER_USER = (r"C:\Users\Administrator\AppData\Local\Programs\Python"
            r"\Python313\python.exe")
ALL_USERS = r"C:\Program Files\Python313\python.exe"

#: A location no other discovery route covers: not under a user profile, not in
#: the all-users list. Used to isolate one route at a time — placing the
#: interpreter at a path two routes can reach makes a test pass even when the
#: route it names has been removed.
UNLISTED = r"D:\Tools\CPython313\python.exe"


def bare_windows() -> FakeWindows:
    """A Windows host with no Python anywhere and no helpful environment."""
    return FakeWindows(env={
        "USERPROFILE": r"C:\Users\Administrator",
        "LOCALAPPDATA": r"C:\Users\Administrator\AppData\Local",
        "APPDATA": r"C:\Users\Administrator\AppData\Roaming",
    })


def with_interpreter(host: FakeWindows, path: str, *,
                     version: str = "3 13 True") -> FakeWindows:
    """Put a real, working interpreter on disk and nothing else."""
    host.add_file(path, b"MZ")
    host.on(path, "sys.version_info", stdout=version)
    return host


class EachRouteWorksOnItsOwn(unittest.TestCase):
    """One discovery mechanism per test, with the others deliberately empty."""

    def test_the_registry_alone_is_enough(self):
        """PEP 514 is the authoritative record and must be consulted.

        The interpreter sits at a path no other route covers, so this passes
        only if the registry is genuinely read.
        """
        host = with_interpreter(bare_windows(), UNLISTED)
        host.registry_paths = [UNLISTED]
        chosen = pr.choose(pr.discover(host))
        self.assertIsNotNone(chosen)
        self.assertEqual(chosen.path, UNLISTED)

    def test_path_alone_is_enough(self):
        host = with_interpreter(bare_windows(), UNLISTED)
        host.path_programs["python.exe"] = UNLISTED
        chosen = pr.choose(pr.discover(host))
        self.assertIsNotNone(chosen)
        self.assertEqual(chosen.path, UNLISTED)

    def test_the_py_launcher_alone_is_enough(self):
        host = with_interpreter(bare_windows(), UNLISTED)
        host.add_file(r"C:\Windows\py.exe", b"MZ")
        host.on("py.exe", "-0p",
                stdout=f" -V:3.13 *        {UNLISTED}\n")
        chosen = pr.choose(pr.discover(host))
        self.assertIsNotNone(chosen)
        self.assertEqual(chosen.path, UNLISTED)

    def test_an_all_users_location_alone_is_enough(self):
        host = with_interpreter(bare_windows(), ALL_USERS)
        chosen = pr.choose(pr.discover(host))
        self.assertIsNotNone(chosen)
        self.assertEqual(chosen.path, ALL_USERS)

    def test_a_per_user_location_alone_is_enough(self):
        """No registry, no PATH, no launcher. Found by sweeping profiles.

        This is the route that did not exist and whose absence caused the
        reported failure.
        """
        host = with_interpreter(bare_windows(), PER_USER)
        chosen = pr.choose(pr.discover(host))
        self.assertIsNotNone(chosen,
                             "a per-user python.org install must be found")
        self.assertEqual(chosen.path, PER_USER)

    def test_another_users_profile_is_searched_too(self):
        """The installer is elevated, so the owner's profile may not be ours."""
        other = (r"C:\Users\jo\AppData\Local\Programs\Python"
                 r"\Python313\python.exe")
        host = with_interpreter(bare_windows(), other)
        chosen = pr.choose(pr.discover(host))
        self.assertIsNotNone(chosen)
        self.assertEqual(chosen.path, other)

    def test_every_supported_minor_version_is_searched(self):
        for minor, version in (("311", "3 11 True"), ("312", "3 12 True"),
                               ("313", "3 13 True"), ("314", "3 14 True")):
            path = (r"C:\Users\Administrator\AppData\Local\Programs\Python"
                    rf"\Python{minor}\python.exe")
            host = with_interpreter(bare_windows(), path, version=version)
            chosen = pr.choose(pr.discover(host))
            self.assertIsNotNone(chosen, f"Python{minor} was not found")

    def test_program_files_x86_is_searched(self):
        path = r"C:\Program Files (x86)\Python313\python.exe"
        host = with_interpreter(bare_windows(), path)
        self.assertIsNotNone(pr.choose(pr.discover(host)))


class ThePatchLevelIsIrrelevant(unittest.TestCase):
    """Every 3.13.x lives in ``Python313``; the floor is on major.minor."""

    def test_a_late_patch_release_is_accepted(self):
        for version in ("3 13 True", "3 11 True", "3 14 True"):
            host = with_interpreter(bare_windows(), PER_USER, version=version)
            host.registry_paths = [PER_USER]
            chosen = pr.choose(pr.discover(host))
            self.assertIsNotNone(chosen, f"{version} was rejected")
            self.assertEqual(chosen.rejected, "")

    def test_the_directory_name_carries_no_patch_level(self):
        for candidate in pr.machine_candidates():
            tail = candidate.lower().split("\\python")[-1]
            self.assertNotIn(".", tail.replace(".exe", ""),
                             f"{candidate} encodes a patch level")

    def test_only_too_old_versions_are_refused(self):
        host = with_interpreter(bare_windows(), PER_USER, version="3 10 True")
        host.registry_paths = [PER_USER]
        found = pr.discover(host)
        self.assertTrue(found)
        self.assertIn("3.11 or newer", found[0].rejected)
        self.assertIsNone(pr.choose(found))


class ReportingIsDiagnostic(unittest.TestCase):
    """When nothing is found the owner is told where it looked."""

    def test_a_found_interpreter_is_reported_with_its_path(self):
        host = with_interpreter(bare_windows(), PER_USER)
        host.registry_paths = [PER_USER]
        found = pr.discover(host)
        row = pr.row(found, pr.choose(found), host)
        self.assertEqual(row.outcome, Outcome.PASS)
        self.assertIn(PER_USER, row.detail)

    def test_nothing_found_names_the_number_of_locations_checked(self):
        host = bare_windows()
        row = pr.row([], None, host)
        self.assertEqual(row.outcome, Outcome.WILL_INSTALL)
        self.assertIn("checked", row.detail)
        self.assertIn("registry", row.detail)

    def test_the_search_report_covers_every_route(self):
        host = bare_windows()
        report = pr.search_report(host)
        self.assertEqual(set(report), {"registry (PEP 514)", "PATH",
                                       "py launcher", "all-users locations",
                                       "per-user locations"})
        self.assertTrue(report["all-users locations"])

    def test_an_unusable_interpreter_is_named_rather_than_ignored(self):
        host = with_interpreter(bare_windows(), PER_USER, version="3 9 True")
        host.registry_paths = [PER_USER]
        found = pr.discover(host)
        row = pr.row(found, None, host)
        self.assertIn("cannot be used", row.detail)
        self.assertIn(PER_USER, row.facts["rejected"][0]["path"])


class MetaTradersInterpreterIsStillRefused(unittest.TestCase):
    """Widening the search must not widen it into the trading terminal."""

    MT5_PYTHON = r"C:\Program Files\MetaTrader 5\Python\python.exe"

    def test_it_is_refused_even_when_the_registry_names_it(self):
        host = with_interpreter(bare_windows(), self.MT5_PYTHON)
        host.registry_paths = [self.MT5_PYTHON]
        found = pr.discover(host)
        self.assertTrue(found)
        self.assertFalse(found[0].usable)
        self.assertIn("MetaTrader", found[0].rejected)
        self.assertIsNone(pr.choose(found))

    def test_it_is_refused_when_it_is_the_one_on_path(self):
        host = with_interpreter(bare_windows(), self.MT5_PYTHON)
        host.path_programs["python.exe"] = self.MT5_PYTHON
        self.assertIsNone(pr.choose(pr.discover(host)))

    def test_it_is_refused_when_the_launcher_reports_it(self):
        host = with_interpreter(bare_windows(), self.MT5_PYTHON)
        host.add_file(r"C:\Windows\py.exe", b"MZ")
        host.on("py.exe", "-0p",
                stdout=f" -V:3.13 *        {self.MT5_PYTHON}\n")
        self.assertIsNone(pr.choose(pr.discover(host)))

    def test_it_is_never_even_executed(self):
        """Refused by path, before the interpreter is run."""
        host = with_interpreter(bare_windows(), self.MT5_PYTHON)
        host.registry_paths = [self.MT5_PYTHON]
        pr.discover(host)
        for argv in host.commands:
            self.assertNotIn("metatrader", argv[0].lower())

    def test_a_good_interpreter_is_still_preferred_over_metatraders(self):
        host = bare_windows()
        with_interpreter(host, self.MT5_PYTHON)
        with_interpreter(host, ALL_USERS)
        host.registry_paths = [self.MT5_PYTHON, ALL_USERS]
        chosen = pr.choose(pr.discover(host))
        self.assertIsNotNone(chosen)
        self.assertEqual(chosen.path, ALL_USERS)

    def test_the_mt4_and_mt5_path_fragments_are_refused(self):
        for path in (r"C:\MT5\Python\python.exe",
                     r"D:\Broker\mt4\python\python.exe",
                     r"C:\Users\Admin\AppData\Roaming\MetaQuotes\py\python.exe"):
            host = with_interpreter(bare_windows(), path)
            host.registry_paths = [path]
            self.assertIsNone(pr.choose(pr.discover(host)),
                              f"{path} must be refused")


class TheOwnersMachineAsReported(unittest.TestCase):
    """The regression test for the reported failure, end to end.

    Python 3.13.12 installed per-user by python.org's installer with its
    default options: not in ``Program Files``, not on this process's PATH. The
    old search found nothing here and offered to install a second copy.
    """

    def setUp(self):
        self.host = with_interpreter(bare_windows(), PER_USER)
        # python.org registers itself under PEP 514 even for a per-user
        # install. Both are asserted: with the registration, and without it.
        self.host.registry_paths = [PER_USER]

    def test_the_installer_finds_it(self):
        chosen = pr.choose(pr.discover(self.host))
        self.assertIsNotNone(chosen)
        self.assertEqual(chosen.path, PER_USER)
        self.assertEqual(chosen.version_text, "3.13")

    def test_the_owner_is_not_asked_to_install_python(self):
        found = pr.discover(self.host)
        row = pr.row(found, pr.choose(found), self.host)
        self.assertEqual(row.outcome, Outcome.PASS)
        self.assertNotIn("WILL INSTALL", row.detail)

    def test_no_python_installer_is_downloaded(self):
        from deskpilot_installer.engine import Engine, OwnerAnswers
        from deskpilot_installer.installlog import Log
        from deskpilot_installer.layout import Layout
        from deskpilot_installer.probe import Settings
        from deskpilot_installer.report import Secret
        engine = Engine(self.host, Settings(layout=Layout()),
                        OwnerAnswers(owner_identity="Jo",
                                     web_password=Secret("correct-horse-batt")),
                        Log(self.host))
        result = engine.step_python()
        self.assertTrue(result.skipped, "an existing Python must be reused")
        self.assertIn("reusing", result.detail)
        self.assertEqual(
            [a for a in self.host.commands if a[0].endswith("curl.exe")], [],
            "nothing should have been downloaded")

    def test_it_is_found_without_the_registry_entry_too(self):
        """Belt and braces: even an unregistered install is located."""
        self.host.registry_paths = []
        chosen = pr.choose(pr.discover(self.host))
        self.assertIsNotNone(chosen)
        self.assertEqual(chosen.path, PER_USER)


class TheWizardSearchesTheSamePlaces(unittest.TestCase):
    """The wizard decides whether Python is installed, so it must agree.

    The generated include is the mechanism that keeps them in step; these
    tests assert the mechanism is wired up and that the wizard consults the
    routes the engine does. Two hand-maintained lists is how the original
    defect arose: the engine could find a PATH interpreter and the wizard,
    which is what the owner's screen reflects, could not.
    """

    def setUp(self):
        self.nsi = (ROOT / "windows" / "installer"
                    / "DeskPilot-Setup.nsi").read_text()

    def test_the_wizard_includes_the_generated_search_list(self):
        self.assertIn("PYTHON_SEARCH_NSH", self.nsi)
        self.assertIn("DP_EACH_MACHINE_PATH", self.nsi)

    def test_the_wizard_reads_the_registry(self):
        self.assertIn("EnumRegKey", self.nsi)
        self.assertIn("InstallPath", self.nsi)
        self.assertIn("ExecutablePath", self.nsi)

    def test_the_wizard_reads_both_registry_hives(self):
        self.assertIn("HKCU", self.nsi)
        self.assertIn("HKLM", self.nsi)

    def test_the_wizard_reads_both_registry_views(self):
        """A 32-bit process reading the plain path misses 64-bit entries."""
        self.assertIn("SetRegView 64", self.nsi)
        self.assertIn("SetRegView 32", self.nsi)

    def test_the_wizard_searches_the_path(self):
        self.assertIn("SearchPath", self.nsi)

    def test_the_wizard_sweeps_user_profiles(self):
        self.assertIn("FindFirst", self.nsi)
        self.assertIn("DP_PYTHON_USER_SUFFIX", self.nsi)

    def test_the_wizard_refuses_metatraders_interpreter(self):
        lowered = self.nsi.lower()
        self.assertIn('"metatrader"', lowered)
        self.assertIn('"metaquotes"', lowered)

    def test_the_generator_emits_everything_the_engine_knows(self):
        """The generator is the mechanism; test it, not a build artifact.

        Generated fresh into a temporary directory rather than read from
        ``dist/``. A committed build output goes stale the moment the module
        changes — which it did, the first time this ran — and a test that
        fails because nobody has rebuilt is a test that gets deleted. What
        matters is that running the generator now produces every location and
        pin the engine currently holds; ``build.py`` regenerates on every
        build, so that is sufficient.
        """
        import tempfile

        sys.path.insert(0, str(ROOT / "windows" / "installer"))
        import build

        with tempfile.TemporaryDirectory() as box:
            build.write_python_search(pathlib.Path(box))
            text = (pathlib.Path(box) / "python-search.nsh").read_text()

        for candidate in pr.machine_candidates():
            self.assertIn(candidate, text,
                          f"{candidate} is missing from the wizard's list")
        for minor in pr.MINORS:
            self.assertIn(f'"{minor}"', text)
        self.assertIn(pr.BOOTSTRAP_VERSION, text)
        self.assertIn(pr.BOOTSTRAP_SHA256, text)
        self.assertIn(pr.BOOTSTRAP_URL, text)
        self.assertIn(pr._USER_SUFFIX, text)


if __name__ == "__main__":
    unittest.main()
