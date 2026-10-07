"""The AI backend is described accurately, and the model choice is legal.

Two defects, both of which the previous Windows experience had, and both of
which would be invisible to a test that only checked the happy path.

**Configured is not working.** ``roflo``'s ``BackendConfig.kind`` is a dataclass
field defaulting to the string ``"ollama"``. An installer that read it and
announced a working AI backend would be reporting the existence of a default
value. The first class below walks the ladder one rung at a time and asserts
that every rung short of the top reports something the owner can act on, and
that none of them reads as ready.

**A cleared licence is not a licence for every size.** Qwen2.5 is not uniformly
Apache-2.0 -- this repository's own model-rights evidence records that the 3B is
``qwen-research``, "FOR NON-COMMERCIAL PURPOSES ONLY", and the 72B carries a
conditional licence. The 3B is also precisely the model a reasonable sizing
algorithm picks for a small VPS: same family, right size, obviously correct.
Picking it would put a business that sells its output in breach of the licence.
The second class makes that trap explicit, including the specific memory
configuration in which 3B fits and must still be refused.
"""

from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "windows" / "installer"))

from tests_solvent.installer_fake_host import (  # noqa: E402
    FakeWindows, OLLAMA_LIST, fresh_windows,
)

from deskpilot_installer import ai  # noqa: E402
from deskpilot_installer.report import Level, Outcome, ready  # noqa: E402

GB = ai.GB
OLLAMA = r"C:\Program Files\Ollama\ollama.exe"


def host_with_ollama(*, installed=True, reachable=True, has_model=True,
                     inference=True) -> FakeWindows:
    """A machine at a chosen rung of the ladder and no higher."""
    host = fresh_windows()
    if not installed:
        return host
    host.add_program("ollama.exe", OLLAMA)
    if not reachable:
        host.on("ollama.exe", "list", returncode=1,
                stderr="could not connect to ollama app")
        return host
    host.on("ollama.exe", "list",
            stdout=OLLAMA_LIST if has_model
            else "NAME    ID    SIZE    MODIFIED\n")
    if inference:
        host.on("ollama.exe", "run", stdout="ok\n")
    else:
        host.on("ollama.exe", "run", returncode=1, stderr="out of memory")
    return host


class TheLadderIsClimbedOneRungAtATime(unittest.TestCase):

    def test_an_unknown_backend_is_absent(self):
        state = ai.assess(fresh_windows(), kind="telepathy",
                          model_tag="qwen2.5:14b-instruct")
        self.assertIs(state.level, Level.ABSENT)
        self.assertFalse(ready(state.level))

    def test_a_supported_backend_with_no_model_is_only_supported(self):
        """The exact shape of the old lie: the backend name is known."""
        state = ai.assess(fresh_windows(), kind="ollama", model_tag="")
        self.assertIs(state.level, Level.SUPPORTED)
        self.assertFalse(ready(state.level))
        self.assertIn("not a working AI backend", state.detail)

    def test_configured_but_not_installed_says_so(self):
        state = ai.assess(host_with_ollama(installed=False), kind="ollama",
                          model_tag="qwen2.5:14b-instruct")
        self.assertIs(state.level, Level.CONFIGURED)
        self.assertFalse(ready(state.level))
        self.assertIn("is not installed on this computer", state.detail)

    def test_installed_but_unreachable_says_so(self):
        state = ai.assess(host_with_ollama(reachable=False), kind="ollama",
                          model_tag="qwen2.5:14b-instruct")
        self.assertIs(state.level, Level.INSTALLED)
        self.assertFalse(ready(state.level))
        self.assertIn("did not answer", state.detail)

    def test_reachable_without_the_model_says_so(self):
        state = ai.assess(host_with_ollama(has_model=False), kind="ollama",
                          model_tag="qwen2.5:14b-instruct")
        self.assertIs(state.level, Level.REACHABLE)
        self.assertFalse(ready(state.level))
        self.assertIn("has not been downloaded", state.detail)

    def test_a_model_that_will_not_answer_is_not_ready(self):
        state = ai.assess(host_with_ollama(inference=False), kind="ollama",
                          model_tag="qwen2.5:14b-instruct")
        self.assertIs(state.level, Level.REACHABLE)
        self.assertFalse(ready(state.level))
        self.assertIn("did not answer a prompt", state.detail)

    def test_only_a_working_inference_is_ready(self):
        state = ai.assess(host_with_ollama(), kind="ollama",
                          model_tag="qwen2.5:14b-instruct")
        self.assertIs(state.level, Level.READY)
        self.assertTrue(ready(state.level))

    def test_skipping_the_inference_probe_stops_short_of_ready(self):
        """A check that did not test inference must not claim it passed."""
        state = ai.assess(host_with_ollama(), kind="ollama",
                          model_tag="qwen2.5:14b-instruct",
                          probe_inference=False)
        self.assertIs(state.level, Level.REACHABLE)
        self.assertIn("inference was not tested", state.detail)

    def test_the_echo_backend_is_never_ready(self):
        """It answers instantly and is a test double, not a language model."""
        state = ai.assess(fresh_windows(), kind="echo", model_tag="anything")
        self.assertIs(state.level, Level.CONFIGURED)
        self.assertFalse(ready(state.level))
        self.assertIn("test double", state.detail)

    def test_every_rung_is_recorded_as_evidence(self):
        state = ai.assess(host_with_ollama(), kind="ollama",
                          model_tag="qwen2.5:14b-instruct")
        rungs = [name for name, _ok, _note in state.evidence]
        self.assertEqual(rungs, ["supported by roflo", "configured by the owner",
                                 "installed on this computer",
                                 "service reachable", "model present",
                                 "inference works"])
        self.assertTrue(all(ok for _n, ok, _d in state.evidence))

    def test_only_ready_produces_a_passing_row(self):
        for level_host, expect in ((host_with_ollama(), Outcome.PASS),
                                   (host_with_ollama(installed=False),
                                    Outcome.ACTION),
                                   (host_with_ollama(reachable=False),
                                    Outcome.ACTION),
                                   (host_with_ollama(has_model=False),
                                    Outcome.ACTION)):
            state = ai.assess(level_host, kind="ollama",
                              model_tag="qwen2.5:14b-instruct")
            self.assertEqual(ai.row(state).outcome, expect, state.detail)

    def test_failed_is_outside_the_ladder_not_merely_low(self):
        """A failure must not compare as "below ready" and become a warning."""
        self.assertFalse(Level.FAILED.at_least(Level.SUPPORTED))
        self.assertFalse(Level.READY.at_least(Level.FAILED))
        self.assertTrue(Level.FAILED.at_least(Level.FAILED))

    def test_the_model_tag_match_tolerates_the_latest_suffix(self):
        self.assertTrue(ai._tag_present(
            "NAME\nqwen2.5:14b-instruct    abc\n", "qwen2.5:14b-instruct"))
        self.assertFalse(ai._tag_present(
            "NAME\nqwen2.5:7b-instruct    abc\n", "qwen2.5:14b-instruct"))


class TheNonCommercialModelIsRefused(unittest.TestCase):
    """The 3B trap, in the configuration where it would actually be chosen."""

    def test_the_catalogue_records_the_licence_per_size(self):
        by_tag = {o.tag: o for o in ai.CATALOGUE}
        self.assertEqual(by_tag["qwen2.5:3b-instruct"].licence, "qwen-research")
        self.assertFalse(by_tag["qwen2.5:3b-instruct"].commercial)
        self.assertEqual(by_tag["qwen2.5:14b-instruct"].licence, "apache-2.0")
        self.assertTrue(by_tag["qwen2.5:14b-instruct"].commercial)

    def test_the_catalogue_agrees_with_the_model_rights_evidence(self):
        """Pinned against the document that did the legal work.

        If the evidence is ever revised, this fails and someone has to look at
        both, which is the correct outcome for a licence conclusion.
        """
        evidence = (ROOT / "docs" / "solvent-model-rights-evidence.md").read_text()
        self.assertIn("qwen-research", evidence)
        self.assertIn("FOR NON-COMMERCIAL PURPOSES ONLY", evidence)
        self.assertIn("Qwen2.5-3B-Instruct", evidence)

    def test_a_small_machine_is_not_given_the_three_b(self):
        """8 GB with MetaTrader: 3 GB to spare, and 3B needs 2.5 GB.

        It fits. It is refused anyway, and something smaller is chosen.
        """
        rec = ai.recommend(8 * GB, 60 * GB, mt5_present=True)
        self.assertNotEqual(rec.tag, "qwen2.5:3b-instruct")
        self.assertEqual(rec.tag, "qwen2.5:0.5b-instruct")
        reasons = dict(rec.rejected)
        self.assertIn("qwen2.5:3b-instruct", reasons)
        self.assertIn("licence", reasons["qwen2.5:3b-instruct"])

    def test_the_three_b_is_refused_at_every_memory_size(self):
        for gigabytes in (4, 6, 8, 12, 16, 32, 64, 128):
            rec = ai.recommend(gigabytes * GB, 500 * GB, mt5_present=False)
            self.assertNotEqual(rec.tag, "qwen2.5:3b-instruct",
                                f"{gigabytes} GB chose the non-commercial model")

    def test_recording_a_clearance_for_it_is_refused(self):
        """Even if something upstream selected it, the step refuses."""
        from deskpilot_installer.engine import Engine, OwnerAnswers, StepFailed
        from deskpilot_installer.installlog import Log
        from deskpilot_installer.layout import Layout
        from deskpilot_installer.probe import Settings
        from deskpilot_installer.report import Secret
        host = fresh_windows()
        engine = Engine(host, Settings(layout=Layout()),
                        OwnerAnswers(owner_identity="Jo",
                                     web_password=Secret("correct-horse-batt"),
                                     ai_model_tag="qwen2.5:3b-instruct"),
                        Log(host))
        with self.assertRaises(StepFailed) as caught:
            engine.step_model_rights()
        self.assertIn("does not permit commercial use", caught.exception.detail)


class SizingLeavesRoomForOtherSoftware(unittest.TestCase):

    def test_a_large_machine_gets_a_large_model(self):
        rec = ai.recommend(64 * GB, 500 * GB, mt5_present=False)
        self.assertEqual(rec.tag, "qwen2.5:72b-instruct")

    def test_metatrader_reduces_the_budget(self):
        """The same machine must not be filled when a terminal is running.

        14 GB is chosen because it straddles a boundary: the 14B model needs
        about 10 GB, which fits in 14 GB once the operating system and
        DeskPilot are reserved for, and does not fit once MetaTrader is too. A
        size that did not straddle one would pass whatever the reserve did.
        """
        without = ai.recommend(14 * GB, 200 * GB, mt5_present=False)
        with_mt5 = ai.recommend(14 * GB, 200 * GB, mt5_present=True)
        self.assertEqual(without.tag, "qwen2.5:14b-instruct")
        self.assertEqual(with_mt5.tag, "qwen2.5:7b-instruct")
        self.assertGreater(ai.reserve_bytes(True), ai.reserve_bytes(False))

    def test_a_tiny_machine_is_sent_to_the_cloud_rather_than_degraded(self):
        rec = ai.recommend(3 * GB, 200 * GB, mt5_present=True)
        self.assertTrue(rec.cloud_instead)
        self.assertIsNone(rec.model)
        self.assertIn("cloud", rec.reason.lower())

    def test_the_reserve_is_never_spent(self):
        """Whatever is chosen must fit inside total minus the reserve."""
        for gigabytes in (4, 8, 16, 32, 64, 256):
            for mt5 in (True, False):
                rec = ai.recommend(gigabytes * GB, 1000 * GB, mt5_present=mt5)
                if rec.model is None:
                    continue
                budget = gigabytes * GB - ai.reserve_bytes(mt5)
                self.assertLessEqual(rec.model.ram_bytes, budget)

    def test_a_full_disk_rules_out_a_model_that_memory_would_allow(self):
        rec = ai.recommend(64 * GB, 5 * GB, mt5_present=False)
        self.assertIsNotNone(rec.model)
        self.assertLessEqual(rec.model.disk_bytes, 5 * GB)

    def test_a_slow_processor_is_mentioned_rather_than_hidden(self):
        rec = ai.recommend(32 * GB, 500 * GB, mt5_present=False, cpu_count=1)
        self.assertIn("answer slowly", rec.reason)

    def test_only_the_evidenced_model_can_be_cleared_unattended(self):
        """The installer must not invent a digest to clear a model with."""
        autoclearable = [o.tag for o in ai.CATALOGUE if o.can_autoclear]
        self.assertEqual(autoclearable, ["qwen2.5:14b-instruct"])

    def test_the_autoclearable_digest_matches_the_applications_default(self):
        import re
        cli = (ROOT / "solvent" / "cli.py").read_text()
        digest = ai.QWEN14B_DIGEST.split("sha256:")[1]
        flattened = re.sub(r'"\s*\n\s*"', "", cli)
        self.assertIn(digest, flattened,
                      "the installer's digest must be the one solvent "
                      "setup model already defaults to")


if __name__ == "__main__":
    unittest.main()
