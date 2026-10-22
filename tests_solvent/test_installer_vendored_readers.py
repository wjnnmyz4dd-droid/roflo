"""The independent (T2) readers, vendored instead of pip-installed.

This closes the last open dependency question: on a fresh Windows
machine DeskPilot could not produce a second opinion on a client's
file, because openpyxl, python-docx, CleverCSV and markdown-it-py
were not there and the installer has no pip step by design.

The answer is to carry them. These tests hold the carrying to the
same standard as the payload it travels in: the bytes are hashed
against a manifest before anything is written, a hostile archive
entry is refused by name, and extraction is all-or-nothing.

**Three of the eleven packages are exercised for real.** The wheels
are Windows wheels, so the four compiled ones (lxml, regex, chardet,
CleverCSV) cannot be imported on the machine running this suite. The
pure-Python ones can, and :class:`TheyActuallyImport` unpacks them
exactly as the installer does and imports them off the result. A test
that only checked file counts would pass just as happily on an
extraction that wrote the files in the wrong shape.
"""

from __future__ import annotations

import hashlib
import io
import ntpath
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
INSTALLER = ROOT / "windows" / "installer"
MANIFEST = INSTALLER / "t2-windows-x64.txt"
sys.path.insert(0, str(INSTALLER))

from deskpilot_installer import vendor                       # noqa: E402
from deskpilot_installer.vendor import ReaderRefused          # noqa: E402
from tests_solvent.installer_fake_host import FakeWindows    # noqa: E402

#: What `solvent.independent` actually imports. Everything else in the
#: manifest is there because one of these four needs it.
READERS = ("openpyxl", "python-docx", "clevercsv", "markdown-it-py")

#: Which of them are pure Python, and so testable off a real
#: extraction on any operating system.
PORTABLE = ("openpyxl", "markdown_it", "et_xmlfile", "mdurl")


def manifest_text() -> str:
    return MANIFEST.read_text(encoding="utf-8")


def wheel_bytes(entries: dict) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, body in entries.items():
            archive.writestr(name, body)
    return buffer.getvalue()


class TheManifestIsTheAuthority(unittest.TestCase):
    """What may be unpacked is decided here and nowhere else."""

    def test_it_exists_and_pins_every_reader(self):
        text = manifest_text()
        for package in READERS:
            with self.subTest(package):
                self.assertRegex(text, rf"(?m)^{re.escape(package)}==")

    def test_every_pin_is_exact(self):
        """No ranges. A second opinion that changes under you is not one."""
        for line in manifest_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("--hash"):
                continue
            with self.subTest(line):
                self.assertRegex(line, r"^[A-Za-z0-9._-]+==[^\s]+ \\$")

    def test_every_pin_carries_at_least_one_hash(self):
        pins = re.findall(r"(?m)^([A-Za-z0-9._-]+)==", manifest_text())
        blocks = re.split(r"(?m)^[A-Za-z0-9._-]+==", manifest_text())[1:]
        self.assertEqual(len(pins), len(blocks))
        for package, block in zip(pins, blocks):
            with self.subTest(package):
                self.assertTrue(re.search(r"--hash=sha256:[0-9a-f]{64}",
                                          block),
                                f"{package} is pinned with no hash")

    def test_the_hashes_are_well_formed_and_distinct(self):
        hashes = vendor.allowed_digests(manifest_text())
        self.assertGreaterEqual(len(hashes), 16)
        for digest in hashes:
            self.assertRegex(digest, r"^[0-9a-f]{64}$")

    def test_every_licence_is_permissive_and_named(self):
        """Phase 5 asks for licences to be verified, so they are listed.

        chardet is the one worth naming: it was LGPL-2.1 until 6.0.0
        and is 0BSD from there on. That relicensing is what dissolved
        the blocker this file closes, so a future bump that moved it
        back would matter, and a comment nobody checks would not
        catch it.
        """
        text = manifest_text()
        permissive = ("MIT", "0BSD", "BSD-3-Clause", "Apache-2.0",
                      "PSF-2.0", "BSD-2-Clause", "CNRI-Python")
        named = re.findall(r"#\s+\S+\.whl\s+\[([^\]]+)\]", text)
        self.assertGreaterEqual(len(named), 16)
        for licence in named:
            with self.subTest(licence):
                self.assertTrue(
                    any(token in licence for token in permissive),
                    f"{licence!r} is not on the permissive list; a "
                    "copyleft dependency is an owner decision, not a "
                    "packaging one")
        self.assertIn("0BSD", named, "chardet's licence is load-bearing")

    def test_it_ships_the_timezone_database(self):
        """Not a reader, and the one Windows failure nobody predicted.

        Windows ships no IANA timezone database, so
        ``ZoneInfo("America/New_York")`` raises on every Windows host
        and ``solvent.briefing`` falls back to UTC -- correctly and
        visibly, saying so in the summary, and still meaning the
        owner's 06:50 briefing arrives at some other hour. zoneinfo
        reads this package automatically when the system has none,
        so carrying it is the whole repair.
        """
        self.assertRegex(manifest_text(), r"(?m)^tzdata==")

    def test_nothing_in_it_is_a_source_distribution(self):
        """A sdist would mean compiling on the owner's machine."""
        self.assertNotIn(".tar.gz", manifest_text())


class ChoosingTheRightWheels(unittest.TestCase):
    """A cp311 extension on cp313 fails at import, far from here."""

    NAMES = [
        "openpyxl-3.1.5-py2.py3-none-any.whl",
        "lxml-6.1.3-cp311-cp311-win_amd64.whl",
        "lxml-6.1.3-cp313-cp313-win_amd64.whl",
        "regex-2026.9.29-cp311-cp311-win_amd64.whl",
    ]

    def test_a_pure_python_wheel_serves_every_interpreter(self):
        for tag in ("cp311", "cp312", "cp313"):
            with self.subTest(tag):
                self.assertIn("openpyxl-3.1.5-py2.py3-none-any.whl",
                              vendor.wheels_for(self.NAMES, python_tag=tag))

    def test_a_compiled_wheel_serves_only_its_own(self):
        chosen = vendor.wheels_for(self.NAMES, python_tag="cp311")
        self.assertIn("lxml-6.1.3-cp311-cp311-win_amd64.whl", chosen)
        self.assertNotIn("lxml-6.1.3-cp313-cp313-win_amd64.whl", chosen)

    def test_an_interpreter_with_no_wheels_gets_none_rather_than_the_wrong_ones(self):
        self.assertEqual(
            vendor.wheels_for(self.NAMES, python_tag="cp312"),
            ["openpyxl-3.1.5-py2.py3-none-any.whl"])

    def test_something_that_is_not_a_wheel_is_ignored(self):
        self.assertEqual(
            vendor.wheels_for(["notes.txt", "setup.exe"], python_tag="cp311"),
            [])


class NothingUnverifiedIsUnpacked(unittest.TestCase):
    """The check that makes carrying them safe."""

    def setUp(self):
        self.host = FakeWindows()
        self.where = r"C:\\DeskPilot\\app\\vendor\\wheels"
        self.host.makedirs(self.where)

    def add(self, name: str, body: bytes) -> str:
        self.host.write_bytes(ntpath.join(self.where, name), body)
        return hashlib.sha256(body).hexdigest()

    def test_a_wheel_the_manifest_vouches_for_passes(self):
        digest = self.add("openpyxl-3.1.5-py2.py3-none-any.whl", b"pretend")
        wheels = vendor.verify(self.host, self.where,
                               ["openpyxl-3.1.5-py2.py3-none-any.whl"],
                               {digest})
        self.assertEqual([w.package for w in wheels], ["openpyxl"])
        self.assertTrue(wheels[0].universal)

    def test_one_byte_different_is_refused(self):
        digest = self.add("openpyxl-3.1.5-py2.py3-none-any.whl", b"pretend")
        self.add("openpyxl-3.1.5-py2.py3-none-any.whl", b"pretene")
        with self.assertRaises(ReaderRefused) as refused:
            vendor.verify(self.host, self.where,
                          ["openpyxl-3.1.5-py2.py3-none-any.whl"], {digest})
        self.assertIn("does not list", refused.exception.failure.why)
        self.assertEqual(refused.exception.failure.changed, ())

    def test_the_refusal_says_what_to_do(self):
        with self.assertRaises(ReaderRefused) as refused:
            self.add("x-1.0-py3-none-any.whl", b"junk")
            vendor.verify(self.host, self.where,
                          ["x-1.0-py3-none-any.whl"], {"0" * 64})
        self.assertIn("Reinstall", refused.exception.failure.safe_next_action)
        self.assertIn("the owner's data", refused.exception.failure.not_changed)


class AHostileWheelIsRefused(unittest.TestCase):
    """A verified wheel is the right bytes, not necessarily a safe archive."""

    ESCAPES = {
        "absolute": "/etc/passwd",
        "windows absolute": "\\\\windows\\\\system32\\\\evil.dll",
        "drive letter": "C:/windows/evil.dll",
        "parent segment": "../../evil.py",
        "backslash traversal": "a\\\\..\\\\..\\\\evil.py",
    }

    def test_each_escape_is_named_rather_than_written(self):
        for label, member in self.ESCAPES.items():
            with self.subTest(label):
                self.assertTrue(vendor.unsafe_member(member),
                                f"{member!r} was not refused")

    def test_an_ordinary_member_is_allowed(self):
        """The control. A check that refuses everything proves nothing."""
        for member in ("lxml/etree.pyd", "openpyxl/__init__.py",
                       "openpyxl-3.1.5.dist-info/METADATA"):
            with self.subTest(member):
                self.assertEqual(vendor.unsafe_member(member), "")

    def test_nothing_is_written_when_one_entry_is_hostile(self):
        host = FakeWindows()
        where = r"C:\\DeskPilot\\app\\vendor\\wheels"
        into = r"C:\\DeskPilot\\app\\vendor\\site"
        host.makedirs(where)
        body = wheel_bytes({"good/__init__.py": "x = 1\n",
                            "a\\\\..\\\\..\\\\evil.py": "boom\n"})
        host.write_bytes(ntpath.join(where, "good-1.0-py3-none-any.whl"), body)
        digest = hashlib.sha256(body).hexdigest()
        wheels = vendor.verify(host, where, ["good-1.0-py3-none-any.whl"],
                               {digest})
        before = set(host.files)
        with self.assertRaises(ReaderRefused):
            vendor.unpack(host, where, into, wheels)
        self.assertEqual(set(host.files), before,
                         "a hostile entry left part of the wheel on disk")

    def test_the_metadata_is_kept_and_the_data_directory_is_not(self):
        """Licences ship with the code; CleverCSV's man pages do not."""
        body = wheel_bytes({
            "pkg/__init__.py": "x = 1\n",
            "pkg-1.0.dist-info/METADATA": "Name: pkg\n",
            "pkg-1.0.dist-info/licenses/LICENSE": "MIT\n",
            "pkg-1.0.data/data/man/man1/pkg.1": "manual\n"})
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            kept = vendor.members(archive)
        self.assertIn("pkg-1.0.dist-info/licenses/LICENSE", kept)
        self.assertIn("pkg/__init__.py", kept)
        self.assertNotIn("pkg-1.0.data/data/man/man1/pkg.1", kept)


class TheyActuallyImport(unittest.TestCase):
    """Unpacked the way the installer unpacks them, then imported.

    The compiled four are Windows-only and cannot run here. The pure
    ones can, and they are the proof that the extraction produces an
    importable tree rather than merely the right number of files.
    """

    #: Needs a child process twice over -- pip to fetch, and a fresh
    #: interpreter to import off the extraction -- and DeskPilot's
    #: egress guard denies ``subprocess.Popen`` process-wide once any
    #: test has built a Solvent. The guard is irreversible by design
    #: and is not worked around; the precedent for saying so is
    #: ``test_installer_packaged_entrypoint.py``.
    RUN_SEPARATELY = ("python3 -I -m pytest "
                      "tests_solvent/test_installer_vendored_readers.py "
                      "-k TheyActuallyImport")

    @classmethod
    def setUpClass(cls):
        try:
            from solvent import egress
        except ImportError:                      # pragma: no cover
            egress = None
        if egress is not None and egress.is_installed():
            raise unittest.SkipTest(
                "the egress guard is installed in this interpreter, so "
                "neither pip nor a child interpreter can be started. Run "
                f"this case on its own:\n    {cls.RUN_SEPARATELY}")
        cls.box = tempfile.TemporaryDirectory()
        cls.site = pathlib.Path(cls.box.name) / "site"
        cls.fetched = _fetch_portable(pathlib.Path(cls.box.name) / "wheels")
        if not cls.fetched:
            raise unittest.SkipTest(
                "the pure-Python wheels are not available offline; this "
                "case needs them and does not pretend to have run")
        host = _RealHost()
        allowed = vendor.allowed_digests(manifest_text())
        names = [p.name for p in cls.fetched]
        wheels = vendor.verify(host, str(cls.fetched[0].parent), names,
                               allowed)
        cls.written = vendor.unpack(host, str(cls.fetched[0].parent),
                                    str(cls.site), wheels)

    @classmethod
    def tearDownClass(cls):
        cls.box.cleanup()

    def test_each_pure_python_reader_imports_off_the_extraction(self):
        for module in PORTABLE:
            with self.subTest(module):
                asked = subprocess.run(
                    [sys.executable, "-I", "-c",
                     f"import sys; sys.path.insert(0, {str(self.site)!r}); "
                     f"import {module}; print({module}.__file__)"],
                    capture_output=True, text=True, check=False)
                self.assertEqual(asked.returncode, 0,
                                 f"{module}: {asked.stderr.strip()[-300:]}")
                self.assertIn(str(self.site), asked.stdout)

    def test_openpyxl_reads_a_workbook_it_did_not_write(self):
        """The reader does its job, not merely its import."""
        asked = subprocess.run(
            [sys.executable, "-I", "-c",
             f"import sys; sys.path.insert(0, {str(self.site)!r});\n"
             "from openpyxl import Workbook, load_workbook\n"
             "import io\n"
             "book = Workbook(); book.active['A1'] = 'Ω total'\n"
             "buffer = io.BytesIO(); book.save(buffer); buffer.seek(0)\n"
             "print(load_workbook(buffer).active['A1'].value)"],
            capture_output=True, text=True, check=False)
        self.assertEqual(asked.returncode, 0, asked.stderr.strip()[-400:])
        self.assertIn("Ω total", asked.stdout)

    def test_the_licence_files_travelled_with_the_code(self):
        found = [w for w in self.written if "LICEN" in w.upper()]
        self.assertTrue(found, "no licence file was unpacked")


class _RealHost:
    """Just enough Host for vendor.py, against the real filesystem.

    ``vendor`` joins with :mod:`ntpath`, because the machine it runs
    on is Windows. Running the same code here means translating the
    separator on the way to the filesystem; the alternative -- making
    the product choose its separator at runtime -- would be changing
    the thing under test so the test can run, which is the opposite
    of what this file is for.
    """

    @staticmethod
    def _local(path: str) -> pathlib.Path:
        return pathlib.Path(path.replace("\\", "/"))

    def read_bytes(self, path: str) -> bytes:
        return self._local(path).read_bytes()

    def write_bytes(self, path: str, data: bytes) -> None:
        self._local(path).write_bytes(data)

    def makedirs(self, path: str) -> None:
        self._local(path).mkdir(parents=True, exist_ok=True)


def portable_requirements() -> str:
    """The manifest, narrowed to the wheels that are not Windows-only.

    Narrowed rather than used whole, and the reason is worth stating:
    passing the whole manifest here fails, correctly. On Linux pip
    resolves a manylinux wheel for lxml, that file's hash is not in a
    manifest built for win_amd64, and ``--require-hashes`` refuses
    it. That refusal is the control working; this function simply
    does not ask it the question.
    """
    out, block, portable = [], [], True
    for line in manifest_text().splitlines():
        comment = re.match(r"^#\s+(\S+\.whl)\b", line)
        if comment:
            # The filenames above a pin say what it resolves to. All of
            # them must be platform-independent for the pin to be.
            if not block:
                portable = portable and comment.group(1).endswith("-any.whl")
            continue
        if re.match(r"^[A-Za-z0-9._-]+==", line):
            block = [line] if portable else []
            continue
        if block and line.strip().startswith("--hash"):
            block.append(line)
            if not line.rstrip().endswith("\\"):
                out.append("\n".join(block))
                block, portable = [], True
            continue
        if not line.strip() or line.startswith("#"):
            block, portable = block, True if not block else portable
    return "\n".join(out) + "\n"


def _fetch_portable(into: pathlib.Path) -> list[pathlib.Path]:
    """The pure-Python wheels from the manifest, if pip can get them.

    Hash-checked by pip against the manifest's own hashes, exactly as
    the build does it. Skipped rather than faked when there is no
    network: a case that quietly substituted a stub would be testing
    the stub.
    """
    into.mkdir(parents=True, exist_ok=True)
    requirements = into / "portable.txt"
    requirements.write_text(portable_requirements(), encoding="utf-8")
    asked = subprocess.run(
        [sys.executable, "-m", "pip", "download", "--disable-pip-version-check",
         "-q", "--no-cache-dir", "--require-hashes", "--only-binary=:all:",
         "--no-deps", "--dest", str(into), "-r", str(requirements)],
        capture_output=True, text=True, check=False, timeout=600)
    if asked.returncode != 0:
        return []
    return sorted(p for p in into.glob("*.whl")
                  if p.name.endswith("-any.whl"))
