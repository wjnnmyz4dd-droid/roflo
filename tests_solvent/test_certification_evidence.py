"""How much evidence before trusting a verifier for a check?

Written against the failure that motivated it. Four verifiers were built whose
entire implementation was a list of the certification battery's own fixtures —
one decided a total was correct whenever it was not ``999.99`` — and all four
earned full CERTIFIED and were trusted for the check they faked. A fixed, small,
public battery measures familiarity with the battery.

So these tests are mostly about what certification *refuses*, and the fitted
verifiers are kept as permanent adversaries rather than deleted once fixed.
"""

from __future__ import annotations

import json
import random
import unittest
from pathlib import Path

from solvent import certgen, csvverify, reportverify, verifiercert as vc
from solvent.capability import CapabilityRegistry
from solvent.errors import FailClosed
from solvent.harness import (
    CAPABILITIES, CERTIFICATION_CASES, OWNER, Solvent,
    ensure_verifier_certified, surprise_audit, verifier_ref,
)
from solvent.types import CheckResult

CASES = CERTIFICATION_CASES


def outcome(passed: bool, detail: str = "TEST_ONLY"):
    class _Outcome:
        result = CheckResult.PASS if passed else CheckResult.FAIL
        computed: dict = {}
    _Outcome.passed = passed
    _Outcome.detail = detail
    return _Outcome()


def read(path) -> str:
    return Path(path).read_text(encoding="utf-8-sig")


# --------------------------------------------------------- fitted adversaries
#
# Each of these was CERTIFIED by the old fixed battery and trusted for the check
# it fakes. They are kept because a defence is only demonstrated against the
# attack it was built for, and deleting them would leave the floor asserting
# numbers with nothing pushing against them.

def fitted_last_row_dupe(check, *, source, output, params):
    """Catches a duplicate only when it is the final row."""
    if check == "drop_exact_duplicates":
        rows = [r for r in read(output).splitlines() if r.strip()][1:]
        return outcome(not (rows and rows[-1] in rows[:-1]))
    return csvverify.run(check, source=source, output=output, params=params)


def fitted_total_not_999(check, *, source, output, params):
    """"Correct" means "not the wrong total I was shown in testing"."""
    if check == "report_total_correct":
        text = read(output)
        return outcome("999.99" not in text and "450.00" not in text)
    return reportverify.run(check, source=source, output=output, params=params)


def fitted_not_rivera(check, *, source, output, params):
    if check == "report_missing_disclosed":
        text = read(output)
        if "J. Rivera" in text:
            return outcome(False)
        return outcome("approved_by" in text)
    return reportverify.run(check, source=source, output=output, params=params)


def fitted_not_corporation(check, *, source, output, params):
    if check == "report_facts_rendered":
        text = read(output)
        for known in ("Acme Corporation", "Acme Holdings", "Q1 2026 (provisional)"):
            if known in text:
                return outcome(False)
        return outcome(True)
    return reportverify.run(check, source=source, output=output, params=params)


def weak_substring_facts(check, *, source, output, params):
    """Substring matching: the defect this whole line of work started from."""
    if check == "report_facts_rendered":
        from solvent.reportwork import load_source

        text = read(output)
        facts, rows, problem = load_source(source)
        if problem:
            return outcome(False)
        for name in params.get("fields") or []:
            value = facts.get(name)
            if value is not None and f"- {name}: {value}" not in text:
                return outcome(False)
        return outcome(True)
    return reportverify.run(check, source=source, output=output, params=params)


def weak_presence_only(check, *, source, output, params):
    """The field is mentioned, so it must be right."""
    if check == "report_facts_rendered":
        text = read(output)
        for name in params.get("fields") or []:
            if f"- {name}:" not in text:
                return outcome(False)
        return outcome(True)
    return reportverify.run(check, source=source, output=output, params=params)


def weak_ignores_source(check, *, source, output, params):
    """Judges the deliverable in isolation; never opens the source."""
    if check in ("require_columns", "normalise_dates", "trim_whitespace",
                 "map_values", "parses_as_csv"):
        return csvverify.run(check, source=output, output=output, params=params)
    return outcome(True)


def weak_rounding_tolerant(check, *, source, output, params):
    if check == "preserve_columns":
        return outcome(True)
    return csvverify.run(check, source=source, output=output, params=params)


ADVERSARIES = (
    ("csv-cleanup", "fitted-last-row-dupe", fitted_last_row_dupe,
     "drop_exact_duplicates"),
    ("report-builder", "fitted-total-not-999", fitted_total_not_999,
     "report_total_correct"),
    ("report-builder", "fitted-not-rivera", fitted_not_rivera,
     "report_missing_disclosed"),
    ("report-builder", "fitted-not-corporation", fitted_not_corporation,
     "report_facts_rendered"),
    ("report-builder", "weak-substring-facts", weak_substring_facts,
     "report_facts_rendered"),
    ("report-builder", "weak-presence-only", weak_presence_only,
     "report_facts_rendered"),
    ("csv-cleanup", "weak-ignores-source", weak_ignores_source,
     "drop_exact_duplicates"),
    ("csv-cleanup", "weak-rounding-tolerant", weak_rounding_tolerant,
     "preserve_columns"),
)


def certify(solvent, label, capability, fn, seed=None):
    report = vc.run_generated(fn, capability=capability, verifier_ref=label,
                              seed=seed or certgen.CERTIFICATION_SEED, cases=CASES)
    record = solvent.capability.certify_verifier(
        verifier_ref=label, capability_version=f"{label}/1.0", report=report,
        fingerprint=vc.implementation_fingerprint(fn))
    return record, report


class TheGeneratedEvidenceIsSound(unittest.TestCase):
    """Guards everything else: a battery whose answers are wrong proves nothing."""

    RUNNERS = {"csv-cleanup": csvverify.run, "report-builder": reportverify.run}

    def test_the_real_verifiers_accept_every_correct_artifact(self):
        """A false reject here would mean the generator, not the verifier, is wrong."""
        for capability, run in self.RUNNERS.items():
            report = vc.run_generated(run, capability=capability,
                                      verifier_ref="real",
                                      seed=certgen.CERTIFICATION_SEED, cases=CASES)
            with self.subTest(capability=capability):
                self.assertEqual(
                    [(r.trial.check, r.detail) for r in report.false_rejects], [])

    def test_the_real_verifiers_reject_every_injected_defect(self):
        for capability, run in self.RUNNERS.items():
            report = vc.run_generated(run, capability=capability,
                                      verifier_ref="real",
                                      seed=certgen.CERTIFICATION_SEED, cases=CASES)
            with self.subTest(capability=capability):
                self.assertEqual(
                    [(r.trial.check, r.trial.defect_class)
                     for r in report.false_accepts], [])

    def test_a_case_is_reproducible_from_its_recorded_seed(self):
        first = certgen.generate("csv-cleanup", seed=certgen.CERTIFICATION_SEED,
                                 cases=4)
        second = certgen.generate("csv-cleanup", seed=certgen.CERTIFICATION_SEED,
                                  cases=4)
        self.assertEqual([t.output_text for t in first],
                         [t.output_text for t in second])

    def test_different_seeds_give_materially_different_cases(self):
        one = {(t.source_text, t.output_text) for t in certgen.generate(
            "csv-cleanup", seed=certgen.CERTIFICATION_SEED, cases=6)}
        two = {(t.source_text, t.output_text) for t in certgen.generate(
            "csv-cleanup", seed=certgen.SURPRISE_SEED, cases=6)}
        self.assertEqual(one & two, set())

    def test_the_three_streams_share_no_case_at_all(self):
        """Development, certification and surprise must not overlap.

        Compared as (source, artifact) pairs rather than artifacts alone. One
        defect class deletes the whole document, and an empty file is an empty
        file in every stream — that is the defect working, not a shared case.
        What must never repeat is the *job*: the source and what was made of it.
        """
        streams = {}
        for name, seed in (("development", certgen.DEVELOPMENT_SEED),
                           ("certification", certgen.CERTIFICATION_SEED),
                           ("surprise", certgen.SURPRISE_SEED)):
            streams[name] = {(t.source_text, t.output_text)
                             for t in certgen.generate("report-builder",
                                                       seed=seed, cases=8)}
        names = sorted(streams)
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                with self.subTest(pair=(a, b)):
                    self.assertEqual(streams[a] & streams[b], set())

    def test_no_stream_shares_a_source_with_another(self):
        sources = {}
        for name, seed in (("development", certgen.DEVELOPMENT_SEED),
                           ("certification", certgen.CERTIFICATION_SEED),
                           ("surprise", certgen.SURPRISE_SEED)):
            sources[name] = {t.source_text for t in certgen.generate(
                "csv-cleanup", seed=seed, cases=8)}
        names = sorted(sources)
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                with self.subTest(pair=(a, b)):
                    self.assertEqual(sources[a] & sources[b], set())

    def test_the_battery_is_not_volume_from_near_duplicates(self):
        """Ten copies of one fixture are one piece of evidence, not ten."""
        for capability in ("csv-cleanup", "report-builder"):
            trials = certgen.generate(capability,
                                      seed=certgen.CERTIFICATION_SEED, cases=CASES)
            good = [t for t in trials if t.expect == certgen.GOOD]
            with self.subTest(capability=capability):
                # Every case's correct artifact is distinct from every other's.
                self.assertEqual(len({t.output_text for t in good}), CASES)

    def test_each_defect_class_appears_at_distinct_places_and_values(self):
        trials = certgen.generate("csv-cleanup",
                                  seed=certgen.CERTIFICATION_SEED, cases=CASES)
        by_class: dict = {}
        for trial in trials:
            if trial.expect == certgen.BAD:
                by_class.setdefault(trial.defect_class, set()).add(trial.output_text)
        for name, artifacts in by_class.items():
            with self.subTest(defect=name):
                self.assertGreaterEqual(
                    len(artifacts), 5,
                    f"{name} has too few distinct instances to mean anything")

    def test_the_battery_is_substantial(self):
        for capability in ("csv-cleanup", "report-builder"):
            trials = certgen.generate(capability,
                                      seed=certgen.CERTIFICATION_SEED, cases=CASES)
            with self.subTest(capability=capability):
                self.assertGreater(len(trials), 100)

    def test_both_directions_are_represented_for_every_check(self):
        for capability in ("csv-cleanup", "report-builder"):
            trials = certgen.generate(capability,
                                      seed=certgen.CERTIFICATION_SEED, cases=CASES)
            good = {t.check for t in trials if t.expect == certgen.GOOD}
            bad = {t.check for t in trials if t.expect == certgen.BAD}
            with self.subTest(capability=capability):
                self.assertEqual(good - bad, set(),
                                 "a check is only ever shown correct work")


class TheEvidenceFloorRefusesALuckyVerifier(unittest.TestCase):
    """The point of the exercise."""

    def setUp(self):
        self.s = Solvent()

    def test_every_fitted_verifier_is_refused_for_the_check_it_fakes(self):
        for capability, label, fn, faked in ADVERSARIES:
            certify(self.s, label, capability, fn)
            allowed, why = self.s.capability.may_authorize_delivery(
                verifier_ref=label, capability_version=f"{label}/1.0",
                check=faked, fingerprint=vc.implementation_fingerprint(fn))
            with self.subTest(verifier=label):
                self.assertFalse(allowed, f"{label} is trusted for {faked}")

    def test_a_verifier_that_hardcodes_one_value_catches_almost_nothing(self):
        record, report = certify(self.s, "fitted-total-not-999", "report-builder",
                                 fitted_total_not_999)
        caught = report.class_instances("report_total_correct")
        self.assertLess(caught.get("WRONG_NUMBER", 0),
                        self.s.capability.MIN_INSTANCES_PER_DEFECT)

    def test_approve_everything_still_fails_outright(self):
        record, _ = certify(self.s, "approve-all", "csv-cleanup",
                            lambda c, **k: outcome(True))
        self.assertEqual(record["state"], "FAILED")
        self.assertGreater(record["false_accepts"], 0)

    def test_reject_everything_still_fails_outright(self):
        record, _ = certify(self.s, "reject-all", "csv-cleanup",
                            lambda c, **k: outcome(False))
        self.assertEqual(record["state"], "FAILED")
        self.assertGreater(record["false_rejects"], 0)

    def test_a_check_shown_no_defect_is_not_certified(self):
        """Saying yes to correct work is not evidence of anything."""
        report = vc.TrialReport(capability="x", verifier_ref="y")
        for index in range(40):
            report.results.append(vc.TrialResult(
                vc.Trial(name=f"good-{index}", check="c", expect=vc.GOOD,
                         defect_class="CORRECT"), "ACCEPT"))
        certified, short = self.s.capability._checks_meeting_the_floor(report)
        self.assertEqual(certified, [])
        self.assertIn("no defect was ever put in front of it", " ".join(short))

    def test_a_verifier_right_about_a_small_battery_is_still_not_certified(self):
        """The floor's own job, isolated from the generated battery's.

        Mutation probes showed that against the generated adversaries, the
        both-directions rule alone already refuses every one — so weakening the
        floor changed nothing and no test noticed. The floor exists for a
        different case: a verifier that is right about *everything it was
        shown*, when what it was shown was four examples. That is exactly the
        situation the old fixed battery created, and it has to be tested with a
        small battery or not at all.
        """
        report = vc.TrialReport(capability="small", verifier_ref="lucky")
        for index in range(2):
            report.results.append(vc.TrialResult(
                vc.Trial(name=f"good-{index}", check="c", expect=vc.GOOD,
                         defect_class="CORRECT",
                         content_digest=f"g{index}"), "ACCEPT"))
            report.results.append(vc.TrialResult(
                vc.Trial(name=f"bad-{index}", check="c", expect=vc.BAD,
                         defect_class="SOME_DEFECT",
                         content_digest=f"b{index}"), "REJECT"))
        self.assertEqual(report.false_accepts, [])
        self.assertEqual(report.false_rejects, [])
        self.assertEqual(report.checks_passed(), {"c"},
                         "it was right in both directions, which is the point")

        certified, short = self.s.capability._checks_meeting_the_floor(report)
        self.assertEqual(certified, [],
                         "a flawless record over four examples was certified")
        self.assertTrue(short)

    def test_the_shortfall_says_which_floor_was_missed(self):
        report = vc.TrialReport(capability="small", verifier_ref="lucky")
        for index in range(20):
            report.results.append(vc.TrialResult(
                vc.Trial(name=f"good-{index}", check="c", expect=vc.GOOD,
                         defect_class="CORRECT",
                         content_digest=f"g{index}"), "ACCEPT"))
        for index in range(2):
            report.results.append(vc.TrialResult(
                vc.Trial(name=f"bad-{index}", check="c", expect=vc.BAD,
                         defect_class="SOME_DEFECT",
                         content_digest=f"b{index}"), "REJECT"))
        certified, short = self.s.capability._checks_meeting_the_floor(report)
        self.assertEqual(certified, [])
        self.assertIn("SOME_DEFECT", " ".join(short))
        self.assertIn("fewer than 5", " ".join(short))

    def test_enough_distinct_instances_does_certify(self):
        """Guards the two tests above: a floor nothing clears proves nothing."""
        report = vc.TrialReport(capability="small", verifier_ref="thorough")
        for index in range(12):
            report.results.append(vc.TrialResult(
                vc.Trial(name=f"good-{index}", check="c", expect=vc.GOOD,
                         defect_class="CORRECT",
                         content_digest=f"g{index}"), "ACCEPT"))
        for index in range(6):
            report.results.append(vc.TrialResult(
                vc.Trial(name=f"bad-{index}", check="c", expect=vc.BAD,
                         defect_class="SOME_DEFECT",
                         content_digest=f"b{index}"), "REJECT"))
        certified, short = self.s.capability._checks_meeting_the_floor(report)
        self.assertEqual(certified, ["c"], short)

    def test_repeating_one_artifact_is_not_repeated_evidence(self):
        """Ten copies of one fixture are one piece of evidence, not ten."""
        report = vc.TrialReport(capability="small", verifier_ref="padded")
        for index in range(20):
            report.results.append(vc.TrialResult(
                vc.Trial(name=f"good-{index}", check="c", expect=vc.GOOD,
                         defect_class="CORRECT",
                         content_digest="always-the-same"), "ACCEPT"))
        for index in range(20):
            report.results.append(vc.TrialResult(
                vc.Trial(name=f"bad-{index}", check="c", expect=vc.BAD,
                         defect_class="SOME_DEFECT",
                         content_digest="also-always-the-same"), "REJECT"))
        self.assertEqual(report.good_instances("c"), 1)
        self.assertEqual(report.class_instances("c"), {"SOME_DEFECT": 1})
        certified, short = self.s.capability._checks_meeting_the_floor(report)
        self.assertEqual(certified, [],
                         "forty copies of two fixtures certified a check")

    def test_certify_verifier_actually_applies_the_floor(self):
        """The wiring, not the rule.

        The tests above call the floor directly, so removing its *call* from
        ``certify_verifier`` left them all passing. A control that is correct
        and not reached is not a control.
        """
        report = vc.TrialReport(capability="small", verifier_ref="lucky-small")
        for index in range(2):
            report.results.append(vc.TrialResult(
                vc.Trial(name=f"good-{index}", check="c", expect=vc.GOOD,
                         defect_class="CORRECT",
                         content_digest=f"g{index}"), "ACCEPT"))
            report.results.append(vc.TrialResult(
                vc.Trial(name=f"bad-{index}", check="c", expect=vc.BAD,
                         defect_class="SOME_DEFECT",
                         content_digest=f"b{index}"), "REJECT"))

        record = self.s.capability.certify_verifier(
            verifier_ref="lucky-small", capability_version="lucky-small/1.0",
            report=report, fingerprint="fp")
        self.assertEqual(record["certified_checks"], "",
                         "a flawless four-example record was certified")
        allowed, why = self.s.capability.may_authorize_delivery(
            verifier_ref="lucky-small", capability_version="lucky-small/1.0",
            check="c", fingerprint="fp")
        self.assertFalse(allowed, why)

    def test_certify_verifier_certifies_when_the_floor_is_met(self):
        """Guards the test above: a wiring that refuses everything proves nothing."""
        report = vc.TrialReport(capability="small", verifier_ref="thorough-small")
        for index in range(12):
            report.results.append(vc.TrialResult(
                vc.Trial(name=f"good-{index}", check="c", expect=vc.GOOD,
                         defect_class="CORRECT",
                         content_digest=f"g{index}"), "ACCEPT"))
        for index in range(6):
            report.results.append(vc.TrialResult(
                vc.Trial(name=f"bad-{index}", check="c", expect=vc.BAD,
                         defect_class="SOME_DEFECT",
                         content_digest=f"b{index}"), "REJECT"))
        record = self.s.capability.certify_verifier(
            verifier_ref="thorough-small",
            capability_version="thorough-small/1.0", report=report,
            fingerprint="fp")
        self.assertEqual(record["state"], "CERTIFIED", record["why"])
        allowed, why = self.s.capability.may_authorize_delivery(
            verifier_ref="thorough-small",
            capability_version="thorough-small/1.0", check="c", fingerprint="fp")
        self.assertTrue(allowed, why)

    def test_padding_a_battery_with_copies_does_not_certify(self):
        """Through certify_verifier, not the helper: forty trials, two fixtures."""
        report = vc.TrialReport(capability="small", verifier_ref="padded-small")
        for index in range(20):
            report.results.append(vc.TrialResult(
                vc.Trial(name=f"good-{index}", check="c", expect=vc.GOOD,
                         defect_class="CORRECT",
                         content_digest="one-and-only"), "ACCEPT"))
            report.results.append(vc.TrialResult(
                vc.Trial(name=f"bad-{index}", check="c", expect=vc.BAD,
                         defect_class="SOME_DEFECT",
                         content_digest="also-one-and-only"), "REJECT"))
        record = self.s.capability.certify_verifier(
            verifier_ref="padded-small", capability_version="padded-small/1.0",
            report=report, fingerprint="fp")
        self.assertEqual(record["certified_checks"], "")

    def test_the_floor_numbers_are_stated_not_implicit(self):
        self.assertEqual(self.s.capability.MIN_INSTANCES_PER_DEFECT, 5)
        self.assertEqual(self.s.capability.MIN_GOOD_INSTANCES, 10)

    def test_a_real_verifier_clears_the_floor_on_every_check(self):
        """Guards the tests above: an unclearable floor refuses everything."""
        for capability, run in (("csv-cleanup", csvverify.run),
                                ("report-builder", reportverify.run)):
            report = vc.run_generated(run, capability=capability,
                                      verifier_ref="real",
                                      seed=certgen.CERTIFICATION_SEED, cases=CASES)
            certified, short = self.s.capability._checks_meeting_the_floor(report)
            with self.subTest(capability=capability):
                self.assertEqual(short, [])
                self.assertEqual(set(certified), report.checks_exercised)


class CertificationIsPerCheckNotReputation(unittest.TestCase):
    def setUp(self):
        self.s = Solvent()

    def test_a_verifier_weak_on_one_check_keeps_the_others(self):
        label = "weak-rounding-tolerant"
        certify(self.s, label, "csv-cleanup", weak_rounding_tolerant)
        fingerprint = vc.implementation_fingerprint(weak_rounding_tolerant)
        refused, _ = self.s.capability.may_authorize_delivery(
            verifier_ref=label, capability_version=f"{label}/1.0",
            check="preserve_columns", fingerprint=fingerprint)
        allowed, why = self.s.capability.may_authorize_delivery(
            verifier_ref=label, capability_version=f"{label}/1.0",
            check="drop_exact_duplicates", fingerprint=fingerprint)
        self.assertFalse(refused, "it is trusted on the check it fakes")
        self.assertTrue(allowed, why)

    def test_partial_success_does_not_become_blanket_trust(self):
        label = "weak-substring-facts"
        record, _ = certify(self.s, label, "report-builder", weak_substring_facts)
        self.assertEqual(record["state"], "CERTIFIED_WITH_LIMITS")
        certified = [c for c in record["certified_checks"].split(",") if c]
        self.assertNotIn("report_facts_rendered", certified)
        self.assertTrue(certified, "it should keep the checks it delegates")


class CertificationIsBoundToTheImplementation(unittest.TestCase):
    """A name is not code, and a caller-supplied label manufactures nothing."""

    def setUp(self):
        self.s = Solvent()

    def test_two_different_callables_fingerprint_differently(self):
        self.assertNotEqual(vc.implementation_fingerprint(csvverify.run),
                            vc.implementation_fingerprint(reportverify.run))

    def test_two_lambdas_in_one_module_fingerprint_differently(self):
        """They once did not, and silently shared a measurement."""
        first = lambda c, **k: outcome(True)      # noqa: E731
        def second(c, **k):
            return outcome(False)
        self.assertNotEqual(vc.implementation_fingerprint(first),
                            vc.implementation_fingerprint(second))

    def test_editing_the_verifier_invalidates_its_certification(self):
        certify(self.s, "v", "csv-cleanup", csvverify.run)
        allowed, why = self.s.capability.may_authorize_delivery(
            verifier_ref="v", capability_version="v/1.0",
            check="drop_exact_duplicates",
            fingerprint="sha256:something-else-entirely")
        self.assertFalse(allowed)
        self.assertIn("different code", why)

    def test_a_spoofed_name_earns_nothing(self):
        """Registering under a trusted name does not inherit its evidence."""
        certify(self.s, "solvent.csvverify.run", "csv-cleanup", csvverify.run)
        allowed, why = self.s.capability.may_authorize_delivery(
            verifier_ref="solvent.csvverify.run",
            capability_version="solvent.csvverify.run/1.0",
            check="drop_exact_duplicates",
            fingerprint=vc.implementation_fingerprint(fitted_last_row_dupe))
        self.assertFalse(allowed, "a different implementation used a trusted name")

    def test_certification_is_not_shared_across_capability_versions(self):
        certify(self.s, "v2", "csv-cleanup", csvverify.run)
        allowed, why = self.s.capability.may_authorize_delivery(
            verifier_ref="v2", capability_version="csv-cleanup/9.9",
            check="drop_exact_duplicates",
            fingerprint=vc.implementation_fingerprint(csvverify.run))
        self.assertFalse(allowed)
        self.assertIn("never been tested", why)

    def test_a_certification_with_no_fingerprint_cannot_vouch_for_code(self):
        report = vc.run_generated(csvverify.run, capability="csv-cleanup",
                                  verifier_ref="nofp",
                                  seed=certgen.CERTIFICATION_SEED, cases=CASES)
        self.s.capability.certify_verifier(
            verifier_ref="nofp", capability_version="nofp/1.0", report=report)
        allowed, why = self.s.capability.may_authorize_delivery(
            verifier_ref="nofp", capability_version="nofp/1.0",
            check="drop_exact_duplicates",
            fingerprint=vc.implementation_fingerprint(csvverify.run))
        self.assertFalse(allowed)
        self.assertIn("no way to tell", why)


class TheSurpriseBatteryKeepsThemHonest(unittest.TestCase):
    """Certification says it was right about what it was shown."""

    def setUp(self):
        self.s = Solvent()

    def test_the_real_verifiers_survive_an_unseen_battery(self):
        for capability in ("csv-cleanup", "report-builder"):
            ensure_verifier_certified(self.s, capability)
            with self.subTest(capability=capability):
                result = surprise_audit(self.s, capability, cases=8)
                self.assertTrue(result["passed"], result.get("wrong"))

    def test_a_verifier_that_fails_the_surprise_is_revoked_not_patched(self):
        from solvent.harness import ServiceCapability, _report_derived
        from solvent import checks, reportwork

        CAPABILITIES["surprise-victim"] = ServiceCapability(
            version="surprise-victim/0.1", execute=reportwork.build,
            verify=weak_presence_only, plan=None, derive=_report_derived,
            extension="md")
        checks.REGISTRY["surprise-victim"] = checks.REGISTRY["report-builder"]
        self.addCleanup(CAPABILITIES.pop, "surprise-victim", None)
        self.addCleanup(checks.REGISTRY.pop, "surprise-victim", None)

        ensure_verifier_certified(self.s, "surprise-victim")
        result = surprise_audit(self.s, "surprise-victim", cases=8)
        self.assertFalse(result["passed"])
        self.assertEqual(result["state"], "REVOKED")

    def test_the_revocation_names_what_went_wrong(self):
        self.test_a_verifier_that_fails_the_surprise_is_revoked_not_patched()
        history = [r for r in self.s.store.raw_readonly(
            "SELECT * FROM verifier_certifications") if r["state"] == "REVOKED"]
        self.assertTrue(history)
        self.assertIn("surprise battery", history[-1]["why"])

    def test_the_surprise_seed_is_recorded_so_a_failure_can_be_replayed(self):
        self.assertTrue(certgen.SURPRISE_SEED)
        self.assertNotEqual(certgen.SURPRISE_SEED, certgen.CERTIFICATION_SEED)


class RevocationIsNotSelfHealing(unittest.TestCase):
    """A revoked verifier must not be handed its certification back by a job."""

    def setUp(self):
        self.s = Solvent()
        ensure_verifier_certified(self.s, "csv-cleanup")
        self.ref = verifier_ref(CAPABILITIES["csv-cleanup"])
        self.version = CAPABILITIES["csv-cleanup"].version
        self.s.capability.revoke_verifier(
            verifier_ref=self.ref, capability_version=self.version,
            why="demonstrated false PASS on job J-TEST", decided_by="qc")

    def test_running_the_pipeline_again_does_not_undo_the_revocation(self):
        record = ensure_verifier_certified(self.s, "csv-cleanup")
        self.assertEqual(record["state"], "REVOKED")

    def test_a_job_is_refused_while_the_verifier_is_revoked(self):
        from tests_solvent import fixtures_csv as fx
        from solvent.harness import run_csv_job

        source, work = fx.workspace(fx.DUPLICATES)
        with self.assertRaises(FailClosed):
            run_csv_job(source=source, requirements=list(fx.SIMPLE),
                        workdir=work, solvent=self.s)

    def test_re_certifying_is_an_owner_act(self):
        report = vc.run_generated(csvverify.run, capability="csv-cleanup",
                                  verifier_ref=self.ref,
                                  seed=certgen.CERTIFICATION_SEED, cases=CASES)
        for identity in ("qc", "capability", "worker", "execution"):
            with self.subTest(identity=identity):
                with self.assertRaises(FailClosed):
                    self.s.capability.recertify_after_revocation(
                        verifier_ref=self.ref, capability_version=self.version,
                        report=report, fingerprint="fp", owner_identity=identity,
                        why="fixed it")

    def test_re_certification_needs_a_reason(self):
        report = vc.run_generated(csvverify.run, capability="csv-cleanup",
                                  verifier_ref=self.ref,
                                  seed=certgen.CERTIFICATION_SEED, cases=CASES)
        with self.assertRaises(FailClosed):
            self.s.capability.recertify_after_revocation(
                verifier_ref=self.ref, capability_version=self.version,
                report=report, fingerprint="fp", owner_identity=OWNER, why="")

    def test_an_owner_can_re_earn_it_with_fresh_evidence(self):
        report = vc.run_generated(csvverify.run, capability="csv-cleanup",
                                  verifier_ref=self.ref,
                                  seed=certgen.CERTIFICATION_SEED, cases=CASES)
        record = self.s.capability.recertify_after_revocation(
            verifier_ref=self.ref, capability_version=self.version,
            report=report,
            fingerprint=vc.implementation_fingerprint(csvverify.run),
            owner_identity=OWNER, why="root cause fixed and re-tested")
        self.assertEqual(record["state"], "CERTIFIED")

    def test_re_certification_still_has_to_clear_the_floor(self):
        """An owner may decide to re-test. They may not decide the result."""
        report = vc.run_generated(weak_ignores_source, capability="csv-cleanup",
                                  verifier_ref=self.ref,
                                  seed=certgen.CERTIFICATION_SEED, cases=CASES)
        record = self.s.capability.recertify_after_revocation(
            verifier_ref=self.ref, capability_version=self.version,
            report=report, fingerprint="fp-weak", owner_identity=OWNER,
            why="trying a weaker implementation")
        self.assertNotEqual(record["state"], "CERTIFIED")

    def test_the_whole_history_survives(self):
        report = vc.run_generated(csvverify.run, capability="csv-cleanup",
                                  verifier_ref=self.ref,
                                  seed=certgen.CERTIFICATION_SEED, cases=CASES)
        self.s.capability.recertify_after_revocation(
            verifier_ref=self.ref, capability_version=self.version,
            report=report,
            fingerprint=vc.implementation_fingerprint(csvverify.run),
            owner_identity=OWNER, why="root cause fixed")
        states = [r["state"] for r in
                  self.s.capability.verifier_history(self.ref, self.version)]
        self.assertEqual(states, ["CERTIFIED", "REVOKED", "CERTIFIED"])


if __name__ == "__main__":
    unittest.main()


class CountingIsNotIdentity(unittest.TestCase):
    """Checks that used to compare row counts and call that verification.

    The generated battery catches these only by luck: its cross-job cases
    usually have different row counts, so the count comparison rejects them for
    the wrong reason and the source binding is never exercised. Two files with
    *the same* number of rows is the case that separates "the right number of
    rows" from "this source's rows", and it has to be built deliberately.
    """

    HEADER = "Order ID,Customer,Order Date,Region,Units,Total\n"
    MINE = (HEADER
            + "1001,Acme Ltd,2026-01-15,North,10,250.00\n"
            + "1002,Beta LLC,2026-01-16,South,5,200.00\n"
            + "1003,Gamma Corp,2026-01-17,East,8,100.00\n")
    THEIRS = (HEADER
              + "5001,Zeta GmbH,2026-05-01,West,3,900.00\n"
              + "5002,Yarrow Group,2026-05-02,North,7,450.00\n"
              + "5003,Xanthe Ltd,2026-05-03,South,2,125.00\n")

    def setUp(self):
        import tempfile

        self.dir = Path(tempfile.mkdtemp(prefix="count-"))
        self.mine = self.dir / "mine.csv"
        self.theirs = self.dir / "theirs.csv"
        self.mine.write_text(self.MINE, encoding="utf-8")
        self.theirs.write_text(self.THEIRS, encoding="utf-8")

    def run_check(self, name, source, output, **params):
        return csvverify.run(name, source=str(source), output=str(output),
                             params=params)

    def test_the_two_files_have_the_same_number_of_rows(self):
        """Guards everything below: different counts would prove nothing."""
        self.assertEqual(self.MINE.count("\n"), self.THEIRS.count("\n"))

    def test_row_reconciliation_accepts_this_sources_own_rows(self):
        outcome = self.run_check("row_reconciliation", self.mine, self.mine,
                                 duplicates_removed=True)
        self.assertIs(outcome.result, CheckResult.PASS, outcome.detail)

    def test_row_reconciliation_refuses_another_jobs_rows(self):
        outcome = self.run_check("row_reconciliation", self.mine, self.theirs,
                                 duplicates_removed=True)
        self.assertIs(outcome.result, CheckResult.FAIL)
        self.assertIn("not this source's rows", outcome.detail)

    def test_drop_exact_duplicates_accepts_this_sources_own_rows(self):
        outcome = self.run_check("drop_exact_duplicates", self.mine, self.mine)
        self.assertIs(outcome.result, CheckResult.PASS, outcome.detail)

    def test_drop_exact_duplicates_refuses_another_jobs_rows(self):
        outcome = self.run_check("drop_exact_duplicates", self.mine, self.theirs)
        self.assertIs(outcome.result, CheckResult.FAIL)
        self.assertIn("does not appear to be made from this source",
                      outcome.detail)

    def test_a_legitimate_cleanup_still_passes_both(self):
        """The transformations a real job performs must not read as foreign."""
        messy = self.dir / "messy.csv"
        messy.write_text(
            self.HEADER
            + "1001,  Acme Ltd  ,01/15/2026,north,10,250.00\n"
            + "1002,Beta LLC,2026-01-16,SOUTH,5,200.00\n"
            + "1003,Gamma Corp,2026-01-17,East,8,100.00\n"
            + "1001,  Acme Ltd  ,01/15/2026,north,10,250.00\n", encoding="utf-8")
        for check in ("row_reconciliation", "drop_exact_duplicates"):
            with self.subTest(check=check):
                outcome = self.run_check(check, messy, self.mine,
                                         duplicates_removed=True)
                self.assertIs(outcome.result, CheckResult.PASS, outcome.detail)

    def test_rows_tabulated_refuses_another_jobs_table(self):
        import json

        facts = self.dir / "facts.json"
        facts.write_text(json.dumps({
            "client": "Acme Ltd", "period": "Q1 2026",
            "rows": [{"item": "Consulting", "amount": "100.00"},
                     {"item": "Support", "amount": "350.50"}]}), encoding="utf-8")
        foreign = self.dir / "foreign.md"
        foreign.write_text(
            "## Lines\n\n| item | amount |\n|---|---|\n"
            "| Hosting | 900.00 |\n| Retainer | 450.00 |\n", encoding="utf-8")
        outcome = reportverify.run("report_rows_tabulated", source=str(facts),
                                   output=str(foreign), params={})
        self.assertIs(outcome.result, CheckResult.FAIL)
        self.assertIn("do not carry this source's values", outcome.detail)


class TheHarnessItselfIsAttacked(unittest.TestCase):
    """Evidence accounting must not be fooled by cosmetic diversity.

    A battery that can be padded is a battery that will be, the first time
    somebody needs a number to come out higher.
    """

    def setUp(self):
        self.s = Solvent()

    def report_with(self, trials):
        report = vc.TrialReport(capability="attack", verifier_ref="attacker")
        report.results = list(trials)
        return report

    def good(self, index, digest=None, check="c"):
        return vc.TrialResult(
            vc.Trial(name=f"good-{index}", check=check, expect=vc.GOOD,
                     defect_class="CORRECT",
                     content_digest=digest or f"g{index}"), "ACCEPT")

    def bad(self, index, digest=None, check="c", cls="SOME_DEFECT"):
        return vc.TrialResult(
            vc.Trial(name=f"bad-{index}", check=check, expect=vc.BAD,
                     defect_class=cls, content_digest=digest or f"b{index}"),
            "REJECT")

    def test_one_fixture_under_many_names_is_one_piece_of_evidence(self):
        trials = [self.good(i, digest="same") for i in range(40)]
        trials += [self.bad(i, digest="same-bad") for i in range(40)]
        report = self.report_with(trials)
        self.assertEqual(report.good_instances("c"), 1)
        self.assertEqual(report.class_instances("c"), {"SOME_DEFECT": 1})
        self.assertEqual(self.s.capability._checks_meeting_the_floor(report)[0], [])

    def test_many_defect_classes_do_not_substitute_for_instances(self):
        """Twelve classes with one instance each is not evidence of any of them."""
        trials = [self.good(i) for i in range(20)]
        trials += [self.bad(i, cls=f"CLASS_{i}") for i in range(12)]
        report = self.report_with(trials)
        certified, short = self.s.capability._checks_meeting_the_floor(report)
        self.assertEqual(certified, [])
        self.assertIn("fewer than", " ".join(short))

    def test_a_skipped_case_is_not_counted_as_caught(self):
        """A mutation that did not apply must not read as a defect detected."""
        trials = [self.good(i) for i in range(12)]
        trials += [self.bad(i) for i in range(6)]
        # One "bad" trial the verifier accepted: an unapplied mutation looks
        # exactly like this, and it must not be silently forgiven.
        trials.append(vc.TrialResult(
            vc.Trial(name="bad-unapplied", check="c", expect=vc.BAD,
                     defect_class="SOME_DEFECT", content_digest="bx"), "ACCEPT"))
        report = self.report_with(trials)
        self.assertEqual(self.s.capability._checks_meeting_the_floor(report)[0], [])
        self.assertIn("SOME_DEFECT", report.classes_missed("c"))

    def test_a_generator_that_produced_no_defect_is_skipped_not_faked(self):
        """certgen drops a case that cannot carry a defect rather than inventing one."""
        trials = certgen.generate("csv-cleanup",
                                  seed=certgen.CERTIFICATION_SEED, cases=CASES)
        for trial in trials:
            with self.subTest(trial=trial.name):
                if trial.expect == certgen.BAD:
                    self.assertNotEqual(trial.output_text, "")

    def test_no_generated_bad_case_equals_its_own_correct_artifact(self):
        """A 'defect' identical to the correct output is not a defect."""
        for capability in ("csv-cleanup", "report-builder"):
            trials = certgen.generate(capability,
                                      seed=certgen.CERTIFICATION_SEED, cases=CASES)
            good = {(t.source_text, t.output_text) for t in trials
                    if t.expect == certgen.GOOD}
            with self.subTest(capability=capability):
                for trial in trials:
                    if trial.expect != certgen.BAD or trial.defect_class == "CROSS_JOB":
                        continue
                    self.assertNotIn((trial.source_text, trial.output_text), good,
                                     f"{trial.name} is not actually defective")

    def test_the_verifier_is_told_nothing_about_the_expected_answer(self):
        """It receives two paths and the check's parameters. Nothing else."""
        seen = []

        def nosy(check, *, source, output, params):
            seen.append({"check": check, "source": source, "output": output,
                         "params": dict(params)})
            return outcome(True)

        vc.run_generated(nosy, capability="csv-cleanup", verifier_ref="nosy",
                         seed=certgen.CERTIFICATION_SEED, cases=2)
        blob = json.dumps(seen)
        for leak in ("GOOD", "BAD", "expect", "defect_class", "ROW_LOSS",
                     "CORRECT", "certification"):
            with self.subTest(leak=leak):
                self.assertNotIn(leak, blob)

    def test_the_seed_is_not_discoverable_from_the_paths(self):
        seen = []

        def nosy(check, *, source, output, params):
            seen.append(source + "|" + output)
            return outcome(True)

        vc.run_generated(nosy, capability="csv-cleanup", verifier_ref="nosy2",
                         seed=certgen.CERTIFICATION_SEED, cases=2)
        blob = "\n".join(seen)
        self.assertNotIn(str(certgen.CERTIFICATION_SEED), blob)

    def test_holdout_and_development_do_not_quietly_share_a_seed(self):
        seeds = {certgen.DEVELOPMENT_SEED, certgen.CERTIFICATION_SEED,
                 certgen.SURPRISE_SEED}
        self.assertEqual(len(seeds), 3)

    def test_every_defect_class_in_the_battery_names_a_real_check(self):
        """A class assigned to a check nobody runs is evidence of nothing."""
        from solvent import checks

        for capability, defects in certgen.DEFECTS.items():
            known = set(checks.checks_for(capability))
            for defect in defects:
                with self.subTest(capability=capability, defect=defect.name):
                    self.assertIn(defect.check, known)
