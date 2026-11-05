"""Small, defensive CSV helpers for uploads and exports."""
import csv
import io

MAX_CHARS = 2_000_000
MAX_ROWS = 5000
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


class CsvError(ValueError):
    pass


def safe_cell(value):
    """Stop spreadsheet formulas in exported text (a name such as =HYPERLINK(...) would run in Excel)."""
    s = "" if value is None else str(value)
    return "'" + s if s.startswith(_FORMULA_START) else s


def clean_text(value, limit):
    """Free text from a file: single line, no leading formula characters."""
    s = " ".join(str(value or "").split())
    return s.lstrip("=+-@")[:limit]


def read_rows(text, aliases):
    """Parse CSV/TSV/semicolon text into [(line_number, {canonical_name: value})].

    `aliases` maps a canonical column name to the header spellings accepted for it."""
    if not isinstance(text, str) or not text.strip():
        raise CsvError("The file is empty.")
    if len(text) > MAX_CHARS:
        raise CsvError("The file is too large (limit 2 MB).")
    text = text.lstrip("﻿")
    first = text.splitlines()[0]
    delim = max(",;\t", key=first.count)
    reader = csv.reader(io.StringIO(text), delimiter=delim)
    rows = [r for r in reader if any(c.strip() for c in r)]
    if not rows:
        raise CsvError("The file is empty.")
    head = [h.strip().lower().replace(" ", "_") for h in rows[0]]
    index = {}
    for canon, names in aliases.items():
        for i, h in enumerate(head):
            if h in names and canon not in index:
                index[canon] = i
    body = rows[1:]
    if len(body) > MAX_ROWS:
        raise CsvError(f"Too many rows (limit {MAX_ROWS}).")
    out = []
    for n, r in enumerate(body, start=2):
        out.append((n, {c: (r[i].strip() if i < len(r) else "") for c, i in index.items()}))
    return out, set(index)
