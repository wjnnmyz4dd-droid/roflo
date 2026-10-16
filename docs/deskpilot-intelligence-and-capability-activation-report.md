# DeskPilot / Solvent — intelligence and capability activation

A sweep that was told not to stop at another audit. It did not: every
finding below is either a change in the code or a measurement of the
code, and each is reported with the number it moved.

Branch `claude/solvent-architecture-audit-8ot9et`. All figures in this
report come from runs recorded in `docs/evidence/` or reproduced in the
commits named beside them. Nothing here is estimated.

---

## A. What was asked, and what was done

The brief was to activate capability rather than describe it: widen
what the system can accept, certify the worker–checker pairs, run the
adversarial and mutation machinery against whatever was new, bake off
candidate libraries for the formats still refused, and report
first-dollar readiness honestly.

Nine commits, this report the last of them. The headline movements:

| | before | after |
|---|---|---|
| realistic client inputs handled correctly | 10 / 13 | **13 / 13** |
| of those, accepted rather than refused | 4 / 7 | **7 / 7** |
| test suite | 4,631 passed | **4,700 passed**, 44 skipped |
| mutation coverage | 143 mutants | **155**, all killed |
| worker–checker pairs sharing a parser | 4 of 5 | **0 of 5** |
| sealed exams | 1 (capacity) | **2** (capacity, reading) |

---

## B. The defect shape this sweep kept finding

One shape accounts for most of what follows: **a check whose success
does not depend on the thing it is checking.**

It appeared five times in five different disguises.

1. `check_no_character_substitution` asked whether every delivered
   character appears in the source under *some* decoding it performed
   itself. Mojibake is by construction a valid decoding under the wrong
   codec, so the question cannot fail. It passed the exact corruption it
   was written to catch.
2. `reportverify` opened with "it never imports the worker" and
   imported the worker's parser on line 21. One parser read the client's
   file for both, so neither could catch the other.
3. `sheetverify` reported `opens_as_workbook` as a PASS whose stated
   evidence was "read back through solvent.xlsx" — our reader reading
   our writer.
4. `csvverify` and `entryverify` read the source under assumptions the
   workers had stopped making, so the pair could not agree about what
   the client's file even said.
5. The skill-matrix grader read its own fixtures with a hard-coded
   comma, so it expected delivery for files whose dates read two ways.

The second theme, from earlier sweeps, held again: **unreadable must
never read as healthy.** `ambiguity.csv_ambiguities` returned "no
ambiguity" for a file it could not parse, which is the dangerous
direction to fail.

---

## C. Reading a client's file as the client saved it

`solvent/tabular.py` (new) decodes by a fixed ladder — `utf-8-sig`,
then `utf-16` **behind a byte-order mark only**, then `cp1252` — and
sniffs comma or semicolon with `csv.Sniffer`. No dependency was added;
none was needed.

The UTF-16 gate is the whole design. Python's `utf-16` codec does not
fail on input that is not UTF-16: it accepts any even-length byte
sequence and returns CJK. Ungated, that rung sits at the top of the
ladder and swallows every cp1252 file of even length. "It decoded as
UTF-16" carries no information without the mark.

A file that cannot be read honestly is refused, and the refusal says
something the owner can act on. A UTF-16 file saved without a mark and
a `.xlsx` are both NUL-heavy and need opposite advice; they are told
apart by whether the NULs *interleave* — one per character, all on one
side, with text on the other.

## D. The new way to be wrong that detection creates

Widening what is read introduces exactly one new failure, and it is the
one no existing check can see: pick the wrong encoding and every other
check still passes, because they all compare the deliverable against
the same source the worker misread. A file mangled end to end
reconciles perfectly with itself.

`check_no_character_substitution` closes it with two rules it cannot
talk itself out of: no replacement character in the deliverable, and a
source that is overwhelmingly printable ASCII must still yield one
(whole-file mojibake collapses that share from above 0.80 to near
zero). The encoding family is pinned by the bytes rather than chosen.

## E. That check failed its own first version

Worth recording rather than smoothing over, because it is the clearest
instance of section B. The first version was free to try several
codecs, `utf-16` among them. Shown cp1252 read as UTF-16, it rediscovered
the worker's mistake, found the characters, agreed with itself and
returned PASS. The probe that caught it was four cases long.

The lesson is not "try harder". It is that a check given the same
degrees of freedom as the thing it checks has no power, however
carefully it is written.

## F. Certification, not registration

Registering a check widens `certifiable_scope`, and the system refused
the whole job the moment the promotion claimed twelve checks on
evidence for eleven — the control working, loudly, before any job ran.

The generated battery now cycles source shapes by case index rather
than drawing them at random (`_build`'s own docstring warns that a coin
flip leaves a shape short of the evidence floor) and carries two new
defect classes, `ENCODING_MOJIBAKE` and `CHARACTER_LOST`. Result: **568
of 568 trials correct**, 14 correct artifacts accepted and 28 defects
caught. Recorded as owner decision **OD-16**.

---

## G. The five worker–checker pairs

Each pair was checked for two faults: the checker sharing the worker's
parser, and the checker reading the source under different assumptions
from the worker. Four of five had one or the other.

| pair | fault | state |
|---|---|---|
| csv-cleanup | checker read UTF-8 only | fixed — own decoder |
| report-builder | checker imported the worker's parser | fixed — own parser |
| data-entry | checker read UTF-8 only; worker's import unused | fixed — both |
| spreadsheet-automation | "opens" proved only reader-reads-writer | fixed — OPC check |
| document-processing | same | fixed — OPC check |

Both independence tests now parse imports rather than matching file
text, so a docstring may discuss the worker while the code cannot reach
it. The CSV one additionally forbids `solvent.tabular`: a shared
*library* is the easy version of this mistake, because importing it
looks harmless.

## H. csv-cleanup

The checks read the source as UTF-8 only, so once the worker accepted a
European export the checks would have refused to verify their own
correct work. `csvverify` now has its own decoder — stated locally, not
imported from `tabular` — which is the independence the module exists
for. The cost is that an ambiguous file can be read two ways and the
job then refused. That is the safe direction.

## I. report-builder

The independence claim was false and the consequence was demonstrable.
Given a source with two columns named `amount`:

```
item,amount,amount
Consulting,100.00,999.00
Support,50.00,888.00
```

`csv.DictReader` keeps whichever column came last, silently. The worker
reported 999.00 and 888.00, totalling 1887.00, and every check passed —
including `check_no_invented_facts`, whose detail line reads "every
figure traces to the supplied data". It did trace. To the misreading of
it. The client's own first column appeared nowhere and nothing noticed.

`csv-cleanup` had always refused that shape as ambiguous. report-builder
now refuses it at build, and `reportverify`'s own loader refuses it too
and reports UNVERIFIABLE rather than PASS.

## J. data-entry

`entrywork` imported `solvent.tabular` and never called it — a dead
import, committed by me, that made the module read as encoding-aware
while `_read_csv` still assumed UTF-8 and commas. `entryverify` assumed
the same regardless, and that mismatch pointed the expensive way: once
the worker accepted a European export the verifier could not read it,
so the job was done in full and then refused at verification. Safe, and
useless.

Both are fixed, and a semicolon-separated cp1252 export of the standard
fixture now runs end to end with every verifier check passing and
"Café Münster" spelled correctly on delivery.

## K. spreadsheet and document: what "it opens" was worth

`opens_as_workbook` passed on the evidence "read back through
solvent.xlsx". Measured, not supposed: both `xlsx.read_workbook` and
`docx.read_document` accept a package with `[Content_Types].xml`
removed and one with `_rels/.rels` removed; `read_workbook` also accepts
one missing the workbook's own relationships, which is how a workbook
finds its worksheets. All are mandatory in the Open Packaging
Conventions and Excel and Word refuse every one. Three files that do
not open, certified as opening.

`solvent/opcpackage.py` (new) asks the packaging question with
`zipfile` and `xml.etree` and shares no code with either writer. It is
verifier-side only and a test forbids the seven producing modules from
importing it.

It is deliberately not stricter than the specification: a part with no
relationships needs no relationships part, so a document without
`word/_rels/document.xml.rels` still opens and is still accepted. A
stricter check that is wrong costs more than the loose one it replaces.

## L. Two regressions I introduced, and one I nearly shipped

- **Sniffing tab and pipe** widened the capability past what it
  advertises. `supported_formats` is `("csv",)` and a separate test
  asserts that proving CSV does not imply TSV. Narrowed to comma and
  semicolon: a semicolon `.csv` is the same format, a `.tsv` is not.
- **Removing the one-column plausibility guard**, on the reasoning that
  the sniffer settles the delimiter first. It does not — it falls back
  to comma when it cannot tell, which is exactly what a PDF does, and a
  PDF was accepted, cleaned, verified against itself and delivered. The
  guard is restored and the sniffer now falls back silently so the
  older, more specific guards give the diagnosis.
- **`opcpackage` resolved relationship targets against the `_rels`
  folder** rather than the folder above it, so it rejected every valid
  file. The happy path was in the probe, which is why that lasted one
  run.

---

## M. Input acceptance, measured before and after

One script (`docs/evidence/acceptance-probe.py`) against two commits —
`f980efc`, the state this sweep began from, and this branch. Full
record in `docs/evidence/input-acceptance-reprobe.txt`.

| | before | after |
|---|---|---|
| accepted, of 7 that should be | 4 | **7** |
| correctly refused, of 6 that should be | 6 | 6 |
| wrongly refused | 3 | **0** |
| wrongly accepted | 0 | 0 |
| handled correctly | 10 / 13 | **13 / 13** |

The audit's "accepted 2 of 13" is not carried forward: that probe's own
notes flagged two rows as probe artifacts, and re-run cleanly the
pre-sweep code accepts four. The honest movement is **4 to 7**.

The three that changed are the three that matter commercially — a
windows-1252 accented export, a UTF-16 "Unicode text" export, and a
semicolon-delimited European CSV.

## N. What stays refused, and why that is correct

Six inputs are refused and should be. Forcing thirteen acceptances
would mean delivering work in formats nothing here has certified.

- **tab-separated** — `.tsv` is a different advertised format.
- **a real `.xlsx` renamed `.csv`** — now refused with "exported to CSV
  first", advice its owner can act on, rather than "re-save it as
  UTF-8", which they cannot.
- **PDF, legacy `.doc`, RTF, OpenDocument `.odt`** — not read at all.

One acceptance is recorded as weak rather than counted as a win: EU
comma decimals (`1.234,56`) pass only because nothing in that brief
computes on the column. They travel through as text. Any requirement
that totalled or sorted that column would be a different measurement,
and this sweep did not make it.

One honest limit of the substitution check: it does not distinguish
cp1252 from Latin-1 within the single-byte family, so a file whose
0x80–0x9F bytes are read the wrong way is not caught. The check says so
in its own docstring rather than implying coverage it lacks.

---

## O. PDF: three candidates, measured

PDF is the commonest real job in this market and the largest remaining
gap. Three libraries were measured in a throwaway venv outside the
repository, against four fixtures with contents known before the files
existed and two real-world holdouts nothing here produced (the W3C
dummy PDF and the IRS Form W-9). Full record:
`docs/evidence/pdf-bakeoff.md`.

Licences read from the files on disk, not from metadata claims:

| library | licence | source |
|---|---|---|
| pypdf 6.20.1 | BSD-3-Clause | `dist-info/licenses/LICENSE` |
| pdfplumber 0.11.10 | MIT | `dist-info/licenses/LICENSE.txt` |
| pymupdf 1.28.2 | **AGPL-3.0 or paid Artifex licence** | `dist-info/COPYING` |

All three recovered 16 of 16 known strings across all six documents and
were deterministic, so accuracy did not separate them. Two other
results did:

- **PyMuPDF returns 1,389 characters of text from a truncated file**
  where the others raise. A short read that looks like a complete one
  is the precise failure this system exists to prevent. Excluded on
  that alone, before the licence.
- **pdfplumber is the only one that recovered a ruled table as an exact
  grid.** Invoices are tables. It costs five runtime dependencies
  including compiled ones and runs six times slower than pypdf, which
  has none.

## P. The PDF decision, and the condition on it

`test_architecture.py::ZeroThirdPartyDependencies` asserts that
`solvent` imports only the standard library, and says why: *"Every
dependency is a potential path around the Action Gate."* `solvent` is
stdlib-only today — verified, not assumed. There is no PDF reader in
the standard library, so unlike the encoding work this gap **cannot be
closed with code already present**. Breaking a security-motivated
invariant the owner set is an owner decision and is not taken here.

One finding shapes any adoption. Four pairs here turned out to share a
parser; a PDF worker and checker both calling pypdf would rebuild that
fault on the commonest job type there is, and a PDF capability that
cannot verify itself is worse than none — it would ship unverifiable
work. So the two readers were measured against each other:

- **exact agreement on 5 of 6 documents** after whitespace normalisation;
- on the sixth, the real W-9, **99.3% token overlap**, diverging only on
  rotated text, which pdfplumber returns reversed (`snoitcurtsnI`) and
  pypdf reads correctly;
- on a truncated file **both raise**.

That is what a usable cross-check looks like: agreement on ordinary
content, divergence exactly where extraction is genuinely hard.

**Recommendation.** Never PyMuPDF. If the owner accepts third-party
dependencies, pypdf for the worker and pdfplumber for the checker, with
disagreement escalating rather than resolving to either answer — at a
true cost of eight packages that must be bundled into the Windows
installer. Until that decision, PDFs stay refused, which is correct and
is already what happens. **Nothing was installed into the project and no
PDF capability was built.**

---

## Q. Mutation testing

155 mutants, run sequentially, never concurrently, with no commit made
while source was altered. `NOT_APPLIED` is not a kill and none was
counted as one.

Writing the twelve new mutants found a gap before the run did: nothing
anywhere asserted that a UTF-16 file without a byte-order mark is
refused rather than misread. The gate existed and nothing pinned it.

The first full run scored **149 of 152**, and all three survivors were
controls this sweep had added — all the same fault, two overlapping
guards hiding each other so the test could not tell which was working.
Breaking the ASCII-share rule changed nothing because the character
rule caught the same mojibake; breaking the character rule changed
nothing because the share rule did. Both looked pinned; neither was.

Fixed by isolating each control, not by loosening anything. Final run
with the three resource-guard mutants added: **155 of 155 killed, 0
survived, 0 not applied.**

## R. Adversarial scenarios

The existing suites — 214 scenarios across six files — attack the
business surface: forged estimates, laundered grants, instructions
hidden in a posting, a client claiming a capability Solvent lacks. All
pass. None of them attacks the client's *file*, and widening what the
system reads widened exactly that surface.

`tests_solvent/test_hostile_client_files.py` (new) does, and found two
real amplification defects:

- `xlsx.read_workbook`: a **410 KB** workbook with a padded worksheet
  part allocated **1.23 GB over 5.2 seconds and then accepted the
  file**. Pre-existing. `max_cells` cannot help — it counts cells the
  parser has already built, which is after the memory is spent.
- `opcpackage.problem`: a **206 KB** package allocated **815 MB** and
  called it valid. Mine, three commits old, in the check whose whole
  job is being the careful one.

This process shares a machine with the owner's trading session, so
neither is abstract. Both readers now bound each part by reading at
most a limit and one byte — never by the header's declared size, which
a crafted archive can set to anything. The workbook bomb goes from
5.2 s / 1.23 GB to **0.13 s and refused**.

Three vectors probed and found already safe, recorded so they are not
re-discovered: billion laughs is refused (by CPython's expat
amplification limit, not by anything written here — so it is a property
of the Python the product ships on); a part named to traverse out of
the archive writes nothing to disk; and a file that is semicolon-
delimited for sixty lines then switches to commas defeats the sniffer,
which samples fifty, and is refused rather than delivered.

## S. The sealed exams

The **capacity exam**'s seal covers four modules, none of which this
sweep touched. Seal intact; re-sat and passed. It does not examine
reading.

So a second paper was set over the reading surface:
`tests_solvent/readingexam.py`, sealing eight modules by SHA-256, with
the seed derived from the freeze rather than the environment, and every
answer fixed before the system is asked. Its inputs are ones
development never used — Greek, Cyrillic, Hebrew, Vietnamese, Polish
and Turkish names, fields that quote the separator, fields holding an
embedded newline, empty fields, a 300-character field — and a test
asserts the paper contains none of the development fixtures.

**The first sitting scored 46 of 56, and all ten wrong answers were
faults in the paper, not the system.** Recorded, because a paper
quietly corrected until it passes is not an exam. Appending high bytes
to a UTF-16 file damages nothing — they are valid UTF-16 code units.
The other eight marked the wrong layer: a tab-separated file is read by
the ladder as one column, which is the honest answer to what it was
shown, and is refused above it.

Marking now runs the whole job, under a brief naming the paper's own
columns — under a brief the files could not satisfy, every file would
be refused and the paper would score full marks while measuring
nothing. Second sitting, same seal, same seed: **56 of 56**, with 24
readable files delivered intact and correctly declared and 32 damaged
files refused. `docs/evidence/reading-exam.json`.

## T. Test evidence

| evidence | result |
|---|---|
| full suite | 4,700 passed, 44 skipped, 10,071 subtests |
| mutation | 155 / 155 killed, 0 survived, 0 not applied |
| verifier certification battery | 568 / 568 trials correct |
| adversarial (6 existing suites) | 214 passed |
| hostile client files (new) | 7 passed |
| development skill matrix | 45 scenarios |
| unseen final exam | 45 scenarios, seed used nowhere else |
| sealed capacity exam | seal intact, passed |
| sealed reading exam | 56 / 56 |
| input acceptance probe | 13 / 13 handled correctly |

No reviewer ran that did not run. K-LEAN and iFixAi were not executed
in this sweep; the advisory position recorded in the previous report
(`docs/evidence/advisory/`) is unchanged and is not restated here as
though it were new evidence.

---

## U. Could five genuine paying assignments be handled today?

Separating the three things that question actually asks.

**Can the system do the work?** For work of the shape the five
certified skills cover — yes, and for more of it than before this
sweep. A continental European export now runs end to end through
csv-cleanup and data-entry with every check passing. All five
capabilities carry an owner promotion. csv-cleanup covers twelve
certified checks, up from eleven.

**Can it obtain the assignments?** Only by being approached.
Competitive bidding is frozen and the freeze is correct: no marketplace
read so far permits a machine to submit proposals, and Freelancehunt's
clause 3.1.11 forbids it by name. The open channels are
`DIRECT_CLIENT` and `DIRECT_INVITATION`. So five assignments must
arrive through the owner or through a direct invitation; the system
cannot go and win them.

**Can it be paid?** No. The payment rail reports
`operational_status: SELECTED`, `decided: False`, `money_can_move:
False`, and no rail is named. Invoices can be issued; receipt cannot be
confirmed, and the monitor will not treat anything but a `rail_event`
as confirmation.

**The honest answer: the work, yes; the acquisition and the money,
no.** Five paying assignments are not blocked by capability any more.
They are blocked by two things this sweep was not authorised to change
and did not touch — a payment rail nobody has stood up, and an
acquisition route that requires a human to bring the client.

The smallest evidence-based path to a first dollar is unchanged and
short: stand up one payment rail to the point where a `rail_event` can
be observed, and take one direct-hire assignment of CSV-shaped work. No
new capability is required for that, and after this sweep the range of
client files such an assignment may arrive in is materially wider.

## V. What was not done, and what remains

Not done, deliberately:

- **No PDF capability.** The bake-off is complete and the
  recommendation is costed; adoption breaks a security-motivated
  architectural invariant and is the owner's call.
- **No dependency added, no subscription activated, no deployment, no
  marketplace submission, no payment account connected, no live bid.**
- **No second decision engine, no new agents**, no anti-detection
  behaviour, and nothing that touches the owner's keyboard or MT5
  session.
- **K-LEAN and iFixAi were not run**, and are not reported as if they
  had been.

Remaining gaps, in the order they cost money:

1. **PDF, `.doc`, RTF, `.odt` unreadable.** PDF is the largest single
   gap in this market. Blocked on one owner decision, not on work.
2. **The payment rail cannot move money.** Hard blocker on revenue.
3. **Acquisition requires a human.** Correct under the agreements read
   so far, and worth re-reading if any platform's terms change.
4. **EU decimal numbers are not understood**, only passed through.
5. **cp1252 versus Latin-1 within the single-byte family** is not
   distinguished by the substitution check.
6. **UTF-16 without a byte-order mark is refused.** Correct — guessing
   past a missing mark is how a file becomes mojibake — but it is a
   real export shape, and the refusal now tells the client how to fix
   it in one step.

The recurring lesson, stated once more because it earned its place:
five times this sweep, a check was passing because it had been given
the same freedom as the thing it was checking. The fix is never to
write the check more carefully. It is to take the freedom away.
