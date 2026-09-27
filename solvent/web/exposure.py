"""Is the control centre reachable by anyone it should not be?

A control centre is the one part of Solvent that deliberately listens, so it is
the one part that can be exposed by accident. This module answers that question
from the *configuration that will actually be used*, not from a document
describing intent — a firewall the owner meant to enable protects nothing.

It decides nothing and changes nothing. It reports, and the readiness projection
refuses activation on what it reports.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass

#: Ports a control centre has no business listening on directly. 80 and 443
#: belong to a reverse proxy that terminates TLS; binding them here would mean
#: the owner's password crossing a network in clear text.
PRIVILEGED = (80, 443)


@dataclass(frozen=True, slots=True)
class Finding:
    name: str
    safe: bool
    detail: str
    remedy: str = ""


def _is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host in ("localhost", "localhost.localdomain")


def assess(*, host: str, port: int, behind_proxy: bool = False,
           tls_terminated: bool = False, secure_cookies: bool = True,
           firewall_default_deny: bool | None = None,
           password_configured: bool = True) -> list[Finding]:
    """Every way this binding could be wrong, as separate findings.

    ``firewall_default_deny`` is ``None`` when nobody has said. Unknown is not
    safe: a host whose firewall posture has not been established is exactly the
    host somebody forgot.
    """
    findings: list[Finding] = []

    loopback = _is_loopback(host)
    wildcard = host in ("0.0.0.0", "::", "")
    findings.append(Finding(
        "bind address",
        loopback or behind_proxy,
        f"listening on {host}" + (" (loopback)" if loopback else ""),
        "" if loopback or behind_proxy else
        "bind 127.0.0.1 and put a reverse proxy in front, or say explicitly "
        "that a proxy is already there"))
    if wildcard and not behind_proxy:
        findings.append(Finding(
            "wildcard bind", False,
            f"{host} accepts connections on every interface this host has",
            "a control centre on every interface is reachable from anywhere the "
            "host is; bind loopback instead"))

    findings.append(Finding(
        "privileged port", port not in PRIVILEGED,
        f"port {port}",
        "" if port not in PRIVILEGED else
        "80 and 443 belong to the proxy that terminates TLS; binding one here "
        "means the owner's password crosses the network in clear text"))

    findings.append(Finding(
        "transport encryption", tls_terminated or loopback,
        "TLS terminated by a proxy" if tls_terminated else
        ("loopback only, so nothing crosses a network" if loopback else
         "no TLS recorded for a non-loopback binding"),
        "" if tls_terminated or loopback else
        "terminate TLS at the proxy before exposing this"))

    findings.append(Finding(
        "secure cookies", secure_cookies or loopback,
        "session cookie carries Secure" if secure_cookies else
        "session cookie omits Secure",
        "" if secure_cookies or loopback else
        "--insecure-cookies is for local plain-HTTP testing only; a session "
        "cookie without Secure can cross a plain connection"))

    findings.append(Finding(
        "inbound firewall", firewall_default_deny is True,
        {True: "default deny recorded",
         False: "the host firewall does not default to deny",
         None: "nobody has recorded the host firewall posture"}[
            firewall_default_deny],
        "" if firewall_default_deny is True else
        "set the host firewall to deny inbound by default and allow only the "
        "proxy's port; then record it, because an unrecorded posture is the one "
        "somebody forgot"))

    findings.append(Finding(
        "authentication configured", password_configured,
        "a web password hash is set" if password_configured else
        "no web password is configured",
        "" if password_configured else
        "run `solvent web-password`; with no hash the control centre refuses to "
        "start rather than admitting everybody"))

    return findings


def unsafe(findings: list[Finding]) -> list[Finding]:
    return [f for f in findings if not f.safe]


def summary(findings: list[Finding]) -> str:
    bad = unsafe(findings)
    if not bad:
        return f"{len(findings)} exposure check(s) pass"
    return f"{len(bad)} of {len(findings)} exposure check(s) unsafe: " + "; ".join(
        f.name for f in bad)
