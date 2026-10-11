"""Regression tests for the lead / import / scheduling review fixes."""

import json
from datetime import datetime, timedelta

import pytest

from settleezy_cofounder import igdiscovery as igd
from settleezy_cofounder import importer, leadgen, leadintel, leadquery, leads, ops, pipeline
from settleezy_cofounder import scheduling as sch
from settleezy_cofounder.instagram import RateLimited
from settleezy_cofounder.scraping import extractors as ex
from settleezy_cofounder.scraping import monitor


class Page:
    def __init__(self, url, status, text=""):
        self.url, self.status, self.text = url, status, text


# -- names, dedupe, partners -------------------------------------------------------------------------

def test_norm_name_idempotent_and_name_key_dedupe(db):
    assert leads._norm_name("Foo - GmbH") == "Foo"
    for n in ["Foo - GmbH", "Bar & Co. KG", " Baz e.K. | ", "Café Kranz UG"]:
        once = leads._norm_name(n)
        assert leads._norm_name(once) == once
    a, _ = leads.upsert(db, "Foo - GmbH", "merchant", website="foo.de")
    b, created = leads.upsert(db, db.one("SELECT name FROM leads WHERE id=?", (a,))["name"], "merchant", email="a@foo.de")
    assert (b, created) == (a, False)
    c, _ = leads.upsert(db, "Café Kranz", "merchant")
    d, created = leads.upsert(db, "CAFÉ KRANZ", "merchant")       # SQLite lower() is ASCII-only
    assert (d, created) == (c, False)
    assert db.one("SELECT name_key FROM leads WHERE id=?", (c,))["name_key"] == "café kranz"


def test_name_key_backfill(db):
    db.x("INSERT INTO leads(name,kind,status,sources,source_urls) VALUES('Old Shop -','merchant','new','[]','[]')")
    lid, created = leads.upsert(db, "OLD SHOP GmbH", "merchant")
    assert not created and db.one("SELECT name_key FROM leads WHERE id=?", (lid,))["name_key"] == "old shop"


def test_partner_with_legal_suffix_gets_no_cold_sequence(cfg, db):
    ops.add_partner(db, "Bäckerei Hansen GmbH", "venue")
    lid, _ = leads.upsert(db, "BÄCKEREI HANSEN", "merchant", email="x@hansen.de")
    with pytest.raises(ValueError):
        pipeline.start_sequence(cfg, db, lid)
    assert lid not in [s["lead_id"] for s in pipeline.auto_start(cfg, db)]
    assert ops.link_partners_to_leads(db) == 0           # already linked by auto_start
    assert db.one("SELECT status FROM leads WHERE id=?", (lid,))["status"] == "partner"


def test_enrich_updates_by_id_and_records_failures(db):
    good, _ = leads.upsert(db, "Foo GmbH", "merchant", website="foo.de")
    dead, _ = leads.upsert(db, "Dead Site", "merchant", website="dead.de")

    class F:
        calls = 0

        def get(self, url, **kw):
            F.calls += 1
            if "dead" in url:
                return Page(url, 500)
            return Page(url, 200, "<p>Mail: info@foo.de</p>")

    assert leads.enrich(db, F()) == 1
    assert db.one("SELECT COUNT(*) n FROM leads")["n"] == 2
    assert db.one("SELECT email FROM leads WHERE id=?", (good,))["email"] == "info@foo.de"
    assert db.one("SELECT enriched_at FROM leads WHERE id=?", (dead,))["enriched_at"]
    calls = F.calls
    leads.enrich(db, F())
    assert F.calls == calls                                # the dead site isn't retried right away


def test_leadintel_failure_sets_enriched_at(db):
    lid, _ = leads.upsert(db, "Gone", "merchant", website="gone.de")
    res = leadintel.enrich_lead(db, type("F", (), {"get": lambda self, u, **k: Page(u, 404)})(), lid)
    assert "error" in res and db.one("SELECT enriched_at FROM leads WHERE id=?", (lid,))["enriched_at"]


def test_score_counts_only_competitor_sources(db):
    a, _ = leads.upsert(db, "Plain Cafe", "merchant", category="cafe", source="osm:TU")
    b, _ = leads.upsert(db, "Insta Cafe", "merchant", category="cafe", source="instagram:#berlinfood")
    leads.upsert(db, "Insta Cafe", "merchant", source="instagram:#berlinvegan")
    c, _ = leads.upsert(db, "Deal Cafe", "merchant", category="cafe", source="groupon")
    assert leads.rescore(db, a) == leads.rescore(db, b) < leads.rescore(db, c)


# -- importer ------------------------------------------------------------------------------------------

def test_import_status_mapping_columns_and_names(db, tmp_path):
    assert importer._map_columns(["Name", "Contact email", "Status"]) == {"name": "Name", "email": "Contact email", "status": "Status"}
    assert importer._map_columns(["Partner type", "Name", "Email"]) == {"kind": "Partner type", "name": "Name", "email": "Email"}
    (tmp_path / "partners.csv").write_text(
        "Name,Contact email,Status\nCafé Eins,eins@cafe1.de,Not interested\nBar Zwei,zwei@bar2.de,Declined partnership\n"
        "Kiosk Drei,k@drei.de,Inactive\nShop Vier,v@vier.de,Contract sent\nLive Bar,l@live.de,Live\n", encoding="utf-8")
    rep = importer.import_table(db, tmp_path / "partners.csv")
    st = {r["name"]: r["status"] for r in db.q("SELECT name, status FROM leads")}
    assert st == {"Café Eins": "lost", "Bar Zwei": "lost", "Kiosk Drei": "new", "Shop Vier": "contacted", "Live Bar": "partner"}
    assert rep["partners"] == 1
    (tmp_path / "contacts.csv").write_text("Email,Phone\nanna@gmail.com,1\nbob.smith@gmail.com,2\n", encoding="utf-8")
    importer.import_table(db, tmp_path / "contacts.csv")
    assert not db.one("SELECT 1 FROM leads WHERE name='Gmail'")
    assert {r["name"] for r in db.q("SELECT name FROM leads WHERE email LIKE '%gmail.com'")} == {"Anna", "Bob Smith"}
    (tmp_path / "names.csv").write_text("name\nCafe A\nCafe B\n", encoding="utf-8")   # one column: Sniffer can't tell
    assert importer.import_table(db, tmp_path / "names.csv")["leads_new"] == 2


def test_import_never_moves_a_lead_back(db, tmp_path):
    lid, _ = leads.upsert(db, "Brew Lab", "merchant")
    leads.set_status(db, lid, "meeting")
    (tmp_path / "old.csv").write_text("Name,Status\nBrew Lab,contacted\n", encoding="utf-8")
    importer.import_table(db, tmp_path / "old.csv")
    assert db.one("SELECT status FROM leads WHERE id=?", (lid,))["status"] == "meeting"


# -- scheduling ------------------------------------------------------------------------------------------

def test_proposed_times_dates_and_do():
    sent = datetime(2026, 10, 7, 9, 0)   # Wednesday
    assert sch.proposed_times("Hätten Sie Dienstag, 14.10. um 10 Uhr Zeit?", sent) == [datetime(2026, 10, 14, 10, 0)]
    assert sch.proposed_times("Could we do 10:30 tomorrow?", sent) == []
    assert sch.proposed_times("Geht Do 10:30?", sent) == [datetime(2026, 10, 8, 10, 30)]
    assert sch.proposed_times("How about Tuesday 21st at 3pm?", sent) == []


def test_multi_day_event_blocks_every_day(cfg, db):
    start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=3)
    end = start + timedelta(days=5)
    db.x("INSERT INTO events(id,source,title,start,end) VALUES('v','outlook','Vacation',?,?)",
         (start.isoformat(timespec="minutes"), end.isoformat(timespec="minutes")))
    for i in range(5):
        day = start + timedelta(days=i)
        assert not sch.is_free(cfg, db, day.replace(hour=10), 30)
        assert sch._busy(db, day.date()) == [(day, day + timedelta(days=1))]
    assert sch._busy(db, end.date()) == []


def test_past_and_unsynced_proposals_are_not_accepted(cfg, db):
    sch._ensure(db)
    assert not sch.is_free(cfg, db, datetime(2020, 1, 6, 10, 0), 30)
    far = (datetime.now() + timedelta(days=20)).replace(hour=11, minute=0, second=0, microsecond=0)
    while far.weekday() >= 5:
        far += timedelta(days=1)
    db.kv_set("calendar:synced_until", (datetime.now() + timedelta(days=9)).strftime("%Y-%m-%dT%H:%M"))
    db.x("INSERT INTO meeting_requests(source,ref,who,email,subject,lang,received_at,proposed,status) "
         "VALUES('email','mail:x','Anna','a@b.de','call','en','2026-10-01',?, 'open')",
         (json.dumps(["2026-10-06T14:00", far.isoformat(timespec="minutes")]),))
    r = sch.requests(cfg, db)[0]
    assert r["proposed_free"] == [] and r["proposed_unknown"] == [far.isoformat(timespec="minutes")]
    assert "works for me" not in sch.reply_text(cfg, r, r["slots"])


def test_calendar_sync_covers_slot_window(cfg, db):
    from settleezy_cofounder.mail_sync import sync_calendar

    seen = {}

    class G:
        def calendar_view(self, start, end):
            seen["days"] = (end - start).days
            return []

    sync_calendar(cfg, db, G())
    assert seen["days"] >= sch.DAYS_AHEAD + 1 and db.kv_get("calendar:synced_until")


# -- lead query --------------------------------------------------------------------------------------------

def test_leadquery_parse_fixes():
    q = leadquery.parse("cafés with over 10000 followers")
    assert q.postcodes == [] and q.min_followers == 10000
    assert leadquery.parse("bars over 1.5k followers").min_followers == 1500
    assert leadquery.parse("restaurants that have not yet been contacted").contacted is False


def test_leadquery_campus_and_district_exact(cfg, db):
    a, _ = leads.upsert(db, "Mitte Cafe", "merchant", category="cafe")
    b, _ = leads.upsert(db, "Adlershof Cafe", "merchant", category="cafe")
    c, _ = leads.upsert(db, "Kreuzberg Cafe", "merchant", category="cafe")
    db.x("UPDATE leads SET campus='HU Berlin (Mitte)', district='Mitte' WHERE id=?", (a,))
    db.x("UPDATE leads SET campus='HU Berlin (Adlershof)', district='Adlershof' WHERE id=?", (b,))
    db.x("UPDATE leads SET district='Kreuzberg', address='Mittenwalder Str. 1' WHERE id=?", (c,))
    assert [p["name"] for p in leadquery.find(cfg, db, "cafes near HU")["results"]] == ["Mitte Cafe"]
    assert [p["name"] for p in leadquery.find(cfg, db, "cafes in Mitte")["results"]] == ["Mitte Cafe"]


# -- lead intel / Instagram ------------------------------------------------------------------------------

def test_contact_history_ignores_social_hosts_and_automated_mail(db):
    lid, _ = leads.upsert(db, "Imbiss Acht", "merchant", website="https://www.instagram.com/imbiss8/")
    db.x("INSERT INTO messages(id,conversation_id,folder,from_addr,to_addrs,subject,sent_at,automated) "
         "VALUES('m1','c1','inbox','no-reply@mail.instagram.com','[]','New follower','2026-10-01T10:00:00+00:00',1)")
    lead = dict(db.one("SELECT * FROM leads WHERE id=?", (lid,)))
    assert leadintel._domain(lead) == "" and not leadintel.contact_history(db, lead)["contacted"]
    l2, _ = leads.upsert(db, "Foo", "merchant", website="foo.de")
    db.x("INSERT INTO messages(id,conversation_id,folder,from_addr,to_addrs,subject,sent_at,automated) "
         "VALUES('m2','c2','inbox','x@notfoo.de','[]','hi','2026-10-01T10:00:00+00:00',0)")
    db.x("INSERT INTO messages(id,conversation_id,folder,from_addr,to_addrs,subject,sent_at,automated) "
         "VALUES('m3','c3','sent','me@settleezy.de','[\"anna@foo.de\"]','hi','2026-10-02T10:00:00+00:00',0)")
    h = leadintel.contact_history(db, dict(db.one("SELECT * FROM leads WHERE id=?", (l2,))))
    assert h["emails_sent"] == 1 and h["emails_received"] == 0


def test_ig_save_merges_attributes(db):
    lid, _ = leads.upsert(db, "Kaffeehaus Sieben", "merchant", category="cafe")
    db.x("UPDATE leads SET attributes=? WHERE id=?", (json.dumps({"vegan": "yes"}), lid))
    prof = {"username": "kaffeehaus7", "name": "Kaffeehaus Sieben", "biography": "Café in Kreuzberg", "followers_count": 1200,
            "media_count": 40, "media": {"data": []}}
    igd._save(db, prof, "instagram:#berlincafe")
    attrs = json.loads(db.one("SELECT attributes FROM leads WHERE id=?", (lid,))["attributes"])
    assert attrs == {"vegan": "yes", "instagram_posts": 40}


class LimitedIG:
    user_id = "me"

    def __init__(self):
        self.calls = 0

    def get(self, path, **params):
        self.calls += 1
        raise RateLimited("Instagram API: Application request limit reached")


def test_rate_limit_stops_instead_of_skipping(cfg, db):
    leads.upsert(db, "A", "merchant", instagram="@a")
    leads.upsert(db, "B", "merchant", instagram="@b")
    ig = LimitedIG()
    assert igd.refresh_profiles(cfg, db, ig=ig) == {"refreshed": 0, "checked": 0} and ig.calls == 1
    assert db.one("SELECT COUNT(*) n FROM leads WHERE ig_checked_at IS NOT NULL")["n"] == 0
    rep = igd.discover(cfg, db, ["berlinfood"], ig=LimitedIG(), pause=0)
    assert rep["rate_limited"] and json.loads(db.kv_get("igdisc:skipped", "{}") or "{}") == {}


def test_refresh_profiles_records_failed_attempts(cfg, db):
    lid, _ = leads.upsert(db, "Private", "merchant", instagram="@private")

    class IG:
        user_id = "me"
        calls = 0

        def get(self, path, **params):
            IG.calls += 1
            raise RuntimeError("Invalid user id")

    igd.refresh_profiles(cfg, db, ig=IG())
    igd.refresh_profiles(cfg, db, ig=IG())
    assert IG.calls == 1 and db.one("SELECT ig_checked_at FROM leads WHERE id=?", (lid,))["ig_checked_at"]


def test_instagram_get_wraps_errors(monkeypatch):
    import httpx

    from settleezy_cofounder.instagram import Instagram

    ig = Instagram.__new__(Instagram)
    ig.base, ig.token = "https://graph.example", "t"

    def answer(body, status=200):
        return httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(status, content=body)))

    ig.http = answer(b"<html>Bad gateway</html>", 502)
    with pytest.raises(RuntimeError):
        ig.get("me")
    ig.http = answer(json.dumps({"error": {"message": "slow down", "code": 4}}).encode())
    with pytest.raises(RateLimited):
        ig.get("me")


# -- scraping ------------------------------------------------------------------------------------------------

def test_jsonld_top_level_offers_and_list_names():
    html = ('<script type="application/ld+json">[{"@type":"Offer","name":"10% off","seller":{"name":"Cafe A"}},'
            '{"@type":"Offer","name":"2for1","seller":{"name":"Bar B"}}]</script>')
    items = ex.jsonld_items(html, "https://deals.example/berlin")
    assert [i["merchant"] for i in items] == ["Cafe A", "Bar B"] and len({i["url"] for i in items}) == 2

    class F:
        def get(self, url, **k):
            return Page(url, 200, html)

    assert len(monitor.collect({"strategies": ["jsonld"], "start_urls": ["https://deals.example/berlin"]}, F(), None)) == 2
    it = ex.jsonld_items('<script type="application/ld+json">{"@type":"Restaurant","name":["Cafe X","Café X"],"url":"/x"}</script>',
                         "https://d.example/")[0]
    assert it["title"] == it["merchant"] == "Cafe X" and monitor._merchant_from({}, it) == "Cafe X"
    assert ex.canonical("https://x.de/deal.php?id=1") != ex.canonical("https://x.de/deal.php?id=2")
    assert ex.canonical("https://x.de/deal.php?id=1&utm_source=nl") == "https://x.de/deal.php?id=1"


def test_iban_is_not_a_phone():
    c = ex.contacts_from_html("<p>Bank: IBAN DE89 3704 0044 0532 0130 00</p><p>Mail info@x.de</p>")
    assert c["phone"] == "" and c["email"] == "info@x.de"


def test_monitor_survives_a_broken_site(cfg, db, monkeypatch):
    monkeypatch.setattr(monitor, "load_sites", lambda cfg: [{"name": "bad"}, {"name": "good", "strategies": []}])
    real = monitor.collect

    def collect(site, f, llm):
        if site["name"] == "bad":
            raise ValueError("boom")
        return real(site, f, llm)

    monkeypatch.setattr(monitor, "collect", collect)
    res = monitor.run(cfg, db)
    assert "boom" in res[0]["error"] and res[1]["site"] == "good" and "error" not in res[1]


# -- OSM lead generation ----------------------------------------------------------------------------------------

def test_osm_generic_names_stay_separate(cfg, db):
    data = {"elements": [
        {"type": "node", "id": 1, "lat": 52.5126, "lon": 13.3267, "tags": {"name": "Späti", "shop": "convenience", "addr:street": "Hardenbergstr"}},
        {"type": "way", "id": 9, "center": {"lat": 52.5127, "lon": 13.3268}, "tags": {"name": "Späti", "shop": "convenience"}},
        {"type": "node", "id": 2, "lat": 52.5180, "lon": 13.3937, "tags": {"name": "Späti", "shop": "convenience", "addr:street": "Dorotheenstraße"}},
        {"type": "node", "id": 3, "lat": 52.5181, "lon": 13.3938,
         "tags": {"name": "Cafe Q", "amenity": "cafe", "contact:instagram": "https://www.instagram.com/cafeq/?hl=de"}}]}
    leadgen.discover(cfg, db, "TU Berlin", fetch=lambda q: data, pause=0)
    names = sorted(r["name"] for r in db.q("SELECT name FROM leads"))
    assert names == ["Cafe Q", "Späti", "Späti (Dorotheenstr.)"]
    assert db.one("SELECT instagram FROM leads WHERE name='Cafe Q'")["instagram"] == "cafeq"
    leadgen.discover(cfg, db, "TU Berlin", fetch=lambda q: data, pause=0, force=True)   # re-scan: same leads
    assert db.one("SELECT COUNT(*) n FROM leads")["n"] == 3


def test_overpass_non_json_tries_next_mirror(monkeypatch):
    import httpx

    calls = []

    def post(url, **kw):
        calls.append(url)
        req = httpx.Request("POST", url)
        if len(calls) == 1:
            return httpx.Response(200, content=b"<html>rate limited</html>", request=req)
        return httpx.Response(200, json={"elements": []}, request=req)

    monkeypatch.setattr(httpx, "post", post)
    assert leadgen._overpass("q", ["https://a", "https://b"]) == {"elements": []} and calls == ["https://a", "https://b"]


def test_discover_universities_keeps_campus_file(cfg, db):
    f = leadgen._campus_file(cfg)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps([{"name": "Old College", "lat": 52.6, "lon": 13.2, "enabled": False}]), encoding="utf-8")
    with pytest.raises(RuntimeError):
        leadgen.discover_universities(cfg, db, fetch=lambda q: {"elements": [], "remark": "runtime error: timeout"})
    assert json.loads(f.read_text(encoding="utf-8"))[0]["name"] == "Old College"
    els = [{"type": "node", "id": i, "lat": 52.40 + i * 0.05, "lon": 13.1, "tags": {"name": "X Hochschule", "amenity": "college"}}
           for i in range(3)]
    leadgen.discover_universities(cfg, db, fetch=lambda q: {"elements": els})
    rows = json.loads(f.read_text(encoding="utf-8"))
    assert {"Old College"} < {r["name"] for r in rows} and next(r for r in rows if r["name"] == "Old College")["enabled"] is False
    names = [c["name"] for c in leadgen.cluster_universities(els)]
    assert len(names) == len(set(names)) == 3
