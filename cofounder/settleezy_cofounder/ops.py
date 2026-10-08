"""Operations layer: service partners, onboarding, who to contact, to-dos, daily routine, meeting prep, scorecard.

This is what turns Setz from "an inbox helper" into a chief of staff: it knows every partner's onboarding stage
and health, decides who you should talk to next and why, keeps your to-do list, lays your routine around
today's meetings, prepares you for each meeting and keeps a weekly scorecard.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

from .config import Config
from .db import DB, utcnow

# -- partners ---------------------------------------------------------------

STAGES = [
    ("agreed", "Agreed in principle"),
    ("contract", "Agreement signed"),
    ("offer", "Member offer set up"),
    ("listed", "Listed in the app"),
    ("promoted", "Launch promo done"),
    ("live", "Live"),
]
STAGE_KEYS = [s for s, _ in STAGES]
PARTNER_KINDS = {"merchant": "venue", "brand": "brand", "university": "university", "housing": "housing", "service": "service"}
EDITABLE = {"name", "kind", "category", "contact_name", "email", "phone", "instagram", "website", "status", "stage", "offer",
            "signed_at", "live_at", "renewal_date", "last_contact_at", "redemptions", "notes"}


def _today() -> date:
    return datetime.now().date()


def _days_since(iso: str | None) -> float | None:
    if not iso:
        return None
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).total_seconds() / 86400


def add_partner(db: DB, name: str, kind: str = "venue", **fields: Any) -> int:
    now = utcnow()
    data = {k: v for k, v in fields.items() if k in EDITABLE and v not in (None, "")}
    data.update({"name": name.strip(), "kind": kind, "created_at": now, "updated_at": now, "stage_since": now})
    if "lead_id" in fields:
        data["lead_id"] = fields["lead_id"]
    data.setdefault("stage", "agreed")
    cols = ",".join(data)
    cur = db.x(f"INSERT INTO partners({cols}) VALUES({','.join('?' * len(data))})", list(data.values()))
    if "lead_id" not in data:
        link_partners_to_leads(db)   # a partner added by hand must never get cold outreach as a "new lead"
    return cur.lastrowid


def link_partners_to_leads(db: DB) -> int:
    """Attach partners without a lead to the lead with the same name, and mark that lead as a partner."""
    from .leads import set_status

    n = 0
    for p in db.q("SELECT id, name FROM partners WHERE lead_id IS NULL"):
        lead = db.one("SELECT id, status FROM leads WHERE lower(name)=lower(?) AND id NOT IN "
                      "(SELECT lead_id FROM partners WHERE lead_id IS NOT NULL) ORDER BY id LIMIT 1", (p["name"],))
        if not lead:
            continue
        db.x("UPDATE partners SET lead_id=? WHERE id=?", (lead["id"], p["id"]))
        if lead["status"] != "partner":
            set_status(db, lead["id"], "partner", "")   # also stops any outreach sequence
        n += 1
    return n


def ensure_partner_from_lead(db: DB, lead_id: int) -> int:
    row = db.one("SELECT id FROM partners WHERE lead_id=?", (lead_id,))
    if row:
        return row["id"]
    lead = db.one("SELECT * FROM leads WHERE id=?", (lead_id,))
    if not lead:
        raise ValueError(f"no lead {lead_id}")
    return add_partner(db, lead["name"], PARTNER_KINDS.get(lead["kind"], lead["kind"]), lead_id=lead_id, category=lead["category"],
                       email=lead["email"], phone=lead["phone"], instagram=lead["instagram"], website=lead["website"],
                       signed_at=_today().isoformat(), last_contact_at=utcnow())


def update_partner(db: DB, pid: int, **fields: Any) -> dict:
    cur = db.one("SELECT * FROM partners WHERE id=?", (pid,))
    if not cur:
        raise ValueError(f"no partner {pid}")
    data = {k: v for k, v in fields.items() if k in EDITABLE}
    if "stage" in data:
        if data["stage"] not in STAGE_KEYS:
            raise ValueError(f"stage must be one of {STAGE_KEYS}")
        if data["stage"] != cur["stage"]:
            data["stage_since"] = utcnow()
        if data["stage"] == "live":
            data.setdefault("status", "live")
            data.setdefault("live_at", cur["live_at"] or _today().isoformat())
    if data.get("status") and data["status"] not in {"onboarding", "live", "paused", "ended"}:
        raise ValueError("status must be onboarding, live, paused or ended")
    if not data:
        return dict(cur)
    data["updated_at"] = utcnow()
    db.x(f"UPDATE partners SET {', '.join(f'{k}=?' for k in data)} WHERE id=?", [*data.values(), pid])
    return dict(db.one("SELECT * FROM partners WHERE id=?", (pid,)))


def advance(db: DB, pid: int) -> dict:
    p = db.one("SELECT stage FROM partners WHERE id=?", (pid,))
    if not p:
        raise ValueError(f"no partner {pid}")
    i = STAGE_KEYS.index(p["stage"]) if p["stage"] in STAGE_KEYS else 0
    return update_partner(db, pid, stage=STAGE_KEYS[min(i + 1, len(STAGE_KEYS) - 1)])


def health(cfg: Config, p: dict) -> dict:
    """0-100 with the reasons, so the dashboard can say *why* a partner needs attention."""
    score, reasons = 100, []
    stuck_after = int(cfg.get("partners.stage_stuck_days", 7))
    checkin_after = int(cfg.get("partners.checkin_days", 30))
    if p["status"] == "onboarding":
        d = _days_since(p["stage_since"]) or 0
        if d > stuck_after:
            score -= min(50, int(5 * (d - stuck_after)) + 20)
            reasons.append(f"stuck at '{dict(STAGES).get(p['stage'], p['stage'])}' for {d:.0f} days")
    if p["status"] == "live":
        d = _days_since(p["last_contact_at"])
        if d is None or d > checkin_after:
            score -= 30
            reasons.append("no contact for " + (f"{d:.0f} days" if d is not None else "a long time"))
        live_days = _days_since(p["live_at"]) or 0
        if live_days > 30 and not p["redemptions"]:
            score -= 25
            reasons.append("live 30+ days with no recorded redemptions")
    if p["renewal_date"]:
        days_left = (date.fromisoformat(p["renewal_date"]) - _today()).days
        if days_left <= 30:
            score -= 20 if days_left >= 0 else 35
            reasons.append(f"renewal {'overdue' if days_left < 0 else f'in {days_left} days'}")
    if p["status"] in {"paused", "ended"}:
        score = min(score, 40)
        reasons.append(p["status"])
    score = max(0, score)
    return {"score": score, "label": "healthy" if score >= 75 else "watch" if score >= 50 else "at risk", "reasons": reasons}


def partners(cfg: Config, db: DB, status: str | None = None) -> list[dict]:
    sql, params = "SELECT * FROM partners", []
    if status:
        sql += " WHERE status=?"
        params.append(status)
    rows = [dict(r) for r in db.q(sql + " ORDER BY updated_at DESC", params)]
    for p in rows:
        p["health"] = health(cfg, p)
        p["stage_index"] = STAGE_KEYS.index(p["stage"]) if p["stage"] in STAGE_KEYS else 0
        p["stage_label"] = dict(STAGES).get(p["stage"], p["stage"])
    return rows


def partner_stats(cfg: Config, db: DB) -> dict:
    rows = partners(cfg, db)
    today = _today()
    week_start = today - timedelta(days=today.weekday())
    month_start = today.replace(day=1)
    live = [p for p in rows if p["status"] == "live"]
    onboarded = lambda since: [p for p in live if p["live_at"] and date.fromisoformat(p["live_at"][:10]) >= since]  # noqa: E731
    ttl = [(date.fromisoformat(p["live_at"][:10]) - date.fromisoformat(p["created_at"][:10])).days for p in live if p["live_at"]]
    by_kind: dict[str, int] = {}
    for p in live:
        by_kind[p["kind"] or "other"] = by_kind.get(p["kind"] or "other", 0) + 1
    return {
        "total": len(rows),
        "live": len(live),
        "onboarding": sum(p["status"] == "onboarding" for p in rows),
        "onboarded_this_week": len(onboarded(week_start)),
        "onboarded_this_month": len(onboarded(month_start)),
        "avg_days_to_live": round(sum(ttl) / len(ttl), 1) if ttl else None,
        "at_risk": sum(p["health"]["label"] == "at risk" for p in rows),
        "live_by_kind": by_kind,
        "by_stage": {s: sum(p["stage"] == s and p["status"] == "onboarding" for p in rows) for s in STAGE_KEYS[:-1]},
    }


# -- tasks -------------------------------------------------------------------

_WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6,
             "montag": 0, "dienstag": 1, "mittwoch": 2, "donnerstag": 3, "freitag": 4, "samstag": 5, "sonntag": 6}


def _dotted(m: re.Match, today: date) -> date:
    """German-style 12.10. or 12.10.2026; a past day without a year means next year."""
    d = date(int(m.group(3) or today.year), int(m.group(2)), int(m.group(1)))
    return d.replace(year=d.year + 1) if not m.group(3) and d < today else d


def parse_due(text: str, today: date | None = None) -> tuple[str | None, str]:
    """Pull a due date out of free text ('call Kranz tomorrow', 'on Friday', 'in 3 days', '12.10.'). Returns (due, rest)."""
    today = today or _today()
    t = text
    rules: list[tuple[str, Any]] = [
        (r"\b(today|heute)\b", lambda m: today),
        (r"\b(tomorrow|morgen)\b", lambda m: today + timedelta(days=1)),
        (r"\bin (\d+) (days?|tagen?)\b", lambda m: today + timedelta(days=int(m.group(1)))),
        (r"\b(next week|nächste woche)\b", lambda m: today + timedelta(days=7 - today.weekday())),
        (r"\b(?:on |am )?(" + "|".join(_WEEKDAYS) + r")\b",
         lambda m: today + timedelta(days=(_WEEKDAYS[m.group(1).lower()] - today.weekday()) % 7 or 7)),
        (r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})?", lambda m: _dotted(m, today)),
    ]
    for rx, fn in rules:
        m = re.search(rx, t, re.I)
        if m:
            try:
                due = fn(m)
            except ValueError:
                continue
            rest = (t[: m.start()] + t[m.end():]).strip(" ,.")
            return due.isoformat(), re.sub(r"\s{2,}", " ", rest)
    return None, t.strip()


def add_task(db: DB, title: str, *, due: str | None = None, priority: int = 2, source: str = "manual", notes: str = "",
             lead_id: int | None = None, partner_id: int | None = None, dedupe_key: str | None = None,
             category: str | None = None, est_minutes: int | None = None) -> int | None:
    from .planner import estimate, infer_category

    if due is None and source in {"manual", "voice"}:
        due, title = parse_due(title)
    if re.search(r"\b(urgent|asap|dringend|wichtig)\b", title, re.I):
        priority = 3
    if dedupe_key is None and db.one("SELECT 1 FROM tasks WHERE status='open' AND lower(title)=lower(?) AND due IS ?",
                                     (title.strip(), due)):
        return None   # the same open to-do already exists
    category = category or infer_category(title)
    est_minutes = est_minutes or estimate(title, category)
    cur = db.x(
        "INSERT OR IGNORE INTO tasks(title,notes,due,priority,source,lead_id,partner_id,dedupe_key,created_at,category,est_minutes) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (title.strip(), notes, due, priority, source, lead_id, partner_id, dedupe_key, utcnow(), category, est_minutes),
    )
    return cur.lastrowid if cur.rowcount else None


TASK_EDITABLE = {"title", "notes", "due", "priority", "category", "est_minutes"}


def update_task(db: DB, tid: int, **fields: Any) -> dict:
    from .planner import CATEGORIES

    data = {k: v for k, v in fields.items() if k in TASK_EDITABLE}
    if "category" in data and data["category"] not in CATEGORIES:
        raise ValueError(f"category must be one of {list(CATEGORIES)}")
    if "due" in data and data["due"]:
        date.fromisoformat(data["due"])   # validates YYYY-MM-DD
    if data:
        db.x(f"UPDATE tasks SET {', '.join(f'{k}=?' for k in data)} WHERE id=?", [*data.values(), tid])
    row = db.one("SELECT * FROM tasks WHERE id=?", (tid,))
    if not row:
        raise ValueError(f"no task {tid}")
    return dict(row)


def set_task(db: DB, tid: int, action: str, days: int = 1) -> None:
    if action == "done":
        db.x("UPDATE tasks SET status='done', done_at=? WHERE id=?", (utcnow(), tid))
        from .pipeline import on_task_done

        on_task_done(db, tid)   # finishing an outreach step moves the lead along the pipeline
    elif action == "reopen":
        db.x("UPDATE tasks SET status='open', done_at=NULL WHERE id=?", (tid,))
    elif action == "snooze":
        db.x("UPDATE tasks SET snoozed_until=? WHERE id=?", ((_today() + timedelta(days=days)).isoformat(), tid))
    elif action == "delete":
        db.x("DELETE FROM tasks WHERE id=?", (tid,))
    else:
        raise ValueError("action must be done, reopen, snooze or delete")


def tasks(db: DB, scope: str = "open") -> list[dict]:
    """scope: open (everything not done, not snoozed) | today (due today/overdue/undated) | done (last 30 days)."""
    today = _today().isoformat()
    if scope == "done":
        since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        return [dict(r) for r in db.q("SELECT * FROM tasks WHERE status='done' AND done_at >= ? ORDER BY done_at DESC", (since,))]
    rows = db.q(
        "SELECT * FROM tasks WHERE status='open' AND (snoozed_until IS NULL OR snoozed_until <= ?) "
        "ORDER BY CASE WHEN due IS NULL THEN 1 ELSE 0 END, due, priority DESC, created_at",
        (today,),
    )
    out = [dict(r) for r in rows]
    for t in out:
        t["overdue"] = bool(t["due"] and t["due"] < today)
    if scope == "today":
        out = [t for t in out if not t["due"] or t["due"] <= today]
    return out


# -- who to reach out to -------------------------------------------------------

def reach_out(cfg: Config, db: DB, limit: int = 20) -> list[dict]:
    """Everyone you should contact now, with the reason and the best next action, most important first."""
    from .triage import followups_due, needs_reply

    items: list[dict] = []

    def add(priority: int, who: str, reason: str, action: str, **ref: Any) -> None:
        items.append({"priority": priority, "who": who, "reason": reason, "action": action, **ref})

    for r in needs_reply(cfg, db, None)[:8]:
        add(90 if r.priority >= 3 else 70, r.counterpart_name or r.counterpart, f"Waiting for your reply ({r.age_days:.0f}d): {r.subject}",
            "reply", email=r.counterpart, message_id=r.message_id)
    for f in followups_due(cfg, db)[:8]:
        add(60, f.counterpart, f"No answer for {f.age_days:.0f} days: {f.subject}", "follow up", email=f.counterpart, message_id=f.message_id)
    for p in partners(cfg, db):
        h = p["health"]
        if h["label"] != "healthy" and p["status"] in {"onboarding", "live"}:
            action = "move onboarding forward" if p["status"] == "onboarding" else "check in"
            add(75 if h["label"] == "at risk" else 55, p["name"], "Partner: " + "; ".join(h["reasons"]), action,
                partner_id=p["id"], email=p["email"] or "")
    for l in db.q("SELECT * FROM leads WHERE status='replied' ORDER BY updated_at"):
        add(80, l["name"], "Replied to your outreach: book a call", "book meeting", lead_id=l["id"], email=l["email"] or "")
    for l in db.q("SELECT * FROM leads WHERE status='meeting' AND updated_at <= ?", ((datetime.now(timezone.utc) - timedelta(days=5)).isoformat(),)):
        add(65, l["name"], "Met 5+ days ago: send the agreement / mark as partner", "close", lead_id=l["id"], email=l["email"] or "")
    month = _today().month
    if month in (5, 6, 7, 11, 12, 1):
        for l in db.q("SELECT * FROM leads WHERE kind='university' AND status IN ('new','drafted') ORDER BY score DESC LIMIT 3"):
            add(58, l["name"], "Next intake is coming: pitch the Buddy platform now", "first outreach", lead_id=l["id"], email=l["email"] or "")
    for l in db.q("SELECT * FROM leads WHERE status='new' AND email != '' AND kind != 'university' ORDER BY score DESC LIMIT 5"):
        add(40 + l["score"] / 10, l["name"], f"Strong new lead (score {l['score']:.0f}, seen on {', '.join(json.loads(l['sources'] or '[]'))})",
            "first outreach", lead_id=l["id"], email=l["email"])
    seen, out = set(), []
    for it in sorted(items, key=lambda x: -x["priority"]):
        key = (it["who"].lower(), it["action"])
        if key not in seen:
            seen.add(key)
            out.append(it)
    return out[:limit]


def sync_auto_tasks(cfg: Config, db: DB) -> int:
    """Turn the important signals into to-dos (once each): stuck onboarding, renewals, post-meeting follow-ups."""
    link_partners_to_leads(db)
    n = 0
    for p in partners(cfg, db):
        if p["status"] == "onboarding" and any("stuck" in r for r in p["health"]["reasons"]):
            nxt = STAGES[min(p["stage_index"] + 1, len(STAGES) - 1)][1]
            n += bool(add_task(db, f"{p['name']}: get to '{nxt}'", priority=3, source="auto", partner_id=p["id"],
                               due=_today().isoformat(), dedupe_key=f"stuck:{p['id']}:{p['stage']}"))
        if p["renewal_date"] and (date.fromisoformat(p["renewal_date"]) - _today()).days <= 30:
            n += bool(add_task(db, f"Renew agreement with {p['name']} (due {p['renewal_date']})", priority=3, source="auto",
                               partner_id=p["id"], due=p["renewal_date"], dedupe_key=f"renew:{p['id']}:{p['renewal_date']}"))
    now = datetime.now()
    for e in db.q("SELECT * FROM events WHERE end <= ? AND end >= ?", (now.strftime("%Y-%m-%dT%H:%M"), (now - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M"))):
        n += bool(add_task(db, f"Send follow-up after '{e['title']}'", priority=2, source="auto", due=e["end"][:10],
                           notes=e["attendees"] or "", dedupe_key=f"meeting-followup:{e['id']}"))
    return n


# -- daily routine + plan ------------------------------------------------------

DEFAULT_ROUTINE = [
    {"start": "08:30", "end": "09:00", "title": "Inbox & replies", "focus": "inbox"},
    {"start": "09:00", "end": "10:30", "title": "Partner outreach", "focus": "outreach"},
    {"start": "10:30", "end": "12:00", "title": "Partner onboarding & universities", "focus": "partners"},
    {"start": "13:00", "end": "14:00", "title": "Content & Instagram", "focus": "content"},
    {"start": "14:00", "end": "16:30", "title": "Calls, visits & workshops", "focus": "calls"},
    {"start": "16:30", "end": "17:00", "title": "Wrap-up: follow-ups & tomorrow", "focus": "review"},
]


def _hm(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def today_plan(cfg: Config, db: DB, now: datetime | None = None) -> dict:
    now = now or datetime.now()
    day = now.date().isoformat()
    weekday = now.weekday()
    blocks = [b for b in cfg.get("routine.blocks", DEFAULT_ROUTINE) if weekday in b.get("days", [0, 1, 2, 3, 4])]
    meetings = [dict(r) for r in db.q("SELECT * FROM events WHERE substr(start,1,10)=? ORDER BY start", (day,))]
    todo = tasks(db, "today")
    queue = reach_out(cfg, db, 30)
    ig_comments = json.loads(db.kv_get("instagram:unanswered_comments", "[]"))
    by_focus = {
        "inbox": [f"Reply: {q['who']}" for q in queue if q["action"] == "reply"][:6],
        "outreach": [f"{q['action'].capitalize()}: {q['who']}" for q in queue if q["action"] in {"follow up", "first outreach", "book meeting"}][:6],
        "partners": [f"{q['action'].capitalize()}: {q['who']}" for q in queue if "partner_id" in q or q["action"] == "close"][:6],
        "content": ([f"Answer {len(ig_comments)} Instagram comments"] if ig_comments else []) + ["Post today's reel / story"],
        "calls": [],
        "review": [t["title"] for t in todo][:6],
    }
    try:   # the week planner decides which to-dos go into which block today
        from .planner import plan_week, scheduled_on

        sched = scheduled_on(plan_week(cfg, db, now=now), day)
    except Exception:
        sched = {}
    items = []
    for m in meetings:
        items.append({"type": "meeting", "start": m["start"][11:16], "end": m["end"][11:16], "title": m["title"],
                      "detail": m["location"] or "", "event_id": m["id"], "source": m["source"]})
    for b in blocks:
        s, e = _hm(b["start"]), _hm(b["end"])
        clash = [m for m in meetings if _hm(m["start"][11:16]) < e and _hm(m["end"][11:16]) > s]
        items.append({"type": "block", "start": b["start"], "end": b["end"], "title": b["title"], "focus": b.get("focus", ""),
                      "suggestions": by_focus.get(b.get("focus", ""), []), "clash": [m["title"] for m in clash],
                      "scheduled": sched.get(b["title"], [])})
    items.sort(key=lambda x: (x["start"], 0 if x["type"] == "meeting" else 1))
    cur = now.hour * 60 + now.minute
    active = [i for i in items if _hm(i["start"]) <= cur < _hm(i["end"])]
    current = next((i for i in active if i["type"] == "meeting"), active[0] if active else None)  # meetings win
    upcoming = next((i for i in items if _hm(i["start"]) > cur), None)
    return {"date": day, "items": items, "now": now.strftime("%H:%M"), "current": current, "next": upcoming,
            "tasks_today": todo[:12]}


def focus_now(cfg: Config, db: DB) -> str:
    plan = today_plan(cfg, db)
    c, n = plan["current"], plan["next"]
    if c and c["type"] == "meeting":
        return f"You're in '{c['title']}' until {c['end']}."
    if c:
        tip = c["suggestions"][0] if c.get("suggestions") else (plan["tasks_today"][0]["title"] if plan["tasks_today"] else "")
        return f"Now: {c['title']} until {c['end']}." + (f" Start with: {tip}." if tip else "") + (
            f" Next: {n['title']} at {n['start']}." if n else "")
    if n:
        return f"Next up: {n['title']} at {n['start']}."
    return "Nothing else planned today." + (f" Open to-dos: {len(plan['tasks_today'])}." if plan["tasks_today"] else "")


# -- meeting prep ---------------------------------------------------------------

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def meeting_prep(cfg: Config, db: DB, event_id: str, llm=None) -> str:
    e = db.one("SELECT * FROM events WHERE id=?", (event_id,))
    if not e:
        raise ValueError("meeting not found")
    emails = [m.lower() for m in _EMAIL.findall(e["attendees"] or "") if m.lower() not in cfg.my_addresses]
    domains = {m.split("@")[1] for m in emails}
    lines = [f"# Prep: {e['title']}", f"*{e['start'][:10]} {e['start'][11:16]}–{e['end'][11:16]}* · {e['location'] or ''}", ""]
    lines.append("## Who")
    lines += [f"- {a.strip()}" for a in (e["attendees"] or "").split(",") if a.strip()] or ["- (no attendees listed)"]
    context = []
    for dom in domains:
        for l in db.q("SELECT * FROM leads WHERE email LIKE ? OR website LIKE ?", (f"%@{dom}", f"%{dom}%")):
            context.append(f"Lead: {l['name']} ({l['kind']}, status {l['status']}, score {l['score']:.0f}). Notes: {l['notes'] or '-'}")
        for p in db.q("SELECT * FROM partners WHERE email LIKE ? OR website LIKE ?", (f"%@{dom}", f"%{dom}%")):
            context.append(f"Partner: {p['name']} ({p['kind']}, {p['status']}, stage {p['stage']}). Offer: {p['offer'] or '-'}. Notes: {p['notes'] or '-'}")
    generic = {"call", "meeting", "with", "settleezy", "buddy", "platform", "international", "office", "partnership", "chat",
               "workshop", "berlin", "university", "intro", "follow", "sync", "students", "student", "termin", "gespräch"}
    title_words = [] if context else [w for w in re.findall(r"[A-Za-zÄÖÜäöüß]{3,}", e["title"]) if w.lower() not in generic]
    for w in title_words[:3]:
        for l in db.q("SELECT * FROM leads WHERE name LIKE ? LIMIT 2", (f"%{w}%",)):
            context.append(f"Lead: {l['name']} ({l['kind']}, status {l['status']}). Notes: {l['notes'] or '-'}")
    lines += ["", "## What we know"] + ([f"- {c}" for c in dict.fromkeys(context)] or ["- No lead or partner record matches yet."])
    history = []
    for m in emails[:4]:
        history += db.q(
            "SELECT * FROM messages WHERE from_addr=? OR to_addrs LIKE ? ORDER BY sent_at DESC LIMIT 4", (m, f'%"{m}"%'))
    history = sorted({h["id"]: h for h in history}.values(), key=lambda h: h["sent_at"], reverse=True)[:6]
    lines += ["", "## Recent emails"]
    lines += [f"- {h['sent_at'][:10]} {'you →' if h['folder'] == 'sent' else '← them'}: **{h['subject']}**: {(h['body_text'] or '')[:160].strip()}…"
              for h in history] or ["- No emails with these people yet."]
    agenda = ""
    if llm is not None:
        try:
            from .knowledge import ONE_LINER

            agenda = llm.cloud(
                f"{ONE_LINER}\n\nMeeting: {e['title']}\nAttendees: {e['attendees']}\nContext:\n" + "\n".join(context)
                + "\n\nRecent emails:\n" + "\n".join(f"{h['subject']}: {(h['body_text'] or '')[:400]}" for h in history)
                + "\n\nWrite: 1) the goal of this meeting in one line, 2) a 4-point agenda, 3) the one ask to close, "
                  "4) two likely objections with answers. Markdown, concise.",
                "You are Setz, the founder's chief of staff. Be practical.", effort="low", max_tokens=2000)
        except Exception as exc:  # prep still useful without the AI part
            agenda = f"(AI agenda unavailable: {exc})"
    if agenda:
        lines += ["", "## Plan", agenda]
    return "\n".join(lines) + "\n"


# -- weekly scorecard ------------------------------------------------------------

def scorecard(cfg: Config, db: DB) -> list[dict]:
    today = _today()
    this_start = today - timedelta(days=today.weekday())
    last_start = this_start - timedelta(days=7)

    def window(start: date) -> tuple[str, str]:
        return start.isoformat(), (start + timedelta(days=7)).isoformat()

    def count(sql: str, start: date) -> int:
        a, b = window(start)
        return db.one(sql, (a, b))["n"]

    me = tuple(cfg.my_addresses) or ("",)
    rows = [
        ("Outreach emails sent", "SELECT COUNT(*) n FROM drafts WHERE kind='outreach' AND sent_at >= ? AND sent_at < ?"),
        ("Replies received", "SELECT COUNT(*) n FROM messages WHERE folder='inbox' AND automated=0 AND sent_at >= ? AND sent_at < ? "
                             f"AND from_addr NOT IN ({','.join('?' * len(me))})"),
        ("Meetings held", "SELECT COUNT(*) n FROM events WHERE start >= ? AND start < ?"),
        ("Partners gone live", "SELECT COUNT(*) n FROM partners WHERE live_at >= ? AND live_at < ?"),
        ("New leads", "SELECT COUNT(*) n FROM leads WHERE created_at >= ? AND created_at < ?"),
        ("To-dos completed", "SELECT COUNT(*) n FROM tasks WHERE status='done' AND done_at >= ? AND done_at < ?"),
    ]
    out = []
    for label, sql in rows:
        if "NOT IN" in sql:
            a1, b1 = window(this_start)
            a0, b0 = window(last_start)
            cur = db.one(sql, (a1, b1, *me))["n"]
            prev = db.one(sql, (a0, b0, *me))["n"]
        else:
            cur, prev = count(sql, this_start), count(sql, last_start)
        out.append({"metric": label, "this_week": cur, "last_week": prev, "delta": cur - prev})
    return out
