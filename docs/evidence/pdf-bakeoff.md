# PDF reader bake-off — measured, with a recommendation and no adoption

PDF is the commonest real job in this market and the largest capability
gap this system has. Three candidates were measured in a throwaway venv
outside the repository. **Nothing was installed into the product and no
PDF capability was built.** Adoption needs an owner decision, for the
reason in "The constraint that decides it" below.

Scripts, re-runnable: `docs/evidence/make_fixtures.py` (fixtures),
`docs/evidence/bakeoff.py` (criteria), `docs/evidence/agreement.py`
(cross-checking). Versions measured: pdfplumber 0.11.10, pypdf 6.20.1,
pymupdf 1.28.2, on CPython 3.11.17.

## Inputs

Four PDFs generated with fpdf2 — a plain invoice, an invoice with a
ruled line-item table, a two-page statement whose closing balance is on
page 2, and a European invoice — so the contents are known before the
files exist. fpdf2 is a fixture tool in the scratch venv; nothing
proposes shipping it. Generated files carry compressed content streams
and real font resources, unlike a hand-rolled PDF, which is why they
were generated rather than written by hand.

Two real-world holdouts nothing here produced: the W3C `dummy.pdf` and
the IRS Form W-9 (`fw9.pdf`, 140 KB, a dense government form).

## Licences, read from the installed files rather than from metadata

| library | licence | verified from |
|---|---|---|
| pypdf 6.20.1 | BSD-3-Clause | `pypdf-6.20.1.dist-info/licenses/LICENSE` |
| pdfplumber 0.11.10 | MIT | `pdfplumber-0.11.10.dist-info/licenses/LICENSE.txt` |
| pymupdf 1.28.2 | **AGPL-3.0 or paid Artifex commercial licence** | `pymupdf-1.28.2.dist-info/COPYING` |

PyMuPDF's `COPYING` file reads, in full: "Dual Licensed - GNU AFFERO GPL
3.0 or Artifex Commercial License". For a commercial service that
clients reach over a network, AGPL-3.0 is a real obligation, and the
alternative is a paid subscription nobody has authorised. **Excluded on
licence.**

## Measurements

| | pdfplumber | pypdf | pymupdf |
|---|---|---|---|
| known strings recovered | 16/16 | 16/16 | 16/16 |
| documents read | 6/6 | 6/6 | 6/6 |
| deterministic across two runs | yes | yes | yes |
| total extraction time, 6 docs | 0.975 s | **0.162 s** | 0.138 s |
| cold import | 0.113 s | 0.127 s | 0.119 s |
| exact table grid recovered | **yes** | no table API | not measured |
| on a truncated file | raises | raises | **returns 1389 chars** |
| runtime dependencies | 5 (pdfminer.six, pillow, pypdfium2, cryptography, + transitives) | **0** | 0 |

Two results decide more than the speed column.

**PyMuPDF returns text from a truncated file instead of raising.** A
short read that looks like a complete one is the exact failure this
system is built to prevent — it is the same shape as every defect this
sweep removed. That would have excluded it even if the licence had not.

**pdfplumber is the only one that recovers a ruled table as a grid**,
exactly matching the four-column fixture. Invoices are tables, so this
is not a minor convenience.

## The constraint that decides it

`tests_solvent/test_architecture.py::ZeroThirdPartyDependencies` asserts
that `solvent` imports only the standard library, and states why: *"Every
dependency is a potential path around the Action Gate."* `solvent` is
stdlib-only today — verified, not assumed. There is no PDF reader in the
standard library, so unlike the encoding work, this gap cannot be closed
with code already present. Adopting any of these breaks a
security-motivated invariant that the owner set and a test enforces.
That is an owner decision and it is not taken here.

## Cross-checking: the finding that shapes any adoption

Four worker-checker pairs in this system turned out to share a parser,
and a shared parser cannot catch its own misreading. A PDF worker and
checker both calling pypdf would rebuild that fault on the commonest job
type in the market, and a PDF capability that cannot verify itself is
worse than no PDF capability: it would ship unverifiable work.

So two independent readers were measured against each other:

- **5 of 6 documents: exact agreement** after whitespace normalisation.
- **1 of 6 disagrees — the real IRS W-9** — with 99.3% token overlap.
  The divergence is rotated text: pdfplumber returns it reversed
  (`snoitcurtsnI`, `cificepS`, `egap`) where pypdf reads `Print`,
  `type.` correctly.
- On a truncated file **both raise**, rather than both inventing text.

This is what a usable cross-check looks like: agreement on ordinary
content, divergence exactly where PDF extraction is genuinely hard. A
capability built this way would escalate the W-9 to a human instead of
delivering one reader's guess, which is the right answer for a rotated
form.

## Recommendation

1. **Do not adopt PyMuPDF.** Excluded twice over: AGPL-3.0 or a paid
   licence, and partial text from a damaged file.
2. **If, and only if, the owner decides to accept third-party
   dependencies**, adopt **pypdf for the worker** (BSD-3-Clause, zero
   runtime dependencies, fastest, raises on damage — the smallest
   possible breach of the zero-dependency invariant) and **pdfplumber
   for the checker** (MIT, independent implementation, and the only one
   that reads tables). Disagreement between them escalates; it never
   resolves to either reader's answer.
3. The cost of (2) is honest and should be weighed as a whole: eight
   packages including compiled ones (`cryptography`, `pillow`,
   `pypdfium2`), all of which must be bundled into the Windows
   installer, against a product that ships zero today.
4. **Until that decision, PDFs stay refused**, which is the correct
   behaviour and already the measured behaviour.

No capability was built, no dependency was added to the project, and no
subscription was activated.
