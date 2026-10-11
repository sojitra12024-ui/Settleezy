import json

import pytest

from settleezy_cofounder import leadintel, leadquery
from settleezy_cofounder.leads import set_status, upsert

IMPRESSUM = """<html><body><h1>Impressum</h1>
<p>Angaben gemäß § 5 TMG</p><p>Bohnenwerk Kaffee UG (haftungsbeschränkt)<br>Oranienstraße 12<br>10997 Berlin</p>
<p>Vertreten durch: Lena Schmidt</p><p>Kontakt<br>Telefon: +49 30 1234 5678<br>E-Mail: hallo [at] bohnenwerk [dot] de</p>
<p><a href="https://www.instagram.com/bohnenwerk.berlin/">Instagram</a></p></body></html>"""
HOME = """<html><body><a href="/impressum">Impressum</a><a href="/karte">Speisekarte</a><p>Flat White 3,40 €</p></body></html>"""
MENU = "<html><body>Espresso 2,20 € Cappuccino 3,50 € Kuchen 4,20 € Bagel 6,90 € Chai 3,90 € Brunch 11,50 €</body></html>"


class FakeFetcher:
    def __init__(self, pages):
        self.pages, self.seen = pages, []

    def get(self, url, conditional=True):
        self.seen.append(url)
        page = type("P", (), {})()
        page.url, page.status, page.text = url, (200 if url in self.pages else 404), self.pages.get(url, "")
        return page


def test_parse_impressum_gets_owner_legal_name_address_phone_email():
    im = leadintel.parse_impressum(IMPRESSUM)
    assert im["owner"] == "Lena Schmidt"
    assert im["legal_name"].startswith("Bohnenwerk Kaffee UG")
    assert im["address"] == "Oranienstraße 12, 10997 Berlin" and im["postcode"] == "10997"
    assert im["phone"] == "+49 30 1234 5678" and im["email"] == "hallo@bohnenwerk.de"
    assert im["instagram"] == "@bohnenwerk.berlin"


def test_price_levels():
    assert leadintel.prices_from_text("Espresso 2,20 € · € 3.50 · 4 EUR · 2026") == [2.2, 3.5, 4.0]
    assert leadintel.price_level_from_prices([2.2, 3.5, 4.2, 6.9, 3.9], "cafe") == 2
    assert leadintel.price_level_from_prices([22, 28, 31, 26], "restaurant") == 3
    assert leadintel.price_level_from_prices([3, 4], "cafe") is None            # too few prices to judge
    assert leadintel.price_prior("späti convenience") == 1 and leadintel.price_label(3) == "€€€"


def test_enrich_lead_fills_contacts_and_price(cfg, db):
    lid, _ = upsert(db, "Bohnenwerk", "merchant", category="cafe", website="https://bohnenwerk.de")
    f = FakeFetcher({"https://bohnenwerk.de": HOME, "https://bohnenwerk.de/impressum": IMPRESSUM, "https://bohnenwerk.de/karte": MENU})
    res = leadintel.enrich_lead(db, f, lid)
    lead = dict(db.one("SELECT * FROM leads WHERE id=?", (lid,)))
    assert lead["email"] == "hallo@bohnenwerk.de" and lead["phone"] == "+49 30 1234 5678" and lead["owner"] == "Lena Schmidt"
    assert lead["address"] == "Oranienstraße 12, 10997 Berlin" and lead["postcode"] == "10997" and lead["instagram"] == "@bohnenwerk.berlin"
    assert lead["price_level"] == 2 and lead["price_source"] == "menu" and lead["enriched_at"]
    assert res["prices_seen"] >= 6
    assert leadintel.enrich_batch(db, f)["enriched"] == 0                       # enriched recently: skipped


def test_profile_platforms_history_and_likelihood(cfg, db):
    lid, _ = upsert(db, "Pho Viet", "merchant", source="groupon", category="restaurant vietnamese", email="hi@phoviet.de")
    upsert(db, "Pho Viet", "merchant", source="osm:HU Berlin (Mitte)")
    db.x("UPDATE leads SET lat=52.52, lon=13.395, instagram='@phoviet' WHERE id=?", (lid,))
    p = leadintel.profile(db, dict(db.one("SELECT * FROM leads WHERE id=?", (lid,))))
    assert p["platforms"] == ["Groupon", "OpenStreetMap", "Instagram"] and p["on_competitors"] == ["Groupon"]
    assert p["district"] == "Mitte" and p["price"] == "€€" and p["price_source"] == "estimated"
    assert not p["contacted_before"] and 0 < p["join_likelihood"] < 0.5
    db.x("INSERT INTO messages(id,conversation_id,folder,from_addr,to_addrs,subject,sent_at,automated) VALUES('m1','c','sent','me@settleezy.de',?,"
         "'Partnership','2026-09-01T10:00:00+00:00',0)", (json.dumps(["owner@phoviet.de"]),))
    p2 = leadintel.profile(db, dict(db.one("SELECT * FROM leads WHERE id=?", (lid,))))
    assert p2["contacted_before"] and p2["last_contact"] == "2026-09-01"          # found by domain, different address
    set_status(db, lid, "meeting")
    assert leadintel.join_likelihood(db, dict(db.one("SELECT * FROM leads WHERE id=?", (lid,)))) == 0.5


def _seed(db):
    rows = [("Café Grün", "cafe", "vegan", 52.5185, 13.3940, "HU Berlin (Mitte)", 150, "a@gruen.de", "", "osm:HU"),
            ("Kaffee Kiez", "cafe", "", 52.5190, 13.3960, "HU Berlin (Mitte)", 300, "", "030 1", "groupon"),
            ("Sushi Bar Ko", "restaurant", "sushi", 52.4990, 13.4030, "", None, "s@ko.de", "", "osm:x"),
            ("Späti Ecke", "späti convenience", "", 52.4810, 13.4350, "", None, "", "0176 2", "osm:x"),
            ("Insta Bakery", "bakery", "", None, None, "", None, "", "", "instagram:#berlinbakery")]
    ids = {}
    for name, cat, sub, lat, lon, campus, dist, email, phone, src in rows:
        lid, _ = upsert(db, name, "merchant", source=src, category=cat, email=email, phone=phone)
        db.x("UPDATE leads SET subcategory=?, lat=?, lon=?, campus=?, distance_m=? WHERE id=?", (sub, lat, lon, campus or None, dist, lid))
        ids[name] = lid
    db.x("UPDATE leads SET instagram='@instabakery', ig_followers=8200 WHERE id=?", (ids["Insta Bakery"],))
    return ids


@pytest.mark.parametrize("question,expected", [
    ("vegan cafés near HU with email, not contacted", ["Café Grün"]),
    ("cafés near HU", ["Café Grün", "Kaffee Kiez"]),
    ("cafés listed on Groupon", ["Kaffee Kiez"]),
    ("cafés near HU not on any competitor", ["Café Grün"]),
    ("sushi in Kreuzberg", ["Sushi Bar Ko"]),
    ("Spätis in Neukölln mit Telefonnummer", ["Späti Ecke"]),
    ("cheap places with phone", ["Späti Ecke"]),
    ("popular venues only on Instagram", ["Insta Bakery"]),
    ("cafés within 200m of HU", ["Café Grün"]),
])
def test_finder(cfg, db, question, expected):
    _seed(db)
    res = leadquery.find(cfg, db, question)
    assert sorted(p["name"] for p in res["results"]) == sorted(expected), res["understood"]


def test_finder_hides_partners_and_contacted_and_exports(cfg, db):
    ids = _seed(db)
    set_status(db, ids["Kaffee Kiez"], "partner")
    assert [p["name"] for p in leadquery.find(cfg, db, "cafés near HU")["results"]] == ["Café Grün"]
    set_status(db, ids["Café Grün"], "contacted")
    assert leadquery.find(cfg, db, "cafés near HU not contacted")["results"] == []
    res = leadquery.find(cfg, db, "cafés near HU, include partners")
    csv = leadquery.to_csv(res["results"])
    assert csv.splitlines()[0].startswith("name,category,subcategory,address") and "Kaffee Kiez" in csv
    assert "I found 2 leads" in leadquery.spoken(res)


def test_finder_discovers_on_osm_when_few_results(cfg, db):
    from tests.test_pipeline_planner import OVERPASS

    res = leadquery.find(cfg, db, "cafés near TU", discover=True, fetch=lambda q: OVERPASS)
    assert [p["name"] for p in res["results"]] == ["Café Campus"] and res["discovered"]["new"] == 2
    p = res["results"][0]
    assert p["district"] == "Charlottenburg" and p["instagram"] == "cafecampus" and p["platforms"] == ["OpenStreetMap", "Instagram"]


def test_finder_api_and_csv(cfg, db):
    from fastapi.testclient import TestClient

    from settleezy_cofounder.dashboard.app import app

    _seed(db)
    c = TestClient(app)
    r = c.get("/api/leads/find", params={"q": "cafés near HU"}).json()
    assert r["total"] == 2 and r["question"] == "cafés near HU" and "campus: HU Berlin (Mitte)" in r["understood"]
    csv = c.get("/api/leads/find.csv", params={"q": "cafés near HU"})
    assert csv.headers["content-type"].startswith("text/csv") and "Café Grün" in csv.text
    assert c.post("/api/leads/enrich", json={"ids": [1]}).status_code == 403
