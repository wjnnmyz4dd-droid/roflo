# DeskPilot — independent review of Markdown deliverables (finding C)

**Status: VERIFIED FIXED**

Date: 2026-10-18 · Platform: Linux x86_64 · No real external effect.

---

## 1. Reproduction

In the 100-opportunity acceptance run, three document-processing jobs
above T1 stopped with:

> no independently authored reader for .md is available here (none is
> known to this build), so a second automated opinion is not possible

Correct behaviour — a T2 that silently becomes a T1 is worse than no T2 —
but it meant Markdown work above T1 could never be delivered unattended.

## 2. What was already there, and why it was not independent

Two capabilities emit Markdown: `report-builder` (always) and
`document-processing` (when the brief applies a template).

The worker renders it with `docwork._as_text`. The capability's own
checker reads it back by matching line prefixes:

```python
headings = [line[3:].strip() for line in text.splitlines()
            if line.startswith("## ")]
```

That is one project's idea of Markdown consulted twice. It cannot tell a
heading from four characters of code inside a fence.

## 3. The reader

`markdown-it-py==3.0.0` — a CommonMark parser ported from the JavaScript
`markdown-it`, written by other people from a published specification.

**Dependency review**, as the policy requires:

| | |
|---|---|
| License | MIT (OSI approved) |
| Runtime dependencies | one: `mdurl` 0.1.2, MIT, no dependencies of its own |
| Scope | a parser; it reads text and returns tokens |
| Security | no `os`, `socket`, `subprocess`, `urllib` or `http` imports; no `eval`, `exec` or `__import__` (verified by grep over the installed package) |
| Pinned | yes — a second opinion that changes under you is not a second opinion |

Declared in `pyproject.toml` under the existing `[verification]` extra,
alongside openpyxl, python-docx and clevercsv. Solvent still runs without
it and says so; `independent.available()` reports what this host can
actually produce.

## 4. That it reads Markdown *as Markdown*

These are the disagreements with a line-prefix scan, and they are tested
rather than asserted:

| Case | A prefix scan | The parser |
|---|---|---|
| `## Not a heading` inside a fenced code block | counts it | does not |
| `Overview` underlined with `-----` (setext) | misses it | finds it |
| `x \| y` escaped inside a table cell | splits the cell | keeps it whole |
| `[the invoice](https://…)` | carries the URL | carries the text |

Also tested: tables as rows, list items, a 2,000-row document, and
malformed input (CommonMark has no parse errors, so it must not crash or
hang).

## 5. That it checks the document, not just the syntax

A second parser proves a document is well-formed. It says nothing about
whether the figures in it are the client's, and recording that as T2
would be the theatre this module exists to avoid. So the review
recomputes four things.

**For a report (`report_section` brief, JSON source):**

* **structure** — required headings present, as headings the parser
  recognises;
* **provenance** — every table row is a row the client supplied;
* **completeness** — every supplied row reached the table;
* **arithmetic** — the stated total is the sum of the client's figures,
  added up here.

**For a converted document (`.docx` source, the acceptance case):** two
independently authored readers meet — python-docx on the source,
markdown-it-py on the deliverable, and neither is the code that did the
work. The review establishes **provenance**: every value the deliverable
states is a string the client's own document contains, with table cells
included.

What it does **not** claim: from flattened document text it cannot
establish that nothing was *omitted*, so it does not say so. A finding
this check does not make is left to the capability's own checker and the
owner.

## 6. Tests

`tests_solvent/test_markdown_t2.py` — **28 tests** in five classes:
the reader exists and is not this project's code; it reads Markdown as
Markdown (8); it checks the document (8); the review refuses rather than
degrading (3); and the case that was blocked in acceptance (5), which
runs the real worker on the real fixture and then tampers with its
output three ways — a fabricated line item, a rewritten client name and
an inflated amount — each of which must be caught.

A brief with nothing independently checkable still yields no findings,
and `differential_review` then refuses with "review by a person is
required" rather than passing.

## 7. Mutation

| Mutant | Verdict |
|---|---|
| `markdown-has-no-independent-reader-again` | KILLED |
| `markdown-review-accepts-values-the-source-never-had` | KILLED |
| `markdown-review-treats-every-value-as-boilerplate` | KILLED |
| `markdown-totals-are-not-recomputed` | KILLED |

## 8. Limits

* The JSON facts source is read with the standard library's `json`. The
  format under review is the Markdown the worker rendered; JSON has one
  unambiguous reading, and what must not be shared is the reader of the
  artifact being judged.
* Independence here is by authorship, not by a person. It is the class of
  error a single implementation cannot catch about itself, which is not
  the same as proof the client got what they wanted.
