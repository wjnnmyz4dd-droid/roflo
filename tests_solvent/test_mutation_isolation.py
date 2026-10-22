"""A mutation run must not be able to put its defects in a commit.

One did. ``de94386`` was staged with ``git add -A`` while a run held
``solvent/finder.py`` patched, and the snapshot took the mutant: two
pushed commits carried ``if False:`` where the Finder declines work that
costs more than it earns. The runner restored the file a second later, so
the working tree looked right and nothing noticed for two commits.

These tests cover the three defences in :mod:`tools.mutation_guard`
separately, because each one is the only cover for a case the others
miss, and a suite that tested them as a bundle would not notice one of
them silently doing nothing.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import mutation_guard  # noqa: E402


class TheRunnersIsolateByDefault(unittest.TestCase):
    """Production source is not written to at all unless asked."""

    def test_the_solvent_runner_defaults_to_a_copy(self):
        import tools.mutate_solvent as runner

        parser_source = __import__("inspect").getsource(runner.main)
        self.assertIn("isolated_tree", parser_source)
        self.assertIn("--in-place", parser_source)

    def test_the_installer_runner_defaults_to_a_copy(self):
        import tools.mutate_installer as runner

        parser_source = __import__("inspect").getsource(runner.main)
        self.assertIn("isolated_tree", parser_source)
        self.assertIn("--in-place", parser_source)

    def test_an_isolated_tree_carries_uncommitted_work(self):
        """A worktree of HEAD would test different code from the one certified."""
        scratch = ROOT / "_isolation_probe.tmp"
        scratch.write_text("uncommitted\n", encoding="utf-8")
        try:
            with mutation_guard.isolated_tree(
                    ROOT, names=[scratch.name, "solvent/finder.py"]) as work:
                copied = work / scratch.name
                self.assertTrue(copied.is_file(),
                                "untracked working-tree files must be copied")
                self.assertEqual(copied.read_text(encoding="utf-8"),
                                 "uncommitted\n")
                self.assertTrue((work / "solvent" / "finder.py").is_file())
        finally:
            scratch.unlink(missing_ok=True)

    def test_git_supplies_the_file_list_when_nothing_else_does(self):
        """The list the runners actually use, which needs the git CLI.

        Skipped once the egress guard is armed, because it denies
        subprocess for the whole interpreter and that is the Action Gate
        doing its job rather than anything wrong here. Reported as a skip,
        never as a pass.
        """
        from solvent import egress

        if egress.is_installed():
            self.skipTest("the egress guard denies subprocess process-wide; "
                          "run this module on its own to exercise the git path")
        names = mutation_guard.tracked_files(ROOT)
        self.assertIn("solvent/finder.py", names)
        self.assertIn("tools/mutation_guard.py", names)

    def test_patching_the_copy_leaves_the_original_alone(self):
        target = ROOT / "solvent" / "finder.py"
        before = target.read_text(encoding="utf-8")
        with mutation_guard.isolated_tree(
                ROOT, names=["solvent/finder.py"]) as work:
            (work / "solvent" / "finder.py").write_text("mutated\n",
                                                        encoding="utf-8")
            self.assertEqual(target.read_text(encoding="utf-8"), before)
        self.assertEqual(target.read_text(encoding="utf-8"), before)

    def test_the_copy_is_removed_afterwards(self):
        with mutation_guard.isolated_tree(
                ROOT, names=["solvent/finder.py"]) as work:
            path = work
        self.assertFalse(path.exists())

    def test_a_copy_a_killed_run_left_behind_is_reaped(self):
        """SIGKILL cannot be caught, so the finally does not always run.

        Observed: a battery hit a harness time limit mid-run. The working
        tree was clean and no claim was left -- the isolation did its job
        -- but the copy stayed in /tmp. Tidiness rather than a control,
        and kept honest by a test because an untested cleanup is a
        comment.
        """
        import os
        import tempfile
        import time

        parent = pathlib.Path(tempfile.gettempdir())
        abandoned = pathlib.Path(
            tempfile.mkdtemp(prefix="deskpilot-mutation-", dir=parent))
        (abandoned / "leftover.py").write_text("x = 1\n", encoding="utf-8")
        old = time.time() - mutation_guard._ABANDONED_AFTER_SECONDS - 60
        os.utime(abandoned, (old, old))

        fresh = pathlib.Path(
            tempfile.mkdtemp(prefix="deskpilot-mutation-", dir=parent))
        try:
            reaped = mutation_guard._reap_abandoned_copies()
            self.assertIn(str(abandoned), reaped)
            self.assertFalse(abandoned.exists())
            self.assertTrue(fresh.exists(),
                            "a running battery's own copy must survive")
        finally:
            import shutil

            shutil.rmtree(abandoned, ignore_errors=True)
            shutil.rmtree(fresh, ignore_errors=True)


class TheClaimProvesRestoration(unittest.TestCase):
    """For the in-place mode, which still exists and still needs cover."""

    def setUp(self):
        self.tree = pathlib.Path(tempfile.mkdtemp(prefix="claimtest-"))
        self.file = self.tree / "guard.py"
        self.file.write_text("if real_condition:\n    refuse()\n",
                             encoding="utf-8")
        self.claim_path = self.tree / ".mutation-claim.json"
        self._real_claim = mutation_guard.CLAIM
        mutation_guard.CLAIM = self.claim_path

    def tearDown(self):
        mutation_guard.CLAIM = self._real_claim
        for path in sorted(self.tree.rglob("*"), reverse=True):
            path.unlink(missing_ok=True)
        self.tree.rmdir()

    def test_a_clean_run_removes_its_claim(self):
        with mutation_guard.Claim(["guard.py"], root=self.tree):
            self.file.write_text("if False:\n    refuse()\n", encoding="utf-8")
            self.assertTrue(self.claim_path.exists())
            self.file.write_text("if real_condition:\n    refuse()\n",
                                 encoding="utf-8")
        self.assertFalse(self.claim_path.exists())

    def test_a_run_that_does_not_restore_raises_and_keeps_the_claim(self):
        with self.assertRaises(mutation_guard.RestorationUnproven) as caught:
            with mutation_guard.Claim(["guard.py"], root=self.tree):
                self.file.write_text("if False:\n    refuse()\n",
                                     encoding="utf-8")
        self.assertIn("guard.py", str(caught.exception))
        self.assertTrue(self.claim_path.exists(),
                        "the evidence must outlive the run that failed")

    def test_a_restoration_that_is_off_by_one_byte_is_not_a_restoration(self):
        with self.assertRaises(mutation_guard.RestorationUnproven):
            with mutation_guard.Claim(["guard.py"], root=self.tree):
                self.file.write_text("if real_condition:\n    refuse()\n ",
                                     encoding="utf-8")

    def test_a_second_run_cannot_claim_a_claimed_tree(self):
        """Two runs at once is how a module was clobbered earlier on."""
        with mutation_guard.Claim(["guard.py"], root=self.tree):
            with self.assertRaises(mutation_guard.RestorationUnproven) as caught:
                with mutation_guard.Claim(["guard.py"], root=self.tree):
                    pass
            self.assertIn("already claims", str(caught.exception))

    def test_a_claim_from_a_dead_process_reads_as_interrupted(self):
        self.claim_path.write_text(json.dumps(
            {"pid": 2 ** 22, "files": {"guard.py": "0" * 64}}),
            encoding="utf-8")
        held = mutation_guard.active_claim()
        self.assertIsNotNone(held)
        self.assertTrue(held["stale"], "a dead claim is not an all-clear")

    def test_an_unreadable_claim_is_not_an_all_clear(self):
        self.claim_path.write_text("{ truncated", encoding="utf-8")
        held = mutation_guard.active_claim()
        self.assertIsNotNone(held)
        self.assertTrue(held["stale"])


class TheScanFindsAnAppliedMutant(unittest.TestCase):
    """The defence that needs no cooperation from the runner."""

    def test_the_tree_as_it_stands_carries_no_mutant(self):
        self.assertEqual(mutation_guard.mutated_files(), [])

    def test_the_finder_incident_would_be_caught(self):
        """The exact text that reached origin, asked of staged content."""
        target = str(ROOT / "solvent" / "finder.py")
        real = (ROOT / "solvent" / "finder.py").read_text(encoding="utf-8")
        mutated = real.replace(
            "        if econ.profit_cents is not None and econ.profit_cents <= 0:",
            "        if False:")
        self.assertNotEqual(mutated, real, "the anchor must still be there")
        found = mutation_guard.mutated_files({target: mutated})
        self.assertEqual(
            found, [("finder-recommends-unprofitable-work", target)],
            "the scan must name the mutant that reached origin, in its file")

    def test_a_replacement_whose_original_is_still_there_is_not_a_hit(self):
        """Short replacements occur legitimately; both halves must be read."""
        target = str(ROOT / "solvent" / "finder.py")
        real = (ROOT / "solvent" / "finder.py").read_text(encoding="utf-8")
        self.assertEqual(mutation_guard.mutated_files({target: real}), [])

    def test_report_refuses_while_a_claim_is_held(self):
        probe = pathlib.Path(tempfile.mkdtemp(prefix="reporttest-"))
        (probe / "x.py").write_text("x = 1\n", encoding="utf-8")
        real = mutation_guard.CLAIM
        mutation_guard.CLAIM = probe / ".mutation-claim.json"
        try:
            with mutation_guard.Claim(["x.py"], root=probe):
                safe, why = mutation_guard.report()
                self.assertFalse(safe)
                self.assertIn("claims this tree", why)
        finally:
            mutation_guard.CLAIM = real
            for path in sorted(probe.rglob("*"), reverse=True):
                path.unlink(missing_ok=True)
            probe.rmdir()


class ThePreCommitHookIsReal(unittest.TestCase):
    """A hook nobody installed protects nothing.

    The checks run in-process against supplied index contents rather than
    by spawning the script. Once any test installs DeskPilot's egress
    guard it denies subprocess for the whole interpreter, so a test that
    shells out passes alone and fails in the suite -- which is the only
    run that counts.
    """

    def test_the_hook_exists_and_is_executable(self):
        hook = ROOT / ".githooks" / "pre-commit"
        self.assertTrue(hook.is_file())
        self.assertTrue(os.access(hook, os.X_OK), "the hook must be runnable")
        self.assertIn("precommit_mutation_check",
                      hook.read_text(encoding="utf-8"))

    def test_the_repository_points_at_the_hook_directory(self):
        """Read from .git/config rather than asked of the git CLI.

        A developer's clone must point at the hook; a CI checkout
        cannot, and should not. ``actions/checkout`` clones fresh for
        every run and configures nothing, so asserting the setting
        there was a test measuring the environment rather than the
        repository -- one of the Windows CI failures, and it would
        have failed on a Linux runner just as surely.

        Skipped rather than relaxed. The assertion is the one that
        matters on the machine where the hook actually runs.
        """
        import os

        config = (ROOT / ".git" / "config")
        if not config.is_file():                      # a worktree or a submodule
            self.skipTest("no .git/config here to read")
        if os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"):
            self.skipTest(
                "a CI checkout configures no hooks and runs no commit, so "
                "this asserts nothing here; it is the developer clone that "
                "has to point at .githooks")
        body = config.read_text(encoding="utf-8", errors="replace")
        self.assertIn("hooksPath = .githooks", body,
                      "run: git config core.hooksPath .githooks")

    def test_the_checker_passes_on_a_clean_index(self):
        from tools import precommit_mutation_check as hook

        clean = {str(ROOT / "solvent" / "finder.py"):
                 (ROOT / "solvent" / "finder.py").read_text(encoding="utf-8")}
        self.assertEqual(hook.main(staged=clean), 0)

    def test_the_checker_refuses_a_mutant_in_the_index(self):
        """The incident, through the real entry point.

        The file on disk is correct; only the index holds the defect.
        That is exactly the state that put a disabled profit guard on
        origin, so it is the state the hook has to catch.
        """
        from tools import precommit_mutation_check as hook

        real = (ROOT / "solvent" / "finder.py").read_text(encoding="utf-8")
        mutated = real.replace(
            "        if econ.profit_cents is not None and econ.profit_cents <= 0:",
            "        if False:")
        self.assertNotEqual(mutated, real, "the anchor must still be there")
        staged = {str(ROOT / "solvent" / "finder.py"): mutated}
        self.assertEqual(hook.main(staged=staged), 1)

    def test_the_checker_refuses_while_a_claim_is_held(self):
        """The concurrent-commit case, through the real entry point."""
        from tools import precommit_mutation_check as hook

        claim = mutation_guard.CLAIM
        self.assertFalse(claim.exists(), "a real run is in progress; not now")
        claim.write_text(json.dumps(
            {"pid": os.getpid(), "reason": "test",
             "files": {"solvent/finder.py": "0" * 64}}), encoding="utf-8")
        try:
            self.assertEqual(hook.main(staged={}), 1)
        finally:
            claim.unlink(missing_ok=True)

    def test_the_hook_reads_the_index_not_the_working_tree(self):
        """Stated as a test, because the distinction is the whole incident."""
        import inspect

        from tools import precommit_mutation_check as hook

        source = inspect.getsource(hook.staged_text)
        self.assertIn("--cached", source)
        self.assertIn("git", source)


if __name__ == "__main__":
    unittest.main()
