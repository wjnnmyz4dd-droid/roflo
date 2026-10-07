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
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "windows" / "installer"))

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
