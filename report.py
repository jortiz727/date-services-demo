#!/usr/bin/env python3
"""
Compare a fresh scrape with the previous one and record what changed.

Each run, for a scraper called NAME:
    data/NAME_latest.csv    replaced with the fresh scrape (the next run's baseline)
    data/NAME_changes.csv   one row appended per new, removed, or re-priced product
    data/NAME_report.md     readable summary of this run's changes

Usage
-----
    python scrape_books.py
    python report.py --name books --current books.csv
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

KEY = "url"
CHANGE_COLS = ["date", "change", "name", "brand", "old_price", "new_price", "diff", "url"]
SECTIONS = [("drop", "Price drops"), ("rise", "Price rises"), ("new", "New products"),
            ("removed", "No longer listed")]


def read_rows(path: Path) -> tuple[list[str], dict[str, dict]]:
    """A scraper CSV as (column names, rows keyed by product URL)."""
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        return list(reader.fieldnames or []), {r[KEY]: r for r in reader if r.get(KEY)}


def _price(row: dict) -> float | None:
    try:
        return float(row.get("price") or "")
    except ValueError:
        return None


def _change(kind: str, row: dict, old: float | None, new: float | None, date: str) -> dict:
    both = old is not None and new is not None
    return {
        "date": date, "change": kind, "name": row.get("name", ""), "brand": row.get("brand", ""),
        "old_price": old, "new_price": new, "diff": round(new - old, 2) if both else None,
        "url": row[KEY],
    }


def diff(previous: dict[str, dict], current: dict[str, dict], date: str) -> list[dict]:
    """Every difference between two scrapes, biggest price moves first."""
    changes = []
    for url, row in current.items():
        if url not in previous:
            changes.append(_change("new", row, None, _price(row), date))
            continue
        old, new = _price(previous[url]), _price(row)
        if old is not None and new is not None and old != new:
            changes.append(_change("drop" if new < old else "rise", row, old, new, date))
    for url, row in previous.items():
        if url not in current:
            changes.append(_change("removed", row, _price(row), None, date))
    order = [kind for kind, _ in SECTIONS]
    return sorted(changes, key=lambda c: (order.index(c["change"]), -abs(c["diff"] or 0)))


def _usd(value: float | None) -> str:
    return f"${value:,.2f}" if value is not None else ""


def render(name: str, date: str, tracked: int, changes: list[dict], first_run: bool) -> str:
    lines = [f"# {name} price report: {date}", ""]
    if first_run:
        lines.append(f"First run: baseline of {tracked} products recorded. "
                     "Changes appear from the next run.")
        return "\n".join(lines) + "\n"
    if not changes:
        lines.append(f"{tracked} products tracked. No changes since the previous run.")
        return "\n".join(lines) + "\n"

    counts = {kind: sum(c["change"] == kind for c in changes) for kind, _ in SECTIONS}
    lines.append(f"{tracked} products tracked. {counts['drop']} price drops, {counts['rise']} rises, "
                 f"{counts['new']} new, {counts['removed']} no longer listed.")
    for kind, title in SECTIONS:
        rows = [c for c in changes if c["change"] == kind]
        if not rows:
            continue
        lines += ["", f"## {title}", "", "| Product | Was | Now | Change |", "|---|---:|---:|---:|"]
        for c in rows:
            label = f"[{c['name'].replace('|', '/')}]({c['url']})"
            move = f"{c['diff']:+,.2f}" if c["diff"] is not None else ""
            lines.append(f"| {label} | {_usd(c['old_price'])} | {_usd(c['new_price'])} | {move} |")
    return "\n".join(lines) + "\n"


def _write(path: Path, cols: list[str], rows: list[dict], append: bool = False) -> None:
    new_file = not path.exists()
    with path.open("a" if append else "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols, lineterminator="\n")
        if new_file or not append:
            writer.writeheader()
        writer.writerows(rows)


def run(name: str, current_csv: Path, data_dir: Path, date: str, force: bool = False) -> str:
    """Update the data files for one scraper and return the report text."""
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

    changes = [] if first_run else diff(previous, current, date)
    data_dir.mkdir(parents=True, exist_ok=True)
    if changes:
        _write(data_dir / f"{name}_changes.csv", CHANGE_COLS, changes, append=True)
    _write(latest, cols, list(current.values()))
    text = render(name, date, len(current), changes, first_run)
    (data_dir / f"{name}_report.md").write_text(text, encoding="utf-8", newline="\n")
    return text


def main() -> int:
    ap = argparse.ArgumentParser(description="Record what changed since the previous scrape.")
    ap.add_argument("--name", required=True, help="short scraper name used in file names, e.g. books")
    ap.add_argument("--current", required=True, help="CSV the scraper just wrote")
    ap.add_argument("--data-dir", default="data", help="where the tracked files live")
    ap.add_argument("--force", action="store_true", help="accept a scrape with far fewer products")
    args = ap.parse_args()

    date = datetime.now(timezone.utc).date().isoformat()
    try:
        text = run(args.name, Path(args.current), Path(args.data_dir), date, args.force)
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
