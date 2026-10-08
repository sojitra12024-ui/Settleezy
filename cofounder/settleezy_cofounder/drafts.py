"""Write replies, follow-ups and outreach in the founder's voice and save them as Outlook drafts.

Nothing is ever sent: drafts land in Outlook > Drafts with the category "Settleezy AI".
"""

from __future__ import annotations

import json

from .config import Config
from .db import DB, utcnow
from .knowledge import facts
from .llm import LLM
from .msgraph import Graph
from .triage import Item, followups_due, needs_reply
from .voice_profile import load_playbook, load_profile


def _system(cfg: Config) -> str:
    me = cfg.me
    return f"""You draft emails as {me.get('name', 'the founder')}, {me.get('role', 'founder')} of Settleezy.
Write exactly as they write. Follow this style guide strictly:

{load_profile(cfg) or '(No style guide yet: write short, warm, direct and professional.)'}

Rules:
- Reply in the language of the email you answer (German or English); match du/Sie to the thread.
- Only state facts that are in the thread or in the Settleezy facts. Never invent prices, dates, partners or promises.
- If a decision or information only the founder has is needed, write [[CHECK: ...]] at that spot.
- Output only the email body (no subject line, no quoted thread), ending with their usual sign-off and name.

Settleezy facts:
{facts()}"""


def _thread_text(db: DB, conversation_id: str, last_n: int = 4) -> str:
    rows = db.conversation(conversation_id)[-last_n:]
    return "\n\n".join(
        f"--- {'ME' if r['folder'] == 'sent' else (r['from_name'] or r['from_addr'])} ({r['sent_at'][:10]}) ---\n{r['body_text'][:2500]}"
        for r in rows
    )


def draft_for_item(cfg: Config, db: DB, llm: LLM, graph: Graph, item: Item) -> str:
    thread = _thread_text(db, item.conversation_id)
    if item.kind == "reply":
        task = f"Write the reply to the latest message in this thread.\n\nThread (oldest first):\n{thread}"
    else:
        playbook = load_playbook(cfg)
        task = (
            f"Write follow-up #{item.followup_number} to my last email; they have not replied for {item.age_days:.0f} days. "
            f"Keep it shorter than the original, add one new reason to reply, and make it easy to say yes or no."
            + (f"\n\nOutreach playbook:\n{playbook[:4000]}" if playbook else "")
            + f"\n\nThread (oldest first):\n{thread}"
        )
    body = llm.cloud(task, _system(cfg), effort="medium", max_tokens=4000)
    draft = graph.create_reply_draft(item.message_id, body)
    db.x(
        "INSERT INTO drafts(kind,conversation_id,source_message_id,graph_draft_id,subject,created_at) VALUES(?,?,?,?,?,?)",
        (item.kind, draft.get("conversationId") or item.conversation_id, item.message_id, draft.get("id", ""), item.subject, utcnow()),
    )
    return body


def run(cfg: Config, db: DB, llm: LLM | None = None, graph: Graph | None = None, *, limit: int | None = None,
        min_priority: int = 2) -> list[dict]:
    llm = llm or LLM(cfg)
    graph = graph or Graph(cfg)
    limit = limit or int(cfg.get("drafts.max_per_run", 15))
    queue = [i for i in needs_reply(cfg, db, llm) if i.priority >= min_priority] + followups_due(cfg, db)
    done = []
    for item in queue[:limit]:
        try:
            draft_for_item(cfg, db, llm, graph, item)
            done.append({**item.as_dict(), "status": "drafted"})
        except Exception as exc:  # keep going; report per item
            done.append({**item.as_dict(), "status": f"error: {exc}"})
    return done


def outreach_draft(cfg: Config, db: DB, lead_id: int, llm: LLM | None = None, graph: Graph | None = None) -> dict:
    """First-touch partnership email to a lead, saved as a draft."""
    llm = llm or LLM(cfg)
    graph = graph or Graph(cfg)
    lead = db.one("SELECT * FROM leads WHERE id=?", (lead_id,))
    if not lead or not lead["email"]:
        raise ValueError("Lead has no email address; add one or contact them via Instagram/phone.")
    playbook = load_playbook(cfg)
    from .importer import library

    past = library(cfg, "outreach", 5000)
    lang = "German" if (lead["website"] or "").endswith(".de") or lead["kind"] in {"merchant", "university"} else "English"
    prompt = f"""Write a first partnership email to this lead. Language: {lang} (use Sie unless the style guide says otherwise for this type).
Lead: {json.dumps(dict(lead), ensure_ascii=False, default=str)}
Why them: they appear on {lead['sources']} which means they already run discounts/offers for Berlin audiences.
Goal by type: merchant (restaurant, café, grocery store, activity, event) -> become a Settleezy partner by offering
members a discount, in return for a steady stream of local student customers; brand -> a member discount for students in Berlin;
university -> a Buddy platform partnership: A-to-Z support (including accommodation support) for their new incoming
students; student organisation -> tell their members about the Settleezy app, free trial and workshops;
housing/service -> accommodation or service support for the same students, or co-marketing.
Only promise what the Settleezy facts say (e.g. don't claim the listing is free unless the facts say so).
One clear ask (a 15-minute call or a yes/no). Under 120 words. Include a one-line polite opt-out.
First line of your output must be "SUBJECT: ..." then a blank line, then the body.
{('Outreach playbook:' + chr(10) + playbook[:4000]) if playbook else ''}
{('Outreach you wrote before (reuse what fits, keep the voice):' + chr(10) + past) if past else ''}"""
    out = llm.cloud(prompt, _system(cfg), effort="medium", max_tokens=3000)
    subject, _, body = out.partition("\n")
    subject = subject.replace("SUBJECT:", "").strip() or "Settleezy x " + lead["name"]
    draft = graph.create_draft([lead["email"]], subject, body.strip())
    db.x(
        "INSERT INTO drafts(kind,lead_id,conversation_id,graph_draft_id,subject,created_at) VALUES('outreach',?,?,?,?,?)",
        (lead_id, draft.get("conversationId", ""), draft.get("id", ""), subject, utcnow()),
    )
    db.x("UPDATE leads SET status='drafted', updated_at=? WHERE id=? AND status='new'", (utcnow(), lead_id))
    return {"lead": lead["name"], "subject": subject}
