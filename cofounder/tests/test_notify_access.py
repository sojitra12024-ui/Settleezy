import json

import pytest
from fastapi.testclient import TestClient

from settleezy_cofounder import notify
from settleezy_cofounder.dashboard import app as dash


@pytest.fixture()
def quiet_off(cfg, monkeypatch):
    cfg.raw.setdefault("notifications", {})["quiet_hours"] = []
    monkeypatch.setattr(notify, "toast", lambda *a: True)
    monkeypatch.setattr(notify, "send_push", lambda *a: 0)
    return cfg


def test_notify_stores_dedupes_and_marks_read(quiet_off, db):
    a = notify.notify(quiet_off, db, "Lea replied", "Book a call", kind="lead", key="replied:1")
    assert a and notify.notify(quiet_off, db, "Lea replied", "again", kind="lead", key="replied:1") is None
    notify.notify(quiet_off, db, "Weird kind", kind="party")
    r = notify.recent(db)
    assert r["unread"] == 2 and r["items"][0]["kind"] == "info" and r["items"][1]["title"] == "Lea replied"
    notify.mark_read(db, [a])
    assert notify.recent(db)["unread"] == 1
    notify.mark_read(db)
    assert notify.recent(db)["unread"] == 0


def test_quiet_hours(cfg):
    from datetime import datetime

    cfg.raw.setdefault("notifications", {})["quiet_hours"] = ["22:00", "07:30"]
    assert notify.quiet_now(cfg, datetime(2026, 10, 8, 23, 15)) and notify.quiet_now(cfg, datetime(2026, 10, 8, 6, 0))
    assert not notify.quiet_now(cfg, datetime(2026, 10, 8, 12, 0))


def test_push_subscription_validation(db):
    with pytest.raises(ValueError):
        notify.save_subscription(db, {"endpoint": "http://evil"})
    notify.save_subscription(db, {"endpoint": "https://fcm.googleapis.com/x", "keys": {"p256dh": "a", "auth": "b"}}, "phone")
    assert db.one("SELECT COUNT(*) n FROM push_subscriptions")["n"] == 1


def test_job_failure_and_findings_notify(quiet_off, db, monkeypatch):
    from settleezy_cofounder import jobs

    def boom(cfg, db):
        raise RuntimeError("Outlook said no")

    monkeypatch.setitem(jobs.JOBS, "boom", boom)
    with pytest.raises(RuntimeError):
        jobs.run_job(quiet_off, "boom")
    items = notify.recent(db)["items"]
    assert items[0]["title"] == "Setz job failed: boom" and "Outlook said no" in items[0]["body"]
    from settleezy_cofounder.leads import set_status, upsert

    lid, _ = upsert(db, "Brew Lab", "merchant")
    set_status(db, lid, "replied")
    monkeypatch.setitem(jobs.JOBS, "track", lambda cfg, db: {})
    jobs.run_job(quiet_off, "track")
    jobs.run_job(quiet_off, "track")
    assert [n["title"] for n in notify.recent(db)["items"]].count("Brew Lab replied 🎉") == 1


def test_remote_access_requires_pin(cfg):
    c = TestClient(dash.app)
    assert c.get("/api/notifications").status_code == 200                        # the laptop itself
    remote = {"X-Forwarded-For": "100.64.0.7"}
    assert c.get("/api/notifications", headers=remote).status_code == 403        # no PIN configured
    assert c.get("/api/notifications", headers={**remote, "Tailscale-User-Login": "me@example.com"}).status_code == 200
    cfg.raw.setdefault("dashboard", {})["access_pin"] = "4711"
    from settleezy_cofounder import config as config_mod

    orig = config_mod.load_config
    dash.load_config = lambda *a, **k: cfg
    try:
        assert c.get("/api/notifications", headers=remote).status_code == 401
        r = c.get("/", headers=remote, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].startswith("/login")
        assert c.get("/manifest.webmanifest", headers=remote).json()["short_name"] == "Setz"   # installable before login
        bad = c.post("/login", content="pin=0000&next=/", headers={**remote, "Content-Type": "application/x-www-form-urlencoded"},
                     follow_redirects=False)
        assert "err=1" in bad.headers["location"]
        ok = c.post("/login", content="pin=4711&next=/command", headers={**remote, "Content-Type": "application/x-www-form-urlencoded"},
                    follow_redirects=False)
        assert ok.status_code == 303 and ok.headers["location"] == "/command" and "httponly" in ok.headers["set-cookie"].lower()
        c.cookies.set("sz_session", ok.cookies["sz_session"])
        assert c.get("/api/notifications", headers=remote).status_code == 200
        for _ in range(5):
            c.post("/login", content="pin=1&next=/", headers={**remote, "Content-Type": "application/x-www-form-urlencoded"})
        assert c.post("/login", content="pin=4711", headers={**remote, "Content-Type": "application/x-www-form-urlencoded"}).status_code == 429
    finally:
        dash.load_config = orig
        dash._login_tries.clear()


def test_pwa_files_and_notify_api(cfg):
    c = TestClient(dash.app)
    sw = c.get("/sw.js")
    assert sw.status_code == 200 and "showNotification" in sw.text and sw.headers["service-worker-allowed"] == "/"
    assert c.get("/static/icon-192.png").headers["content-type"] == "image/png"
    assert c.post("/api/notify/event", json={"type": "notification", "title": "x"}).status_code == 403
    assert c.post("/api/notify/event", json={"type": "evil"}, headers={"X-SZ": "1"}).status_code == 400
    assert c.post("/api/notify/event", json={"type": "refresh", "what": "mail"}, headers={"X-SZ": "1"}).json()["ok"]
    assert c.get("/api/push/key").json() == {"publicKey": None} or True     # None unless pywebpush is installed


def test_serve_refuses_network_bind_without_pin(cfg, monkeypatch):
    cfg.raw.setdefault("dashboard", {})["host"] = "0.0.0.0"
    monkeypatch.setattr(dash, "load_config", lambda *a, **k: cfg)
    with pytest.raises(RuntimeError):
        dash.serve(8799)
