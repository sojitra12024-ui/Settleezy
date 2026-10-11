"""Local dashboard (http://127.0.0.1:8765) and voice hologram (/hologram).

Runs on localhost only. State-changing endpoints require the `X-SZ` header, which browsers can't add
cross-site without a CORS preflight (which this app never grants), so other websites can't trigger them.
"""

from __future__ import annotations

import asyncio
import base64
import json
import math
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response, StreamingResponse

from .. import jobs, tts
from ..config import load_config
from ..db import DB
from ..leads import pipeline, set_status, top, upsert
from ..scraping.monitor import new_listings

STATIC = Path(__file__).parent / "static"
app = FastAPI(title="Settleezy HQ", docs_url=None, redoc_url=None)
_running: dict[str, str] = {}
_voice_state: dict = {"state": "idle", "text": "", "envelope": [], "frame_ms": 40, "at": 0}
_subscribers: set[asyncio.Queue] = set()
_loop: asyncio.AbstractEventLoop | None = None
_agent_subs: set[asyncio.Queue] = set()
_notify_subs: set[asyncio.Queue] = set()
_login_tries: dict[str, list[float]] = {}
_run_lock = threading.Lock()   # guards the check-and-set of _running


def _guard(x_sz: str | None) -> None:
    if x_sz != "1":
        raise HTTPException(403, "missing X-SZ header")


def _ints(values, what: str = "ids") -> list[int]:
    try:
        return [int(i) for i in (values or [])]
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, f"{what} must be a list of numbers") from exc


# -- access: the laptop itself is always allowed; phones/other devices need the PIN ---------------------------

LOOPBACK = {"127.0.0.1", "::1", "localhost", "testclient"}
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "testserver"}   # Host names a local browser uses (anything else: DNS rebinding)
OPEN_PATHS = {"/login", "/manifest.webmanifest", "/sw.js", "/static/icon-192.png", "/static/icon-512.png", "/static/icon.svg",
              "/api/bridge/webhook", "/api/bridge/v1/recommendations"}   # bridge: signature / bearer token checked in the handler


def _session_secret(cfg) -> bytes:
    import secrets

    path = cfg.data_dir / "session_secret"
    if not path.exists():
        path.write_text(secrets.token_hex(32), encoding="utf-8")
    return path.read_text(encoding="utf-8").strip().encode()


def _session_value(cfg) -> str:
    import hashlib
    import hmac

    return hmac.new(_session_secret(cfg), str(cfg.get("dashboard.access_pin", "")).encode(), hashlib.sha256).hexdigest()


def _peer_is_loopback(request: Request) -> bool:
    return request.client is not None and request.client.host in LOOPBACK


def _is_remote(request: Request) -> bool:
    """Requests through Tailscale Serve / a proxy, or from another device on the network."""
    h = request.headers
    return bool(h.get("tailscale-user-login") or h.get("x-forwarded-for")) or (request.client is not None and request.client.host not in LOOPBACK)


def _host_allowed(request: Request) -> bool:
    """Local requests must name this machine in Host; a foreign name means a DNS-rebinding page is talking to us."""
    from urllib.parse import urlsplit

    try:
        name = urlsplit("//" + request.headers.get("host", "")).hostname or ""
    except ValueError:
        return False
    if name in LOCAL_HOSTS:
        return True
    cfg = load_config()
    extra = {str(x).lower() for x in (cfg.get("dashboard.allowed_hosts", []) or [])}
    ts_name = str(cfg.get("dashboard.tailscale_hostname", "") or "").lower().rstrip(".")
    if ts_name:
        extra |= {ts_name, ts_name.split(".")[0]}
    return name in extra


@app.middleware("http")
async def access_control(request: Request, call_next):
    if not _is_remote(request):
        if not _host_allowed(request):
            return Response("misdirected request: unknown Host", 421)
        return await call_next(request)
    if request.url.path in OPEN_PATHS:
        return await call_next(request)
    import hmac

    cfg = load_config()
    pin = str(cfg.get("dashboard.access_pin", "") or "")
    if not pin:
        # your own tailnet, identified by Tailscale; only `tailscale serve` (a local proxy) can set this header
        if request.headers.get("tailscale-user-login") and _peer_is_loopback(request):
            return await call_next(request)
        return HTMLResponse("Setz: remote access needs [dashboard] access_pin in config.toml (see README: phone access).", 403)
    if hmac.compare_digest(request.cookies.get("sz_session", "").encode(), _session_value(cfg).encode()):
        return await call_next(request)
    if request.url.path.startswith("/api/"):
        return Response('{"detail":"login required"}', 401, media_type="application/json")
    from urllib.parse import quote

    nxt = request.url.path + ("?" + request.url.query if request.url.query else "")
    return RedirectResponse("/login?next=" + quote(nxt, safe=""), 303)


LOGIN_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Setz login</title><meta name="theme-color" content="#030810"><link rel="manifest" href="/manifest.webmanifest">
<style>:root{color-scheme:dark}body{margin:0;min-height:100vh;display:grid;place-items:center;background:radial-gradient(ellipse at 50% 30%,#0b1d2c,#030810 70%);
color:#e2f6ff;font:15px/1.5 system-ui,sans-serif}form{display:grid;gap:12px;width:min(320px,calc(100% - 32px));text-align:center}
b{letter-spacing:.35em;font-size:20px}input{font:inherit;font-size:22px;text-align:center;letter-spacing:.3em;padding:12px;border-radius:10px;
border:1px solid rgba(94,232,255,.4);background:rgba(94,232,255,.06);color:#e2f6ff}button{font:inherit;padding:11px;border-radius:10px;border:0;
background:linear-gradient(135deg,#1b6fd0,#12a5c8);color:#fff;font-weight:600}p{color:#9cc3d2;font-size:13px;margin:0}.e{color:#ff8a8a}</style></head>
<body><form method="post" action="/login"><b>SETZ</b><p>Enter your dashboard PIN</p>
<input name="pin" type="password" inputmode="numeric" autocomplete="current-password" autofocus aria-label="PIN">
<input type="hidden" name="next" value="__NEXT__"><button>Open</button><p class="e">__ERR__</p></form></body></html>"""


@app.get("/login")
def login_page(next: str = "/", err: str = "") -> HTMLResponse:
    nxt = next if next.startswith("/") and not next.startswith("//") else "/"
    return HTMLResponse(LOGIN_HTML.replace("__NEXT__", nxt.replace('"', "")).replace("__ERR__", "Wrong PIN" if err else ""))


@app.post("/login")
async def login_submit(request: Request) -> Response:
    import hmac
    from urllib.parse import parse_qs

    from urllib.parse import quote

    ip = request.client.host if request.client else "?"
    if ip in LOOPBACK and request.headers.get("x-forwarded-for"):   # a local proxy (tailscale serve): it appends the real client last
        ip = request.headers["x-forwarded-for"].split(",")[-1].strip() or ip
    now = time.time()
    for k in [k for k, ts in _login_tries.items() if not ts or now - ts[-1] >= 300]:   # forget stale clients
        _login_tries.pop(k, None)
    tries = [t for t in _login_tries.get(ip, []) if now - t < 300]
    if len(tries) >= 5 or sum(1 for ts in _login_tries.values() for t in ts if now - t < 300) >= 30:
        return HTMLResponse("Too many attempts. Wait 5 minutes.", 429)
    form = {k: v[0] for k, v in parse_qs((await request.body()).decode()).items()}
    cfg = load_config()
    pin = str(cfg.get("dashboard.access_pin", "") or "")
    nxt = form.get("next", "/")
    nxt = nxt if nxt.startswith("/") and not nxt.startswith("//") else "/"
    if not pin or not hmac.compare_digest(form.get("pin", "").encode(), pin.encode()):
        _login_tries[ip] = tries + [now]
        return RedirectResponse(f"/login?err=1&next={quote(nxt, safe='')}", 303)
    resp = RedirectResponse(nxt, 303)
    resp.set_cookie("sz_session", _session_value(cfg), max_age=90 * 86400, httponly=True, samesite="strict",
                    secure=request.headers.get("x-forwarded-proto", request.url.scheme) == "https")
    return resp


# -- installable app (PWA) ------------------------------------------------------------------------------------

@app.get("/manifest.webmanifest")
def manifest() -> Response:
    data = {"name": "Setz · Settleezy HQ", "short_name": "Setz", "start_url": "/", "scope": "/", "display": "standalone",
            "background_color": "#030810", "theme_color": "#030810", "description": "Settleezy's AI chief of staff",
            "icons": [{"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
                      {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
                      {"src": "/static/icon.svg", "sizes": "any", "type": "image/svg+xml"}],
            "display_override": ["standalone", "minimal-ui"], "orientation": "any", "categories": ["business", "productivity"],
            "shortcuts": [{"name": "Talk to Setz", "short_name": "Talk", "url": "/talk", "icons": [{"src": "/static/icon-192.png", "sizes": "192x192"}]},
                          {"name": "Approvals", "url": "/#approvals"}, {"name": "Onboard next", "short_name": "Market", "url": "/#market"},
                          {"name": "Lead finder", "url": "/#leads"}, {"name": "Command center", "url": "/command"}],
            # share a link, an Instagram profile, an email text or a contract file from any app straight to Setz
            "share_target": {"action": "/share", "method": "POST", "enctype": "multipart/form-data",
                             "params": {"title": "title", "text": "text", "url": "url",
                                        "files": [{"name": "file", "accept": [".pdf", ".docx", ".txt", ".md", "application/pdf",
                                                                               "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                                                                               "text/plain", "image/*"]}]}}}
    return Response(json.dumps(data), media_type="application/manifest+json")


@app.get("/sw.js")
def service_worker() -> FileResponse:
    return FileResponse(STATIC / "sw.js", media_type="text/javascript", headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"})


@app.on_event("startup")
async def _startup() -> None:
    global _loop
    _loop = asyncio.get_running_loop()
    threading.Thread(target=_watch_loop, daemon=True).start()


def _watch_loop() -> None:
    """Background: remind about meetings 10 minutes before they start (pop-up on every screen)."""
    from datetime import datetime as _dt

    while True:
        try:
            cfg = load_config()
            mins = int(cfg.get("notifications.meeting_reminder_minutes", 10))
            with DB(cfg.db_path) as db:
                now = _dt.now()
                soon = (now + timedelta(minutes=mins)).strftime("%Y-%m-%dT%H:%M")
                for e in db.q("SELECT * FROM events WHERE start > ? AND start <= ?", (now.strftime("%Y-%m-%dT%H:%M"), soon)):
                    from ..notify import notify

                    notify(cfg, db, f"In {mins} min: {e['title']}", f"{e['start'][11:16]}–{e['end'][11:16]} · {e['location'] or ''}".strip(" ·"),
                           kind="meeting", url="/#today", key=f"remind:{e['id']}:{e['start']}")
        except Exception:
            pass
        time.sleep(60)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/command")
def command_page() -> FileResponse:
    return FileResponse(STATIC / "command.html")


@app.get("/hologram")
def hologram_page() -> FileResponse:
    return FileResponse(STATIC / "hologram.html")


@app.get("/static/{name}")
def static_file(name: str) -> FileResponse:
    path = (STATIC / name).resolve()
    if path.parent != STATIC.resolve() or not path.is_file():
        raise HTTPException(404)
    return FileResponse(path)


# -- summary ----------------------------------------------------------------

@app.get("/api/summary")
def summary() -> dict:
    from ..tracking import draft_funnel, goals

    cfg = load_config()
    with DB(cfg.db_path) as db:
        today = datetime.now().date().isoformat()
        since30 = (datetime.now() - timedelta(days=30)).date().isoformat()
        since7 = (datetime.now() - timedelta(days=7)).isoformat()
        triage = {"replies": [], "followups": []}
        try:
            from ..triage import followups_due, needs_reply

            triage["replies"] = [i.as_dict() for i in needs_reply(cfg, db, None)][:25]
            triage["followups"] = [i.as_dict() for i in followups_due(cfg, db)][:25]
        except Exception as exc:  # empty db on first launch
            triage["error"] = str(exc)
        metrics: dict[str, list] = {}
        for r in db.q("SELECT day, source, key, value FROM metrics WHERE day >= ? ORDER BY day", (since30,)):
            metrics.setdefault(f"{r['source']}.{r['key']}", []).append([r["day"], r["value"]])
        listings_by_day = [dict(r) for r in db.q(
            "SELECT substr(first_seen,1,10) day, source, COUNT(*) n FROM listings WHERE first_seen != 'baseline' AND first_seen >= ? "
            "GROUP BY day, source ORDER BY day", ((datetime.now() - timedelta(days=14)).date().isoformat(),))]
        categories = [dict(r) for r in db.q(
            "SELECT COALESCE(NULLIF(category,''),'Uncategorised') category, COUNT(*) n FROM listings "
            "WHERE first_seen != 'baseline' AND first_seen >= ? GROUP BY 1 ORDER BY n DESC LIMIT 8", (since30,))]
        stats_path = cfg.data_dir / "email_stats.json"
        email_stats = json.loads(stats_path.read_text(encoding="utf-8")) if stats_path.exists() else {}
        briefs = sorted((cfg.data_dir / "briefs").glob("*.md")) if (cfg.data_dir / "briefs").exists() else []
        reports = sorted((cfg.data_dir / "reports").glob("*.md")) if (cfg.data_dir / "reports").exists() else []
        kpi_keys = cfg.get("tracking.manual_kpis", [])
        return {
            "now": datetime.now().isoformat(timespec="minutes"),
            "me": cfg.me.get("name", ""),
            "events_today": [dict(r) for r in db.q("SELECT * FROM events WHERE substr(start,1,10)=? ORDER BY start", (today,))],
            "events_upcoming": [dict(r) for r in db.q("SELECT * FROM events WHERE substr(start,1,10)>? ORDER BY start LIMIT 15", (today,))],
            **triage,
            "drafts_7d": db.one("SELECT COUNT(*) n FROM drafts WHERE created_at >= ?", (since7,))["n"],
            "draft_funnel": draft_funnel(db, 30),
            "new_listings": new_listings(db, 24 * 7)[:60],
            "listings_by_day": listings_by_day,
            "listing_categories": categories,
            "listings_total": db.one("SELECT COUNT(*) n FROM listings")["n"],
            "leads_new_7d": db.one("SELECT COUNT(*) n FROM leads WHERE created_at >= ?", (since7,))["n"],
            "pipeline": pipeline(db),
            "metrics": metrics,
            "metric_keys": sorted({f"{r['source']}.{r['key']}" for r in db.q("SELECT DISTINCT source, key FROM metrics")}),
            "goals": goals(cfg, db),
            "kpi_keys": kpi_keys,
            "kpi_latest": {k: (metrics.get(f"manual.{k}") or [[None, None]])[-1][1] for k in kpi_keys},
            "instagram_comments": json.loads(db.kv_get("instagram:unanswered_comments", "[]")),
            "instagram_top_posts": json.loads(db.kv_get("instagram:top_posts", "[]")),
            "outreach": email_stats.get("outreach", {}),
            "response_time": email_stats.get("response_time_hours", {}),
            "brief": briefs[-1].read_text(encoding="utf-8") if briefs else "",
            "report": reports[-1].read_text(encoding="utf-8") if reports else "",
            "connections": json.loads(db.kv_get("connections", "{}")),
            "last_sync": {k: db.kv_get(f"last_run:{k}") for k in jobs.JOBS},
            "running": dict(_running),
            "voice": {"elevenlabs": tts.elevenlabs_enabled(cfg)},
        }


@app.get("/api/hub")
def hub() -> list[dict]:
    """Channels flowing into Setz on the full-screen hologram: connection state + one live number each."""
    from ..ops import partner_stats

    cfg = load_config()
    with DB(cfg.db_path) as db:
        conn = {r["name"]: r for r in json.loads(db.kv_get("connections", "{}")).get("results", [])}
        ok = lambda name: conn[name]["ok"] if name in conn else None  # noqa: E731
        today = datetime.now().date().isoformat()
        since7 = (datetime.now() - timedelta(days=7)).isoformat()
        latest = lambda src, key: (db.one("SELECT value FROM metrics WHERE source=? AND key=? ORDER BY day DESC LIMIT 1", (src, key)) or {"value": None})["value"]  # noqa: E731
        followers = latest("instagram", "followers")
        ps = partner_stats(cfg, db)
        new_listings_7d = db.one("SELECT COUNT(*) n FROM listings WHERE first_seen != 'baseline' AND first_seen >= ?", (since7,))["n"]
        return [
            {"key": "outlook", "label": "Outlook", "icon": "outlook", "ok": ok("Outlook"),
             "value": f"{len(_safe_replies(cfg, db))} to reply"},
            {"key": "instagram", "label": "Instagram", "icon": "instagram", "ok": ok("Instagram"),
             "value": f"{followers:,.0f} followers" if followers else ""},
            {"key": "calendar", "label": "Calendar", "icon": "calendar", "ok": ok("Calendly") if "Calendly" in conn else ok("Outlook"),
             "value": f"{db.one('SELECT COUNT(*) n FROM events WHERE substr(start,1,10)=?', (today,))['n']} meetings today"},
            {"key": "web", "label": "Competitors", "icon": "web", "ok": True if db.kv_get("last_run:scrape") else None,
             "value": f"{new_listings_7d} new / 7d"},
            {"key": "partners", "label": "Partners", "icon": "partners", "ok": True,
             "value": f"{ps['live']} live · {ps['onboarding']} onboarding"},
            {"key": "claude", "label": "Claude", "icon": "brain", "ok": ok("Claude API"), "value": ""},
            {"key": "ollama", "label": "Local model", "icon": "chip", "ok": ok("Local model (Ollama)"), "value": ""},
            {"key": "voice", "label": "Voice", "icon": "voice", "ok": ok("Voice (ElevenLabs)"), "value": ""},
        ]


def _safe_replies(cfg, db) -> list:
    try:
        from ..triage import needs_reply

        return needs_reply(cfg, db, None)
    except Exception:
        return []


@app.get("/api/metric")
def metric_series(key: str, days: int = 90) -> dict:
    cfg = load_config()
    source, _, k = key.partition(".")
    since = (datetime.now() - timedelta(days=max(1, min(days, 3650)))).date().isoformat()
    with DB(cfg.db_path) as db:
        rows = db.q("SELECT day, value FROM metrics WHERE source=? AND key=? AND day >= ? ORDER BY day", (source, k, since))
    return {"key": key, "series": [[r["day"], r["value"]] for r in rows]}


# -- leads ----------------------------------------------------------------

@app.get("/api/leads")
def leads(q: str = "", kind: str = "", status: str = "", limit: int = 300) -> list[dict]:
    cfg = load_config()
    sql, params = "SELECT * FROM leads WHERE 1=1", []
    if q:
        sql += " AND (name LIKE ? OR category LIKE ? OR email LIKE ? OR sources LIKE ?)"
        params += [f"%{q}%"] * 4
    if kind:
        sql += " AND kind=?"
        params.append(kind)
    if status:
        sql += " AND status=?"
        params.append(status)
    sql += " ORDER BY CASE status WHEN 'meeting' THEN 0 WHEN 'replied' THEN 1 WHEN 'contacted' THEN 2 WHEN 'drafted' THEN 3 ELSE 4 END, score DESC LIMIT ?"
    params.append(max(1, min(limit, 1000)))
    with DB(cfg.db_path) as db:
        return [dict(r) for r in db.q(sql, params)]


@app.get("/api/leads/find")
def leads_find(q: str, discover: int = 0, limit: int = 0, x_sz: str | None = Header(default=None)) -> dict:
    """Natural-language lead search: 'vegan cafés near HU with email, not contacted'."""
    if discover:   # scans OpenStreetMap and writes leads: a state change
        _guard(x_sz)
    from ..leadquery import find, parse

    cfg = load_config()
    lq = parse(q)
    if limit:
        lq.limit = max(1, min(limit, 1000))
    with DB(cfg.db_path) as db:
        return find(cfg, db, lq, discover=bool(discover)) | {"question": q}


@app.get("/api/leads/find/last")
def leads_find_last() -> dict:
    """The last lead search asked by voice ("Hey Setz, find me …"), so the dashboard can show the full list."""
    cfg = load_config()
    with DB(cfg.db_path) as db:
        return json.loads(db.kv_get("leadfinder:last", "{}") or "{}")


@app.get("/api/leads/find.csv")
def leads_find_csv(q: str) -> Response:
    from ..leadquery import find, parse, to_csv

    cfg = load_config()
    lq = parse(q)
    lq.limit = 5000
    with DB(cfg.db_path) as db:
        res = find(cfg, db, lq)
    name = re.sub(r"[^a-z0-9]+", "-", q.lower()).strip("-")[:50] or "leads"   # headers must be ASCII
    return Response("\ufeff" + to_csv(res["results"]), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="setz-{name}.csv"'})


@app.post("/api/leads/enrich")
def leads_enrich(body: dict, x_sz: str | None = Header(default=None)) -> dict:
    """Read website + menu + Impressum for the given lead ids (max 25): email, phone, owner, address, price level."""
    _guard(x_sz)
    from ..leadintel import enrich_lead, profile
    from ..scraping.fetcher import Fetcher

    ids = _ints(body.get("ids"))[:25]
    if not ids:
        raise HTTPException(400, "ids required")
    cfg = load_config()
    out = {}
    with DB(cfg.db_path) as db:
        f = Fetcher(db, min_delay=float(cfg.get("scraping.min_delay_seconds", 4)))
        try:
            for i in ids:
                try:
                    res = enrich_lead(db, f, i)
                except ValueError as exc:
                    res = {"error": str(exc)}
                row = db.one("SELECT * FROM leads WHERE id=?", (i,))
                out[i] = {"found": res, "lead": profile(db, dict(row)) if row else None}
        finally:
            f.close()
    return out


@app.post("/api/instagram/add")
def instagram_add(body: dict, x_sz: str | None = Header(default=None)) -> list[dict]:
    """Add Instagram accounts you spotted (handles or profile links) as leads, with followers and bio."""
    _guard(x_sz)
    from ..igdiscovery import add_handles

    handles = [h for h in re.split(r"[\s,;]+", body.get("handles") or "") if h.strip()][:20]
    if not handles:
        raise HTTPException(400, "handles required")
    cfg = load_config()
    with DB(cfg.db_path) as db:
        try:
            return add_handles(cfg, db, handles)
        except RuntimeError as exc:   # Instagram not connected
            raise HTTPException(400, str(exc)) from exc


@app.post("/api/leads")
def add_lead(body: dict, x_sz: str | None = Header(default=None)) -> dict:
    _guard(x_sz)
    if not (body.get("name") or "").strip():
        raise HTTPException(400, "name is required")
    cfg = load_config()
    with DB(cfg.db_path) as db:
        lid, created = upsert(db, body["name"], body.get("kind") or "merchant", source="manual",
                              category=body.get("category", ""), website=body.get("website", ""),
                              email=body.get("email", ""), instagram=body.get("instagram", ""))
    return {"id": lid, "created": created}


@app.post("/api/leads/{lead_id}/status")
def lead_status(lead_id: int, body: dict, x_sz: str | None = Header(default=None)) -> dict:
    _guard(x_sz)
    cfg = load_config()
    with DB(cfg.db_path) as db:
        try:
            set_status(db, lead_id, body.get("status", ""), body.get("note", ""))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    return {"ok": True}


@app.post("/api/leads/{lead_id}/draft")
def lead_draft(lead_id: int, x_sz: str | None = Header(default=None)) -> dict:
    _guard(x_sz)
    from ..drafts import outreach_draft

    cfg = load_config()
    with DB(cfg.db_path) as db:
        try:
            return outreach_draft(cfg, db, lead_id)
        except Exception as exc:
            raise HTTPException(400, str(exc)) from exc


# -- operations: plan, to-dos, reach-out, partners, prep ------------------

@app.get("/api/ops")
def ops_summary() -> dict:
    from .. import ops

    cfg = load_config()
    with DB(cfg.db_path) as db:
        plan = ops.today_plan(cfg, db)
        return {
            "plan": plan,
            "focus_now": ops.focus_now(cfg, db),
            "tasks": ops.tasks(db, "open"),
            "tasks_done": ops.tasks(db, "done")[:15],
            "reach_out": ops.reach_out(cfg, db, 20),
            "partners": ops.partners(cfg, db),
            "partner_stats": ops.partner_stats(cfg, db),
            "stages": ops.STAGES,
            "scorecard": ops.scorecard(cfg, db),
        }


@app.post("/api/tasks")
def task_add(body: dict, x_sz: str | None = Header(default=None)) -> dict:
    _guard(x_sz)
    from ..ops import add_task

    title = (body.get("title") or "").strip()
    if not title:
        raise HTTPException(400, "title is required")
    try:
        priority = int(body.get("priority", 2))
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, "priority must be a number") from exc
    cfg = load_config()
    with DB(cfg.db_path) as db:
        tid = add_task(db, title, due=body.get("due") or None, priority=priority, source=body.get("source", "manual"),
                       lead_id=body.get("lead_id"), partner_id=body.get("partner_id"))
    return {"id": tid, "duplicate": tid is None}


@app.post("/api/tasks/{task_id}")
def task_action(task_id: int, body: dict, x_sz: str | None = Header(default=None)) -> dict:
    _guard(x_sz)
    from ..ops import set_task

    cfg = load_config()
    with DB(cfg.db_path) as db:
        try:
            if body.get("action") == "edit":
                from ..ops import update_task

                return update_task(db, task_id, **(body.get("fields") or {}))
            set_task(db, task_id, body.get("action", ""), int(body.get("days", 1)))
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, str(exc)) from exc
    return {"ok": True}


# -- brain ------------------------------------------------------------------------

@app.get("/api/brain")
def brain_graph(limit: int = 90) -> dict:
    from .. import brain

    cfg = load_config()
    with DB(cfg.db_path) as db:
        return brain.graph(cfg, db, max(10, min(limit, 200)))


@app.get("/api/brain/recall")
def brain_recall(q: str, k: int = 8) -> list[dict]:
    from .. import brain

    cfg = load_config()
    with DB(cfg.db_path) as db:
        return brain.recall(cfg, db, q, max(1, min(k, 20)))


@app.post("/api/brain/remember")
def brain_remember(body: dict, x_sz: str | None = Header(default=None)) -> dict:
    _guard(x_sz)
    from .. import brain

    cfg = load_config()
    with DB(cfg.db_path) as db:
        try:
            mid = brain.remember(cfg, db, body.get("text", ""), kind=body.get("kind", "fact"), subject=body.get("subject", ""),
                                 source="dashboard", importance=float(body.get("importance", 0.8)))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    _broadcast_agent({"agent": "setz", "status": "ok", "summary": "New memory stored", "kind": "memory"})
    return {"id": mid}


@app.post("/api/brain/forget/{memory_id}")
def brain_forget(memory_id: int, x_sz: str | None = Header(default=None)) -> dict:
    _guard(x_sz)
    from .. import brain

    cfg = load_config()
    with DB(cfg.db_path) as db:
        return {"removed": brain.forget(db, memory_id)}


# -- speech coach -------------------------------------------------------------------

_whisper: dict = {}


def _whisper_model(cfg):
    if "m" not in _whisper:
        from faster_whisper import WhisperModel

        name = cfg.get("voice.whisper_model", "small")
        try:
            _whisper["m"] = WhisperModel(name, device="cuda", compute_type="int8_float16")
        except Exception:
            _whisper["m"] = WhisperModel(name, device="cpu", compute_type="int8")
    return _whisper["m"]


@app.get("/api/speech")
def speech_overview() -> dict:
    from .. import speech

    cfg = load_config()
    with DB(cfg.db_path) as db:
        return {"sessions": speech.sessions(db, 20, "practice") + speech.sessions(db, 10, "file"),
                "trend": speech.trend(db, 90)}


@app.post("/api/speech/analyse")
async def speech_analyse(request: Request, mode: str = "practice", coach: int = 1, topic: str = "venue partnership pitch",
                         x_sz: str | None = Header(default=None)) -> dict:
    """Body: an audio recording (webm/ogg/wav/m4a from the browser's recorder). Returns delivery analysis + coaching."""
    _guard(x_sz)
    data = await request.body()
    if not data:
        raise HTTPException(400, "empty recording")
    if len(data) > 40_000_000:
        raise HTTPException(413, "recording too long")
    cfg = load_config()

    def work() -> dict:
        from .. import speech

        audio = speech.decode(data)
        segs, info = _whisper_model(cfg).transcribe(audio, beam_size=1, vad_filter=True, word_timestamps=True)
        segs = list(segs)
        m = speech.analyse(audio, words=speech.words_from_segments(segs), lang=info.language if info.language in ("en", "de") else "en")
        if coach and m.get("text"):
            try:
                m["coaching"] = speech.coach(cfg, m, topic[:80])
            except Exception as exc:
                m["coaching"] = f"(AI coaching unavailable: {exc})"
        with DB(cfg.db_path) as db:
            m["session_id"] = speech.save(db, m, mode if mode in {"practice", "file"} else "practice", m.get("coaching", ""))
        return m

    try:
        return await asyncio.to_thread(work)
    except (ImportError, RuntimeError) as exc:
        raise HTTPException(501, f"Speech analysis needs the voice extras on this PC: pip install -e \".[voice]\" ({exc})") from exc


# -- meeting requests -> calendar ------------------------------------------------------

@app.get("/api/meetings/requests")
def meeting_requests() -> dict:
    from .. import scheduling

    cfg = load_config()
    with DB(cfg.db_path) as db:
        return {"requests": scheduling.requests(cfg, db), "slots": scheduling.suggest_slots(cfg, db),
                "calendar_write": bool(cfg.get("outlook.calendar_write", True))}


@app.post("/api/meetings/requests/{req_id}")
def meeting_action(req_id: int, body: dict, x_sz: str | None = Header(default=None)) -> dict:
    """action: draft (reply with slots as an Outlook draft) | book (start, minutes, invite, online) | dismiss."""
    _guard(x_sz)
    from .. import scheduling
    from ..msgraph import GraphError

    cfg = load_config()
    with DB(cfg.db_path) as db:
        try:
            a = body.get("action")
            if a == "draft":
                return scheduling.draft_reply(cfg, db, req_id)
            if a == "book":
                res = scheduling.book(cfg, db, req_id, body["start"], body.get("minutes"), invite=bool(body.get("invite", True)),
                                      online=body.get("online"))
                _notify_safe(cfg, "Meeting booked", f"{res['subject']} · {res['start'][5:16].replace('T', ' ')}", "calendar")
                return res
            if a == "dismiss":
                scheduling.dismiss(db, req_id)
                return {"ok": True}
            raise HTTPException(400, "action must be draft, book or dismiss")
        except (ValueError, KeyError) as exc:
            raise HTTPException(400, str(exc)) from exc
        except (GraphError, RuntimeError) as exc:
            raise HTTPException(502, f"Outlook: {exc}") from exc


def _notify_safe(cfg, title: str, body: str, kind: str = "info") -> None:
    try:
        from ..notify import notify

        with DB(cfg.db_path) as db:
            notify(cfg, db, title, body, kind=kind)
    except Exception:
        pass


# -- pipeline + week planner ---------------------------------------------------

@app.get("/api/pipeline")
def pipeline_summary() -> dict:
    from .. import pipeline as pl

    cfg = load_config()
    with DB(cfg.db_path) as db:
        return pl.summary(cfg, db)


@app.post("/api/leads/{lead_id}/next-step")
def lead_next_step(lead_id: int, body: dict, x_sz: str | None = Header(default=None)) -> dict:
    _guard(x_sz)
    from ..pipeline import set_next_step

    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "text is required")
    cfg = load_config()
    with DB(cfg.db_path) as db:
        try:
            return set_next_step(db, lead_id, text, body.get("due") or None)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc


@app.post("/api/leads/{lead_id}/sequence")
def lead_sequence(lead_id: int, body: dict, x_sz: str | None = Header(default=None)) -> dict:
    _guard(x_sz)
    from ..pipeline import start_sequence, stop_sequence

    cfg = load_config()
    with DB(cfg.db_path) as db:
        try:
            if body.get("action") == "stop":
                return {"removed": stop_sequence(db, lead_id)}
            return {"steps": start_sequence(cfg, db, lead_id)}
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc


@app.post("/api/pipeline/sequences/auto")
def sequences_auto(body: dict | None = None, x_sz: str | None = Header(default=None)) -> dict:
    _guard(x_sz)
    from ..pipeline import auto_start

    cfg = load_config()
    with DB(cfg.db_path) as db:
        return {"started": auto_start(cfg, db, (body or {}).get("limit"))}


def _week(start: str | None):
    from datetime import date as _date

    from .. import planner

    cfg = load_config()
    db = DB(cfg.db_path)
    try:
        return planner.plan_week(cfg, db, _date.fromisoformat(start) if start else None), db
    except ValueError as exc:
        db.close()
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/week")
def week(start: str | None = None) -> dict:
    plan, db = _week(start)
    db.close()
    return plan


@app.get("/api/week.ics")
def week_ics(start: str | None = None) -> Response:
    from ..planner import to_ics

    plan, db = _week(start)
    db.close()
    return Response(to_ics(plan), media_type="text/calendar",
                    headers={"Content-Disposition": f'attachment; filename="setz-week-{plan["week_start"]}.ics"'})


@app.post("/api/week/apply")
def week_apply(body: dict | None = None, x_sz: str | None = Header(default=None)) -> dict:
    _guard(x_sz)
    from ..planner import apply_plan

    plan, db = _week((body or {}).get("start"))
    try:
        return {"updated": apply_plan(db, plan)}
    finally:
        db.close()


@app.post("/api/partners")
def partner_add(body: dict, x_sz: str | None = Header(default=None)) -> dict:
    _guard(x_sz)
    from ..ops import add_partner

    if not (body.get("name") or "").strip():
        raise HTTPException(400, "name is required")
    cfg = load_config()
    with DB(cfg.db_path) as db:
        fields = {k: v for k, v in body.items() if k not in {"name", "kind"}}
        return {"id": add_partner(db, body["name"], body.get("kind") or "venue", **fields)}


@app.post("/api/partners/{partner_id}")
def partner_update(partner_id: int, body: dict, x_sz: str | None = Header(default=None)) -> dict:
    _guard(x_sz)
    from .. import ops

    cfg = load_config()
    with DB(cfg.db_path) as db:
        try:
            if body.get("advance"):
                return ops.advance(db, partner_id)
            if body.get("log_contact"):
                from ..db import utcnow

                body = {"last_contact_at": utcnow()}
            return ops.update_partner(db, partner_id, **body)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc


@app.get("/api/prep/{event_id}")
def prep(event_id: str, ai: int = 0, x_sz: str | None = Header(default=None)) -> dict:
    if ai:   # calls the LLM (costs money): needs the same header as state-changing calls
        _guard(x_sz)
    from ..llm import LLM
    from ..ops import meeting_prep

    cfg = load_config()
    with DB(cfg.db_path) as db:
        try:
            return {"markdown": meeting_prep(cfg, db, event_id, LLM(cfg) if ai else None)}
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc


# -- Setz's agent team -------------------------------------------------------

@app.get("/api/agents")
def agents_overview() -> dict:
    from .. import agents

    cfg = load_config()
    with DB(cfg.db_path) as db:
        return {
            "agents": [{"key": a.key, "name": a.name, "role": a.role, "icon": a.icon} for a in agents.AGENTS.values()],
            "reports": agents.latest(db),
            "setz": json.loads(db.kv_get("setz:synthesis", "{}")),
            "activity": agents.activity(db, 50),
            "running": _running.get("agents") == "running",
        }


@app.post("/api/agents/run")
def agents_run(x_sz: str | None = Header(default=None)) -> dict:
    _guard(x_sz)
    return run_job("agents", x_sz)


@app.post("/api/agents/event")
async def agents_event(body: dict, x_sz: str | None = Header(default=None)) -> dict:
    _guard(x_sz)
    event = {"agent": str(body.get("agent", ""))[:40], "status": str(body.get("status", ""))[:10],
             "summary": str(body.get("summary", ""))[:300], "at": time.time()}
    for q in list(_agent_subs):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            pass
    return {"ok": True}


@app.get("/api/agents/stream")
async def agents_stream(request: Request) -> StreamingResponse:
    q: asyncio.Queue = asyncio.Queue(maxsize=50)
    _agent_subs.add(q)

    async def gen():
        try:
            yield ": connected\n\n"
            while not await request.is_disconnected():
                try:
                    yield f"data: {json.dumps(await asyncio.wait_for(q.get(), timeout=15))}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            _agent_subs.discard(q)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


# -- tracking ---------------------------------------------------------------

@app.post("/api/kpi")
def kpi(body: dict, x_sz: str | None = Header(default=None)) -> dict:
    _guard(x_sz)
    from ..tracking import record_kpi

    cfg = load_config()
    key = str(body.get("key", ""))
    if key not in cfg.get("tracking.manual_kpis", []):
        raise HTTPException(400, f"unknown KPI {key!r}; add it to [tracking] manual_kpis in config.toml")
    try:
        value = float(body.get("value"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, "value must be a number") from exc
    if not math.isfinite(value):
        raise HTTPException(400, "value must be a finite number")
    with DB(cfg.db_path) as db:
        record_kpi(db, key, value, body.get("day") or None)
    return {"ok": True}


@app.post("/api/connections/refresh")
def refresh_connections(x_sz: str | None = Header(default=None)) -> dict:
    _guard(x_sz)
    return run_job("doctor", x_sz)


# -- jobs -------------------------------------------------------------------

@app.post("/api/run/{job}")
def run_job(job: str, x_sz: str | None = Header(default=None)) -> dict:
    _guard(x_sz)
    if job not in jobs.JOBS:
        raise HTTPException(404, f"unknown job {job}")
    with _run_lock:
        if _running.get(job) == "running":
            return {"started": False, "status": "already running"}
        _running[job] = "running"
    from ..operations import JOB_AGENT

    agent = JOB_AGENT.get(job, "setz")
    _broadcast_agent({"agent": agent, "status": "working", "summary": f"Running {job}…", "job": job})

    def work() -> None:
        try:
            jobs.run_job(load_config(), job)
            _running.pop(job, None)
            _broadcast_agent({"agent": agent, "status": "ok", "summary": f"{job} done", "job": job})
        except Exception as exc:
            _running[job] = f"error: {exc}"
            _broadcast_agent({"agent": agent, "status": "alert", "summary": f"{job} failed: {exc}"[:200], "job": job})

    threading.Thread(target=work, daemon=True).start()
    return {"started": True}


@app.get("/api/operations")
def operations_overview() -> dict:
    from ..operations import overview

    cfg = load_config()
    with DB(cfg.db_path) as db:
        return overview(cfg, db, dict(_running))


@app.post("/api/playbook/{name}")
def playbook_run(name: str, x_sz: str | None = Header(default=None)) -> dict:
    _guard(x_sz)
    from ..operations import PLAYBOOKS, run_playbook

    if name not in PLAYBOOKS:
        raise HTTPException(404, f"unknown playbook {name}")
    with _run_lock:
        if _running.get("playbook") == "running":
            return {"started": False, "status": "a playbook is already running"}
        _running["playbook"] = "running"

    def work() -> None:
        try:
            run_playbook(load_config(), name, _broadcast_agent)
        finally:
            _running.pop("playbook", None)

    threading.Thread(target=work, daemon=True).start()
    return {"started": True, "steps": PLAYBOOKS[name]["steps"]}


# -- notifications -----------------------------------------------------------------

def _notify_fanout(event: dict) -> None:
    for q in list(_notify_subs):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            pass


def broadcast_notify(event: dict) -> None:
    """Thread-safe: deliver a notification / refresh event to every open page."""
    event = {**event, "at": time.time()}
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if _loop is not None and running is not _loop:
        _loop.call_soon_threadsafe(_notify_fanout, event)
    else:
        _notify_fanout(event)


@app.post("/api/notify/event")
def notify_event(body: dict, x_sz: str | None = Header(default=None)) -> dict:
    """Other Setz processes (scheduler jobs, voice) hand events to the open pages through here."""
    _guard(x_sz)
    if body.get("type") not in {"notification", "refresh"}:
        raise HTTPException(400, "bad event")
    broadcast_notify({k: body.get(k) for k in ("type", "id", "title", "body", "kind", "url", "quiet", "what") if k in body})
    return {"ok": True, "listeners": len(_notify_subs)}


@app.get("/api/notify/stream")
async def notify_stream(request: Request) -> StreamingResponse:
    q: asyncio.Queue = asyncio.Queue(maxsize=100)
    _notify_subs.add(q)

    async def gen():
        try:
            yield ": connected\n\n"
            while not await request.is_disconnected():
                try:
                    yield f"data: {json.dumps(await asyncio.wait_for(q.get(), timeout=15))}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            _notify_subs.discard(q)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@app.get("/api/notifications")
def notifications(limit: int = 40) -> dict:
    from ..notify import recent

    cfg = load_config()
    with DB(cfg.db_path) as db:
        return recent(db, max(1, min(limit, 200)))


@app.post("/api/notifications/read")
def notifications_read(body: dict | None = None, x_sz: str | None = Header(default=None)) -> dict:
    _guard(x_sz)
    from ..notify import mark_read

    cfg = load_config()
    with DB(cfg.db_path) as db:
        mark_read(db, _ints((body or {}).get("ids")) or None)
    return {"ok": True}


@app.post("/api/notifications/test")
def notifications_test(x_sz: str | None = Header(default=None)) -> dict:
    _guard(x_sz)
    from ..notify import notify

    cfg = load_config()
    with DB(cfg.db_path) as db:
        nid = notify(cfg, db, "Setz is connected", "Notifications reach this device. 👋", kind="success", key=f"test:{time.time()}")
    return {"id": nid}


@app.get("/api/push/key")
def push_key() -> dict:
    from ..notify import vapid_keys

    keys = vapid_keys(load_config())
    return {"publicKey": keys["public"] if keys else None}


@app.post("/api/push/subscribe")
async def push_subscribe(request: Request, x_sz: str | None = Header(default=None)) -> dict:
    _guard(x_sz)
    from ..notify import save_subscription

    cfg = load_config()
    with DB(cfg.db_path) as db:
        try:
            sub = await request.json()
            if not isinstance(sub, dict):
                raise ValueError("subscription must be a JSON object")
            save_subscription(db, sub, request.headers.get("user-agent", ""))
        except (ValueError, KeyError) as exc:
            raise HTTPException(400, str(exc)) from exc
    return {"ok": True}


# -- voice + hologram -------------------------------------------------------

def _fanout(state: dict) -> None:
    for q in list(_subscribers):
        try:
            q.put_nowait(state)
        except asyncio.QueueFull:
            pass


def _agent_fanout(event: dict) -> None:
    for q in list(_agent_subs):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            pass


def _broadcast_agent(event: dict) -> None:
    """Push an event to the command center's live stream (thread-safe)."""
    event = {**event, "at": time.time()}
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if _loop is not None and running is not _loop:
        _loop.call_soon_threadsafe(_agent_fanout, event)
    elif running is not None:
        _agent_fanout(event)


def _broadcast(event: dict) -> None:
    """Safe to call from the event loop or from worker threads (sync endpoints)."""
    _voice_state.update(event)
    state = dict(_voice_state)
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if _loop is not None and running is not _loop:
        _loop.call_soon_threadsafe(_fanout, state)
    else:
        _fanout(state)


@app.post("/api/voice/event")
async def voice_event(body: dict, x_sz: str | None = Header(default=None)) -> dict:
    _guard(x_sz)
    if body.get("state") not in {"idle", "listening", "thinking", "speaking"}:
        raise HTTPException(400, "bad state")
    try:
        envelope = [float(x) for x in (body.get("envelope") or [])][:20000]
        frame_ms = int(body.get("frame_ms", 40))
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, "envelope must be a list of numbers, frame_ms a number") from exc
    if not all(math.isfinite(x) for x in envelope):
        raise HTTPException(400, "envelope must be finite numbers")
    _broadcast({"state": body["state"], "text": str(body.get("text", ""))[:600],
                "envelope": envelope, "frame_ms": frame_ms, "at": time.time()})
    return {"ok": True, "listeners": len(_subscribers)}


@app.get("/api/voice/stream")
async def voice_stream(request: Request) -> StreamingResponse:
    global _loop
    _loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue(maxsize=50)
    _subscribers.add(q)

    async def gen():
        try:
            yield f"data: {json.dumps(_voice_state)}\n\n"
            while not await request.is_disconnected():
                try:
                    event = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"data: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            _subscribers.discard(q)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


class _Channel:
    """Adapter that lets one /api/events queue sit in a per-channel subscriber set; tags each event with `ch`."""

    def __init__(self, q: asyncio.Queue, ch: str):
        self.q, self.ch = q, ch

    def put_nowait(self, event: dict) -> None:
        self.q.put_nowait({**event, "ch": self.ch})


@app.get("/api/events")
async def events_stream(request: Request) -> StreamingResponse:
    """One stream per page for everything live (voice state, agent activity, notifications), each event tagged
    with `ch`: browsers allow only ~6 connections per host over HTTP/1.1, so separate streams per tab run out."""
    global _loop
    _loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue(maxsize=200)
    subs = [(_subscribers, _Channel(q, "voice")), (_agent_subs, _Channel(q, "agent")), (_notify_subs, _Channel(q, "notify"))]
    for group, sub in subs:
        group.add(sub)

    async def gen():
        try:
            yield f"data: {json.dumps({**_voice_state, 'ch': 'voice'})}\n\n"
            while not await request.is_disconnected():
                try:
                    yield f"data: {json.dumps(await asyncio.wait_for(q.get(), timeout=15))}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            for group, sub in subs:
                group.discard(sub)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@app.post("/api/ask")
def ask(body: dict, x_sz: str | None = Header(default=None)) -> dict:
    """Ask Setz from the dashboard. With speak=true and ElevenLabs set up, the reply comes back as audio
    that the page plays while the hologram animates."""
    _guard(x_sz)
    from ..mailtext import detect_language
    from ..voice import answer, clean_for_speech

    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "empty question")
    cfg = load_config()
    lang = "de" if detect_language(text) == "de" else "en"
    _broadcast({"state": "thinking", "text": text, "envelope": [], "at": time.time()})
    try:
        reply = answer(cfg, text, lang)
    except Exception as exc:
        _broadcast({"state": "idle", "text": "", "envelope": []})
        raise HTTPException(500, f"Could not answer: {exc}") from exc
    out: dict = {"answer": reply}
    if body.get("speak") and tts.elevenlabs_enabled(cfg):
        try:
            pcm = tts.synthesize(cfg, clean_for_speech(reply))
            out["audio"] = base64.b64encode(tts.wav_bytes(pcm)).decode()
            env = tts.envelope(pcm)
            _broadcast({"state": "speaking", "text": reply, "envelope": env, "frame_ms": 40, "at": time.time()})
        except Exception as exc:
            out["tts_error"] = str(exc)
            _broadcast({"state": "idle", "text": reply, "envelope": []})
    else:
        _broadcast({"state": "idle", "text": reply, "envelope": []})
    return out


# -- market intelligence: partners, competitors, whom to onboard next ----------------------------------------

@app.get("/api/market")
def market_view(ai: int = 0, x_sz: str | None = Header(default=None)) -> dict:
    from .. import market

    cfg = load_config()
    with DB(cfg.db_path) as db:
        if ai:
            _guard(x_sz)   # spends API credits
            return market.strategy(cfg, db)
        return {"partners": market.partner_analysis(cfg, db), "competitors": market.competitor_analysis(cfg, db),
                "onboard_next": market.onboard_next(cfg, db, 15), "summary": ""}


@app.get("/api/market/onboard-next")
def market_next(limit: int = 15, bucket: str = "", campus: str = "") -> list[dict]:
    from ..market import onboard_next

    cfg = load_config()
    with DB(cfg.db_path) as db:
        return onboard_next(cfg, db, max(1, min(limit, 100)), bucket or None, campus or None)


# -- universities & campuses (OpenStreetMap) ----------------------------------------------------------------

@app.get("/api/campuses")
def campus_list() -> dict:
    from ..leadgen import CAMPUSES, coverage, discovered_campuses

    cfg = load_config()
    with DB(cfg.db_path) as db:
        return {"coverage": coverage(cfg, db), "built_in": len(CAMPUSES), "discovered": discovered_campuses(cfg),
                "last_discovery": db.kv_get("last_run:universities")}


@app.post("/api/leadgen/universities")
def campus_discover(x_sz: str | None = Header(default=None)) -> dict:
    _guard(x_sz)
    from ..leadgen import discover_universities

    cfg = load_config()
    with DB(cfg.db_path) as db:
        try:
            return discover_universities(cfg, db)
        except RuntimeError as exc:
            raise HTTPException(502, str(exc)) from exc


@app.post("/api/campuses/toggle")
def campus_toggle(body: dict, x_sz: str | None = Header(default=None)) -> dict:
    _guard(x_sz)
    from ..leadgen import set_campus_enabled

    if not set_campus_enabled(load_config(), str(body.get("name", "")), bool(body.get("enabled", True))):
        raise HTTPException(404, "no such discovered campus")
    return {"ok": True}


# -- Lex: contracts ------------------------------------------------------------------------------------------

@app.get("/api/contracts")
def contracts_list() -> dict:
    from .. import contracts as lex

    cfg = load_config()
    with DB(cfg.db_path) as db:
        return {"contracts": lex.contracts(db), "templates": lex.templates(cfg),
                "partners": [dict(r) for r in db.q("SELECT id, name, stage, status FROM partners ORDER BY name")]}


@app.post("/api/contracts")
def contracts_prepare(body: dict, x_sz: str | None = Header(default=None)) -> dict:
    _guard(x_sz)
    from .. import contracts as lex

    cfg = load_config()
    fields = body.get("fields") or {}
    if not isinstance(fields, dict):
        raise HTTPException(400, "fields must be an object")
    try:
        pid = int(body["partner_id"]) if body.get("partner_id") else None
        cid = int(body["id"]) if body.get("id") else None
        with DB(cfg.db_path) as db:
            r = lex.prepare(cfg, db, str(body.get("template") or "partner_de"), partner_id=pid, fields=fields, contract_id=cid)
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {k: v for k, v in r.items() if k != "text"} | {"text": r["text"]}


@app.get("/api/contracts/{cid}")
def contracts_get(cid: int) -> dict:
    from .. import contracts as lex

    with DB(load_config().db_path) as db:
        try:
            return lex.get(db, cid)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc


@app.get("/api/contracts/{cid}/download")
def contracts_download(cid: int, fmt: str = "docx") -> FileResponse:
    from .. import contracts as lex

    cfg = load_config()
    with DB(cfg.db_path) as db:
        try:
            c = lex.get(db, cid)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
    path = Path(c["path_docx"] if fmt == "docx" else c["path_md"] or "")
    root = (cfg.data_dir / "contracts").resolve()
    if not c.get("path_docx" if fmt == "docx" else "path_md") or not path.is_file() or root not in path.resolve().parents:
        raise HTTPException(404, "file not available (install the contracts extra for .docx)")
    return FileResponse(path, filename=path.name.encode("ascii", "ignore").decode() or f"contract.{fmt}")


@app.post("/api/contracts/{cid}/status")
def contracts_status(cid: int, body: dict, x_sz: str | None = Header(default=None)) -> dict:
    _guard(x_sz)
    from .. import contracts as lex

    with DB(load_config().db_path) as db:
        try:
            lex.set_status(db, cid, str(body.get("status", "")))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    return {"ok": True}


@app.post("/api/contracts/analyse")
async def contracts_analyse(request: Request, x_sz: str | None = Header(default=None), x_filename: str = Header(default="draft.txt"),
                            ai: int = 0) -> dict:
    """Body = the file (docx / pdf / txt / md), file name in X-Filename. Or JSON {"text": ...}."""
    _guard(x_sz)
    from .. import contracts as lex

    data = await request.body()
    if len(data) > 15_000_000:
        raise HTTPException(413, "file too large (15 MB max)")
    cfg = load_config()
    name = Path(x_filename).name or "draft.txt"
    if request.headers.get("content-type", "").startswith("application/json"):
        try:
            data, name = str(json.loads(data).get("text", "")).encode(), "pasted.txt"
        except (ValueError, AttributeError) as exc:
            raise HTTPException(400, "invalid JSON") from exc
    def review() -> dict:
        with DB(cfg.db_path) as db:   # opened in the worker thread that uses it
            return lex.review_file(cfg, db, name, data, ai=bool(ai))

    try:
        return await asyncio.to_thread(review)
    except (ValueError, RuntimeError, KeyError) as exc:
        raise HTTPException(400, f"Could not read the file: {exc}") from exc


# -- council: agents' messages, peer review, brains ------------------------------------------------------------

@app.get("/api/council")
def council_view() -> dict:
    from .. import council

    cfg = load_config()
    with DB(cfg.db_path) as db:
        return council.overview(cfg, db)


@app.post("/api/council/run")
def council_run(x_sz: str | None = Header(default=None)) -> dict:
    _guard(x_sz)
    from .. import council

    cfg = load_config()
    with DB(cfg.db_path) as db:
        r = council.cross_check(cfg, db, autofix=False)
    _broadcast_agent({"agent": "sentinel", "status": "ok", "summary": f"Peer review: {r['messages']} open, {len(r['new'])} new"})
    return r


@app.post("/api/council/messages/{mid}")
def council_message(mid: int, body: dict, x_sz: str | None = Header(default=None)) -> dict:
    _guard(x_sz)
    from .. import council

    cfg = load_config()
    action = body.get("action")
    with DB(cfg.db_path) as db:
        try:
            if action == "fix":
                return {"result": council.apply_fix(cfg, db, mid)}
            council.set_message(db, mid, {"dismiss": "dismissed", "resolve": "resolved", "reopen": "open"}.get(action, ""))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    return {"ok": True}


# -- autopilot & approvals ---------------------------------------------------------------------------------------

@app.get("/api/autopilot")
def autopilot_view() -> dict:
    from .. import autopilot

    cfg = load_config()
    with DB(cfg.db_path) as db:
        return {"rules": autopilot.rules(cfg), "pending": autopilot.pending(db), "history": autopilot.history(db),
                "last_run": db.kv_get("last_run:autopilot")}


@app.post("/api/approvals/{aid}")
def approval_decide(aid: int, body: dict, x_sz: str | None = Header(default=None)) -> dict:
    _guard(x_sz)
    from .. import autopilot

    cfg = load_config()
    with DB(cfg.db_path) as db:
        try:
            return autopilot.decide(cfg, db, aid, bool(body.get("approve")))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc


# -- bridge: admin panel + mobile app ------------------------------------------------------------------------------

@app.get("/api/bridge")
def bridge_view() -> dict:
    from .. import bridge

    cfg = load_config()
    with DB(cfg.db_path) as db:
        return bridge.status(cfg, db)


@app.post("/api/bridge/webhook")
async def bridge_webhook(request: Request) -> dict:
    """Events from the admin panel (Standard Webhooks signature). Open path: the signature is the authentication."""
    from .. import bridge

    body = await request.body()
    if len(body) > 1_000_000:
        raise HTTPException(413, "too large")
    try:
        bridge.verify(dict(request.headers), body)
        event = json.loads(body)
        if not isinstance(event, dict):
            raise ValueError("event must be an object")
    except ValueError as exc:
        raise HTTPException(401 if "signature" in str(exc) or "header" in str(exc) or "timestamp" in str(exc) or "off" in str(exc) else 400,
                            str(exc)) from exc
    cfg = load_config()
    with DB(cfg.db_path) as db:
        return bridge.handle_event(cfg, db, request.headers.get("webhook-id", ""), event)


@app.get("/api/bridge/v1/recommendations")
def bridge_recommendations(authorization: str = Header(default="")) -> dict:
    """For the admin panel: Setz's onboarding recommendations (Bearer SZ_BRIDGE_TOKEN)."""
    import hmac

    from ..config import secret
    from ..market import onboard_next

    token = secret("SZ_BRIDGE_TOKEN")
    if not token or not hmac.compare_digest(authorization.removeprefix("Bearer ").strip().encode(), token.encode()):
        raise HTTPException(401, "invalid token")
    cfg = load_config()
    with DB(cfg.db_path) as db:
        recs = onboard_next(cfg, db, 20)
    return {"items": [{k: r[k] for k in ("name", "bucket", "category", "campus", "distance_m", "priority", "reasons", "pitch", "status")}
                      for r in recs]}


# -- phone: push-to-talk and the share sheet --------------------------------------------------------------------

@app.get("/talk")
def talk_page() -> FileResponse:
    return FileResponse(STATIC / "talk.html")


_whisper = None
_whisper_lock = threading.Lock()


def _transcribe(audio_bytes: bytes) -> tuple[str, str]:
    """Phone recording (webm/ogg/m4a/wav) -> text with the laptop's Whisper (same model as the voice loop)."""
    global _whisper
    from ..speech import decode

    audio = decode(audio_bytes)
    with _whisper_lock:
        if _whisper is None:
            from faster_whisper import WhisperModel

            cfg = load_config()
            name = cfg.get("voice.whisper_model", "small")
            try:
                _whisper = WhisperModel(name, device="cuda", compute_type="int8_float16")
            except Exception:
                _whisper = WhisperModel(name, device="cpu", compute_type="int8")
        segments, info = _whisper.transcribe(audio, beam_size=1, vad_filter=True)
        text = " ".join(s.text for s in segments).strip()
    return text, (info.language if info.language in ("en", "de") else "en")


@app.post("/api/voice/ask-audio")
async def ask_audio(request: Request, x_sz: str | None = Header(default=None), speak: int = 1) -> dict:
    """Push-to-talk from the phone: audio in, Setz's answer (text + voice) out."""
    _guard(x_sz)
    data = await request.body()
    if not data:
        raise HTTPException(400, "no audio")
    if len(data) > 12_000_000:
        raise HTTPException(413, "recording too long")
    try:
        text, _lang = await asyncio.to_thread(_transcribe, data)
    except (ImportError, RuntimeError) as exc:
        raise HTTPException(501, "Speech recognition needs the voice extras on the laptop: pip install -e .[voice]") from exc
    except Exception as exc:
        raise HTTPException(400, f"Could not read the recording: {exc}") from exc
    if not text:
        return {"text": "", "answer": "I didn't catch that."}
    out = await asyncio.to_thread(ask, {"text": text, "speak": bool(speak)}, "1")
    return {"text": text, **out}


_SHARE_MAX = 15_000_000


@app.post("/share")
async def share_target(request: Request) -> Response:
    """Android share sheet -> Setz. Shared items wait in the inbox until you pick what Setz does with them."""
    import secrets as _secrets

    form = await request.form()
    cfg = load_config()
    item = {"id": _secrets.token_hex(6), "at": datetime.now().isoformat(timespec="seconds"),
            "title": str(form.get("title") or "")[:300], "text": str(form.get("text") or "")[:20000], "url": str(form.get("url") or "")[:2000]}
    f = form.get("file")
    if f is not None and hasattr(f, "read"):
        data = await f.read()
        if 0 < len(data) <= _SHARE_MAX:
            inbox = cfg.data_dir / "inbox"
            inbox.mkdir(exist_ok=True)
            safe = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(getattr(f, "filename", "") or "shared").name)[-80:] or "shared"
            dest = inbox / f"{item['id']}-{safe}"
            dest.write_bytes(data)
            item["file"] = dest.name
    with DB(cfg.db_path) as db:
        items = json.loads(db.kv_get("share:inbox", "[]") or "[]")
        db.kv_set("share:inbox", json.dumps(([item] + items)[:30], ensure_ascii=False))
    return RedirectResponse(f"/?shared={item['id']}#approvals", 303)


@app.get("/api/share/inbox")
def share_inbox() -> list[dict]:
    from ..share import suggest

    cfg = load_config()
    with DB(cfg.db_path) as db:
        return [it | {"suggestions": suggest(it)} for it in json.loads(db.kv_get("share:inbox", "[]") or "[]")]


@app.post("/api/share/{sid}")
def share_act(sid: str, body: dict, x_sz: str | None = Header(default=None)) -> dict:
    _guard(x_sz)
    from ..share import act

    cfg = load_config()
    with DB(cfg.db_path) as db:
        items = json.loads(db.kv_get("share:inbox", "[]") or "[]")
        it = next((i for i in items if i["id"] == sid), None)
        if it is None:
            raise HTTPException(404, "not in the inbox")
        try:
            result = act(cfg, db, it, str(body.get("action", "")))
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(400, str(exc)) from exc
        db.kv_set("share:inbox", json.dumps([i for i in items if i["id"] != sid], ensure_ascii=False))
    return result


def serve(port: int = 8765) -> None:
    import uvicorn

    cfg = load_config()
    host = str(cfg.get("dashboard.host", "127.0.0.1"))
    if host not in LOOPBACK and not cfg.get("dashboard.access_pin"):
        raise RuntimeError("dashboard.host exposes Setz to your network: set [dashboard] access_pin first (or use Tailscale, see README)")
    uvicorn.run(app, host=host, port=port, log_level="warning")
