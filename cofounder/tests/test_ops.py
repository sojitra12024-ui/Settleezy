from datetime import date, datetime, timedelta, timezone

import pytest

from settleezy_cofounder import ops
from settleezy_cofounder.leads import set_status, upsert

ME = "me@settleezy.de"


def ago(days: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).replace(microsecond=0).isoformat()


def test_partner_lifecycle_stats_and_health(cfg, db):
    lid, _ = upsert(db, "Café Kranz", "merchant", email="hi@kranz.de", category="café")
    set_status(db, lid, "partner")
    p = db.one("SELECT * FROM partners WHERE lead_id=?", (lid,))
    assert p["kind"] == "venue" and p["stage"] == "agreed" and p["email"] == "hi@kranz.de"
    set_status(db, lid, "partner")                                   # idempotent
    assert db.one("SELECT COUNT(*) n FROM partners")["n"] == 1

    for _ in range(5):
        ops.advance(db, p["id"])
    live = dict(db.one("SELECT * FROM partners WHERE id=?", (p["id"],)))
    assert live["stage"] == "live" and live["status"] == "live" and live["live_at"] == date.today().isoformat()
    stats = ops.partner_stats(cfg, db)
    assert stats["live"] == 1 and stats["onboarded_this_month"] == 1 and stats["live_by_kind"] == {"venue": 1}

    # stuck onboarding + renewal soon -> at risk, with reasons and auto to-dos (created once)
    pid = ops.add_partner(db, "Boulderhalle Ost", "venue", renewal_date=(date.today() + timedelta(days=10)).isoformat())
    db.x("UPDATE partners SET stage_since=? WHERE id=?", (ago(20), pid))
    h = ops.health(cfg, dict(db.one("SELECT * FROM partners WHERE id=?", (pid,))))
    assert h["label"] == "at risk" and any("stuck" in r for r in h["reasons"]) and any("renewal" in r for r in h["reasons"])
    assert ops.sync_auto_tasks(cfg, db) == 2
    assert ops.sync_auto_tasks(cfg, db) == 0
    with pytest.raises(ValueError):
        ops.update_partner(db, pid, stage="party")


def test_tasks_parse_scope_and_actions(db):
    t1 = ops.add_task(db, "Call Café Kranz tomorrow")
    t2 = ops.add_task(db, "Urgent: send HTW the Buddy deck today")
    t3 = ops.add_task(db, "Plan CV workshop")
    rows = {t["id"]: t for t in ops.tasks(db)}
    assert rows[t1]["title"] == "Call Café Kranz" and rows[t1]["due"] == (date.today() + timedelta(days=1)).isoformat()
    assert rows[t2]["priority"] == 3 and rows[t2]["due"] == date.today().isoformat()
    today_ids = [t["id"] for t in ops.tasks(db, "today")]
    assert t2 in today_ids and t3 in today_ids and t1 not in today_ids
    ops.set_task(db, t2, "done")
    ops.set_task(db, t3, "snooze", 2)
    assert [t["id"] for t in ops.tasks(db)] == [t1]
    assert ops.tasks(db, "done")[0]["id"] == t2
    with pytest.raises(ValueError):
        ops.set_task(db, t1, "explode")


def test_reach_out_ranks_reasons(cfg, db):
    lid, _ = upsert(db, "Brew Lab", "merchant", email="lea@brewlab.de")
    set_status(db, lid, "replied")
    upsert(db, "Späti 24", "merchant", email="s@spaeti.de", category="grocery")
    pid = ops.add_partner(db, "Kino Babylon", "venue", status="live", email="k@babylon.de")
    db.x("UPDATE partners SET last_contact_at=? WHERE id=?", (ago(60), pid))
    items = ops.reach_out(cfg, db)
    who = [i["who"] for i in items]
    assert who.index("Brew Lab") < who.index("Späti 24")
    babylon = next(i for i in items if i["who"] == "Kino Babylon")
    assert babylon["action"] == "check in" and "no contact for 60 days" in babylon["reason"]


def test_today_plan_routine_meetings_and_focus(cfg, db):
    day = date.today().isoformat()
    db.x("INSERT INTO events VALUES('e1','outlook','Call with HTW international office',?,?, 'Teams','Lena <lena@htw-berlin.de>','')",
         (f"{day}T09:30", f"{day}T10:00"))
    ops.add_task(db, "Order flyers today")
    now = datetime.combine(date.today(), datetime.min.time()).replace(hour=9, minute=45)
    plan = ops.today_plan(cfg, db, now)
    if date.today().weekday() < 5:
        outreach = next(i for i in plan["items"] if i["type"] == "block" and i["focus"] == "outreach")
        assert outreach["clash"] == ["Call with HTW international office"]
        review = next(i for i in plan["items"] if i.get("focus") == "review")
        assert "Order flyers" in review["suggestions"]
    assert plan["current"]["title"] == "Call with HTW international office"
    assert plan["tasks_today"][0]["title"] == "Order flyers"


def test_meeting_prep_and_scorecard(cfg, db):
    day = date.today().isoformat()
    db.x("INSERT INTO events VALUES('e1','outlook','Buddy platform – HTW',?,?, 'Teams','Lena <lena@htw-berlin.de>','')",
         (f"{day}T09:30", f"{day}T10:00"))
    upsert(db, "HTW Berlin", "university", website="https://www.htw-berlin.de")
    db.upsert_message({"id": "m1", "conversation_id": "c1", "folder": "inbox", "from_addr": "lena@htw-berlin.de", "from_name": "Lena",
                       "to_addrs": [ME], "cc_addrs": [], "subject": "Buddy platform for winter intake", "body_text": "Can you share pricing?",
                       "sent_at": ago(2), "is_read": 1, "language": "en", "automated": 0})
    db.conn.commit()
    md = ops.meeting_prep(cfg, db, "e1")
    assert "Lena <lena@htw-berlin.de>" in md and "HTW Berlin" in md and "Buddy platform for winter intake" in md
    with pytest.raises(ValueError):
        ops.meeting_prep(cfg, db, "nope")
    card = {r["metric"]: r for r in ops.scorecard(cfg, db)}
    assert card["Replies received"]["this_week"] + card["Replies received"]["last_week"] == 1
    assert card["New leads"]["this_week"] >= 1


def test_setz_voice_routing_and_announcements(cfg, db, monkeypatch):
    from settleezy_cofounder import voice

    monkeypatch.setattr(voice, "ask_brain", lambda cfg, text, lang: "BRAIN")
    db.close()
    assert voice.answer(cfg, "Remind me to call Café Kranz tomorrow", "en").startswith("Added: call Café Kranz, due ")
    assert voice.answer(cfg, "Remind me to call Café Kranz tomorrow", "en") == "That's already on your list."
    assert "to-dos" in voice.answer(cfg, "what are my to-dos", "en") or "No to-dos" in voice.answer(cfg, "what are my to-dos", "en")
    assert "partners are live" in voice.answer(cfg, "how are my service partners doing", "en")
    assert voice.answer(cfg, "what should I do right now", "en")
    assert voice.answer(cfg, "tell me a joke", "en") == "BRAIN"
    assert voice.split_wake("Hey Setz, who should I contact?") == (True, "who should I contact")
    assert voice.split_wake("two sets of keys")[0] is False

    import time as _t
    first = cfg.get("routine.blocks")[0]
    h, m = map(int, first["start"].split(":"))
    lt = _t.localtime()
    at = _t.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, h, m, 5, 0, 0, -1))
    announced: set[str] = set()
    msg = voice.due_announcement(cfg, announced, at)
    if lt.tm_wday < 5:
        assert msg and first["title"] in msg
        assert voice.due_announcement(cfg, announced, at) is None


def test_dashboard_ops_endpoints(cfg):
    from fastapi.testclient import TestClient

    from settleezy_cofounder.dashboard.app import app

    c, h = TestClient(app), {"X-SZ": "1"}
    assert c.post("/api/tasks", json={"title": "x"}).status_code == 403
    tid = c.post("/api/tasks", json={"title": "Call Kranz tomorrow"}, headers=h).json()["id"]
    assert c.post("/api/tasks", json={"title": "Call Kranz tomorrow"}, headers=h).json()["duplicate"] is True
    pid = c.post("/api/partners", json={"name": "Café Kranz", "kind": "venue", "offer": "10%"}, headers=h).json()["id"]
    assert c.post(f"/api/partners/{pid}", json={"advance": True}, headers=h).json()["stage"] == "contract"
    assert c.post(f"/api/partners/{pid}", json={"stage": "nope"}, headers=h).status_code == 400
    assert c.post(f"/api/partners/{pid}", json={"log_contact": True}, headers=h).json()["last_contact_at"]
    o = c.get("/api/ops").json()
    assert o["tasks"][0]["id"] == tid and o["partners"][0]["name"] == "Café Kranz" and o["partner_stats"]["onboarding"] == 1
    assert {"plan", "focus_now", "reach_out", "scorecard", "stages"} <= set(o)
    assert c.post(f"/api/tasks/{tid}", json={"action": "done"}, headers=h).json()["ok"]
    assert c.get("/api/ops").json()["tasks"] == []
    assert c.get("/api/prep/missing").status_code == 404
