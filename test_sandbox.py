"""Offline tests for the two sandbox scrapers, using fixtures shaped like the real markup."""
import unittest

from common import apply_filters, crawl
from scrape_books import BASE, category_url, parse_listing
from scrape_countries import SORTS, parse_countries

NOW = "2026-10-12T00:00:00+00:00"


def pod(href, title, price, rating="Three", stock="In stock"):
    return f"""
    <article class="product_pod">
      <p class="star-rating {rating}"><i class="icon-star"></i></p>
      <h3><a href="{href}" title="{title}">{title[:10]}...</a></h3>
      <div class="product_price">
        <p class="price_color">{price}</p>
        <p class="instock availability"><i class="icon-ok"></i> {stock} </p>
      </div>
    </article>"""


def catalogue(pods, pager):
    return f"""<html><body>
    <div class="side_categories"><ul><li><a href="catalogue/category/books_1/index.html">Books</a>
      <ul><li><a href="catalogue/category/books/travel_2/index.html"> Travel </a></li>
          <li><a href="catalogue/category/books/mystery_3/index.html"> Mystery </a></li></ul></li></ul></div>
    <section>{''.join(pods)}</section><ul class="pager">{pager}</ul></body></html>"""


HOME = catalogue(
    [pod("catalogue/a-light-in-the-attic_1000/index.html", "A Light in the Attic", "£51.77"),
     pod("catalogue/tipping-the-velvet_999/index.html", "Tipping the Velvet", "£53.74", "One"),
     pod("catalogue/sharp-objects_997/index.html", "Sharp Objects", "£47.82", "Four", "Out of stock")],
    '<li class="next"><a href="catalogue/page-2.html">next</a></li>',
)
PAGE_2 = catalogue(
    [pod("the-requiem-red_995/index.html", "The Requiem Red", "£22.65", "Five")],
    '<li class="previous"><a href="page-1.html">previous</a></li>',
)
PAGE_2_URL = BASE + "catalogue/page-2.html"


class BookTests(unittest.TestCase):
    def setUp(self):
        self.books, self.next_url = parse_listing(HOME, BASE, 1, now=NOW)

    def test_reads_full_title_price_rating_and_stock(self):
        first, _, third = self.books
        self.assertEqual((first.name, first.price, first.currency, first.rating, first.in_stock),
                         ("A Light in the Attic", 51.77, "GBP", 3, True))
        self.assertEqual((third.rating, third.in_stock), (4, False))

    def test_links_resolve_against_the_page_they_were_found_on(self):
        self.assertEqual(self.books[0].url, BASE + "catalogue/a-light-in-the-attic_1000/index.html")
        self.assertEqual(self.next_url, PAGE_2_URL)
        books, nxt = parse_listing(PAGE_2, PAGE_2_URL, 2, now=NOW)
        self.assertEqual(books[0].url, BASE + "catalogue/the-requiem-red_995/index.html")
        self.assertIsNone(nxt)

    def test_crawl_walks_every_page_once(self):
        pages = {BASE: HOME, PAGE_2_URL: PAGE_2}
        requested = []

        def get(url):
            requested.append(url)
            return pages[url]

        books = crawl(BASE, get, parse_listing, delay=0, log=lambda _: None)
        self.assertEqual(requested, [BASE, PAGE_2_URL])
        self.assertEqual([(b.name, b.page) for b in books][-1], ("The Requiem Red", 2))
        self.assertEqual(len(books), 4)

    def test_shared_price_filters_apply(self):
        kept = apply_filters(self.books, max_price=52, exclude=("velvet",))
        self.assertEqual([b.name for b in kept], ["A Light in the Attic", "Sharp Objects"])

    def test_category_lookup_ignores_case_and_the_parent_link(self):
        self.assertEqual(category_url(HOME, "travel"), BASE + "catalogue/category/books/travel_2/index.html")
        self.assertIsNone(category_url(HOME, "Books"))
        self.assertIsNone(category_url(HOME, "cookery"))


COUNTRIES = """<html><body>
<div class="col-md-4 country"><h3 class="country-name"><i class="flag-icon flag-icon-ad"></i> Andorra </h3>
  <div class="country-info"><strong>Capital:</strong> <span class="country-capital">Andorra la Vella</span><br>
  <strong>Population:</strong> <span class="country-population">84000</span><br>
  <strong>Area (km<sup>2</sup>):</strong> <span class="country-area">468.0</span><br></div></div>
<div class="col-md-4 country"><h3 class="country-name"><i class="flag-icon flag-icon-aq"></i> Antarctica </h3>
  <div class="country-info"><span class="country-capital">None</span>
  <span class="country-population">0</span><span class="country-area">1.4E7</span></div></div>
<div class="col-md-4 country"><h3 class="country-name"> Monaco </h3>
  <div class="country-info"><span class="country-capital">Monaco</span>
  <span class="country-population">32965</span><span class="country-area">1.95</span></div></div>
<div class="col-md-4 country"><h3 class="country-name"> Nowhere </h3>
  <div class="country-info"><span class="country-capital">X</span>
  <span class="country-population">n/a</span><span class="country-area">0.0</span></div></div>
</body></html>"""


class CountryTests(unittest.TestCase):
    def setUp(self):
        self.by_name = {c.name: c for c in parse_countries(COUNTRIES, now=NOW)}

    def test_text_becomes_typed_columns(self):
        c = self.by_name["Andorra"]
        self.assertEqual((c.capital, c.population, c.area_km2, c.density_per_km2),
                         ("Andorra la Vella", 84000, 468.0, 179.5))

    def test_missing_capital_and_scientific_notation(self):
        c = self.by_name["Antarctica"]
        self.assertEqual((c.capital, c.population, c.area_km2, c.density_per_km2), ("", 0, 14000000.0, 0.0))

    def test_bad_numbers_become_blank_instead_of_crashing(self):
        c = self.by_name["Nowhere"]
        self.assertEqual((c.population, c.area_km2, c.density_per_km2), (None, 0.0, None))

    def test_sorts_put_largest_first(self):
        rows = sorted(self.by_name.values(), key=SORTS["density"])
        self.assertEqual(rows[0].name, "Monaco")
        self.assertEqual(sorted(self.by_name, key=str.lower)[0], "Andorra")


if __name__ == "__main__":
    unittest.main(verbosity=2)
