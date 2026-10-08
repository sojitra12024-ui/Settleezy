import json
from datetime import date, datetime, timedelta, timezone

from fastapi.testclient import TestClient

from settleezy_cofounder import agents, ops
from settleezy_cofounder.leads import set_status, upsert


def ago(days: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).replace(microsecond=0).isoformat()


def test_team_reports_findings_and_setz_creates_todos(cfg, db):
    lid, _ = upsert(db, "Pho Saigon", "merchant", email="hi@pho.de")
    set_status(db, lid, "replied")
    pid = ops.add_partner(db, "Kino Babylon", "venue", status="live")
    db.x("UPDATE partners SET last_contact_at=?, live_at=? WHERE id=?", (ago(60), (date.today() - timedelta(days=50)).isoformat(), pid))
    for i in range(10):
        db.metric("manual", "paying_members", 100 + 5 * i, (date.today() - timedelta(days=9 - i)).isoformat())

    res = agents.run(cfg, db, publish_events=False)
    by = {a["agent"]: a for a in res["agents"]}
    assert set(by) == set(agents.AGENTS) and all(a["status"] != "error" for a in by.values()), by
    assert by["hunter"]["status"] == "alert" and by["atlas"]["status"] == "alert"

    reps = agents.latest(db)
    quant_goal = next(f for f in reps["quant"]["findings"] if f["key"] == "goal:manual.paying_members")
    assert "on track for" in quant_goal["detail"]

    setz = res["setz"]
    assert setz["status"] == "alert" and setz["counts"]["alert"] >= 2
    assert setz["priorities"][0]["severity"] == "alert" and setz["briefing"].startswith("Setz here.")
    todos = [t["title"] for t in ops.tasks(db)]
    assert "Book a call with Pho Saigon" in todos and "Check in with Kino Babylon" in todos
    assert agents.run(cfg, db, publish_events=False)["setz"]["todos_created"] == 0      # no duplicates on rerun
    assert agents.activity(db, 10)[0]["agent"] in agents.AGENTS


def test_single_agent_failure_is_contained(cfg, db, monkeypatch):
    def boom(cfg, db, rep):
        raise RuntimeError("kaputt")

    monkeypatch.setattr(agents.AGENTS["nova"], "fn", boom)
    res = agents.run(cfg, db, only=["nova", "chrono"], publish_events=False)
    st = {a["agent"]: a for a in res["agents"]}
    assert st["nova"]["status"] == "error" and "kaputt" in st["nova"]["summary"] and st["chrono"]["status"] != "error"


def test_forecast():
    s = [((date.today() - timedelta(days=9 - i)).isoformat(), 100 + 10 * i) for i in range(10)]
    assert agents._forecast(s, 300).startswith("on track for")
    assert agents._forecast([(d, 5) for d, _ in s], 300) == "not growing at the current rate"
    assert agents._forecast(s[:3], 300) is None


def test_command_center_endpoints(cfg):
    from settleezy_cofounder.dashboard.app import app

    c, h = TestClient(app), {"X-SZ": "1"}
    assert "SETZ" in c.get("/command").text
    a = c.get("/api/agents").json()
    assert len(a["agents"]) == 9 and a["reports"] == {}
    assert c.post("/api/agents/run").status_code == 403
    assert c.post("/api/agents/event", json={"agent": "atlas", "status": "ok", "summary": "x"}, headers=h).json()["ok"]
    from settleezy_cofounder.db import DB

    with DB(cfg.db_path) as db:
        agents.run(cfg, db, publish_events=False)
    a = c.get("/api/agents").json()
    assert set(a["reports"]) == set(agents.AGENTS) and a["setz"]["briefing"] and a["activity"]
