"""Operations: what Setz is working on, what's scheduled, and multi-step playbooks it can run on its own.

  missions   the open work queues across the business (outreach steps due, leads missing contacts, meeting
             requests, replies owed, pipeline gap, partners at risk, unscanned campuses, ...) with the agent
             that owns each one and the job or page that resolves it
  jobs       every scheduled job with its schedule, last run, last error, whether it's running now and when it
             runs next (mirrors scripts/register_tasks.ps1)
  playbooks  chains of jobs, e.g. "prospecting" = campus scan -> Impressum enrichment -> Instagram discovery ->
             brain learning -> start outreach; run with one click, by voice or `sz playbook prospecting`
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any, Callable

from .config import Config
from .db import DB

WD = [0, 1, 2, 3, 4]
ALL = [0, 1, 2, 3, 4, 5, 6]
# job -> (label, days, times). Keep in sync with scripts/register_tasks.ps1.
SCHEDULE: dict[str, tuple[str, list[int], list[str]]] = {
    "morning": ("Morning routine + brief", WD, ["07:45"]),
    "mail": ("Outlook sync + meeting requests", ALL, ["09:00", "11:00", "13:00", "15:00", "17:00", "19:00"]),
    "agents": ("Agent team report", ALL, ["08:30", "10:30", "12:30", "14:30", "16:30", "18:30"]),
    "scrape": ("Competitor scan", ALL, ["08:15", "14:15", "20:15"]),
    "enrich": ("Impressum enrichment", ALL, ["13:00"]),
    "drafts": ("Reply + follow-up drafts", WD, ["14:30"]),
    "instagram": ("Instagram sync", ALL, ["18:00"]),
    "igdiscover": ("Instagram venue discovery", [1, 4], ["12:15"]),
    "leadgen": ("Campus venue scan (OSM)", [6], ["21:00"]),
    "pipeline": ("Start outreach sequences", [0], ["07:30"]),
    "growth": ("Weekly growth review", [0], ["07:15"]),
    "learn": ("Re-learn your writing voice", [6], ["20:00"]),
}
JOB_AGENT = {"mail": "hermes", "track": "hermes", "drafts": "hermes", "learn": "hermes", "scrape": "scout", "leadgen": "hunter",
             "enrich": "hunter", "pipeline": "hunter", "igdiscover": "nova", "instagram": "nova", "growth": "quant",
             "calendly": "chrono", "ops": "atlas", "doctor": "sentinel", "agents": "setz", "brain": "setz", "brief": "setz",
             "morning": "setz"}
PLAYBOOKS: dict[str, dict[str, Any]] = {
    "prospecting": {"label": "Prospecting run", "steps": ["leadgen", "enrich", "igdiscover", "brain", "pipeline"],
                    "what": "Find venues near campuses and on Instagram, read their Impressum, learn, start outreach"},
    "inbox": {"label": "Inbox zero", "steps": ["mail", "track", "drafts"],
              "what": "Sync Outlook, match replies, write reply and follow-up drafts"},
    "intelligence": {"label": "Market intelligence", "steps": ["scrape", "instagram", "agents", "growth"],
                     "what": "Competitor listings, Instagram numbers, agent reports, growth review"},
    "learning": {"label": "Brain refresh", "steps": ["brain", "learn"],
                 "what": "Refresh memories, retrain the lead model, re-learn your writing voice"},
}


def next_run(job: str, now: datetime | None = None) -> str | None:
    if job not in SCHEDULE:
        return None
    now = now or datetime.now()
    _, days, times = SCHEDULE[job]
    for i in range(8):
        d = now.date() + timedelta(days=i)
        if d.weekday() not in days:
            continue
        for t in times:
            dt = datetime.combine(d, datetime.strptime(t, "%H:%M").time())
            if dt > now:
                return dt.isoformat(timespec="minutes")
    return None


def jobs_status(db: DB, running: dict[str, str] | None = None) -> list[dict]:
    from .jobs import JOBS

    running = running or {}
    out = []
    for name in ["morning", *[j for j in JOBS if j != "morning"]]:
        label = SCHEDULE.get(name, (name.capitalize(), [], []))[0]
        last = db.kv_get(f"last_run:{name}")
        err = db.kv_get(f"last_error:{name}")
        err_newer = bool(err and (not last or err[:19] > last[:19]))
        out.append({"name": name, "label": label, "agent": JOB_AGENT.get(name, "setz"), "scheduled": name in SCHEDULE,
                    "schedule": _describe(name), "last_run": last, "next_run": next_run(name),
                    "running": running.get(name) == "running" or bool(db.kv_get(f"running:{name}")),
                    "on_demand": name not in SCHEDULE,
                    "error": err.split(" ", 1)[-1] if err_newer else (running.get(name, "")[7:] if str(running.get(name, "")).startswith("error") else "")})
    # running first, then scheduled by next run, then on-demand jobs
    return sorted(out, key=lambda j: (not j["running"], j["on_demand"], j["next_run"] or "9", j["name"]))


def _describe(job: str) -> str:
    if job not in SCHEDULE:
        return "on demand"
    _, days, times = SCHEDULE[job]
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    d = "daily" if days == ALL else "weekdays" if days == WD else "/".join(names[x] for x in days)
    return f"{d} {times[0]}" + (f" + {len(times) - 1}×" if len(times) > 1 else "")


def missions(cfg: Config, db: DB) -> list[dict]:
    """Open work across the business, most important first. Each resolves through a job or a dashboard page."""
    today = date.today().isoformat()
    out: list[dict] = []

    def add(key, title, count, detail, agent, priority, action, target):
        if count:
            out.append({"key": key, "title": title, "count": count, "detail": detail, "agent": agent, "priority": priority,
                        "action": action, "target": target})

    q1 = lambda sql, *p: db.one(sql, p)["n"]  # noqa: E731
    try:
        from .scheduling import _ensure

        _ensure(db)
        add("meetings", "Meeting requests to answer", q1("SELECT COUNT(*) n FROM meeting_requests WHERE status IN ('open','proposed')"),
            "Slots are ready: book or draft a reply", "chrono", 95, "page", "/#today")
    except Exception:
        pass
    add("replied", "Leads waiting for you", q1("SELECT COUNT(*) n FROM leads WHERE status='replied'"),
        "They answered: propose a call while they're warm", "hunter", 90, "page", "/#pipeline")
    add("seq", "Outreach steps due", q1("SELECT COUNT(*) n FROM tasks WHERE source='sequence' AND status='open' AND due <= ?", today),
        "Emails, DMs and visits from running sequences", "hunter", 80, "page", "/#schedule")
    add("overdue", "Overdue to-dos", q1("SELECT COUNT(*) n FROM tasks WHERE status='open' AND due < ?", today),
        "Re-plan or drop them in the week plan", "chrono", 70, "page", "/#schedule")
    try:
        from .triage import followups_due, needs_reply

        add("inbox", "Replies owed", len(needs_reply(cfg, db, None)), "Setz can draft them in your voice", "hermes", 75, "job", "drafts")
        add("followups", "Follow-ups due", len(followups_due(cfg, db)), "Quiet threads worth a nudge", "hermes", 60, "job", "drafts")
    except Exception:
        pass
    try:
        from .ops import partners

        risk = [p for p in partners(cfg, db) if p["health"]["label"] == "at risk"]
        add("partners", "Partners at risk", len(risk), ", ".join(p["name"] for p in risk[:3]), "atlas", 72, "page", "/#partners")
    except Exception:
        pass
    add("contacts", "Leads without contact details", q1(
        "SELECT COUNT(*) n FROM leads WHERE website != '' AND (email IS NULL OR email = '') AND status NOT IN ('partner','lost') "
        "AND (enriched_at IS NULL OR enriched_at < datetime('now','-30 day'))"),
        "Read their website + Impressum for email, phone, owner", "hunter", 50, "job", "enrich")
    try:
        from .pipeline import capacity, started_this_week, targets

        gap = next((t for t in targets(cfg, db) if t["stage"] == "contacted"), None)
        room = capacity(cfg, db) - started_this_week(db)
        if gap and not gap["on_track"] and room > 0:
            add("pipeline", "Pipeline behind target", room, f"{gap['actual_week']} of {gap['target_week']:g} first contacts this week: "
                f"start up to {room} sequences", "hunter", 65, "job", "pipeline")
    except Exception:
        pass
    try:
        from .leadgen import coverage

        empty = [c["campus"] for c in coverage(cfg, db) if not c["found"]]
        add("campuses", "Campuses not scanned yet", len(empty), ", ".join(empty[:3]), "hunter", 40, "job", "leadgen")
    except Exception:
        pass
    try:
        from .brain import lead_model, stats

        st = stats(db)
        stale = (st["updated_at"] or "") < (datetime.now() - timedelta(days=3)).isoformat()
        if st["memories"] == 0 or stale:
            add("brain", "Brain needs a refresh", 1, "Learn from partners, leads and meetings", "setz", 30, "job", "brain")
        elif not lead_model(db):
            n = q1("SELECT COUNT(DISTINCT lead_id) n FROM lead_events WHERE to_status='contacted'")
            add("model", "Lead model still learning", max(1, 20 - n), f"{n} contacted leads so far; it trains at ~20 with outcomes",
                "setz", 20, "page", "/command")
    except Exception:
        pass
    return sorted(out, key=lambda m: -m["priority"])


def overview(cfg: Config, db: DB, running: dict[str, str] | None = None) -> dict[str, Any]:
    running_pb = json.loads(db.kv_get("playbook:running", "{}") or "{}")
    return {"missions": missions(cfg, db), "jobs": jobs_status(db, running),
            "playbooks": [{"name": k, **v} for k, v in PLAYBOOKS.items()], "playbook": running_pb or None}


def run_playbook(cfg: Config, name: str, progress: Callable[[dict], None] | None = None) -> dict[str, Any]:
    """Run the steps in order. A failing step is reported and the playbook continues with the next one."""
    from .db import utcnow
    from .jobs import run_job

    if name not in PLAYBOOKS:
        raise ValueError(f"unknown playbook {name}; choose from {', '.join(PLAYBOOKS)}")
    steps = PLAYBOOKS[name]["steps"]
    results: dict[str, Any] = {}
    state = {"name": name, "label": PLAYBOOKS[name]["label"], "steps": steps, "done": [], "current": None, "started": utcnow()}

    def save(s: dict | None) -> None:
        with DB(cfg.db_path) as db:
            db.kv_set("playbook:running", json.dumps(s or {}))

    for step in steps:
        state["current"] = step
        save(state)
        if progress:
            progress({"agent": JOB_AGENT.get(step, "setz"), "status": "working", "summary": f"{state['label']}: {step}…"})
        try:
            results[step] = {"ok": True, "result": run_job(cfg, step)}
        except Exception as exc:
            results[step] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:300]}
        state["done"].append(step)
        if progress:
            ok = results[step]["ok"]
            progress({"agent": JOB_AGENT.get(step, "setz"), "status": "ok" if ok else "alert",
                      "summary": f"{step} {'done' if ok else 'failed: ' + results[step]['error'][:120]}"})
    save(None)
    failed = [s for s, r in results.items() if not r["ok"]]
    try:
        from .notify import notify

        with DB(cfg.db_path) as db:
            notify(cfg, db, f"{PLAYBOOKS[name]['label']} finished", f"{len(steps) - len(failed)}/{len(steps)} steps ok"
                   + (f" · failed: {', '.join(failed)}" if failed else ""), kind="alert" if failed else "success",
                   url="/command", key=f"playbook:{name}:{utcnow()}")
    except Exception:
        pass
    return {"playbook": name, "results": results, "failed": failed}
