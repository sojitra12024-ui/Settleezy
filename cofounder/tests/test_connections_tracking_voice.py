import array
import io
import json
import math
import wave
from datetime import datetime, timedelta, timezone

import httpx
from fastapi.testclient import TestClient

from settleezy_cofounder import connections, tracking, tts
from settleezy_cofounder.leads import upsert

ME = "me@settleezy.de"


def iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).replace(microsecond=0).isoformat()


def msg(db, id, conv, folder, frm, to, body, days_ago, automated=0):
    db.upsert_message({"id": id, "conversation_id": conv, "folder": folder, "from_addr": frm, "from_name": "", "to_addrs": [to],
                       "cc_addrs": [], "subject": "S", "body_text": body, "sent_at": iso(days_ago), "is_read": 1,
                       "language": "en", "automated": automated})
    db.conn.commit()


# -- tts ------------------------------------------------------------------

def test_envelope_follows_loudness_and_wav_is_valid():
    sr = tts.SAMPLE_RATE
    quiet = [int(500 * math.sin(i / 5)) for i in range(sr // 2)]
    loud = [int(20000 * math.sin(i / 5)) for i in range(sr // 2)]
    pcm = array.array("h", quiet + loud).tobytes()
    env = tts.envelope(pcm, sr, 40)
    assert 20 <= len(env) <= 30
    assert max(env) == 1.0 and env[2] < 0.2 < env[-3]
    with wave.open(io.BytesIO(tts.wav_bytes(pcm, sr))) as w:
        assert w.getframerate() == sr and w.getnframes() == sr


def test_elevenlabs_disabled_without_key(cfg):
    assert not tts.elevenlabs_enabled(cfg)
    assert tts.voice_id(cfg) == "CUvmi6RSy4BQr6vnMyEw"
    assert tts.voice_id(cfg, alt=True) == "r1KmysJdVYZjJCm4mL3b"


# -- connections ----------------------------------------------------------

def test_doctor_reports_every_integration_with_a_fix(cfg, db, monkeypatch):
    for k in ("MS_CLIENT_ID", "IG_ACCESS_TOKEN", "IG_USER_ID", "CALENDLY_TOKEN", "ELEVENLABS_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    cfg.raw["llm"]["ollama_url"] = "http://127.0.0.1:9"     # nothing listens there
    results = connections.run_all(cfg, db)
    by = {r["name"]: r for r in results}
    assert set(by) == {"Outlook", "Instagram", "Calendly", "Claude API", "Local model (Ollama)", "Voice (ElevenLabs)"}
    assert all(not r["ok"] and r["fix"] for r in results)
    assert "sz auth outlook" in by["Outlook"]["fix"] and "sz auth instagram" in by["Instagram"]["fix"]
    assert by["Calendly"]["optional"] and by["Voice (ElevenLabs)"]["optional"]
    assert json.loads(db.kv_get("connections"))["results"] == results


def test_instagram_setup_gets_page_token_and_ig_id():
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/oauth/access_token"):
            assert req.url.params["fb_exchange_token"] == "SHORT"
            return httpx.Response(200, json={"access_token": "LONG"})
        if req.url.path.endswith("/me/accounts"):
            assert req.url.params["access_token"] == "LONG"
            return httpx.Response(200, json={"data": [
                {"name": "Other page", "access_token": "P0"},
                {"name": "Settleezy", "access_token": "PAGE", "instagram_business_account": {"id": "1784", "username": "settleezy"}},
            ]})
        return httpx.Response(404)

    res = connections.instagram_setup("SHORT", "app", "secret", httpx.Client(transport=httpx.MockTransport(handler)))
    assert res == {"IG_ACCESS_TOKEN": "PAGE", "IG_USER_ID": "1784", "username": "settleezy", "page": "Settleezy"}


def test_write_env_updates_and_appends(tmp_path):
    p = tmp_path / ".env"
    p.write_text("# keep\nIG_ACCESS_TOKEN=old\nOTHER=1\n", encoding="utf-8")
    connections.write_env(p, {"IG_ACCESS_TOKEN": "new", "IG_USER_ID": "42"})
    assert p.read_text(encoding="utf-8") == "# keep\nIG_ACCESS_TOKEN=new\nOTHER=1\nIG_USER_ID=42\n"


# -- tracking -------------------------------------------------------------

def test_draft_sent_and_replied_are_detected_and_move_the_lead(cfg, db):
    lid, _ = upsert(db, "Café Kranz", "merchant", email="hi@kranz.de")
    db.x("INSERT INTO drafts(kind,lead_id,conversation_id,graph_draft_id,subject,created_at) VALUES('outreach',?,?,?,?,?)",
         (lid, "c1", "d1", "Hi", iso(3)))
    assert tracking.track_drafts(cfg, db) == {"newly_sent": 0, "newly_replied": 0}
    msg(db, "s1", "c1", "sent", ME, "hi@kranz.de", "Hi!", 2)
    assert tracking.track_drafts(cfg, db)["newly_sent"] == 1
    assert db.one("SELECT status FROM leads WHERE id=?", (lid,))["status"] == "contacted"
    msg(db, "r1", "c1", "inbox", "hi@kranz.de", ME, "Yes, let's talk", 1)
    assert tracking.track_drafts(cfg, db)["newly_replied"] == 1
    assert db.one("SELECT status FROM leads WHERE id=?", (lid,))["status"] == "replied"
    assert tracking.draft_funnel(db)["outreach"] == {"drafted": 1, "sent": 1, "replied": 1}


def test_snapshot_kpis_and_goals(cfg, db):
    msg(db, "a", "c1", "inbox", "x@y.de", ME, "Can you help?", 0.5)
    msg(db, "b", "c1", "sent", ME, "x@y.de", "Sure", 0.4)
    from settleezy_cofounder import ops
    from settleezy_cofounder.leads import set_status

    lid, _ = upsert(db, "Uni X", "university")
    set_status(db, lid, "partner")                      # creates the partner record (onboarding)
    pid = db.one("SELECT id FROM partners WHERE lead_id=?", (lid,))["id"]
    ops.update_partner(db, pid, stage="live")
    values = tracking.snapshot(cfg, db)
    assert values["partners_university"] == 1 and values["partners_live_university"] == 1
    assert values["response_hours_median_7d"] == 2.4
    tracking.record_kpi(db, "paying_members", 250)
    g = {x["label"]: x for x in tracking.goals(cfg, db)}
    assert g["Paying members"]["current"] == 250 and g["Paying members"]["pct"] == 25.0
    assert g["Buddy platform universities"]["current"] == 1


# -- dashboard ------------------------------------------------------------

def test_dashboard_kpi_leads_metric_and_voice(cfg, monkeypatch):
    from settleezy_cofounder.dashboard import app as appmod

    c = TestClient(appmod.app)
    h = {"X-SZ": "1"}
    assert c.post("/api/kpi", json={"key": "paying_members", "value": 120}, headers=h).status_code == 200
    assert c.post("/api/kpi", json={"key": "nope", "value": 1}, headers=h).status_code == 400
    assert c.get("/api/metric", params={"key": "manual.paying_members"}).json()["series"][0][1] == 120
    assert c.post("/api/leads", json={"name": "Späti Ost", "kind": "merchant", "category": "grocery"}, headers=h).json()["created"]
    assert c.get("/api/leads", params={"q": "späti"}).json()[0]["name"] == "Späti Ost"
    s = c.get("/api/summary").json()
    assert s["kpi_latest"]["paying_members"] == 120 and s["goals"][0]["current"] == 120

    assert c.post("/api/voice/event", json={"state": "speaking", "envelope": [0.1, 0.9]}).status_code == 403
    assert c.post("/api/voice/event", json={"state": "dancing"}, headers=h).status_code == 400
    assert c.post("/api/voice/event", json={"state": "speaking", "text": "Hi", "envelope": [0.1, 0.9]}, headers=h).json()["ok"]
    assert appmod._voice_state["state"] == "speaking" and appmod._voice_state["envelope"] == [0.1, 0.9]

    monkeypatch.setattr("settleezy_cofounder.voice.answer", lambda cfg, text, lang: f"answer to {text} ({lang})")
    r = c.post("/api/ask", json={"text": "Was steht heute an?", "speak": True}, headers=h).json()
    assert r == {"answer": "answer to Was steht heute an? (de)"}          # no ElevenLabs key -> text only
    assert "Setz" in c.get("/hologram").text and "class Hologram" in c.get("/static/hologram.js").text
    hub = c.get("/api/hub").json()
    assert [x["key"] for x in hub][:3] == ["outlook", "instagram", "calendar"] and all("icon" in x for x in hub)
    assert c.get("/static/..%2Fapp.py").status_code == 404
