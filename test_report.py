"""Offline tests for the change report."""
import csv
import tempfile
import unittest
from pathlib import Path

from report import diff, read_rows, run

COLS = ["sku", "name", "brand", "price", "url"]


def row(sku, name, price, brand="Breville"):
    return {"sku": sku, "name": name, "brand": brand, "price": price, "url": f"https://x.test/{sku}"}


DAY_1 = [row("A", "Bambino", 299.95), row("B", "Barista Express", 699.95), row("C", "Oracle", 1999.95)]
DAY_2 = [row("A", "Bambino", 249.95), row("B", "Barista Express", 749.95), row("D", "Dual Boiler", 1599.95)]


class DiffTests(unittest.TestCase):
    def test_classifies_each_kind_of_change(self):
        keyed = lambda rows: {r["url"]: {k: str(v) for k, v in r.items()} for r in rows}
        changes = diff(keyed(DAY_1), keyed(DAY_2), "2026-10-13")
        self.assertEqual([(c["change"], c["name"], c["diff"]) for c in changes], [
            ("drop", "Bambino", -50.0),
            ("rise", "Barista Express", 50.0),
            ("new", "Dual Boiler", None),
            ("removed", "Oracle", None),
        ])


class RunTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.data = self.dir / "data"

    def scrape(self, rows):
        path = self.dir / "current.csv"
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=COLS)
            writer.writeheader()
            writer.writerows(rows)
        return path

    def test_first_run_records_a_baseline_and_no_changes(self):
        text = run("books", self.scrape(DAY_1), self.data, "2026-10-12")
        self.assertIn("baseline of 3 products", text)
        self.assertEqual(len(read_rows(self.data / "books_latest.csv")[1]), 3)
        self.assertFalse((self.data / "books_changes.csv").exists())

    def test_second_run_reports_changes_and_appends_to_the_log(self):
        run("books", self.scrape(DAY_1), self.data, "2026-10-12")
        text = run("books", self.scrape(DAY_2), self.data, "2026-10-13")
        self.assertIn("1 price drops, 1 rises, 1 new, 1 no longer listed", text)
        self.assertIn("| [Bambino](https://x.test/A) | $299.95 | $249.95 | -50.00 |", text)
        run("books", self.scrape(DAY_1), self.data, "2026-10-14")
        with (self.data / "books_changes.csv").open(encoding="utf-8") as fh:
            log = list(csv.DictReader(fh))
        self.assertEqual(len(log), 8)
        self.assertEqual({r["date"] for r in log}, {"2026-10-13", "2026-10-14"})

    def test_unchanged_run_says_so(self):
        run("books", self.scrape(DAY_1), self.data, "2026-10-12")
        self.assertIn("No changes", run("books", self.scrape(DAY_1), self.data, "2026-10-13"))

    def test_refuses_a_scrape_that_lost_most_products(self):
        run("books", self.scrape(DAY_1), self.data, "2026-10-12")
        with self.assertRaises(RuntimeError):
            run("books", self.scrape(DAY_1[:1]), self.data, "2026-10-13")
        self.assertEqual(len(read_rows(self.data / "books_latest.csv")[1]), 3)
        run("books", self.scrape(DAY_1[:1]), self.data, "2026-10-13", force=True)
        self.assertEqual(len(read_rows(self.data / "books_latest.csv")[1]), 1)

    def test_empty_scrape_is_rejected(self):
        with self.assertRaises(RuntimeError):
            run("books", self.scrape([]), self.data, "2026-10-12")


if __name__ == "__main__":
    unittest.main(verbosity=2)
