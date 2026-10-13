#!/usr/bin/env python3
"""
Compare a fresh scrape with the previous one and record what changed.

Each run, for a scraper called NAME:
    data/NAME_latest.csv    replaced with the fresh scrape (the next run's baseline)
    data/NAME_changes.csv   one row appended per new, removed, or re-priced product
    data/NAME_report.md     readable summary of this run's changes

The number compared is the `price` column unless --measure names another one.

Usage
-----
    python scrape_books.py
    python report.py --name books --current books.csv

    python scrape_rates.py
    python report.py --name euro --current rates.csv --measure rate --items currencies
                     --symbol "" --source "European Central Bank"      (all on one line)
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

KEY = "url"
CHANGE_COLS = ["date", "change", "name", "brand", "old", "new", "diff", "pct", "url"]
KINDS = ["drop", "rise", "new", "removed"]


def read_rows(path: Path) -> tuple[list[str], dict[str, dict]]:
    """A scraper CSV as (column names, rows keyed by product URL)."""
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        return list(reader.fieldnames or []), {r[KEY]: r for r in reader if r.get(KEY)}


def _value(row: dict, measure: str) -> Decimal | None:
    # Decimal, not float: a rate moving from 0.8446 to 0.8451 must not round away.
    try:
        value = Decimal(row.get(measure) or "")
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


def _change(kind: str, row: dict, old: Decimal | None, new: Decimal | None, date: str) -> dict:
    both = old is not None and new is not None
    return {
        "date": date, "change": kind, "name": row.get("name", ""), "brand": row.get("brand", ""),
        "old": old, "new": new, "diff": new - old if both else None,
        "pct": round(float((new - old) / old * 100), 2) if both and old else None,
        "url": row[KEY],
    }


def diff(previous: dict[str, dict], current: dict[str, dict], date: str,
         measure: str = "price") -> list[dict]:
    """Every difference between two scrapes, biggest percentage moves first."""
    changes = []
    for url, row in current.items():
        if url not in previous:
            changes.append(_change("new", row, None, _value(row, measure), date))
            continue
        old, new = _value(previous[url], measure), _value(row, measure)
        if old is not None and new is not None and old != new:
            changes.append(_change("drop" if new < old else "rise", row, old, new, date))
    for url, row in previous.items():
        if url not in current:
            changes.append(_change("removed", row, _value(row, measure), None, date))
    return sorted(changes, key=lambda c: (KINDS.index(c["change"]), -abs(c["pct"] or 0)))


def _places(*values: Decimal | None) -> int:
    """Decimal places for one table row: the most any of its values has, and at least two."""
    return max([2] + [-v.as_tuple().exponent for v in values if v is not None])


def render(name: str, date: str, tracked: int, changes: list[dict], first_run: bool, *,
           measure: str = "price", items: str = "products", symbol: str = "$",
           source: str = "") -> str:
    lines = [f"# {name} {measure} report: {date}", ""]
    if first_run:
        lines.append(f"First run: baseline of {tracked} {items} recorded. "
                     "Changes appear from the next run.")
    elif not changes:
        lines.append(f"{tracked} {items} tracked. No changes since the previous run.")
    else:
        counts = {kind: sum(c["change"] == kind for c in changes) for kind in KINDS}
        lines.append(f"{tracked} {items} tracked. {counts['drop']} {measure} drops, "
                     f"{counts['rise']} rises, {counts['new']} new, "
                     f"{counts['removed']} no longer listed.")
    titles = {"drop": f"{measure.capitalize()} drops", "rise": f"{measure.capitalize()} rises",
              "new": f"New {items}", "removed": "No longer listed"}
    for kind in KINDS:
        rows = [c for c in changes if c["change"] == kind]
        if not rows:
            continue
        lines += ["", f"## {titles[kind]}", "", "| Name | Was | Now | Change | % |",
                  "|---|---:|---:|---:|---:|"]
        for c in rows:
            places = _places(c["old"], c["new"])
            was, now = (f"{symbol}{v:,.{places}f}" if v is not None else "" for v in (c["old"], c["new"]))
            move = f"{c['diff']:+,.{places}f}" if c["diff"] is not None else ""
            pct = f"{c['pct']:+.2f}%" if c["pct"] is not None else ""
            label = f"[{c['name'].replace('|', '/')}]({c['url']})"
            lines.append(f"| {label} | {was} | {now} | {move} | {pct} |")
    if source:
        lines += ["", f"Source: {source}"]
    return "\n".join(lines) + "\n"


def _write(path: Path, cols: list[str], rows: list[dict], append: bool = False) -> None:
    new_file = not path.exists()
    with path.open("a" if append else "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols, lineterminator="\n")
        if new_file or not append:
            writer.writeheader()
        writer.writerows(rows)


def run(name: str, current_csv: Path, data_dir: Path, date: str, force: bool = False, *,
        measure: str = "price", **wording: str) -> str:
    """Update the data files for one scraper and return the report text.

    `wording` is passed on to `render`: items, symbol, source.
    """
    cols, current = read_rows(current_csv)
    if not current:
        raise RuntimeError(f"{current_csv} has no products; keeping the previous data")

    latest = data_dir / f"{name}_latest.csv"
    first_run = not latest.exists()
    previous = {} if first_run else read_rows(latest)[1]
    # A scrape that lost most of its rows is far more likely a broken page than a real change.
    if not force and len(current) < len(previous) / 2:
        raise RuntimeError(
            f"only {len(current)} products against {len(previous)} last run; "
            "refusing to overwrite. Check the scraper, or pass --force if this is real."
        )

    changes = [] if first_run else diff(previous, current, date, measure)
    data_dir.mkdir(parents=True, exist_ok=True)
    if changes:
        _write(data_dir / f"{name}_changes.csv", CHANGE_COLS, changes, append=True)
    _write(latest, cols, list(current.values()))
    text = render(name, date, len(current), changes, first_run, measure=measure, **wording)
    (data_dir / f"{name}_report.md").write_text(text, encoding="utf-8", newline="\n")
    return text


def main() -> int:
    ap = argparse.ArgumentParser(description="Record what changed since the previous scrape.")
    ap.add_argument("--name", required=True, help="short scraper name used in file names, e.g. books")
    ap.add_argument("--current", required=True, help="CSV the scraper just wrote")
    ap.add_argument("--data-dir", default="data", help="where the tracked files live")
    ap.add_argument("--force", action="store_true", help="accept a scrape with far fewer products")
    ap.add_argument("--measure", default="price",
                    help="column holding the number to compare, also used in the wording, e.g. rate")
    ap.add_argument("--items", default="products", help="what the rows are called, e.g. currencies")
    ap.add_argument("--symbol", default="$", help='put before each value; pass "" for none')
    ap.add_argument("--source", default="", help="credit line added under the report")
    args = ap.parse_args()

    date = datetime.now(timezone.utc).date().isoformat()
    try:
        text = run(args.name, Path(args.current), Path(args.data_dir), date, args.force,
                   measure=args.measure, items=args.items, symbol=args.symbol, source=args.source)
    except (OSError, RuntimeError) as exc:
        print(f"report failed: {exc}", file=sys.stderr)
        return 1
    print(text)
    # On GitHub Actions this puts the report on the run's summary page.
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
