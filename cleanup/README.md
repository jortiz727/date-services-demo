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

## What it fixes

| Column type | Example in | Out |
|---|---|---|
| `name` | `  DAVID   O'BRIEN `, `Chen, Wei` | `David O'Brien`, `Wei Chen` |
| `email` | `mailto:Sarah.Lee@Example.com;` | `sarah.lee@example.com` |
| `phone` | `+1 (555) 010-5555`, `555.010.1234 x14` | `(555) 010-5555`, `(555) 010-1234 x14` |
| `date` | `3/14/24`, `Jan 5, 2025`, `April 2nd 2025` | `2024-03-14`, `2025-01-05`, `2025-04-02` |
| `money` | `$1,250.00`, `2.4k`, `(45.00)`, `USD 560` | `1250.00`, `2400.00`, `-45.00`, `560.00` |
| `state` | `California`, `ny` | `CA`, `NY` |
| `text` | extra spaces, line breaks inside a cell | single spaces |

It also finds the header under title rows, renames headers to the names in the
config, blanks placeholders such as `N/A` and `TBD`, and removes blank rows,
total rows and duplicates. When rows share a `dedupe_on` value, it keeps the
one with the most cells filled in.

Values it cannot fix (`priya.patel@example`, `2025-02-30`, `Calif.`) are left
as they are and listed under Needs review. It never guesses.

## Adapting it to another file

Copy `customers.config.json` and edit it:

- `columns`: the clean column name, its `type`, and any `aliases` the header
  might be written as. Columns not listed are kept and treated as `text`.
- `required`: columns that must not be blank.
- `dedupe_on`: columns that identify a duplicate.
- Add `"day_first": true` to a date column for `14/03/2024`-style dates. The
  default reads `03/04/2025` as March 4.

## Limits

- Phone numbers and states are US formats.
- European number formats (`1.200,50`) are not handled.
- Only the first sheet of a workbook is read, and formulas are read as their
  last calculated values.

`python test_clean.py` runs the offline tests.
