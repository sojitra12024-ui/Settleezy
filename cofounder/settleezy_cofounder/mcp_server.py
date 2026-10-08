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
    "settleezy-cofounder",
    instructions="Tools for Settleezy's founder: daily brief, inbox triage, Outlook drafts (never sends), "
    "competitor listings in Berlin, partner leads and growth reviews.",
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


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
