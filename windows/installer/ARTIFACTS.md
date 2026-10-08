# DeskPilot Windows installer — artifact register

Every installer this project has produced, with its status. Kept for audit:
a superseded artifact is not deleted, because the question "what was I given,
and was it the one with the defect?" has to stay answerable. Each row's hash is
the whole of its identity.

**Nothing here is code-signed.** No Windows code-signing certificate exists in
the build environment, so no Authenticode signature has been applied to any of
these files. Windows will show an unknown-publisher warning for all of them.
See "Signing status" below.

## Current

| | |
|---|---|
| File | `DeskPilot-Setup.exe` |
| Status | **CURRENT — unsigned, Windows-unverified** |
| Installer version | 1.0.0 |
| Source HEAD | see `dist/BUILD.txt` — the build refuses to run unless the installer source is committed, so the recorded commit describes the payload *and* the engine |
| Size | see `dist/BUILD.txt` |
| Installer SHA-256 | see `dist/BUILD.txt`, written by the build — an installer cannot record its own hash in a file that is part of the commit it is built from |
| Payload | `DeskPilot-certified-b938e73.zip` |
| Payload SHA-256 | `b9e324be0fd00bf949fb287e06b9de33b5ed5febce43765bc0da75ef0e442106` |
| Code signature | **none** |
| Windows execution | **not verified** |

## Superseded

Listed newest first. Each was superseded by the row above it, for the reason
given. Do not install any of these.

| Installer SHA-256 | HEAD | Superseded because |
|---|---|---|
| `096ae82cef666c7c0a21f07f453f7c7518fcfdd4249f2ee474bfbf0157cdaccb` | payload `b938e73`, engine uncommitted | **Provenance not fully recorded.** Built from a working tree whose engine changes were not yet committed, so its recorded commit described its payload and not its installer code. Superseded for that reason alone — the code in it is the code that was then committed. The build now refuses this state outright (`require_clean_engine`). |
| `6c57b1a5ca732df4534105374bcaf1b0cbaa4a06df1c6cdb2fa5a83c7a29710d` | `b938e73` | **Defective.** The packaged engine could not start: `cli.py` was run as a script although it is a package module using relative imports, so the install phase raised `ImportError: attempted relative import with no known parent package`. Five further packaging and command-contract defects were found in the same audit. |
| `688fd64cda068ed35788fdbcdb42ab96e566ebe58ff75b1bdc54b79e245f9c61` | `0fb9056` | **Defective.** Python detection searched four fixed directories and consulted neither the registry nor PATH, so a per-user python.org installation — the default — was invisible and the owner was told to install a Python they already had. Also carried the entry-point defect above. |
| `da100231f296701a9c2ed51c1d3b8be84932f606096b914cb38561ce9c9fdfde` | `1450e77` | Pre-release build. Carried both defects above. |
| `60c94fd8733ee8e85f73e02b4199a93e0d26250f222d64ad5ec9fc1da3226054` | `1450e77` | First compiled build, superseded within the same session by a dead-code removal. |

## Signing status

| Kind of signing | State | Detail |
|---|---|---|
| Git commit signing | **available** | SSH signing through the environment's helper. Key `SHA256:32dP45eSMmVSt/G/CGvcxl/P+MO3Nwj9xeTh/GSA2wc` (ssh-ed25519). |
| Windows code signing | **unavailable** | No code-signing certificate exists in this environment, and none ever did. No `.pfx`, `.p12`, `.spc` or `.pvk` signing material; no `signtool`, `osslsigncode` or `jsign` installed. |

The only `.pfx`/`.p12` files on the machine are
`/usr/share/cmake-3.28/Templates/Windows/Windows_TemporaryKey.pfx` — a
placeholder CMake ships with its UWP project template — and
`/root/.ccr/java-truststore.p12`, a CA trust store. Neither is a code-signing
identity, and signing with either would produce a signature that asserts
something untrue about who built this. It has not been done.

## What signing would require

A code-signing certificate from a CA in the Microsoft Trusted Root Program
(DigiCert, Sectigo, SSL.com and others), issued to the owner's legal identity.
Since June 2023 all newly issued code-signing certificates must have their
private keys held in hardware — an HSM or a FIPS-140-2 Level 2 token — so the
key cannot be copied into a build container. Signing therefore happens either
on a machine with the token attached, or through the CA's cloud signing
service.

Once a certificate exists, the artifact can be signed without rebuilding:

```
signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 ^
  /n "<the certificate's subject name>" DeskPilot-Setup.exe
signtool verify /pa /v DeskPilot-Setup.exe
```

The timestamp is not optional in practice: without it the signature stops
validating the day the certificate expires.
