"""The installer refuses the wrong bytes, and refuses them before acting.

Two separate promises, tested separately because they fail separately.

**It notices.** A payload whose digest does not match what the installer was
built for is rejected, including the cases that are easy to get wrong: a
truncated file, a file one byte longer, an absent file, and an installer built
with no expected digest at all. That last one matters most -- "nothing to
compare against" must not be treated as "nothing wrong".

**It refuses before it acts.** Noticing late is almost worthless. The tests
below assert that a rejected payload leaves the machine with no directories
created, no database, no scheduled task, no firewall rule, and -- for an
upgrade -- a running installation still running. The ``Failure`` the owner is
shown has to say so too, because the question after a failed installer is
"what state is my computer in now".

The archive tests cover the Windows-specific escapes, which are the ones a
POSIX-minded reader omits: a backslash in an entry name is an ordinary
character to ``zipfile`` and a directory separator to Windows, and a trailing
space or dot is silently stripped by the filesystem so two distinct entries can
collide.
"""

from __future__ import annotations

import hashlib
import os
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
INSTALLER = ROOT / "windows" / "installer"
sys.path.insert(0, str(INSTALLER))

from tests_solvent.installer_fake_host import (  # noqa: E402
    FakeWindows, certified_payload, fresh_windows, install_ready, zip_bytes,
)

from deskpilot_installer import payload  # noqa: E402
from deskpilot_installer.engine import Engine, OwnerAnswers  # noqa: E402
from deskpilot_installer.installlog import Log  # noqa: E402
from deskpilot_installer.layout import Layout  # noqa: E402
from deskpilot_installer.probe import Settings, system_check  # noqa: E402
from deskpilot_installer.report import Outcome, Secret  # noqa: E402

PACKAGE_PATH = r"C:\Media\DeskPilot-certified.zip"


def answers() -> OwnerAnswers:
    return OwnerAnswers(owner_identity="Jo",
                        web_password=Secret("correct-horse-battery"),
                        ai_model_tag="qwen2.5:14b-instruct")


def settings(digest: str, layout: Layout | None = None) -> Settings:
    return Settings(layout=layout or Layout(), package_path=PACKAGE_PATH,
                    expected_digest=digest)


class TheDigestIsCompared(unittest.TestCase):

    def test_the_matching_payload_is_accepted(self):
        data, digest = certified_payload()
        host = fresh_windows(data, digest)
        verdict = payload.verify(host, PACKAGE_PATH, digest)
        self.assertTrue(verdict.verified)
        self.assertEqual(verdict.headline, "PACKAGE VERIFIED")

    def test_one_extra_byte_is_rejected(self):
        data, digest = certified_payload()
        host = fresh_windows(data, digest)
        host.files[host.files and r"c:\media\deskpilot-certified.zip"] = data + b"\x00"
        verdict = payload.verify(host, PACKAGE_PATH, digest)
        self.assertFalse(verdict.verified)

    def test_a_truncated_payload_is_rejected(self):
        data, digest = certified_payload()
        host = fresh_windows(data, digest)
        host.files[r"c:\media\deskpilot-certified.zip"] = data[:-1]
        self.assertFalse(payload.verify(host, PACKAGE_PATH, digest).verified)

    def test_a_different_but_valid_archive_is_rejected(self):
        """The substitution that matters: a working package, not this one."""
        data, digest = certified_payload()
        host = fresh_windows(data, digest)
        other = zip_bytes({"solvent/cli.py": b"# someone else's code\n"})
        host.files[r"c:\media\deskpilot-certified.zip"] = other
        verdict = payload.verify(host, PACKAGE_PATH, digest)
        self.assertFalse(verdict.verified)
        self.assertEqual(verdict.actual, hashlib.sha256(other).hexdigest())

    def test_an_absent_payload_is_rejected(self):
        host = fresh_windows()
        verdict = payload.verify(host, r"C:\Media\nothing.zip", "ab" * 32)
        self.assertFalse(verdict.verified)
        self.assertIn("not at", verdict.unreadable)

    def test_an_installer_built_without_a_digest_cannot_verify_anything(self):
        """"Nothing to compare" is not a pass. This is the fail-open bug."""
        data, _ = certified_payload()
        host = fresh_windows(data, "")
        verdict = payload.verify(host, PACKAGE_PATH, "")
        self.assertFalse(verdict.verified)
        self.assertIn("without an expected package digest", verdict.unreadable)

    def test_the_comparison_is_case_insensitive_on_the_expected_value(self):
        """A digest pasted in upper case is the same digest."""
        data, digest = certified_payload()
        host = fresh_windows(data, digest)
        self.assertTrue(payload.verify(host, PACKAGE_PATH,
                                       digest.upper()).verified)

    def test_require_verified_raises_for_a_bad_payload(self):
        data, digest = certified_payload()
        host = fresh_windows(data, digest)
        host.files[r"c:\media\deskpilot-certified.zip"] = data + b"x"
        verdict = payload.verify(host, PACKAGE_PATH, digest)
        with self.assertRaises(payload.PayloadRefused):
            payload.require_verified(verdict)

    def test_require_verified_passes_a_good_payload(self):
        data, digest = certified_payload()
        host = fresh_windows(data, digest)
        payload.require_verified(payload.verify(host, PACKAGE_PATH, digest))


class RefusalHappensBeforeAnythingChanges(unittest.TestCase):

    def setUp(self):
        self.data, self.digest = certified_payload()
        self.host = install_ready(package=self.data, digest=self.digest)
        self.host.files[r"c:\media\deskpilot-certified.zip"] = self.data + b"!"
        self.layout = Layout()

    def test_the_system_check_blocks_and_explains(self):
        report = system_check(self.host, settings(self.digest))
        row = report.row("DeskPilot package")
        self.assertEqual(row.outcome, Outcome.FAIL)
        self.assertFalse(report.may_continue)
        self.assertIsNotNone(report.failure)
        self.assertIn("nothing was installed", report.failure.not_changed)

    def test_installation_stops_at_the_first_step(self):
        report = Engine(self.host, settings(self.digest), answers(),
                        Log(self.host)).install()
        self.assertFalse(report.may_continue)
        self.assertEqual(report.failure.what_failed,
                         "Package verification failed")

    def test_nothing_was_written_to_the_machine(self):
        Engine(self.host, settings(self.digest), answers(),
               Log(self.host)).install()
        for directory in self.layout.all_dirs:
            self.assertFalse(self.host.exists(directory),
                             f"{directory} was created despite a refused package")
        self.assertFalse(self.host.exists(self.layout.db))
        self.assertFalse(self.host.exists(self.layout.env_file))

    def test_no_command_was_run_at_all(self):
        """Not one subprocess. The refusal precedes every action."""
        self.host.commands.clear()
        Engine(self.host, settings(self.digest), answers(),
               Log(self.host)).install()
        self.assertEqual(self.host.commands, [])

    def test_the_owner_is_told_metatrader_was_untouched(self):
        report = Engine(self.host, settings(self.digest), answers(),
                        Log(self.host)).install()
        joined = " ".join(report.failure.not_changed)
        self.assertIn("MetaTrader was not touched", joined)

    def test_the_safe_next_action_does_not_suggest_installing_anyway(self):
        report = Engine(self.host, settings(self.digest), answers(),
                        Log(self.host)).install()
        advice = report.failure.safe_next_action.lower()
        self.assertIn("do not install this copy", advice)


class AHostileArchiveIsNotUnpacked(unittest.TestCase):

    def _host_with(self, entries: dict) -> tuple[FakeWindows, str]:
        data = zip_bytes(entries)
        digest = hashlib.sha256(data).hexdigest()
        host = fresh_windows(data, digest)
        return host, digest

    def test_a_parent_traversal_entry_is_refused(self):
        host, digest = self._host_with({"../../evil.py": b"x"})
        with self.assertRaises(payload.PayloadRefused):
            payload.extract(host, PACKAGE_PATH, r"C:\DeskPilot\App")

    def test_an_absolute_entry_is_refused(self):
        host, digest = self._host_with({"/Windows/System32/evil.dll": b"x"})
        with self.assertRaises(payload.PayloadRefused):
            payload.extract(host, PACKAGE_PATH, r"C:\DeskPilot\App")

    def test_a_backslash_entry_is_refused(self):
        """``zipfile`` sees a filename; Windows sees a path. The POSIX blind spot."""
        host, digest = self._host_with({r"a\..\..\evil.py": b"x"})
        with self.assertRaises(payload.PayloadRefused):
            payload.extract(host, PACKAGE_PATH, r"C:\DeskPilot\App")

    def test_a_drive_letter_entry_is_refused(self):
        host, digest = self._host_with({"C:/Windows/evil.dll": b"x"})
        with self.assertRaises(payload.PayloadRefused):
            payload.extract(host, PACKAGE_PATH, r"C:\DeskPilot\App")

    def test_a_trailing_dot_entry_is_refused(self):
        """Windows strips it, so ``cli.py.`` can overwrite ``cli.py``."""
        self.assertNotEqual(payload.unsafe_member("solvent/cli.py."), "")

    def test_a_null_byte_entry_is_refused(self):
        self.assertNotEqual(payload.unsafe_member("a\x00b"), "")

    def test_nothing_is_written_when_one_entry_is_hostile(self):
        """All-or-nothing: a sound archive with one bad entry unpacks nothing."""
        host, digest = self._host_with({"solvent/cli.py": b"ok",
                                        "pyproject.toml": b"ok",
                                        "../../evil.py": b"bad"})
        with self.assertRaises(payload.PayloadRefused):
            payload.extract(host, PACKAGE_PATH, r"C:\DeskPilot\App")
        self.assertFalse(host.exists(r"C:\DeskPilot\App\solvent\cli.py"))
        self.assertFalse(host.exists(r"C:\DeskPilot\App\pyproject.toml"))

    def test_a_corrupt_archive_is_reported_not_crashed(self):
        host = fresh_windows()
        host.add_file(PACKAGE_PATH, b"this is not a zip file")
        safe, bad = payload.inspect(host, PACKAGE_PATH)
        self.assertEqual(safe, [])
        self.assertTrue(bad)

    def test_an_ordinary_archive_unpacks(self):
        host, digest = self._host_with({"solvent/cli.py": b"ok",
                                        "docs/readme.md": b"ok"})
        written = payload.extract(host, PACKAGE_PATH, r"C:\DeskPilot\App")
        self.assertEqual(len(written), 2)
        self.assertTrue(host.exists(r"C:\DeskPilot\App\solvent\cli.py"))

    def test_every_path_that_would_be_packaged_is_a_safe_entry_name(self):
        """The real payload's contents, taken from the source of truth.

        Checked against the repository tree rather than a built archive, so it
        runs in every environment and catches a hostile or merely awkward
        filename the day it is committed instead of the day someone builds an
        installer. ``git archive`` writes these paths verbatim, so a path that
        would be an unsafe ZIP entry here is one in the shipped payload.
        """
        sys.path.insert(0, str(ROOT / "windows" / "installer"))
        from build import PAYLOAD_PATHS

        offenders = []
        for top in PAYLOAD_PATHS:
            base = ROOT / top
            if not base.exists():
                continue
            paths = [base] if base.is_file() else sorted(base.rglob("*"))
            for path in paths:
                if "__pycache__" in path.parts:
                    continue
                entry = path.relative_to(ROOT).as_posix()
                if path.is_dir():
                    entry += "/"
                why = payload.unsafe_member(entry)
                if why:
                    offenders.append(f"{entry}: {why}")
        self.assertEqual(offenders, [])
        self.assertTrue(PAYLOAD_PATHS, "the payload path list must not be empty")


if __name__ == "__main__":
    unittest.main()


class ThePayloadDigestIsReproducible(unittest.TestCase):
    """Two builds of one commit must produce one digest.

    The payload's SHA-256 is compiled into the installer and is the
    whole of its supply-chain claim: an owner, an auditor or a future
    maintainer is meant to be able to rebuild the commit and get the
    same number back.

    They could not. ``build_payload`` appended the vendored wheels
    with ``ZipFile.write``, which stamps each entry with the file's
    mtime on disk -- and a wheel's mtime is whenever ``pip download``
    fetched it. One CI run built commit c8e06b5 twice and got
    payloads digesting to ``a81a8a05...`` and ``3fb1e25a...``, while
    the docstring directly above the code said "two builds of one
    commit still produce the same bytes and the same digest".

    The network is not touched here: ``fetch_wheels`` is replaced with
    one that writes two files, and the two builds are separated in
    time so their mtimes genuinely differ. That separation is the
    whole experiment -- without it the old code passes too.
    """

    #: Two mtimes a zip can tell apart. A DOS timestamp stores seconds
    #: in two-second steps, so "download one, sleep a moment, download
    #: the other" is not a reliable difference -- the first version of
    #: this slept 1.1s and the negative test below passed or failed
    #: depending on which side of an even second the clock fell. The
    #: mtimes are set outright instead, which is also what makes this
    #: run in the same time either way.
    MTIMES = (1_700_000_000.0, 1_700_086_400.0)

    def _fake_wheels(self, into):
        into.mkdir(parents=True, exist_ok=True)
        when = self.MTIMES[self._build]
        for tag in ("cp311", "cp313"):
            directory = into / tag
            directory.mkdir(parents=True, exist_ok=True)
            wheel = directory / ("thing-1.0-%s-%s-win_amd64.whl" % (tag, tag))
            wheel.write_bytes(b"wheel bytes")
            os.utime(wheel, (when, when))
        return {"cp311": 1, "cp313": 1}

    def _build_twice(self, writer=None):
        import subprocess
        import sys

        sys.path.insert(0, str(INSTALLER))
        import build

        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(ROOT),
                                capture_output=True, text=True,
                                check=True).stdout.strip()
        original_fetch, build.fetch_wheels = build.fetch_wheels, self._fake_wheels
        original_append = build._append
        if writer is not None:
            build._append = writer
        try:
            digests = []
            for index in range(2):
                self._build = index
                with tempfile.TemporaryDirectory() as box:
                    out = pathlib.Path(box) / "stage" / ("p%d.zip" % index)
                    digest, _size = build.build_payload(out, commit)
                    digests.append(digest)
            return digests
        finally:
            build.fetch_wheels = original_fetch
            build._append = original_append

    def test_two_builds_of_one_commit_agree(self):
        first, second = self._build_twice()
        self.assertEqual(first, second,
                         "the payload digest depends on something other "
                         "than the commit and the pinned wheels")

    def test_the_old_way_of_appending_did_not_agree(self):
        """The defect itself, so this test cannot pass for a free reason.

        If ``ZipFile.write`` ever becomes deterministic this fails,
        and that is the right outcome: it would mean the test above
        has stopped proving anything.
        """
        def by_mtime(archive, source, name, stamp):
            archive.write(source, name)

        first, second = self._build_twice(writer=by_mtime)
        self.assertNotEqual(first, second,
                            "appending by mtime should differ between "
                            "two builds a second apart")
