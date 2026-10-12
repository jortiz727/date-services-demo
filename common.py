"""
Code shared by every scraper: fetching, pagination, filters, CSV and Google Sheets output.
"""
from __future__ import annotations

import csv
import json
import random
import sys
import time
from dataclasses import asdict, fields
from pathlib import Path
from typing import Callable

import requests

# Says what it is, for sites that ask scrapers to identify themselves.
POLITE_HEADERS = {
    "User-Agent": "portfolio-demo-scraper/1.0 (python-requests; educational demo)",
    "Accept": "text/html,application/xhtml+xml",
}

DEFAULT_CREDS = "oauth_client.json"
TOKEN_FILE = "sheets_token.json"
SHEETS_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
LATEST_TAB = "latest"
HISTORY_TAB = "history"



def money(text: str | None) -> float | None:
    if not text:
        return None
    try:
        return float(str(text).replace(",", "").replace("$", "").strip())
    except (TypeError, ValueError):
        return None


def fetch(url: str, session: requests.Session, retries: int = 3) -> str:
    """GET with backoff. Raises on final failure."""
    last = None
    for attempt in range(1, retries + 1):
        try:
            resp = session.get(url, timeout=30)
            if resp.status_code == 200:
                # Without a declared charset, requests assumes Latin-1 and garbles "£".
                if "charset" not in resp.headers.get("content-type", "").lower():
                    resp.encoding = resp.apparent_encoding
                return resp.text
            last = f"HTTP {resp.status_code}"
            if resp.status_code in (403, 429) or resp.status_code >= 500:
                sleep = (2 ** attempt) + random.uniform(0, 1)
                print(f"  {last}, retrying in {sleep:.1f}s", file=sys.stderr)
                time.sleep(sleep)
                continue
            resp.raise_for_status()
        except requests.RequestException as exc:
            last = f"{type(exc).__name__}: {exc}"
            if attempt < retries:
                sleep = (2 ** attempt) + random.uniform(0, 1)
                print(f"  {last}, retrying in {sleep:.1f}s", file=sys.stderr)
                time.sleep(sleep)
    raise RuntimeError(f"giving up on {url} after {retries} tries ({last})")


def crawl(
    start_url: str,
    get: Callable[[str], str],
    parse: Callable[[str, str, int], tuple[list, str | None]],
    *,
    max_pages: int = 50,
    delay: float = 2.0,
    log: Callable[[str], None] = print,
) -> list:
    """Follow next-page links from `start_url`, collecting each row once (by its `url`).

    `parse(html, page_url, page_number)` returns that page's rows and the next
    page's absolute URL, or None on the last page.
    """
    seen: dict[str, object] = {}
    visited: set[str] = set()
    url: str | None = start_url
    page = 0
    while url and url not in visited and page < max_pages:
        visited.add(url)
        page += 1
        log(f"fetching {url}")
        found, nxt = parse(get(url), url, page)
        new = [row for row in found if row.url not in seen]
        log(f"  page {page}: {len(found)} rows, {len(new)} new")
        # Some sites ignore ?page= and serve page 1 forever; stop instead of looping.
        if not new:
            break
        seen.update((row.url, row) for row in new)
        url = nxt
        if url:
            time.sleep(delay)
    if url and page >= max_pages:
        log(f"  stopped at --max-pages {max_pages}; there are more pages")
    return list(seen.values())


def apply_filters(
    products: list,
    *,
    brand: str = "",
    exclude: tuple[str, ...] = (),
    category: str = "",
    min_price: float | None = None,
    max_price: float | None = None,
    on_sale: bool = False,
) -> list:
    """Narrow the scraped rows. Every argument left at its default is a no-op."""
    words = [w.lower() for w in exclude if w]
    kept = []
    for p in products:
        if brand and getattr(p, "brand", "").lower() != brand.lower():
            continue
        if any(w in p.name.lower() for w in words):
            continue
        if category and category.lower() not in getattr(p, "category", "").lower():
            continue
        if (min_price is not None or max_price is not None) and p.price is None:
            continue
        if min_price is not None and p.price < min_price:
            continue
        if max_price is not None and p.price > max_price:
            continue
        if on_sale and not getattr(p, "discount_usd", None):
            continue
        kept.append(p)
    return kept


def write_csv(products: list, path: Path) -> None:
    """Write any list of same-typed dataclass rows; shared with the other scrapers."""
    cols = [f.name for f in fields(products[0])]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols)
        writer.writeheader()
        for p in products:
            writer.writerow(asdict(p))


def sheet_rows(products: list) -> tuple[list[str], list[list]]:
    """Header and data rows for Sheets, which rejects None."""
    cols = [f.name for f in fields(products[0])]
    rows = [["" if v is None else v for v in asdict(p).values()] for p in products]
    return cols, rows


def open_sheet(sheet: str, creds: str):
    """Open a spreadsheet by ID or URL.

    `creds` is either an OAuth desktop-client file (signs in as you through the
    browser on first run, then reuses a cached token) or a service-account key.
    """
    try:
        import gspread
    except ImportError:
        raise RuntimeError("--sheet needs gspread: pip install -r requirements.txt")
    if not Path(creds).is_file():
        raise RuntimeError(
            f"credentials file not found at {creds}. "
            "Pass --creds or set GOOGLE_APPLICATION_CREDENTIALS (see README)."
        )
    try:
        kind = json.loads(Path(creds).read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        raise RuntimeError(f"{creds} is not valid JSON")
    if kind.get("type") == "service_account":
        client = gspread.service_account(filename=creds, scopes=SHEETS_SCOPES)
    elif "installed" in kind:
        client = gspread.oauth(
            scopes=SHEETS_SCOPES,
            credentials_filename=creds,
            authorized_user_filename=str(Path(creds).with_name(TOKEN_FILE)),
        )
    else:
        raise RuntimeError(
            f"{creds} is neither a service-account key nor a Desktop OAuth client file"
        )
    try:
        return client.open_by_url(sheet) if sheet.startswith("http") else client.open_by_key(sheet)
    except gspread.SpreadsheetNotFound:
        raise RuntimeError(
            f"no spreadsheet found for {sheet!r}. Pass the sheet's URL, or the ID "
            "between /d/ and /edit in it, and check the signed-in account can open it."
        )


def _worksheet(spreadsheet, title: str, cols: int):
    for ws in spreadsheet.worksheets():
        if ws.title == title:
            return ws
    return spreadsheet.add_worksheet(title=title, rows=100, cols=cols)


def write_sheet(products: list, spreadsheet, prefix: str = "") -> None:
    """Overwrite the `latest` tab and append this run to the `history` tab.

    `prefix` keeps several scrapers apart in one spreadsheet (`books_latest`, ...).
    """
    header, rows = sheet_rows(products)

    latest = _worksheet(spreadsheet, prefix + LATEST_TAB, len(header))
    latest.clear()
    latest.update(values=[header] + rows, range_name="A1")

    history = _worksheet(spreadsheet, prefix + HISTORY_TAB, len(header))
    if not history.row_values(1):
        rows = [header] + rows
    history.append_rows(rows, value_input_option="RAW")
