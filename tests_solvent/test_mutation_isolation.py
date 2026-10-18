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
            with mutation_guard.isolated_tree(ROOT) as work:
                copied = work / scratch.name
                self.assertTrue(copied.is_file(),
                                "untracked working-tree files must be copied")
                self.assertEqual(copied.read_text(encoding="utf-8"),
                                 "uncommitted\n")
                self.assertTrue((work / "solvent" / "finder.py").is_file())
        finally:
            scratch.unlink(missing_ok=True)

    def test_patching_the_copy_leaves_the_original_alone(self):
        target = ROOT / "solvent" / "finder.py"
        before = target.read_text(encoding="utf-8")
        with mutation_guard.isolated_tree(ROOT) as work:
            (work / "solvent" / "finder.py").write_text("mutated\n",
                                                        encoding="utf-8")
            self.assertEqual(target.read_text(encoding="utf-8"), before)
        self.assertEqual(target.read_text(encoding="utf-8"), before)

    def test_the_copy_is_removed_afterwards(self):
        with mutation_guard.isolated_tree(ROOT) as work:
            path = work
        self.assertFalse(path.exists())


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
    """A hook nobody installed protects nothing."""

    def test_the_hook_exists_and_is_executable(self):
        hook = ROOT / ".githooks" / "pre-commit"
        self.assertTrue(hook.is_file())
        self.assertTrue(os.access(hook, os.X_OK), "the hook must be runnable")

    def test_the_repository_points_at_the_hook_directory(self):
        configured = subprocess.run(
            ["git", "config", "--get", "core.hooksPath"], cwd=ROOT,
            capture_output=True, text=True).stdout.strip()
        self.assertEqual(configured, ".githooks",
                         "run: git config core.hooksPath .githooks")

    def test_the_checker_passes_on_the_tree_as_it_stands(self):
        done = subprocess.run(
            [sys.executable, "tools/precommit_mutation_check.py"], cwd=ROOT,
            capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_the_checker_refuses_while_a_claim_is_held(self):
        """The concurrent-commit case, end to end through the real script."""
        claim = mutation_guard.CLAIM
        self.assertFalse(claim.exists(), "a real run is in progress; not now")
        claim.write_text(json.dumps(
            {"pid": os.getpid(), "reason": "test",
             "files": {"solvent/finder.py": "0" * 64}}), encoding="utf-8")
        try:
            done = subprocess.run(
                [sys.executable, "tools/precommit_mutation_check.py"],
                cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(done.returncode, 1)
            self.assertIn("claims this tree", done.stderr)
            self.assertIn("solvent/finder.py", done.stderr)
        finally:
            claim.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
