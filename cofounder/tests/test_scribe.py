"""Scribe: learns the founder's style and decisions from (incoming -> reply) pairs and drafts like them."""
import json
from datetime import datetime, timedelta, timezone

from settleezy_cofounder import scribe

ME = "me@settleezy.de"


def _ts(days_ago, minutes=0):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago, minutes=-minutes)).isoformat()


def _m(db, mid, cid, folder, body, days_ago, minutes=0, frm=None, subject="Partnership", to=("lea@brewlab.de",)):
    db.upsert_message({"id": mid, "conversation_id": cid, "folder": folder, "from_addr": frm or (ME if folder == "sent" else "lea@brewlab.de"),
                       "from_name": "" if folder == "sent" else "Lea Brandt", "to_addrs": list(to), "cc_addrs": [], "subject": subject,
                       "body_text": body, "sent_at": _ts(days_ago, minutes), "is_read": 1, "language": "en", "automated": 0})


RESCHEDULE_IN = "Hi Mruddual,\nunfortunately something came up and I can't make it to our meeting today. Could we move it?\nBest, Lea"
RESCHEDULE_OUT = ("Hello Lea,\n\nThankyou for your quick response. Shall we reschedule the meeting to Monday next week at 10:00? "
                  "You can also use the link below to schedule the meeting as per your availability.\n\nKind regards,\nMruddual Sojitra\n"
                  "Founder & CEO / Settleezy")
DOUBT_IN = "Dear Mruddual,\nhow does the discount work exactly? Is there a commission for us?\nKind regards, Tom"
DOUBT_OUT = ("Dear Tom,\n\nThank you for your message.\n\nRegarding your questions: we do not operate on a commission model. "
             "The member benefit is set entirely on your own terms.\n\nI would be happy to visit you in person to go through it together.\n\n"
             "Kind regards,\nMruddual Sojitra")


def _mailbox(db):
    for i in range(5):
        _m(db, f"r-in{i}", f"rc{i}", "inbox", RESCHEDULE_IN, 20 - i)
        _m(db, f"r-out{i}", f"rc{i}", "sent", RESCHEDULE_OUT, 20 - i, minutes=30)
        _m(db, f"d-in{i}", f"dc{i}", "inbox", DOUBT_IN, 30 - i, frm="tom@cafe.de")
        _m(db, f"d-out{i}", f"dc{i}", "sent", DOUBT_OUT, 30 - i, minutes=90, to=("tom@cafe.de",))
        _m(db, f"o{i}", f"oc{i}", "sent", "Dear Team,\n\nI hope you are doing well.\n\nMy name is Mruddual Sojitra.\n\nKind regards,\nMruddual Sojitra",
           40 - i, subject="Settleezy x Venue | Partnership Proposal")
    _m(db, "auto", "ac", "sent", "Accepted", 3, subject="Accepted: Call")
    db.conn.commit()


def test_classify_and_moves():
    assert scribe.classify(RESCHEDULE_IN)[0] == "reschedule_request"
    assert scribe.classify(DOUBT_IN)[0] == "doubt"
    assert scribe.classify("Hallo, leider haben wir kein Interesse.")[0] == "decline"
    assert scribe.classify("We made some changes to the agreement, please see the corrections.")[0] == "contract_changes"
    mv = scribe.moves_in(RESCHEDULE_OUT)
    assert {"thank_quick", "propose_day", "booking_link"} <= set(mv)
    assert scribe.questions_in(DOUBT_IN) == ["how does the discount work exactly?", "Is there a commission for us?"]
    body, so, sig = scribe.split_signature(RESCHEDULE_OUT)
    assert so == "Kind regards," and sig.startswith("Mruddual") and "Kind regards" not in body


def test_learn_builds_style_and_decision_profile(cfg, db):
    cfg.raw.setdefault("me", {})["emails"] = [ME]
    _mailbox(db)
    r = scribe.learn(cfg, db, days=90)
    assert r["emails"] == 15 and r["situations"] == {"reschedule_request": 5, "doubt": 5, "first_outreach": 5}
    prof = scribe.profile(cfg)
    en = prof["style"]["en"]
    assert en["sign_offs"][0][0] == "Kind regards," and "I hope you are doing well." in en["openers"]
    assert en["quirks"] == {"Thankyou": 5}
    d = prof["decisions"]["reschedule_request"]
    assert d["moves"]["propose_day"] == 1.0 and d["moves"]["booking_link"] == 1.0 and d["median_hours_to_reply"] == 0.5
    pol = scribe.policy(cfg, "doubt")
    assert pol["source"].startswith("learned") and {"answer_points", "offer_visit"} <= set(pol["moves"])
    assert db.one("SELECT COUNT(*) n FROM scribe_examples")["n"] == 15


def test_plan_and_draft_offline_like_the_founder(cfg, db):
    cfg.raw.setdefault("me", {})["emails"] = [ME]
    cfg.raw["me"]["name"] = "Mruddual Sojitra"
    cfg.raw.setdefault("scribe", {})["booking_link"] = "https://calendly.com/settleezy/discovery"
    _mailbox(db)
    scribe.learn(cfg, db, days=90)
    out = scribe.draft_reply(cfg, db, incoming=RESCHEDULE_IN, sender_name="Lea Brandt", record=False,
                             llm=type("NoLLM", (), {"cloud_enabled": False, "local": lambda *a, **k: ""})())
    pl, text = out["plan"], out["text"]
    assert pl["intent"] == "reschedule_request" and pl["register"] == "quick" and pl["policy_source"].startswith("learned")
    assert text.startswith("Hi Lea,")          # she wrote "Hi": mirrored, like the founder does
    assert "I hope you are doing well" not in text   # quick register: straight to the point
    assert "calendly.com/settleezy/discovery" in text and text.rstrip().endswith("Kind regards,\nMruddual Sojitra")
    assert "Thankyou" not in text            # spelling quirks are fixed unless scribe.keep_quirks = true
    d = scribe.draft_reply(cfg, db, incoming=DOUBT_IN, sender_name="Tom", record=False,
                           llm=type("NoLLM", (), {"cloud_enabled": False, "local": lambda *a, **k: ""})())
    assert d["plan"]["register"] == "formal" and "[[CHECK" in d["text"] and "commission" in d["text"]


def test_follow_up_ladder_and_empathy(cfg, db):
    p3 = scribe.plan(cfg, db, "", kind="follow_up", followup_number=3)
    assert p3["moves"][0] == "deadline" and "third time" in p3["notes"]
    p1 = scribe.plan(cfg, db, "", kind="follow_up", followup_number=1)
    assert "deadline" not in p1["moves"]
    sick = scribe.plan(cfg, db, "Sorry for the late reply, I was sick last week. Can we reschedule?")
    assert sick["moves"][0] == "empathy"


def test_check_fixes_greeting_signoff_and_banned(cfg):
    cfg.raw.setdefault("me", {})["name"] = "Mruddual Sojitra"
    pl = {"lang": "en", "facts": {"greeting": "Dear Lea,"}, "target_words": 40}
    r = scribe.check(cfg, "Hi Lea,\n\nLet's touch base — tomorrow works.\n\nCheers,\nM", pl)
    assert r["text"].startswith("Dear Lea,") and "touch base" not in r["text"] and "—" not in r["text"]
    assert r["text"].endswith("Kind regards,\nMruddual Sojitra")


def test_learns_from_edits(cfg, db):
    cfg.raw.setdefault("me", {})["emails"] = [ME]
    scribe._ensure(db)
    for i in range(3):
        db.x("INSERT INTO scribe_drafts(conversation_id,intent,moves,lang,draft,created_at) VALUES(?,?,?,?,?,?)",
             (f"e{i}", "doubt", "[]", "en", "Dear Tom,\n\nThank you so much for reaching out to us.\n\nIt works like this.\n\nKind regards,\nM",
              _ts(2)))
        _m(db, f"s{i}", f"e{i}", "sent", "Dear Tom,\n\nIt works like this.\n\nKind regards,\nM", 1)
    db.conn.commit()
    assert scribe.compare_sent(cfg, db) == 3
    fb = scribe.feedback_summary(db)
    assert fb["acceptance"]["doubt"]["n"] == 3 and "Thank you so much for reaching out to us." in fb["deleted_phrases"]["en"]


def test_seed_is_used_when_mailbox_is_thin(cfg, db):
    (cfg.data_dir / "style_seed.json").write_text(json.dumps({
        "greetings": {"en": {"first": "Dear {name},", "ongoing": "Hello {name},"}},
        "decisions": {"decline": {"moves": ["thank_quick", "clarify_question", "keep_door_open"], "notes": "Ask what changed."}}}),
        encoding="utf-8")
    pol = scribe.policy(cfg, "decline")
    assert pol["source"] == "from the analysis of your mail" and "clarify_question" in pol["moves"]
    assert scribe.choose_greeting(cfg, "en", True, "", "", "Jan Meyer") == "Dear Jan,"


def test_api_try_and_report(cfg, db):
    from fastapi.testclient import TestClient

    from settleezy_cofounder.dashboard.app import app

    c = TestClient(app)
    assert c.post("/api/scribe/try", json={"text": RESCHEDULE_IN}).status_code == 403
    r = c.post("/api/scribe/try", json={"text": RESCHEDULE_IN, "sender": "Lea"}, headers={"X-SZ": "1"}).json()
    assert r["plan"]["intent"] == "reschedule_request" and r["text"]
    rep = c.get("/api/scribe").json()
    assert any(d["intent"] == "reschedule_request" for d in rep["decisions"])
