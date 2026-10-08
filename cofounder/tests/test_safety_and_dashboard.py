from fastapi.testclient import TestClient

from settleezy_cofounder import msgraph
from settleezy_cofounder.brief import render, spoken


def test_never_requests_send_permission():
    assert all("send" not in s.lower() for s in msgraph.SCOPES)


def test_dashboard_empty_db_and_csrf_guard(cfg):
    from settleezy_cofounder.dashboard.app import app

    c = TestClient(app)
    r = c.get("/api/summary")
    assert r.status_code == 200
    body = r.json()
    assert body["replies"] == [] and body["leads_top"] == []
    assert c.post("/api/run/brief").status_code == 403
    assert c.post("/api/leads/1/status", json={"status": "contacted"}).status_code == 403
    assert "Settleezy HQ" in c.get("/").text


def test_brief_render_without_data():
    data = {"date": "2026-10-08", "meetings": [], "replies": [], "followups": [], "drafts_created_today": 0,
            "instagram": {}, "instagram_comments": [], "new_listings": [], "top_new_leads": [], "pipeline": {}}
    md = render(data, "1. Call Café Kranz (new on Groupon)")
    assert "No meetings." in md and "Call Café Kranz" in md
    assert spoken(data, "1. Call Café Kranz (new on Groupon)").endswith("Top priority: Call Café Kranz.")
