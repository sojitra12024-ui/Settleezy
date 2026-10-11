"""Regression tests for the dashboard review fixes (access control, login, validation, live stream, pages)."""

import asyncio
import re
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from settleezy_cofounder.dashboard import app as dash

STATIC = Path(dash.__file__).parent / "static"
H = {"X-SZ": "1"}
FORM = {"Content-Type": "application/x-www-form-urlencoded"}


@pytest.fixture()
def client(cfg):
    yield TestClient(dash.app)
    dash._login_tries.clear()


@pytest.fixture()
def pinned(cfg, monkeypatch):
    cfg.raw.setdefault("dashboard", {})["access_pin"] = "4711"
    monkeypatch.setattr(dash, "load_config", lambda *a, **k: cfg)
    return cfg


# -- access control ---------------------------------------------------------------------------------------------

def test_dns_rebinding_host_rejected(client, cfg, monkeypatch):
    assert client.get("/api/notifications").status_code == 200
    for host in ("127.0.0.1:8765", "localhost:8765", "[::1]:8765", "LOCALHOST"):
        assert client.get("/api/notifications", headers={"Host": host}).status_code == 200, host
    assert client.get("/api/notifications", headers={"Host": "attacker.example"}).status_code == 421
    assert client.get("/", headers={"Host": "attacker.example:8765"}).status_code == 421
    cfg.raw.setdefault("dashboard", {}).update(allowed_hosts=["setz.lan"], tailscale_hostname="laptop.tail1234.ts.net")
    monkeypatch.setattr(dash, "load_config", lambda *a, **k: cfg)
    for host in ("setz.lan", "laptop.tail1234.ts.net", "laptop.tail1234.ts.net:443"):
        assert client.get("/api/notifications", headers={"Host": host}).status_code == 200, host


def test_tailscale_header_only_trusted_from_loopback_peer(cfg):
    remote = {"X-Forwarded-For": "100.64.0.7", "Tailscale-User-Login": "me@example.com"}
    assert TestClient(dash.app).get("/api/notifications", headers=remote).status_code == 200      # via tailscale serve
    lan = TestClient(dash.app, client=("192.168.1.50", 5000))
    assert lan.get("/api/notifications", headers=remote).status_code == 403                         # forged by a LAN device


def test_non_ascii_cookie_and_pin_do_not_crash(client, pinned):
    r = client.get("/api/notifications", headers={"X-Forwarded-For": "1.2.3.4", "Cookie": "sz_session=ü".encode()})
    assert r.status_code == 401
    r = client.post("/login", content="pin=%C3%BC&next=/", headers={**FORM, "X-Forwarded-For": "1.2.3.4"}, follow_redirects=False)
    assert r.status_code == 303 and "err=1" in r.headers["location"]


def test_login_redirect_keeps_query_string(client, pinned):
    r = client.get("/hologram?mini=1", headers={"X-Forwarded-For": "1.2.3.4"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login?next=%2Fhologram%3Fmini%3D1"
    page = client.get("/login?next=%2Fhologram%3Fmini%3D1", headers={"X-Forwarded-For": "1.2.3.4"}).text
    assert 'value="/hologram?mini=1"' in page
    ok = client.post("/login", content="pin=4711&next=%2Fhologram%3Fmini%3D1", headers={**FORM, "X-Forwarded-For": "1.2.3.4"},
                     follow_redirects=False)
    assert ok.headers["location"] == "/hologram?mini=1"


def test_login_rate_limit_ignores_spoofed_forwarded_for(cfg, pinned):
    lan = TestClient(dash.app, client=("192.168.1.50", 5000))   # not a local proxy: its X-Forwarded-For is not trusted
    try:
        codes = [lan.post("/login", content=f"pin={i:04d}", headers={**FORM, "X-Forwarded-For": f"10.0.0.{i}"},
                          follow_redirects=False).status_code for i in range(7)]
        assert codes[:5] == [303] * 5 and codes[5:] == [429, 429]
        assert set(dash._login_tries) == {"192.168.1.50"}
    finally:
        dash._login_tries.clear()


def test_login_rate_limit_via_local_proxy_uses_last_hop_and_global_cap(client, pinned):
    r = client.post("/login", content="pin=0", headers={**FORM, "X-Forwarded-For": "6.6.6.6, 100.64.0.9"}, follow_redirects=False)
    assert r.status_code == 303 and set(dash._login_tries) == {"100.64.0.9"}
    dash._login_tries.clear()
    codes = [client.post("/login", content="pin=0", headers={**FORM, "X-Forwarded-For": f"100.64.1.{i}"},
                         follow_redirects=False).status_code for i in range(32)]
    assert codes[:30] == [303] * 30 and codes[30:] == [429, 429]   # rotating clients still hit the global cap


def test_login_tries_prunes_stale_clients(client, pinned):
    dash._login_tries["old"] = [1.0]
    client.post("/login", content="pin=0", headers=FORM, follow_redirects=False)
    assert "old" not in dash._login_tries


# -- side-effecting GETs need the header -------------------------------------------------------------------------

def test_discover_and_ai_prep_need_header(client, monkeypatch):
    import settleezy_cofounder.leadquery as lq

    monkeypatch.setattr(lq, "find", lambda cfg, db, q, discover=False: {"results": [], "discover": discover})
    assert client.get("/api/leads/find?q=cafes").status_code == 200
    assert client.get("/api/leads/find?q=cafes&discover=1").status_code == 403
    assert client.get("/api/leads/find?q=cafes&discover=1", headers=H).json()["discover"] is True
    assert client.get("/api/prep/nope").status_code == 404
    assert client.get("/api/prep/nope?ai=1").status_code == 403
    assert client.get("/api/prep/nope?ai=1", headers=H).status_code == 404


# -- input validation -----------------------------------------------------------------------------------------

def test_bad_input_is_400_not_500(cfg):
    c = TestClient(dash.app, raise_server_exceptions=False)
    assert c.post("/api/tasks", json={"title": "x", "priority": "high"}, headers=H).status_code == 400
    assert c.post("/api/leads/enrich", json={"ids": ["a"]}, headers=H).status_code == 400
    assert c.post("/api/voice/event", json={"state": "idle", "envelope": ["x"]}, headers=H).status_code == 400
    assert c.post("/api/voice/event", json={"state": "idle", "envelope": 5}, headers=H).status_code == 400
    assert c.post("/api/voice/event", json={"state": "idle", "envelope": ["nan"]}, headers=H).status_code == 400
    assert c.post("/api/notifications/read", json={"ids": None}, headers=H).status_code == 200
    assert c.post("/api/notifications/read", json={"ids": ["x"]}, headers=H).status_code == 400
    assert c.post("/api/push/subscribe", json=[1], headers=H).status_code == 400
    assert c.get("/api/metric?key=a.b&days=99999999").status_code == 200
    assert c.get("/api/leads?limit=-1").status_code == 200
    key = cfg.get("tracking.manual_kpis")[0]
    for v in ("nan", "inf", "-inf"):
        assert c.post("/api/kpi", json={"key": key, "value": v}, headers=H).status_code == 400
    assert c.post("/api/kpi", json={"key": key, "value": "12"}, headers=H).status_code == 200


def test_run_job_check_and_set_is_atomic(cfg, monkeypatch):
    from settleezy_cofounder import jobs

    release = threading.Event()
    monkeypatch.setitem(jobs.JOBS, "slowtest", lambda cfg, db: {})
    monkeypatch.setattr(jobs, "run_job", lambda cfg, job: release.wait(5))
    results, barrier = [], threading.Barrier(8)

    def go():
        barrier.wait()
        results.append(dash.run_job("slowtest", "1")["started"])

    threads = [threading.Thread(target=go) for _ in range(8)]
    try:
        [t.start() for t in threads]
        [t.join() for t in threads]
        assert results.count(True) == 1
    finally:
        release.set()
        dash._running.pop("slowtest", None)


# -- one multiplexed live stream per page --------------------------------------------------------------------

def test_events_channel_tags_and_fans_out():
    q = asyncio.Queue(maxsize=5)
    voice, agent, note = dash._Channel(q, "voice"), dash._Channel(q, "agent"), dash._Channel(q, "notify")
    dash._subscribers.add(voice); dash._agent_subs.add(agent); dash._notify_subs.add(note)
    try:
        dash._fanout({"state": "idle"})
        dash._agent_fanout({"agent": "setz"})
        dash._notify_fanout({"type": "refresh"})
        got = [q.get_nowait() for _ in range(3)]
        assert [e["ch"] for e in got] == ["voice", "agent", "notify"] and got[0]["state"] == "idle"
    finally:
        dash._subscribers.discard(voice); dash._agent_subs.discard(agent); dash._notify_subs.discard(note)
    assert any(r.path == "/api/events" for r in dash.app.routes)
    assert {"/api/voice/stream", "/api/agents/stream", "/api/notify/stream"} <= {r.path for r in dash.app.routes}   # still served


def test_pages_open_one_event_source_through_live_js():
    live = (STATIC / "live.js").read_text(encoding="utf-8")
    assert 'new EventSource("/api/events")' in live and "S.on =" in live
    holo = (STATIC / "hologram.js").read_text(encoding="utf-8")
    assert 'SetzLive.on("voice"' in holo
    for page in ("index.html", "command.html", "hologram.html"):
        html = (STATIC / page).read_text(encoding="utf-8")
        assert "EventSource" not in html, page
        assert html.index('src="/static/live.js"') < html.index("<script>\n"), page   # live.js before the inline script


# -- page fixes -----------------------------------------------------------------------------------------------

def test_pages_use_local_dates_not_utc():
    for page in ("index.html", "command.html"):
        html = (STATIC / page).read_text(encoding="utf-8")
        assert "toISOString().slice(0, 10)" not in html, page
        assert "const localISO" in html, page
    idx = (STATIC / "index.html").read_text(encoding="utf-8")
    assert 'new Date((WK ? WK.week_start : localISO()) + "T00:00"); d.setDate(d.getDate() + days); wkStart = localISO(d);' in idx
    assert '$("#pdlg").returnValue = ""; $("#pdlg").showModal()' in idx
    assert re.search(r"/api/leads/find\?.*X-SZ", idx) and re.search(r"/api/prep/.*\?ai=1.*X-SZ", idx)


def test_hologram_mini_ask_shows_answer():
    html = (STATIC / "hologram.html").read_text(encoding="utf-8")
    assert "r.answer" in html and "r.audio" in html and 'ev.state === "idle" && ev.text' in html
