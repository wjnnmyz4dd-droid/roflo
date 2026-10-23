"""Every mutant must still quote code that exists, in both batteries.

A mutant replaces an exact string. When the code it quotes is edited
the replacement matches zero times, the harness reports NOT_APPLIED,
and the control that mutant guards is unguarded -- correctly and
honestly reported, and only after the whole battery has run.

``tools/mutate_installer.py`` already had this check, in
``test_installer_architecture.EveryMutantStillDescribesTheCode``, and
it earned its place three times in one afternoon.
``tools/mutate_solvent.py`` did not, and the gap showed: a sweep that
added a tiebreaker to one query in ``solvent/ledger.py`` left
``invoice-recovery-reinvoices-what-is-already-billed`` quoting a line
that no longer existed, and nothing said so until a 234-mutant run
had finished forty minutes later and reported 233 killed, 0 survived,
1 not applied. Zero survivors reads like a clean sheet. It was not
one: the control over writing a client a second invoice for the same
job had no mutant behind it.

Both batteries are checked here, by the same rules, so neither can
lose a guard quietly again.
"""

from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))


def batteries():
    """``(name, module)`` for each mutation battery in the repository."""
    import mutate_installer
    import mutate_solvent

    return (("installer", mutate_installer), ("solvent", mutate_solvent))


class EveryAnchorStillQuotesLiveCode(unittest.TestCase):

    def paths(self, harness, mutant) -> pathlib.Path:
        """Where a battery says one mutant's module lives.

        The two batteries spell this differently -- one takes a name
        relative to the installer package, the other a path relative
        to the repository -- so the helper each provides is asked
        rather than a path being assembled here.
        """
        if hasattr(harness, "source_path"):
            return pathlib.Path(harness.source_path(mutant.module))
        return ROOT / mutant.module

    def test_both_batteries_are_substantial(self):
        for name, harness in batteries():
            with self.subTest(name):
                self.assertGreater(len(harness.MUTANTS), 50)

    def test_every_anchor_appears_exactly_once(self):
        """Exactly once: a pattern matching twice is refused as ambiguous."""
        for name, harness in batteries():
            wrong = []
            for mutant in harness.MUTANTS:
                path = self.paths(harness, mutant)
                if not path.is_file():
                    wrong.append(f"{mutant.name}: {mutant.module} is missing")
                    continue
                found = path.read_text(encoding="utf-8").count(mutant.old)
                if found != 1:
                    wrong.append(f"{mutant.name}: its anchor appears {found} "
                                 f"time(s) in {mutant.module}")
            with self.subTest(name):
                self.assertEqual(wrong, [], "\n".join(wrong))

    def test_every_mutation_would_change_something(self):
        """``old`` equal to ``new`` is a mutant that cannot fail."""
        for name, harness in batteries():
            same = [m.name for m in harness.MUTANTS if m.old == m.new]
            with self.subTest(name):
                self.assertEqual(same, [])

    def test_every_mutant_names_a_test_that_exists(self):
        """A guard pointed at a test nobody wrote guards nothing."""
        import importlib

        for name, harness in batteries():
            missing = []
            for mutant in harness.MUTANTS:
                target = getattr(mutant, "test", "") or ""
                if not target:
                    continue
                module_name = target.split(".")[0] + "." + target.split(".")[1]
                try:
                    importlib.import_module(module_name)
                except Exception as exc:                  # pragma: no cover
                    missing.append(f"{mutant.name}: {module_name} ({exc})")
            with self.subTest(name):
                self.assertEqual(missing, [], "\n".join(missing))


if __name__ == "__main__":
    unittest.main()
