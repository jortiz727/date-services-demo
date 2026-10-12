#!/usr/bin/env python3
"""
Scrape the "Countries of the World" page on scrapethissite.com into a CSV.

scrapethissite.com is a public sandbox whose FAQ invites automated scraping,
on three conditions this script follows: at most one request per second, an
identifying user agent, and respect for its robots.txt. This is the
single-page demo that is safe to publish.

Why this works the way it does
------------------------------
All 250 countries are in one server-rendered page, each in a `div.country`
with its name, capital, population and area in separately classed elements.
One request is the whole job; the work is turning text into typed columns.

Usage
-----
    python scrape_countries.py                           # -> countries.csv
    python scrape_countries.py --sort population --min-population 50000000
    python scrape_countries.py --sheet <id-or-url>       # also push to Google Sheets
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from common import DEFAULT_CREDS, POLITE_HEADERS, fetch, open_sheet, write_csv, write_sheet

URL = "https://www.scrapethissite.com/pages/simple/"
SHEET_PREFIX = "countries_"
# Largest first for the numeric columns, A to Z for the name.
SORTS = {
    "name": lambda c: c.name.lower(),
    "population": lambda c: -(c.population or 0),
    "area": lambda c: -(c.area_km2 or 0),
    "density": lambda c: -(c.density_per_km2 or 0),
}


@dataclass
class Country:
    name: str
    capital: str
    population: int | None
    area_km2: float | None
    density_per_km2: float | None
    scraped_at: str


def _number(text: str) -> float | None:
    try:
        return float(text.replace(",", "").strip())
    except ValueError:
        return None


def parse_countries(html: str, now: str | None = None) -> list[Country]:
    """Every country block on the page."""
    now = now or datetime.now(timezone.utc).isoformat(timespec="seconds")

    def text(block, selector: str) -> str:
        el = block.select_one(selector)
        return el.get_text(" ", strip=True) if el else ""

    countries: list[Country] = []
    for block in BeautifulSoup(html, "html.parser").select("div.country"):
        name = text(block, ".country-name")
        if not name:
            continue
        population = _number(text(block, ".country-population"))
        area = _number(text(block, ".country-area"))
        countries.append(
            Country(
                name=name,
                # A few territories have no capital; the site writes the word "None".
                capital="" if text(block, ".country-capital") == "None" else text(block, ".country-capital"),
                population=int(population) if population is not None else None,
                area_km2=area,
                density_per_km2=round(population / area, 1) if population is not None and area else None,
                scraped_at=now,
            )
        )
    return countries


def main() -> int:
    ap = argparse.ArgumentParser(description="Scrape scrapethissite.com's countries page.")
    ap.add_argument("--out", default="countries.csv", help="output CSV path")
    ap.add_argument("--sort", choices=sorted(SORTS), default="name", help="column to order by")
    ap.add_argument("--min-population", type=int, help="keep countries with at least this many people")
    ap.add_argument("--sheet", default="", help="Google Sheet ID or URL to also write to")
    ap.add_argument("--creds",
                    default=os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", DEFAULT_CREDS),
                    help="OAuth client file or service-account key for --sheet")
    args = ap.parse_args()

    session = requests.Session()
    session.headers.update(POLITE_HEADERS)

    print(f"fetching {URL}")
    scraped = parse_countries(fetch(URL, session))
    if not scraped:
        print("\nNo countries parsed. The page layout probably changed.", file=sys.stderr)
        return 1

    countries = scraped
    if args.min_population is not None:
        countries = [c for c in countries if (c.population or 0) >= args.min_population]
    countries = sorted(countries, key=SORTS[args.sort])
    print(f"{len(scraped)} countries scraped, {len(countries)} after filters")
    if not countries:
        print("Nothing left after filtering. Loosen the filters.", file=sys.stderr)
        return 1

    out = Path(args.out)
    write_csv(countries, out)
    print(f"{len(countries)} countries -> {out.resolve()}")

    if args.sheet:
        try:
            spreadsheet = open_sheet(args.sheet, args.creds)
            write_sheet(countries, spreadsheet, prefix=SHEET_PREFIX)
        except Exception as exc:
            print(f"Google Sheets write failed: {exc}", file=sys.stderr)
            return 1
        print(f"{len(countries)} countries -> {spreadsheet.url} "
              f"({SHEET_PREFIX}latest, {SHEET_PREFIX}history)")

    print()
    for c in countries[:8]:
        people = f"{c.population:,}" if c.population is not None else "n/a"
        print(f"  {c.name[:32]:<32}  {c.capital[:20]:<20}  {people:>14}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
