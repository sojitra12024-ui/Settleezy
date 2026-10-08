"""Learn how the founder writes and what works in outreach.

Step 1 (local, no AI): statistics from every sent email: length, greetings, sign-offs, du/Sie,
response times, which outreach emails got replies and when.
Step 2 (Claude, redacted samples): turn the stats + representative emails into a style guide
(`data/voice_profile.md`) and an outreach playbook (`data/email_playbook.md`).
"""

from __future__ import annotations

import json
import random
import re
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import Config
from .db import DB
from .llm import LLM
from .knowledge import ONE_LINER
from .mailtext import greeting, sign_off

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _is_me(addr: str, me: set[str]) -> bool:
    return addr.lower() in me


def compute_stats(db: DB, me: set[str]) -> dict[str, Any]:
    sent = db.q("SELECT * FROM messages WHERE folder='sent' AND body_text != '' ORDER BY sent_at")
    stats: dict[str, Any] = {"sent_count": len(sent)}
    by_lang: dict[str, list] = defaultdict(list)
    for m in sent:
        by_lang[m["language"]].append(m)

    style = {}
    for lang, msgs in by_lang.items():
        texts = [m["body_text"] for m in msgs]
        words = [len(t.split()) for t in texts]
        style[lang] = {
            "emails": len(msgs),
            "median_words": int(statistics.median(words)) if words else 0,
            "p90_words": int(sorted(words)[int(len(words) * 0.9)]) if words else 0,
            "top_greetings": Counter(g for t in texts if (g := greeting(t))).most_common(6),
            "top_sign_offs": Counter(s for t in texts if (s := sign_off(t))).most_common(6),
            "exclamation_rate": round(sum("!" in t for t in texts) / max(len(texts), 1), 2),
            "emoji_rate": round(sum(bool(re.search(r"[\U0001F300-\U0001FAFF]", t)) for t in texts) / max(len(texts), 1), 2),
            "question_rate": round(sum("?" in t for t in texts) / max(len(texts), 1), 2),
            "bullet_rate": round(sum(bool(re.search(r"^\s*[-•*]\s", t, re.M)) for t in texts) / max(len(texts), 1), 2),
        }
        if lang == "de":
            du = sum(bool(re.search(r"\b(du|dir|dich|dein\w*)\b", t, re.I)) for t in texts)
            sie = sum(bool(re.search(r"\b(Sie|Ihnen|Ihr\w*)\b", t)) for t in texts)
            style[lang]["du_vs_sie"] = {"du": du, "Sie": sie}
    stats["style"] = style

    # Response times: inbound message -> my next message in the same conversation.
    convs: dict[str, list] = defaultdict(list)
    for m in db.q("SELECT conversation_id, folder, from_addr, sent_at, automated, subject, to_addrs FROM messages ORDER BY sent_at"):
        convs[m["conversation_id"]].append(m)
    response_hours = []
    for msgs in convs.values():
        for a, b in zip(msgs, msgs[1:]):
            if a["folder"] == "inbox" and not a["automated"] and b["folder"] == "sent":
                response_hours.append((_dt(b["sent_at"]) - _dt(a["sent_at"])).total_seconds() / 3600)
    if response_hours:
        response_hours.sort()
        stats["response_time_hours"] = {
            "median": round(statistics.median(response_hours), 1),
            "p75": round(response_hours[int(len(response_hours) * 0.75)], 1),
            "same_day_share": round(sum(h < 24 for h in response_hours) / len(response_hours), 2),
        }

    # Outreach: conversations I started. Did they get a reply? After how many follow-ups? When sent?
    outreach = []
    for cid, msgs in convs.items():
        if not msgs or msgs[0]["folder"] != "sent":
            continue
        first = msgs[0]
        my_msgs = [m for m in msgs if m["folder"] == "sent"]
        replies = [m for m in msgs if m["folder"] == "inbox" and not m["automated"] and not _is_me(m["from_addr"], me)]
        followups_before_reply = 0
        if replies:
            followups_before_reply = sum(1 for m in my_msgs[1:] if m["sent_at"] < replies[0]["sent_at"])
        sent_dt = _dt(first["sent_at"])
        outreach.append(
            {
                "conversation_id": cid,
                "subject": first["subject"],
                "weekday": WEEKDAYS[sent_dt.weekday()],
                "hour": sent_dt.astimezone().hour,
                "replied": bool(replies),
                "followups_sent": len(my_msgs) - 1,
                "followups_before_reply": followups_before_reply,
                "days_to_reply": round((_dt(replies[0]["sent_at"]) - sent_dt).total_seconds() / 86400, 1) if replies else None,
                "recipient_domain": (json.loads(first["to_addrs"] or "[]") or ["?"])[0].split("@")[-1],
            }
        )
    stats["outreach"] = summarise_outreach(outreach)
    return stats


def summarise_outreach(rows: list[dict]) -> dict[str, Any]:
    if not rows:
        return {"threads": 0}
    replied = [r for r in rows if r["replied"]]

    def rate(group: list[dict]) -> float:
        return round(sum(r["replied"] for r in group) / len(group), 2) if group else 0.0

    by_day = {d: rate([r for r in rows if r["weekday"] == d]) for d in WEEKDAYS if any(r["weekday"] == d for r in rows)}
    buckets = {"08-11": range(8, 12), "12-14": range(12, 15), "15-18": range(15, 19), "19-23": range(19, 24)}
    by_hour = {k: rate([r for r in rows if r["hour"] in v]) for k, v in buckets.items() if any(r["hour"] in v for r in rows)}
    days = sorted(r["days_to_reply"] for r in replied if r["days_to_reply"] is not None)
    return {
        "threads": len(rows),
        "reply_rate": rate(rows),
        "reply_rate_by_weekday": by_day,
        "reply_rate_by_send_hour": by_hour,
        "median_days_to_reply": days[len(days) // 2] if days else None,
        "replies_after_n_followups": dict(Counter(r["followups_before_reply"] for r in replied)),
        "never_followed_up_no_reply": sum(1 for r in rows if not r["replied"] and r["followups_sent"] == 0),
        "replied_subjects_sample": [r["subject"] for r in replied[-15:]],
        "unreplied_subjects_sample": [r["subject"] for r in rows if not r["replied"]][-15:],
    }


def _samples(db: DB, lang: str, n: int) -> list[str]:
    rows = db.q(
        "SELECT subject, body_text FROM messages WHERE folder='sent' AND language=? AND length(body_text) BETWEEN 80 AND 3000 "
        "ORDER BY sent_at DESC LIMIT 400",
        (lang,),
    )
    rows = list(rows)
    random.Random(7).shuffle(rows)
    return [f"Subject: {r['subject']}\n{r['body_text']}" for r in rows[:n]]


PROFILE_SYSTEM = (
    "You are an expert writing coach. You extract a precise, reusable style guide from a founder's real emails "
    "so an assistant can draft new emails that are indistinguishable from the founder's own."
)


def build_profile(cfg: Config, db: DB, llm: LLM) -> tuple[Path, Path]:
    stats = compute_stats(db, cfg.my_addresses)
    (cfg.data_dir / "email_stats.json").write_text(json.dumps(stats, indent=2, ensure_ascii=False), encoding="utf-8")
    samples = {lang: _samples(db, lang, 25) for lang in ("en", "de")}
    sample_text = "\n\n".join(
        f"=== {lang.upper()} EMAIL {i + 1} ===\n{s}" for lang, items in samples.items() for i, s in enumerate(items)
    )
    from .importer import library

    imported = library(cfg, "outreach", 8000)
    if imported:
        sample_text += "\n\n=== OUTREACH WRITTEN EARLIER (imported documents) ===\n" + imported
    me = cfg.me
    profile = llm.cloud(
        f"""Founder: {me.get('name', 'the founder')}, {me.get('role', 'founder')} of Settleezy.
{ONE_LINER}
Writes in English and German.

Measured statistics from all sent emails:
{json.dumps(stats['style'], ensure_ascii=False, indent=1)}
Response time: {json.dumps(stats.get('response_time_hours'), ensure_ascii=False)}

Representative sent emails (personal data redacted):
{sample_text}

Write a style guide in Markdown with these sections:
1. Voice in one paragraph (tone, energy, formality).
2. English rules: greetings, sign-offs, typical length, sentence style, phrases they actually use, phrases they never use.
3. German rules: same, plus du/Sie rules (when they use which).
4. Structure patterns by email type (reply to partner, reply to student, cold outreach, follow-up, scheduling).
5. 6 short verbatim-style example snippets that capture the voice.
6. "Never do" list (things that would sound unlike them).
Be concrete and grounded only in the evidence above.""",
        PROFILE_SYSTEM,
        effort="high",
    )
    playbook = llm.cloud(
        f"""Outreach statistics from the founder's mailbox (conversations they started):
{json.dumps(stats['outreach'], ensure_ascii=False, indent=1)}

Context: {ONE_LINER}
Partners give members discounts: Berlin restaurants, cafés, grocery stores, activities and event organisers, plus brands
(like those on UNiDAYS / Student Beans). Universities partner through the Buddy platform (A-to-Z support for new incoming
students, including accommodation support); student organisations help reach students.

Write an outreach playbook in Markdown:
1. What the data says works (subject styles, best days/times, follow-up cadence) with the numbers.
2. Recommended cadence: when to follow up (days), how many times, when to stop. Base it on the data; say when data is thin.
3. Templates by partner type (venue: restaurant/café/grocery/activity/event, brand, university Buddy platform, housing/service) in both EN and DE, written in the founder's
   voice, short, with one clear ask, and a polite opt-out line (German UWG-friendly: relevant, personal, not mass mail).
4. Follow-up templates (#1, #2, breakup).
5. Three experiments to improve reply rate, each with a metric.""",
        "You are a B2B partnerships lead who has grown student platforms. Be data-driven and specific.",
        effort="high",
    )
    p1 = cfg.data_dir / "voice_profile.md"
    p2 = cfg.data_dir / "email_playbook.md"
    p1.write_text(profile, encoding="utf-8")
    p2.write_text(playbook, encoding="utf-8")
    return p1, p2


def load_profile(cfg: Config) -> str:
    p = cfg.data_dir / "voice_profile.md"
    return p.read_text(encoding="utf-8") if p.exists() else ""


def load_playbook(cfg: Config) -> str:
    p = cfg.data_dir / "email_playbook.md"
    return p.read_text(encoding="utf-8") if p.exists() else ""
