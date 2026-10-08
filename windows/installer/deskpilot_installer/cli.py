"""The engine's command-line face: what the native wizard actually runs.

The wizard draws screens; this decides things. Each phase is one invocation
that writes a JSON report and prints human-readable lines for the installer's
details pane, so the two halves share no state beyond a file. That split is
what keeps the decisions testable: everything below this module runs the same
way whether a wizard or a test called it.

**How the owner's password gets here.** Not as an argument: command-line
arguments are visible to every other account on the machine through the process
list, which is precisely why Solvent's own ``web-password`` command prompts
instead of accepting one. Not as a file by default either, since that leaves
plaintext on disk for as long as it takes to read it. The wizard puts the
answers in an environment variable and the engine reads it from there --
environment blocks are inherited by the child, are not listed alongside
processes, and vanish when it exits. :func:`read_answers` clears the variable
from its own environment immediately after parsing, so a later subprocess
cannot inherit it. A file is accepted as a fallback for hosts where setting the
variable is impractical, and is overwritten and deleted the moment it is read.

Exit codes are the wizard's gate: ``0`` means the owner may continue, ``1``
means a blocker was reported, ``2`` means the engine was called wrongly. A
wizard that only reads the exit code therefore fails closed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import lifecycle, selfcheck
from dataclasses import dataclass

from .engine import (
    Components, Engine, OwnerAnswers, readiness_headline,
)
from .host import Host, WindowsHost
from .installlog import Log
from .layout import DEFAULT_ROOT, Layout
from .probe import Settings, system_check
from .report import Failure, Report, Secret

#: Where the wizard is expected to leave the answers.
ANSWERS_ENV = "DESKPILOT_SETUP_ANSWERS"

EXIT_OK = 0
EXIT_BLOCKED = 1
EXIT_USAGE = 2


@dataclass(frozen=True)
class AnswersTransfer:
    """The owner's answers, and whether they actually arrived.

    This type exists because of a failure that cost an owner an install. The
    wizard handed the answers over through an environment variable and, because
    of a one-character mistake in a register reference, set it to the string
    ``ollama`` instead of the JSON document. :func:`read_answers` caught the
    parse error, returned defaults, and the installation then refused with "the
    owner details are incomplete -- a control-centre password is needed". The
    owner had typed a password. The message sent them back to a screen they had
    already filled in correctly.

    A silent fall back to defaults is what turned a transfer bug into a
    misleading instruction. So the outcome is now reported rather than
    swallowed: ``error`` is non-empty when something arrived and could not be
    understood, and the caller refuses with a message about the *transfer*
    instead of blaming the owner's typing.
    """

    answers: OwnerAnswers
    #: "environment", "file", or "absent".
    source: str = "absent"
    #: Why the payload could not be used, or ``""``.
    error: str = ""

    @property
    def arrived(self) -> bool:
        return self.source != "absent" and not self.error


def read_answers(env_name: str = ANSWERS_ENV,
                 path: str = "", host: Host | None = None) -> AnswersTransfer:
    """Parse the owner's answers, then destroy the copy they arrived in.

    Never raises: the caller needs a report it can show, not a traceback.
    """
    raw, source = "", "absent"
    if env_name and os.environ.get(env_name):
        raw = os.environ[env_name]
        source = "environment"
        # Removed before anything else runs: a subprocess started later would
        # otherwise inherit the owner's password in its environment.
        os.environ.pop(env_name, None)
    elif path and host is not None and host.exists(path):
        raw = host.read_bytes(path).decode("utf-8", "replace")
        source = "file"
        try:
            host.write_bytes(path, b"\x00" * max(len(raw), 1))
            host.remove_tree(path)
        except OSError:
            pass
    if not raw.strip():
        return AnswersTransfer(OwnerAnswers(), source,
                               "" if source == "absent" else
                               "the owner's answers arrived empty")
    try:
        data = json.loads(raw)
    except ValueError as exc:
        # Deliberately quotes the *shape*, never the content: a malformed
        # payload may still contain the password.
        return AnswersTransfer(
            OwnerAnswers(), source,
            f"the owner's answers did not arrive as readable JSON "
            f"({exc.__class__.__name__}: {exc}). "
            f"{len(raw)} character(s) were received, beginning "
            f"{raw[:12]!r}. This is a fault in the installer handing the "
            f"details over, not in what you typed.")
    if not isinstance(data, dict):
        return AnswersTransfer(
            OwnerAnswers(), source,
            f"the owner's answers arrived as {type(data).__name__}, not an "
            f"object. This is a fault in the installer, not in what you typed.")
    components = data.get("components") or {}
    return AnswersTransfer(_owner_answers(data, components), source)


def _owner_answers(data: dict, components: dict) -> OwnerAnswers:
    return OwnerAnswers(
        owner_identity=str(data.get("owner_identity") or "owner"),
        business_email=str(data.get("business_email") or ""),
        web_password=(Secret(str(data["web_password"]), "web password")
                      if data.get("web_password") else None),
        ai_kind=str(data.get("ai_kind") or "ollama"),
        ai_model_tag=str(data.get("ai_model_tag") or ""),
        ai_api_key=(Secret(str(data["ai_api_key"]), "AI provider key")
                    if data.get("ai_api_key") else None),
        components=Components(
            autostart=bool(components.get("autostart", True)),
            backups=bool(components.get("backups", True)),
            configure_ai=bool(components.get("configure_ai", True)),
            install_ollama=bool(components.get("install_ollama", False)),
            pull_model=bool(components.get("pull_model", False)),
        ),
    )


def transfer_failure(transfer: AnswersTransfer) -> Failure:
    """§21's five answers for a hand-over that did not work.

    Separate from "the owner left a field blank", and worded so nobody is sent
    back to a screen they filled in correctly.
    """
    return Failure(
        what_failed="The installer could not read the details you entered",
        why=transfer.error,
        changed=(),
        not_changed=("nothing was installed",
                     "no directories were created",
                     "no database was created, opened or modified",
                     "no Windows setting, task or firewall rule was changed",
                     "MetaTrader was not touched"),
        safe_next_action=("This is a fault in the installer, not in what you "
                          "typed. Your computer is unchanged. Report it with "
                          "the line above; re-entering the details will not "
                          "help."),
    )


def _emit(report: Report, out_path: str, host: Host) -> None:
    """Print the rows for the details pane, and write the JSON for the wizard."""
    for row in report.rows:
        level = row.outcome.value
        print(f"[{level:12}] {row.name}: {row.detail}")
    if report.failure is not None:
        print()
        print(report.failure.render())
    if out_path:
        try:
            host.write_bytes(out_path, report.dumps().encode("utf-8"))
        except OSError as exc:
            print(f"[WARN        ] the report could not be written to "
                  f"{out_path}: {exc}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="deskpilot-installer",
        description="DeskPilot's Windows installation engine. Driven by "
                    "DeskPilot-Setup.exe; every decision it reports is made "
                    "by DeskPilot's own authorities.")
    parser.add_argument("phase",
                        choices=["selfcheck", "check", "install", "verify",
                                 "repair", "upgrade", "uninstall",
                                 "plan-uninstall"])
    parser.add_argument("--package", default="",
                        help="path to the certified DeskPilot package")
    parser.add_argument("--expect-sha256", default="",
                        help="the digest this installer was built for")
    parser.add_argument("--root", default=DEFAULT_ROOT)
    parser.add_argument("--out", default="",
                        help="write the JSON report here")
    parser.add_argument("--answers-env", default=ANSWERS_ENV,
                        help="environment variable holding the owner's answers")
    parser.add_argument("--answers-file", default="",
                        help="fallback: a file holding the owner's answers, "
                             "erased and deleted as soon as it is read")
    parser.add_argument("--public-https", action="store_true",
                        help="also check ports 80 and 443")
    parser.add_argument("--probe-inference", action="store_true",
                        help="during verify, send the model a real prompt")
    parser.add_argument("--purge-owner-data", action="store_true",
                        help="uninstall: also delete the database, audit "
                             "history and backups. Not the default, and never "
                             "implied.")
    return parser


def main(argv: list[str] | None = None, host: Host | None = None) -> int:
    args = build_parser().parse_args(argv)
    host = host or WindowsHost()
    layout = Layout(root=args.root)
    settings = Settings(layout=layout, package_path=args.package,
                        expected_digest=args.expect_sha256,
                        public_https=args.public_https)
    transfer = read_answers(args.answers_env, args.answers_file, host)
    answers = transfer.answers
    log = Log(host)

    # A hand-over that arrived and could not be read is reported as such,
    # before any phase runs. Only the phases that need the owner's answers
    # are blocked by it; a check or an uninstall does not.
    if transfer.error and args.phase in ("selfcheck", "install"):
        report = Report(args.phase, (), transfer_failure(transfer))
        _emit(report, args.out, host)
        return EXIT_BLOCKED

    if args.phase == "selfcheck":
        # Runs before anything is modified. Its exit code is the wizard's gate.
        report = selfcheck.preinstall(host, settings, transfer=transfer)
    elif args.phase == "check":
        report = system_check(host, settings)
    elif args.phase == "install":
        report = Engine(host, settings, answers, log).install()
    elif args.phase == "verify":
        report = Engine(host, settings, answers, log).verify(
            probe_inference=args.probe_inference)
        print()
        print(readiness_headline(report))
    elif args.phase == "repair":
        report = lifecycle.repair(host, settings, answers, log)
    elif args.phase == "upgrade":
        report = lifecycle.upgrade(host, settings, answers, log)
    elif args.phase == "plan-uninstall":
        from .engine import InstallState
        plan = lifecycle.plan_uninstall(
            host, layout, InstallState.load(host, layout.state_file),
            purge_owner_data=args.purge_owner_data)
        report = Report("plan-uninstall", plan.rows())
    elif args.phase == "uninstall":
        report = lifecycle.uninstall(host, layout,
                                     purge_owner_data=args.purge_owner_data,
                                     log=log)
    else:  # pragma: no cover - argparse restricts the choices
        return EXIT_USAGE

    _emit(report, args.out, host)
    return EXIT_OK if report.may_continue else EXIT_BLOCKED


if __name__ == "__main__":
    sys.exit(main())
