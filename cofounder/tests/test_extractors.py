from settleezy_cofounder.scraping.extractors import (
    canonical,
    contacts_from_html,
    impressum_link,
    jsonld_items,
    link_items,
    parse_sitemap,
    title_from_url,
)


def test_parse_sitemap_index_and_urls():
    index = """<?xml version="1.0"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <sitemap><loc>https://x.de/sitemap-deals-1.xml</loc></sitemap></sitemapindex>"""
    urls, children = parse_sitemap(index)
    assert urls == [] and children == ["https://x.de/sitemap-deals-1.xml"]
    urlset = """<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://x.de/deals/cafe-berlin</loc><lastmod>2026-10-01</lastmod></url>
      <url><loc>https://x.de/about</loc></url></urlset>"""
    urls, _ = parse_sitemap(urlset)
    assert urls[0] == {"url": "https://x.de/deals/cafe-berlin", "lastmod": "2026-10-01"}
    assert len(urls) == 2


def test_jsonld_offer_and_business():
    html = """<script type="application/ld+json">{"@context":"https://schema.org","@type":"Product","name":"2 Burger + Drinks",
      "url":"/deals/burger-bros-berlin","offers":{"@type":"Offer","price":"19.90","seller":{"name":"Burger Bros"}}}</script>
      <script type="application/ld+json">{"@type":"Restaurant","name":"Pho Saigon","address":{"addressLocality":"Berlin"}}</script>"""
    items = jsonld_items(html, "https://www.groupon.de/local/berlin")
    assert items[0]["merchant"] == "Burger Bros" and items[0]["price"] == "19.90"
    assert items[0]["url"] == "https://www.groupon.de/deals/burger-bros-berlin"
    assert items[1]["merchant"] == "Pho Saigon" and items[1]["city"] == "Berlin" and items[1]["category"] == "Restaurant"


def test_link_items_pattern_and_dedupe():
    html = '<a href="/deals/yoga-mitte?utm=1">Yoga Mitte</a><a href="/deals/yoga-mitte">dup</a><a href="/deals/gl-tv">TV</a><a href="/about">x</a>'
    items = link_items(html, "https://www.groupon.de/", r"groupon\.de/deals/[a-z0-9-]+", r"deals/gl-")
    assert items == [{"url": "https://www.groupon.de/deals/yoga-mitte", "title": "Yoga Mitte"}]


def test_contacts_from_impressum():
    html = """<a href="https://instagram.com/cafe_kranz">IG</a><p>Kontakt: info (at) cafe-kranz (dot) de,
      Tel. +49 30 12345678</p><a href="/impressum">Impressum</a>"""
    c = contacts_from_html(html)
    assert c["email"] == "info@cafe-kranz.de"
    assert c["instagram"] == "@cafe_kranz"
    assert c["phone"].startswith("+49 30")
    assert impressum_link(html, "https://cafe-kranz.de/") == "https://cafe-kranz.de/impressum"


def test_helpers():
    assert canonical("https://WWW.x.de/a/b/?q=1#f") == "https://www.x.de/a/b"
    assert title_from_url("https://x.de/deals/liquidrom-berlin-3") == "Liquidrom Berlin 3"
