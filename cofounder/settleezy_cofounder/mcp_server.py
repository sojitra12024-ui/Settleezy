"""MCP server exposing the co-founder toolkit to OpenJarvis (or any MCP client, e.g. Claude Desktop).

OpenJarvis config (~/.openjarvis/config.toml):
    [tools.mcp]
    enabled = true
    servers = "mcp-servers.json"      # see cofounder/openjarvis/mcp-servers.json
"""

from __future__ import annotations

import json

from mcp.server.mcpserver import MCPServer

from . import brief as brief_mod
from . import drafts as drafts_mod
from . import leads as leads_mod
from .config import load_config
from .db import DB
from .llm import LLM
from .scraping.monitor import new_listings
from .triage import followups_due, needs_reply

server = MCPServer(
    "setz",
    instructions="Setz, the chief of staff for Settleezy's founder: daily brief and plan, to-dos, who to contact, "
    "service partners and onboarding, meeting prep, inbox triage, Outlook drafts (never sends), competitor listings "
    "in Berlin, partner leads and growth reviews.",
)


def _db() -> tuple:
    cfg = load_config()
    return cfg, DB(cfg.db_path)


@server.tool()
def daily_brief() -> str:
    """Today's brief: meetings, replies owed, follow-ups due, Instagram, new competitor listings, next actions."""
    cfg, db = _db()
    with db:
        md, _ = brief_mod.build(cfg, db)
    return md


@server.tool()
def inbox_needs_reply(limit: int = 15) -> str:
    """Emails that need a personal reply, highest priority first."""
    cfg, db = _db()
    with db:
        return json.dumps([i.as_dict() for i in needs_reply(cfg, db, LLM(cfg))[:limit]], ensure_ascii=False)


@server.tool()
def followups(limit: int = 15) -> str:
    """Threads where the founder wrote last and a follow-up is now due."""
    cfg, db = _db()
    with db:
        return json.dumps([i.as_dict() for i in followups_due(cfg, db)[:limit]], ensure_ascii=False)


@server.tool()
def create_drafts(limit: int = 10) -> str:
    """Write replies and follow-ups in the founder's voice and save them as Outlook DRAFTS (nothing is sent)."""
    cfg, db = _db()
    with db:
        return json.dumps(drafts_mod.run(cfg, db, limit=limit), ensure_ascii=False)


@server.tool()
def competitor_new_listings(hours: int = 48) -> str:
    """New Berlin listings on Groupon, vspots, Top10, UNiDAYS, Student Beans in the last N hours."""
    _, db = _db()
    with db:
        return json.dumps(new_listings(db, hours), ensure_ascii=False)


@server.tool()
def top_leads(kind: str = "", status: str = "new", limit: int = 15) -> str:
    """Best partner leads. kind: merchant|brand|university|housing|service (empty = all)."""
    _, db = _db()
    with db:
        return json.dumps(leads_mod.top(db, status=status or None, kind=kind or None, limit=limit), ensure_ascii=False, default=str)


@server.tool()
def draft_outreach(lead_id: int) -> str:
    """Write a first partnership email to a lead and save it as an Outlook draft (not sent)."""
    cfg, db = _db()
    with db:
        return json.dumps(drafts_mod.outreach_draft(cfg, db, lead_id), ensure_ascii=False)


@server.tool()
def update_lead(lead_id: int, status: str, note: str = "") -> str:
    """Move a lead through the pipeline: new, drafted, contacted, replied, meeting, partner, lost."""
    _, db = _db()
    with db:
        leads_mod.set_status(db, lead_id, status, note)
    return "ok"


@server.tool()
def todays_meetings() -> str:
    """Meetings today from Outlook and Calendly."""
    _, db = _db()
    from datetime import date

    with db:
        return json.dumps([dict(r) for r in db.q("SELECT * FROM events WHERE substr(start,1,10)=? ORDER BY start", (date.today().isoformat(),))], ensure_ascii=False)


@server.tool()
def connection_status() -> str:
    """Test Outlook, Instagram, Calendly, Claude, Ollama and ElevenLabs end to end; returns what's broken and how to fix it."""
    from .connections import run_all

    cfg, db = _db()
    with db:
        return json.dumps(run_all(cfg, db), ensure_ascii=False)


@server.tool()
def record_kpi(key: str, value: float) -> str:
    """Record a number from the app (e.g. paying_members, trial_members, app_downloads, workshop_signups, buddy_students)."""
    from .tracking import record_kpi as rec

    cfg = load_config()
    if key not in cfg.get("tracking.manual_kpis", []):
        return f"unknown KPI {key}; allowed: {cfg.get('tracking.manual_kpis', [])}"
    with DB(cfg.db_path) as db:
        rec(db, key, value)
    return "ok"


@server.tool()
def todays_plan() -> str:
    """Today's plan: routine blocks laid around meetings, with who/what to do in each block, and what to focus on now."""
    from .ops import focus_now, today_plan

    cfg, db = _db()
    with db:
        plan = today_plan(cfg, db)
        plan["focus_now"] = focus_now(cfg, db)
        return json.dumps(plan, ensure_ascii=False, default=str)


@server.tool()
def who_to_contact(limit: int = 10) -> str:
    """People to reach out to now (replies owed, follow-ups, partners needing attention, warm leads), with reasons."""
    from .ops import reach_out

    cfg, db = _db()
    with db:
        return json.dumps(reach_out(cfg, db, limit), ensure_ascii=False, default=str)


@server.tool()
def add_todo(text: str) -> str:
    """Add a to-do. Dates in the text are understood: 'call Café Kranz tomorrow', 'send contract on Friday'."""
    from .ops import add_task

    _, db = _db()
    with db:
        tid = add_task(db, text, source="voice")
    return f"added #{tid}" if tid else "already on the list"


@server.tool()
def list_todos(scope: str = "today") -> str:
    """To-dos. scope: today | open | done."""
    from .ops import tasks

    _, db = _db()
    with db:
        return json.dumps(tasks(db, scope), ensure_ascii=False, default=str)


@server.tool()
def complete_todo(task_id: int) -> str:
    """Mark a to-do as done."""
    from .ops import set_task

    _, db = _db()
    with db:
        set_task(db, task_id, "done")
    return "ok"


@server.tool()
def service_partners(status: str = "") -> str:
    """Service partners with onboarding stage and health (status: onboarding | live | paused | ended, empty = all), plus stats."""
    from .ops import partner_stats, partners

    cfg, db = _db()
    with db:
        return json.dumps({"stats": partner_stats(cfg, db), "partners": partners(cfg, db, status or None)}, ensure_ascii=False, default=str)


@server.tool()
def update_partner(partner_id: int, stage: str = "", status: str = "", offer: str = "", renewal_date: str = "", notes: str = "") -> str:
    """Update a partner. stage: agreed | contract | offer | listed | promoted | live."""
    from . import ops

    _, db = _db()
    fields = {k: v for k, v in {"stage": stage, "status": status, "offer": offer, "renewal_date": renewal_date, "notes": notes}.items() if v}
    with db:
        return json.dumps(ops.update_partner(db, partner_id, **fields), ensure_ascii=False, default=str)


@server.tool()
def meeting_prep(event_id: str = "") -> str:
    """One-page prep for a meeting (default: the next one): who, what we know, recent emails, suggested plan."""
    from datetime import datetime

    from . import ops
    from .llm import LLM

    cfg, db = _db()
    with db:
        if not event_id:
            nxt = db.one("SELECT id FROM events WHERE start >= ? ORDER BY start LIMIT 1", (datetime.now().strftime("%Y-%m-%dT%H:%M"),))
            if not nxt:
                return "No upcoming meetings."
            event_id = nxt["id"]
        return ops.meeting_prep(cfg, db, event_id, LLM(cfg))


@server.tool()
def weekly_scorecard() -> str:
    """This week vs last week: outreach, replies, meetings, partners gone live, new leads, to-dos done."""
    from .ops import scorecard

    cfg, db = _db()
    with db:
        return json.dumps(scorecard(cfg, db), ensure_ascii=False)


@server.tool()
def run_agents(agents: str = "") -> str:
    """Run Setz's specialist agents (comma-separated subset or all): hermes (inbox), atlas (partners), scout (competitors),
    hunter (leads), nova (Instagram), quant (growth analyst), chrono (schedule), campus (universities/intake), sentinel (systems)."""
    from . import agents as ag

    cfg, db = _db()
    with db:
        only = [a.strip() for a in agents.split(",") if a.strip()] or None
        return json.dumps(ag.run(cfg, db, only), ensure_ascii=False, default=str)


@server.tool()
def agent_reports() -> str:
    """Latest report from every specialist agent plus Setz's synthesis (briefing, priorities)."""
    from . import agents as ag

    cfg, db = _db()
    with db:
        return json.dumps({"reports": ag.latest(db), "setz": json.loads(db.kv_get("setz:synthesis", "{}"))}, ensure_ascii=False, default=str)


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
