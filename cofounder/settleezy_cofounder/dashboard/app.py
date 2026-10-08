"""Local dashboard (http://127.0.0.1:8765): Outlook, Calendly, Instagram, leads, competitors, briefs.

Runs on localhost only. State-changing endpoints require the `X-SZ` header, which browsers can't add
cross-site without a CORS preflight (which this app never grants), so other websites can't trigger them.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse

from .. import jobs
from ..config import load_config
from ..db import DB
from ..leads import pipeline, set_status, top
from ..scraping.monitor import new_listings

STATIC = Path(__file__).parent / "static"
app = FastAPI(title="Settleezy HQ", docs_url=None, redoc_url=None)
_running: dict[str, str] = {}


def _guard(x_sz: str | None) -> None:
    if x_sz != "1":
        raise HTTPException(403, "missing X-SZ header")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/summary")
def summary() -> dict:
    cfg = load_config()
    with DB(cfg.db_path) as db:
        today = datetime.now().date().isoformat()
        since14 = (datetime.now() - timedelta(days=14)).date().isoformat()
        since7 = (datetime.now() - timedelta(days=7)).isoformat()
        triage = {"replies": [], "followups": []}
        try:
            from ..triage import followups_due, needs_reply

            triage["replies"] = [i.as_dict() for i in needs_reply(cfg, db, None)][:25]
            triage["followups"] = [i.as_dict() for i in followups_due(cfg, db)][:25]
        except Exception as exc:  # empty db on first launch
            triage["error"] = str(exc)
        metrics: dict[str, list] = {}
        for r in db.q("SELECT day, source, key, value FROM metrics WHERE day >= ? ORDER BY day", (since14,)):
            metrics.setdefault(f"{r['source']}.{r['key']}", []).append([r["day"], r["value"]])
        listings_by_day = [dict(r) for r in db.q(
            "SELECT substr(first_seen,1,10) day, source, COUNT(*) n FROM listings WHERE first_seen != 'baseline' AND first_seen >= ? "
            "GROUP BY day, source ORDER BY day", (since14,))]
        stats_path = cfg.data_dir / "email_stats.json"
        email_stats = json.loads(stats_path.read_text(encoding="utf-8")) if stats_path.exists() else {}
        briefs = sorted((cfg.data_dir / "briefs").glob("*.md")) if (cfg.data_dir / "briefs").exists() else []
        reports = sorted((cfg.data_dir / "reports").glob("*.md")) if (cfg.data_dir / "reports").exists() else []
        return {
            "now": datetime.now().isoformat(timespec="minutes"),
            "me": cfg.me.get("name", ""),
            "events_today": [dict(r) for r in db.q("SELECT * FROM events WHERE substr(start,1,10)=? ORDER BY start", (today,))],
            "events_upcoming": [dict(r) for r in db.q("SELECT * FROM events WHERE substr(start,1,10)>? ORDER BY start LIMIT 15", (today,))],
            **triage,
            "drafts_7d": db.one("SELECT COUNT(*) n FROM drafts WHERE created_at >= ?", (since7,))["n"],
            "new_listings": new_listings(db, 24 * 7)[:60],
            "listings_by_day": listings_by_day,
            "listings_total": db.one("SELECT COUNT(*) n FROM listings")["n"],
            "leads_top": top(db, status="new", limit=25),
            "leads_active": [dict(r) for r in db.q(
                "SELECT * FROM leads WHERE status IN ('drafted','contacted','replied','meeting') ORDER BY updated_at DESC LIMIT 25")],
            "leads_new_7d": db.one("SELECT COUNT(*) n FROM leads WHERE created_at >= ?", (since7,))["n"],
            "pipeline": pipeline(db),
            "metrics": metrics,
            "instagram_comments": json.loads(db.kv_get("instagram:unanswered_comments", "[]")),
            "instagram_top_posts": json.loads(db.kv_get("instagram:top_posts", "[]")),
            "outreach": email_stats.get("outreach", {}),
            "response_time": email_stats.get("response_time_hours", {}),
            "brief": briefs[-1].read_text(encoding="utf-8") if briefs else "",
            "report": reports[-1].read_text(encoding="utf-8") if reports else "",
            "last_sync": {k: db.kv_get(f"last_run:{k}") for k in jobs.JOBS},
            "running": dict(_running),
        }


@app.post("/api/leads/{lead_id}/status")
def lead_status(lead_id: int, body: dict, x_sz: str | None = Header(default=None)) -> dict:
    _guard(x_sz)
    cfg = load_config()
    with DB(cfg.db_path) as db:
        set_status(db, lead_id, body.get("status", ""), body.get("note", ""))
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


@app.post("/api/run/{job}")
def run_job(job: str, x_sz: str | None = Header(default=None)) -> dict:
    _guard(x_sz)
    if job not in jobs.JOBS:
        raise HTTPException(404, f"unknown job {job}")
    if job in _running:
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


def serve(port: int = 8765) -> None:
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
