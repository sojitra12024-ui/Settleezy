"""Daily brief: meetings, replies owed, follow-ups, drafts waiting, Instagram, competitors, next actions."""

from __future__ import annotations

import json
from datetime import datetime

from .config import Config
from .db import DB
from .knowledge import ONE_LINER
from .leads import pipeline, top
from .llm import LLM, LLMError
from .scraping.monitor import new_listings
from .triage import followups_due, needs_reply


def gather(cfg: Config, db: DB, llm: LLM | None = None) -> dict:
    today = datetime.now().date().isoformat()
    meetings = [dict(r) for r in db.q("SELECT * FROM events WHERE substr(start,1,10)=? ORDER BY start", (today,))]
    replies = [i.as_dict() for i in needs_reply(cfg, db, llm)]
    followups = [i.as_dict() for i in followups_due(cfg, db)]
    drafts_today = db.one("SELECT COUNT(*) n FROM drafts WHERE substr(created_at,1,10)=?", (today,))["n"]
    comments = json.loads(db.kv_get("instagram:unanswered_comments", "[]"))
    ig = {r["key"]: r["value"] for r in db.q("SELECT key,value FROM metrics WHERE source='instagram' AND day=(SELECT MAX(day) FROM metrics WHERE source='instagram')")}
    return {
        "date": today,
        "meetings": meetings,
        "replies": replies,
        "followups": followups,
        "drafts_created_today": drafts_today,
        "instagram": ig,
        "instagram_comments": comments,
        "new_listings": new_listings(db, 24),
        "top_new_leads": [l for l in top(db, status="new", limit=8)],
        "pipeline": pipeline(db),
        **_ops_data(cfg, db),
    }


def _ops_data(cfg: Config, db: DB) -> dict:
    from .ops import partner_stats, reach_out, tasks, today_plan

    try:
        plan = today_plan(cfg, db)
        return {"plan": plan["items"], "tasks": tasks(db, "today")[:10], "reach_out": reach_out(cfg, db, 10),
                "partners": partner_stats(cfg, db), "setz": json.loads(db.kv_get("setz:synthesis", "{}"))}
    except Exception:  # the brief must never fail because of one section
        return {"plan": [], "tasks": [], "reach_out": [], "partners": {}}


def next_actions(cfg: Config, data: dict, llm: LLM) -> str:
    goals = "\n".join(f"- {g}" for g in cfg.get("growth.goals", []))
    compact = {
        "meetings": [(m["start"][11:16], m["title"]) for m in data["meetings"]],
        "replies_owed": [(r["counterpart_name"] or r["counterpart"], r["subject"], r["priority"], r["summary"]) for r in data["replies"][:10]],
        "followups_due": [(f["counterpart"], f["subject"], f["age_days"]) for f in data["followups"][:10]],
        "new_competitor_listings": [(l["source"], l["merchant"], l["category"]) for l in data["new_listings"][:15]],
        "top_new_leads": [(l["name"], l["kind"], l["score"], bool(l["email"])) for l in data["top_new_leads"]],
        "pipeline": data["pipeline"],
        "reach_out_queue": [(r["who"], r["action"], r["reason"]) for r in data.get("reach_out", [])[:10]],
        "todos_today": [(t["title"], t["due"], t["priority"]) for t in data.get("tasks", [])],
        "service_partners": data.get("partners", {}),
        "instagram": data["instagram"],
        "unanswered_ig_comments": len(data["instagram_comments"]),
    }
    return llm.cloud(
        f"""Today is {data['date']}. You are the co-founder of Settleezy.
{ONE_LINER}
Company goals:
{goals or '- grow paying members in Berlin'}

Today's state:
{json.dumps(compact, ensure_ascii=False, default=str)}

Give the founder the 5 most important things to do today, in order, each one line with the reason in brackets.
Be concrete (name the person/lead/listing). Include at most one growth/marketing action that compounds.""",
        "You are a sharp, practical startup co-founder. No fluff.",
        effort="low",
        max_tokens=2000,
    )


def render(data: dict, actions: str) -> str:
    L = [f"# Daily brief — {data['date']}", ""]
    if (data.get("setz") or {}).get("briefing"):
        L += ["> **Setz:** " + data["setz"]["briefing"].replace("\n", " "), ""]
    L.append("## Today's plan")
    if data.get("plan"):
        for i in data["plan"]:
            if i["type"] == "meeting":
                L.append(f"- **{i['start']}–{i['end']}** 📅 {i['title']}{' — ' + i['detail'] if i['detail'] else ''}")
            else:
                extra = f" — {', '.join(i['suggestions'][:3])}" if i.get("suggestions") else ""
                L.append(f"- {i['start']}–{i['end']} {i['title']}{extra}{' (clashes with a meeting)' if i.get('clash') else ''}")
    else:
        L += [f"- **{m['start'][11:16]}–{m['end'][11:16]}** {m['title']} ({m['source']}){' — ' + m['location'] if m['location'] else ''}" for m in data["meetings"]] or ["- No meetings."]
    if data.get("tasks"):
        L += ["", f"## To-dos ({len(data['tasks'])})"]
        L += [f"- [ ] {'❗ ' if t['priority'] >= 3 else ''}{t['title']}{' (overdue)' if t.get('overdue') else ''}" for t in data["tasks"]]
    if data.get("reach_out"):
        L += ["", "## Who to reach out to"]
        L += [f"- **{r['who']}**: {r['action']} — {r['reason']}" for r in data["reach_out"][:8]]
    ps = data.get("partners") or {}
    if ps.get("total"):
        L += ["", "## Service partners",
              f"- {ps['live']} live · {ps['onboarding']} onboarding · {ps['onboarded_this_month']} onboarded this month"
              + (f" · {ps['at_risk']} at risk" if ps.get("at_risk") else "")]
    L += ["", f"## Replies owed ({len(data['replies'])})"]
    L += [f"- {'🔴' if r['priority'] >= 3 else '🟡'} {r['counterpart_name'] or r['counterpart']}: *{r['subject']}* — {r['summary']} ({r['age_days']}d)" for r in data["replies"][:12]] or ["- Inbox clear."]
    L += ["", f"## Follow-ups due ({len(data['followups'])})"]
    L += [f"- {f['counterpart']}: *{f['subject']}* — follow-up #{f['followup_number']}, {f['age_days']:.0f} days quiet" for f in data["followups"][:12]] or ["- None due."]
    L += ["", f"Drafts written today: **{data['drafts_created_today']}** (Outlook → Drafts, category “Settleezy AI”)."]
    if data["instagram"] or data["instagram_comments"]:
        L += ["", "## Instagram"]
        if data["instagram"]:
            L.append("- " + ", ".join(f"{k}: {v:g}" for k, v in data["instagram"].items()))
        L += [f"- Reply to @{c['user']}: “{(c['text'] or '')[:80]}” ({c['post']})" for c in data["instagram_comments"][:8]]
    L += ["", f"## New competitor listings in Berlin (24h): {len(data['new_listings'])}"]
    L += [f"- [{l['source']}] **{l['merchant'] or l['title']}** {('· ' + l['category']) if l['category'] else ''} — {l['url']}" for l in data["new_listings"][:15]] or ["- Nothing new."]
    L += ["", "## Best new leads"]
    L += [f"- {l['name']} ({l['kind']}, score {l['score']:.0f}){' ✉' if l['email'] else ''} — from {', '.join(json.loads(l['sources'] or '[]'))}" for l in data["top_new_leads"]] or ["- No new leads."]
    L += ["", "## What to do next", actions or "- (Next-action planning unavailable.)"]
    return "\n".join(L) + "\n"


def spoken(data: dict, actions: str) -> str:
    """Short version for text-to-speech."""
    parts = [f"Good morning, it's Setz. You have {len(data['meetings'])} meeting{'s' if len(data['meetings']) != 1 else ''} today"]
    if data["meetings"]:
        first = data["meetings"][0]
        parts[-1] += f", the first at {first['start'][11:16]}: {first['title']}"
    parts.append(f"{len(data['replies'])} emails need a reply and {len(data['followups'])} follow-ups are due")
    if data["drafts_created_today"]:
        parts.append(f"I drafted {data['drafts_created_today']} of them for you in Outlook")
    if data.get("tasks"):
        parts.append(f"{len(data['tasks'])} to-dos are due")
    ps = data.get("partners") or {}
    if ps.get("at_risk"):
        parts.append(f"{ps['at_risk']} partner{'s need' if ps['at_risk'] != 1 else ' needs'} attention")
    if data.get("reach_out"):
        parts.append(f"First person to contact: {data['reach_out'][0]['who']}")
    if data["new_listings"]:
        parts.append(f"Competitors added {len(data['new_listings'])} new Berlin listings")
    if actions:
        first_line = next((ln for ln in actions.splitlines() if ln.strip()), "")
        parts.append("Top priority: " + first_line.lstrip("-*0123456789. ").split("(")[0].strip())
    return ". ".join(parts) + "."


def build(cfg: Config, db: DB, llm: LLM | None = None, with_actions: bool = True) -> tuple[str, str]:
    llm = llm or LLM(cfg)
    data = gather(cfg, db, llm)
    actions = ""
    if with_actions:
        try:
            actions = next_actions(cfg, data, llm)
        except LLMError as exc:
            actions = f"- (Could not plan next actions: {exc})"
    md = render(data, actions)
    out = cfg.data_dir / "briefs"
    out.mkdir(exist_ok=True)
    (out / f"{data['date']}.md").write_text(md, encoding="utf-8")
    return md, spoken(data, actions)
