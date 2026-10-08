"""The wizard script, checked by reading it, because nothing here can run it.

Three real-world installation failures have now escaped a green Python suite,
and all three lived in the half of the installer that Python tests never
touch: the NSIS wizard and the seam between it and the engine. The third was a
single character. The wizard handed the owner's details over with

    System::Call '...SetEnvironmentVariable(t "DESKPILOT_SETUP_ANSWERS", t r5)...'

after storing them in ``$R5``. The System plugin reads lowercase ``r5`` as
``$5`` and uppercase ``R5`` as ``$R5`` -- NSIS's own bundled headers use both
conventions, ``FileFunc.nsh`` with ``r0`` for ``$0`` and ``Library.nsh`` with
``R4`` for ``$R4``. ``$5`` happened to hold the AI backend name, so the engine
received the word ``ollama`` where a JSON document belonged, fell back to
defaults, and told an owner who had typed a password that no password had been
entered.

No amount of testing the engine would have found that, because the engine was
behaving correctly on the input it was given. What was missing was a test of
the *script*. So these tests parse the wizard and assert the invariants that
the defect violated:

* a ``System::Call`` may not reference a register in the case that means a
  different register from the one the surrounding code wrote;
* a function that uses NSIS's twenty global registers as scratch must save
  them, because its caller cannot know that it does;
* the owner's details must reach the hand-over from the variable that the
  hand-over reads;
* the JSON the wizard assembles must actually parse, including when the owner
  types a quote or a backslash;
* no secret may appear on a command line.

They are static checks and they are honest about it: they prove the script says
the right thing, not that Windows does the right thing with it.
"""

from __future__ import annotations

import json
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
NSI = ROOT / "windows" / "installer" / "DeskPilot-Setup.nsi"

#: Lines that are comments as far as NSIS is concerned.
_COMMENT = re.compile(r"^\s*(;|#)")


def code_lines() -> list[tuple[int, str]]:
    """Every line that NSIS will execute, with its 1-based number."""
    out = []
    for number, line in enumerate(NSI.read_text().splitlines(), 1):
        if _COMMENT.match(line) or not line.strip():
            continue
        out.append((number, line))
    return out


def functions() -> dict[str, list[str]]:
    """Each ``Function`` body, by name."""
    bodies: dict[str, list[str]] = {}
    current = None
    for _number, line in code_lines():
        stripped = line.strip()
        start = re.match(r"^Function\s+([\w.]+)", stripped)
        if start:
            current = start.group(1)
            bodies[current] = []
            continue
        if stripped.startswith("FunctionEnd"):
            current = None
            continue
        if current is not None:
            bodies[current].append(stripped)
    return bodies


#: Registers a statement writes to.
_WRITERS = re.compile(
    r"^(?:StrCpy|Pop|StrLen|IntOp|ReadRegStr|ReadRegDWORD|SearchPath|"
    r"FindFirst|FindNext|EnumRegKey|GetDlgItem|GetFunctionAddress)\s+(\$R?\d)")
_SAVERS = re.compile(r"^(?:Push|Exch)\s+(\$R?\d)")
#: Functions that are entry points: a page callback or a section has no caller
#: whose registers could matter.
_ENTRY_POINTS = {
    "SystemCheckPage", "SystemCheckLeave", "OwnerSetupPage", "OwnerSetupLeave",
    "AiSetupPage", "AiSetupLeave", "VerifyPage", "InitDefaults",
    "OpenControlCentre", "PythonFailed", "BuildAnswers",
    "un.DataChoicePage", "un.DataChoiceLeave", "un.FindPython",
    # Called only from FindPython, which saves the whole register file on
    # their behalf. Asserted below rather than assumed.
    "ScanRegistryHive", "ScanUserProfiles", "TryOne", "ProbePython",
    "FirstPythonPathIn", "HashFile",
}


class TheSystemPluginRegisterConvention(unittest.TestCase):
    """The defect, pinned. ``r5`` and ``R5`` are different registers."""

    def test_no_system_call_uses_a_lowercase_register(self):
        """The wizard keeps its values in ``$R`` registers, so a lowercase
        reference always means a register nothing deliberately wrote.

        NSIS's own headers confirm the mapping: ``FileFunc.nsh`` passes ``r0``
        for ``$0``, ``Library.nsh`` passes ``R4`` for ``$R4``.
        """
        offenders = []
        for number, line in code_lines():
            if "System::Call" not in line:
                continue
            for match in re.finditer(r"[,(]\s*\w?\s*\.?(r\d)\b", line):
                offenders.append(f"line {number}: {match.group(1)} means "
                                 f"${match.group(1)[1:]}, not "
                                 f"$R{match.group(1)[1:]}")
            for match in re.finditer(r"i\.(r\d)\b", line):
                offenders.append(f"line {number}: return into "
                                 f"{match.group(1)}")
        self.assertEqual(offenders, [], "\n".join(offenders))

    def test_the_handover_reads_the_register_the_handover_wrote(self):
        """The value is loaded into a register and read back in one place."""
        lines = [line.strip() for _n, line in code_lines()]
        call = next(i for i, line in enumerate(lines)
                    if "SetEnvironmentVariable" in line
                    and "DESKPILOT_SETUP_ANSWERS" in line
                    and 't ""' not in line)
        window = lines[max(0, call - 4):call]
        loaded = [re.match(r"StrCpy\s+(\$R?\d)\s", line)
                  for line in window]
        registers = {m.group(1) for m in loaded if m}
        self.assertTrue(registers,
                        "nothing loads a register before the hand-over")
        referenced = re.findall(r"t\s+(R?\d)\)", lines[call])
        self.assertTrue(referenced, f"no register referenced in {lines[call]}")
        for name in referenced:
            self.assertIn(f"${name}", registers,
                          f"the call reads ${name} but the code loaded "
                          f"{sorted(registers)}")

    def test_the_handover_clears_the_register_and_the_variable(self):
        """A password must not sit in a global after it has been handed over."""
        text = NSI.read_text()
        self.assertIn('StrCpy $R5 ""', text)
        self.assertIn('StrCpy $AnswersJson ""', text)


class RegistersAreSavedByFunctionsThatUseThem(unittest.TestCase):
    """NSIS's twenty registers are global; a helper that writes them lies."""

    def test_every_helper_saves_the_registers_it_writes(self):
        offenders = []
        for name, body in functions().items():
            if name in _ENTRY_POINTS:
                continue
            written, saved = set(), set()
            for line in body:
                hit = _WRITERS.match(line)
                if hit:
                    written.add(hit.group(1))
                keep = _SAVERS.match(line)
                if keep:
                    saved.add(keep.group(1))
            missing = sorted(written - saved)
            if missing:
                offenders.append(f"{name} writes {missing} without saving")
        self.assertEqual(offenders, [], "\n".join(offenders))

    def test_find_python_saves_the_whole_register_file(self):
        """Its helpers use registers freely, so it saves on their behalf."""
        body = functions()["FindPython"]
        saved = {m.group(1) for m in (_SAVERS.match(line) for line in body) if m}
        for index in range(10):
            self.assertIn(f"$R{index}", saved,
                          f"FindPython must save $R{index}: its helpers use it")

    def test_find_python_restores_on_every_path(self):
        """The saves are matched by restores, and no path skips them.

        Counting every Push against every Pop cannot work here: plugin calls
        push their own results (``nsExec::ExecToStack`` leaves two values) and
        a helper takes its argument on the stack, so the totals legitimately
        differ. What matters is that the prologue's ten saves are undone by ten
        restores at a single exit, and that nothing jumps past them -- so that
        is what this checks.
        """
        body = functions()["FindPython"]
        prologue = [line for line in body[:10] if _SAVERS.match(line)]
        self.assertEqual(
            prologue,
            [f"Push $R{index}" for index in range(10)],
            "FindPython must open by saving $R0-$R9 in order")

        tail = body[-10:]
        self.assertEqual(
            tail,
            [f"Pop $R{index}" for index in reversed(range(10))],
            "FindPython must close by restoring $R9-$R0 in order")

        self.assertNotIn("Return", " ".join(body),
                         "a bare Return would skip the restore; every path "
                         "must reach the single exit label")
        self.assertIn("find_done:", body,
                      "the single exit label is missing")
        exits = sum(1 for line in body if line == "Goto find_done")
        self.assertGreater(exits, 0, "no path jumps to the exit label")

    def test_build_answers_uses_named_variables(self):
        """Numbered registers are how the hand-over broke in the first place."""
        body = functions()["BuildAnswers"]
        for line in body:
            hit = _WRITERS.match(line)
            if hit and hit.group(1) not in ("$0",):
                self.fail(f"BuildAnswers writes {hit.group(1)}; it must use "
                          f"named variables so it cannot collide: {line}")


class TheAnswersDocumentIsValidJson(unittest.TestCase):
    """What the wizard assembles has to parse, quotes and backslashes included."""

    def template(self) -> str:
        body = "\n".join(functions()["BuildAnswers"])
        match = re.search(r"StrCpy \$AnswersJson '(\{.*\})'", body, re.S)
        self.assertIsNotNone(match, "the answers document was not found")
        return match.group(1)

    def rendered(self, **values) -> str:
        """Substitute NSIS variables the way NSIS would."""
        text = self.template()
        for name, value in values.items():
            text = text.replace(f"${name}", value)
        return text

    def test_the_template_parses_with_ordinary_values(self):
        document = self.rendered(
            AnsName="Jo Owner", AnsEmail="jo@example.com",
            AnsPassword="correct-horse-battery", AnsKind="ollama",
            AnsApiKey="", AnsAutostart="true", AnsBackups="false")
        parsed = json.loads(document)
        self.assertEqual(parsed["owner_identity"], "Jo Owner")
        self.assertEqual(parsed["web_password"], "correct-horse-battery")
        self.assertFalse(parsed["components"]["backups"])

    def test_the_template_parses_with_escaped_quotes_and_backslashes(self):
        """BuildAnswers escapes both before substituting; this checks the result.

        A password containing a quote would otherwise produce a document that
        does not parse -- and, given the old silent fallback, an owner told
        their details were incomplete.
        """
        document = self.rendered(
            AnsName='Jo \\"The Owner\\"', AnsEmail="jo@example.com",
            AnsPassword='pa\\\\ss\\"word-long-enough', AnsKind="ollama",
            AnsApiKey="", AnsAutostart="true", AnsBackups="true")
        parsed = json.loads(document)
        self.assertEqual(parsed["owner_identity"], 'Jo "The Owner"')
        self.assertEqual(parsed["web_password"], 'pa\\ss"word-long-enough')

    def test_the_booleans_are_json_booleans_not_strings(self):
        document = self.rendered(
            AnsName="x", AnsEmail="", AnsPassword="y", AnsKind="ollama",
            AnsApiKey="", AnsAutostart="true", AnsBackups="false")
        parsed = json.loads(document)
        self.assertIsInstance(parsed["components"]["autostart"], bool)
        self.assertIsInstance(parsed["components"]["backups"], bool)

    def test_escaping_happens_before_the_document_is_built(self):
        body = "\n".join(functions()["BuildAnswers"])
        build = body.index("StrCpy $AnswersJson")
        for field in ("AnsName", "AnsEmail", "AnsPassword", "AnsApiKey"):
            escape = body.index(f"${field}")
            self.assertLess(escape, build,
                            f"${field} is used before it is escaped")

    def test_every_field_the_engine_reads_is_present(self):
        """The two halves must agree on the document's shape."""
        document = self.rendered(
            AnsName="x", AnsEmail="", AnsPassword="y", AnsKind="ollama",
            AnsApiKey="", AnsAutostart="true", AnsBackups="true")
        parsed = json.loads(document)
        for key in ("owner_identity", "business_email", "web_password",
                    "ai_kind", "ai_api_key", "components"):
            self.assertIn(key, parsed)


class TheEscapedCopiesDoNotOutliveTheDocument(unittest.TestCase):
    """``BuildAnswers`` clears its escaped password as soon as it has used it.

    That is only correct because NSIS resolves ``$Var`` inside a ``StrCpy``
    string when the instruction runs, so the destination holds the resulting
    text and not a reference to the source. Clearing one line earlier would
    put an empty password into the document instead, and no Python test can
    catch that because no Python test runs NSIS -- so the order is asserted
    here, where an edit that inverts it is visible.
    """

    def build_answers(self) -> list[str]:
        return functions()["BuildAnswers"]

    def test_the_document_is_built_before_the_copies_are_cleared(self):
        body = self.build_answers()
        built = next(i for i, line in enumerate(body)
                     if line.startswith("StrCpy $AnswersJson"))
        for name in ("$AnsPassword", "$AnsApiKey"):
            cleared = next((i for i, line in enumerate(body)
                            if line == f'StrCpy {name} ""'), None)
            self.assertIsNotNone(cleared, f"{name} is never cleared")
            self.assertGreater(
                cleared, built,
                f"{name} is cleared before the document is built, so the "
                f"document would carry an empty value")

    def test_the_document_is_built_once(self):
        """Two assignments would make the order question ambiguous."""
        built = [line for line in self.build_answers()
                 if line.startswith("StrCpy $AnswersJson")]
        self.assertEqual(len(built), 1)


class NoSecretReachesACommandLine(unittest.TestCase):
    """Arguments are visible to every account on the machine."""

    SECRET_VARS = ("$WebPassword", "$WebPassword2", "$AiApiKey",
                   "$AnsPassword", "$AnsApiKey", "$AnswersJson")

    def test_no_nsexec_command_contains_a_secret_variable(self):
        offenders = []
        for number, line in code_lines():
            if "nsExec" not in line:
                continue
            for name in self.SECRET_VARS:
                if name in line:
                    offenders.append(f"line {number}: {name}")
        self.assertEqual(offenders, [], "\n".join(offenders))

    def test_no_detailprint_echoes_a_secret(self):
        """The details pane is on screen and in the install log."""
        offenders = []
        for number, line in code_lines():
            if "DetailPrint" not in line:
                continue
            for name in self.SECRET_VARS:
                if name in line:
                    offenders.append(f"line {number}: {name}")
        self.assertEqual(offenders, [], "\n".join(offenders))

    def test_the_wizard_clears_the_secrets_after_the_install_phase(self):
        text = NSI.read_text()
        for name in ("$WebPassword", "$WebPassword2", "$AiApiKey"):
            self.assertIn(f'StrCpy {name} ""', text,
                          f"{name} is never cleared")


class TheWizardStagesAndRunsWhatItClaims(unittest.TestCase):
    """Cheap structural checks over the install sequence's own wiring."""

    def test_the_self_check_runs_before_the_install_phase(self):
        text = NSI.read_text()
        self.assertLess(text.index("deskpilot-engine.py\" selfcheck"),
                        text.index("deskpilot-engine.py\" install"))

    def test_the_answers_are_set_before_the_self_check(self):
        """The self-check verifies the hand-over, so it must happen after it."""
        text = NSI.read_text()
        self.assertLess(text.index("SetEnvironmentVariable(t \"DESKPILOT_SETUP_ANSWERS\", t R5)"),
                        text.index("deskpilot-engine.py\" selfcheck"))

    def test_the_answers_are_cleared_after_the_install_phase(self):
        """The successful path forgets them once the engine has them."""
        text = NSI.read_text()
        forget = text.index("Call ForgetSecrets", text.index(
            "deskpilot-engine.py\" install"))
        self.assertGreater(forget, text.index("deskpilot-engine.py\" install"))

    def test_a_failed_self_check_aborts(self):
        text = NSI.read_text()
        window = text[text.index("deskpilot-engine.py\" selfcheck"):]
        window = window[:window.index("deskpilot-engine.py\" install")]
        self.assertIn("Abort", window)


if __name__ == "__main__":
    unittest.main()


class EverySecretIsForgottenOnEveryExit(unittest.TestCase):
    """The hand-over puts the password in the process environment.

    It was cleared on the way out of a successful install and nowhere else, so
    a self-check that stopped the installation left the owner's password in the
    installer's environment block for as long as the window stayed open. The
    fix is one function; what follows is the reason it cannot be bypassed.
    """

    #: Where the password enters the environment.
    STAGE = 'SetEnvironmentVariable(t "DESKPILOT_SETUP_ANSWERS", t R5)'

    def install_section(self) -> list[tuple[int, str]]:
        inside, out = False, []
        for number, line in code_lines():
            if line.strip().startswith('Section "-Install"'):
                inside = True
                continue
            if inside and line.strip().startswith("SectionEnd"):
                break
            if inside:
                out.append((number, line.strip()))
        return out

    def test_there_is_exactly_one_authority_for_forgetting(self):
        """Two places to clear a secret is one place to forget to."""
        body = [line for line in code_lines()
                if 'SetEnvironmentVariable(t "DESKPILOT_SETUP_ANSWERS", t "")'
                in line[1]]
        self.assertEqual(len(body), 1,
                         f"clearing is spread over {len(body)} places")
        self.assertIn("ForgetSecrets", functions())
        self.assertIn(body[0][1].strip(), functions()["ForgetSecrets"])

    def test_the_authority_clears_every_secret_variable(self):
        body = functions()["ForgetSecrets"]
        for name in NoSecretReachesACommandLine.SECRET_VARS:
            self.assertIn(f'StrCpy {name} ""', body,
                          f"ForgetSecrets leaves {name} set")

    def test_the_authority_does_not_disturb_its_caller(self):
        """It runs between an engine call and the check of its exit code."""
        body = functions()["ForgetSecrets"]
        written = {match.group(1) for match in
                   (_WRITERS.match(line) for line in body) if match}
        saved = {match.group(1) for match in
                 (_SAVERS.match(line) for line in body) if match}
        plugin = {"$R9"} if any("System::Call" in line and "R9" in line
                                for line in body) else set()
        self.assertEqual((written | plugin) - saved, set(),
                         "ForgetSecrets clobbers registers it does not save")

    def test_no_abort_after_the_hand_over_skips_it(self):
        """The defect: the self-check's Abort left the password behind."""
        lines = self.install_section()
        staged = next(i for i, (_n, line) in enumerate(lines)
                      if self.STAGE in line)
        forgotten, offenders = False, []
        for _number, line in lines[staged:]:
            if line.startswith("Call ForgetSecrets"):
                forgotten = True
            if line.startswith("Abort") and not forgotten:
                offenders.append(_number)
        self.assertEqual(offenders, [],
                         f"Abort without ForgetSecrets at line(s) {offenders}")

    def test_the_section_cannot_end_still_holding_them(self):
        lines = self.install_section()
        staged = next(i for i, (_n, line) in enumerate(lines)
                      if self.STAGE in line)
        self.assertTrue(
            any(line.startswith("Call ForgetSecrets")
                for _n, line in lines[staged:]),
            "the install section stages the answers and never forgets them")


class TheUninstallerDoesNotGuessAtPython(unittest.TestCase):
    """Python discovery is one authority, and the uninstaller is not a copy.

    NSIS cannot share a function between the installer and the uninstaller, so
    the uninstaller had its own two-line version: the install's virtual
    environment, then ``C:\\Program Files\\Python313\\python.exe``. That is the
    defect that stopped the first installer from seeing a Python 3.13.12 which
    was already installed, rebuilt in the half of the script nobody was
    reading. The installer now records the interpreter it used and the
    uninstaller reads the record.
    """

    def body(self) -> list[str]:
        return functions()["un.FindPython"]

    def test_the_installer_records_the_interpreter_it_used(self):
        text = NSI.read_text()
        self.assertIn('WriteRegStr HKLM "${REGKEY}" "PythonExe" "$PythonExe"',
                      text)

    def test_the_uninstaller_reads_that_record(self):
        self.assertTrue(
            any('ReadRegStr $PythonExe HKLM "${REGKEY}" "PythonExe"' in line
                for line in self.body()),
            "un.FindPython does not read the recorded interpreter")

    def test_the_uninstaller_hardcodes_no_interpreter_path(self):
        offenders = [line for line in self.body()
                     if re.search(r"Python3\d|python\.exe", line)
                     and "$INSTDIR" not in line
                     and "$PythonExe" not in line.replace(
                         "StrCpy $PythonExe", "")]
        self.assertEqual(offenders, [], "\n".join(offenders))

    def test_a_recorded_path_that_no_longer_exists_is_not_used(self):
        """An interpreter can be uninstalled between install and uninstall."""
        body = self.body()
        read = next(i for i, line in enumerate(body) if "ReadRegStr" in line)
        window = body[read:]
        self.assertTrue(any("FileExists" in line for line in window),
                        "the recorded path is used without checking it exists")
        self.assertTrue(any('StrCpy $PythonExe ""' in line
                            for line in window[1:]),
                        "a stale record is not cleared, so the uninstaller "
                        "would run a command whose target does not exist")


class TheUpgradeDoesNotMixTwoEngines(unittest.TestCase):
    """Extracting over an install leaves the previous version's leftovers.

    Raised by the K-LEAN advisory gate, whose own installer had just fixed the
    same thing: a copy that does not remove destination files absent from the
    source. Python imports whatever sits in a package directory, so a module
    dropped in a new engine version would still be importable after an
    upgrade, and the install would run a mixture of two engines.

    The fix is narrow on purpose. Only ``$INSTDIR\\Engine`` is cleared, because
    only that directory belongs to the installer. Clearing more would destroy
    the owner's database and backups, which the brief forbids outright.
    """

    OWNED = "$INSTDIR\\Engine"

    def install_section(self) -> list[str]:
        inside, out = False, []
        for _number, line in code_lines():
            if line.strip().startswith('Section "-Install"'):
                inside = True
                continue
            if inside and line.strip().startswith("SectionEnd"):
                break
            if inside:
                out.append(line.strip())
        return out

    def test_the_engine_directory_is_cleared_before_it_is_filled(self):
        body = self.install_section()
        clear = next((i for i, line in enumerate(body)
                      if line == f'RMDir /r "{self.OWNED}"'), None)
        self.assertIsNotNone(clear, "the Engine directory is never cleared")
        fill = next(i for i, line in enumerate(body)
                    if line == f'SetOutPath "{self.OWNED}"')
        self.assertLess(clear, fill,
                        "the Engine directory is cleared after being filled")

    def test_the_install_deletes_nothing_the_owner_owns(self):
        """Data, backups, App and venv survive an upgrade. Non-negotiable."""
        offenders = []
        for line in self.install_section():
            if not line.startswith(("RMDir", "Delete")):
                continue
            if line == f'RMDir /r "{self.OWNED}"':
                continue
            offenders.append(line)
        self.assertEqual(offenders, [], "\n".join(offenders))

    def test_the_clearing_happens_after_the_engine_has_committed(self):
        """Nothing is removed until the install phase has already succeeded."""
        text = NSI.read_text()
        self.assertLess(text.index('deskpilot-engine.py" install'),
                        text.index(f'RMDir /r "{self.OWNED}"'),
                        "the Engine directory is cleared before the install "
                        "phase could fail, so a failed upgrade would leave no "
                        "engine behind at all")
