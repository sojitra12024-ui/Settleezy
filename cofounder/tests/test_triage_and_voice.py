from datetime import datetime, timedelta, timezone

from settleezy_cofounder.triage import followups_due, needs_reply
from settleezy_cofounder.voice_profile import compute_stats

ME = "me@settleezy.de"


def iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).replace(microsecond=0).isoformat()


def msg(db, id, conv, folder, frm, to, body, days_ago, subject="Partnership", automated=0, lang="en"):
    db.upsert_message({
        "id": id, "conversation_id": conv, "folder": folder, "from_addr": frm, "from_name": frm.split("@")[0],
        "to_addrs": [to], "cc_addrs": [], "subject": subject, "body_text": body, "sent_at": iso(days_ago),
        "is_read": 1, "language": lang, "automated": automated,
    })
    db.conn.commit()


def test_needs_reply_and_followups(cfg, db):
    # partner asked a question -> reply owed
    msg(db, "a1", "c1", "inbox", "anna@cafe.de", ME, "Could you send the student discount details?", 1)
    # newsletter -> ignored
    msg(db, "n1", "c2", "inbox", "news@tool.io", ME, "Our new features?", 1, automated=1)
    # I answered already -> nothing owed
    msg(db, "b1", "c3", "inbox", "ben@gym.de", ME, "Can we talk?", 3)
    msg(db, "b2", "c3", "sent", ME, "ben@gym.de", "Sure, Thursday?", 2)
    # my outreach, 5 days quiet -> follow-up #1 due
    msg(db, "o1", "c4", "sent", ME, "lea@bar.de", "Would you be interested in a student deal?", 5)
    # my outreach, 2 days quiet -> not yet
    msg(db, "o2", "c5", "sent", ME, "tom@spa.de", "Would you be interested?", 2)

    replies = needs_reply(cfg, db, llm=None)
    assert [r.conversation_id for r in replies] == ["c1"]
    assert db.one("SELECT COUNT(*) n FROM triage")["n"] == 0   # heuristic guesses are not cached
    fu = followups_due(cfg, db)
    assert [(f.conversation_id, f.followup_number) for f in fu] == [("c4", 1)]


def test_followup_cadence_counts_previous_followups(cfg, db):
    msg(db, "o1", "c9", "sent", ME, "lea@bar.de", "Would you be interested?", 20)
    msg(db, "o2", "c9", "sent", ME, "lea@bar.de", "Just checking in - would you be interested?", 8)
    fu = followups_due(cfg, db)
    assert fu and fu[0].followup_number == 2   # 8 days >= cadence[1] = 7


def test_outreach_stats(cfg, db):
    msg(db, "o1", "k1", "sent", ME, "a@x.de", "Hi Anna,\nWould you like to partner?\nBest,\nMe", 10)
    msg(db, "r1", "k1", "inbox", "a@x.de", ME, "Yes!", 8)
    msg(db, "o2", "k2", "sent", ME, "b@y.de", "Hallo Ben,\nhättest du Interesse?\nViele Grüße\nMe", 9, lang="de")
    stats = compute_stats(db, {ME})
    assert stats["outreach"]["threads"] == 2
    assert stats["outreach"]["reply_rate"] == 0.5
    assert stats["outreach"]["median_days_to_reply"] == 2.0
    assert stats["style"]["en"]["top_sign_offs"][0][0] == "Best,"
    assert stats["style"]["de"]["du_vs_sie"]["du"] == 1
