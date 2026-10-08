"""The installation itself: an ordered list of steps over existing authorities.

This module is the orchestrator and nothing more. It does not decide whether a
capability is certified, whether a model's rights are cleared, whether the
system is ready, or what the firewall posture is. Each of those has an
authority inside Solvent already, reached here by running the installed
application's own command-line interface against the installed database. The
installer's contribution is the order, the error handling, and telling the
owner what happened.

That boundary is worth stating sharply because it is easy to erode. If this
file ever grows a function that *computes* readiness rather than reading
``solvent readiness``, DeskPilot acquires a second opinion about whether it can
trade, and the two will diverge on the day one is changed. So the rule is: the
installer may run a command and show its result; it may not reach a verdict a
Solvent authority is responsible for.

**How the application is put on the import path.** No pip, because DeskPilot
has no dependencies -- its suite passes with site-packages switched off -- and
adding a package manager to the normal path would be adding the problem this
installer exists to remove. Instead a ``.pth`` file in DeskPilot's own virtual
environment names the application directory. That is enough for ``import
solvent`` to work under ``python -I``, which matters: ``-I`` makes the
interpreter ignore ``PYTHONPATH``, skip user site-packages, and keep the
current directory off ``sys.path``. A planted ``json.py`` in whatever directory
the installer happened to be launched from therefore cannot be imported by
DeskPilot, and neither can an attacker's ``PYTHONPATH``. The ``.pth`` gives
reach; ``-I`` takes away everything else.

**Every step records what it changed.** Not for tidiness: §21 requires that a
failure tells the owner what was and was not altered, and §19 requires an
upgrade that can roll back. Both need the same ledger, so steps append to one.
"""

from __future__ import annotations

import json
import ntpath
from dataclasses import asdict, dataclass, field
from typing import Callable

from . import ai, integration, payload, python_runtime, secrets_env
from .host import Host
from .installlog import Log
from .layout import Layout, WEB_PORT
from .probe import Settings
from .report import Failure, Level, Outcome, Report, Row, Secret

#: The owner identity used for governance records.
#:
#: Deliberately *not* the name the owner types into the wizard. Solvent's
#: capability authority accepts only a registered owner -- ``is_owner`` requires
#: the ``owner:`` prefix *and* membership of a policy-held list -- and refuses
#: anything else with "only a registered owner decides capability growth". An
#: installer that passed a free-text business name was refused on every run.
#:
#: Left empty on purpose: the installer omits ``--owner`` entirely and lets
#: Solvent apply its own registered default. Choosing an identity here would
#: make the installer a second authority on who the owner is, which is exactly
#: the thing it must not become. The typed name is recorded where it belongs,
#: as a contracting legal name.
GOVERNANCE_OWNER_OMITTED = True

#: Recorded in the install state so a later installer knows what it is looking at.
STATE_VERSION = 1


@dataclass(frozen=True, slots=True)
class Components:
    """What the owner chose to install. Core and control centre are not optional."""

    autostart: bool = True
    backups: bool = True
    configure_ai: bool = True
    #: Install Ollama if it is missing. Never implied by ``configure_ai``.
    install_ollama: bool = False
    #: Download the chosen model. Separate because it is the slow, large one.
    pull_model: bool = False


@dataclass
class OwnerAnswers:
    """The only things the owner is asked for. Everything else is derived."""

    owner_identity: str = "owner"
    business_email: str = ""
    web_password: Secret | None = None
    ai_kind: str = "ollama"
    ai_model_tag: str = ""
    ai_api_key: Secret | None = None
    components: Components = field(default_factory=Components)

    def problems(self) -> list[str]:
        """Owner-facing validation. Checked before anything is written."""
        out: list[str] = []
        if not self.owner_identity.strip():
            out.append("an owner name is needed")
        if self.web_password is None or not self.web_password.present:
            out.append("a control-centre password is needed")
        elif len(self.web_password.reveal()) < secrets_env.MIN_PASSWORD_LENGTH:
            out.append(f"the control-centre password must be at least "
                       f"{secrets_env.MIN_PASSWORD_LENGTH} characters")
        if self.business_email and "@" not in self.business_email:
            out.append("the business email does not look like an address")
        return out


@dataclass
class InstallState:
    """What this installer did, written to disk for repair, upgrade and rollback."""

    state_version: int = STATE_VERSION
    payload_sha256: str = ""
    python_exe: str = ""
    root: str = ""
    model_tag: str = ""
    tasks: list[str] = field(default_factory=list)
    firewall_rule: str = ""
    components: dict = field(default_factory=dict)
    installed_at: str = ""

    def dumps(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)

    @classmethod
    def load(cls, host: Host, path: str) -> "InstallState | None":
        if not host.exists(path):
            return None
        try:
            raw = json.loads(host.read_bytes(path).decode("utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            return None
        if not isinstance(raw, dict):
            return None
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in raw.items() if k in known})


@dataclass(frozen=True, slots=True)
class StepResult:
    """One step's outcome, and its contribution to the change ledger."""

    key: str
    title: str
    ok: bool
    detail: str
    changed: tuple[str, ...] = ()
    #: Set when a step was legitimately not needed.
    skipped: bool = False
    facts: dict = field(default_factory=dict)

    def row(self) -> Row:
        if self.skipped:
            return Row(self.title, Outcome.PASS, self.detail, facts=self.facts)
        return Row(self.title, Outcome.PASS if self.ok else Outcome.FAIL,
                   self.detail, facts=self.facts)


class StepFailed(Exception):
    """A step could not complete. Carries the owner-facing detail."""

    def __init__(self, detail: str, facts: dict | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.facts = facts or {}


class Engine:
    """Runs the installation. One instance per attempt; not reusable."""

    def __init__(self, host: Host, settings: Settings, answers: OwnerAnswers,
                 log: Log | None = None) -> None:
        self.host = host
        self.settings = settings
        self.layout: Layout = settings.layout
        self.answers = answers
        self.log = log if log is not None else Log(host)
        self.changed: list[str] = []
        self.state = InstallState(root=self.layout.root)
        self.owner_key: Secret | None = None
        self.python_exe: str = ""

    # -- helpers ---------------------------------------------------------
    def solvent_cli(self, *args: str, timeout: float = 600.0):
        """Run the installed application's CLI. The only way to reach authority."""
        argv = [self.python_exe, "-I", "-m", "solvent.cli", *args]
        result = self.host.run(argv, cwd=self.layout.app, timeout=timeout)
        self.log.command(argv, result.returncode, result.output)
        return result

    def _note(self, what: str) -> None:
        self.changed.append(what)
        self.log.write(f"  changed: {what}")

    # -- steps -----------------------------------------------------------
    def step_verify_package(self) -> StepResult:
        verdict = payload.verify(self.host, self.settings.package_path,
                                 self.settings.expected_digest)
        payload.require_verified(verdict)
        self.state.payload_sha256 = verdict.actual
        return StepResult("verify", "Verifying package", True,
                          f"VERIFIED ({verdict.size} bytes)",
                          facts={"sha256": verdict.actual})

    def step_python(self) -> StepResult:
        found = python_runtime.discover(self.host)
        chosen = python_runtime.choose(found)
        if chosen is not None:
            self.python_exe = chosen.path
            return StepResult("python", "Installing Python runtime", True,
                              f"reusing {chosen.version_text} at {chosen.path}",
                              skipped=True,
                              facts={"path": chosen.path,
                                     "version": chosen.version_text})
        installer_path = ntpath.join(self.layout.logs,
                                     f"python-{python_runtime.BOOTSTRAP_VERSION}"
                                     f"-amd64.exe")
        got = self.host.run(["curl.exe", "-sS", "-L", "--fail",
                             "-o", installer_path,
                             python_runtime.BOOTSTRAP_URL], timeout=900.0)
        self.log.command(got.argv, got.returncode, got.output)
        if not got.ok:
            raise StepFailed(f"Python {python_runtime.BOOTSTRAP_VERSION} could "
                             f"not be downloaded from python.org: "
                             f"{got.output.strip()[:200]}")
        why = python_runtime.verify_download(self.host, installer_path)
        if why:
            # Fail closed. A Python installer that is not the expected bytes is
            # not run, whatever the reason it differs.
            raise StepFailed(f"the downloaded Python installer was rejected: {why}")
        self._note(f"downloaded the Python {python_runtime.BOOTSTRAP_VERSION} "
                   f"installer to {installer_path}")
        ran = self.host.run([installer_path, *python_runtime.BOOTSTRAP_ARGS],
                            timeout=1800.0)
        self.log.command(ran.argv, ran.returncode, ran.output)
        if not ran.ok:
            raise StepFailed(f"the Python installer reported failure: "
                             f"{ran.output.strip()[:200]}")
        self._note(f"installed Python {python_runtime.BOOTSTRAP_VERSION}")
        found = python_runtime.discover(self.host)
        chosen = python_runtime.choose(found)
        if chosen is None:
            raise StepFailed("Python was installed but could not then be found")
        self.python_exe = chosen.path
        return StepResult("python", "Installing Python runtime", True,
                          f"installed {chosen.version_text}",
                          facts={"path": chosen.path})

    def step_directories(self) -> StepResult:
        made = []
        for directory in self.layout.all_dirs:
            if not self.host.is_dir(directory):
                self.host.makedirs(directory)
                made.append(directory)
        if made:
            self._note(f"created {len(made)} directories under {self.layout.root}")
        self.log.path = self.layout.install_log
        return StepResult("dirs", "Creating directories", True,
                          f"{self.layout.root} ready",
                          facts={"created": made})

    def step_extract(self) -> StepResult:
        if self.host.is_dir(self.layout.app) and self.host.listdir(self.layout.app):
            # An upgrade replaces the application wholesale. Leaving old files
            # behind is how a module deleted in a new version keeps being
            # imported by a stale .pyc somewhere.
            #
            # Guarded on the directory being non-empty, not merely existing:
            # the previous step creates it, so an unguarded check reports
            # "removed the previous application" on a fresh install. A change
            # ledger that lists things that did not happen is worse than no
            # ledger, because the owner cannot tell which entries to trust.
            self.host.remove_tree(self.layout.app)
            self._note(f"removed the previous application at {self.layout.app}")
        written = payload.extract(self.host, self.settings.package_path,
                                  self.layout.app)
        self._note(f"extracted {len(written)} files to {self.layout.app}")
        return StepResult("extract", "Installing DeskPilot", True,
                          f"{len(written)} files in {self.layout.app}",
                          facts={"files": len(written)})

    def step_venv(self) -> StepResult:
        """DeskPilot's own environment, with the app reachable and nothing else."""
        if self.python_exe.lower().startswith(self.layout.venv.lower()):
            # Rebuilding a virtual environment with the interpreter that lives
            # inside it cannot work, and the error Windows gives for it is
            # obscure. An upgrade reached this by taking the interpreter from
            # the recorded install state, which is the venv's; the caller must
            # re-discover the system Python first.
            raise StepFailed(
                f"the Python environment at {self.layout.venv} cannot be "
                f"rebuilt using the interpreter inside it "
                f"({self.python_exe}); a system Python is needed")
        made = self.host.run([self.python_exe, "-m", "venv", self.layout.venv],
                             timeout=900.0)
        self.log.command(made.argv, made.returncode, made.output)
        if not made.ok:
            raise StepFailed(f"the DeskPilot Python environment could not be "
                             f"created: {made.output.strip()[:200]}")
        self._note(f"created a Python environment at {self.layout.venv}")
        venv_python = ntpath.join(self.layout.venv, "Scripts", "python.exe")
        where = self.host.run([venv_python, "-c",
                               "import site;print(site.getsitepackages()[0])"],
                              timeout=120.0)
        self.log.command(where.argv, where.returncode, where.output)
        if not where.ok or not where.stdout.strip():
            raise StepFailed("the DeskPilot Python environment did not report "
                             "its package directory")
        pth = ntpath.join(where.stdout.strip().splitlines()[0].strip(),
                          "deskpilot-app.pth")
        self.host.write_bytes(pth, (self.layout.app + "\n").encode("utf-8"))
        self._note(f"put the application on the environment's import path "
                   f"via {pth}")
        self.python_exe = venv_python
        self.state.python_exe = venv_python
        return StepResult("venv", "Creating isolated Python environment", True,
                          f"{self.layout.venv} (no packages installed - "
                          f"DeskPilot needs none)",
                          facts={"python": venv_python, "pth": pth})

    def step_secrets(self) -> StepResult:
        key = secrets_env.generate_owner_key()
        problem = secrets_env.self_check(key)
        if problem:
            raise StepFailed(f"the generated owner key was rejected: {problem}")
        self.owner_key = key
        password_hash = None
        if self.answers.web_password is not None:
            try:
                password_hash = secrets_env.hash_web_password(
                    self.host, self.python_exe, self.layout.app,
                    self.answers.web_password)
            except ValueError as exc:
                raise StepFailed(str(exc)) from exc
        extra: list[tuple[str, Secret]] = []
        if self.answers.ai_api_key is not None and self.answers.ai_api_key.present:
            extra.append(("ROFLO_API_KEY", self.answers.ai_api_key))
        contents = secrets_env.EnvContents(owner_key=key,
                                           web_password_hash=password_hash,
                                           extra=tuple(extra))
        try:
            ran = secrets_env.write_env_file(self.host, self.layout.env_file,
                                             contents)
        except secrets_env.PermissionsRefused as exc:
            raise StepFailed(str(exc)) from exc
        for line in ran:
            self.log.write(f"$ {line}")
        self._note(f"created {self.layout.env_file}, readable only by SYSTEM "
                   f"and Administrators")
        return StepResult("secrets", "Creating owner credentials", True,
                          "owner signing key generated on this computer; "
                          "control-centre password stored as a hash",
                          facts={"env_file": self.layout.env_file})

    def step_database(self) -> StepResult:
        """Create the real database, using a command that will create one.

        ``setup check`` and not ``doctor``. ``doctor`` deliberately refuses to
        open a database that does not exist -- "doctor reports what a
        deployment is actually enforcing, so it will not create one to report
        on" -- which is correct of it and made this step fail on every fresh
        installation. The refusal was invisible to a test double whose handler
        returned success and created the file; it took running the real command
        to see it.
        """
        result = self.solvent_cli("setup", "check", "--db", self.layout.db)
        # Its exit code is deliberately ignored. ``setup check`` is a *view*:
        # it reports whether the owner's configuration is complete, and on a
        # fresh installation it is not -- no work source approved, no payment
        # rail, activation blocked pending owner decisions -- so it exits
        # non-zero every time. That is correct of it and says nothing about
        # whether the command worked. The success criterion for this step is
        # the one thing the step is for: a database now exists.
        if not self.host.exists(self.layout.db):
            raise StepFailed(
                f"the DeskPilot database at {self.layout.db} was not created. "
                f"DeskPilot said: {result.output.strip()[:300] or '(nothing)'}")
        # Then ask ``doctor``, whose exit code *is* meaningful now that the
        # file exists: it refuses only an absent database, and fails on one it
        # cannot open. Each command is used for what its exit code actually
        # means -- ``setup check`` to create, ``doctor`` to confirm the result
        # is a database rather than merely a file at the right path.
        opened = self.solvent_cli("doctor", "--db", self.layout.db)
        if not opened.ok:
            raise StepFailed(
                f"a file exists at {self.layout.db} but DeskPilot could not "
                f"open it as a database: "
                f"{opened.output.strip()[:300] or '(no detail)'}. It has not "
                f"been replaced or deleted.")
        self._note(f"created the database at {self.layout.db}")
        return StepResult("database", "Creating persistent database", True,
                          self.layout.db, facts={"db": self.layout.db})

    def step_contracting(self) -> StepResult:
        """Record the owner's name and email as a contracting identity.

        This is where the name typed into the wizard belongs. It is a legal
        name for a client agreement, not a governance identity -- and keeping
        the two apart is what lets the capability step use Solvent's registered
        owner instead of being refused.
        """
        if not self.answers.owner_identity.strip():
            return StepResult("contracting", "Recording owner details", True,
                              "no name given", skipped=True)
        args = ["setup", "contracting", "--db", self.layout.db,
                "--structure", "INDIVIDUAL",
                "--legal-name", self.answers.owner_identity]
        if self.answers.business_email:
            args += ["--email", self.answers.business_email]
        result = self.solvent_cli(*args)
        if not result.ok:
            raise StepFailed(f"the owner's contracting details could not be "
                             f"recorded: {result.output.strip()[:300]}")
        self._note("recorded the owner's contracting identity")
        return StepResult("contracting", "Recording owner details", True,
                          (result.output.strip().splitlines() or ["recorded"])[0])

    def step_capability(self) -> StepResult:
        """Ask Solvent to promote csv-cleanup. It re-runs the verifier itself."""
        env = {secrets_env.OWNER_KEY_ENV: self.owner_key.reveal()} \
            if self.owner_key else {}
        argv = [self.python_exe, "-I", "-m", "solvent.cli", "setup",
                "capability", "--db", self.layout.db]
        result = self.host.run(argv, cwd=self.layout.app, env=env, timeout=900.0)
        self.log.command(argv, result.returncode, result.output)
        if not result.ok:
            raise StepFailed(f"the certified capability could not be "
                             f"registered: {result.output.strip()[:300]}")
        self._note("registered csv-cleanup/1.0 as a proven capability")
        return StepResult("capability", "Registering certified capabilities",
                          True, result.stdout.strip().splitlines()[0]
                          if result.stdout.strip() else "registered",
                          facts={"output": result.output.strip()[:500]})

    def step_model_rights(self) -> StepResult:
        """Record the chosen model's commercial clearance, when we may.

        Only for a model this repository holds verified rights evidence for.
        An installer that cleared a model by passing a digest it invented would
        be manufacturing the evidence the clearance is supposed to rest on.
        """
        tag = self.answers.ai_model_tag
        option = next((o for o in ai.CATALOGUE if o.tag == tag), None)
        if option is None:
            return StepResult("model", "Configuring AI", True,
                              f"no rights record written: {tag or 'no model'} "
                              f"is not in the catalogue", skipped=True)
        if not option.commercial:
            raise StepFailed(
                f"{option.display} is licensed {option.licence}, which does "
                f"not permit commercial use. DeskPilot will not record a "
                f"commercial clearance for it.")
        if not option.can_autoclear:
            return StepResult("model", "Configuring AI", True,
                              f"{option.display} selected; its commercial "
                              f"rights need a digest you verify before "
                              f"DeskPilot will use it", skipped=True,
                              facts={"tag": tag})
        env = {secrets_env.OWNER_KEY_ENV: self.owner_key.reveal()} \
            if self.owner_key else {}
        argv = [self.python_exe, "-I", "-m", "solvent.cli", "setup", "model",
                "--db", self.layout.db,
                "--tag", option.tag, "--digest", option.verified_digest,
                "--provider", self.answers.ai_kind]
        result = self.host.run(argv, cwd=self.layout.app, env=env, timeout=600.0)
        self.log.command(argv, result.returncode, result.output)
        if not result.ok:
            raise StepFailed(f"the model's commercial clearance could not be "
                             f"recorded: {result.output.strip()[:300]}")
        self.state.model_tag = option.tag
        self._note(f"recorded commercial clearance for {option.tag}")
        return StepResult("model", "Configuring AI", True,
                          f"{option.display} cleared ({option.licence})",
                          facts={"tag": option.tag, "licence": option.licence})

    def step_firewall(self) -> StepResult:
        """One scoped rule, then tell Solvent what the posture now is."""
        for argv in integration.firewall_commands(self.python_exe):
            result = self.host.run(list(argv), timeout=120.0)
            self.log.command(argv, result.returncode, result.output)
            if not result.ok:
                raise StepFailed(f"the DeskPilot firewall rule could not be "
                                 f"added: {result.output.strip()[:200]}")
        self.state.firewall_rule = integration.FIREWALL_RULE_NAME
        self._note(f"added one firewall rule: "
                   f"{integration.FIREWALL_RULE_NAME}")
        env = {secrets_env.OWNER_KEY_ENV: self.owner_key.reveal()} \
            if self.owner_key else {}
        argv = [self.python_exe, "-I", "-m", "solvent.cli", "setup", "firewall",
                "--db", self.layout.db,
                "--default-deny", "--note",
                "Windows Firewall, inbound default deny. DeskPilot binds "
                "loopback only; one scoped block rule added by the installer."]
        recorded = self.host.run(argv, cwd=self.layout.app, env=env, timeout=300.0)
        self.log.command(argv, recorded.returncode, recorded.output)
        if not recorded.ok:
            raise StepFailed(f"the firewall posture could not be recorded: "
                             f"{recorded.output.strip()[:300]}")
        return StepResult("firewall", "Configuring web control centre", True,
                          f"control centre on loopback {WEB_PORT}; one scoped "
                          f"firewall rule added; nothing else changed",
                          facts={"rule": integration.FIREWALL_RULE_NAME})

    def step_autostart(self) -> StepResult:
        if not self.answers.components.autostart:
            return StepResult("autostart", "Configuring autostart", True,
                              "not selected", skipped=True)
        scripts = integration.InstalledScripts.under(self.layout)
        self.host.write_bytes(scripts.launcher,
                              integration.launcher_source(self.layout)
                              .encode("utf-8"))
        xml = integration.task_xml(self.python_exe, scripts.launcher,
                                   at_startup=True)
        self.host.write_bytes(scripts.task_xml, xml.encode("utf-8"))
        if not integration.register_task(self.host, integration.TASK_NAME,
                                          scripts.task_xml):
            raise StepFailed("the DeskPilot startup task could not be created")
        self.state.tasks.append(integration.TASK_NAME)
        self._note(f"created the scheduled task {integration.TASK_NAME!r}, "
                   f"which starts DeskPilot at boot and restarts it on failure")
        return StepResult("autostart", "Configuring autostart", True,
                          f"{integration.TASK_NAME} runs at boot as SYSTEM and "
                          f"restarts on failure",
                          facts={"task": integration.TASK_NAME})

    def step_backups(self) -> StepResult:
        if not self.answers.components.backups:
            return StepResult("backups", "Configuring backups", True,
                              "not selected", skipped=True)
        scripts = integration.InstalledScripts.under(self.layout)
        self.host.write_bytes(scripts.backup,
                              integration.backup_source(self.layout)
                              .encode("utf-8"))
        xml = integration.task_xml(self.python_exe, scripts.backup,
                                   at_startup=False)
        self.host.write_bytes(scripts.backup_task_xml, xml.encode("utf-8"))
        if not integration.register_task(self.host,
                                          integration.BACKUP_TASK_NAME,
                                          scripts.backup_task_xml):
            raise StepFailed("the DeskPilot backup task could not be created")
        self.state.tasks.append(integration.BACKUP_TASK_NAME)
        self._note(f"created the scheduled task "
                   f"{integration.BACKUP_TASK_NAME!r}, which backs up the "
                   f"database daily and keeps "
                   f"{integration.BACKUP_RETENTION}")
        return StepResult("backups", "Configuring backups", True,
                          f"daily to {self.layout.backups}, newest "
                          f"{integration.BACKUP_RETENTION} kept, secrets "
                          f"excluded",
                          facts={"task": integration.BACKUP_TASK_NAME,
                                 "retention": integration.BACKUP_RETENTION})

    def step_record_state(self) -> StepResult:
        import datetime
        self.state.installed_at = (datetime.datetime.now(datetime.timezone.utc)
                                   .strftime("%Y-%m-%dT%H:%M:%SZ"))
        self.state.components = asdict(self.answers.components)
        self.host.write_bytes(self.layout.state_file,
                              self.state.dumps().encode("utf-8"))
        return StepResult("state", "Recording installation", True,
                          self.layout.state_file, skipped=True)

    # -- the sequence ----------------------------------------------------
    def steps(self) -> list[tuple[str, Callable[[], StepResult]]]:
        """The install order. Each entry is shown to the owner as it runs."""
        return [
            ("Verifying package", self.step_verify_package),
            ("Installing Python runtime", self.step_python),
            ("Creating directories", self.step_directories),
            ("Installing DeskPilot", self.step_extract),
            ("Creating isolated Python environment", self.step_venv),
            ("Creating owner credentials", self.step_secrets),
            ("Creating persistent database", self.step_database),
            ("Recording owner details", self.step_contracting),
            ("Registering certified capabilities", self.step_capability),
            ("Configuring AI", self.step_model_rights),
            ("Configuring web control centre", self.step_firewall),
            ("Configuring autostart", self.step_autostart),
            ("Configuring backups", self.step_backups),
            ("Recording installation", self.step_record_state),
        ]

    def install(self) -> Report:
        """Run every step in order. Stops at the first failure, fail closed."""
        problems = self.answers.problems()
        if problems:
            return Report("install", (), Failure(
                what_failed="The owner details are incomplete",
                why="; ".join(problems),
                changed=(),
                not_changed=("nothing was installed",
                             "no directories were created",
                             "MetaTrader was not touched"),
                safe_next_action="Go back and complete the owner setup screen."))
        rows: list[Row] = []
        for title, step in self.steps():
            self.log.step(title)
            try:
                result = step()
            except payload.PayloadRefused as refused:
                self.log.write(f"REFUSED: {refused.failure.what_failed}")
                self.log.flush()
                return Report("install", tuple(rows), refused.failure)
            except StepFailed as failed:
                self.log.write(f"FAILED: {failed.detail}")
                rows.append(Row(title, Outcome.FAIL, failed.detail,
                                facts=failed.facts))
                self.log.flush()
                return Report("install", tuple(rows),
                              self._failure(title, failed.detail))
            except OSError as exc:
                detail = f"{exc.strerror or exc}".strip()
                self.log.write(f"FAILED: {detail}")
                rows.append(Row(title, Outcome.FAIL, detail))
                self.log.flush()
                return Report("install", tuple(rows),
                              self._failure(title, detail))
            rows.append(result.row())
            self.log.write(f"  {result.detail}")
        self.log.flush()
        return Report("install", tuple(rows))

    def _failure(self, title: str, why: str) -> Failure:
        """Assemble the §21 explanation from the ledger the steps built."""
        return Failure(
            what_failed=f"{title} failed",
            why=why,
            changed=tuple(self.changed),
            not_changed=self._untouched(),
            safe_next_action=(
                f"Nothing further was changed. Read "
                f"{self.layout.install_log} for the detail, fix the cause, and "
                f"run the installer again - it will reuse what succeeded and "
                f"will not duplicate anything. Your DeskPilot data, if any "
                f"existed, was not altered."),
        )

    def _untouched(self) -> tuple[str, ...]:
        """What is certainly unchanged. MetaTrader is always on this list."""
        out = ["MetaTrader was not touched: no process of its was stopped, "
               "none of its files changed, and none of its firewall rules "
               "altered"]
        if not any("database" in c for c in self.changed):
            out.append("no DeskPilot database was created or modified")
        if not any("firewall" in c for c in self.changed):
            out.append("no firewall rule was added or changed")
        if not any("scheduled task" in c for c in self.changed):
            out.append("no Windows scheduled task was created")
        if not any("Python" in c for c in self.changed):
            out.append("no Python installation on this computer was changed")
        return tuple(out)

    # -- verification ----------------------------------------------------
    def verify(self, *, probe_inference: bool = True) -> Report:
        """Run the authoritative checks against the installed database.

        Pointed at :attr:`Layout.db` explicitly on every command. The defect
        that made this worth stating was ``doctor`` opening a default, empty
        database and reporting on that instead -- a verification that passes
        because it examined nothing.
        """
        rows: list[Row] = []
        state = InstallState.load(self.host, self.layout.state_file)
        if state is not None and state.python_exe:
            self.python_exe = state.python_exe
        if not self.python_exe:
            self.python_exe = ntpath.join(self.layout.venv, "Scripts",
                                          "python.exe")

        if not self.host.exists(self.layout.db):
            return Report("verify", (Row("Database", Outcome.FAIL,
                                         f"no database at {self.layout.db}"),))
        rows.append(Row("Database", Outcome.PASS, self.layout.db,
                        facts={"db": self.layout.db}))

        # ``health``, ``readiness`` and ``doctor`` report state and exit zero;
        # a non-zero from one of them is a real failure. ``setup check`` is
        # different: it exits non-zero whenever the owner's configuration is
        # incomplete, which it always is immediately after an installation --
        # the work source, the payment rail and the activation decisions are
        # the owner's to make later. Treating that as a failed verification
        # would make every successful installation report NOT READY.
        for title, args, informational in (
                ("Health", ("health",), False),
                ("Readiness", ("readiness",), False),
                ("Diagnostics", ("doctor",), False),
                ("Owner configuration", ("setup", "check"), True)):
            result = self.solvent_cli(*args, "--db", self.layout.db)
            if result.ok:
                outcome = Outcome.PASS
            elif informational:
                outcome = Outcome.WARN
            else:
                outcome = Outcome.FAIL
            detail = (result.output.strip().splitlines() or ["no output"])[0][:200]
            if informational and not result.ok:
                detail = (f"{detail} - items remain for you to decide; this "
                          f"does not block the installation")
            rows.append(Row(title, outcome, detail,
                            facts={"exit": result.returncode,
                                   "output": result.output.strip()[:4000],
                                   "db": self.layout.db}))

        rows.append(self._capability_row())
        rows.append(self._ai_row(probe_inference=probe_inference))
        rows.append(self._autostart_row())
        return Report("verify", tuple(rows))

    def _capability_row(self) -> Row:
        """Read the capability back. The installer never certifies it itself."""
        script = (
            "import json,sys\n"
            "from solvent.harness import Solvent\n"
            "s = Solvent(sys.argv[1])\n"
            "out = []\n"
            "for c in s.capability.capabilities():\n"
            "    ok, why = s.capability.may_deploy(c.name)\n"
            "    out.append({'name': c.name, 'proven': bool(c.proven),\n"
            "                'may_deploy': bool(ok), 'why': str(why)})\n"
            "print(json.dumps(out))\n"
        )
        result = self.host.run([self.python_exe, "-I", "-c", script,
                                self.layout.db], cwd=self.layout.app,
                               timeout=300.0)
        self.log.command(result.argv, result.returncode, result.output)
        if not result.ok:
            return Row("Certified capability", Outcome.FAIL,
                       f"could not be read: {result.output.strip()[:200]}")
        try:
            caps = json.loads(result.stdout.strip() or "[]")
        except ValueError:
            return Row("Certified capability", Outcome.FAIL,
                       "the capability list could not be parsed")
        deployable = [c for c in caps if c.get("may_deploy")]
        if not deployable:
            why = "; ".join(f"{c['name']}: {c['why']}" for c in caps) or "none"
            return Row("Certified capability", Outcome.FAIL,
                       f"nothing may be delivered ({why})",
                       facts={"capabilities": caps})
        names = ", ".join(sorted(c["name"] for c in deployable))
        return Row("Certified capability", Outcome.PASS,
                   f"{names} - CERTIFIED, PROVEN, may_deploy=True",
                   facts={"capabilities": caps})

    def _ai_row(self, *, probe_inference: bool) -> Row:
        state = ai.assess(self.host, kind=self.answers.ai_kind,
                          model_tag=self.answers.ai_model_tag,
                          probe_inference=probe_inference)
        return ai.row(state)

    def _autostart_row(self) -> Row:
        if not self.answers.components.autostart:
            return Row("Autostart", Outcome.WARN,
                       "not configured; DeskPilot will not start after a reboot")
        present = integration.task_exists(self.host, integration.TASK_NAME)
        return Row("Autostart",
                   Outcome.PASS if present else Outcome.FAIL,
                   f"{integration.TASK_NAME} "
                   f"{'registered' if present else 'is missing'}")


def readiness_headline(report: Report) -> str:
    """The last screen's single line. ``DESKPILOT READY`` or the blocker."""
    if report.may_continue:
        return "DESKPILOT READY"
    blockers = report.blockers
    if not blockers:
        return "DESKPILOT NOT READY"
    first = blockers[0]
    return f"NOT READY - {first.name}: {first.detail}"
