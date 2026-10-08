"""Decide what needs the founder: replies owed and follow-ups due."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .config import Config
from .db import DB, utcnow
from .llm import LLM, LLMError


@dataclass
class Item:
    kind: str                  # reply | followup
    conversation_id: str
    message_id: str            # message to reply to (reply) / my last message (followup)
    counterpart: str
    counterpart_name: str
    subject: str
    age_days: float
    priority: int = 2
    summary: str = ""
    followup_number: int = 0
    language: str = "en"

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def _age_days(iso: str) -> float:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if dt.tzinfo is None:   # imported/legacy rows without a timezone: treat as UTC instead of crashing
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).total_seconds() / 86400


def _latest_per_conversation(db: DB, since_days: int):
    since = (datetime.now(timezone.utc) - timedelta(days=since_days)).isoformat()
    return db.q(
        """SELECT m.* FROM messages m
           JOIN (SELECT conversation_id, MAX(sent_at) AS mx FROM messages GROUP BY conversation_id) t
             ON t.conversation_id = m.conversation_id AND t.mx = m.sent_at
           WHERE m.sent_at >= ?
           ORDER BY m.sent_at DESC""",
        (since,),
    )


TRIAGE_SYSTEM = (
    "You triage a startup founder's inbox. Decide if an email needs a personal reply from the founder. "
    "Newsletters, receipts, notifications, cold sales pitches to the founder and FYIs do not. "
    "Partners, students, universities, investors, journalists, collaborators and anyone asking a question do."
)


def classify(cfg: Config, db: DB, llm: LLM | None, msg) -> tuple[bool, int, str, str]:
    """Local model classification, cached. Falls back to heuristics if Ollama isn't running."""
    cached = db.one("SELECT * FROM triage WHERE message_id=?", (msg["id"],))
    if cached:
        return bool(cached["needs_reply"]), cached["priority"], cached["category"] or "", cached["summary"] or ""
    needs, prio, cat, summary = _heuristic(msg)
    classified = False
    if llm is not None:
        try:
            out = llm.local_json(
                f"""From: {msg['from_name']} <{msg['from_addr']}>
Subject: {msg['subject']}
Body:
{msg['body_text'][:3000]}

Return JSON: {{"needs_reply": true/false, "priority": 1-3 (3 = partner/money/time-sensitive),
"category": one of [partner, student, university, investor, press, supplier, internal, admin, other],
"summary": "max 15 words, what they want"}}""",
                TRIAGE_SYSTEM,
            )
            needs = bool(out.get("needs_reply", needs))
            prio = int(out.get("priority", prio))
            cat = str(out.get("category", cat))
            summary = str(out.get("summary", summary))[:200]
            classified = True
        except (LLMError, ValueError, json.JSONDecodeError):
            pass
    if not classified:  # don't cache heuristic guesses; let the model classify on a later run
        return needs, prio, cat, summary
    db.x(
        "INSERT OR REPLACE INTO triage(message_id,needs_reply,priority,category,summary,updated_at) VALUES(?,?,?,?,?,?)",
        (msg["id"], int(needs), prio, cat, summary, utcnow()),
    )
    return needs, prio, cat, summary


def _heuristic(msg) -> tuple[bool, int, str, str]:
    body = msg["body_text"] or ""
    asks = "?" in body or re.search(r"\b(could you|can you|please|let me know|könnten sie|kannst du|bitte|rückmeldung)\b", body, re.I)
    urgent = re.search(r"\b(urgent|asap|today|deadline|dringend|heute|frist)\b", body + " " + (msg["subject"] or ""), re.I)
    return bool(asks), 3 if urgent else 2, "other", (msg["subject"] or "")[:80]


def needs_reply(cfg: Config, db: DB, llm: LLM | None = None, since_days: int = 21) -> list[Item]:
    me = cfg.my_addresses
    ignore = [d.lower() for d in cfg.get("triage.ignore_domains", [])]
    items = []
    for m in _latest_per_conversation(db, since_days):
        if m["folder"] != "inbox" or m["automated"] or m["from_addr"] in me:
            continue
        if any(m["from_addr"].endswith(d) for d in ignore):
            continue
        if db.one("SELECT 1 FROM drafts WHERE source_message_id=?", (m["id"],)):
            continue
        need, prio, cat, summary = classify(cfg, db, llm, m)
        if not need:
            continue
        items.append(
            Item("reply", m["conversation_id"], m["id"], m["from_addr"], m["from_name"], m["subject"],
                 round(_age_days(m["sent_at"]), 1), prio, f"[{cat}] {summary}" if cat and cat != "other" else summary,
                 language=m["language"] or "en")
        )
    return sorted(items, key=lambda i: (-i.priority, -i.age_days))


def followups_due(cfg: Config, db: DB, since_days: int = 60) -> list[Item]:
    """My message is the last one in the thread and the wait exceeded the cadence."""
    cadence = cfg.get("followup.cadence_days", [4, 7, 14])
    me = cfg.my_addresses
    items = []
    for m in _latest_per_conversation(db, since_days):
        if m["folder"] != "sent":
            continue
        thread = db.conversation(m["conversation_id"])
        to = json.loads(m["to_addrs"] or "[]")
        external = [a for a in to if a not in me]
        if not external:
            continue
        # consecutive messages from me at the end of the thread = original + follow-ups already sent
        tail = 0
        for t in reversed(thread):
            if t["folder"] == "sent":
                tail += 1
            else:
                break
        n_followups_sent = tail - 1
        if n_followups_sent >= len(cadence):
            continue
        age = _age_days(m["sent_at"])
        if age < cadence[n_followups_sent]:
            continue
        if not re.search(r"\?|let me know|would you|interested|lass mich wissen|hätten sie|interesse|freue mich", m["body_text"] or "", re.I):
            continue
        if db.one("SELECT 1 FROM drafts WHERE source_message_id=? AND kind='followup'", (m["id"],)):
            continue
        items.append(
            Item("followup", m["conversation_id"], m["id"], external[0], "", m["subject"], round(age, 1),
                 2, f"No reply for {age:.0f} days", n_followups_sent + 1, m["language"] or "en")
        )
    return sorted(items, key=lambda i: -i.age_days)
