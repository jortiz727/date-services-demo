"""Offline tests for the exchange rate scraper, using a fixture shaped like the ECB's feed."""
import unittest
import xml.etree.ElementTree as ET

from scrape_rates import parse_rates

NOW = "2026-10-13T00:00:00+00:00"

FEED = """<?xml version="1.0" encoding="UTF-8"?>
<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01"
                 xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">
  <gesmes:subject>Reference rates</gesmes:subject>
  <gesmes:Sender><gesmes:name>European Central Bank</gesmes:name></gesmes:Sender>
  <Cube>
    <Cube time="2026-10-13">
      <Cube currency="USD" rate="1.1188"/>
      <Cube currency="JPY" rate="176.91"/>
      <Cube currency="GBP" rate="0.84460"/>
      <Cube currency="XXX" rate="2.5"/>
      <Cube currency="BAD" rate="n/a"/>
      <Cube currency="NIL" rate="0"/>
    </Cube>
    <Cube time="2026-10-12">
      <Cube currency="USD" rate="1.1181"/>
      <Cube currency="JPY" rate="177.33"/>
    </Cube>
  </Cube>
</gesmes:Envelope>"""


class RateTests(unittest.TestCase):
    def setUp(self):
        self.rates = parse_rates(FEED, now=NOW)
        self.by_code = {r.code: r for r in self.rates}

    def test_newest_day_is_read_when_no_date_is_given(self):
        self.assertEqual({r.rate_date for r in self.rates}, {"2026-10-13"})
        usd = self.by_code["USD"]
        self.assertEqual((usd.name, usd.rate, usd.scraped_at), ("US dollar (USD)", 1.1188, NOW))

    def test_each_row_links_to_the_page_for_its_currency(self):
        self.assertTrue(self.by_code["GBP"].url.endswith("/eurofxref-graph-gbp.en.html"))
        self.assertEqual(len({r.url for r in self.rates}), len(self.rates))

    def test_unknown_code_is_kept_and_named_by_its_code(self):
        self.assertEqual(self.by_code["XXX"].name, "XXX")

    def test_unreadable_and_zero_rates_are_skipped(self):
        self.assertEqual(sorted(self.by_code), ["GBP", "JPY", "USD", "XXX"])

    def test_an_earlier_day_can_be_picked_by_date(self):
        rates = parse_rates(FEED, date="2026-10-12", now=NOW)
        self.assertEqual([(r.code, r.rate, r.rate_date) for r in rates],
                         [("USD", 1.1181, "2026-10-12"), ("JPY", 177.33, "2026-10-12")])

    def test_a_day_with_no_rates_gives_nothing(self):
        self.assertEqual(parse_rates(FEED, date="2026-10-11", now=NOW), [])

    def test_a_page_that_is_not_xml_raises(self):
        with self.assertRaises(ET.ParseError):
            parse_rates("<html><body>Service unavailable</body>", now=NOW)


if __name__ == "__main__":
    unittest.main(verbosity=2)
