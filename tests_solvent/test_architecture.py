"""The Solvent laws, as tests.

Documentation describes an architecture; these assert it. They are deliberately
structural — they read the source with ``ast`` — so the laws keep holding as the
codebase grows rather than only on the day they were written.
"""

from __future__ import annotations

import ast
import pathlib
import sys
import unittest

from solvent.orchestrator import TIMEOUTS_MINUTES, TRANSITIONS
from solvent.store import TABLE_OWNER
from solvent.types import JobState

PACKAGE = pathlib.Path(__file__).resolve().parent.parent / "solvent"

#: Authorities that make consequential decisions. None of them may grant itself
#: authority, and none may read the Project State projection.
AUTHORITIES = {"governor", "gate", "orchestrator", "ledger", "memory", "capability",
               "audit"}


def modules() -> dict[str, ast.Module]:
    return {p.stem: ast.parse(p.read_text(), filename=str(p))
            for p in sorted(PACKAGE.glob("*.py"))}


def calls_in(tree: ast.Module) -> set[str]:
    """Every attribute call name in a module, e.g. ``amend`` from ``x.amend()``."""
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            found.add(node.func.attr)
    return found


def external_imports(tree: ast.Module) -> set[str]:
    """Absolute top-level imports only — what this module depends on from outside."""
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module.split(".")[0])
    return found


def sibling_imports(tree: ast.Module) -> set[str]:
    """Intra-package (relative) imports, e.g. ``from .projection import project``."""
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level:
            if node.module:
                found.add(node.module.split(".")[0])
            found.update(alias.name for alias in node.names)
    return found


class OneAuthorityPerResponsibility(unittest.TestCase):
    def test_no_table_has_two_owners(self):
        self.assertEqual(len(TABLE_OWNER), len(set(TABLE_OWNER)))

    def test_learning_owns_only_its_own_knowledge(self):
        """Business Memory may hold knowledge and nothing that governs or pays."""
        owned = {t for t, owner in TABLE_OWNER.items() if owner == "memory"}
        self.assertEqual(owned, {"memory_facts"})

    def test_only_the_policy_authority_owns_governance_tables(self):
        for table in ("policy_current", "policy_versions", "pricing_reference"):
            self.assertEqual(TABLE_OWNER[table], "policy")

    def test_audit_owns_all_evidence(self):
        for table in ("audit_log", "verification_evidence"):
            self.assertEqual(TABLE_OWNER[table], "audit")


class NoComponentGrantsItselfAuthority(unittest.TestCase):
    def test_no_authority_module_amends_policy(self):
        """Only the owner path may write Policy. Not the Governor, not Learning."""
        for name, tree in modules().items():
            if name in ("policy", "cli", "harness"):
                continue  # the owner path and the fixture demo
            with self.subTest(module=name):
                self.assertNotIn("amend", calls_in(tree),
                                 f"{name}.py must not amend Policy")
                self.assertNotIn("set_operating_mode", calls_in(tree),
                                 f"{name}.py must not set the kill switch")

    def test_no_authority_module_registers_capabilities(self):
        for name, tree in modules().items():
            if name in ("capability", "cli", "harness"):
                continue
            with self.subTest(module=name):
                self.assertNotIn("register", calls_in(tree),
                                 f"{name}.py must not register capabilities")


class GateIsTheOnlyEgress(unittest.TestCase):
    def test_only_the_gate_opens_an_egress_window(self):
        for name, tree in modules().items():
            if name in ("gate", "egress"):
                continue
            with self.subTest(module=name):
                self.assertNotIn("window", calls_in(tree),
                                 f"{name}.py must not open an egress window")

    def test_no_module_imports_a_network_client_except_the_boundary(self):
        """A network import anywhere else is a path around the Gate."""
        network = {"socket", "http", "urllib", "ssl", "ftplib", "smtplib",
                   "telnetlib", "asyncio", "subprocess"}
        for name, tree in modules().items():
            if name in ("egress",):
                continue
            with self.subTest(module=name):
                self.assertEqual(external_imports(tree) & network, set(),
                                 f"{name}.py imports a network or process module")


class NoGateReadsTheProjection(unittest.TestCase):
    def test_decision_modules_do_not_import_the_projection(self):
        """The instant a gate reads the projection, the projection is authoritative."""
        for name in ("governor", "gate", "policy", "orchestrator", "ledger"):
            tree = modules()[name]
            with self.subTest(module=name):
                self.assertNotIn("projection", sibling_imports(tree))


class ZeroThirdPartyDependencies(unittest.TestCase):
    def test_solvent_imports_only_the_standard_library(self):
        """Every dependency is a potential path around the Action Gate."""
        allowed = set(sys.stdlib_module_names) | {"solvent"}
        for name, tree in modules().items():
            external = {m for m in external_imports(tree) if m and m not in allowed}
            with self.subTest(module=name):
                self.assertEqual(external, set(),
                                 f"{name}.py imports non-stdlib: {external}")


class StateMachineInvariants(unittest.TestCase):
    def test_every_non_terminal_state_has_a_timeout(self):
        """A state with no timeout is a place a job can stall silently."""
        for state in JobState:
            if state.is_terminal:
                continue
            with self.subTest(state=state):
                self.assertIn(state, TIMEOUTS_MINUTES)

    def test_every_state_that_can_be_blocked_can_resume(self):
        """Otherwise a job can be blocked into a corner it cannot leave."""
        entering = {s for s, targets in TRANSITIONS.items()
                    if JobState.BLOCKED in targets}
        stuck = entering - TRANSITIONS[JobState.BLOCKED]
        self.assertEqual(stuck, set(), f"blocked with no way back: {stuck}")

    def test_terminal_states_have_no_exits(self):
        for state in (JobState.COMPLETE, JobState.REJECTED, JobState.FAILED):
            with self.subTest(state=state):
                self.assertEqual(TRANSITIONS[state], frozenset())

    def test_every_state_appears_in_the_transition_table(self):
        self.assertEqual(set(TRANSITIONS), set(JobState))

    def test_rejected_is_distinct_from_failed(self):
        """Declining work is a success of the system, not a failure."""
        self.assertIsNot(JobState.REJECTED, JobState.FAILED)
        self.assertTrue(JobState.REJECTED.is_terminal)


if __name__ == "__main__":
    unittest.main()
