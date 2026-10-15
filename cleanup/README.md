# Spreadsheet cleanup

Cleans a messy CSV or Excel export and logs every change, so the result can be
checked instead of trusted.

## Run it

```
pip install -r requirements.txt
python clean_sheet.py sample/messy_customers.csv --config customers.config.json
```

Writes `sample/messy_customers_clean.csv` and `sample/messy_customers_clean.xlsx`
next to the input (or into `--out-dir`). The workbook has four tabs:

| Tab | Contents |
|---|---|
| Clean | The cleaned data |
| Changes | Every altered cell: source row, column, before, after, rule |
| Needs review | Values it could not fix, with the reason |
| Removed rows | Title, blank, total and duplicate rows, with their original content |

Row numbers in those tabs are the row numbers of the original file.

### Workbooks with more than one sheet

By default the first sheet is cleaned. If the workbook has other sheets, the
run says so and names them (hidden sheets are marked `(hidden)`), so nothing
is skipped without you knowing.

```
python clean_sheet.py export.xlsx --config my.config.json --sheet "Customers"
python clean_sheet.py export.xlsx --config my.config.json --sheet all
```

- `--sheet NAME` cleans that sheet. The name is matched exactly first, then
  ignoring upper/lower case and spaces at either end. If no sheet matches (or
  two match only that loosely), it stops and lists the workbook's sheets.
  Output is named after the sheet: `export_Customers_clean.csv` / `.xlsx`.
- `--sheet all` cleans every sheet separately with the same config and writes
  one pair per sheet, `export_{sheet}_clean.csv` / `.xlsx` (characters that
  can't go in a file name become `_`). Sheets with no data rows are reported
  and skipped; it only fails if every sheet is empty. Sheets are never merged
  or de-duplicated against each other.
- For a sheet actually named `all`, put `=` in front: `--sheet =all`.
- `--sheet` with a CSV input is an error.

## What it fixes

| Column type | Example in | Out |
|---|---|---|
| `name` | `  DAVID   O'BRIEN `, `Chen, Wei` | `David O'Brien`, `Wei Chen` |
| `email` | `mailto:Sarah.Lee@Example.com;` | `sarah.lee@example.com` |
| `phone` | `+1 (555) 010-5555`, `555.010.1234 x14` | `(555) 010-5555`, `(555) 010-1234 x14` |
| `date` | `3/14/24`, `Jan 5, 2025`, `April 2nd 2025` | `2024-03-14`, `2025-01-05`, `2025-04-02` |
| `money` | `$1,250.00`, `2.4k`, `(45.00)`, `USD 560`, `€45` | `1250.00`, `2400.00`, `-45.00`, `560.00`, `45.00` |
| `money` with `decimal_comma` | `1.234,56`, `1.200`, `45,5 EUR` | `1234.56`, `1200.00`, `45.50` |
| `state` | `California`, `ny` | `CA`, `NY` |
| `sku` | ` FM-1003 `, `'00417`, `"012345679001"` | `FM-1003`, `00417`, `012345679001` |
| `integer` | ` 12 `, `1,200`, `12.0` | `12`, `1200`, `12` |
| `text` | extra spaces, line breaks inside a cell | single spaces |

It also finds the header under title rows, renames headers to the names in the
config, blanks placeholders such as `N/A` and `TBD`, and removes blank rows,
total rows and duplicates. When rows share a `dedupe_on` value, it keeps the
one with the most cells filled in.

Values it cannot fix (`priya.patel@example`, `2025-02-30`, `Calif.`) are left
as they are and listed under Needs review. It never guesses.

`sku` keeps letter case and leading zeros. It flags codes with characters
other than letters, digits, `-`, `_`, `.`, `/` and single spaces (`FM#1013`),
and codes Excel turned into scientific notation (`7.12E+11`), whose original
digits may be gone. `integer` flags anything that is not a plain whole number
(`12 pcs`, `about 40`, `2.5`), and flags negative counts (`-3`) instead of
converting them, since negative stock usually means an error in the source
system.

## Product lists

`products.config.json` is ready for store product lists: SKU, Title, Price,
Compare At Price, Quantity, Category, Vendor and Barcode, with common header
spellings (`Item #`, `Retail Price`, `MSRP`, `On Hand`, `UPC`, ...). SKU,
Title and Price are required, and rows sharing a SKU are deduplicated.
Titles are only whitespace-tidied, so `USB-C 2PK` keeps its capitals.

```
python clean_sheet.py sample/messy_products.csv --config products.config.json
```

```
19 rows in, 15 rows out, 5 removed, 28 cells changed, 7 values need review
```

## Adapting it to another file

Copy `customers.config.json` and edit it:

- `columns`: the clean column name, its `type`, and any `aliases` the header
  might be written as. Columns not listed are kept and treated as `text`.
- `required`: columns that must not be blank.
- `dedupe_on`: columns that identify a duplicate.
- Add `"day_first": true` to a date column for `14/03/2024`-style dates. The
  default reads `03/04/2025` as March 4.
- Add `"decimal_comma": true` to a money column for European amounts
  (`1.234,56`): dots are thousands separators and the comma is the decimal
  mark. The default reads US amounts, where a comma is accepted only as a
  thousands separator in groups of three (`1,234`, `1,234,567.89`).
- Add `"uppercase": true` to a `sku` column to turn `fm-1002` into `FM-1002`.
  By default letter case is left alone.

## Limits

- Phone numbers and states are US formats.
- One number format per money column. Amounts that do not fit the column's
  reading are not converted: with the default, `1,25`, `1.200,50` and
  `12,3456` go to Needs review as "number format unclear (comma decimal?)";
  with `decimal_comma`, `1,234.56` and `1.25` go to Needs review too. A file
  that mixes both formats in one column needs those rows checked by hand.
- `$`, `USD`, `€` and `EUR` are removed and the amount is kept; currencies are
  not converted or recorded.
- Leading zeros that Excel already dropped from a number cell (a UPC stored
  as a number) cannot be recovered; keep such columns as text in the export.
- Duplicate SKUs are matched ignoring case, so `fm-1002` and `FM-1002` count
  as the same product.
- One sheet is cleaned per output (the first, unless `--sheet` says
  otherwise). With `--sheet all` each sheet is cleaned on its own: rows are
  not combined across sheets and duplicates are only found within a sheet.
- Formulas are read as their last calculated values. Chart sheets are ignored.

`python test_clean.py` runs the offline tests.
