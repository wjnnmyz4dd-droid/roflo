"""The packaged engine, executed the way the wizard executes it.

Every other installer test calls Python functions. These start a real
subprocess, against a real on-disk layout, with the real argument form the NSIS
script issues — because that is the only thing that would have caught the
failure this file exists for.

The wizard used to run ``python -I deskpilot_installer\\cli.py``. That cannot
work and never did: ``cli.py`` is a module inside a package and uses relative
imports, so run as a script it has no parent package and raises ``ImportError:
attempted relative import with no known parent package`` on its first import.
Every unit test passed throughout, because they imported the module instead of
executing the file. The install phase had therefore never run on any machine;
the owner's earlier Python-detection failure simply stopped them before they
reached it.

So the tests here assert three things a function call cannot:

* The **command string in the NSIS script** names a file that the **extraction
  layout in the same script** actually produces.
* That file, executed with the exact isolated flags, **imports every module**
  the installation needs.
* A **damaged** engine produces a readable diagnostic and a non-zero exit
  rather than a traceback — which is what lets the wizard stop before touching
  the machine.

The second class goes further and runs the real ``solvent`` CLI against a real
SQLite database in a real virtual environment, with no fake host anywhere. That
is what found three more defects of the same family: ``doctor`` refusing to
create a database, the capability authority refusing a free-text owner name,
and ``Solvent`` imported from a module it does not live in.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
INSTALLER = ROOT / "windows" / "installer"
PACKAGE = INSTALLER / "deskpilot_installer"
LAUNCHER = INSTALLER / "deskpilot-engine.py"
NSI = INSTALLER / "DeskPilot-Setup.nsi"

sys.path.insert(0, str(INSTALLER))

#: This suite must run in its own interpreter, and says so rather than
#: quietly degrading.
#:
#: Every test here starts a real subprocess, which is the entire point: the
#: defect it exists for was invisible to any test that imported a module
#: instead of executing a file. But DeskPilot's egress guard denies
#: ``subprocess.Popen`` outside an Action Gate window, and an audit hook cannot
#: be removed once added -- so in a combined run, whichever test installs the
#: guard first makes every subprocess here impossible.
#:
#: The guard is not worked around. ``egress.window()`` is documented as
#: Action-Gate-only and a test calling it would be impersonating the Action
#: Gate; choosing a spawn path the hook does not cover would be exactly the
#: bypass ``test_egress_bypass.py`` exists to forbid. The repository's own
#: precedent for this situation is to reproduce the case separately against
#: the same code path, which is what the command below does.
RUN_SEPARATELY = "python3 -m unittest tests_solvent.test_installer_packaged_entrypoint"


def setUpModule() -> None:
    """Skip, with the reason and the command, when the guard is already up."""
    try:
        from solvent import egress
    except ImportError:
        return
    if egress.is_installed():
        raise unittest.SkipTest(
            "DeskPilot's egress guard is installed in this interpreter, so no "
            "subprocess can be started and these tests cannot execute the "
            "packaged entry point. The guard is deliberately irreversible and "
            "is not worked around. Run this suite on its own:\n"
            f"    {RUN_SEPARATELY}")


def stage_engine(into: pathlib.Path) -> pathlib.Path:
    """Lay the engine out exactly as the wizard's extraction does.

    ``File /r <dir>`` puts the directory itself under the output path, and the
    launcher is placed beside it. Reproduced here rather than assumed, so the
    layout under test is the layout that ships.
    """
    into.mkdir(parents=True, exist_ok=True)
    shutil.copytree(PACKAGE, into / "deskpilot_installer")
    shutil.copy2(LAUNCHER, into / "deskpilot-engine.py")
    return into / "deskpilot-engine.py"


def run_engine(launcher: pathlib.Path, *args: str,
               cwd: str | None = None) -> subprocess.CompletedProcess:
    """Run the launcher with the wizard's exact flags, from an unrelated cwd.

    ``-I`` and ``cwd`` outside the engine tree together are the conditions the
    original defect needed: isolated mode puts neither the script's directory
    nor the current directory on ``sys.path``.
    """
    return subprocess.run(
        [sys.executable, "-I", str(launcher), *args],
        capture_output=True, text=True, timeout=300,
        cwd=cwd or str(pathlib.Path(tempfile.gettempdir())))


class TheWizardCommandMatchesTheWizardLayout(unittest.TestCase):
    """The script's own two halves have to agree about where the engine is."""

    def setUp(self):
        self.nsi = NSI.read_text()

    def test_the_launcher_is_shipped_beside_the_package(self):
        self.assertTrue(LAUNCHER.is_file(),
                        f"{LAUNCHER} is missing: the wizard runs this file")
        self.assertIn("ENGINE_LAUNCHER", self.nsi)

    def test_the_launcher_is_not_inside_the_package(self):
        """Inside the package it would be a module, and unrunnable as a script."""
        self.assertFalse((PACKAGE / "deskpilot-engine.py").exists())

    def test_the_launcher_has_no_relative_imports(self):
        """The defect in one line: a script cannot do relative imports."""
        import ast
        tree = ast.parse(LAUNCHER.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                self.assertEqual(node.level, 0,
                                 "the launcher must not use relative imports")

    def test_every_engine_invocation_runs_the_launcher(self):
        """No command may run a module inside the package as a script."""
        calls = [line for line in self.nsi.splitlines()
                 if "nsExec" in line and "$PythonExe" in line]
        self.assertTrue(calls, "the wizard must invoke the engine")
        for call in calls:
            self.assertIn("deskpilot-engine.py", call,
                          f"this invocation does not use the launcher: {call}")
            self.assertNotIn("deskpilot_installer\\cli.py", call)

    def test_the_install_invocation_targets_the_extracted_launcher(self):
        """The command's path and the extraction's path must be the same place."""
        self.assertIn('File "${ENGINE_LAUNCHER}"', self.nsi)
        self.assertIn('$EngineDir\\deskpilot-engine.py', self.nsi)
        self.assertIn('StrCpy $EngineDir "$PLUGINSDIR\\engine"', self.nsi)

    def test_the_uninstaller_targets_an_installed_path(self):
        """It used to target $INSTDIR\\App, where the engine was never put."""
        self.assertIn('$INSTDIR\\Engine\\deskpilot-engine.py', self.nsi)
        self.assertNotIn('$INSTDIR\\App\\deskpilot_installer', self.nsi)

    def test_the_engine_is_installed_not_only_staged(self):
        """$PLUGINSDIR is deleted when the installer exits."""
        self.assertIn('SetOutPath "$INSTDIR\\Engine"', self.nsi)

    def test_the_self_check_gates_the_installation(self):
        self.assertIn("selfcheck", self.nsi)
        order = self.nsi.index("selfcheck")
        install = self.nsi.index('deskpilot-engine.py" install')
        self.assertLess(order, install,
                        "the self-check must run before the install phase")

    def test_the_self_check_failure_aborts_before_changing_anything(self):
        window = self.nsi[self.nsi.index("selfcheck"):]
        window = window[:window.index("install --package")]
        self.assertIn("Abort", window)
        self.assertIn("BEFORE ANY CHANGE", window.upper())


class TheRealLauncherRuns(unittest.TestCase):
    """Subprocess execution, with the wizard's flags, from an unrelated cwd."""

    @classmethod
    def setUpClass(cls):
        cls.box = tempfile.TemporaryDirectory()
        cls.launcher = stage_engine(pathlib.Path(cls.box.name) / "engine")

    @classmethod
    def tearDownClass(cls):
        cls.box.cleanup()

    def test_it_starts_at_all(self):
        """The regression test for the reported failure."""
        done = run_engine(self.launcher, "--help")
        self.assertEqual(done.returncode, 0,
                         f"the packaged launcher did not start:\n{done.stderr}")
        self.assertIn("DeskPilot", done.stdout)

    def test_it_does_not_raise_the_relative_import_error(self):
        done = run_engine(self.launcher, "--help")
        self.assertNotIn("attempted relative import", done.stderr)
        self.assertNotIn("Traceback", done.stderr)

    def test_every_phase_is_reachable(self):
        done = run_engine(self.launcher, "--help")
        for phase in ("selfcheck", "check", "install", "verify", "repair",
                      "upgrade", "uninstall"):
            self.assertIn(phase, done.stdout)

    def test_the_self_check_imports_every_required_module(self):
        """Imports are proven by importing, in the packaged copy, isolated."""
        done = run_engine(self.launcher, "selfcheck", "--root", "C:/DeskPilot",
                          "--package", os.devnull, "--expect-sha256", "ab" * 32)
        self.assertIn("Installer modules", done.stdout)
        self.assertIn("required modules import", done.stdout,
                      f"module import check did not pass:\n{done.stdout}")

    def test_the_self_check_reports_the_engine_started(self):
        done = run_engine(self.launcher, "selfcheck", "--root", "C:/DeskPilot",
                          "--package", os.devnull, "--expect-sha256", "ab" * 32)
        self.assertIn("Installation engine", done.stdout)
        self.assertIn("started on Python", done.stdout)

    def test_the_self_check_runs_in_isolated_mode(self):
        """Isolation is the property -I was chosen for; prove it is in effect."""
        out = pathlib.Path(self.box.name) / "selfcheck.json"
        run_engine(self.launcher, "selfcheck", "--root", "C:/DeskPilot",
                   "--package", os.devnull, "--expect-sha256", "ab" * 32,
                   "--out", str(out))
        report = json.loads(out.read_text())
        engine_row = next(r for r in report["rows"]
                          if r["name"] == "Installation engine")
        self.assertTrue(engine_row["facts"]["isolated"],
                        "the engine must run with PYTHONPATH, the current "
                        "directory and user site-packages excluded")

    def test_a_bad_payload_makes_the_self_check_fail_closed(self):
        done = run_engine(self.launcher, "selfcheck", "--root", "C:/DeskPilot",
                          "--package", os.devnull, "--expect-sha256", "ab" * 32)
        self.assertEqual(done.returncode, 1)
        self.assertIn("VERIFICATION FAILED", done.stdout)

    def test_the_self_check_writes_a_machine_readable_report(self):
        out = pathlib.Path(self.box.name) / "report.json"
        run_engine(self.launcher, "selfcheck", "--root", "C:/DeskPilot",
                   "--package", os.devnull, "--expect-sha256", "ab" * 32,
                   "--out", str(out))
        report = json.loads(out.read_text())
        self.assertEqual(report["phase"], "selfcheck")
        self.assertFalse(report["may_continue"])
        names = {row["name"] for row in report["rows"]}
        for required in ("Installation engine", "Installer modules", "Python",
                         "DeskPilot package", "Installation paths",
                         "Existing DeskPilot data", "MetaTrader protection"):
            self.assertIn(required, names)

    def test_the_owner_gets_a_diagnostic_not_a_traceback(self):
        done = run_engine(self.launcher, "selfcheck", "--root", "C:/DeskPilot",
                          "--package", os.devnull, "--expect-sha256", "ab" * 32)
        self.assertIn("What was NOT changed", done.stdout)
        self.assertIn("Safe next action", done.stdout)
        self.assertNotIn("Traceback (most recent call last)", done.stdout)


class ADamagedEngineStopsSafely(unittest.TestCase):
    """A broken package must not produce a traceback or a zero exit."""

    def setUp(self):
        self.box = tempfile.TemporaryDirectory()
        self.launcher = stage_engine(pathlib.Path(self.box.name) / "engine")

    def tearDown(self):
        self.box.cleanup()

    def test_a_syntax_error_in_a_module_is_reported_readably(self):
        target = pathlib.Path(self.box.name) / "engine" / "deskpilot_installer"
        (target / "payload.py").write_text("this is not python ((\n")
        done = run_engine(self.launcher, "selfcheck")
        self.assertEqual(done.returncode, 3)
        self.assertIn("DESKPILOT ENGINE COULD NOT START", done.stderr)
        self.assertIn("nothing", done.stderr.lower())
        self.assertIn("Safe next action", done.stderr)

    def test_a_missing_module_is_reported_readably(self):
        target = pathlib.Path(self.box.name) / "engine" / "deskpilot_installer"
        (target / "engine.py").unlink()
        done = run_engine(self.launcher, "selfcheck")
        self.assertEqual(done.returncode, 3)
        self.assertIn("DESKPILOT ENGINE COULD NOT START", done.stderr)

    def test_a_missing_package_is_reported_readably(self):
        shutil.rmtree(pathlib.Path(self.box.name) / "engine"
                      / "deskpilot_installer")
        done = run_engine(self.launcher, "selfcheck")
        self.assertEqual(done.returncode, 3)
        self.assertIn("ModuleNotFoundError", done.stderr)
        self.assertIn("installation has not begun", done.stderr)

    def test_a_damaged_engine_never_reports_success(self):
        target = pathlib.Path(self.box.name) / "engine" / "deskpilot_installer"
        (target / "probe.py").write_text("raise RuntimeError('boom')\n")
        done = run_engine(self.launcher, "selfcheck")
        self.assertNotEqual(done.returncode, 0)


class TheRealApplicationCommandsWork(unittest.TestCase):
    """No fake host. A real venv, a real database, the real ``solvent`` CLI.

    This is the class that found the remaining defects. A test double can only
    answer the way its author expected; these commands answer the way they
    actually behave, and three of them behaved differently from the
    installer's assumption.
    """

    @classmethod
    def setUpClass(cls):
        cls.box = tempfile.TemporaryDirectory()
        base = pathlib.Path(cls.box.name)
        cls.app = base / "App"
        cls.app.mkdir()
        for part in ("solvent", "roflo"):
            source = ROOT / part
            if source.is_dir():
                shutil.copytree(source, cls.app / part,
                                ignore=shutil.ignore_patterns("__pycache__"))
        venv_dir = base / "venv"
        subprocess.run([sys.executable, "-m", "venv", str(venv_dir)],
                       check=True, capture_output=True, timeout=600)
        cls.python = venv_dir / "bin" / "python"
        if not cls.python.exists():
            cls.python = venv_dir / "Scripts" / "python.exe"
        site = subprocess.run(
            [str(cls.python), "-c",
             "import site;print(site.getsitepackages()[0])"],
            capture_output=True, text=True, check=True, timeout=120)
        (pathlib.Path(site.stdout.strip())
         / "deskpilot-app.pth").write_text(str(cls.app) + "\n")
        cls.db = base / "solvent.db"
        cls.env = dict(os.environ)
        cls.env["SOLVENT_OWNER_KEY"] = "a" * 16 + "b1c2d3e4f5" * 5

    @classmethod
    def tearDownClass(cls):
        cls.box.cleanup()

    def solvent(self, *args: str) -> subprocess.CompletedProcess:
        """Exactly the form the engine uses: isolated, module, real argv."""
        return subprocess.run(
            [str(self.python), "-I", "-m", "solvent.cli", *args],
            capture_output=True, text=True, timeout=600, env=self.env,
            cwd=str(self.app))

    def test_01_the_application_is_importable_through_the_pth(self):
        """No PYTHONPATH, no cwd on sys.path: only the .pth can do this."""
        done = subprocess.run(
            [str(self.python), "-I", "-c",
             "import solvent.cli; print('ok')"],
            capture_output=True, text=True, timeout=120, cwd="/")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("ok", done.stdout)

    def test_02_doctor_refuses_to_create_a_database(self):
        """Pinned because the installer used to rely on the opposite.

        If this ever starts passing, `doctor` has changed its behaviour and the
        provisioning step should be reconsidered -- but it must never be
        *assumed* again.
        """
        missing = pathlib.Path(self.box.name) / "absent.db"
        done = self.solvent("doctor", "--db", str(missing))
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("no database", (done.stdout + done.stderr).lower())
        self.assertFalse(missing.exists(),
                         "doctor must not create a database to report on")

    def test_03_setup_check_provisions_the_database(self):
        """It creates the database and exits non-zero. Both matter.

        The exit code reports whether the owner's configuration is complete,
        which on a fresh installation it is not. An installer that read that
        as failure would fail every install; one that read it as success would
        miss a genuinely absent database. The file is the criterion.
        """
        done = self.solvent("setup", "check", "--db", str(self.db))
        self.assertTrue(self.db.exists(), "setup check must create the database")
        self.assertGreater(self.db.stat().st_size, 0)
        self.assertNotEqual(
            done.returncode, 0,
            "if this starts exiting zero, the provisioning step's success "
            "criterion should be revisited -- but never assumed")

    def test_04_an_unregistered_owner_name_is_refused(self):
        """A business name typed into a wizard is not a governance identity."""
        done = self.solvent("setup", "capability", "--db", str(self.db),
                            "--owner", "Jo Owner")
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("registered owner", done.stdout + done.stderr)

    def test_05_the_capability_registers_with_solvents_own_owner(self):
        """No --owner: the installer must not invent a governance identity."""
        done = self.solvent("setup", "capability", "--db", str(self.db))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("proven", done.stdout)

    def test_06_the_capability_reads_back_as_deployable(self):
        """The exact script the engine runs, with the import it now uses."""
        script = (
            "import json,sys\n"
            "from solvent.harness import Solvent\n"
            "s = Solvent(sys.argv[1])\n"
            "out = []\n"
            "for c in s.capability.capabilities():\n"
            "    ok, why = s.capability.may_deploy(c.name)\n"
            "    out.append({'name': c.name, 'proven': bool(c.proven),\n"
            "                'may_deploy': bool(ok), 'why': str(why)})\n"
            "print(json.dumps(out))\n"
        )
        done = subprocess.run(
            [str(self.python), "-I", "-c", script, str(self.db)],
            capture_output=True, text=True, timeout=300, cwd=str(self.app))
        self.assertEqual(done.returncode, 0, done.stderr)
        caps = json.loads(done.stdout.strip())
        self.assertTrue(caps, "no capabilities were reported")
        deployable = [c for c in caps if c["may_deploy"]]
        self.assertTrue(deployable,
                        f"nothing may be delivered: {caps}")

    def test_07_solvent_is_not_importable_from_services(self):
        """The wrong import, pinned so it cannot be reintroduced quietly."""
        done = subprocess.run(
            [str(self.python), "-I", "-c",
             "from solvent.services import Solvent"],
            capture_output=True, text=True, timeout=120, cwd=str(self.app))
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("cannot import name 'Solvent'", done.stderr)

    def test_08_the_engines_capability_script_uses_the_right_module(self):
        source = (PACKAGE / "engine.py").read_text()
        self.assertIn("from solvent.harness import Solvent", source)
        self.assertNotIn("from solvent.services import Solvent", source)

    def test_09_contracting_accepts_the_owners_typed_name(self):
        done = self.solvent("setup", "contracting", "--db", str(self.db),
                            "--structure", "INDIVIDUAL",
                            "--legal-name", "Jo Owner",
                            "--email", "jo@example.com")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_10_doctor_works_once_the_database_exists(self):
        self.solvent("setup", "check", "--db", str(self.db))
        done = self.solvent("doctor", "--db", str(self.db))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_11_health_and_readiness_run_against_the_real_database(self):
        self.solvent("setup", "check", "--db", str(self.db))
        for command in ("health", "readiness"):
            done = self.solvent(command, "--db", str(self.db))
            self.assertEqual(done.returncode, 0,
                             f"{command}: {done.stdout}{done.stderr}")
            self.assertTrue(done.stdout.strip())

    def test_12_the_password_hash_matches_solvents_stored_form(self):
        """The engine's exact invocation, password on stdin."""
        script = (
            "import sys\n"
            "from solvent.web.auth import hash_password\n"
            "sys.stdout.write(hash_password(sys.stdin.read().rstrip('\\n')))\n"
        )
        done = subprocess.run(
            [str(self.python), "-I", "-c", script],
            input="correct-horse-battery", capture_output=True, text=True,
            timeout=300, cwd=str(self.app))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue(done.stdout.startswith("pbkdf2$"), done.stdout[:60])

    def test_13_the_engines_step_order_matches_what_the_cli_requires(self):
        """Provision before anything that needs a database."""
        source = (PACKAGE / "engine.py").read_text()
        order = [m for m in re.findall(r'\("([^"]+)", self\.step_\w+\)', source)]
        self.assertIn("Creating persistent database", order)
        database = order.index("Creating persistent database")
        for later in ("Recording owner details",
                      "Registering certified capabilities",
                      "Configuring AI", "Configuring web control centre"):
            self.assertGreater(order.index(later), database,
                               f"{later} runs before the database exists")

    def test_14_no_step_passes_a_typed_name_as_a_governance_identity(self):
        source = (PACKAGE / "engine.py").read_text()
        flattened = " ".join(source.split())
        self.assertNotIn('"--owner", self.answers.owner_identity', flattened)


if __name__ == "__main__":
    unittest.main()
