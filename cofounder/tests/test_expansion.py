"""Universities (OSM), market intelligence, Lex contracts, council, autopilot, admin-panel bridge, phone share."""
import base64
import json
import time

import pytest

from settleezy_cofounder import autopilot, bridge, contracts as lex, council, leadgen, market
from settleezy_cofounder.leads import set_status, upsert
from settleezy_cofounder.ops import add_partner, add_task, update_partner


def _uni(i, name, lat, lon, **tags):
    return {"type": "way", "id": i, "center": {"lat": lat, "lon": lon}, "tags": {"name": name, "amenity": "university", **tags}}


def test_universities_cluster_and_become_campuses_and_leads(cfg, db):
    data = {"elements": [
        _uni(1, "Institut für Informatik", 52.4560, 13.2970, operator="Freie Universität Berlin"),
        _uni(2, "Rost- und Silberlaube", 52.4568, 13.2990, operator="Freie Universität Berlin"),
        _uni(3, "Berliner Hochschule für Technik Haus Gauß", 52.5440, 13.3520),        # near a built-in campus -> no new campus
        _uni(4, "Beuth Mensa", 52.6000, 13.2000),                                        # unnamed operator, still a campus
        _uni(5, "Hochschule Neu", 52.4100, 13.5800, website="https://neu.example", email="info@neu.example"),
    ]}
    clusters = leadgen.cluster_universities(data["elements"])
    fu = [c for c in clusters if c["institution"] == "Freie Universität Berlin"]
    assert len(fu) == 1 and fu[0]["buildings"] == 2
    r = leadgen.discover_universities(cfg, db, fetch=lambda q: data)
    assert r["new_university_leads"] >= 3
    names = [c["name"] for c in leadgen.campuses(cfg)]
    assert "Hochschule Neu" in names and len(names) > len(leadgen.CAMPUSES)
    lead = db.one("SELECT * FROM leads WHERE name='Hochschule Neu'")
    assert lead["kind"] == "university" and lead["email"] == "info@neu.example"
    assert leadgen.set_campus_enabled(cfg, "Hochschule Neu", False)
    assert "Hochschule Neu" not in [c["name"] for c in leadgen.campuses(cfg)]
    # an empty answer (Overpass timeout) must not wipe the discovered campuses
    with pytest.raises(RuntimeError):
        leadgen.discover_universities(cfg, db, fetch=lambda q: {"elements": [], "remark": "runtime error: timeout"})
    assert leadgen.discovered_campuses(cfg)


def test_more_venue_types_are_queried():
    q = leadgen.overpass_query(52.5, 13.4, 800)
    for tag in ("hairdresser", "language_school", "coworking", "escape_game", "laundry", "optician"):
        assert tag in q


def _market_fixture(db):
    p1 = add_partner(db, "Café Kranz", "venue", category="cafe", redemptions=40, live_at="2026-08-01")
    update_partner(db, p1, stage="live", status="live", live_at="2026-08-01")
    lid, _ = upsert(db, "Burger Bros", "merchant", source="groupon", category="restaurant burger", email="hi@bb.de")
    db.x("UPDATE leads SET distance_m=200, campus='TU Berlin (Charlottenburg)' WHERE id=?", (lid,))
    upsert(db, "Copy Point", "merchant", category="copyshop student service", phone="+49 30 1")
    far, _ = upsert(db, "Far Away Spa", "merchant", category="spa wellness")
    db.x("UPDATE leads SET distance_m=4000 WHERE id=?", (far,))
    db.x("INSERT INTO listings(id,source,url,title,merchant,category,first_seen,last_seen) VALUES "
         "('g1','groupon','u1','x','Café Kranz','cafe','2026-10-01','2026-10-01'),"
         "('g2','groupon','u2','x','Burger Bros','restaurant','2026-10-01','2026-10-01'),"
         "('s1','studentbeans','u3','x','Gym One','gym','2026-10-01','2026-10-01')")
    return lid


def test_market_onboard_next_explains_and_ranks(cfg, db):
    lid = _market_fixture(db)
    nxt = market.onboard_next(cfg, db)
    names = [r["name"] for r in nxt]
    assert names[0] == "Burger Bros" and names[-1] == "Far Away Spa"
    top = nxt[0]
    assert any("Groupon" in x for x in top["reasons"]) and any("200 m" in x for x in top["reasons"]) and top["pitch"]
    assert market.onboard_next(cfg, db, bucket_filter="study")[0]["name"] == "Copy Point"
    ca = market.competitor_analysis(cfg, db)
    groupon = next(p for p in ca["platforms"] if p["source"] == "groupon")
    assert groupon["overlap_with_partners"] == ["café kranz"] and ca["warm_leads"][0]["id"] == lid
    pa = market.partner_analysis(cfg, db)
    assert pa["totals"]["live"] == 1 and pa["top"][0]["name"] == "Café Kranz" and pa["top"][0]["per_week"] > 0
    assert any(g["bucket"] == "Study & services" and g["gap"] > 0 for g in pa["mix"])
    # members asking for a venue in the app push it up
    db.kv_set("bridge:requests", json.dumps([{"venue": "Far Away Spa", "count": 9}]))
    assert any("members asked" in x for x in next(r for r in market.onboard_next(cfg, db) if r["name"] == "Far Away Spa")["reasons"])


def test_lex_prepares_from_known_data_and_flags_risks(cfg, db):
    lid, _ = upsert(db, "Brew Lab", "merchant", email="hi@brew.de")
    db.x("UPDATE leads SET legal_name='Brew Lab GmbH', owner='Mia Klein', address='Torstr. 1, 10119 Berlin' WHERE id=?", (lid,))
    pid = add_partner(db, "Brew Lab", "venue", lead_id=lid, offer="15% auf alle Getränke")
    r = lex.prepare(cfg, db, "partner_de", partner_id=pid)
    assert "Brew Lab GmbH" in r["text"] and "Mia Klein" in r["text"] and "15% auf alle Getränke" in r["text"]
    assert set(r["missing"]) <= {"our_legal_name", "our_address", "our_signatory"}
    r2 = lex.prepare(cfg, db, "partner_de", contract_id=r["id"], fields={"our_legal_name": "Settleezy UG", "our_address": "Berlin",
                                                                         "our_signatory": "Founder"})
    assert r2["missing"] == [] and r2["analysis"]["score"] >= 85 and not [x for x in r2["analysis"]["risks"] if x["severity"] == "alert"]
    bad = lex.analyse("Vertrag zwischen A und B. Der Partner ist exklusiver Partner. Bei Verstoß Vertragsstrafe von 5.000 €. "
                      "Die Haftung ist ausgeschlossen. Kündigungsfrist 6 Monate.")
    keys = {x["key"] for x in bad["risks"]}
    assert {"exclusivity", "penalty", "liability_excl", "long_notice"} <= keys and bad["score"] < 60
    assert "fees" not in {x["key"] for x in lex.analyse("The Partner pays no commission or fees. If a provision is invalid...")["risks"]}
    lex.set_status(db, r["id"], "signed")
    assert db.one("SELECT stage, signed_at FROM partners WHERE id=?", (pid,))["stage"] == "contract"


def test_lex_auto_prepares_for_agreed_partners_once(cfg, db):
    add_partner(db, "Kaffeebar", "venue")
    assert len(lex.auto_prepare(cfg, db)) == 1 and lex.auto_prepare(cfg, db) == []


def test_lex_reads_docx(cfg, db, tmp_path):
    docx = pytest.importorskip("docx")
    d = docx.Document()
    d.add_paragraph("Agreement between Settleezy and Gym One. Exclusive partner for Berlin.")
    p = tmp_path / "draft.docx"
    d.save(str(p))
    r = lex.review_file(cfg, db, str(p))
    assert "exclusivity" in {x["key"] for x in r["risks"]} and lex.contracts(db, "review")


def test_council_finds_cross_agent_problems_and_fixes_them(cfg, db):
    lid, _ = upsert(db, "Späti 24", "merchant")
    pid = add_partner(db, "Späti 24", "venue")          # links + marks the lead partner
    db.x("UPDATE leads SET status='contacted' WHERE id=?", (lid,))   # data drifts: partner looks like a lead again
    lost, _ = upsert(db, "Old Bar", "merchant")
    add_task(db, "Send first email", source="sequence", lead_id=lost, due="2026-01-01")
    set_status(db, lost, "lost")
    add_task(db, "Step 2", source="sequence", lead_id=lost, due="2026-01-05")
    db.kv_set("last_error:scrape", "2099-01-01T00:00:00+00:00 RuntimeError: groupon blocked")
    r = council.cross_check(cfg, db, autofix=False)
    msgs = {m["key"]: m for m in council.messages(db)}
    assert {"partner-lead-status", "seq-closed", "crash:job:scrape", "contracts-missing"} <= set(msgs)
    assert msgs["crash:job:scrape"]["to_agent"] == "scout" and msgs["crash:job:scrape"]["kind"] == "bug"
    assert msgs["seq-closed"]["fix"] == "stop_sequences"
    council.apply_fix(cfg, db, msgs["partner-lead-status"]["id"])
    council.apply_fix(cfg, db, msgs["seq-closed"]["id"])
    assert db.one("SELECT status FROM leads WHERE id=?", (lid,))["status"] == "partner"
    assert not db.one("SELECT 1 FROM tasks WHERE lead_id=? AND status='open' AND source='sequence'", (lost,))
    r = council.cross_check(cfg, db, autofix=False)
    assert r["resolved"] >= 1 and "seq-closed" not in {m["key"] for m in council.messages(db)}
    # auto-fix mode drafts the missing contract
    council.cross_check(cfg, db)
    assert db.one("SELECT COUNT(*) n FROM contracts WHERE partner_id=?", (pid,))["n"] == 1


def test_agent_brains_remember_streaks_and_escalate(cfg, db):
    f = [{"key": "x", "title": "Thing", "detail": "", "severity": "warn", "action": ""}]
    for _ in range(5):
        out = council.observe(cfg, db, "hunter", f)
    assert out[0]["streak"] == 5 and out[0]["severity"] == "warn"
    out = council.observe(cfg, db, "hunter", f)
    assert out[0]["severity"] == "alert" and out[0]["escalated"]
    council.observe(cfg, db, "hunter", [])
    b = council.brain_summary(db, "hunter")
    assert b["resolved"] == 1 and b["tracking"] == 0


def test_agents_run_in_parallel_with_council(cfg, db):
    from settleezy_cofounder.agents import AGENTS, run

    upsert(db, "Brew Lab", "merchant", email="a@b.de")
    out = run(cfg, db, publish_events=False, parallel=True)
    assert len(out["agents"]) == len(AGENTS) and "council" in out
    assert {"lex", "vega", "bridge"} <= {a["agent"] for a in out["agents"]}
    assert all(a["status"] != "error" for a in out["agents"]), out["agents"]


def test_autopilot_asks_before_reaching_people(cfg, db):
    from datetime import datetime, timedelta

    from settleezy_cofounder.scheduling import _ensure

    _ensure(db)
    start = (datetime.now() + timedelta(days=3)).replace(hour=15, minute=0, second=0, microsecond=0)
    while start.weekday() >= 5:
        start += timedelta(days=1)
    db.x("INSERT INTO meeting_requests(source,ref,who,email,subject,proposed,status,detected_at,received_at) VALUES(?,?,?,?,?,?,?,?,?)",
         ("email", "m1", "Lea", "lea@x.de", "Kooperation", json.dumps([start.isoformat(timespec="minutes")]), "open",
          datetime.now().isoformat(), datetime.now().isoformat()))
    upsert(db, "Burger Bros", "merchant", email="hi@bb.de", category="restaurant")
    r = autopilot.run(cfg, db)
    assert not r["errors"], r["errors"]
    pend = autopilot.pending(db)
    kinds = {a["rule"] for a in pend}
    assert "book_meetings" in kinds and "start_sequences" in kinds
    assert not db.one("SELECT 1 FROM meeting_requests WHERE status='booked'")
    seq = next(a for a in pend if a["rule"] == "start_sequences")
    assert autopilot.decide(cfg, db, seq["id"], True)["status"] == "done"
    assert db.one("SELECT COUNT(*) n FROM tasks WHERE source='sequence'")["n"] > 0
    bk = next(a for a in pend if a["rule"] == "book_meetings")
    assert autopilot.decide(cfg, db, bk["id"], False)["status"] == "rejected"
    with pytest.raises(ValueError):
        autopilot.decide(cfg, db, bk["id"], True)
    assert autopilot.run(cfg, db)["asked"] == []        # no duplicate approvals


def _signed(secret, body, ts=None, mid="msg_1"):
    ts = str(int(ts or time.time()))
    return {"webhook-id": mid, "webhook-timestamp": ts, "webhook-signature": bridge.sign(secret, mid, ts, body)}


def test_bridge_webhook_signature_and_events(cfg, db, monkeypatch):
    secret = "whsec_" + base64.b64encode(b"0123456789abcdef0123456789abcdef").decode()
    monkeypatch.setenv("SZ_ADMIN_WEBHOOK_SECRET", secret)
    body = json.dumps({"type": "venue.requested", "data": {"venue": "Mustafa's Gemüse Kebap", "category": "döner"}}).encode()
    bridge.verify(_signed(secret, body), body)
    with pytest.raises(ValueError):
        bridge.verify(_signed(secret, body), body + b" ")
    with pytest.raises(ValueError):
        bridge.verify(_signed(secret, body, ts=time.time() - 3600), body)
    assert bridge.handle_event(cfg, db, "msg_1", json.loads(body))["ok"]
    assert bridge.handle_event(cfg, db, "msg_1", json.loads(body))["duplicate"]
    assert db.one("SELECT 1 FROM leads WHERE name=?", ("Mustafa's Gemüse Kebap",))
    pid = add_partner(db, "Café Kranz", "venue")
    db.x("UPDATE partners SET external_id='p9' WHERE id=?", (pid,))
    bridge.handle_event(cfg, db, "msg_2", {"type": "redemption.created", "data": {"partner_id": "p9"}})
    assert db.one("SELECT redemptions FROM partners WHERE id=?", (pid,))["redemptions"] == 1

    from fastapi.testclient import TestClient

    from settleezy_cofounder.dashboard.app import app

    c = TestClient(app)
    ok = json.dumps({"type": "member.converted", "data": {"plan": "semester"}}).encode()
    assert c.post("/api/bridge/webhook", content=ok, headers=_signed(secret, ok, mid="m3")).status_code == 200
    assert c.post("/api/bridge/webhook", content=ok, headers={**_signed(secret, ok, mid="m4"), "webhook-signature": "v1,AAAA"}).status_code == 401
    # remote (through Tailscale funnel) the webhook path needs no PIN, but everything else still does
    remote = {"x-forwarded-for": "1.2.3.4"}
    assert c.post("/api/bridge/webhook", content=ok, headers={**_signed(secret, ok, mid="m5"), **remote}).status_code == 200
    assert c.get("/api/leads", headers=remote).status_code in (401, 403)


class FakePanel:
    def __init__(self):
        self.created = []

    def partners(self, since=None):
        return [{"id": "p1", "name": "Café Kranz GmbH", "status": "live", "redemptions_total": 55, "redemptions_30d": 12},
                {"id": "p2", "name": "Brand New Gym", "status": "draft"}]

    def member_stats(self):
        return {"paying_members": 240, "trials_active": 31, "plan": "x"}

    def venue_requests(self, since=None):
        return [{"venue": "Lisboa Bar", "category": "bar", "count": 4}]

    def create_draft_partner(self, body):
        self.created.append(body)
        return {"id": "p77"}


def test_bridge_sync_and_push(cfg, db):
    pid = add_partner(db, "Café Kranz", "venue")
    panel = FakePanel()
    r = bridge.sync(cfg, db, panel)
    p = db.one("SELECT * FROM partners WHERE id=?", (pid,))
    assert p["external_id"] == "p1" and p["status"] == "live" and p["redemptions"] == 55 and p["redemptions_30d"] == 12
    assert r["metrics"] == 2 and db.one("SELECT value FROM metrics WHERE source='app' AND key='paying_members'")["value"] == 240
    assert db.one("SELECT 1 FROM partners WHERE name='Brand New Gym'") and db.one("SELECT 1 FROM leads WHERE name='Lisboa Bar'")
    p2 = add_partner(db, "Yoga Mitte", "venue", offer="20% off")
    update_partner(db, p2, stage="offer")
    out = bridge.push_partner(cfg, db, p2, panel)
    assert out["external_id"] == "p77" and panel.created[0]["status"] == "draft" and panel.created[0]["offer"] == "20% off"
    assert bridge.push_partner(cfg, db, p2, panel)["skipped"]


def test_share_suggestions_and_actions(cfg, db, monkeypatch):
    from settleezy_cofounder import share

    s = share.suggest({"url": "https://www.instagram.com/brewlab.berlin/?igsh=x", "text": ""})
    assert s[0]["action"] == "instagram"
    s = share.suggest({"text": "Hi! Could we meet on Tuesday at 15:00 to talk about a partnership? Best, Lea", "title": "Partnership"})
    assert "meeting" in {x["action"] for x in s} and "task" in {x["action"] for x in s}
    r = share.act(cfg, db, {"text": "Call Kranz about the contract tomorrow", "title": ""}, "task")
    assert db.one("SELECT due FROM tasks WHERE id=?", (r["task_id"],))["due"]


def test_dashboard_new_endpoints(cfg, db):
    from fastapi.testclient import TestClient

    from settleezy_cofounder.dashboard.app import app

    _market_fixture(db)
    c, h = TestClient(app), {"X-SZ": "1"}
    assert c.get("/api/market").json()["onboard_next"][0]["name"] == "Burger Bros"
    assert c.get("/api/market?ai=1").status_code == 403
    t = c.get("/api/contracts").json()
    assert {x["key"] for x in t["templates"]} >= {"partner_de", "partner_en", "university_en", "nda_en"}
    made = c.post("/api/contracts", json={"template": "partner_en", "partner_id": t["partners"][0]["id"]}, headers=h).json()
    assert made["id"] and "Café Kranz" in made["text"]
    a = c.post("/api/contracts/analyse", json={"text": "Agreement between A and B. Exclusive partner."}, headers=h).json()
    assert "exclusivity" in {x["key"] for x in a["risks"]}
    assert c.post("/api/contracts/analyse", content=b"x", headers={"X-Filename": "a.exe"}).status_code == 403
    assert c.post("/api/council/run", headers=h).status_code == 200 and "brains" in c.get("/api/council").json()
    assert {"rules", "pending"} <= set(c.get("/api/autopilot").json())
    assert c.get("/api/bridge/v1/recommendations").status_code == 401
    assert c.get("/talk").status_code == 200
    m = c.get("/manifest.webmanifest").json()
    assert m["share_target"]["action"] == "/share" and any(s["url"] == "/talk" for s in m["shortcuts"])
    r = c.post("/share", data={"title": "Brew Lab", "url": "https://www.instagram.com/brewlab/"}, follow_redirects=False)
    assert r.status_code == 303 and "#approvals" in r.headers["location"]
    inbox = c.get("/api/share/inbox").json()
    assert inbox[0]["suggestions"][0]["action"] == "instagram"
    sid = inbox[0]["id"]
    assert c.post(f"/api/share/{sid}", json={"action": "discard"}, headers=h).json()["done"] == "discarded"
    assert c.get("/api/share/inbox").json() == []
