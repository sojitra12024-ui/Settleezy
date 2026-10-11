import json
from datetime import date, datetime, timedelta, timezone

import pytest

from settleezy_cofounder import agents, brain, operations as op, ops, planner, voice


@pytest.fixture()
def no_brain(monkeypatch):
    monkeypatch.setattr(voice, "ask_brain", lambda *a: "BRAIN")


# -- voice: booking ---------------------------------------------------------------------------------

def _req(rid, who):
    return {"id": rid, "who": who, "proposed_free": [], "slots": [{"start": "2026-10-13T10:00", "end": "2026-10-13T10:30"},
                                                                  {"start": "2026-10-13T14:00", "end": "2026-10-13T14:30"}]}


@pytest.fixture()
def booking(monkeypatch):
    from settleezy_cofounder import scheduling as sch

    state = {"reqs": [_req(1, "Lea Wolf")], "booked": []}
    monkeypatch.setattr(sch, "requests", lambda *a, **k: state["reqs"])
    monkeypatch.setattr(sch, "fmt_slot", lambda s, *a: s["start"])

    def book(cfg, db, rid, start, *a, **k):
        state["booked"].append((rid, start))
        return {"subject": "Call", "start": start, "end": start, "invite_sent_by_outlook": False}

    monkeypatch.setattr(sch, "book", book)
    return state


def test_book_option_out_of_range_never_books(cfg, db, booking, no_brain):
    for t in ["book option 3", "book option 0", "book option 12"]:
        assert voice._answer(cfg, t, "en").startswith("There are only 2 options")
    assert booking["booked"] == []


def test_booking_needs_confirmation(cfg, db, booking, no_brain):
    r = voice._answer(cfg, "book option 2", "en")
    assert "Lea Wolf" in r and "2026-10-13T14:00" in r and "yes, book it" in r and booking["booked"] == []
    assert "Booked" in voice._answer(cfg, "yes, book it", "en") and booking["booked"] == [(1, "2026-10-13T14:00")]
    assert db.kv_get("voice:pending_booking") is None
    voice._answer(cfg, "Yes", "en")                                   # nothing pending any more
    assert len(booking["booked"]) == 1


def test_booking_confirmation_expires(cfg, db, booking, no_brain):
    voice._answer(cfg, "book option 1", "en")
    p = json.loads(db.kv_get("voice:pending_booking"))
    db.kv_set("voice:pending_booking", json.dumps({**p, "expires": p["expires"] - 300}))
    assert "expired" in voice._answer(cfg, "yes", "en") and booking["booked"] == []


@pytest.mark.parametrize("t", ["don't book it yet", "please do not book the meeting", "nicht buchen", "not yet"])
def test_negations_never_book(cfg, db, booking, no_brain, t):
    voice._answer(cfg, "book option 1", "en")
    assert "won't book" in voice._answer(cfg, t, "en") or "nichts" in voice._answer(cfg, t, "de")
    voice._answer(cfg, "yes", "en")
    assert booking["booked"] == [] and db.kv_get("voice:pending_booking") is None


def test_booking_with_several_requests_needs_a_name(cfg, db, booking, no_brain):
    booking["reqs"] = [_req(1, "Lea Wolf"), _req(2, "Tom Berg")]
    r = voice._answer(cfg, "book option 1", "en")
    assert "Who is it for" in r and db.kv_get("voice:pending_booking") is None
    assert "Tom Berg" in voice._answer(cfg, "book option 2 for Tom", "en")
    voice._answer(cfg, "yes", "en")
    assert booking["booked"] == [(2, "2026-10-13T14:00")]


# -- voice: intents ----------------------------------------------------------------------------------

def test_note_to_self_and_remember_to_become_tasks(cfg, db, no_brain):
    assert voice._answer(cfg, "Note to self: send Kranz the contract", "en").startswith("Added: send Kranz the contract")
    assert voice._answer(cfg, "Remember to call Brew Lab tomorrow", "en").startswith("Added: call Brew Lab")
    assert voice._answer(cfg, "Remember that Kranz prefers WhatsApp", "en") == "Got it, I'll remember that."
    assert {r["title"] for r in db.q("SELECT title FROM tasks")} == {"send Kranz the contract", "call Brew Lab"}


def test_tell_me_about_builtin_topics_falls_through(cfg, db, no_brain):
    assert voice._answer(cfg, "Tell me about my week", "en").startswith("This week you have")
    assert "active deals" in voice._answer(cfg, "Tell me about the pipeline", "en")
    assert voice._answer(cfg, "What do you know about my to-dos", "en") == "No to-dos due today."
    assert voice._answer(cfg, "What do you know about Cafe Nowhere", "en").startswith("I don't know anything")


def test_plan_next_week_on_weekend(cfg, db, no_brain, monkeypatch):
    import datetime as dt

    seen = []

    class Sat(dt.date):
        @classmethod
        def today(cls):
            return dt.date(2026, 10, 10)

    monkeypatch.setattr(dt, "date", Sat)
    monkeypatch.setattr(planner, "plan_week", lambda cfg, db, start=None: seen.append(start) or {})
    monkeypatch.setattr(planner, "spoken_week", lambda p: "ok")
    voice._answer(cfg, "plan next week", "en")
    assert seen == [dt.date(2026, 10, 12)]


# -- agents ------------------------------------------------------------------------------------------

def test_persistent_alert_keeps_one_task(cfg, db, monkeypatch):
    rep = {"hunter": {"agent": "hunter", "summary": "", "findings": [
        {"key": "replied", "title": "1 lead replied", "detail": "Kranz", "severity": "alert", "action": "Call Kranz"}]}}
    monkeypatch.setattr(agents, "latest", lambda db: rep)
    for _ in range(3):
        agents.synthesize(cfg, db, use_ai=False)
    rows = db.q("SELECT * FROM tasks WHERE source LIKE 'agent:%'")
    assert len(rows) == 1 and rows[0]["dedupe_key"] == "agent:replied"
    ops.set_task(db, rows[0]["id"], "done")
    assert agents.synthesize(cfg, db, use_ai=False)["todos_created"] == 0      # done recently: stays done
    old = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    db.x("UPDATE tasks SET done_at=?", (old,))
    assert agents.synthesize(cfg, db, use_ai=False)["todos_created"] == 1
    t = db.one("SELECT * FROM tasks")
    assert t["status"] == "open" and t["due"] == date.today().isoformat()


def test_chrono_meeting_soon_near_midnight(cfg, db, monkeypatch):
    from settleezy_cofounder import ops as o

    class Late(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 12, 23, 0)

    monkeypatch.setattr(agents, "datetime", Late)
    monkeypatch.setattr(o, "today_plan", lambda cfg, db: {"date": "2026-10-12", "current": None, "tasks_today": [], "items": [
        {"type": "meeting", "start": "23:30", "title": "Late call", "event_id": "e1"},
        {"type": "meeting", "start": "22:00", "title": "Earlier", "event_id": "e2"}]})
    monkeypatch.setattr(planner, "plan_week", lambda cfg, db: {"totals": {"meeting_hours": 0}, "insights": [], "week_start": ""})
    rep = agents.Report("chrono")
    agents._chrono(cfg, db, rep)
    assert [f.key for f in rep.findings if f.key.startswith("prep:")] == ["prep:e1"]


# -- ops ---------------------------------------------------------------------------------------------

def test_partner_dates_are_validated(cfg, db):
    with pytest.raises(ValueError, match="renewal_date"):
        ops.add_partner(db, "Brew Lab", "venue", renewal_date="March 2027")
    pid = ops.add_partner(db, "Brew Lab", "venue", signed_at="2026-10-01")
    with pytest.raises(ValueError, match="live_at"):
        ops.update_partner(db, pid, live_at="soon")
    assert ops.update_partner(db, pid, renewal_date="2027-03-31")["renewal_date"] == "2027-03-31"
    assert ops.update_partner(db, pid, renewal_date="")["renewal_date"] is None


def test_invalid_stored_dates_dont_crash(cfg, db):
    pid = ops.add_partner(db, "Brew Lab", "venue")
    db.x("UPDATE partners SET renewal_date='March 2027', live_at='later', status='live' WHERE id=?", (pid,))
    p = ops.partners(cfg, db)[0]
    assert "invalid renewal date" in p["health"]["reasons"]
    ops.partner_stats(cfg, db)
    ops.reach_out(cfg, db)
    ops.sync_auto_tasks(cfg, db)


def test_scorecard_ignores_future_meetings(cfg, db):
    now = datetime.now()
    for i, d in enumerate([now - timedelta(minutes=5), now + timedelta(minutes=5)]):
        if d.date() - timedelta(days=d.weekday()) == now.date() - timedelta(days=now.weekday()):
            db.x("INSERT INTO events(id,title,start,end) VALUES(?,?,?,?)", (f"e{i}", "x", d.strftime("%Y-%m-%dT%H:%M"),
                                                                           d.strftime("%Y-%m-%dT%H:%M")))
    future = db.one("SELECT COUNT(*) n FROM events WHERE start > ?", (now.strftime("%Y-%m-%dT%H:%M"),))["n"]
    total = db.one("SELECT COUNT(*) n FROM events")["n"]
    held = {r["metric"]: r["this_week"] for r in ops.scorecard(cfg, db)}["Meetings held"]
    assert held == total - future


# -- planner / notify / operations --------------------------------------------------------------------

def test_ics_uids_unique_for_chunks():
    plan = {"days": [{"date": "2026-10-12", "blocks": [{"title": "Deep work", "start": "09:00", "end": "17:00", "items": [
        {"task_id": 1, "start": "09:00", "end": "10:00", "title": "Deck (1/2)", "category": "content"},
        {"task_id": 1, "start": "14:00", "end": "16:00", "title": "Deck (2/2)", "category": "content"}]}]}]}
    uids = [ln for ln in planner.to_ics(plan).split("\r\n") if ln.startswith("UID:")]
    assert len(uids) == 2 and len(set(uids)) == 2


def test_quiet_hours_unpadded():
    from settleezy_cofounder.notify import quiet_now

    class C:
        def get(self, k, d=None):
            return ["22:00", "7:30"]

    at = lambda hm: quiet_now(C(), datetime.strptime("2026-10-12 " + hm, "%Y-%m-%d %H:%M"))  # noqa: E731
    assert at("23:00") and at("07:00") and not at("08:00") and not at("12:00") and not at("20:30")


def test_stale_running_marker_and_playbook_cleanup(cfg, db, monkeypatch):
    from settleezy_cofounder import jobs

    db.kv_set("running:scrape", (datetime.now(timezone.utc) - timedelta(hours=4)).isoformat())
    db.kv_set("running:mail", (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat())
    js = {j["name"]: j for j in op.jobs_status(db)}
    assert not js["scrape"]["running"] and js["mail"]["running"]

    def boom(cfg, step):
        raise KeyboardInterrupt

    monkeypatch.setattr(jobs, "run_job", boom)
    with pytest.raises(KeyboardInterrupt):
        op.run_playbook(cfg, "inbox")
    assert json.loads(db.kv_get("playbook:running")) == {}


# -- brain -------------------------------------------------------------------------------------------

def test_embedder_failure_falls_back_to_hash(cfg, db):
    brain.remember(cfg, db, "Lea prefers WhatsApp", subject="Lea")
    e = brain.embedder(cfg)
    e.name = "ollama:bge-m3"
    e._ollama = lambda *a: (_ for _ in ()).throw(ConnectionError("refused"))
    try:
        assert brain.recall(cfg, db, "who prefers WhatsApp")
        assert e.name.startswith("hash:") and "ollama:bge-m3 failed" in e.note
    finally:
        e.name = f"hash:{brain.HASH_DIM}"


def test_remember_does_not_reuse_system_or_other_kind_keys(cfg, db):
    text = "Cafe Kranz is a partner since March"
    brain.remember(cfg, db, text, kind="fact", key="partner:7", subject="Cafe Kranz")
    brain.remember(cfg, db, text, kind="fact", subject="Cafe Kranz")
    assert db.one("SELECT text FROM memories WHERE key='partner:7'")["text"] == text
    assert db.one("SELECT COUNT(*) n FROM memories")["n"] == 2
    brain.remember(cfg, db, text + "!", kind="preference", subject="Cafe Kranz")
    assert db.one("SELECT COUNT(*) n FROM memories")["n"] == 3
