"""Offline tests for the spreadsheet cleaner."""
import json
import tempfile
import unittest
from pathlib import Path

from clean_sheet import (
    clean_date,
    clean_email,
    clean_money,
    clean_name,
    clean_phone,
    clean_state,
    clean_table,
    read_table,
    write_csv,
    write_workbook,
)

HERE = Path(__file__).parent
CONFIG = json.loads((HERE / "customers.config.json").read_text(encoding="utf-8"))


class CleanerTests(unittest.TestCase):
    def test_name(self):
        self.assertEqual(clean_name("  DAVID   O'BRIEN ", {})[0], "David O'Brien")
        self.assertEqual(clean_name("Chen, Wei", {})[0], "Wei Chen")
        self.assertEqual(clean_name("Smith, Jr.", {})[0], "Smith, Jr.")
        self.assertEqual(clean_name("Leslie McDonald", {})[0], "Leslie McDonald")

    def test_email(self):
        self.assertEqual(clean_email(" mailto:Sarah.Lee@Example.com; ", {}), ("sarah.lee@example.com", None))
        self.assertIsNotNone(clean_email("priya.patel@example", {})[1])

    def test_phone(self):
        for raw in ("555.010.1234", "+1 (555) 010-1234", "5550101234", "1-555-010-1234"):
            self.assertEqual(clean_phone(raw, {}), ("(555) 010-1234", None))
        self.assertEqual(clean_phone("555-010-2222 ext. 14", {})[0], "(555) 010-2222 x14")
        self.assertEqual(clean_phone("555-0106", {}), ("555-0106", "not a 10-digit phone number"))

    def test_date(self):
        for raw in ("03/14/2024", "3/14/24", "2024-03-14", "Mar 14, 2024", "March 14th 2024",
                    "14 Mar 2024", "03-14-2024", "2024-03-14 00:00:00"):
            self.assertEqual(clean_date(raw, {}), ("2024-03-14", None), raw)
        self.assertEqual(clean_date("14/03/2024", {"day_first": True})[0], "2024-03-14")
        self.assertIsNotNone(clean_date("2025-02-30", {})[1])

    def test_money(self):
        cases = {"$1,250.00": "1250.00", "1250": "1250.00", "2.4k": "2400.00",
                 "(45.00)": "-45.00", "USD 560": "560.00", "-$12.5": "-12.50"}
        for raw, want in cases.items():
            self.assertEqual(clean_money(raw, {}), (want, None), raw)
        self.assertIsNotNone(clean_money("twelve", {})[1])

    def test_state(self):
        self.assertEqual(clean_state("california", {})[0], "CA")
        self.assertEqual(clean_state("New  York", {})[0], "NY")
        self.assertEqual(clean_state("tx", {})[0], "TX")
        self.assertIsNotNone(clean_state("Calif.", {})[1])


class SampleFileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.table = read_table(HERE / "sample" / "messy_customers.csv")
        cls.result = clean_table(cls.table, CONFIG)
        cls.by_email = {row[1]: row for row in cls.result.rows}

    def test_header_is_found_below_title_rows_and_renamed(self):
        self.assertEqual(self.result.header, list(CONFIG["columns"]))
        reasons = [r["reason"] for r in self.result.removed]
        self.assertEqual(reasons.count("title row above the header"), 2)

    def test_row_counts_add_up(self):
        r = self.result
        self.assertEqual(len(r.rows), 12)
        self.assertEqual(r.rows_in, len(r.rows) + len(r.removed) - 2)  # 2 title rows sit above the header

    def test_cleans_a_typical_row(self):
        self.assertEqual(self.by_email["wei.chen@example.com"], [
            "Wei Chen", "wei.chen@example.com", "(555) 010-2222 x14", "NY",
            "2025-01-05", "2026-09-12", "89.50", ""])

    def test_duplicates_keep_the_most_complete_row(self):
        self.assertEqual(self.by_email["maria.gonzalez@example.com"][7], "VIP")
        reasons = " / ".join(r["reason"] for r in self.result.removed)
        self.assertIn("same Email as row 5", reasons)
        self.assertIn("exact duplicate of row 12", reasons)

    def test_total_and_blank_rows_are_removed_not_cleaned(self):
        reasons = [r["reason"] for r in self.result.removed]
        self.assertEqual(reasons.count("total row"), 1)
        self.assertEqual(reasons.count("blank row"), 2)
        self.assertFalse(any("TOTAL" in row for row in self.result.rows))

    def test_unfixable_values_are_flagged_and_left_alone(self):
        problems = {(r["column"], r["problem"]) for r in self.result.review}
        self.assertEqual(problems, {
            ("Signup Date", "not a recognizable date"),
            ("Email", "not a valid email address"),
            ("Email", "required value is missing"),
            ("Phone", "not a 10-digit phone number"),
            ("State", "not a US state"),
        })
        self.assertEqual(self.by_email["david.obrien@example.com"][4], "2025-02-30")

    def test_placeholders_become_blank_and_multiline_notes_are_joined(self):
        self.assertEqual(self.by_email["linh.nguyen@example.com"][5:7], ["", ""])
        self.assertEqual(self.by_email["omar.haddad@example.com"][7], "prefers phone calls")

    def test_every_change_is_logged_with_its_original_value(self):
        change = next(c for c in self.result.changes
                      if c["source_row"] == 5 and c["column"] == "Total Spent")
        self.assertEqual((change["before"], change["after"]), ("$1,250.00", "1250.00"))

    def test_outputs_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_csv(self.result, Path(tmp) / "out.csv")
            write_workbook(self.result, Path(tmp) / "out.xlsx")
            self.assertEqual(read_table(Path(tmp) / "out.csv")[1:], self.result.rows)
            # An .xlsx input goes through the same pipeline and is already clean.
            again = clean_table(read_table(Path(tmp) / "out.xlsx"), CONFIG)
            self.assertEqual(len(again.rows), len(self.result.rows))
            self.assertEqual(again.removed, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
