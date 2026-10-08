"""Meeting requests -> your calendar.

After every Outlook sync Setz looks for messages asking to meet ("can we have a call?", "hätten Sie nächste Woche
Zeit für einen Termin?", "are you free Tuesday at 2?"), and Instagram comments asking to meet or collaborate. For each:

  * times they proposed are read (weekday/date + time, EN/DE) and checked against your calendar
  * otherwise 3 free slots on different days are suggested: inside working hours, around existing meetings with a
    buffer, preferring your routine's calls block, avoiding days that already have too many meetings
  * one click writes the reply as an Outlook DRAFT with the slots (never sent by Setz)
  * when you pick a slot, Setz books the event in Outlook (optionally with a Teams link and an invitation to them),
    adds it to today's plan, moves the lead to "meeting" and creates a prep to-do
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, time as dtime, timedelta
from typing import Any

from .config import Config
from .db import DB, utcnow

REQUEST = re.compile(
    r"\b(call|meeting|meet(?:\s+up)?|catch[- ]up|chat|zoom|teams call|google meet|coffee|video ?call|phone call|appointment|"
    r"time slot|slot|availability|available|free (?:on|next|this|for)|when (?:are|would) you|schedule|"
    r"termin|treffen|telefonat|anruf|gespräch|kennenlernen|zeit für|zeit haben|verfügbar|videocall|besprechung|"
    r"kaffee trinken|vorbeikommen|austausch)\b", re.I)
ASK = re.compile(r"\?|\b(would|could|can|shall|let'?s|möchten|könnten|können|hätten|wollen|lass uns|gerne)\b", re.I)
COLLAB = re.compile(r"\b(collab|kooperation|zusammenarbeit|partnership|partner|meet|treffen|call|dm me|schreib mir)\b", re.I)

WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6,
            "mon": 0, "tue": 1, "tues": 1, "wed": 2, "thu": 3, "thur": 3, "thurs": 3, "fri": 4,
            "montag": 0, "dienstag": 1, "mittwoch": 2, "donnerstag": 3, "freitag": 4, "samstag": 5, "sonntag": 6,
            "mo": 0, "di": 1, "mi": 2, "do": 3, "fr": 4}
DAY_NAMES = {"en": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"], "de": ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]}
MONTHS = {"en": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
          "de": ["Jan.", "Feb.", "März", "Apr.", "Mai", "Juni", "Juli", "Aug.", "Sep.", "Okt.", "Nov.", "Dez."]}

_TIME = r"(?:at|um|ab|gegen|@)?\s*(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm|uhr|h\b)?"


def _hour(h: str, m: str | None, ampm: str | None) -> dtime | None:
    hh, mm = int(h), int(m or 0)
    if ampm and ampm.lower() == "pm" and hh < 12:
        hh += 12
    if ampm and ampm.lower() == "am" and hh == 12:
        hh = 0
    if not ampm and 1 <= hh <= 7:   # "Tuesday at 2" means 14:00 in a work context
        hh += 12
    if not (0 <= hh <= 23 and 0 <= mm <= 59) or not (7 <= hh <= 21):
        return None
    return dtime(hh, mm)


def proposed_times(text: str, sent: datetime) -> list[datetime]:
    """Times the other person suggested, resolved relative to when they wrote ('Tuesday at 2pm', 'am 14.10. um 10 Uhr')."""
    out: list[datetime] = []
    t = text.lower()
    base = sent.date()
    for m in re.finditer(r"\b(next\s+|nächsten?\s+|kommenden?\s+)?(" + "|".join(sorted(WEEKDAYS, key=len, reverse=True)) + r")\b\.?,?\s*" + _TIME, t):
        tm = _hour(m.group(3), m.group(4), m.group(5))
        if not tm or (len(m.group(2)) <= 2 and not m.group(5) and not m.group(4)):   # "do 3" is too ambiguous in German
            continue
        ahead = (WEEKDAYS[m.group(2)] - base.weekday()) % 7 or 7   # the next such day after they wrote
        out.append(datetime.combine(base + timedelta(days=ahead), tm))
    for m in re.finditer(r"\b(\d{1,2})\.(\d{1,2})\.(\d{2,4})?\s*,?\s*" + _TIME, t):
        tm = _hour(m.group(4), m.group(5), m.group(6))
        if not tm or not m.group(6) and not m.group(5):
            continue
        try:
            y = int(m.group(3)) if m.group(3) else base.year
            d = date(y + 2000 if y < 100 else y, int(m.group(2)), int(m.group(1)))
        except ValueError:
            continue
        if d < base and not m.group(3):
            d = d.replace(year=d.year + 1)
        out.append(datetime.combine(d, tm))
    for m in re.finditer(r"\b(tomorrow|morgen)\b\s*" + _TIME, t):
        tm = _hour(m.group(2), m.group(3), m.group(4))
        if tm and (m.group(4) or m.group(3)):
            out.append(datetime.combine(base + timedelta(days=1), tm))
    return sorted(set(out))


# -- availability --------------------------------------------------------------------------------

def _busy(db: DB, day: date) -> list[tuple[datetime, datetime]]:
    out = []
    for e in db.q("SELECT start, end FROM events WHERE substr(start,1,10)=?", (day.isoformat(),)):
        try:
            out.append((datetime.fromisoformat(e["start"][:16]), datetime.fromisoformat((e["end"] or e["start"])[:16])))
        except ValueError:
            continue
    return out


def is_free(cfg: Config, db: DB, start: datetime, minutes: int) -> bool:
    buf = timedelta(minutes=int(cfg.get("scheduling.buffer_minutes", 15)))
    end = start + timedelta(minutes=minutes)
    ds, de = (dtime.fromisoformat(cfg.get("scheduling.day_start", "09:00")), dtime.fromisoformat(cfg.get("scheduling.day_end", "18:00")))
    if start.weekday() >= 5 or start.time() < ds or end.time() > de or end.date() != start.date():
        return False
    return all(end + buf <= s or start >= e + buf for s, e in _busy(db, start.date()))


def suggest_slots(cfg: Config, db: DB, minutes: int | None = None, n: int = 3, now: datetime | None = None,
                  days_ahead: int = 8) -> list[dict]:
    """Free slots on different days, best first by: inside the calls block, a light meeting day, soonest."""
    from .ops import DEFAULT_ROUTINE

    minutes = minutes or int(cfg.get("scheduling.meeting_minutes", 30))
    now = now or datetime.now()
    earliest = now + timedelta(hours=int(cfg.get("scheduling.notice_hours", 18)))
    max_load = float(cfg.get("planner.max_meeting_hours_per_day", 4)) * 60
    calls = [b for b in cfg.get("routine.blocks", DEFAULT_ROUTINE) if b.get("focus") == "calls"]
    cands = []
    for i in range(days_ahead):
        day = (now + timedelta(days=i)).date()
        if day.weekday() >= 5:
            continue
        load = sum((e - s).total_seconds() / 60 for s, e in _busy(db, day))
        if load + minutes > max_load:
            continue
        t = datetime.combine(day, dtime(8, 0))
        while t.date() == day and t.hour < 20:
            if t >= earliest and is_free(cfg, db, t, minutes):
                in_calls = any(b["start"] <= t.strftime("%H:%M") and (t + timedelta(minutes=minutes)).strftime("%H:%M") <= b["end"]
                               and day.weekday() in b.get("days", [0, 1, 2, 3, 4]) for b in calls)
                score = (2 if in_calls else 0) + (1 - load / max(max_load, 1)) - i * 0.15 + (0.3 if t.minute == 0 else 0)
                cands.append((score, t))
            t += timedelta(minutes=30)
    picked: list[datetime] = []
    for _, t in sorted(cands, key=lambda c: -c[0]):
        if all(p.date() != t.date() for p in picked):
            picked.append(t)
        if len(picked) >= n:
            break
    return [{"start": p.isoformat(timespec="minutes"), "end": (p + timedelta(minutes=minutes)).isoformat(timespec="minutes")}
            for p in sorted(picked)]


def fmt_slot(slot: dict, lang: str = "en") -> str:
    s, e = datetime.fromisoformat(slot["start"]), datetime.fromisoformat(slot["end"])
    lang = lang if lang in DAY_NAMES else "en"
    if lang == "de":
        return f"{DAY_NAMES['de'][s.weekday()]}, {s.day}. {MONTHS['de'][s.month - 1]} · {s:%H:%M}–{e:%H:%M} Uhr"
    return f"{DAY_NAMES['en'][s.weekday()]} {s.day} {MONTHS['en'][s.month - 1]} · {s:%H:%M}–{e:%H:%M}"


# -- requests ----------------------------------------------------------------------------------------

def _ensure(db: DB) -> None:
    db.conn.execute(
        "CREATE TABLE IF NOT EXISTS meeting_requests (id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT, ref TEXT UNIQUE, "
        "message_id TEXT, who TEXT, email TEXT, subject TEXT, snippet TEXT, lang TEXT, received_at TEXT, detected_at TEXT, "
        "proposed TEXT, slots TEXT, status TEXT DEFAULT 'open', lead_id INTEGER, draft_id TEXT, event_id TEXT, booked_start TEXT)")
    db.conn.commit()


def looks_like_request(subject: str, body: str) -> bool:
    text = f"{subject}\n{body[:1500]}"
    return bool(REQUEST.search(text) and ASK.search(text))


def _lead_for(db: DB, email: str) -> int | None:
    if not email:
        return None
    row = db.one("SELECT id FROM leads WHERE lower(email)=lower(?)", (email,))
    if row:
        return row["id"]
    dom = email.split("@")[-1].lower()
    if dom in {"gmail.com", "web.de", "gmx.de", "gmx.net", "outlook.com", "hotmail.com", "yahoo.com", "icloud.com", "t-online.de"}:
        return None
    row = db.one("SELECT id FROM leads WHERE lower(email) LIKE ? OR lower(website) LIKE ?", (f"%@{dom}", f"%{dom}%"))
    return row["id"] if row else None


def detect(cfg: Config, db: DB, days: int = 14) -> dict[str, int]:
    """Scan recent inbox mail (and Instagram comments) for open meeting requests."""
    _ensure(db)
    me = tuple(cfg.my_addresses) or ("",)
    since = (datetime.now() - timedelta(days=days)).isoformat()
    new = 0
    for m in db.q(f"SELECT * FROM messages WHERE folder='inbox' AND automated=0 AND sent_at >= ? AND from_addr NOT IN ({','.join('?' * len(me))}) "
                  "ORDER BY sent_at DESC", (since, *me)):
        if not looks_like_request(m["subject"] or "", m["body_text"] or ""):
            continue
        answered = db.one("SELECT 1 FROM messages WHERE conversation_id=? AND folder='sent' AND sent_at > ?", (m["conversation_id"], m["sent_at"]))
        if answered:
            db.x("UPDATE meeting_requests SET status='answered' WHERE ref=? AND status='open'", ("mail:" + m["id"],))
            continue
        sent = datetime.fromisoformat(m["sent_at"].replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
        prop = [p.isoformat(timespec="minutes") for p in proposed_times(f"{m['subject']} {m['body_text'] or ''}", sent) if p > datetime.now()]
        cur = db.x("INSERT OR IGNORE INTO meeting_requests(source,ref,message_id,who,email,subject,snippet,lang,received_at,detected_at,proposed,lead_id) "
                   "VALUES('email',?,?,?,?,?,?,?,?,?,?,?)",
                   ("mail:" + m["id"], m["id"], m["from_name"] or m["from_addr"], m["from_addr"], m["subject"],
                    re.sub(r"\s+", " ", m["body_text"] or "")[:400], m["language"] or "en", m["sent_at"], utcnow(),
                    json.dumps(prop), _lead_for(db, m["from_addr"])))
        new += cur.rowcount
    for c in json.loads(db.kv_get("instagram:unanswered_comments", "[]") or "[]"):
        if COLLAB.search(c.get("text") or "") and ASK.search(c.get("text") or ""):
            cur = db.x("INSERT OR IGNORE INTO meeting_requests(source,ref,who,subject,snippet,lang,received_at,detected_at,proposed) "
                       "VALUES('instagram',?,?,?,?,?,?,?,'[]')",
                       (f"ig:{c.get('user')}:{c.get('at')}", "@" + (c.get("user") or ""), f"Instagram comment on {c.get('post')}",
                        (c.get("text") or "")[:400], "en", c.get("at"), utcnow()))
            new += cur.rowcount
    return {"new_requests": new, "open": db.one("SELECT COUNT(*) n FROM meeting_requests WHERE status IN ('open','proposed')")["n"]}


def requests(cfg: Config, db: DB, status: tuple[str, ...] = ("open", "proposed")) -> list[dict]:
    _ensure(db)
    rows = [dict(r) for r in db.q(f"SELECT * FROM meeting_requests WHERE status IN ({','.join('?' * len(status))}) ORDER BY received_at DESC",
                                  status)]
    slots_cache: list[dict] | None = None
    for r in rows:
        r["proposed"] = json.loads(r["proposed"] or "[]")
        r["proposed_free"] = [p for p in r["proposed"] if is_free(cfg, db, datetime.fromisoformat(p), int(cfg.get("scheduling.meeting_minutes", 30)))]
        if r["slots"]:
            r["slots"] = json.loads(r["slots"])
        else:
            slots_cache = slots_cache if slots_cache is not None else suggest_slots(cfg, db)
            r["slots"] = slots_cache
        lead = db.one("SELECT name, status, kind FROM leads WHERE id=?", (r["lead_id"],)) if r["lead_id"] else None
        r["lead"] = dict(lead) if lead else None
    return rows


def reply_text(cfg: Config, req: dict, slots: list[dict]) -> str:
    lang = "de" if req.get("lang") == "de" else "en"
    first = re.split(r"[\s,]+", (req.get("who") or "").replace('"', "").strip())[0] if req.get("who") and "@" not in req["who"] else ""
    me = cfg.me.get("name", "")
    calendly = cfg.get("scheduling.calendly_link", "")
    online = cfg.get("scheduling.online_default", True)
    if req.get("proposed_free"):
        s = {"start": req["proposed_free"][0], "end": (datetime.fromisoformat(req["proposed_free"][0])
                                                       + timedelta(minutes=int(cfg.get("scheduling.meeting_minutes", 30)))).isoformat()}
        if lang == "de":
            return (f"Hallo{' ' + first if first else ''},\n\n{fmt_slot(s, 'de')} passt mir gut, ich schicke Ihnen gleich eine "
                    f"Einladung{' mit Teams-Link' if online else ''}.\n\nViele Grüße\n{me}")
        return f"Hi{' ' + first if first else ''},\n\n{fmt_slot(s)} works for me. I'll send you an invite{' with a Teams link' if online else ''}.\n\nBest,\n{me}"
    lines = "\n".join("• " + fmt_slot(s, lang) for s in slots)
    if lang == "de":
        return (f"Hallo{' ' + first if first else ''},\n\nvielen Dank für Ihre Nachricht, ich freue mich auf das Gespräch! "
                f"Passt Ihnen einer dieser Termine?\n\n{lines}\n\n"
                + ("Gerne per Teams; den Link schicke ich mit der Einladung. " if online else "")
                + ("Falls nichts passt, wählen Sie gerne direkt hier einen Termin: " + calendly if calendly else "Falls nichts passt, schlagen Sie gerne einen anderen Zeitpunkt vor.")
                + f"\n\nViele Grüße\n{me}")
    return (f"Hi{' ' + first if first else ''},\n\nthanks for reaching out, happy to talk! Would one of these work for you?\n\n{lines}\n\n"
            + ("Happy to do it on Teams; I'll send the link with the invite. " if online else "")
            + ("If none fits, just pick a time here: " + calendly if calendly else "If none fits, just suggest another time.")
            + f"\n\nBest,\n{me}")


def draft_reply(cfg: Config, db: DB, req_id: int, graph=None) -> dict:
    """Write the reply with slots as an Outlook draft (never sent). Instagram requests return the text to paste."""
    req = next((r for r in requests(cfg, db, ("open", "proposed")) if r["id"] == req_id), None)
    if not req:
        raise ValueError("request not found or already handled")
    text = reply_text(cfg, req, req["slots"])
    draft_id = ""
    if req["source"] == "email" and req["message_id"]:
        from .msgraph import Graph

        draft = (graph or Graph(cfg)).create_reply_draft(req["message_id"], text)
        draft_id = draft.get("id", "")
    db.x("UPDATE meeting_requests SET status='proposed', slots=?, draft_id=? WHERE id=?", (json.dumps(req["slots"]), draft_id, req_id))
    return {"text": text, "draft_id": draft_id, "slots": req["slots"]}


def book(cfg: Config, db: DB, req_id: int, start: str, minutes: int | None = None, *, invite: bool = True,
         online: bool | None = None, graph=None) -> dict:
    """Create the Outlook event for the chosen slot; update plan, lead and to-dos."""
    _ensure(db)
    req = db.one("SELECT * FROM meeting_requests WHERE id=?", (req_id,))
    if not req:
        raise ValueError("request not found")
    minutes = minutes or int(cfg.get("scheduling.meeting_minutes", 30))
    s = datetime.fromisoformat(start)
    e = s + timedelta(minutes=minutes)
    if not is_free(cfg, db, s, minutes):
        raise ValueError(f"{fmt_slot({'start': s.isoformat(), 'end': e.isoformat()})} is no longer free")
    online = cfg.get("scheduling.online_default", True) if online is None else online
    who = req["who"] or req["email"] or "Partner"
    lead = db.one("SELECT * FROM leads WHERE id=?", (req["lead_id"],)) if req["lead_id"] else None
    subject = f"Settleezy × {lead['name'] if lead else who}"
    from .msgraph import Graph

    ev = (graph or Graph(cfg)).create_event(
        subject, s, e, attendees=[(req["email"], req["who"] or "")] if invite and req["email"] else None,
        body=f"Re: {req['subject']}\n\nBooked by Setz from your request.", online=online)
    eid = "outlook:" + ev.get("id", f"local-{req_id}")
    db.x("INSERT OR REPLACE INTO events(id,source,title,start,end,location,attendees,url) VALUES(?,?,?,?,?,?,?,?)",
         (eid, "outlook", subject, s.isoformat(timespec="minutes"), e.isoformat(timespec="minutes"),
          "Online (Teams)" if online else "", req["email"] or "", ev.get("webLink", "")))
    db.x("UPDATE meeting_requests SET status='booked', event_id=?, booked_start=? WHERE id=?", (eid, s.isoformat(timespec="minutes"), req_id))
    from .ops import add_task

    add_task(db, f"Prep for {subject} ({s:%a %d.%m. %H:%M})", due=(s - timedelta(days=1)).date().isoformat() if s.date() > date.today() else s.date().isoformat(),
             priority=2, source="auto", lead_id=req["lead_id"], dedupe_key=f"prep:{eid}", category="calls", est_minutes=15)
    if lead and lead["status"] in {"new", "drafted", "contacted", "replied"}:
        from .leads import set_status

        set_status(db, lead["id"], "meeting", f"Meeting booked {s:%d.%m. %H:%M}")
    return {"event_id": eid, "start": s.isoformat(timespec="minutes"), "end": e.isoformat(timespec="minutes"), "subject": subject,
            "invite_sent_by_outlook": bool(invite and req["email"]), "teams": bool(online)}


def dismiss(db: DB, req_id: int) -> None:
    _ensure(db)
    db.x("UPDATE meeting_requests SET status='dismissed' WHERE id=?", (req_id,))
