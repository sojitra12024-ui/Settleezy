import json

from settleezy_cofounder import igdiscovery as igd
from settleezy_cofounder import leadquery

PROFILES = {
    "kaffeekiez": {"username": "kaffeekiez", "name": "Kaffee Kiez", "followers_count": 5400, "media_count": 310, "website": "https://kaffeekiez.de",
                   "biography": "Specialty coffee & cake ☕ 📍 Weserstraße 12, 12047 Neukölln · Mo–Fr 8–18", "media": {"data": [{"like_count": 300, "comments_count": 20}] * 3}},
    "tofutales": {"username": "tofutales", "name": "Tofu Tales", "followers_count": 2100, "media_count": 90, "website": "https://tofutales.shop",
                  "biography": "Handmade vegan tofu snacks 🌱 made in Berlin · online shop + Versand", "media": {"data": []}},
    "berlinfoodgirl": {"username": "berlinfoodgirl", "name": "Mia | Berlin Foodie", "followers_count": 48000, "media_count": 900, "website": "",
                       "biography": "Food blogger 🍜 | collab & PR: 📩 mia@mail.de", "media": {"data": []}},
    "tiny": {"username": "tiny", "name": "Tiny Café", "followers_count": 80, "media_count": 3, "biography": "café", "media": {"data": []}},
}
CAPTION = {"berlinfood": [("Best flat white at @kaffeekiez with @berlinfoodgirl 🤤", 900, 40), ("@kaffeekiez again!", 500, 10),
                          ("Snack time @tofutales @tiny @settleezy", 300, 5)],
           "berlinvegan": [("@tofutales is the best", 200, 4)]}


class FakeIG:
    user_id = "me"

    def __init__(self):
        self.calls = []

    def get(self, path, **params):
        self.calls.append((path, params))
        if path == "ig_hashtag_search":
            return {"data": [{"id": "h_" + params["q"]}]}
        if path.endswith("/top_media"):
            tag = path.split("/")[0][2:]
            return {"data": [{"caption": c, "like_count": l, "comments_count": k, "permalink": f"https://instagram.com/p/{tag}{i}"}
                             for i, (c, l, k) in enumerate(CAPTION.get(tag, []))]}
        if path == "me" and "business_discovery" in params.get("fields", ""):
            user = params["fields"].split("username(")[1].split(")")[0]
            if user not in PROFILES:
                raise RuntimeError("Invalid user id")
            return {"business_discovery": PROFILES[user]}
        raise AssertionError(path)


def test_classify_and_bio_address():
    assert igd.classify(PROFILES["kaffeekiez"]) == ("merchant", "coffee")
    assert igd.classify(PROFILES["tofutales"])[0] == "brand"
    assert igd.classify(PROFILES["berlinfoodgirl"]) == ("creator", "content creator")
    assert igd.address_from_bio(PROFILES["kaffeekiez"]["biography"]) == ("Weserstraße 12, 12047 Berlin", "12047")
    assert igd.engagement(PROFILES["kaffeekiez"]) == round(100 * 340 / 5400, 2)


def test_discover_turns_mentions_into_leads(cfg, db):
    db.kv_set("instagram:username", "settleezy")
    ig = FakeIG()
    rep = igd.discover(cfg, db, ["berlinfood", "#berlinvegan"], ig=ig, pause=0)
    assert rep["new"] == 3 and rep["venues"] == 1 and rep["brands"] == 1 and rep["creators"] == 1 and rep["skipped"] == 1  # tiny < 300
    assert not any("settleezy" in str(c) for c in ig.calls if "business_discovery" in str(c))          # never looks itself up
    kk = dict(db.one("SELECT * FROM leads WHERE instagram='@kaffeekiez'"))
    assert kk["kind"] == "merchant" and kk["ig_followers"] == 5400 and kk["postcode"] == "12047" and kk["district"] == "Neukölln"
    assert "#berlinfood" in kk["notes"] and json.loads(kk["sources"]) == ["instagram:#berlinfood"]
    assert json.loads(kk["attributes"])["instagram_engagement_pct"] == 6.3
    assert igd.hashtags_left(db) == 28
    # a second run doesn't look up known accounts again or spend new budget on the same tags
    ig2 = FakeIG()
    rep2 = igd.discover(cfg, db, ["berlinfood"], ig=ig2, pause=0)
    assert rep2["new"] == 0 and not [c for c in ig2.calls if "business_discovery" in str(c)] and igd.hashtags_left(db) == 28
    # the finder: Instagram-only venues, creators only when asked for
    names = [p["name"] for p in leadquery.find(cfg, db, "venues only on Instagram")["results"]]
    assert "Kaffee Kiez" in names and "Mia | Berlin Foodie" not in names
    assert [p["name"] for p in leadquery.find(cfg, db, "food creators with over 10k followers")["results"]] == ["Mia | Berlin Foodie"]
    assert [p["name"] for p in leadquery.find(cfg, db, "brands with own products")["results"]] == ["Tofu Tales"]


def test_hashtag_budget_is_respected(cfg, db):
    used = {f"tag{i}": "2099-01-01T00:00:00+00:00" for i in range(30)}
    db.kv_set("igdisc:hashtags_used", json.dumps(used))
    rep = igd.discover(cfg, db, ["berlinfood"], ig=FakeIG(), pause=0)
    assert rep["hashtags"]["berlinfood"].startswith("skipped") and rep["new"] == 0
    assert igd.pick_hashtags(cfg, db, 5) == []


def test_add_handles_from_links(cfg, db):
    out = igd.add_handles(cfg, db, ["https://www.instagram.com/tofutales/?hl=de", "@nobody"], ig=FakeIG())
    assert out[0]["kind"] == "brand" and out[0]["followers"] == 2100 and "error" in out[1]
