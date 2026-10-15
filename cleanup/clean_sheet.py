#!/usr/bin/env python3
"""
Clean a messy spreadsheet and show exactly what was changed.

Give it a CSV or Excel file and a small config naming each column's type. It
writes a cleaned CSV plus an Excel workbook with four tabs:

    Clean          the cleaned data
    Changes        every cell that was altered: row, column, before, after, rule
    Needs review   values it could not fix (bad email, impossible date, ...)
    Removed rows   junk, blank, total and duplicate rows, with their original content

Nothing is changed silently and nothing is thrown away: anything not in Clean
is in Removed rows, and every edit is in Changes.

Usage
-----
    python clean_sheet.py sample/messy_customers.csv --config customers.config.json
    python clean_sheet.py export.xlsx --config my.config.json --out-dir cleaned
    python clean_sheet.py export.xlsx --config my.config.json --sheet "Customers"   (or: --sheet all)
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

BLANK_WORDS = {"n/a", "#n/a", "none", "null", "-", "--", "tbd"}
# A cell that starts with the word total ("Total", "Grand Total", "Sub-total:"), not with
# a longer word such as "Totally".
TOTAL_RE = re.compile(r"\s*(grand|sub)?[\s-]*totals?\b", re.IGNORECASE)
EMAIL_RE = re.compile(r"^[a-z0-9._%+-]+@[a-z0-9-]+(\.[a-z0-9-]+)+$")
# Digits with optional thousands groups, then an optional decimal part.
US_NUMBER_RE = re.compile(r"(\d{1,3}(,\d{3})+|\d*)(\.\d*)?")
EU_NUMBER_RE = re.compile(r"(\d{1,3}(\.\d{3})+|\d*)(,\d*)?")
NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}
NAMED_MONTH_FORMATS = ["%b %d, %Y", "%B %d, %Y", "%b %d %Y", "%B %d %Y",
                       "%d %b %Y", "%d %B %Y", "%d-%b-%Y", "%d-%b-%y"]
STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA",
    "colorado": "CO", "connecticut": "CT", "delaware": "DE", "district of columbia": "DC",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID", "illinois": "IL",
    "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY", "louisiana": "LA",
    "maine": "ME", "maryland": "MD", "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC", "south dakota": "SD",
    "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT", "virginia": "VA",
    "washington": "WA", "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
}
STATE_CODES = set(STATES.values())
SKU_RE = re.compile(r"^[A-Za-z0-9_./-]+( [A-Za-z0-9_./-]+)*$")
SCIENTIFIC_RE = re.compile(r"^\d+(\.\d+)?[eE][+-]?\d+$")
QUOTES = {'"': '"', "'": "'", "“": "”", "‘": "’"}
WHOLE_NUMBER_RE = re.compile(r"^(\d{1,3}(,\d{3})+|\d+)(\.0+)?$")


# --- cell cleaners: each returns (cleaned value, problem or None) ---------------------------

def clean_text(value: str, opts: dict) -> tuple[str, str | None]:
    return " ".join(value.split()), None


def clean_name(value: str, opts: dict) -> tuple[str, str | None]:
    value = " ".join(value.split())
    if value.count(",") == 1:
        last, first = (part.strip() for part in value.split(","))
        if first and last and first.lower().rstrip(".") not in NAME_SUFFIXES:
            value = f"{first} {last}"
    if value.isupper() or value.islower():
        value = re.sub(r"[A-Za-z]+", lambda m: m.group().capitalize(), value)
    return value, None


def clean_email(value: str, opts: dict) -> tuple[str, str | None]:
    value = re.sub(r"\s+", "", value).lower().removeprefix("mailto:").strip("<>,;.")
    return value, None if EMAIL_RE.match(value) else "not a valid email address"


def clean_phone(value: str, opts: dict) -> tuple[str, str | None]:
    ext = re.search(r"(?:ext\.?|x)\s*(\d+)\s*$", value, re.IGNORECASE)
    digits = re.sub(r"\D", "", value[: ext.start()] if ext else value)
    if len(digits) == 11 and digits[0] == "1":
        digits = digits[1:]
    if len(digits) != 10:
        return " ".join(value.split()), "not a 10-digit phone number"
    phone = f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    return phone + (f" x{ext.group(1)}" if ext else ""), None


def clean_date(value: str, opts: dict) -> tuple[str, str | None]:
    text = re.sub(r"\s+\d{1,2}:\d{2}.*$", "", value.strip())
    text = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", text, flags=re.IGNORECASE)
    text = " ".join(text.replace(",", ", ").split())
    first, second = ("%d", "%m") if opts.get("day_first") else ("%m", "%d")
    formats = ["%Y-%m-%d", "%Y/%m/%d"] + NAMED_MONTH_FORMATS
    for sep in "/-.":
        formats += [f"{first}{sep}{second}{sep}%Y", f"{first}{sep}{second}{sep}%y"]
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).date().isoformat(), None
        except ValueError:
            continue
    return " ".join(value.split()), "not a recognizable date"


def clean_money(value: str, opts: dict) -> tuple[str, str | None]:
    original = " ".join(value.split())
    text = "".join(value.split()).lower()
    for junk in ("usd", "eur", "$", "€"):
        text = text.replace(junk, "")
    negative = text.startswith("-") or (text.startswith("(") and text.endswith(")"))
    text = text.strip("()-")
    multiplier = 1
    if text.endswith("k"):
        multiplier, text = 1000, text[:-1]
    if not re.fullmatch(r"[\d.,]*\d[\d.,]*", text):
        return original, "not a number"
    # A separator is only trusted in full groups of three, so 1,25 or 1.200,50 is never
    # read as a whole number by accident; anything else goes to review.
    if opts.get("decimal_comma"):
        if not EU_NUMBER_RE.fullmatch(text):
            return original, "number format unclear (does not fit the decimal_comma reading)"
        text = text.replace(".", "").replace(",", ".")
    elif not US_NUMBER_RE.fullmatch(text):
        if "," in text:
            return original, "number format unclear (comma decimal?)"
        return original, "number format unclear (dots as thousands separators?)"
    else:
        text = text.replace(",", "")
    number = float(text) * multiplier
    return f"{-number if negative else number:.2f}", None


def clean_state(value: str, opts: dict) -> tuple[str, str | None]:
    text = " ".join(value.replace(".", "").split())
    if text.upper() in STATE_CODES:
        return text.upper(), None
    if text.lower() in STATES:
        return STATES[text.lower()], None
    return text, "not a US state"


def clean_sku(value: str, opts: dict) -> tuple[str, str | None]:
    text = value.strip()
    if len(text) > 1 and QUOTES.get(text[0]) == text[-1]:
        text = text[1:-1]
    elif text.startswith("'"):  # Excel's "store as text" marker
        text = text[1:]
    text = " ".join(text.split())
    as_is = " ".join(value.split())
    if SCIENTIFIC_RE.match(text):
        return as_is, ("looks like a number Excel converted to scientific notation; "
                       "the original SKU or barcode may have been lost")
    if not SKU_RE.match(text):
        return as_is, ("unexpected character for a SKU or barcode "
                       "(allowed: letters, digits, - _ . / and spaces)")
    return (text.upper() if opts.get("uppercase") else text), None


def clean_integer(value: str, opts: dict) -> tuple[str, str | None]:
    text = value.strip()
    if text.startswith("-") and WHOLE_NUMBER_RE.match(text[1:]):
        # Negative stock usually means an error in the source system, so it is not converted.
        return text, "negative quantity; usually an error in the source system"
    if not WHOLE_NUMBER_RE.match(text):
        return " ".join(value.split()), "not a whole number"
    return str(int(text.split(".")[0].replace(",", ""))), None


CLEANERS = {
    "text": (clean_text, "whitespace tidied"),
    "name": (clean_name, "name formatted"),
    "email": (clean_email, "email normalized"),
    "phone": (clean_phone, "phone formatted"),
    "date": (clean_date, "date converted to YYYY-MM-DD"),
    "money": (clean_money, "amount converted to a plain number"),
    "state": (clean_state, "state converted to 2-letter code"),
    "sku": (clean_sku, "SKU/code tidied"),
    "integer": (clean_integer, "quantity converted to a whole number"),
}


# --- reading ------------------------------------------------------------------------------

def _cell_text(cell) -> str:
    if cell is None:
        return ""
    if isinstance(cell, (datetime, date)):
        return cell.isoformat()[:10]
    if isinstance(cell, float) and cell.is_integer():
        return str(int(cell))
    return str(cell)


def is_workbook(path: Path) -> bool:
    return path.suffix.lower() in (".xlsx", ".xlsm")


def _open_workbook(path: Path):
    from openpyxl import load_workbook

    return load_workbook(path, read_only=True, data_only=True)


def list_sheets(path: Path) -> list[tuple[str, bool]]:
    """(name, hidden) for each worksheet of a workbook, in tab order."""
    book = _open_workbook(path)
    try:
        return [(ws.title, ws.sheet_state != "visible") for ws in book.worksheets]
    finally:
        book.close()  # read-only mode keeps the file locked until closed


def match_sheet(names: list[str], wanted: str) -> str:
    """The sheet `wanted` names: exact match first, then ignoring case and surrounding spaces."""
    if wanted in names:
        return wanted
    hits = [n for n in names if n.strip().casefold() == wanted.strip().casefold()]
    if len(hits) == 1:
        return hits[0]
    if hits:
        raise RuntimeError(f'sheet "{wanted}" matches more than one sheet '
                           f'({", ".join(quote(n) for n in hits)}); give the exact name')
    raise RuntimeError(f'no sheet named "{wanted}". '
                       f'Sheets in this workbook: {", ".join(quote(n) for n in names)}')


def quote(name: str) -> str:
    return f'"{name}"'


def read_table(path: Path, sheet: str | None = None) -> list[list[str]]:
    """Every row of a CSV, or of one sheet of an .xlsx (the first unless `sheet` names one), as text."""
    if is_workbook(path):
        book = _open_workbook(path)
        try:
            if not book.worksheets:
                raise RuntimeError(f"{path.name} has no worksheets")
            if sheet is None:
                worksheet = book.worksheets[0]
            else:
                worksheet = book[match_sheet([ws.title for ws in book.worksheets], sheet)]
            rows = worksheet.iter_rows(values_only=True)
            return [[_cell_text(c) for c in row] for row in rows]
        finally:
            book.close()  # read-only mode keeps the file locked until closed
    if sheet is not None:
        raise RuntimeError(f"a sheet can only be picked in an Excel workbook (.xlsx/.xlsm), "
                           f"and {path.name} is not one")
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            with path.open(newline="", encoding=encoding) as fh:
                return [list(row) for row in csv.reader(fh)]
        except UnicodeDecodeError:
            continue
    raise RuntimeError(f"could not decode {path}")


# --- cleaning -----------------------------------------------------------------------------

@dataclass
class Result:
    header: list[str]
    types: list[str]
    rows: list[list[str]] = field(default_factory=list)
    source_rows: list[int] = field(default_factory=list)
    changes: list[dict] = field(default_factory=list)
    review: list[dict] = field(default_factory=list)
    removed: list[dict] = field(default_factory=list)
    rows_in: int = 0


def _key(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _find_header(table: list[list[str]], aliases: dict[str, str]) -> int:
    """Index of the row that looks most like the header (exports often start with title rows)."""
    best, best_hits = 0, 0
    for i, row in enumerate(table[:20]):
        hits = sum(_key(cell) in aliases for cell in row)
        if hits > best_hits:
            best, best_hits = i, hits
    return best


def clean_table(table: list[list[str]], config: dict) -> Result:
    columns = config.get("columns", {})
    aliases = {}
    for name, spec in columns.items():
        for alias in [name, *spec.get("aliases", [])]:
            aliases[_key(alias)] = name

    head = _find_header(table, aliases)
    width = max((len(row) for row in table), default=0)
    raw_header = table[head] + [""] * (width - len(table[head])) if table else []
    header = [aliases.get(_key(h), " ".join(h.split()) or f"column_{i + 1}")
              for i, h in enumerate(raw_header)]
    types = [columns.get(h, {}).get("type", "text") for h in header]
    result = Result(header=header, types=types, rows_in=max(len(table) - head - 1, 0))

    def remove(row_no: int, reason: str, cells: list[str]) -> None:
        original = " | ".join(c.strip() for c in cells if c.strip())
        result.removed.append({"source_row": row_no, "reason": reason, "original": original})

    for i, cells in enumerate(table[:head]):
        if any(c.strip() for c in cells):
            remove(i + 1, "title row above the header", cells)

    required = set(config.get("required", []))
    kept: list[tuple[int, list[str], list[str]]] = []  # (source row, cleaned, original)
    pending_review: list[dict] = []
    for i, cells in enumerate(table[head + 1:], start=head + 2):
        cells = cells + [""] * (width - len(cells))
        filled = [c for c in cells if c.strip()]
        if not filled:
            remove(i, "blank row", cells)
            continue
        if len(filled) <= 3 and any(TOTAL_RE.match(c) for c in filled):
            remove(i, "total row", cells)
            continue

        cleaned = []
        for column, kind, before in zip(header, types, cells):
            cleaner, rule = CLEANERS[kind]
            stripped = before.strip()
            problem = None
            if stripped.lower() in BLANK_WORDS:
                after, rule = "", "placeholder replaced with blank"
            elif not stripped:
                after, rule = "", "whitespace tidied"
            else:
                after, problem = cleaner(before, columns.get(column, {}))
                if after == stripped:
                    rule = "whitespace tidied"
            if after != before:
                result.changes.append({"source_row": i, "column": column,
                                       "before": before, "after": after, "rule": rule})
            if problem:
                pending_review.append({"source_row": i, "column": column,
                                       "value": after, "problem": problem})
            elif not after and column in required:
                pending_review.append({"source_row": i, "column": column,
                                       "value": "", "problem": "required value is missing"})
            cleaned.append(after)
        kept.append((i, cleaned, cells))

    # Duplicates: identical rows first, then rows sharing the configured key columns.
    dedupe_on = [header.index(c) for c in config.get("dedupe_on", []) if c in header]
    label = " + ".join(header[j] for j in dedupe_on)
    seen_exact: dict[tuple, int] = {}
    groups: dict[tuple, list[tuple[int, list[str], list[str]]]] = {}
    survivors = []
    for entry in kept:
        row_no, cleaned, cells = entry
        if tuple(cleaned) in seen_exact:
            remove(row_no, f"exact duplicate of row {seen_exact[tuple(cleaned)]}", cells)
            continue
        seen_exact[tuple(cleaned)] = row_no
        key = tuple(cleaned[j].lower() for j in dedupe_on)
        if dedupe_on and any(key):
            groups.setdefault(key, []).append(entry)
        survivors.append(entry)
    dropped = set()
    for group in groups.values():
        if len(group) < 2:
            continue
        # Keep the most complete row; the others stay recoverable in Removed rows.
        keeper = max(group, key=lambda e: (sum(bool(c) for c in e[1]), -e[0]))
        for row_no, _, cells in group:
            if row_no != keeper[0]:
                dropped.add(row_no)
                remove(row_no, f"same {label} as row {keeper[0]}, which has more filled in", cells)

    for row_no, cleaned, _ in survivors:
        if row_no not in dropped:
            result.rows.append(cleaned)
            result.source_rows.append(row_no)

    # Columns with no header and no data are export debris.
    empty = [j for j, h in enumerate(raw_header)
             if not h.strip() and not any(row[j] for row in result.rows)]
    for j in reversed(empty):
        del result.header[j], result.types[j]
        for row in result.rows:
            del row[j]

    clean_row = {src: n for n, src in enumerate(result.source_rows, start=2)}
    result.review = [{"clean_row": clean_row[r["source_row"]], **r}
                     for r in pending_review if r["source_row"] in clean_row]
    result.changes = [c for c in result.changes if c["source_row"] in clean_row]
    result.removed.sort(key=lambda r: r["source_row"])
    return result


# --- writing ------------------------------------------------------------------------------

def write_csv(result: Result, path: Path) -> None:
    # utf-8-sig so Excel opens accented names correctly.
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(result.header)
        writer.writerows(result.rows)


def _typed(value: str, kind: str):
    """Numbers as Excel numbers; values left for review stay text."""
    if kind == "money" and re.fullmatch(r"-?\d+\.\d\d", value):
        return float(value)
    if kind == "integer" and value.isdigit():
        return int(value)
    return value


def write_workbook(result: Result, path: Path) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Font

    def add_sheet(book, title, header, rows):
        sheet = book.create_sheet(title)
        sheet.append(header)
        for row in rows:
            sheet.append(row)
        for cell in sheet[1]:
            cell.font = Font(bold=True)
        sheet.freeze_panes = "A2"
        for letter, column in zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ", zip(header, *rows)):
            sheet.column_dimensions[letter].width = min(max(len(str(v)) for v in column) + 2, 60)
        return sheet

    book = Workbook()
    book.remove(book.active)
    money = [j for j, kind in enumerate(result.types) if kind == "money"]
    typed = [[_typed(v, kind) for v, kind in zip(row, result.types)] for row in result.rows]
    clean = add_sheet(book, "Clean", result.header, typed)
    for j in money:
        for cell in list(clean.columns)[j][1:]:
            cell.number_format = "#,##0.00"
    for title, cols, records in [
        ("Changes", ["source_row", "column", "before", "after", "rule"], result.changes),
        ("Needs review", ["clean_row", "source_row", "column", "value", "problem"], result.review),
        ("Removed rows", ["source_row", "reason", "original"], result.removed),
    ]:
        add_sheet(book, title, cols, [[r[c] for c in cols] for r in records])
    book.save(path)


def summary(result: Result) -> str:
    lines = [f"{result.rows_in} rows in, {len(result.rows)} rows out, "
             f"{len(result.removed)} removed, {len(result.changes)} cells changed, "
             f"{len(result.review)} values need review"]
    for title, counts in [
        ("Changes", Counter(c["rule"] for c in result.changes)),
        ("Needs review", Counter(f"{r['column']}: {r['problem']}" for r in result.review)),
        ("Removed", Counter(re.sub(r" of row \d+| as row \d+.*", "", r["reason"])
                            for r in result.removed)),
    ]:
        if counts:
            lines.append(f"\n{title}")
            lines += [f"  {n:>4}  {what}" for what, n in counts.most_common()]
    return "\n".join(lines)


# --- command line -------------------------------------------------------------------------

ALL_SHEETS = "all"
NO_ROWS = "no data rows found. Check the config's column names."


def safe_part(name: str) -> str:
    """A sheet name made safe to use inside a file name."""
    return re.sub(r"[^\w.-]+", "_", name).strip("._") or "sheet"


def _file_parts(names: list[str]) -> list[str]:
    """safe_part for each sheet, numbered where two would clash (file names ignore case on Windows)."""
    parts, used = [], set()
    for name in names:
        base = part = safe_part(name)
        n = 1
        while part.casefold() in used:
            n += 1
            part = f"{base}_{n}"
        used.add(part.casefold())
        parts.append(part)
    return parts


def _label(name: str, hidden: bool) -> str:
    return quote(name) + (" (hidden)" if hidden else "")


def _load_config(path: Path) -> dict:
    config = json.loads(path.read_text(encoding="utf-8"))
    unknown = {s.get("type", "text") for s in config.get("columns", {}).values()} - set(CLEANERS)
    if unknown:
        raise RuntimeError(f"unknown column type(s) in config: {', '.join(sorted(unknown))}")
    return config


def _write_outputs(result: Result, out_dir: Path, stem: str) -> str:
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"{stem}_clean.csv"
    book_path = out_dir / f"{stem}_clean.xlsx"
    write_csv(result, csv_path)
    write_workbook(result, book_path)
    return f"-> {csv_path.resolve()}\n-> {book_path.resolve()}"


def _skipped_sheets_notice(sheets: list[tuple[str, bool]]) -> str:
    rest = ", ".join(_label(*s) for s in sheets[1:])
    return (f"Cleaned sheet {_label(*sheets[0])}, the first of {len(sheets)}. Not cleaned: {rest}.\n"
            f"Use --sheet NAME to clean another sheet, or --sheet all to clean each one "
            f"separately.\n")


def clean_one_sheet(source: Path, config: dict, out_dir: Path, sheet: str | None) -> int:
    stem, heading = source.stem, ""
    if is_workbook(source):
        sheets = list_sheets(source)
        if sheet is None and len(sheets) > 1:
            print(_skipped_sheets_notice(sheets))
        elif sheet is not None:
            sheet = match_sheet([name for name, _ in sheets], sheet)
            # Named after the sheet so cleaning another sheet does not overwrite this one.
            stem = f"{source.stem}_{safe_part(sheet)}"
            heading = f"Sheet {_label(sheet, dict(sheets)[sheet])}\n"
    result = clean_table(read_table(source, sheet), config)
    if not result.rows:
        raise RuntimeError(NO_ROWS)
    paths = _write_outputs(result, out_dir, stem)
    print(f"{heading}{summary(result)}\n\n{paths}")
    return 0


def clean_all_sheets(source: Path, config: dict, out_dir: Path) -> int:
    """Each sheet cleaned on its own, as if picked with --sheet; empty sheets are skipped."""
    sheets = list_sheets(source)
    parts = _file_parts([name for name, _ in sheets])
    skipped = []
    for (name, hidden), part in zip(sheets, parts):
        result = clean_table(read_table(source, name), config)
        if not result.rows:
            skipped.append(_label(name, hidden))
            print(f"Sheet {_label(name, hidden)}: no data rows, skipped.\n")
            continue
        paths = _write_outputs(result, out_dir, f"{source.stem}_{part}")
        print(f"Sheet {_label(name, hidden)}\n{summary(result)}\n\n{paths}\n")
    if len(skipped) == len(sheets):
        raise RuntimeError("no data rows found in any sheet. Check the config's column names.")
    done = len(sheets) - len(skipped)
    print(f"Cleaned {done} of {len(sheets)} sheets."
          + (f" Skipped (no data rows): {', '.join(skipped)}." if skipped else ""))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Clean a messy spreadsheet and log every change.")
    ap.add_argument("input", help="CSV or .xlsx file to clean")
    ap.add_argument("--config", required=True, help="JSON file describing the columns")
    ap.add_argument("--out-dir", default="", help="where to write results (default: next to input)")
    ap.add_argument("--sheet", help="workbook sheet to clean (default: the first); 'all' cleans "
                                    "each sheet separately; '=all' picks a sheet named all")
    args = ap.parse_args(argv)

    source = Path(args.input)
    out_dir = Path(args.out_dir) if args.out_dir else source.parent
    try:
        config = _load_config(Path(args.config))
        if args.sheet is None:
            return clean_one_sheet(source, config, out_dir, None)
        if not is_workbook(source):
            raise RuntimeError(f"--sheet only works with Excel workbooks (.xlsx/.xlsm), "
                               f"and {source.name} is not one")
        if args.sheet.strip().casefold() == ALL_SHEETS:
            return clean_all_sheets(source, config, out_dir)
        # A leading "=" means "the sheet with this name", so a sheet called "all" can be picked.
        return clean_one_sheet(source, config, out_dir, args.sheet.removeprefix("="))
    except (OSError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"cleanup failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
