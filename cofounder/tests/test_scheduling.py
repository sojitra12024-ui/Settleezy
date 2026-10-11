import json
from datetime import date, datetime, timedelta

import pytest

from settleezy_cofounder import scheduling as sch
from settleezy_cofounder.leads import upsert

ME = "me@settleezy.de"


def next_weekday(wd, after=None):
    d = (after or date.today()) + timedelta(days=1)
    while d.weekday() != wd:
        d += timedelta(days=1)
    return d


def test_proposed_times_en_de():
    sent = datetime(2026, 10, 5, 9, 30)           # a Monday
    assert sch.proposed_times("Are you free Tuesday at 2pm or Thursday 10:30?", sent) == [datetime(2026, 10, 6, 14, 0), datetime(2026, 10, 8, 10, 30)]
    assert sch.proposed_times("Hätten Sie am Mittwoch um 15 Uhr Zeit?", sent) == [datetime(2026, 10, 7, 15, 0)]
    assert sch.proposed_times("Wie wäre es am 14.10. um 10 Uhr?", sent) == [datetime(2026, 10, 14, 10, 0)]
    assert sch.proposed_times("tomorrow at 9:15?", sent) == [datetime(2026, 10, 6, 9, 15)]
    assert sch.proposed_times("we have 3 locations, open Mo 8", sent) == []          # too ambiguous
    assert sch.proposed_times("Monday at 2", sent) == [datetime(2026, 10, 12, 14, 0)]  # 'at 2' at work = 14:00, next Monday


def test_detection_rules():
    assert sch.looks_like_request("Partnership", "Could we have a quick call next week?")
    assert sch.looks_like_request("Kooperation", "Hätten Sie Zeit für ein kurzes Telefonat?")
    assert not sch.looks_like_request("Invoice", "Please find the invoice attached. Thanks for the call yesterday.")


def test_free_slots_avoid_meetings_and_prefer_calls_block(cfg, db):
    d = next_weekday(1, date.today() + timedelta(days=1))
    db.x("INSERT INTO events(id,source,title,start,end) VALUES('e1','outlook','Busy',?,?)", (f"{d}T14:00", f"{d}T15:30"))
    assert not sch.is_free(cfg, db, datetime.fromisoformat(f"{d}T15:30"), 30)       # 15-min buffer after the meeting
    assert sch.is_free(cfg, db, datetime.fromisoformat(f"{d}T15:45"), 30)
    assert not sch.is_free(cfg, db, datetime.fromisoformat(f"{d}T17:45"), 30)       # ends after 18:00
    assert not sch.is_free(cfg, db, datetime.combine(next_weekday(5), datetime.min.time()).replace(hour=10), 30)  # Saturday
    slots = sch.suggest_slots(cfg, db, now=datetime.combine(d - timedelta(days=1), datetime.min.time()).replace(hour=8))
    starts = [datetime.fromisoformat(s["start"]) for s in slots]
    assert len(slots) == 3 and len({s.date() for s in starts}) == 3
    assert all(14 <= s.hour < 17 for s in starts)                                   # the calls block (14:00–16:30)
    assert all(sch.is_free(cfg, db, s, 30) for s in starts)


def test_detect_reply_draft_and_book(cfg, db):
    lid, _ = upsert(db, "Brew Lab", "merchant", email="lea@brewlab.de")
    day = next_weekday(2, date.today() + timedelta(days=1))
    sent = datetime.now().replace(microsecond=0)
    db.upsert_message({"id": "m1", "conversation_id": "c1", "folder": "inbox", "from_addr": "lea@brewlab.de", "from_name": "Lea Brandt",
                       "to_addrs": [ME], "cc_addrs": [], "subject": "Kooperation", "sent_at": sent.isoformat(), "is_read": 0,
                       "body_text": "Hallo! Hätten Sie Zeit für ein kurzes Telefonat? Vielleicht nächsten Mittwoch um 15 Uhr?",
                       "language": "de", "automated": 0})
    db.upsert_message({"id": "m2", "conversation_id": "c2", "folder": "inbox", "from_addr": "x@shop.de", "from_name": "Shop",
                       "to_addrs": [ME], "cc_addrs": [], "subject": "Your order", "sent_at": sent.isoformat(), "is_read": 0,
                       "body_text": "Your order has shipped.", "language": "en", "automated": 0})
    db.kv_set("instagram:unanswered_comments", json.dumps([{"user": "kaffeekiez", "text": "Love this! Can we collab? DM me", "post": "p1", "at": "2026-10-01"}]))
    assert sch.detect(cfg, db) == {"new_requests": 2, "open": 2}
    assert sch.detect(cfg, db)["new_requests"] == 0                                  # idempotent
    reqs = {r["source"]: r for r in sch.requests(cfg, db)}
    r = reqs["email"]
    assert r["lead"]["name"] == "Brew Lab" and r["lang"] == "de"
    wed = datetime.fromisoformat(r["proposed"][0])
    assert wed.weekday() == 2 and wed.hour == 15 and r["proposed_free"] == r["proposed"]
    text = sch.reply_text(cfg, r, r["slots"])
    assert text.startswith("Hallo Lea") and "passt mir gut" in text and "15:00" in text

    class FakeGraph:
        def __init__(self):
            self.calls = []

        def create_reply_draft(self, mid, text):
            self.calls.append(("draft", mid, text))
            return {"id": "d1"}

        def create_event(self, subject, start, end, **kw):
            self.calls.append(("event", subject, start, end, kw))
            return {"id": "ev1", "webLink": "https://outlook/ev1"}

    g = FakeGraph()
    out = sch.draft_reply(cfg, db, r["id"], graph=g)
    assert g.calls[0][:2] == ("draft", "m1") and out["draft_id"] == "d1"
    res = sch.book(cfg, db, r["id"], r["proposed_free"][0], graph=g)
    _, subject, start, end, kw = g.calls[1]
    assert subject == "Settleezy × Brew Lab" and (end - start).seconds == 1800 and kw["attendees"] == [("lea@brewlab.de", "Lea Brandt")]
    assert kw["online"] is True and res["invite_sent_by_outlook"]
    assert db.one("SELECT status FROM leads WHERE id=?", (lid,))["status"] == "meeting"
    assert db.one("SELECT COUNT(*) n FROM events WHERE id='outlook:ev1'")["n"] == 1
    assert db.one("SELECT COUNT(*) n FROM tasks WHERE title LIKE 'Prep for Settleezy × Brew Lab%'")["n"] == 1
    with pytest.raises(ValueError):                                                  # the slot is taken now
        sch.book(cfg, db, reqs["instagram"]["id"], r["proposed_free"][0], graph=g)
    assert [x["source"] for x in sch.requests(cfg, db)] == ["instagram"]
    # replying in Outlook closes the request on the next scan
    db.upsert_message({"id": "m3", "conversation_id": "c1", "folder": "sent", "from_addr": ME, "from_name": "", "to_addrs": ["lea@brewlab.de"],
                       "cc_addrs": [], "subject": "Re", "sent_at": (sent + timedelta(minutes=5)).isoformat(), "is_read": 1,
                       "body_text": "ok", "language": "de", "automated": 0})


def test_meeting_api_guard(cfg):
    from fastapi.testclient import TestClient

    from settleezy_cofounder.dashboard.app import app

    c = TestClient(app)
    assert c.get("/api/meetings/requests").json()["requests"] == []
    assert c.post("/api/meetings/requests/1", json={"action": "dismiss"}).status_code == 403
    assert c.post("/api/meetings/requests/1", json={"action": "fly"}, headers={"X-SZ": "1"}).status_code == 400
