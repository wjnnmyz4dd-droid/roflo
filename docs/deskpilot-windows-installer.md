# DeskPilot on Windows

## For the owner: the whole procedure

1. Double-click **DeskPilot-Setup.exe** and allow it when Windows asks for
   Administrator.
2. Click **Install DeskPilot**, then read the system check and click
   **Continue**.
3. Fill in **Owner setup**: your name, your email, a password of at least 12
   characters.
4. Choose an **AI setup** option and click **Continue**.
5. When it finishes, click **Open the DeskPilot control centre**.

That is all of it. There is no ZIP to extract, no PowerShell, no `pip`, no
paths to type, no database to create, no capability to register by hand, no
Task Scheduler entry to configure, and no health check to run yourself. If
something goes wrong the installer tells you what failed, what it changed,
what it did not change, and what to do next.

**MetaTrader is not touched.** If it is on the machine the installer says so
and leaves it entirely alone — it is not stopped, restarted, reconfigured, or
supervised, and no rule of its is altered.

---

## Why it is built this way

### Two layers, and why the line is where it is

`DeskPilot-Setup.exe` is compiled with NSIS and contains almost no judgement.
It draws the screens, asks for Administrator, finds or installs Python,
registers the uninstaller — and delegates every decision to a pure
standard-library Python package, `deskpilot_installer`, which it carries
inside itself.

The split is what makes the installer testable. Decisions live where a test can
reach them through an injected host, so the 25 Windows scenarios in §23 of the
installation contract are exercised as unit tests on any machine. Had the logic
been written in NSIS script it would be verifiable only by running Windows
installers by hand, which is how installers come to be shipped on hope.

The division also sets the honest limit of that testing: these tests prove the
installer reaches the right decision when the machine reports a given state.
They cannot prove `schtasks.exe` accepts the task XML, because nothing on the
test side of the seam executes Windows. Both halves are stated in the
certification report rather than blurred.

### The installer is an orchestrator, not an authority

It does not decide whether a capability is certified, whether the model's
rights are cleared, whether the installation is ready, or what the firewall
posture is. Each of those already has an authority inside Solvent, and the
installer reaches them by running the installed application's own command-line
interface against the installed database:

| Concern | Authority | What the installer does |
|---|---|---|
| Database creation | `solvent.store.Store` | passes the real path to every command |
| Capability promotion | `solvent setup capability` | invokes it; Solvent re-runs the verifier |
| Deployability | `capability.may_deploy` | reads it back, never infers it |
| Model rights | `solvent setup model` | invokes it, only for an evidenced digest |
| Firewall posture | `solvent setup firewall` | records it after enacting one scoped rule |
| Health, readiness, diagnostics | `health`, `readiness`, `doctor` | runs each against `C:\DeskPilot\Data\solvent.db` |
| Password hashing | `solvent.web.auth.hash_password` | asks the application, over stdin |

A structural test asserts this: the installer package may not define
`certify`, `promote_capability`, `compute_readiness` or `approve`, and may not
contain `pbkdf2_hmac`.

### No pip, and how the application gets on the import path

DeskPilot imports nothing outside the standard library — its whole suite passes
with site-packages switched off — so "install Python" is the entire dependency
story and there is no `pip` step in a normal installation.

The application is reached through a `.pth` file in DeskPilot's own virtual
environment. That is enough for `import solvent` to work under `python -I`,
which is the point: `-I` makes the interpreter ignore `PYTHONPATH`, skip user
site-packages, and keep the current directory off `sys.path`. A planted
`json.py` beside the installer therefore cannot be imported by DeskPilot
running as Administrator, and neither can an attacker's `PYTHONPATH`. The
`.pth` grants reach; `-I` removes everything else.

### Autostart is a scheduled task, not a service

A real Windows service must speak the Service Control Manager protocol, which
Python cannot do without `pywin32` or a wrapper such as `nssm`. Both are
third-party, and adding one to get a nicer entry in `services.msc` would trade
away the property that makes this installer simple. Task Scheduler is native,
starts at boot with no logged-in user, and — through the XML definition rather
than the shorter command-line form — restarts the process when it dies. That is
every requirement actually stated.

### The firewall change opens nothing

The control centre binds loopback, and Windows does not filter loopback
traffic, so no inbound allow rule is needed for it to work. Adding one would be
the installer quietly making an internal service reachable. The single rule
created is a **block**: inbound, public profile, DeskPilot's own interpreter. It
changes nothing today and means a later mistake does not silently expose the
business. Nothing else is touched, and uninstall removes that one rule by name.

### Finding the Python you already have

This is the part that was wrong, and it is worth describing precisely because
the symptom was so misleading.

An owner with a working Python 3.13.12 was told the installer would install
Python. The version was never the problem: every 3.13.x release installs into a
directory called `Python313`, and 3.13 clears the 3.11 floor comfortably. The
**search** was the problem. It looked in four fixed directories — two under
`Program Files`, two at the root of `C:` — and nowhere else. python.org's
installer defaults to a **per-user** install, into
`%LOCALAPPDATA%\Programs\Python\Python313`, and leaves **"Add python.exe to
PATH" unticked**. Both defaults put a perfectly good interpreter outside
everything the installer looked at. Worse, the wizard — the half that draws the
system-check screen and decides whether to install Python — consulted neither
`PATH` nor the registry at all.

The search now has five routes, and each is tested in isolation:

| Route | Why it is there |
|---|---|
| **Registry (PEP 514)** | The authoritative record on Windows. python.org registers itself here regardless of where it installed or whether PATH was touched. Read from `HKCU` and `HKLM`, in both the 64- and 32-bit views — a 32-bit process reading the plain path is redirected into `WOW6432Node` and misses every 64-bit entry. |
| **PATH** | Whatever `python` means to this process. |
| **`py -0p`** | The launcher knows every registered install, including ones registered unusually. |
| **All-users locations** | `Program Files`, `Program Files (x86)` and the root of `C:`, for 3.11 through 3.14. |
| **Per-user locations** | `AppData\Local\Programs\Python` under **every** profile on the machine — not just the current one. The installer is elevated, so `%LOCALAPPDATA%` may be the administrator's while the owner's Python sits in theirs. |

The wizard's fallback list is **generated from the engine's** by `build.py` into
`python-search.nsh`. Two hand-maintained lists is two sources of truth, and the
one that drifts is the one nobody is testing — which is exactly how the engine
came to see a PATH interpreter that the wizard could not.

When nothing is found the screen now says how many locations were checked and
names the registry among them, so "it did not find my Python" becomes a
question with an answer instead of a dead end.

### Which Python it installs, when it has to

DeskPilot declares 3.11 or newer, and 3.11 is what the suite is proven against.
But python.org no longer publishes a **Windows installer** for 3.11 or 3.12 —
both are security-only, source only. So on Windows the real choice is 3.13 or
newer. The floor stays at 3.11 so an existing 3.11 or 3.12 is reused rather
than replaced; the bootstrap fetches 3.13.12, and only when no usable
interpreter exists at all.

That download is verified against a SHA-256 compiled into the installer, and a
mismatch aborts without running it. The digest's provenance is one download
from the official HTTPS URL; the MD5 python.org publishes is recorded beside it
so the owner can check the pin against the publisher rather than against this
installer.

An interpreter found inside a MetaTrader directory is discarded by path before
it is even version-checked. Adopting it would mean DeskPilot's packages and
MetaTrader's sharing a `site-packages`, which is the one way a back-office
installer could break someone's trading.

### Model choice, and the trap in it

Qwen2.5 is **not** uniformly Apache-2.0. This repository's own model-rights
evidence records that the 3B is released under `qwen-research` — "FOR
NON-COMMERCIAL PURPOSES ONLY" — and that the 72B carries a conditional licence.

The 3B is also exactly the model a reasonable sizing algorithm picks for a small
VPS: same family, right size, obviously correct. Picking it would put a business
that sells its output in breach. So the catalogue carries the licence per size
and the recommender refuses a non-commercial model however well it fits. At
8 GB with MetaTrader running there are about 3 GB to spare and the 3B needs
2.5 GB — it fits, it is refused, and the 0.5B is chosen instead.

Sizing also leaves room for other software: the operating system, DeskPilot
itself, and MetaTrader when present each hold back memory the recommender will
not spend. If nothing commercially licensed fits, it says so and recommends the
approved cloud path rather than degrading the server.

### "Configured" is not "working"

`roflo`'s `BackendConfig.kind` defaults to the string `"ollama"`. Nothing about
that default implies Ollama is installed, running, holding the model, or able to
answer — and an installer that read it and reported a working AI backend would
be reporting the value of a dataclass field.

So five states are kept distinct, and only the last means usable:

| State | Means |
|---|---|
| `SUPPORTED` | roflo has a backend by that name |
| `CONFIGURED` | the owner chose it and a model tag |
| `INSTALLED` | the executable is on this computer |
| `REACHABLE` | its service answered |
| `READY` | the chosen model returned output from a real prompt |

A check that skipped the inference probe reports `REACHABLE`, never `READY`.
The `echo` backend answers instantly and is a test double, so it is capped at
`CONFIGURED` however well it works.

### Secrets

The owner signing key is generated on the machine, is never displayed, and is
checked against Solvent's own key rules before it is stored. The control-centre
password is hashed by asking the installed application, with the password
passed on **stdin** — an argument would be visible in the process list to every
other account on that VPS.

Nothing secret reaches a command line. The wizard hands the owner's answers to
the engine through an inherited environment variable, which is not listed
alongside processes, and clears it immediately afterwards. The secrets file is
created, restricted to SYSTEM and Administrators with `icacls`, and only then
filled — the other order leaves the key on disk under inherited permissions,
briefly readable by everyone. If the restriction fails the installation stops:
better no installation than a world-readable signing key.

The installation log is guarded twice. A `Secret` masks itself through `str`,
`repr`, f-strings and JSON, which covers every ordinary path. A scrubber
catches what that cannot — a value echoed back by a tool that quotes its own
arguments.

Backups deliberately exclude the secrets file, matching `deploy/backup.sh`: a
backup that quietly contained the owner key would turn every copy into
somewhere it can leak from.

### Uninstall keeps the business records

The directory tree separates what is replaceable from what is the owner's:

```
C:\DeskPilot\App       replaced by an upgrade, removed by an uninstall
C:\DeskPilot\venv      the same
C:\DeskPilot\Data      the database — kept
C:\DeskPilot\Config    owner key and configuration — kept
C:\DeskPilot\Logs      kept
C:\DeskPilot\Backups   kept
```

Uninstall preserves all four owner directories by default and verifies they are
still there afterwards rather than merely promising it. Deleting them requires a
separately confirmed choice. "Uninstall" meaning "delete the accounting
records" is a defensible option and an indefensible default.

**Repair** replaces application files and Windows integration and touches no
owner data. In particular it does not generate a new owner key: a fresh key
would look exactly like a successful repair and would silently invalidate every
approval the owner had ever signed, discovered only at their next attempt to
authorise spending.

**Upgrade** is reversible. The package is verified before anything is stopped or
moved, the previous application is moved aside rather than deleted, and the
database is backed up *before* the new code is allowed near it — a backup taken
afterwards is a backup of whatever the new code did. Any failure restores both.

### Recovery paths that remain documented

The manual procedures still work and are still documented; they are no longer
the normal path. The engine can be driven directly for diagnosis:

```
python -I C:\DeskPilot\App\deskpilot_installer\cli.py check   --package <zip> --expect-sha256 <digest>
python -I C:\DeskPilot\App\deskpilot_installer\cli.py verify  --root C:\DeskPilot --probe-inference
python -I C:\DeskPilot\App\deskpilot_installer\cli.py repair  --package <zip> --expect-sha256 <digest>
python -I C:\DeskPilot\App\deskpilot_installer\cli.py uninstall --root C:\DeskPilot
```

Exit code `0` means the owner may continue, `1` that a blocker was reported.
A wizard that reads only the exit code therefore fails closed.

## Building the installer

```
python3 windows/installer/build.py
```

It builds the payload with `git archive` at the current `HEAD` — so the contents
are exactly what the repository holds and nothing from the working tree leaks in
— then compiles the wizard with the payload's digest baked in as a compile-time
constant. The installer cannot be talked into accepting a different payload,
because the value it compares against is in its own code section rather than in
a file beside it. Changing the payload requires rebuilding, and rebuilding is
what produces a new digest to publish.

## Testing

```
python3 -m unittest discover -s tests_solvent -t .     # the whole suite
python3 tools/mutate_installer.py                      # the mutation battery
```

The mutation harness breaks one load-bearing control at a time and requires a
named test to fail. It counts a kill only on a green baseline, a mutation that
really reached the file, a newly failing designated test, and a failure that
disappears when reverted — and it purges cached bytecode before every run,
because a mutation that replaces a string with one of the same length changes
neither the source size nor, within a second, its modification time, and
CPython will then load the stale `.pyc` and test code that is not on disk.
