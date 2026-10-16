"""Measure three PDF readers on the same inputs, against known truth.

Criteria, measured rather than asserted:

  text recovery    does every string the fixture is known to contain come
                   back, exactly
  tables           are ruled table cells recovered as a grid
  multi-page       is a fact on page 2 found
  determinism      same bytes in, same text out, twice in one process
  cold start       import cost, which a per-job process pays every job
  speed            extraction time per document
  robustness       what happens on a truncated file: an exception, or
                   silent partial text (far worse -- a short read that
                   looks like a complete one is the failure this whole
                   system is built to avoid)

Licence and dependency weight are recorded separately: they come from
the installed files, not from a measurement.
"""
import hashlib
import importlib
import json
import pathlib
import subprocess
import sys
import time

FIX = pathlib.Path(sys.argv[1])
HOLD = pathlib.Path(sys.argv[2])
TRUTH = json.loads((FIX / "truth.json").read_text(encoding="utf-8"))


def with_pdfplumber(data: bytes) -> str:
    import io

    import pdfplumber
    with pdfplumber.open(io.BytesIO(data)) as doc:
        return "\n".join((page.extract_text() or "") for page in doc.pages)


def with_pypdf(data: bytes) -> str:
    import io

    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def with_pymupdf(data: bytes) -> str:
    import pymupdf
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        return "\n".join(page.get_text() for page in doc)


READERS = {"pdfplumber": with_pdfplumber, "pypdf": with_pypdf,
           "pymupdf": with_pymupdf}

IMPORTS = {"pdfplumber": "pdfplumber", "pypdf": "pypdf", "pymupdf": "pymupdf"}


def tables_with_pdfplumber(data):
    import io

    import pdfplumber
    with pdfplumber.open(io.BytesIO(data)) as doc:
        out = []
        for page in doc.pages:
            out += page.extract_tables() or []
        return out


def main():
    results = {name: {} for name in READERS}

    # cold import cost, in a fresh interpreter each time
    for name, module in IMPORTS.items():
        start = time.perf_counter()
        subprocess.run([sys.executable, "-c", f"import {module}"], check=True)
        results[name]["cold_import_s"] = round(time.perf_counter() - start, 3)

    documents = sorted(FIX.glob("*.pdf")) + sorted(HOLD.glob("*.pdf"))
    for name, reader in READERS.items():
        recovered, expected_total, timings, failures = 0, 0, [], []
        determinism = True
        for path in documents:
            data = path.read_bytes()
            start = time.perf_counter()
            try:
                text = reader(data)
            except Exception as exc:                      # noqa: BLE001
                failures.append(f"{path.name}: {type(exc).__name__}")
                continue
            timings.append(time.perf_counter() - start)
            if hashlib.sha256(reader(data).encode()).hexdigest() != \
               hashlib.sha256(text.encode()).hexdigest():
                determinism = False
            wanted = TRUTH.get(path.name, {}).get("strings", [])
            expected_total += len(wanted)
            recovered += sum(1 for s in wanted if s in text)
        results[name].update(
            strings_recovered=f"{recovered}/{expected_total}",
            documents_read=f"{len(timings)}/{len(documents)}",
            deterministic=determinism,
            total_extract_s=round(sum(timings), 3),
            failures=failures)

        # truncation: an exception is acceptable, silent partial text is not
        truncated = (FIX / "statement-multipage.pdf").read_bytes()[:900]
        try:
            text = reader(truncated)
            results[name]["on_truncated"] = (
                f"returned {len(text)} chars of text" if text.strip()
                else "returned empty text")
        except Exception as exc:                          # noqa: BLE001
            results[name]["on_truncated"] = f"raised {type(exc).__name__}"

    # tables are a pdfplumber-only capability among these three
    table_truth = TRUTH["invoice-table.pdf"]["table"]
    found = tables_with_pdfplumber((FIX / "invoice-table.pdf").read_bytes())
    results["pdfplumber"]["table_grid_exact"] = bool(
        found and found[0] == table_truth)
    results["pypdf"]["table_grid_exact"] = "no table API"
    results["pymupdf"]["table_grid_exact"] = "has find_tables (not measured)"

    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
