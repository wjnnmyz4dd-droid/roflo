"""§107: count the parallel authorities. The expected answer is zero.

Asserted rather than claimed, because "we did not duplicate anything" is the
easiest sentence in an architecture document to write and the hardest to keep
true. A second thing that answers a question an authority already answers is a
defect even when both are correct today: the first time they disagree, nobody
knows which is right, and there is no rule that says.

Each test names the one authoritative owner and then looks for a second.
"""

from __future__ import annotations

import ast
import pathlib
import unittest

from solvent.store import TABLE_OWNER

SOLVENT = pathlib.Path(__file__).resolve().parent.parent / "solvent"

#: One responsibility, one module that owns it. Anything else writing that
#: module's tables, or recomputing its answer, is a duplicate.
AUTHORITIES = {
    "policy": "policy.py",
    "audit": "audit.py",
    "ledger": "ledger.py",
    "governor": "governor.py",
    "orchestrator": "orchestrator.py",
    "gate": "gate.py",
    "capability": "capability.py",
    "discovery": "discovery.py",
    "relations": "clientrelations.py",
    "feedback": "feedback.py",
    "memory": "memory.py",
    "qualification": "qualification.py",
    "owner": "owner.py",
    "skillslab": "skillslab.py",
}


def modules():
    for path in sorted(SOLVENT.rglob("*.py")):
        yield path


def authority_strings(path: pathlib.Path) -> set[str]:
    """Authority names this module asks the store for.

    Resolves a module-level constant as well as a literal: ``skillslab.py``
    passes ``AUTHORITY``, and a scan that only saw literals reported it as
    claiming nothing — a check that would have missed a module claiming several
    authorities through constants.
    """
    tree = ast.parse(path.read_text())
    constants = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    constants[target.id] = node.value.value
    found = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "for_authority"
                and node.args):
            continue
        argument = node.args[0]
        if isinstance(argument, ast.Constant):
            found.add(argument.value)
        elif isinstance(argument, ast.Name) and argument.id in constants:
            found.add(constants[argument.id])
        else:
            # An authority chosen at run time would defeat this whole check.
            found.add(f"<computed in {path.name}>")
    return found


def reads_environment(path: pathlib.Path, needle: str) -> bool:
    """Whether this module reads an environment variable whose name matches.

    Two precisions this needed. Naming a variable in a message or a docstring is
    not reading it -- `readiness.py` tells the owner which one to set and
    `web/__init__.py` explains which one the surface never touches, and both are
    desirable. And the name is often a module constant (`os.environ.get(KEY_ENV)`),
    so a check that demanded a literal would miss the one real reader while
    flagging every module that reads some *other* variable.
    """
    tree = ast.parse(path.read_text())
    constants = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    constants[target.id] = node.value.value

    def names(expression) -> list[str]:
        if isinstance(expression, ast.Constant):
            return [str(expression.value)]
        if isinstance(expression, ast.Name):
            return [str(constants.get(expression.id, ""))]
        if isinstance(expression, ast.Attribute):
            return [expression.attr]
        return []

    for node in ast.walk(tree):
        source = ""
        candidates: list = []
        if isinstance(node, ast.Call):
            source = ast.unparse(node.func)
            candidates = list(node.args)
        elif isinstance(node, ast.Subscript):
            source = ast.unparse(node.value)
            candidates = [node.slice]
        if "environ" not in source:
            continue
        for candidate in candidates:
            if any(needle in name for name in names(candidate)):
                return True
    return False


class EachTableHasExactlyOneWriter(unittest.TestCase):
    def test_every_table_names_one_authority(self):
        """The mapping is the single source of who may write what."""
        for table, owner in TABLE_OWNER.items():
            with self.subTest(table=table):
                self.assertIsInstance(owner, str)
                self.assertTrue(owner)

    def test_no_module_claims_an_authority_that_is_not_its_own(self):
        """A module taking two authorities' connections holds two authorities.

        The composition root is exempt: wiring is its job. So are tests.
        """
        allowed_multiple = {"harness.py", "store.py"}
        for path in modules():
            claimed = authority_strings(path)
            if len(claimed) <= 1 or path.name in allowed_multiple:
                continue
            with self.subTest(module=path.name):
                self.fail(f"{path.name} claims {sorted(claimed)}")

    def test_each_authority_is_claimed_by_the_module_named_for_it(self):
        for authority, filename in AUTHORITIES.items():
            with self.subTest(authority=authority):
                path = SOLVENT / filename
                self.assertTrue(path.exists(), f"{filename} missing")
                self.assertIn(authority, authority_strings(path))

    def test_the_web_surface_claims_no_authority_at_all(self):
        """It reads through a read-only connection and writes a spool. If it ever
        asks the store for an authority, it has stopped being a window."""
        for path in sorted((SOLVENT / "web").glob("*.py")):
            with self.subTest(module=path.name):
                self.assertEqual(authority_strings(path), set())


class NothingRecomputesAnothersAnswer(unittest.TestCase):
    """The subtler duplicate: not a second table, a second calculation."""

    def test_only_the_ledger_decides_what_money_arrived(self):
        """A second module computing collected revenue is a second Ledger."""
        offenders = []
        for path in modules():
            if path.name in ("ledger.py", "metrics.py"):
                continue
            text = path.read_text()
            if "SUM(collected_cents)" in text and "readmodel" not in path.name:
                offenders.append(path.name)
        self.assertEqual(offenders, [])

    def test_the_web_projection_reads_columns_rather_than_recomputing_profit(self):
        """It may total a column the Ledger owns. It must not implement the
        Ledger's rules about what counts."""
        text = (SOLVENT / "web" / "readmodel.py").read_text()
        for rule in ("def actual_profit", "def real_revenue_cents",
                     "margin =", "def _decide_event"):
            with self.subTest(rule=rule):
                self.assertNotIn(rule, text)

    def test_only_the_capability_registry_decides_what_is_proven(self):
        offenders = []
        for path in modules():
            if path.name in ("capability.py", "harness.py"):
                continue
            text = path.read_text()
            if "proven=True" in text:
                offenders.append(path.name)
        self.assertEqual(offenders, [])

    def test_the_skills_lab_does_not_certify(self):
        """It records a certification result. It must not produce one."""
        text = (SOLVENT / "skillslab.py").read_text()
        self.assertNotIn("run_generated", text)
        self.assertNotIn("TrialReport", text)
        self.assertNotIn("certify_verifier", text)

    def test_the_skills_lab_does_not_register_a_capability(self):
        text = (SOLVENT / "skillslab.py").read_text()
        self.assertNotIn("capability.register", text)
        self.assertNotIn(".register(", text)

    def test_only_the_action_gate_performs_an_external_effect(self):
        """`egress.install` is the marker: whatever calls it is the containment
        boundary, and there must be exactly one."""
        callers = [path.name for path in modules()
                   if "egress.install()" in path.read_text()]
        self.assertEqual(callers, ["gate.py"])

    def test_only_the_owner_channel_reads_the_signing_key(self):
        """Naming the variable is not reading it. Other modules tell the owner
        which one to set, and the web package explains which one it never
        touches; both are desirable."""
        offenders = [path.name for path in modules()
                     if path.name != "owner.py"
                     and reads_environment(path, "OWNER_KEY")]
        self.assertEqual(offenders, [])

    def test_the_owner_channel_does_read_it(self):
        """Guards the test above: a check that finds no reader anywhere would
        pass on a codebase where the key is never used at all."""
        self.assertTrue(reads_environment(SOLVENT / "owner.py", "OWNER_KEY"))

    def test_no_module_chooses_its_authority_at_run_time(self):
        """An authority picked from a variable would defeat every check here."""
        for path in modules():
            claimed = authority_strings(path)
            with self.subTest(module=path.name):
                self.assertEqual([c for c in claimed if c.startswith("<computed")],
                                 [])

    def test_only_one_module_decides_the_operating_mode(self):
        setters = [path.name for path in modules()
                   if "def set_operating_mode" in path.read_text()]
        self.assertEqual(setters, ["policy.py"])


class TheNewSurfacesAddedNoAuthority(unittest.TestCase):
    """What this pass built, and what it deliberately did not become."""

    def test_there_is_no_second_policy_engine(self):
        for forbidden in ("WebPolicy", "DashboardGovernor", "UIActionGate",
                          "SkillsLabGovernor", "CustomerServiceGovernor",
                          "WebsiteLedger", "WebsiteAudit", "LabRegistry"):
            with self.subTest(name=forbidden):
                offenders = [p.name for p in modules()
                             if forbidden in p.read_text()]
                self.assertEqual(offenders, [])

    def test_the_skills_lab_owns_only_its_own_four_tables(self):
        owned = {t for t, o in TABLE_OWNER.items() if o == "skillslab"}
        self.assertEqual(owned, {"skill_projects", "skill_project_events",
                                 "skill_evidence", "skill_versions"})

    def test_the_web_package_owns_no_table(self):
        self.assertEqual({t for t, o in TABLE_OWNER.items() if o == "web"}, set())

    def test_the_intent_list_is_closed(self):
        """An open verb list would make "what can the website do?" a question
        nobody can answer."""
        from solvent.web import intents

        text = (SOLVENT / "web" / "intents.py").read_text()
        self.assertIn("is not an intent this surface may write", text)
        self.assertGreater(len(intents.INTENTS), 0)

    def test_every_intent_verb_is_classified(self):
        from solvent.web import intents

        for verb in intents.INTENTS:
            with self.subTest(verb=verb):
                self.assertIn(intents.classify(verb),
                              (intents.SAFE, intents.CONSEQUENTIAL))

    def test_the_count_of_unjustified_duplicates_is_zero(self):
        """The headline number, computed rather than asserted."""
        duplicates = []
        for path in modules():
            if path.name in ("harness.py", "store.py"):
                continue
            claimed = authority_strings(path)
            if len(claimed) > 1:
                duplicates.append((path.name, sorted(claimed)))
        self.assertEqual(duplicates, [], f"{len(duplicates)} duplicate(s)")
