# DeskPilot — Windows installer certification

Date: 2026-10-21 · Target: Windows 11 x64 · Branch
`claude/solvent-architecture-audit-8ot9et`

The installer's logic, certified against its simulated host on Linux.
**The installer has never been run on Windows**, and §7 says what that
leaves open.

---

## 1. Python requirements, as the installer enforces them

| Requirement | Where |
|---|---|
| **3.11 or newer** | `MIN_VERSION = (3, 11)`; anything older is rejected by name |
| **64-bit** | rejected explicitly if `sys.maxsize > 2**32` is false |
| Never inferred from a path | each candidate is run with `-I -c` and asked `sys.version_info` and `sys.maxsize` |
| Minor versions searched | `Python313`, `Python314`, `Python312`, `Python311`, newest first — the patch level never appears in an install path |

Five independent discovery routes, each tested on its own, because a
test supplying two at once passes when only one is consulted:

* PEP 514 registry
* `PATH`
* the `py` launcher at `C:\Windows\py.exe`
* all-users roots (`C:\Program Files`, `C:\Program Files (x86)`, `C:\`)
* per-user `%LOCALAPPDATA%\Programs\Python` — **python.org installs
  per-user by default with "Add to PATH" unticked**, which is how a
  perfectly good interpreter was once invisible

**Another application's Python is refused**, by design: a path
containing `metatrader`, `metaquotes`, `\mt4\` or `\mt5\` is rejected
with "it belongs to MetaTrader; DeskPilot never uses another
application's Python." On the owner's machine, which runs MetaTrader 5,
this is the likeliest reason a seemingly present Python is not used.

## 2. Exit codes

All eight the brief names, interpreted and tested:

| Code | Meaning | What DeskPilot does |
|---|---|---|
| **0** | installed | continue |
| **3010** | `ERROR_SUCCESS_REBOOT_REQUIRED` | **success** — continue, and record that Windows wants a restart |
| **1602** | cancelled | stop; accept the elevation prompt and re-run |
| **1603** | fatal error | stop; repair or remove an existing Python 3.13, then re-run |
| **1618** | another installation running | stop; wait for it — Windows Update counts |
| **1638** | a conflicting Python 3.13 present | stop; remove it or install by hand |
| **5** | access denied | stop; run as Administrator |
| **740** | elevation required | stop; run as Administrator |
| anything else | unrecognised | stop, **naming the number**, and point at a manual install |

**3010 was previously classified as a failure.** `Result.ok` is
`returncode == 0`, so DeskPilot aborted an installation in which Python
had installed correctly, told the owner it had failed, and would have
*found* that Python on the next run. Repaired: `BOOTSTRAP_OK_CODES =
{0, 3010}`.

**The exit code previously never reached the owner.** `/quiet` means
the installer writes essentially nothing, and the failure message was
built from its output — so the owner read "the Python installer
reported failure:" followed by nothing at all. The code is now printed
once, with its meaning and a remedy, and whatever the installer did
manage to say is carried through.

## 3. ARM64

`BOOTSTRAP_URL` is the **amd64** build and there is no ARM64 pin.
Nothing checked the machine, so Windows on ARM received an
incompatible installer and a generic failure.

`bootstrap_supported()` now reads `PROCESSOR_ARCHITEW6432` **before**
`PROCESSOR_ARCHITECTURE` — the first lies under emulation — and refuses
before anything is downloaded, naming the architecture and pointing at
a manual ARM64 install. Checked **only when a bootstrap is actually
needed**: an ARM64 machine that already has a usable Python is served
normally, and refusing it for an architecture that never comes up would
be a fault of its own.

This compounds with the dependency audit: `python-docx` needs `lxml`,
whose ARM64 Windows wheels are not reliably published.

## 4. The download

| | |
|---|---|
| Source | `python.org`, 29,096,776 bytes |
| Pin | SHA-256 `96159fcb…f981`, cross-checkable against the publisher's MD5 |
| Gate | **both** size and digest must match, or the file is never executed |
| Fetch | `curl.exe -sS -L --fail`, 900s timeout |
| Install | `/quiet InstallAllUsers=1 PrependPath=0 Include_launcher=0`, 1800s timeout |

`PrependPath=0` and `Include_launcher=0` are deliberate: DeskPilot uses
an absolute path to its own interpreter and never needs `PATH`, and the
`py` launcher is global state another application may already own.

A timeout is now distinguished from a non-zero exit, because they need
different remedies.

## 5. Installation succeeds only when Python is independently found

After the bootstrap, discovery runs **again** against the real machine.
If nothing usable is found, the step fails with "Python was installed
but could not then be found" — the installer does not take its own
word for it.

## 6. Verification performed

```
tests_solvent/test_installer_*            510 passed (16 new)
tools/mutate_installer.py --only <each>     4 new mutants, all KILLED
```

New mutants: `reboot-required-reads-as-a-failed-install`,
`the-arm64-check-never-fires`, `an-arm64-machine-downloads-anyway`,
`the-exit-code-never-reaches-the-owner`.

One pre-existing test pinned the old wording; it now asserts the
**stronger** message — the exit code and the installer's own output —
rather than being relaxed to fit.

## 7. UNVERIFIED ON WINDOWS

The installer has not been executed on Windows. Specifically unproven:

* that registry, launcher and per-user discovery find a real Python
* that the bootstrap runs, and what it returns on a real machine
* elevation, and the behaviour of a standard (non-admin) account
* the 3010 path end to end, including resuming after the restart
* `%PROGRAMDATA%` and `C:\DeskPilot` ACLs, and the spool drop box
* Defender or an antivirus interfering with the download or the venv
* uninstallation

The pending-reboot requirement in the brief — *record the state, stop
safely, explain the next action, resume after restart* — is **partly
met**: 3010 is recorded as success-needing-restart and the message says
so. Resuming verification after a restart has not been exercised,
because that needs a machine that can restart.

## 8. Status

**WINDOWS INSTALLER: UNVERIFIED** on Windows; **PASS** against its
simulated host, with three defects found and repaired in this round.
