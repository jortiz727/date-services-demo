#!/usr/bin/env python3
"""
Collect the European Central Bank's euro foreign exchange reference rates into a CSV.

This is the demo whose data really changes: the ECB publishes a new set of
rates at around 16:00 CET every working day, so a daily run followed by
`report.py` has rises and drops to show.

The ECB's site allows free use of its information provided it is reproduced
accurately, the ECB is cited as the source, and any modification is stated.
The rates are written here exactly as published (units of each currency per
one euro). The ECB publishes them for information purposes only and
discourages using them for transactions. Its robots.txt asks for five seconds
between requests; this script makes one.

Why this works the way it does
------------------------------
The rates are a small XML file, not a web page: one `Cube` element per day
holding one `Cube` per currency. The daily file has the latest day only; the
90-day file has every working day in that window, newest first, which is what
`--date` reads. Each row links to the ECB's own page for that currency, which
also gives `report.py` a stable key.

Usage
-----
    python scrape_rates.py                       # latest rates -> rates.csv
    python scrape_rates.py --only USD GBP JPY    # a few currencies
    python scrape_rates.py --date 2026-10-12     # an earlier working day (last 90 days)
    python scrape_rates.py --sheet <id-or-url>   # also push to Google Sheets
"""
from __future__ import annotations

import argparse
import os
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import requests

from common import DEFAULT_CREDS, POLITE_HEADERS, fetch, open_sheet, write_csv, write_sheet

DAILY_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
HISTORY_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist-90d.xml"
CURRENCY_PAGE = ("https://www.ecb.europa.eu/stats/policy_and_exchange_rates/"
                 "euro_reference_exchange_rates/html/eurofxref-graph-{code}.en.html")
SOURCE = "European Central Bank, euro foreign exchange reference rates"
NS = {"ecb": "http://www.ecb.int/vocabulary/2002-08-01/eurofxref"}
SHEET_PREFIX = "rates_"
# The feed carries codes only. A code missing from here is still collected, named by its code.
NAMES = {
    "AUD": "Australian dollar", "BRL": "Brazilian real", "CAD": "Canadian dollar",
    "CHF": "Swiss franc", "CNY": "Chinese yuan renminbi", "CZK": "Czech koruna",
    "DKK": "Danish krone", "GBP": "Pound sterling", "HKD": "Hong Kong dollar",
    "HUF": "Hungarian forint", "IDR": "Indonesian rupiah", "ILS": "Israeli shekel",
    "INR": "Indian rupee", "ISK": "Icelandic krona", "JPY": "Japanese yen",
    "KRW": "South Korean won", "MXN": "Mexican peso", "MYR": "Malaysian ringgit",
    "NOK": "Norwegian krone", "NZD": "New Zealand dollar", "PHP": "Philippine peso",
    "PLN": "Polish zloty", "RON": "Romanian leu", "SEK": "Swedish krona",
    "SGD": "Singapore dollar", "THB": "Thai baht", "TRY": "Turkish lira",
    "USD": "US dollar", "ZAR": "South African rand",
}


@dataclass
class Rate:
    name: str
    code: str
    rate: float
    rate_date: str
    url: str
    scraped_at: str


def parse_rates(xml: str, date: str = "", now: str | None = None) -> list[Rate]:
    """Rates for `date` (YYYY-MM-DD), or for the newest day in the file when no date is given.

    Returns an empty list if the file has no such day.
    """
    now = now or datetime.now(timezone.utc).isoformat(timespec="seconds")
    days = ET.fromstring(xml).findall(".//ecb:Cube[@time]", NS)
    day = next((d for d in days if not date or d.get("time") == date), None)
    if day is None:
        return []

    rates: list[Rate] = []
    for cube in day.findall("ecb:Cube[@currency]", NS):
        code = cube.get("currency", "").strip().upper()
        try:
            value = float(cube.get("rate", ""))
        except ValueError:
            continue
        if not code or value <= 0:
            continue
        rates.append(
            Rate(
                name=f"{NAMES[code]} ({code})" if code in NAMES else code,
                code=code,
                rate=value,
                rate_date=day.get("time", ""),
                url=CURRENCY_PAGE.format(code=code.lower()),
                scraped_at=now,
            )
        )
    return rates


def main() -> int:
    ap = argparse.ArgumentParser(description="Collect the ECB's euro reference rates.")
    ap.add_argument("--out", default="rates.csv", help="output CSV path")
    ap.add_argument("--date", default="", metavar="YYYY-MM-DD",
                    help="an earlier working day from the last 90 days, instead of the latest")
    ap.add_argument("--only", nargs="*", default=[], metavar="CODE",
                    help="keep only these currency codes, e.g. USD GBP JPY")
    ap.add_argument("--sheet", default="", help="Google Sheet ID or URL to also write to")
    ap.add_argument("--creds",
                    default=os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", DEFAULT_CREDS),
                    help="OAuth client file or service-account key for --sheet")
    args = ap.parse_args()

    session = requests.Session()
    session.headers.update(POLITE_HEADERS)
    session.headers["Accept"] = "application/xml,text/xml"

    url = HISTORY_URL if args.date else DAILY_URL
    print(f"fetching {url}")
    try:
        scraped = parse_rates(fetch(url, session), args.date)
    except ET.ParseError as exc:
        print(f"\nThe file is not valid XML ({exc}). The feed probably changed.", file=sys.stderr)
        return 1
    if not scraped:
        print(f"\nNo rates published for {args.date}. It must be a working day in the last 90 days."
              if args.date else "\nNo rates parsed. The feed layout probably changed.", file=sys.stderr)
        return 1

    wanted = {code.upper() for code in args.only}
    rates = [r for r in scraped if not wanted or r.code in wanted]
    print(f"{len(scraped)} rates published for {scraped[0].rate_date}, {len(rates)} after filters")
    if not rates:
        print("Nothing left after filtering. Check the codes passed to --only.", file=sys.stderr)
        return 1

    out = Path(args.out)
    write_csv(rates, out)
    print(f"{len(rates)} rates -> {out.resolve()}")

    if args.sheet:
        try:
            spreadsheet = open_sheet(args.sheet, args.creds)
            write_sheet(rates, spreadsheet, prefix=SHEET_PREFIX)
        except Exception as exc:
            print(f"Google Sheets write failed: {exc}", file=sys.stderr)
            return 1
        print(f"{len(rates)} rates -> {spreadsheet.url} ({SHEET_PREFIX}latest, {SHEET_PREFIX}history)")

    print(f"\nOne euro buys (source: {SOURCE}):")
    for r in rates[:8]:
        print(f"  {r.name[:32]:<32}  {r.rate:>12,.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
