import random
from datetime import datetime, timedelta, timezone

import pytest

from settleezy_cofounder import brain, pipeline
from settleezy_cofounder.leads import upsert


@pytest.fixture()
def hcfg(cfg):
    cfg.raw.setdefault("brain", {})["embedder"] = "hash"   # offline, deterministic
    return cfg


def ago(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).replace(microsecond=0).isoformat()


def test_hash_embedder_is_normalised_and_similar_texts_are_close():
    a, b, c = (brain._hash_embed(t) for t in ("Lea prefers WhatsApp", "Lea prefers WhatsApp messages", "Kino Babylon renewal"))
    assert abs(brain._dot(a, a) - 1) < 1e-6
    assert brain._dot(a, b) > 0.6 > brain._dot(a, c)


def test_remember_recall_dedupe_and_forget(hcfg, db):
    i1 = brain.remember(hcfg, db, "Lea from Brew Lab prefers WhatsApp over email", kind="preference", subject="Brew Lab")
    i2 = brain.remember(hcfg, db, "Lea from Brew Lab prefers WhatsApp over email.", kind="preference", subject="Brew Lab", importance=0.9)
    assert i1 == i2                                                    # near-duplicate updates, doesn't duplicate
    brain.remember(hcfg, db, "Universities plan intake support in May and November", kind="insight", subject="universities")
    brain.remember(hcfg, db, "Kino Babylon renewal is due in December", subject="Kino Babylon")
    top = brain.recall(hcfg, db, "how do I reach Brew Lab?", 2)
    assert top[0]["subject"] == "Brew Lab" and top[0]["importance"] == 0.9
    assert brain.recall(hcfg, db, "when should I pitch universities about intake", 1)[0]["subject"] == "universities"
    assert db.one("SELECT uses FROM memories WHERE id=?", (i1,))["uses"] == 1
    with pytest.raises(ValueError):
        brain.remember(hcfg, db, "  ")
    assert brain.forget(db, i1) and not brain.forget(db, i1)
    assert brain.stats(db)["memories"] == 2


def test_learn_ingests_partners_and_events(hcfg, db):
    from settleezy_cofounder.leads import set_status
    from settleezy_cofounder.ops import update_partner

    lid, _ = upsert(db, "Café Kranz", "merchant", email="a@kranz.de")
    set_status(db, lid, "replied")
    set_status(db, lid, "partner")
    pid = db.one("SELECT id FROM partners WHERE lead_id=?", (lid,))["id"]
    update_partner(db, pid, offer="10% off all drinks", notes="Anna wants a monthly redemption report")
    out = brain.learn(hcfg, db)
    assert out["memories_refreshed"] > 5 and isinstance(out["lead_model"], str)   # too little history to train
    again = brain.learn(hcfg, db)["memories_refreshed"]
    n = brain.stats(db)["memories"]
    brain.learn(hcfg, db)
    assert brain.stats(db)["memories"] == n and again == out["memories_refreshed"]   # idempotent
    hits = brain.recall(hcfg, db, "What does Café Kranz offer members?", 3)
    assert any("10% off all drinks" in h["text"] for h in hits)
    g = brain.graph(hcfg, db)
    assert any(n["id"] == "s:Café Kranz" for n in g["nodes"]) and g["links"]


def test_leadnet_learns_a_real_signal_and_ranks_sequences(hcfg, db):
    rng = random.Random(1)
    for i in range(60):
        has_ig = i % 2 == 0
        lid, _ = upsert(db, f"Venue {i}", "merchant", category="cafe", instagram="v" if has_ig else "", website="https://x.de")
        db.x("INSERT INTO lead_events(lead_id,from_status,to_status,at) VALUES(?,?,?,?)", (lid, "new", "contacted", ago(20)))
        db.x("UPDATE leads SET status='contacted', stage_changed_at=? WHERE id=?", (ago(20), lid))
        if rng.random() < (0.7 if has_ig else 0.1):      # leads with Instagram reply far more often
            db.x("INSERT INTO lead_events(lead_id,from_status,to_status,at) VALUES(?,?,?,?)", (lid, "contacted", "replied", ago(15)))
            db.x("UPDATE leads SET status='replied' WHERE id=?", (lid,))
    net = brain.LeadNet.train_from_db(db)
    assert net is not None and net.meta["auc"] >= 0.65 and net.meta["top_signals"][0] == "has Instagram"
    with_ig = {"kind": "merchant", "category": "cafe", "instagram": "x", "website": "y", "score": 50}
    without = {**with_ig, "instagram": ""}
    assert brain.reply_chance(db, with_ig) > brain.reply_chance(db, without)
    # restored from storage, same predictions
    assert abs(brain.LeadNet.from_json(db.kv_get("brain:leadnet")).predict(brain.features(with_ig)) - brain.reply_chance(db, with_ig)) < 1e-3
    # new leads: the one with Instagram gets the outreach sequence first even with a slightly lower score
    a, _ = upsert(db, "New With IG", "merchant", category="cafe", instagram="x", website="https://a.de")
    b, _ = upsert(db, "New No IG", "merchant", category="cafe", website="https://b.de", email="b@b.de")
    db.x("UPDATE leads SET score=60 WHERE id=?", (a,))
    db.x("UPDATE leads SET score=64 WHERE id=?", (b,))
    hcfg.raw.setdefault("pipeline", {})["new_sequences_per_week"] = 1
    assert pipeline.auto_start(hcfg, db)[0]["name"] == "New With IG"
    assert pipeline.board(db)[0]["reply_chance"] is not None


def test_auc():
    assert brain.auc([1, 0, 1, 0], [0.9, 0.1, 0.8, 0.2]) == 1.0
    assert brain.auc([1, 1], [0.5, 0.6]) is None
