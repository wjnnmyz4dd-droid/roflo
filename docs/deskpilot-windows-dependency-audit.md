# DeskPilot — Windows dependency audit

Date: 2026-10-21 · Target: Windows 11 x64 · Branch
`claude/solvent-architecture-audit-8ot9et`

Measured on Linux x86_64, CPython 3.11.17. Wheel availability for
Windows is read from each project's published metadata and is marked
**UNVERIFIED ON WINDOWS** where it has not been installed there.

---

## 1. DeskPilot's core needs nothing

`solvent/` imports **only the standard library**, and a test enforces
it: `test_installer_architecture.py::ThePackageIsSelfContained::
test_every_module_imports_only_the_standard_library` walks every module
and compares its imports against `sys.stdlib_module_names`.

The installer relies on that. It runs **no pip at all** — it creates a
virtual environment and writes a `.pth` naming the application
directory, then runs everything under `python -I`, which ignores
`PYTHONPATH`, skips user site-packages and keeps the current directory
off `sys.path`. Its own docstring gives the reason: adding a package
manager to the normal path "would be adding the problem this installer
exists to remove."

So the base `[project] dependencies` in `pyproject.toml` — fastapi,
uvicorn, httpx, pydantic — belong to the `roflo` package that shares
this repository. **None is required to run DeskPilot**, and the
installer installs none of them.

| Prerequisite | Needed by DeskPilot? |
|---|---|
| Visual C++ runtime | No, for the core |
| Windows SDK | No |
| C/C++ compiler | No, if wheels are available (§3) |
| PowerShell | Yes — the installer and its probes use it |
| Git | No |
| fastapi / uvicorn / httpx / pydantic | No — `roflo`'s, not DeskPilot's |
| torch / transformers / accelerate | No — the `local` extra, unused by the five skills |

## 2. The one real prerequisite, and it is undocumented

The `verification` extra is what produces **T2 independent review**: a
second opinion from a reader DeskPilot did not write, required for jobs
above the C_HIGH threshold.

```
openpyxl>=3.1.5      python-docx>=1.1.2
clevercsv==0.8.5     markdown-it-py==3.0.0
```

**The installer does not install them.** It installs nothing. So a
freshly installed Windows machine has no differential reader, and the
only remaining T2 producer is the owner signing each review by hand.

This is not a crash and not a silent downgrade. `independent.available()`
reports what the host can actually produce, and the business cycle
declines a job needing T2 **while it is still a quote** rather than
discovering it at delivery. Verified here:

```
independent.available()
  Producer(name='owner review', usable=False,
           why='no Owner Channel with a signing key is configured',
           needs_a_person=True)
```

Fail-closed, and correct. But the consequence is concrete and belongs
in the installation documentation rather than in a comment in
`pyproject.toml`:

> **A fresh Windows install can take C_LOW and C_MED work (T1). It
> cannot take C_HIGH work unattended until the verification extra is
> installed into DeskPilot's virtual environment.**

Recorded as a **remaining blocker**, not repaired: installing packages
is precisely the thing the installer was designed not to do, and
changing that is an architectural decision for the owner.

## 3. What the verification extra actually pulls in

This matters on Windows because three of them are **native code** and
need a matching wheel.

| Package | Version here | Native? | Pulls in |
|---|---|---|---|
| `openpyxl` | 3.1.5 | pure Python | `et-xmlfile` (pure) |
| `python-docx` | 1.2.0 | pure Python | **`lxml`** (native), `typing_extensions` (pure) |
| `clevercsv` | 0.8.5 | **native** | `chardet` (native), **`regex`** (native), `packaging`, and per its metadata `faust-cchardet` and `pandas` |
| `markdown-it-py` | 3.0.0 | pure Python | `mdurl` 0.1.2 (pure, no dependencies) |

Observations:

* **`markdown-it-py` is the cleanest of the four** — pure Python, one
  pure-Python dependency, and the only one added in this engagement.
  It was chosen and pinned for that reason.
* **`lxml` is the heaviest hard requirement.** `python-docx` cannot run
  without it. x64 Windows wheels are published for CPython 3.11 and
  3.13; **ARM64 Windows wheels are not reliably published**, and
  without one, installing `python-docx` on Windows on ARM would need a
  C compiler and libxml2. This compounds the ARM64 finding in
  `deskpilot-windows-installer-certification.md`.
* **`clevercsv` has the largest tail.** Its declared requirements
  include `pandas`, which brings `numpy`. For a CSV dialect detector
  used only as a second opinion, that is a disproportionate footprint
  on a laptop that also runs MetaTrader 5.
* Licences: openpyxl MIT, python-docx MIT, lxml BSD-3, clevercsv MIT,
  markdown-it-py MIT, mdurl MIT, regex Apache-2.0, chardet LGPL-2.1.
  **`chardet` is LGPL**, reached transitively through `clevercsv`.
  Dynamically linked and unmodified it is ordinarily fine for
  redistribution, but it is the only copyleft component in the tree and
  it is worth an explicit decision before DeskPilot is distributed.

## 4. Pinning

| | |
|---|---|
| Pinned exactly | `clevercsv==0.8.5`, `markdown-it-py==3.0.0` |
| Floor only | `openpyxl>=3.1.5`, `python-docx>=1.1.2` |

The comment in `pyproject.toml` gives the reason for pinning
`markdown-it-py`: "a second opinion that changes under you is not a
second opinion." That argument applies equally to `openpyxl` and
`python-docx`, which are floors. **Recommended, not done:** pin all
four, since an independent verifier whose behaviour can change on a
`pip install --upgrade` is exactly the thing the T2 tier exists to
avoid. Left alone here because changing a dependency declaration before
a Windows install that has not happened yet adds risk without
evidence.

## 5. Offline and timeout behaviour

* **The core needs no network to install** — no pip, no downloads.
* **The Python bootstrap does**, and it is the only download the
  installer makes: 29,096,776 bytes from python.org, pinned to
  SHA-256 `96159fcb…f981`, fetched with `curl.exe -sS -L --fail` under
  a 900-second timeout. A download that does not match the pin in
  **both** size and digest is refused and never executed.
* A failed or interrupted download reports the curl output and tells
  the owner to install Python by hand, after which the installer
  detects it and continues.
* **No retry or resume.** A download cut at 90% is re-fetched from zero
  on the next run. Acceptable for a 29MB file; recorded because it is
  not obvious from the code.

## 6. Nothing was added

No dependency was introduced by this audit. The only dependency added
in this engagement is `markdown-it-py==3.0.0`, declared in
`pyproject.toml` with its licence, its single transitive dependency and
its security review recorded in the comment beside it.

## 7. Status

**WINDOWS DEPENDENCIES: UNVERIFIED.**

The analysis above is from published metadata and a Linux install. No
package has been installed on Windows, no wheel has been resolved
there, and the ARM64 wheel situation for `lxml` is inferred rather than
tested. `.github/workflows/windows-preinstall.yml` resolves it the
first time it runs.

## 8. Prerequisites, as the installation documentation should state them

1. Windows 11, 64-bit, **x64** (ARM64 is refused by the installer —
   install Python for ARM64 by hand first).
2. Administrator rights, for an all-users Python install.
3. PowerShell (present on Windows 11 by default).
4. Internet access **only if** no Python 3.11+ 64-bit is already
   present.
5. **For unattended C_HIGH work**: the `verification` extra installed
   into DeskPilot's virtual environment. Without it, DeskPilot runs and
   declines such work rather than delivering it unverified.
