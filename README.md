# Data services demos

Working demos of four small data jobs: a single-page scraper, a multi-page
scraper, a scheduled run with a change report, and a spreadsheet cleaner.

Both scrapers target sites built for scraping practice, which invite it:

| Demo | Site | Shape | The site's own words |
|---|---|---|---|
| `scrape_countries.py` | scrapethissite.com | One page, 250 rows | Its FAQ says to feel free to scrape it with software |
| `scrape_books.py` | books.toscrape.com | 50 pages, 1,000 rows | "We love being scraped!" |

## Run them

```
pip install -r requirements.txt

python scrape_countries.py --sort population      # one page -> countries.csv
python scrape_books.py                            # 50 pages -> books.csv
python scrape_books.py --category travel --max-price 20 --min-rating 4
python scrape_books.py --max-pages 3              # a quick sample
```

Both send a user agent that says what they are and wait 1.5 seconds between
requests, inside scrapethissite.com's stated limit of one request per second.

## Single-page scraper

`scrape_countries.py` makes one request and turns 250 blocks of text into typed
columns: name, capital, population, area, and a calculated density. Bad or
missing numbers become blanks instead of crashing the run.

## Multi-page scraper

`scrape_books.py` starts at the first catalogue page and follows the "next"
link until there is none. `crawl()` in `common.py` does the walking for any
scraper: it resolves each link against the page it was found on, keeps each
item once, stops if a page adds nothing new (some sites ignore `?page=` and
serve page 1 forever), and stops at `--max-pages`.

Filters: `--category`, `--min-price`, `--max-price`, `--min-rating`,
`--in-stock`, and `--exclude WORD ...`.

## Google Sheets output

```
python scrape_books.py --sheet <spreadsheet-id-or-url>
```

Each run overwrites a `books_latest` tab and appends the same rows to a
`books_history` tab. The CSV is still written. One-time setup:

1. In Google Cloud, create a project and enable the Google Sheets API.
2. Under Google Auth Platform, configure the consent screen, then create an
   OAuth client of type **Desktop app** and download its JSON.
3. Save it in this folder as `oauth_client.json` (or point `--creds` /
   `GOOGLE_APPLICATION_CREDENTIALS` at it).

The first run opens a browser to sign in and caches the result in
`sheets_token.json`. Both files are in `.gitignore`. A service-account key
also works: pass it with `--creds` and share the sheet with the account.

## Scheduled run and change report

`report.py` compares a fresh scrape with the previous one:

```
python scrape_books.py
python report.py --name books --current books.csv
```

| File | Contents |
|---|---|
| `data/NAME_latest.csv` | The most recent scrape; the next run's baseline |
| `data/NAME_changes.csv` | Running log: one row per price drop, rise, new or removed item |
| `data/NAME_report.md` | Readable summary of the latest run's changes |

If a scrape comes back with fewer than half the rows of the previous run, the
report refuses to overwrite the data, because a broken page is likelier than a
real change. Pass `--force` if the drop is real.

`.github/workflows/daily-scrape.yml` runs the scrapers and the report on
GitHub Actions and commits the updated `data/` files. It is set to run on
demand from the Actions tab; uncomment the `schedule` lines to run it daily.

books.toscrape.com assigns its prices at random and never changes them, so
its report always reads "no changes". The change detection itself is covered
by `test_report.py`.

## Spreadsheet cleanup

[`cleanup/`](cleanup/README.md) cleans a messy CSV or Excel export and writes a
workbook that logs every changed cell, every value it could not fix, and every
removed row. The sample data is made up.

## Before pointing a scraper at another site

Read the site's terms of use as well as its `robots.txt`. A page that
`robots.txt` allows can still be off limits under the terms, and many
commercial sites prohibit automated collection outright.

## Tests

```
python test_sandbox.py
python test_report.py
python cleanup/test_clean.py      # needs: pip install -r cleanup/requirements.txt
```

All tests run offline against fixtures.
