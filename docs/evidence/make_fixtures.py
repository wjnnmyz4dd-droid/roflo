"""Four representative client PDFs whose contents are known before they exist.

Generated with fpdf2 rather than hand-written, so they carry the things a
real export carries -- compressed content streams, a font resource, real
text positioning -- instead of the uncompressed toy structure a hand-rolled
PDF has. A library that only ever reads toy PDFs has not been measured.

fpdf2 is a fixture tool in a throwaway venv. Nothing proposes shipping it.
"""
import json
import pathlib
import sys

from fpdf import FPDF

OUT = pathlib.Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)

truth = {}


def new():
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", size=11)
    return pdf


# 1. A plain invoice: facts on their own lines.
pdf = new()
lines = ["ACME TRADING LTD", "Invoice INV-2026-0411",
         "Date: 2026-04-11", "Client: Borealis GmbH",
         "Subtotal: 1250.00", "VAT 20%: 250.00", "Total due: 1500.00"]
for line in lines:
    pdf.cell(0, 8, line, new_x="LMARGIN", new_y="NEXT")
pdf.output(str(OUT / "invoice-plain.pdf"))
truth["invoice-plain.pdf"] = {"strings": lines}

# 2. An invoice with a line-item table.
pdf = new()
pdf.cell(0, 8, "Invoice INV-2026-0412", new_x="LMARGIN", new_y="NEXT")
pdf.ln(4)
rows = [["Item", "Qty", "Unit", "Amount"],
        ["Consulting", "10", "75.00", "750.00"],
        ["Support", "5", "40.00", "200.00"],
        ["Hosting", "12", "25.00", "300.00"]]
for row in rows:
    for width, cell in zip((70, 20, 30, 30), row):
        pdf.cell(width, 8, cell, border=1)
    pdf.ln()
pdf.ln(4)
pdf.cell(0, 8, "Total due: 1250.00", new_x="LMARGIN", new_y="NEXT")
pdf.output(str(OUT / "invoice-table.pdf"))
truth["invoice-table.pdf"] = {"strings": ["Invoice INV-2026-0412",
                                          "Total due: 1250.00"],
                              "table": rows}

# 3. A multi-page statement: the fact that matters is on page 2.
pdf = new()
pdf.cell(0, 8, "Statement of Account, page 1", new_x="LMARGIN", new_y="NEXT")
for index in range(40):
    pdf.cell(0, 6, f"2026-03-{index % 28 + 1:02d}  transaction {index:03d}"
             f"  {index * 13 + 7}.00", new_x="LMARGIN", new_y="NEXT")
pdf.add_page()
pdf.cell(0, 8, "Statement of Account, page 2", new_x="LMARGIN", new_y="NEXT")
pdf.cell(0, 8, "Closing balance: 9876.54", new_x="LMARGIN", new_y="NEXT")
pdf.output(str(OUT / "statement-multipage.pdf"))
truth["statement-multipage.pdf"] = {
    "strings": ["Statement of Account, page 1",
                "Statement of Account, page 2",
                "Closing balance: 9876.54"],
    "pages": 2}

# 4. Accented European text, which is where encoding assumptions show.
pdf = FPDF()
pdf.add_page()
pdf.set_font("Helvetica", size=11)
accented = ["Rechnung fuer Cafe Munster", "Kunde: Cafe Munster GmbH",
            "Betrag: 1.234,56 EUR", "Faellig: 2026-05-01"]
for line in accented:
    pdf.cell(0, 8, line, new_x="LMARGIN", new_y="NEXT")
pdf.output(str(OUT / "invoice-european.pdf"))
truth["invoice-european.pdf"] = {"strings": accented}

(OUT / "truth.json").write_text(json.dumps(truth, indent=2), encoding="utf-8")
print(f"wrote {len(truth)} fixtures to {OUT}")
