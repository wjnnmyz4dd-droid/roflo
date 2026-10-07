"""The system check: one screen that says what is here and what is missing.

This is the page the owner reads before anything is written, so it has one
job beyond gathering facts -- it decides whether installation may proceed at
all, and it does that by composing rows whose outcomes carry the verdict. There
is no separate "can we install?" function to fall out of step with the screen,
because :attr:`~deskpilot_installer.report.Report.may_continue` is derived from
the rows the owner was actually shown. What they read is what was decided.

The check writes nothing. Not a directory, not a log, not a database. An owner
who clicks Install and then closes the installer at this screen has an
unchanged computer, and that is a property worth keeping rather than a
coincidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import ai, mt5, payload, python_runtime
from .host import Host
from .layout import Layout, WEB_PORT, forbidden_root
from .report import Outcome, Report, Row

GB = 1024 ** 3

#: Free space DeskPilot needs for itself: application, Python, database growth,
#: and enough backup room that the first week of retention is not a surprise.
MIN_FREE_DISK = 2 * GB
#: Below this, Windows plus DeskPilot plus a trading terminal is already tight.
MIN_RAM = 2 * GB
#: Windows 10 / Server 2016 and later. Earlier releases are out of support and
#: DeskPilot is not tested on them.
MIN_WINDOWS_BUILD = 10


@dataclass(frozen=True, slots=True)
class Settings:
    """What the installer was launched with. Not owner answers -- see steps."""

    layout: Layout = field(default_factory=Layout)
    #: Path to the certified payload the installer carries.
    package_path: str = ""
    #: Digest the installer was built expecting. Empty means it cannot verify.
    expected_digest: str = ""
    needed_ports: tuple[int, ...] = (WEB_PORT,)
    #: Set when the owner has asked for public HTTPS, which needs 80 and 443.
    public_https: bool = False
    #: Skip the live inference probe. Used by the system check, where spending
    #: three minutes loading a model to draw a screen would be absurd.
    probe_inference: bool = False


def windows_row(host: Host) -> Row:
    facts = host.facts()
    if not facts.is_windows:
        return Row("Windows", Outcome.BLOCKED,
                   f"this installer runs on Windows; this is "
                   f"{facts.os_name or 'another operating system'}")
    if not facts.is_64bit:
        return Row("Windows", Outcome.BLOCKED,
                   "64-bit Windows is required")
    return Row("Windows", Outcome.PASS,
               f"{facts.os_name} {facts.os_version} 64-bit",
               facts={"version": facts.os_version})


def admin_row(host: Host) -> Row:
    """Administrator is genuinely required, so say which parts need it."""
    if host.facts().is_admin:
        return Row("Administrator", Outcome.PASS, "granted")
    return Row("Administrator", Outcome.BLOCKED,
               "DeskPilot installs to C:\\DeskPilot, registers a startup task "
               "and adds one firewall rule. All three need Administrator. "
               "Right-click the installer and choose 'Run as administrator'.")


def disk_row(host: Host, layout: Layout) -> Row:
    free = host.free_bytes(layout.root)
    if free < MIN_FREE_DISK:
        return Row("Disk", Outcome.BLOCKED,
                   f"{free // GB} GB free on the DeskPilot drive; "
                   f"{MIN_FREE_DISK // GB} GB is the minimum",
                   facts={"free_bytes": free})
    return Row("Disk", Outcome.PASS, f"{free // GB} GB free",
               facts={"free_bytes": free})


def ram_row(host: Host) -> Row:
    total = host.facts().total_ram_bytes
    if total and total < MIN_RAM:
        return Row("Memory", Outcome.BLOCKED,
                   f"{total // GB} GB of RAM; {MIN_RAM // GB} GB is the minimum",
                   facts={"total_bytes": total})
    if not total:
        return Row("Memory", Outcome.WARN, "could not be determined")
    return Row("Memory", Outcome.PASS, f"{total // GB} GB",
               facts={"total_bytes": total})


def location_row(layout: Layout) -> Row:
    why = forbidden_root(layout.root)
    if why:
        return Row("Install location", Outcome.BLOCKED,
                   f"{layout.root} is {why}")
    return Row("Install location", Outcome.PASS, layout.root)


def existing_row(host: Host, layout: Layout) -> Row:
    """Is DeskPilot already here, and does it have data worth keeping?"""
    app = host.is_dir(layout.app)
    db = host.exists(layout.db)
    if not app and not db:
        return Row("Existing DeskPilot", Outcome.PASS, "NONE - a fresh install")
    if db:
        return Row("Existing DeskPilot", Outcome.PASS,
                   "UPDATE AVAILABLE - an existing installation with a "
                   "database was found. Its data, configuration and audit "
                   "history will be preserved.",
                   facts={"db": layout.db, "app": layout.app})
    return Row("Existing DeskPilot", Outcome.PASS,
               "application files found with no database; they will be replaced",
               facts={"app": layout.app})


def ports_row(host: Host, settings: Settings,
              presence: mt5.Presence) -> tuple[Row, dict[int, str]]:
    """Port availability, and who owns anything that is taken."""
    wanted = list(settings.needed_ports)
    if settings.public_https:
        wanted += [80, 443]
    owners: dict[int, str] = {}
    taken: list[int] = []
    for port in wanted:
        if host.port_in_use(port):
            taken.append(port)
            owners[port] = host.port_owner(port) or "an unknown process"
    if not taken:
        return Row("Ports", Outcome.PASS,
                   f"AVAILABLE ({', '.join(str(p) for p in wanted)})"), owners
    described = ", ".join(f"{p} (held by {owners[p]})" for p in taken)
    # The control-centre port is a hard requirement. 80 and 443 are not: a
    # proxy already holding them is a normal, even desirable, arrangement, and
    # DeskPilot's own backend stays on loopback regardless.
    blocking = [p for p in taken if p in settings.needed_ports]
    if blocking:
        return Row("Ports", Outcome.BLOCKED,
                   f"CONFLICT - {described}. DeskPilot will not take a port "
                   f"from another program.",
                   facts={"owners": {str(k): v for k, v in owners.items()}}), owners
    return Row("Ports", Outcome.WARN,
               f"{described} - DeskPilot's control centre stays on loopback "
               f"{WEB_PORT}; public HTTPS will need the existing listener "
               f"reconfigured as a proxy by hand.",
               facts={"owners": {str(k): v for k, v in owners.items()}}), owners


def protected_row(presence: mt5.Presence) -> Row:
    """MetaTrader. Always a PASS: its presence never blocks DeskPilot."""
    if not presence.present:
        return Row("Protected applications", Outcome.PASS,
                   "no MetaTrader installation detected")
    return Row("Protected applications", Outcome.PASS, presence.summary,
               facts={"directories": list(presence.directories),
                      "processes": list(presence.processes)})


def system_check(host: Host, settings: Settings) -> Report:
    """Assemble the owner's first screen. Reads only; writes nothing."""
    presence = mt5.detect(host)
    rows: list[Row] = [
        windows_row(host),
        admin_row(host),
        location_row(settings.layout),
        disk_row(host, settings.layout),
        ram_row(host),
    ]

    found = python_runtime.discover(host)
    chosen = python_runtime.choose(found)
    rows.append(python_runtime.row(found, chosen))

    verdict = payload.verify(host, settings.package_path, settings.expected_digest)
    rows.append(payload.row(verdict))

    rows.append(existing_row(host, settings.layout))
    rows.append(protected_row(presence))

    port_row, owners = ports_row(host, settings, presence)
    rows.append(port_row)

    clash = mt5.conflict(presence, settings.needed_ports, owners)
    if clash:
        rows.append(Row("MetaTrader coexistence", Outcome.BLOCKED, clash))

    facts = host.facts()
    recommendation = ai.recommend(facts.total_ram_bytes,
                                  host.free_bytes(settings.layout.root),
                                  mt5_present=presence.present,
                                  cpu_count=facts.cpu_count)
    rows.append(_model_row(recommendation))

    report = Report("system-check", tuple(rows))
    if not verdict.verified:
        # The package row already blocks. Attaching the structured refusal as
        # well means the installer has one object to show and the owner is told
        # what was *not* changed, which is the question they will ask.
        return Report("system-check", tuple(rows), payload.refusal(verdict))
    return report


def _model_row(rec: ai.Recommendation) -> Row:
    if rec.cloud_instead:
        return Row("AI model", Outcome.ACTION, rec.reason,
                   facts={"rejected": [{"tag": t, "why": w}
                                       for t, w in rec.rejected]})
    assert rec.model is not None
    detail = f"recommended: {rec.model.display} - {rec.reason}"
    if not rec.model.can_autoclear:
        detail += ("; its commercial rights will need a digest you verify "
                   "before DeskPilot will use it")
    return Row("AI model", Outcome.PASS, detail,
               facts={"tag": rec.model.tag, "licence": rec.model.licence,
                      "autoclear": rec.model.can_autoclear,
                      "rejected": [{"tag": t, "why": w}
                                   for t, w in rec.rejected]})
