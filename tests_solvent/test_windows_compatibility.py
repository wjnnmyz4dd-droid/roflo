"""Windows invariants, enforced rather than audited once.

DeskPilot's target is Windows 11 and its development and test machines
are Linux. That asymmetry is the whole problem: a POSIX assumption costs
nothing here and breaks there, and an audit that reads the source once
goes stale the week after it is written.

So each class below encodes one property the source has to keep. They
are source-level properties -- none of them is a substitute for running
on Windows, and none is claimed as one. See
``docs/deskpilot-windows-compatibility.md`` for what is still
UNVERIFIED ON WINDOWS.
"""

from __future__ import annotations

import ntpath
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
PRODUCTION = sorted((ROOT / "solvent").rglob("*.py"))
INSTALLER = sorted(
    (ROOT / "windows" / "installer" / "deskpilot_installer").rglob("*.py"))
#: The test tree and the tooling, which were outside this check until
#: Windows CI failed on eight files and the cause in every one of them
#: was an undeclared encoding in a *test*. A matcher that guards the
#: product and not the thing that measures the product cannot tell
#: that the product is fine; it just goes red on Windows and nowhere
#: else. 147 calls were missing one.
HARNESS = sorted(
    list((ROOT / "tests_solvent").rglob("*.py"))
    + list((ROOT / "tools").rglob("*.py")))


def source(paths):
    for path in paths:
        yield path.relative_to(ROOT).as_posix(), path.read_text(
            encoding="utf-8")


class TheSpoolNamesADrive(unittest.TestCase):
    """The defect this module was started for.

    ``/var/lib/solvent/owner-intents`` was the default on both platforms.
    On Windows it names no drive, so it resolves against whichever drive
    the process was started from -- the control centre writing intents
    where the runtime never reads them. The installer always configures
    the spool explicitly, so an installed system never hit this; a
    developer or a hand-started control centre would.
    """

    def test_the_windows_default_names_a_drive(self):
        from solvent.types import default_owner_intent_spool

        spool = default_owner_intent_spool(windows=True,
                                           program_data=r"C:\ProgramData")
        drive, _ = ntpath.splitdrive(spool)
        self.assertTrue(drive, f"{spool!r} is drive-relative on Windows")
        self.assertTrue(ntpath.isabs(spool))

    def test_the_posix_default_is_unchanged(self):
        from solvent.types import default_owner_intent_spool

        self.assertEqual(default_owner_intent_spool(windows=False),
                         "/var/lib/solvent/owner-intents")

    def test_a_drive_relative_spool_is_refused_not_normalised(self):
        """Normalising would pick a drive on the owner's behalf."""
        from solvent.types import intent_spool_problem

        why = intent_spool_problem("/var/lib/solvent/owner-intents",
                                   windows=True)
        self.assertIn("names no drive", why)
        self.assertIn("whichever drive", why)
        self.assertNotIn("C:/var", why,
                         "the path must be refused, not silently "
                         "normalised onto a drive nobody chose")

    def test_a_proper_windows_spool_is_accepted(self):
        from solvent.types import intent_spool_problem

        self.assertEqual(
            intent_spool_problem(r"C:\ProgramData\DeskPilot\owner-intents",
                                 windows=True), "")

    def test_a_relative_posix_spool_is_refused(self):
        from solvent.types import intent_spool_problem

        self.assertIn("absolute",
                      intent_spool_problem("spool", windows=False))

    def test_an_empty_spool_is_refused_on_both(self):
        from solvent.types import intent_spool_problem

        for windows in (True, False):
            with self.subTest(windows=windows):
                self.assertTrue(intent_spool_problem("", windows=windows))

    def test_the_writer_and_the_reader_share_one_definition(self):
        """Two literals drift; a drifted spool loses requests silently."""
        from solvent import harness
        from solvent.web import intents

        self.assertEqual(harness.OWNER_INTENT_SPOOL, intents.INTENT_SPOOL)


class NoPosixOnlyPathsInProduction(unittest.TestCase):
    """A path literal that only exists on Linux is a Windows crash."""

    #: Read where the host will say and absent where it will not --
    #: capacity treats a missing memory reading as the ordinary Windows
    #: case and an exception as a refusal, which is the distinction that
    #: makes this one safe. ``types.py`` holds the single POSIX base that
    #: ``default_state_path`` derives the POSIX side from. Listed so that
    #: a *new* one is not.
    ALLOWED = {"solvent/diagnostics.py": {"/proc/meminfo"},
               "solvent/types.py": {"/var/lib/solvent"}}

    POSIX_ONLY = re.compile(
        r"[\"'](/(?:proc|sys|etc|var|usr|bin|sbin|dev|opt|tmp|home)/[^\"']*)[\"']")

    def test_no_unlisted_posix_path_literal(self):
        for name, text in source(PRODUCTION):
            allowed = self.ALLOWED.get(name, set())
            for found in self.POSIX_ONLY.findall(text):
                if found in allowed:
                    continue
                with self.subTest(module=name, path=found):
                    self.fail(f"{name} hardcodes the POSIX path {found!r}; "
                              f"derive it per platform or list it with a "
                              f"reason in ALLOWED")

    def test_the_allowance_is_real_and_not_a_stale_entry(self):
        """An allowance for something that has gone is an allowance that
        would silently cover the next thing to appear there."""
        for name, paths in self.ALLOWED.items():
            text = (ROOT / name).read_text(encoding="utf-8")
            for path in paths:
                with self.subTest(module=name, path=path):
                    self.assertIn(path, text)


class NoPosixOnlyCalls(unittest.TestCase):
    """Calls with no Windows equivalent, or different Windows semantics."""

    FORBIDDEN = {
        "os.fork": "Windows has no fork",
        "os.getuid": "Windows has no uid",
        "os.geteuid": "Windows has no uid",
        "signal.SIGKILL": "Windows has no SIGKILL",
        "signal.SIGHUP": "Windows has no SIGHUP",
        "AF_UNIX": "Windows named pipes are not Unix sockets",
        "shell=True": "the shell differs, and it is an injection surface",
        "os.rename": "on Windows rename fails if the target exists; "
                     "os.replace is the cross-platform atomic move",
    }

    def test_production_and_installer_avoid_them(self):
        """A `hasattr` guard makes a POSIX-only call cross-platform.

        ``hasattr(os, "geteuid") and os.geteuid() == 0`` is correct code
        and the blunt version of this test called it a defect. What is
        forbidden is an *unguarded* use.
        """
        for name, text in source(PRODUCTION + INSTALLER):
            for number, line in enumerate(text.splitlines(), start=1):
                if line.lstrip().startswith("#"):
                    continue
                for call, why in self.FORBIDDEN.items():
                    if call not in line:
                        continue
                    attribute = call.split(".")[-1]
                    if f'hasattr(os, "{attribute}")' in line:
                        continue
                    with self.subTest(module=name, line=number, call=call):
                        self.fail(f"{name}:{number}: {why}")


class TextIoDeclaresItsEncoding(unittest.TestCase):
    """Windows defaults text IO to the ANSI code page, not UTF-8.

    An undeclared encoding is the quietest Windows defect there is: it
    works for ASCII, mangles anything else, and raises
    UnicodeEncodeError on the client file that finally contains a Greek
    name. DeskPilot's whole encoding ladder exists to preserve those
    characters, so writing them back out through cp1252 would undo it.
    """

    CALL = re.compile(r"\b(?:open|read_text|write_text)\s*\(")

    @staticmethod
    def _quoted(text: str) -> list[tuple[int, int]]:
        """Character ranges covered by a string literal or a comment.

        Tokenised rather than guessed at. The matcher reads raw source,
        so ``self.assertNotIn("open(request", text)`` -- a test
        asserting that something is *absent* -- looked exactly like a
        bare call, and so did every example written inside this
        module's own docstrings. Three false positives the moment the
        scan was pointed at the test tree, all of the same kind:
        flagging a file for containing the string it exists to refuse.
        """
        import io
        import tokenize

        offsets, running = [0], 0
        for line in text.splitlines(keepends=True):
            running += len(line)
            offsets.append(running)
        spans = []
        try:
            for token in tokenize.generate_tokens(io.StringIO(text).readline):
                if token.type in (tokenize.STRING, tokenize.COMMENT):
                    start = offsets[token.start[0] - 1] + token.start[1]
                    end = offsets[token.end[0] - 1] + token.end[1]
                    spans.append((start, end))
        except (tokenize.TokenError, IndentationError):   # pragma: no cover
            return []
        return spans

    def test_every_text_call_names_an_encoding(self):
        for name, text in source(PRODUCTION + INSTALLER + HARNESS):
            quoted = self._quoted(text)
            for match in self.CALL.finditer(text):
                if any(a <= match.start() < b for a, b in quoted):
                    continue
                # To the call's own closing bracket, counting depth: a
                # 400-character window cut `write_text(json.dumps({...}),
                # encoding="utf-8")` in half and called it a defect.
                window, depth = "", 0
                for character in text[match.end() - 1:]:
                    window += character
                    depth += character == "("
                    depth -= character == ")"
                    if depth == 0:
                        break
                head = window
                if any(token in window for token in
                       ('"rb"', "'rb'", '"wb"', "'wb'", '"ab"', "'ab'",
                        '"r+b"', "'r+b'", "encoding=")):
                    continue
                # A zip member is opened in binary by definition, and the
                # name before the dot is what says so.
                before = text[max(0, match.start() - 24):match.start()]
                if re.search(r"(?:archive|zf|zipf|package)\.$", before):
                    continue
                # `def open(self)` is a method, not a file. A codebase
                # may legitimately call something `open` -- a
                # negotiation opens -- and flagging the definition is
                # the fifth false positive this matcher has produced.
                # Calls on an object are skipped for the same reason;
                # only a bare `open(...)` opens a file.
                if re.search(r"(?:def|async\s+def)\s+$", before):
                    continue
                if match.group().startswith("open") and before.rstrip().endswith("."):
                    continue
                line = text[:match.start()].count("\n") + 1
                if text.splitlines()[line - 1].lstrip().startswith("#"):
                    continue
                with self.subTest(module=name, line=line):
                    self.fail(f"{name}:{line} does text IO without an "
                              f"encoding; on Windows that is the ANSI code "
                              f"page: {head.strip()[:90]}")


    def test_the_matcher_still_finds_one(self):
        """A control. A scan that cannot report a bare call proves nothing.

        The sample is built from pieces so that this file does not
        itself contain the text being looked for -- which would make
        the scan above report this very line and prove only that the
        two halves of the check disagree.
        """
        call = "read_" + "text("
        sample = "text = path." + call + ")\n"
        found = [m.group() for m in self.CALL.finditer(sample)
                 if not any(a <= m.start() < b
                            for a, b in self._quoted(sample))]
        self.assertEqual(found, [call])

    def test_the_matcher_ignores_one_inside_a_string(self):
        """The false positive that appeared the moment it scanned tests."""
        call = "read_" + "text("
        sample = 'self.assertNotIn("' + call + '", text)\n'
        quoted = self._quoted(sample)
        found = [m.group() for m in self.CALL.finditer(sample)
                 if not any(a <= m.start() < b for a, b in quoted)]
        self.assertEqual(found, [])

    def test_the_harness_is_actually_scanned(self):
        """Otherwise the list above could quietly become empty."""
        self.assertGreater(len(HARNESS), 150)
        names = {p.name for p in HARNESS}
        self.assertIn("test_windows_compatibility.py", names)
        self.assertIn("mutate_solvent.py", names)


class TheDatabaseSurvivesWindowsLocking(unittest.TestCase):
    """SQLite on Windows locks differently and shares worse."""

    def test_a_busy_timeout_is_set(self):
        from solvent import store

        self.assertGreater(store.BUSY_TIMEOUT_S, 0,
                           "without a busy timeout a concurrent reader "
                           "raises 'database is locked' instead of waiting")

    def test_the_connection_sets_a_busy_timeout_pragma_too(self):
        text = (ROOT / "solvent" / "store.py").read_text(encoding="utf-8")
        self.assertIn("PRAGMA busy_timeout", text)

    def test_write_ahead_logging_is_requested(self):
        text = (ROOT / "solvent" / "store.py").read_text(encoding="utf-8")
        self.assertIn("PRAGMA journal_mode = WAL", text)

    def test_a_path_with_spaces_and_unicode_opens(self):
        """C:\\Program Files and a non-ASCII owner name are ordinary."""
        import tempfile

        from solvent.store import Store

        with tempfile.TemporaryDirectory() as box:
            folder = pathlib.Path(box) / "Program Files" / "DeskPilot Ωmega"
            folder.mkdir(parents=True)
            store = Store(str(folder / "solvent.db"))
            self.assertTrue((folder / "solvent.db").exists())
            store.close() if hasattr(store, "close") else None


class TheControlCentreStaysOnLoopback(unittest.TestCase):
    """A dashboard bound to every interface is a dashboard on the LAN."""

    def test_the_default_host_is_loopback(self):
        from solvent.web import server

        self.assertEqual(server.DEFAULT_HOST, "127.0.0.1")

    def test_nothing_binds_all_interfaces(self):
        """`web/exposure.py` names the wildcard in order to refuse it,
        which is the opposite of binding to it."""
        for name, text in source(PRODUCTION):
            if name == "solvent/web/exposure.py":
                self.assertIn("wildcard", text,
                              "the allowance holds only while this module "
                              "is the one that detects a wildcard bind")
                continue
            with self.subTest(module=name):
                self.assertNotIn("0.0.0.0", text)


class TheInstallerUsesWindowsPathSemantics(unittest.TestCase):
    """The installer builds Windows paths and runs only on Windows."""

    #: Modules whose ``os.path`` use is correct because the code runs on
    #: the Windows machine, where ``os.path`` *is* ``ntpath``: these two
    #: emit scripts and probes executed there. The installer's own path
    #: building, which happens in this interpreter, must use ntpath.
    RUN_ON_THE_TARGET = {
        "windows/installer/deskpilot_installer/integration.py",
        "windows/installer/deskpilot_installer/isolation.py",
    }

    def test_it_joins_with_ntpath_where_it_builds_paths_itself(self):
        joined = [name for name, text in source(INSTALLER)
                  if "os.path.join" in text
                  and name not in self.RUN_ON_THE_TARGET]
        self.assertEqual(joined, [],
                         "os.path.join builds POSIX paths when the installer "
                         "is exercised from Linux tests; use ntpath.join")

    def test_the_layout_is_rooted_on_a_drive(self):
        import sys

        sys.path.insert(0, str(ROOT / "windows" / "installer"))
        from deskpilot_installer.layout import DEFAULT_ROOT

        drive, _ = ntpath.splitdrive(DEFAULT_ROOT)
        self.assertTrue(drive, f"{DEFAULT_ROOT!r} names no drive")


if __name__ == "__main__":
    unittest.main()
