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
| Status | **CURRENT — unsigned test build, Windows-unverified** |
| Installer version | 1.0.0 |
| Source HEAD | `53ce33c` — built from a clean tree, so the commit describes the payload *and* the engine |
| Size | 1,332,395 bytes |
| Installer SHA-256 | `51e80e3fee113571a23c25c4edd4e1426596a511c68946133cd8ab8c8aaf6aeb` |
| Provenance | `dist/BUILD-53ce33c.txt` (and `dist/BUILD.txt` until the next build) |
| Payload | `DeskPilot-certified-53ce33c.zip` |
| Payload SHA-256 | `771e75c425bb3799fcaa5201cd8b3bcab4129d3657f315c6ba0c2fbcd9151998` |
| Code signature | **none** |
| Windows execution | **not verified** |

Verified after compiling, by extracting the executable rather than trusting the
build log: the embedded payload's SHA-256 equals both the payload built beside
it and the digest compiled into the wizard; all seventeen engine modules and
the bootstrap launcher in the executable are byte-identical to the repository's;
the shipped `cli.py` and `selfcheck.py` contain the credential-transfer repair;
and the `.nsi` that `makensis` compiled is byte-identical to the committed blob
at `53ce33c`.

This is a **test build, not a production release.** The distinction is not
cosmetic: a production release would carry an Authenticode signature from a
certificate issued to the owner's legal identity, and this file carries none.
Windows will warn that the publisher is unknown. Check the SHA-256 above before
running it.

Throwaway builds made with `--allow-dirty` during development are not listed
here. They are not artifacts: their own provenance file marks them
`NOT FOR RELEASE`, and none was delivered.

## Superseded

Listed newest first. Each was superseded by the row above it, for the reason
given. Do not install any of these.

| Installer SHA-256 | HEAD | Superseded because |
|---|---|---|
| *not recoverable — see note below* | `b938e73` | **Defective.** The owner's details never reached the engine. The wizard stored them in `$R5` and handed them over with `t r5`, which the System plugin reads as `$5`, not `$R5`; `$5` held the AI backend name, so the engine received the word `ollama` where a JSON document belonged, found no owner details, and told an owner who had typed a password that one was needed. The engine also defaulted silently instead of saying the transfer had failed. Four further defects were found auditing the stages this one was not in. |
| `096ae82cef666c7c0a21f07f453f7c7518fcfdd4249f2ee474bfbf0157cdaccb` | payload `b938e73`, engine uncommitted | **Provenance not fully recorded.** Built from a working tree whose engine changes were not yet committed, so its recorded commit described its payload and not its installer code. Superseded for that reason alone — the code in it is the code that was then committed. The build now refuses this state outright (`require_clean_engine`). |
| `6c57b1a5ca732df4534105374bcaf1b0cbaa4a06df1c6cdb2fa5a83c7a29710d` | `b938e73` | **Defective.** The packaged engine could not start: `cli.py` was run as a script although it is a package module using relative imports, so the install phase raised `ImportError: attempted relative import with no known parent package`. Five further packaging and command-contract defects were found in the same audit. |
| `688fd64cda068ed35788fdbcdb42ab96e566ebe58ff75b1bdc54b79e245f9c61` | `0fb9056` | **Defective.** Python detection searched four fixed directories and consulted neither the registry nor PATH, so a per-user python.org installation — the default — was invisible and the owner was told to install a Python they already had. Also carried the entry-point defect above. |
| `da100231f296701a9c2ed51c1d3b8be84932f606096b914cb38561ce9c9fdfde` | `1450e77` | Pre-release build. Carried both defects above. |
| `60c94fd8733ee8e85f73e02b4199a93e0d26250f222d64ad5ec9fc1da3226054` | `1450e77` | First compiled build, superseded within the same session by a dead-code removal. |


### Why one hash above is not recoverable

That installer's SHA-256 was recorded only in `dist/BUILD.txt`, which is not in
the repository and which every subsequent build overwrote. `makensis` embeds a
build timestamp, so rebuilding `b938e73` does not reproduce the file either.
The hash is therefore gone, and saying so is better than offering one that
would not match.

This was a defect in the register itself, and it is fixed: each build now also
writes `dist/BUILD-<commit>.txt`, which later builds leave alone, and the rows
above point at those files.

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
