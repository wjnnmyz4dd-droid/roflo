# DeskPilot — mutation isolation certification

Date: 2026-10-18 · Platform: Linux x86_64 · CPython 3.11 · Branch
`claude/solvent-architecture-audit-8ot9et`

A mutation run works by writing a deliberate defect into real source,
running one test against it, and writing the source back. For the
seconds in between, the working tree holds code nobody wants.

That was fine while it stayed in the working tree. It did not.

---

## 1. The incident

`de94386` was staged with `git add -A` while a run held
`solvent/finder.py` patched. The snapshot captured the mutant:

```diff
-        if econ.profit_cents is not None and econ.profit_cents <= 0:
+        if False:
             return Verdict.DECLINE, (
                 f"it would cost {(econ.cost_cents or 0) / 100:,.2f} to earn "
```

For two commits — `de94386` and `8f5c94f`, both pushed — the tree on
`origin` would have bid on work at a loss. Nothing noticed: the runner
restored the file a second later, so the working tree looked right, and
only an inverted diff on an unrelated check gave it away. Corrected in
`df34ca6`.

A related failure earlier in the same engagement: two mutation
processes run at once clobbered `solvent/harness.py`, which had to be
restored by hand.

Both have the same shape. A mutation run owns the files it patches, and
nothing enforced that.

## 2. What the runners modify

| Runner | Files it may write to |
|---|---|
| `tools/mutate_solvent.py` | 194 mutants across `solvent/` modules, `solvent/web/`, and now `tools/` |
| `tools/mutate_installer.py` | 194 mutants across `windows/installer/deskpilot_installer/` and `DeskPilot-Setup.nsi` |

Both wrote in place, restored in a `try/finally`, and verified nothing
afterwards. A `SIGKILL` between the write and the `finally` leaves the
mutant on disk with no record that it is there.

## 3. Three defences

Implemented in `tools/mutation_guard.py`. Three rather than one because
each covers a case the others miss, and a single mechanism would have a
blind spot nobody could see.

### Isolation — the real fix

`isolated_tree()` copies the working tree into a temporary directory and
the run patches *that*. Production source is never written to at all, so
there is nothing for a commit to capture. This is now the **default** for
both runners; `--in-place` is opt-in.

It copies the *working tree*, not a commit: mutation testing is almost
always run against uncommitted work, and a `git worktree` of HEAD would
quietly certify different code from the one being tested. The file list
comes from `git ls-files --cached --others --exclude-standard`, so
untracked work in progress is carried and ignored files are not.

Verdicts are unchanged by isolation — the smoke tests below and the full
run in §6 confirm it.

### A claim — for runs that cannot isolate

`Claim` writes `.mutation-claim.json` at the repository root naming
every file the run may touch and the SHA-256 each had before it started.
The file is removed **only** after every hash is checked back. Three
properties matter:

* a run that does not restore raises `RestorationUnproven` and the claim
  is deliberately *left on disk* — the evidence outlives the process
  that was interrupted;
* a second run cannot claim a tree another already holds, which is the
  clobbered-`harness.py` case;
* a claim whose process is gone reads as `stale`, not as all-clear. An
  unreadable claim also reads as stale. Unreadable must never read as
  healthy.

### A scan — needing no cooperation at all

`mutated_files()` asks the question directly: is a mutant's replacement
text present in a file where its original is *gone*? Both halves matter.
A replacement alone proves nothing — `return []` occurs legitimately all
over — but original-absent-and-replacement-present is exactly the state
a runner leaves between its two writes.

It needs no claim and no cooperation from the runner, so it catches a
mutation applied by hand, by an editor, or by a run that died before
writing a claim.

## 4. The pre-commit hook

`.githooks/pre-commit` runs `tools/precommit_mutation_check.py`, which
asks both questions and refuses the commit on either.

The scan is run against **staged content**, not the working tree. That
distinction is the whole incident: `git add` takes a snapshot, and the
file on disk can be correct again by the time `git commit` runs while
the index still holds the defect. `git show :<path>` is what the hook
reads.

Installed with:

```
git config core.hooksPath .githooks
```

This repository is configured, and a test asserts it
(`test_the_repository_points_at_the_hook_directory`), because a hook
nobody installed protects nothing.

`--no-verify` still works, deliberately. A person who has read the
reason may commit a mutant on purpose — to add a regression fixture,
say — and a hook that cannot be overridden gets uninstalled.

## 5. Restoration is proven, not assumed

The brief asked for the certification process to fail if restoration
cannot be proven. It does, in two places:

* `Claim.__exit__` raises `RestorationUnproven` naming every file whose
  hash does not match, and keeps the claim on disk.
* `mutation_guard.report()` returns `(False, why)` for a held claim *or*
  an applied mutant, and `python3 tools/mutation_guard.py` exits 1. It
  is the single call a certification script, a hook or a person uses.

On the tree as certified:

```
$ python3 tools/mutation_guard.py
OK: no mutation claim, and no mutant is applied
```

## 6. Coverage is preserved

The brief requires existing mutation coverage to survive the change.

Smoke tests under isolation:

```
$ python3 tools/mutate_solvent.py --only t2-converted
isolated: patching a copy of the working tree at /tmp/deskpilot-mutation-00_dskm3
KILLED       t2-converted-provenance-invents-nothing
KILLED       t2-converted-provenance-skips-the-table
2/2 killed  (0 survived, 0 not applied or harness)

$ python3 tools/mutate_installer.py --only log-scrubber-disabled
isolated: patching a copy of the working tree at /tmp/deskpilot-mutation-s5jx_tg0
[1/1] log-scrubber-disabled (installlog.py) ... KILLED
```

Full-run figures are in `deskpilot-final-engineering-certification.md`.

## 7. The tests

`tests_solvent/test_mutation_isolation.py`, 19 tests, grouped so that
each defence is covered on its own — a suite that tested them as a
bundle would not notice one of them silently doing nothing.

| Group | What it establishes |
|---|---|
| `TheRunnersIsolateByDefault` | both runners default to a copy; the copy carries uncommitted work; patching the copy leaves the original byte-identical; the copy is removed afterwards |
| `TheClaimProvesRestoration` | a clean run removes its claim; a run that does not restore raises and keeps it; a restoration off by one byte is not a restoration; a second run cannot claim a claimed tree; a dead claim reads as interrupted; an unreadable claim is not an all-clear |
| `TheScanFindsAnAppliedMutant` | the tree as it stands carries no mutant; **the exact text that reached `origin` is caught, by name, from staged content**; a replacement whose original is still present is not a hit; `report()` refuses while a claim is held |
| `ThePreCommitHookIsReal` | the hook exists and is executable; the repository points at it; it passes on this tree; it refuses while a claim is held — the concurrent-commit case, end to end through the real script |

## 8. Mutation coverage of the guard itself

Five mutants, all killed:

| Mutant | What it breaks |
|---|---|
| `mutation-isolation-the-runner-patches-production` | a run patches real source again |
| `mutation-isolation-a-claim-never-checks-the-hashes` | an in-place run reports success without showing it restored |
| `mutation-isolation-a-dead-claim-reads-as-clear` | a claim left by a killed run reads as live, so interrupted-mid-patch is never reported |
| `mutation-isolation-the-scan-reads-only-the-replacement` | the scan calls legitimate code mutated, so every commit is refused and the check gets turned off |
| `mutation-isolation-the-hook-ignores-a-live-claim` | a commit is allowed from underneath a running mutation process |

The fourth is there on purpose. A guard that cries wolf is a guard
somebody uninstalls, so false positives are a failure mode worth a
mutant of its own, not just false negatives.

## 9. Status

**MUTATION ISOLATION: PASS.**

Temporary mutations cannot enter committed production code unnoticed:
the default run never writes to it, an in-place run announces what it
owns and must hash it back, a stray mutant is found by a scan that needs
no cooperation, and a commit carrying one is refused by a hook that
reads the index rather than the disk.

## 10. What this does not say

* It does not say a mutant can never be committed. `--no-verify`
  overrides the hook by design, and a clone that has not run
  `git config core.hooksPath .githooks` has no hook at all. The test
  asserting the configuration will fail in such a clone, which is the
  intended signal.
* It does not say the scan knows about defects that are not in a mutant
  table. It knows exactly the 388 mutants the two runners define.
* It does not say the isolated copy is a sandbox. It is a copy of the
  working tree for the purpose of not writing to the original; the tests
  it runs have the same access to the machine they always had.
