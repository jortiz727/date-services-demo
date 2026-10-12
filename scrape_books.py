#!/usr/bin/env python3
"""
Scrape every book from books.toscrape.com, following its pagination to the last page.

books.toscrape.com is a sandbox built for scraping practice ("We love being
scraped!"), so this is the multi-page demo that is safe to publish. Its prices
and ratings are randomly assigned and never change.

Why this works the way it does
------------------------------
The catalogue is server-rendered, 20 books per page across 50 pages. Each page
links to the next with a relative URL (`catalogue/page-2.html` from the home
page, `page-3.html` from inside the catalogue), so every link is resolved
against the page it was found on. The page declares no charset, which makes
the pound sign arrive garbled unless the encoding is detected; `fetch` does that.

Usage
-----
    python scrape_books.py                         # all 1000 books -> books.csv
    python scrape_books.py --category travel       # one category
    python scrape_books.py --max-price 20 --min-rating 4 --in-stock
    python scrape_books.py --max-pages 3           # a quick sample
    python scrape_books.py --sheet <id-or-url>     # also push to Google Sheets
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from common import (
    DEFAULT_CREDS,
    POLITE_HEADERS,
    apply_filters,
    crawl,
    fetch,
    open_sheet,
    write_csv,
    write_sheet,
)

BASE = "https://books.toscrape.com/"
RATINGS = {"One": 1, "Two": 2, "Three": 3, "Four": 4, "Five": 5}
PRICE_RE = re.compile(r"\d+(?:\.\d+)?")
SHEET_PREFIX = "books_"


@dataclass
class Book:
    name: str
    price: float | None
    currency: str
    rating: int | None
    in_stock: bool
    url: str
    page: int
    scraped_at: str


def parse_listing(html: str, page_url: str = BASE, page: int = 1,
                  now: str | None = None) -> tuple[list[Book], str | None]:
    """Books on one catalogue page, plus the next page's URL (None on the last)."""
    now = now or datetime.now(timezone.utc).isoformat(timespec="seconds")
    soup = BeautifulSoup(html, "html.parser")

    books: list[Book] = []
    for pod in soup.select("article.product_pod"):
        link = pod.select_one("h3 a[href]")
        if link is None:
            continue
        price_el = pod.select_one(".price_color")
        price = PRICE_RE.search(price_el.get_text()) if price_el else None
        stars = pod.select_one(".star-rating")
        rating = next((RATINGS[c] for c in (stars.get("class") or []) if c in RATINGS), None) if stars else None
        stock = pod.select_one(".availability")
        books.append(
            Book(
                # The visible title is truncated with "..."; the full one is in the title attribute.
                name=link.get("title") or link.get_text(strip=True),
                price=float(price.group()) if price else None,
                currency="GBP",
                rating=rating,
                in_stock=bool(stock and "in stock" in stock.get_text().lower()),
                url=urljoin(page_url, link["href"]),
                page=page,
                scraped_at=now,
            )
        )

    nxt = soup.select_one("li.next a[href]")
    return books, (urljoin(page_url, nxt["href"]) if nxt else None)


def category_url(html: str, name: str) -> str | None:
    """URL of the sidebar category called `name` (case-insensitive), from the home page."""
    for link in BeautifulSoup(html, "html.parser").select(".side_categories ul ul a[href]"):
        if link.get_text(strip=True).lower() == name.strip().lower():
            return urljoin(BASE, link["href"])
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="Scrape books.toscrape.com.")
    ap.add_argument("--category", default="", help="one category by name, e.g. travel")
    ap.add_argument("--out", default="books.csv", help="output CSV path")
    ap.add_argument("--max-pages", type=int, default=60, help="safety cap on pages followed")
    ap.add_argument("--delay", type=float, default=1.5, help="seconds between requests")
    ap.add_argument("--exclude", nargs="*", default=[], metavar="WORD",
                    help="drop books whose title contains any of these words")
    ap.add_argument("--min-price", type=float, help="keep books at or above this price")
    ap.add_argument("--max-price", type=float, help="keep books at or below this price")
    ap.add_argument("--min-rating", type=int, choices=range(1, 6), help="keep books rated at least this")
    ap.add_argument("--in-stock", action="store_true", help="keep only books in stock")
    ap.add_argument("--sheet", default="", help="Google Sheet ID or URL to also write to")
    ap.add_argument("--creds",
                    default=os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", DEFAULT_CREDS),
                    help="OAuth client file or service-account key for --sheet")
    args = ap.parse_args()

    session = requests.Session()
    session.headers.update(POLITE_HEADERS)

    start = BASE
    if args.category:
        start = category_url(fetch(BASE, session), args.category)
        if start is None:
            print(f"No category called {args.category!r} in the site's sidebar.", file=sys.stderr)
            return 1

    scraped = crawl(start, lambda url: fetch(url, session), parse_listing,
                    max_pages=args.max_pages, delay=args.delay)
    if not scraped:
        print("\nNo books parsed. The page layout probably changed.", file=sys.stderr)
        return 1

    books = apply_filters(scraped, exclude=tuple(args.exclude),
                          min_price=args.min_price, max_price=args.max_price)
    if args.min_rating:
        books = [b for b in books if (b.rating or 0) >= args.min_rating]
    if args.in_stock:
        books = [b for b in books if b.in_stock]
    print(f"\n{len(scraped)} books scraped, {len(books)} after filters")
    if not books:
        print("Nothing left after filtering. Loosen the filters.", file=sys.stderr)
        return 1

    out = Path(args.out)
    write_csv(books, out)
    print(f"{len(books)} books -> {out.resolve()}")

    if args.sheet:
        try:
            spreadsheet = open_sheet(args.sheet, args.creds)
            write_sheet(books, spreadsheet, prefix=SHEET_PREFIX)
        except Exception as exc:
            print(f"Google Sheets write failed: {exc}", file=sys.stderr)
            return 1
        print(f"{len(books)} books -> {spreadsheet.url} ({SHEET_PREFIX}latest, {SHEET_PREFIX}history)")

    prices = [b.price for b in books if b.price is not None]
    if prices:
        print(f"\nPrices £{min(prices):.2f} to £{max(prices):.2f}, average £{sum(prices) / len(prices):.2f}. "
              f"{sum(b.rating == 5 for b in books)} five-star, {sum(not b.in_stock for b in books)} out of stock.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
