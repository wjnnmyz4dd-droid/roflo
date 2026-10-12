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
| Source HEAD | `24520a4` — built from a clean tree |
| Size | 1,641,051 bytes |
| Installer SHA-256 | `0dab9f3d0edf4e3bfbd2f47d992a07b841a2f4f9ca897d900b4d4322e0f5b524` |
| Provenance | `dist/BUILD-24520a4.txt` |
| Payload | `DeskPilot-certified-24520a4.zip` |
| Payload SHA-256 | `dee6bcc40c970170c71b41c9d955cabbb83e8cdbbff9e387e07ef24ec1718186` |
| Code signature | **none** |
| Windows execution | **not verified** |

Verified by extracting the executable rather than by reading the build script:
the embedded payload's SHA-256 matches the payload built beside it, all
sixty-six shipped `solvent/` modules are byte-identical to the repository's,
and what the payload can do was read from the *shipped* syntax trees rather
than by searching for strings — `checks.REGISTRY` names the five capabilities,
`harness.UNCERTIFIABLE` names `spreadsheet-automation.number_format` as the one
check no evidence can certify, and `harness.promote_capability`,
`harness.owner_promotions` and `harness.certifiable_scope` are all present with
`solvent setup capability` carrying its `--only` and `--why` flags.

**Why this build exists.** The previous one cannot promote a capability: the
code for it did not exist yet. The owner's authorisation of 2026-10-12 cleared
four certified capabilities for client work, and `promote_capability` — the
path that records LIMITED → APPROVED as a new decision while leaving the
LIMITED row on the append-only record — ships here for the first time. An
installation running the previous payload has no `setup capability` command
that can promote anything; it would refuse with an unrecognised argument, not
silently do nothing.

What is new in this build, and what it means for an existing installation:

* `harness.promote_capability` and the four refusals that guard it: only a
  registered owner, only a capability this build has, only a scope the
  re-run certification covers, and only above the evidence floor — plus a
  final check that `may_deploy` actually returns true afterwards, so a
  promotion delivery would still refuse raises instead of reporting success;
* `solvent setup capability` now reports, per capability, the verifier and its
  implementation fingerprint, how many of its listed checks are certified, any
  check withheld *by name*, the black-box evidence summary, and the previous
  decision where there was one. On a partial refusal it names what *was*
  cleared as plainly as what was not, because the dangerous reading of a
  non-zero exit is "nothing happened";
* the Control Centre's skill library section reads the owner's stance from the
  registry, so all five now show as cleared with `spreadsheet-automation`'s
  14-of-15 evidence in its own separate column.

**Installing this still promotes nothing.** No install, repair or upgrade step
runs `setup capability` — verified against the shipped engine and the wizard
script, neither of which references it. The promotion records travel in the
payload as *data*; the decision is the owner running the command. A capability
already promoted on an existing database stays promoted, because the decision
lives in the database and not in the payload.

A repair keeps the database, the owner signing key, the control-centre
password hash, the API keys, the settings and the backups.

**Do not install this on the VPS without deciding to.** No Windows machine has
run it.


## Superseded

Listed newest first. Each was superseded by the row above it, for the reason
given. Do not install any of these.

| Installer SHA-256 | HEAD | Superseded because |
|---|---|---|
| `bb1525a66c6ad4d5a3783d11d12e4aeffe321589d0df75a948e57f25fd44dad2` | `16b32bd` | **Superseded, not defective.** It was the first build to carry the five-skill library and the redaction of verification evidence, and all of that is carried forward. Superseded because its payload is Solvent *before* `promote_capability` existed: an owner running it cannot clear a certified capability for client work, because `solvent setup capability` on that build has no path from LIMITED to APPROVED. Four of its five capabilities are stuck at LIMITED with no command that can change it. Provenance `dist/BUILD-16b32bd.txt`. |
| `18f35c8ab2f2d076db293f32b36c812a418547962ba50b2e6eb8772d251c54dd` | `a5bdfc0` | **Superseded, not defective.** It was the first build to confine the control centre to its own Windows identity and to test that boundary as that identity, and all of that is carried forward. Superseded because its payload is Solvent before the five-skill library: it ships `csv-cleanup` and `report-builder` and none of `spreadsheet-automation`, `data-entry` or `document-processing`, nor the `.xlsx` and `.docx` layers they need. It also predates the redaction of verification evidence, so a credential inside a client's document would reach the audit journal in plain text on that build. |
| `a0e64d00711b1950b3546080e06a7c5e4198593ff02f4e00c6fc654ee6d48361` | `237b63d` | **Superseded, not defective.** Installed, repaired and started both services correctly. Superseded because both services ran as SYSTEM with the same token, so the control centre's confinement was enforced by its own code rather than by Windows; its launcher read the file containing the owner signing key on every boot and filtered it in Python; and nothing recorded `runtime.heartbeat_path`, so the runtime wrote its liveness file to `C:\var\lib\solvent\heartbeat` and the control centre had no way to tell whether the runtime was running. |
| `cf529639c9bf68468154106191dab8ec386136282fc54909d2fda40594e0e3c9` | `5789392` | **Superseded.** Installed and repaired correctly, but shipped a runtime that could not start: `DEFAULT_INTERVALS` had no `heartbeat` cadence and `interval()` used a bare subscript, so registering the first task raised `KeyError` out of `serve()` — after `start()` had already recorded `runtime.started` with `health=HEALTHY`. The runtime died before its first tick on every launch, so no diagnostics were ever recorded. The heartbeat tick also used `Path` without importing it. |
| `ce724f84f6875ada13b3f83a199551b3d7c9a5048642c6ced0e1f1576c8fc960` | `30113a9` | **Superseded, not defective.** Installed and repaired correctly, but could only ever run the `install` phase: the engine's `repair` was unreachable, so the only route to a broken installation was a full reinstall. It also verified nothing — the "Verify installation" page displayed a label saying it was checking and ran no check, and the finish screen's title was the fixed string "DeskPilot is installed". |
| `51e80e3fee113571a23c25c4edd4e1426596a511c68946133cd8ab8c8aaf6aeb` | `53ce33c` | **Defective.** Neither background service ran. The runtime's generated launcher passed `--spool` to `solvent run`, which rejects unrecognised arguments and exits 2, so the scheduled task failed at every boot and Windows gave up after ten attempts. The control centre was never registered as a service at all, so `127.0.0.1:8765` refused connections. Both tasks also carried only a boot trigger and nothing ran them, so even a correct installation was down until the owner rebooted. Fixing the repair path found two more: an installation over existing owner data rotated the owner signing key, and an upgrade never rewrote the generated launchers. |
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
