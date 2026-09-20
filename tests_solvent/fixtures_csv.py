"""Synthetic client CSVs and checklists for the capability suite.

Every fixture is a file on disk, because the point of this capability is that it
produces and verifies real bytes.
"""

from __future__ import annotations

import pathlib
import tempfile

from solvent.types import Criticality, Requirement, RequirementSource as RS

CLEAN = """Order ID,Customer,Order Date,Region,Units,Total
1001,Acme Corp,2026-01-15,North,10,250.00
1002,Beta LLC,2026-01-16,South,5,200.00
"""

DUPLICATES = """Order ID,Customer,Order Date,Region,Units,Total
1001,Acme Corp,2026-01-15,North,10,250.00
1002,Beta LLC,2026-01-16,South,5,200.00
1001,Acme Corp,2026-01-15,North,10,250.00
1002,Beta LLC,2026-01-16,South,5,200.00
"""

MIXED_DATES = """Order ID,Customer,Order Date,Region,Units,Total
1001,Acme Corp,2026-01-15,North,10,250.00
1002,Beta LLC,01/16/2026,south,5,200.00
1003,Gamma Inc,17-Jan-2026,EAST,8,100.00
1004,Delta Co,2026/01/18,west,2,199.98
"""

MESSY = """Order ID,Customer,Order Date,Region,Units,Total
1001,  Acme Corp  ,2026-01-15,North,10,250.00
1002,Beta LLC,01/16/2026,south,5,200.00
1003,Acme Corp,2026-01-17,North,3,75.00
1002,Beta LLC,01/16/2026,south,5,200.00
1004,"Gamma, Inc",17-Jan-2026,EAST,8,100.00
1005,Delta Co,2026-01-18,west,2,199.98
1006,Épsilon Ltd,2026-01-19,South,7,280.00
1004,"Gamma, Inc",17-Jan-2026,EAST,8,100.00
1007,Zeta "Quoted" Co,2026-01-20,north,1,25.00
1008,Eta Co,,North,4,220.00
"""

#: Identifiers with leading zeros, which a naive numeric pass would destroy.
LEADING_ZEROS = """Account,Customer,Order Date,Region,Total
00123,Acme Corp,2026-01-15,North,250.00
00045,Beta LLC,01/16/2026,south,200.00
00123,Acme Corp,2026-01-15,North,250.00
"""

EMBEDDED_NEWLINE = '''Order ID,Customer,Order Date,Region,Total
1001,"Acme Corp
Suite 4",2026-01-15,North,250.00
1002,Beta LLC,01/16/2026,south,200.00
'''

MISSING_COLUMN = """Order ID,Customer,Order Date,Units
1001,Acme Corp,2026-01-15,10
"""

DUPLICATE_HEADERS = """Order ID,Customer,Total,Total
1001,Acme Corp,250.00,250.00
"""

MALFORMED = 'Order ID,Customer\n1001,"unterminated quote\n1002,fine\n'

EMPTY = ""


def workspace(content: str, name: str = "source.csv") -> tuple[str, str]:
    """Write ``content`` to a fresh directory. Returns ``(source, workdir)``."""
    directory = pathlib.Path(tempfile.mkdtemp())
    source = directory / name
    source.write_text(content, encoding="utf-8")
    return str(source), str(directory)


def req(rid, text, check, params=None, source=RS.CLIENT_CONFIRMED_STRUCTURED,
        criticality=Criticality.MANDATORY, acceptance="") -> Requirement:
    return Requirement(id=rid, text=text, source=source,
                       acceptance=acceptance or text, check=check,
                       params=params or {}, criticality=criticality)


DEDUPE = req("R-DEDUPE", "Remove duplicate records.", "drop_exact_duplicates")
ISO_DATES = req("R-DATES", "Order Date must be YYYY-MM-DD.", "normalise_dates",
                {"columns": ["Order Date"]})
REGIONS = req("R-REGION", "Region must be Title Case.", "map_values",
              {"column": "Region", "case_insensitive": True,
               "mapping": {"north": "North", "south": "South",
                           "east": "East", "west": "West"}})
KEEP_TOTALS = req("R-TOTAL", "Do not change any Total.", "preserve_columns",
                  {"columns": ["Total"]})
RECONCILE = req("R-ROWS", "No row may be lost except a duplicate.",
                "row_reconciliation", {"duplicates_removed": True},
                source=RS.DERIVED)
OPENS = req("R-OPEN", "The deliverable must open as CSV.", "parses_as_csv", {},
            source=RS.SYSTEM_SAFETY)
TRIM = req("R-TRIM", "Trim padding from Customer.", "trim_whitespace",
           {"columns": ["Customer"]})

RENAME = req("R-RENAME", "Rename Units to Quantity.", "rename_headers",
             {"mapping": {"Units": "Quantity"}})
SORT = req("R-SORT", "Sort the rows by Order ID.", "sort_rows",
           {"columns": ["Order ID"]})

SIMPLE = [DEDUPE, RECONCILE, OPENS]
MODERATE = [DEDUPE, ISO_DATES, REGIONS, RECONCILE, OPENS]
COMPLEX = [DEDUPE, ISO_DATES, REGIONS, KEEP_TOTALS, TRIM, RECONCILE, OPENS]


#: Headers that are not ASCII and not unique-by-prefix. A rename that matched
#: loosely, or normalised case, would hit the wrong column here.
UNICODE_HEADERS = """Réf,Réference,Order Date,Région,Units,Total
1001,A-1,2026-01-15,North,10,250.00
1002,B-2,2026-01-16,South,5,200.00
"""

#: A byte-order mark in front of the first header name, which is what a
#: spreadsheet exported from Windows actually hands over.
BOM_SOURCE = "\ufeffOrder ID,Customer,Order Date,Region,Units,Total\n" \
             "1002,Beta LLC,2026-01-16,South,5,200.00\n" \
             "1001,Acme Corp,2026-01-15,North,10,250.00\n"

CRLF_SOURCE = ("Order ID,Customer,Order Date,Region,Units,Total\r\n"
               "1002,Beta LLC,2026-01-16,South,5,200.00\r\n"
               "1001,Acme Corp,2026-01-15,North,10,250.00\r\n")

#: Order IDs whose text order is not their numeric order: "10" sorts before
#: "9". Sorting is defined as text, so this fixture pins that definition rather
#: than leaving it to whichever one the implementation happened to pick.
TEXT_ORDER = """Order ID,Customer,Order Date,Region,Units,Total
9,Acme Corp,2026-01-15,North,10,250.00
10,Beta LLC,2026-01-16,South,5,200.00
2,Gamma Inc,2026-01-17,East,8,100.00
"""

#: Two rows sharing a sort key, to pin that ordering is stable rather than
#: arbitrary among equals.
TIED_KEYS = """Order ID,Customer,Order Date,Region,Units,Total
1001,Zeta Co,2026-01-15,North,10,250.00
1001,Acme Corp,2026-01-16,South,5,200.00
1000,Beta LLC,2026-01-17,East,8,100.00
"""

#: A row whose sort key is empty, which has to go somewhere deterministic.
BLANK_KEYS = """Order ID,Customer,Order Date,Region,Units,Total
1001,Acme Corp,2026-01-15,North,10,250.00
,Beta LLC,2026-01-16,South,5,200.00
1000,Gamma Inc,2026-01-17,East,8,100.00
"""

#: A name that already exists, so renaming onto it would silently merge two
#: different columns into one.
COLLIDING_TARGET = """Order ID,Units,Quantity,Total
1001,10,99,250.00
1002,5,98,200.00
"""
