"""Local dashboard (http://127.0.0.1:8765) and voice hologram (/hologram).

Runs on localhost only. State-changing endpoints require the `X-SZ` header, which browsers can't add
cross-site without a CORS preflight (which this app never grants), so other websites can't trigger them.
"""

from __future__ import annotations

import asyncio
import base64
import json
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse

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


def _guard(x_sz: str | None) -> None:
    if x_sz != "1":
        raise HTTPException(403, "missing X-SZ header")


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
    since = (datetime.now() - timedelta(days=days)).date().isoformat()
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
    params.append(min(limit, 1000))
    with DB(cfg.db_path) as db:
        return [dict(r) for r in db.q(sql, params)]


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
    cfg = load_config()
    with DB(cfg.db_path) as db:
        tid = add_task(db, title, due=body.get("due") or None, priority=int(body.get("priority", 2)), source=body.get("source", "manual"),
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
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    return {"ok": True}


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
def prep(event_id: str, ai: int = 0) -> dict:
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
    if _running.get(job) == "running":
        return {"started": False, "status": "already running"}
    _running[job] = "running"

    def work() -> None:
        try:
            jobs.run_job(load_config(), job)
            _running.pop(job, None)
        except Exception as exc:
            _running[job] = f"error: {exc}"

    threading.Thread(target=work, daemon=True).start()
    return {"started": True}


# -- voice + hologram -------------------------------------------------------

def _fanout(state: dict) -> None:
    for q in list(_subscribers):
        try:
            q.put_nowait(state)
        except asyncio.QueueFull:
            pass


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
    _broadcast({"state": body["state"], "text": str(body.get("text", ""))[:600],
                "envelope": [float(x) for x in body.get("envelope", [])][:20000],
                "frame_ms": int(body.get("frame_ms", 40)), "at": time.time()})
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


def serve(port: int = 8765) -> None:
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
