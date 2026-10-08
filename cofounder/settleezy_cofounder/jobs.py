"""Named jobs shared by the CLI, the scheduler and the dashboard's refresh buttons."""

from __future__ import annotations

import json
from typing import Any, Callable

from .config import Config
from .db import DB, utcnow


def _mail(cfg: Config, db: DB) -> Any:
    from .mail_sync import sync, sync_calendar
    from .msgraph import Graph

    g = Graph(cfg)
    out = {"mail": sync(cfg, db, g), "calendar_events": sync_calendar(cfg, db, g)}
    out["tracking"] = _track(cfg, db)   # match sent drafts / replies right after new mail arrives
    out["ops"] = _ops(cfg, db)          # post-meeting follow-ups, stuck onboarding, renewals -> to-dos
    from .scheduling import detect

    out["meeting_requests"] = detect(cfg, db)   # "can we have a call?" -> slots ready on the dashboard
    return out


def _track(cfg: Config, db: DB) -> Any:
    from .tracking import snapshot, track_drafts

    return {**track_drafts(cfg, db), **snapshot(cfg, db)}


def _ops(cfg: Config, db: DB) -> Any:
    from .ops import sync_auto_tasks

    return {"auto_tasks_created": sync_auto_tasks(cfg, db)}


def _agents(cfg: Config, db: DB) -> Any:
    from .agents import run

    return run(cfg, db)


def _doctor(cfg: Config, db: DB) -> Any:
    from .connections import run_all

    return {r["name"]: ("ok" if r["ok"] else r["detail"]) for r in run_all(cfg, db)}


def _calendly(cfg: Config, db: DB) -> Any:
    from .calendly import sync

    return sync(cfg, db)


def _instagram(cfg: Config, db: DB) -> Any:
    from .instagram import sync

    return sync(cfg, db)


def _scrape(cfg: Config, db: DB) -> Any:
    from .llm import LLM
    from .scraping.monitor import run

    return run(cfg, db, LLM(cfg))


def _enrich(cfg: Config, db: DB) -> Any:
    from .leadintel import enrich_batch
    from .scraping.fetcher import Fetcher

    f = Fetcher(db, min_delay=float(cfg.get("scraping.min_delay_seconds", 4)))
    try:   # website + menu + Impressum: email, phone, owner, legal address, price level
        return enrich_batch(db, f, int(cfg.get("scraping.enrich_per_run", 20)))
    finally:
        f.close()


def _drafts(cfg: Config, db: DB) -> Any:
    from .drafts import run

    return run(cfg, db)


def _brief(cfg: Config, db: DB) -> Any:
    from .brief import build

    md, _ = build(cfg, db)
    return {"chars": len(md)}


def _learn(cfg: Config, db: DB) -> Any:
    from .llm import LLM
    from .voice_profile import build_profile

    return [str(p) for p in build_profile(cfg, db, LLM(cfg))]


def _growth(cfg: Config, db: DB) -> Any:
    from .growth import review

    return {"chars": len(review(cfg, db))}


def _leadgen(cfg: Config, db: DB) -> Any:
    from .leadgen import discover

    return discover(cfg, db)


def _pipeline(cfg: Config, db: DB) -> Any:
    """Weekly: start outreach sequences for the best new leads, up to this week's capacity."""
    from .pipeline import auto_start

    return {"sequences_started": auto_start(cfg, db)}


def _igdiscover(cfg: Config, db: DB) -> Any:
    from .igdiscovery import discover

    return discover(cfg, db)


def _brain(cfg: Config, db: DB) -> Any:
    from .brain import learn

    return learn(cfg, db)


JOBS: dict[str, Callable[[Config, DB], Any]] = {
    "mail": _mail,
    "track": _track,
    "ops": _ops,
    "agents": _agents,
    "doctor": _doctor,
    "calendly": _calendly,
    "instagram": _instagram,
    "scrape": _scrape,
    "enrich": _enrich,
    "drafts": _drafts,
    "brief": _brief,
    "learn": _learn,
    "growth": _growth,
    "leadgen": _leadgen,
    "pipeline": _pipeline,
    "brain": _brain,
    "igdiscover": _igdiscover,
}

# What `sz morning` runs, in order. Sources that aren't configured are skipped, not fatal.
MORNING = ["doctor", "mail", "calendly", "instagram", "track", "ops", "agents", "brain", "drafts", "brief"]


def run_job(cfg: Config, name: str) -> Any:
    from .notify import notify, refresh

    with DB(cfg.db_path) as db:
        db.kv_set(f"running:{name}", utcnow())
        try:
            result = JOBS[name](cfg, db)
        except Exception as exc:
            db.kv_set(f"last_error:{name}", f"{utcnow()} {type(exc).__name__}: {exc}"[:500])
            notify(cfg, db, f"Setz job failed: {name}", f"{type(exc).__name__}: {exc}"[:300], kind="alert", url="/command",
                   key=f"jobfail:{name}:{utcnow()[:13]}")   # at most one pop-up per job per hour
            raise
        finally:
            db.x("DELETE FROM kv WHERE key=?", (f"running:{name}",))
        db.kv_set(f"last_run:{name}", utcnow())
        db.kv_set(f"last_result:{name}", json.dumps(result, default=str, ensure_ascii=False)[:4000])
        _notify_after(cfg, db, name, result)
        refresh(cfg, name)
        return result


def _notify_after(cfg: Config, db: DB, name: str, result: Any) -> None:
    """Turn what a job found into pop-ups (each only once). Never breaks the job."""
    try:
        _notify_findings(cfg, db, name, result)
    except Exception as exc:
        db.kv_set("last_error:notify", f"{utcnow()} {name}: {type(exc).__name__}: {exc}"[:500])


def _notify_findings(cfg: Config, db: DB, name: str, result: Any) -> None:
    from .notify import notify

    if name in ("mail", "track"):
        from .scheduling import _ensure as ensure_requests

        ensure_requests(db)   # the table may not exist before the first mail sync
        for r in db.q("SELECT * FROM meeting_requests WHERE status='open' AND detected_at >= datetime('now','-1 day')"):
            notify(cfg, db, f"Meeting request: {r['who']}", f"{r['subject']} · slots are ready to book", kind="meeting",
                   url="/#today", key=f"mreq:{r['id']}")
        for e in db.q("SELECT e.id, l.name FROM lead_events e JOIN leads l ON l.id=e.lead_id WHERE e.to_status='replied' "
                      "AND e.at >= datetime('now','-1 day')"):
            notify(cfg, db, f"{e['name']} replied 🎉", "Book a call while they're warm.", kind="lead", url="/#pipeline",
                   key=f"replied:{e['id']}")
    if name == "agents":
        synth = json.loads(db.kv_get("setz:synthesis", "{}") or "{}")
        for f in [p for p in synth.get("priorities", []) if p.get("severity") == "alert"][:2]:
            notify(cfg, db, f["title"], f.get("detail", ""), kind="alert", url="/command",
                   key=f"alert:{f.get('agent')}:{f.get('key', f['title'])}:{utcnow()[:10]}")
    if name == "igdiscover" and isinstance(result, dict) and result.get("new"):
        notify(cfg, db, f"{result['new']} new venues found on Instagram", f"{result.get('venues', 0)} venues · {result.get('brands', 0)} brands",
               kind="lead", url="/#leads", key=f"igd:{utcnow()[:13]}")
    if name == "leadgen" and isinstance(result, dict) and result.get("new"):
        notify(cfg, db, f"{result['new']} new venues near campuses", "Open the Lead finder to filter them.", kind="lead",
               url="/#leads", key=f"leadgen:{utcnow()[:10]}")
