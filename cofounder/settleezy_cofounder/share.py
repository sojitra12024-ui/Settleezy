"""Things shared to Setz from the phone's share sheet: what they look like and what Setz can do with them.

  Instagram profile link  -> add the venue/brand/creator as a lead (business discovery fills followers, bio)
  website link            -> add as a lead and read its Impressum
  contract file           -> Lex reviews it (and keeps it in Contracts)
  email / DM text         -> look for a meeting request and suggest slots; or save as a memory / to-do
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

from .config import Config
from .db import DB

IG = re.compile(r"instagram\.com/([A-Za-z0-9_.]{2,30})/?(?:\?|$|#)")


def _url(item: dict) -> str:
    if item.get("url"):
        return item["url"]
    m = re.search(r"https?://\S+", item.get("text", ""))
    return m.group(0).rstrip(").,") if m else ""


def suggest(item: dict) -> list[dict]:
    out = []
    url = _url(item)
    m = IG.search(url)
    if m and m.group(1).lower() not in {"p", "reel", "reels", "stories", "explore"}:
        out.append({"action": "instagram", "label": f"Add @{m.group(1)} as a lead"})
    elif url and urlparse(url).hostname:
        out.append({"action": "website", "label": f"Add {urlparse(url).hostname.removeprefix('www.')} as a lead + read Impressum"})
    f = item.get("file", "")
    if f.lower().endswith((".pdf", ".docx", ".txt", ".md")):
        out.append({"action": "contract", "label": "Review as a contract (Lex)"})
    text = item.get("text", "")
    if len(text) > 30:
        from .scheduling import looks_like_request

        if looks_like_request(item.get("title", ""), text):
            out.append({"action": "meeting", "label": "Meeting request: suggest slots"})
        out.append({"action": "task", "label": "Make it a to-do"})
        out.append({"action": "remember", "label": "Remember this"})
    out.append({"action": "discard", "label": "Discard"})
    return out


def act(cfg: Config, db: DB, item: dict, action: str) -> dict:
    url = _url(item)
    if action == "instagram":
        from .igdiscovery import add_handles

        m = IG.search(url)
        if not m:
            raise ValueError("no Instagram profile in this link")
        return {"done": f"added @{m.group(1)}", "result": add_handles(cfg, db, [m.group(1)])}
    if action == "website":
        from .leads import upsert

        host = (urlparse(url).hostname or "").removeprefix("www.")
        if not host:
            raise ValueError("no link to add")
        name = item.get("title") or host.split(".")[0].replace("-", " ").title()
        lead_id, created = upsert(db, name[:120], "merchant", source="manual:share", website=url, city="Berlin")
        try:
            from .leadintel import enrich_lead
            from .scraping.fetcher import Fetcher

            enrich_lead(db, Fetcher(db, min_delay=1), lead_id)
        except Exception:
            pass
        return {"done": f"{'added' if created else 'updated'} lead {name}", "lead_id": lead_id}
    if action == "contract":
        from .contracts import review_file

        path = cfg.data_dir / "inbox" / Path(item.get("file", "")).name
        if not item.get("file") or not path.is_file():
            raise ValueError("the shared file is gone")
        r = review_file(cfg, db, path.name.split("-", 1)[-1], path.read_bytes())
        return {"done": f"Lex reviewed it: score {r['score']}/100 · {r['verdict']}", "contract_id": r["id"]}
    if action == "meeting":
        from .scheduling import proposed_times, suggest_slots

        from datetime import datetime

        from .scheduling import fmt_slot, is_free

        mins = int(cfg.get("scheduling.meeting_minutes", 30))
        props = [p for p in proposed_times(item.get("text", ""), datetime.now()) if p > datetime.now()]
        free = [p.isoformat(timespec="minutes") for p in props if is_free(cfg, db, p, mins)]
        slots = suggest_slots(cfg, db, n=3)
        return {"done": ("You're free at " + ", ".join(f[:16].replace("T", " ") for f in free)) if free
                else "Suggested slots: " + "; ".join(fmt_slot(s) for s in slots), "proposed_free": free, "slots": slots}
    if action == "task":
        from .ops import add_task, parse_due

        text = (item.get("title") or item.get("text", ""))[:200]
        due, title = parse_due(text)
        tid = add_task(db, title or text, due=due, source="share", notes=item.get("text", "")[:2000])
        return {"done": f"to-do added: {title or text}", "task_id": tid}
    if action == "remember":
        from .brain import remember

        mid = remember(cfg, db, (item.get("title", "") + " " + item.get("text", "")).strip()[:1500], kind="fact", source="share")
        return {"done": "remembered", "memory_id": mid}
    if action == "discard":
        if item.get("file"):
            (cfg.data_dir / "inbox" / Path(item["file"]).name).unlink(missing_ok=True)
        return {"done": "discarded"}
    raise ValueError(f"unknown action {action}")
