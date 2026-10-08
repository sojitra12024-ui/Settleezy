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
    from .leads import enrich
    from .scraping.fetcher import Fetcher

    f = Fetcher(db, min_delay=float(cfg.get("scraping.min_delay_seconds", 4)))
    try:
        return {"enriched": enrich(db, f, int(cfg.get("scraping.enrich_per_run", 20)))}
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
}

# What `sz morning` runs, in order. Sources that aren't configured are skipped, not fatal.
MORNING = ["doctor", "mail", "calendly", "instagram", "track", "ops", "agents", "drafts", "brief"]


def run_job(cfg: Config, name: str) -> Any:
    with DB(cfg.db_path) as db:
        result = JOBS[name](cfg, db)
        db.kv_set(f"last_run:{name}", utcnow())
        db.kv_set(f"last_result:{name}", json.dumps(result, default=str, ensure_ascii=False)[:4000])
        return result
