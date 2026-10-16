"""Re-run of the input acceptance probe, after the encoding work.

Thirteen realistic client inputs, the same thirteen the audit measured,
fed to the production workers from a scratch directory. Read-only: it
imports solvent and writes only into a temporary directory.

Each input is classified three ways rather than two. "Refused" is not
automatically a failure -- a PDF is genuinely not a CSV and refusing it
is the right answer -- so the probe records what each outcome *should*
be and reports accepted, correctly-refused and wrongly-refused
separately. Forcing 13 of 13 would mean delivering work on formats
nothing has certified.
"""
import pathlib
import sys
import tempfile

sys.path.insert(0, __import__("os").environ.get("SOLVENT_ROOT", "/home/user/roflo"))

from solvent import docwork                          # noqa: E402
from solvent.harness import run_csv_job              # noqa: E402
from tests_solvent import fixtures_csv as fx         # noqa: E402

GOOD = "Order ID,Customer,Total\n1001,Acme,10.00\n1002,Beta,20.00\n1002,Beta,20.00\n"
ACCENTED = "Order ID,Customer,Total\n1001,Café Münster,10.00\n1002,Beta,20.00\n1002,Beta,20.00\n"

#: (label, filename, bytes, expectation) where expectation is "accept"
#: or "refuse". "refuse" is asserted only where the format is genuinely
#: outside what this system advertises and has certified.
CASES = [
    ("utf-8 comma (the happy path)", "in.csv", GOOD.encode("utf-8"), "accept"),
    ("utf-8 BOM (Excel default on Windows)", "in.csv",
     GOOD.encode("utf-8-sig"), "accept"),
    ("windows-1252 accented (EU export)", "in.csv",
     ACCENTED.encode("cp1252"), "accept"),
    ("utf-16 (Excel 'Unicode text' export)", "in.csv",
     ACCENTED.encode("utf-16"), "accept"),
    ("semicolon-delimited (EU default)", "in.csv",
     GOOD.replace(",", ";").encode("utf-8"), "accept"),
    ("tab-delimited", "in.csv", GOOD.replace(",", "\t").encode("utf-8"),
     "refuse"),
    ("comma decimal (1.234,56 EU numbers)", "in.csv",
     "Order ID,Customer,Total\n1001,Acme,\"1.234,56\"\n1002,Beta,\"2.000,00\"\n"
     "1002,Beta,\"2.000,00\"\n".encode("utf-8"), "accept"),
    ("a real .xlsx renamed .csv", "in.csv", b"PK\x03\x04" + b"\x00" * 200,
     "refuse"),
    ("PDF invoice (the commonest real job)", "in.pdf",
     b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\ntrailer\n", "refuse"),
    ("legacy .doc", "in.doc", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64,
     "refuse"),
    ("RTF", "in.rtf", rb"{\rtf1\ansi Hello}", "refuse"),
    ("OpenDocument .odt", "in.odt", b"PK\x03\x04" + b"\x00" * 64, "refuse"),
    ("plain text (supported)", "in.txt", b"Invoice 1001\nTotal: 10.00\n",
     "accept"),
]


def probe(name, payload, directory):
    """Feed one input to the worker that owns its extension."""
    source = directory / name
    source.write_bytes(payload)
    if source.suffix.lower() == ".csv":
        report = run_csv_job(source=str(source), requirements=fx.SIMPLE,
                             workdir=str(directory))
        return report.delivered, (report.escalated or "")
    problem = docwork.refusal_for(source.suffix)
    if problem:
        return False, problem
    try:
        docwork.read_source(source)
    except Exception as exc:                          # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"
    return True, "read as a document"


def main():
    root = pathlib.Path(tempfile.mkdtemp(prefix="probe-"))
    accepted = correct_refusals = wrong_refusals = wrong_accepts = 0
    lines = []
    for label, name, payload, expectation in CASES:
        directory = pathlib.Path(tempfile.mkdtemp(dir=root))
        ok, detail = probe(name, payload, directory)
        if ok and expectation == "accept":
            verdict, accepted = "ACCEPT  ", accepted + 1
        elif ok:
            verdict, wrong_accepts = "ACCEPT !", wrong_accepts + 1
        elif expectation == "refuse":
            verdict, correct_refusals = "refuse ok", correct_refusals + 1
        else:
            verdict, wrong_refusals = "REFUSE !", wrong_refusals + 1
        lines.append(f"  {verdict:10s} {label:44s} {detail[:78]}")
    print("\n".join(lines))
    print(f"\naccepted {accepted} of {sum(1 for c in CASES if c[3] == 'accept')}"
          f" that should be accepted")
    print(f"correctly refused {correct_refusals} of "
          f"{sum(1 for c in CASES if c[3] == 'refuse')} that should be refused")
    print(f"wrongly refused {wrong_refusals}; wrongly accepted {wrong_accepts}")
    print(f"handled correctly: "
          f"{accepted + correct_refusals} of {len(CASES)}")


if __name__ == "__main__":
    main()
