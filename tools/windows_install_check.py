#!/usr/bin/env python3
"""Read what a real DeskPilot installation left on a real machine.

Run after ``deskpilot-engine.py install``. It asserts nothing about
what the installer *intended* -- the engine's own report covers that
-- and everything about what is on disk afterwards, because an
installer that reports success and leaves a machine without a working
DeskPilot is the failure this exists to catch.

Deliberately run with ``python -I`` outside the installed environment,
so it sees the installation the way an owner's next login would: by
path, with nothing inherited from the process that created it.

Paths are joined with :mod:`os.path`, which on Windows *is*
:mod:`ntpath`. The installer itself uses ``ntpath`` explicitly and
should, because it is Windows-only code; this is a diagnostic, and a
diagnostic that cannot be dry-run anywhere but the machine it
diagnoses is one whose first real execution is the one that matters.

Every check prints PASS or FAIL with what it looked at. The exit code
is non-zero if any required check failed; the checks marked optional
report and do not fail, because DeskPilot runs without the
independent readers and says so rather than refusing to start.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys

#: What the installed application must be able to do before anybody
#: calls this an installation.
REQUIRED = "required"
OPTIONAL = "optional"


def local(path: str) -> str:
    """A Layout path, usable on the machine running this script.

    ``Layout`` joins with :mod:`ntpath` and should: it describes a
    Windows installation. This runs on Windows in anger, where the
    translation is a no-op, and on Linux when it is being dry-run
    before anybody trusts it -- and a diagnostic whose first real
    execution is the one that matters is not a diagnostic.
    """
    return path if os.sep == "\\" else path.replace("\\", "/")


class Checks:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def record(self, name: str, ok: bool, detail: str,
               kind: str = REQUIRED) -> bool:
        mark = "PASS" if ok else ("FAIL" if kind == REQUIRED else "WARN")
        print(f"{mark:4}  {name}: {detail}", flush=True)
        self.rows.append({"name": name, "ok": ok, "kind": kind,
                          "detail": detail})
        return ok

    @property
    def failed(self) -> list[dict]:
        return [r for r in self.rows if not r["ok"] and r["kind"] == REQUIRED]


def run(python: str, code: str, timeout: float = 300.0):
    """A fresh interpreter, isolated, against the installed tree."""
    return subprocess.run([python, "-I", "-c", code], capture_output=True,
                          text=True, check=False, timeout=timeout)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--report", default="")
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv)

    root = args.root
    checks = Checks()
    print(f"reading the installation at {root}\n")

    # Asked of Layout, not guessed at. The first version hardcoded
    # `app`, `data` and `deskpilot.db`; the installed tree uses
    # `App`, `Data` and `solvent.db`, so three checks failed against
    # a correct installation and said the database had not been
    # created when it had. A checker that invents the paths it
    # checks is reporting on a machine that does not exist.
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent
                           / "windows" / "installer"))
    from deskpilot_installer.layout import Layout

    layout = Layout(root=root)

    # ---- what the engine said it did --------------------------------
    report = {}
    if args.report and pathlib.Path(args.report).exists():
        try:
            report = json.loads(
                pathlib.Path(args.report).read_text(encoding="utf-8"))
        except ValueError as exc:
            checks.record("the engine's report is readable", False, str(exc))
    if report:
        # Three readings, because the first one on its own was a
        # control that could not fire. It asked each row for ``ok``
        # and the engine writes ``outcome`` -- so `.get("ok") is
        # False` was never true, `bad` was always empty, and this
        # line printed "14 step(s), 0 failed" over a report whose
        # last row said FAIL and which carried a whole `failure`
        # block. Two CI runs were read as clean installations on the
        # strength of it. A check that cannot fail is worse than no
        # check: it is a claim.
        #
        # So: the vocabulary is now the engine's own, taken from the
        # engine rather than spelled again here; `may_continue` and
        # `failure` are read as well, because a report can stop
        # before it writes the row that says why; and a report whose
        # rows carry none of the keys this understands is itself a
        # finding.
        from deskpilot_installer.report import Outcome

        stopping = {o.value for o in Outcome if o.stops_installation}
        steps = report.get("steps") or report.get("rows") or []
        graded = [s for s in steps
                  if isinstance(s, dict)
                  and ("outcome" in s or "ok" in s)]
        bad = [s for s in steps
               if isinstance(s, dict)
               and (s.get("outcome") in stopping or s.get("ok") is False)]
        checks.record("every installation step is graded by this check",
                      len(graded) == len(steps) and bool(steps),
                      f"{len(graded)} of {len(steps)} row(s) carry an "
                      f"outcome this check understands ({sorted(stopping)} "
                      f"stop an installation)")
        checks.record("every installation step reported success",
                      not bad, f"{len(steps)} step(s), {len(bad)} failed"
                                + (f": {[s.get('name') for s in bad]}"
                                   if bad else ""))
        checks.record("the engine did not stop part-way",
                      report.get("failure") is None
                      and report.get("may_continue") is not False,
                      (report.get("failure") or {}).get("what_failed")
                      or "no failure block; may_continue="
                         f"{report.get('may_continue')}")

    # ---- the tree ----------------------------------------------------
    app = local(layout.app)
    venv_python = local(os.path.join(layout.venv, "Scripts", "python.exe"))
    for name, path in (("the application is on disk",
                        os.path.join(app, "solvent", "harness.py")),
                       ("the Python environment exists", venv_python),
                       ("the data directory exists", local(layout.data))):
        checks.record(name, os.path.exists(path), path)

    if not os.path.exists(venv_python):
        return _finish(checks, args.out)

    # ---- the application imports, by path ---------------------------
    asked = run(venv_python, "import solvent, sys; print(solvent.__file__)")
    checks.record("DeskPilot imports in its own environment",
                  asked.returncode == 0 and app.lower() in
                  asked.stdout.strip().lower(),
                  asked.stdout.strip() or asked.stderr.strip()[-200:])

    # ---- the independent readers ------------------------------------
    asked = run(venv_python, (
        "from solvent import independent\n"
        "import json\n"
        "out = {}\n"
        "for ext in sorted(independent._DIFFERENTIAL_READERS):\n"
        "    report = independent.available(extension=ext)\n"
        "    out[ext] = [p.name for p in report.producers if p.usable]\n"
        "print(json.dumps(out))"))
    usable = {}
    if asked.returncode == 0:
        try:
            usable = json.loads(asked.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            usable = {}
    covered = sorted(k for k, v in usable.items() if v)
    checks.record("independent (T2) readers are installed",
                  len(covered) >= 4,
                  f"usable for {covered or 'nothing'}", OPTIONAL)

    # ---- the five certified skills ----------------------------------
    asked = run(venv_python, (
        "from solvent.harness import Solvent\n"
        "import json, tempfile, os, secrets\n"
        "os.environ.setdefault('SOLVENT_OWNER_KEY', secrets.token_hex(32))\n"
        "import solvent.storefront as sf\n"
        "with tempfile.TemporaryDirectory() as box:\n"
        "    s = Solvent(os.path.join(box, 'probe.db'))\n"
        "    print(json.dumps(sorted(o.offer_id for o in sf.OFFERS)))\n"
        "    s.store.close()"))
    skills = []
    if asked.returncode == 0:
        try:
            skills = json.loads(asked.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            skills = []
    checks.record("the five certified skills load", len(skills) == 5,
                  ", ".join(skills) or asked.stderr.strip()[-200:])

    # ---- the installed database -------------------------------------
    db = local(layout.db)
    checks.record("the database was created", os.path.exists(db), db)

    # ---- the dashboard, against the installed database --------------
    asked = run(venv_python, (
        "from solvent.web.readmodel import ReadModel\n"
        "from solvent.web.app import _connection_cards, _pipeline_section\n"
        f"read = ReadModel({db!r})\n"
        "html = _connection_cards(read) + _pipeline_section(read)\n"
        "print('CARDS' if 'CONNECT BUSINESS EMAIL' in html else 'NO-EMAIL')\n"
        "print('STRIPE' if 'CONNECT STRIPE' in html else 'NO-STRIPE')\n"
        "print('DISCONNECTED' if 'NOT CONNECTED' in html else 'CONNECTED')\n"
        "read.close()"))
    said = asked.stdout.split()
    checks.record("the dashboard renders both connection cards",
                  "CARDS" in said and "STRIPE" in said,
                  " ".join(said) or asked.stderr.strip()[-200:])
    checks.record("email and Stripe are disconnected",
                  "DISCONNECTED" in said, " ".join(said))

    # ---- live external action stays off -----------------------------
    asked = run(venv_python, (
        "from solvent.harness import Solvent\n"
        "from solvent import connections\n"
        f"s = Solvent({db!r})\n"
        "print('SIM' if s.policy.get('egress','simulation_only',"
        "default=True) else 'LIVE')\n"
        "print('OFF' if not connections.live_actions_enabled(s.policy) "
        "else 'ON')\n"
        "s.store.close()"))
    said = asked.stdout.split()
    checks.record("simulation mode is on and live actions are off",
                  said[:2] == ["SIM", "OFF"],
                  " ".join(said) or asked.stderr.strip()[-200:])

    # ---- the scheduler, on the installed application ----------------
    asked = run(venv_python, (
        "from solvent.harness import Solvent\n"
        "from solvent.runtime import Runtime\n"
        f"s = Solvent({db!r})\n"
        "r = Runtime(s); r.start(); r.default_tasks()\n"
        "names = [t.name for t in r.tasks]\n"
        "print('TASKS', len(names))\n"
        "print('COS', 'chief_of_staff' in names)\n"
        "lines = r.run_due()\n"
        "print('RAN', len(lines))\n"
        "print('ALL_RAN', len(lines) == len(names) and len(names) > 0)\n"
        "print([l for l in lines if l.startswith('chief_of_staff')])\n"
        "s.store.close()"), timeout=600.0)
    # ``ALL_RAN``, not a literal count. This read ``"TASKS 10" in
    # stdout``, and the eleventh task turned a correct installation
    # into a failed one on both interpreters -- the same hand-written
    # number going stale that the suite's own task pins did, except
    # this one lives in a tool the Linux suite does not exercise, so
    # it was found by Windows CI rather than before the push.
    #
    # A substring match on a count is doubly wrong: "TASKS 10" is also
    # in "TASKS 100". The property worth checking was never the
    # number, it is that every task the runtime registered actually
    # ran on this machine, which is what the installation is being
    # asked to demonstrate.
    checks.record("the scheduler runs on the installed application",
                  "ALL_RAN True" in asked.stdout and "COS True" in asked.stdout,
                  asked.stdout.strip().replace("\n", " | ")[:300]
                  or asked.stderr.strip()[-300:])

    return _finish(checks, args.out)


def _finish(checks: Checks, out: str) -> int:
    print()
    required = [r for r in checks.rows if r["kind"] == REQUIRED]
    print(f"{sum(1 for r in required if r['ok'])}/{len(required)} required "
          f"check(s) passed")
    if out:
        pathlib.Path(out).write_text(
            json.dumps({"rows": checks.rows,
                        "failed": [r["name"] for r in checks.failed]},
                       indent=2), encoding="utf-8")
    for row in checks.failed:
        print(f"  FAILED: {row['name']}: {row['detail']}")
    return 1 if checks.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
