"""Week planner: time-blocks your to-dos into your routine around real meetings, measures how loaded each day
is and tells you how to make the week work better.

Every to-do has a category (inbox, outreach, partners, content, calls, visits, review, admin) and an estimate in
minutes, inferred from its title unless you set them. Each routine block has a focus; tasks go to the first free
slot of a block with the matching focus on or before their due date, highest priority first. What doesn't fit is
listed as overflow so you can decide what to drop, delegate or move, instead of discovering it on Friday.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

from .config import Config
from .db import DB

CATEGORIES = {
    "inbox": (r"\b(reply|respond|answer|antwort|inbox|email back|get back to)\b", 10),
    "visits": (r"\b(drop by|visit|walk|in person|vorbeigehen|flyer)\b", 30),
    "calls": (r"\b(call|meeting|meet|zoom|teams|anruf|telefon|workshop|pitch call|demo)\b", 30),
    "outreach": (r"\b(outreach|reach out|follow[- ]?up|dm|contact|pitch|introduce|intro|nachfassen|anschreiben|linkedin)\b", 15),
    "partners": (r"\b(onboard|partner|contract|agreement|vertrag|offer|renew|listing|list in the app|redemption)\b", 30),
    "content": (r"\b(post|reel|story|stories|instagram|tiktok|content|newsletter|blog|caption|design|canva|video)\b", 45),
    "review": (r"\b(review|plan|report|kpi|scorecard|retro|weekly)\b", 20),
    "admin": (r"\b(invoice|rechnung|tax|steuer|bank|admin|contract draft|paperwork|insurance|accounting)\b", 20),
}
DEFAULT_MINUTES = {k: v[1] for k, v in CATEGORIES.items()}
# Which tasks each routine-block focus takes; the first category is the block's own.
FOCUS_TAKES = {
    "inbox": ["inbox"],
    "outreach": ["outreach"],
    "partners": ["partners", "admin"],
    "content": ["content"],
    "calls": ["calls", "visits"],
    "review": ["review", "admin", "inbox"],
}
WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def infer_category(title: str) -> str:
    t = (title or "").lower()
    for cat, (rx, _) in CATEGORIES.items():
        if re.search(rx, t):
            return cat
    return "admin"


def estimate(title: str, category: str | None = None) -> int:
    m = re.search(r"\b(\d{1,3})\s?(min|mins|minutes|m)\b", title or "", re.I)
    if m:
        return int(m.group(1))
    m = re.search(r"\b(\d{1,2}(?:[.,]\d)?)\s?(h|hr|hrs|hours?|std)\b", title or "", re.I)
    if m:
        return int(float(m.group(1).replace(",", ".")) * 60)
    return DEFAULT_MINUTES.get(category or infer_category(title), 20)


def _hm(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def _fmt(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def _subtract(seg: tuple[int, int], busy: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out = [seg]
    for bs, be in busy:
        nxt = []
        for s, e in out:
            if be <= s or bs >= e:
                nxt.append((s, e))
                continue
            if bs > s:
                nxt.append((s, bs))
            if be < e:
                nxt.append((be, e))
        out = nxt
    return [(s, e) for s, e in out if e - s >= 5]


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def week_start_for(today: date) -> date:
    """This week's Monday; on weekends plan the coming week."""
    return _monday(today) + (timedelta(days=7) if today.weekday() >= 5 else timedelta())


def _routine(cfg: Config) -> list[dict]:
    from .ops import DEFAULT_ROUTINE

    return cfg.get("routine.blocks", DEFAULT_ROUTINE)


def _open_tasks(db: DB) -> list[dict]:
    rows = [dict(r) for r in db.q("SELECT * FROM tasks WHERE status='open'")]
    for t in rows:
        t["category"] = t.get("category") or infer_category(t["title"])
        t["est_minutes"] = t.get("est_minutes") or estimate(t["title"], t["category"])
    return rows


def plan_week(cfg: Config, db: DB, start: date | None = None, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now()
    today = now.date()
    monday = _monday(start) if start else week_start_for(today)
    n_days = int(cfg.get("planner.days_per_week", 5))
    days = [monday + timedelta(days=i) for i in range(n_days)]
    routine = _routine(cfg)

    plan_days: list[dict] = []
    segments: list[dict] = []
    for d in days:
        iso = d.isoformat()
        meetings = [dict(r) for r in db.q("SELECT * FROM events WHERE substr(start,1,10)=? ORDER BY start", (iso,))]
        busy = []
        for m in meetings:
            if len(m["start"]) >= 16 and len(m["end"] or "") >= 16:
                s = _hm(m["start"][11:16])
                e = _hm(m["end"][11:16]) if m["end"][:10] == iso else 24 * 60
                busy.append((s, max(e, s)))
        blocks = []
        for b in routine:
            if d.weekday() not in b.get("days", [0, 1, 2, 3, 4]):
                continue
            s, e = _hm(b["start"]), _hm(b["end"])
            free = _subtract((s, e), busy)
            if d < today:
                free = []
            elif d == today:
                cur = now.hour * 60 + now.minute
                free = [(max(fs, cur), fe) for fs, fe in free if fe - max(fs, cur) >= 5]
            blk = {"title": b["title"], "focus": b.get("focus", ""), "start": b["start"], "end": b["end"],
                   "free_min": sum(fe - fs for fs, fe in free), "items": []}
            blocks.append(blk)
            for fs, fe in free:
                segments.append({"day": d, "block": blk, "start": fs, "end": fe, "cursor": fs})
        plan_days.append({"date": iso, "weekday": WEEKDAY_NAMES[d.weekday()], "past": d < today,
                          "meetings": [{"title": m["title"], "start": m["start"][11:16], "end": (m["end"] or "")[11:16],
                                        "location": m["location"], "source": m["source"], "id": m["id"]} for m in meetings],
                          "blocks": blocks, "_busy": busy})

    first_day = max(today, monday)
    last_day = days[-1]
    tasks = [t for t in _open_tasks(db) if not (t["snoozed_until"] and t["snoozed_until"] > last_day.isoformat())]
    later = [t for t in tasks if t["due"] and t["due"] > last_day.isoformat()]
    todo = [t for t in tasks if t not in later]
    todo.sort(key=lambda t: (0 if t["due"] else 1, t["due"] or "", -(t["priority"] or 2), t["created_at"] or ""))

    overflow = []
    for t in todo:
        earliest = max(first_day, date.fromisoformat(t["snoozed_until"])) if t["snoozed_until"] else first_day
        deadline = max(date.fromisoformat(t["due"]), first_day) if t["due"] else last_day
        need = int(t["est_minutes"])

        def fits(seg: dict, cats: list[str]) -> bool:
            return (earliest <= seg["day"] <= deadline and seg["end"] - seg["cursor"] >= need
                    and t["category"] in cats)

        # 1) a block whose own focus matches, 2) a block that also takes this category
        seg = next((s for s in segments if fits(s, FOCUS_TAKES.get(s["block"]["focus"], [])[:1])), None) \
            or next((s for s in segments if fits(s, FOCUS_TAKES.get(s["block"]["focus"], []))), None)
        if seg is None:   # any non-inbox block before the deadline beats missing it
            seg = next((s for s in segments if s["block"]["focus"] != "inbox" and earliest <= s["day"] <= deadline
                        and s["end"] - s["cursor"] >= need), None)
        if seg is None and need > 60:   # deep work: split into 30+ minute chunks across the week
            chunks = _chunk(segments, need, earliest, deadline)
            if chunks:
                for k, (sg, mins) in enumerate(chunks, 1):
                    s0 = sg["cursor"]
                    sg["cursor"] += mins
                    sg["block"]["items"].append({
                        "task_id": t["id"], "title": f"{t['title']} (part {k}/{len(chunks)})", "start": _fmt(s0),
                        "end": _fmt(s0 + mins), "minutes": mins, "category": t["category"], "priority": t["priority"],
                        "due": t["due"], "lead_id": t["lead_id"], "date": sg["day"].isoformat(),
                        "late": bool(t["due"] and t["due"] < sg["day"].isoformat())})
                continue
        if seg is None:
            reason = "too long for any free slot" if need > max((s["end"] - s["start"] for s in segments), default=0) else \
                "no free slot before the due date" if t["due"] else "week is full"
            overflow.append({"task_id": t["id"], "title": t["title"], "minutes": need, "due": t["due"],
                             "priority": t["priority"], "category": t["category"], "reason": reason})
            continue
        s0 = seg["cursor"]
        seg["cursor"] += need
        seg["block"]["items"].append({"task_id": t["id"], "title": t["title"], "start": _fmt(s0), "end": _fmt(s0 + need),
                                      "minutes": need, "category": t["category"], "priority": t["priority"],
                                      "due": t["due"], "lead_id": t["lead_id"], "date": seg["day"].isoformat(),
                                      "late": bool(t["due"] and t["due"] < seg["day"].isoformat())})

    for pd in plan_days:
        _load(pd)
    totals = {
        "meeting_hours": round(sum(d["load"]["meeting_min"] for d in plan_days) / 60, 1),
        "focus_hours": round(sum(d["load"]["free_min"] for d in plan_days) / 60, 1),
        "scheduled_hours": round(sum(d["load"]["scheduled_min"] for d in plan_days) / 60, 1),
        "overflow_hours": round(sum(o["minutes"] for o in overflow) / 60, 1),
        "tasks_scheduled": sum(len(b["items"]) for d in plan_days for b in d["blocks"]),
        "tasks_overflow": len(overflow),
    }
    result = {"week_start": monday.isoformat(), "days": plan_days, "overflow": overflow, "totals": totals,
              "later": [{"task_id": t["id"], "title": t["title"], "due": t["due"]} for t in later][:20]}
    result["insights"] = recommendations(cfg, db, result)
    return result


def _chunk(segments: list[dict], need: int, earliest: date, deadline: date) -> list[tuple[dict, int]] | None:
    """Pick the largest free slots (non-inbox, 30+ min) until the task is covered; None if the week can't hold it."""
    pool = sorted((s for s in segments if s["block"]["focus"] not in {"inbox", "review"}
                   and earliest <= s["day"] <= deadline and s["end"] - s["cursor"] >= 30),
                  key=lambda s: -(s["end"] - s["cursor"]))
    out, left = [], need
    for s in pool:
        if left <= 0:
            break
        take = min(s["end"] - s["cursor"], left)
        if 0 < left - take < 30:   # don't leave a useless 10-minute tail for the next chunk
            take = left - 30
            if take < 30:
                continue
        out.append((s, take))
        left -= take
    if left > 0:
        return None
    return sorted(out, key=lambda x: (x[0]["day"], x[0]["cursor"]))


def _load(pd: dict) -> None:
    busy = sorted(pd.pop("_busy"))
    meeting_min = sum(e - s for s, e in _merge(busy))
    starts = [_hm(b["start"]) for b in pd["blocks"]]
    ends = [_hm(b["end"]) for b in pd["blocks"]]
    gaps: list[int] = []
    if starts:
        gaps = [e - s for s, e in _subtract((min(starts), max(ends)), _merge(busy))]
    scheduled = sum(i["minutes"] for b in pd["blocks"] for i in b["items"])
    free = sum(b["free_min"] for b in pd["blocks"])
    pd["load"] = {
        "meeting_min": meeting_min,
        "meetings": len(pd["meetings"]),
        "free_min": free,
        "scheduled_min": scheduled,
        "utilization": round(scheduled / free, 2) if free else (1.0 if scheduled else 0.0),
        "longest_focus_min": max(gaps, default=0),
        "fragments": sum(1 for g in gaps if g < 30),
    }


def _merge(iv: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[list[int]] = []
    for s, e in sorted(iv):
        if out and s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [(s, e) for s, e in out]


def best_outreach_days(cfg: Config, db: DB) -> list[dict]:
    """Reply rate of your own sent emails by weekday (last 180 days), from the synced mailbox."""
    me = tuple(cfg.my_addresses) or ("",)
    since = (datetime.now() - timedelta(days=180)).isoformat()
    rows = db.q(
        "SELECT s.sent_at, EXISTS (SELECT 1 FROM messages r WHERE r.conversation_id=s.conversation_id AND r.folder='inbox' "
        "AND r.sent_at > s.sent_at AND r.automated=0 AND julianday(r.sent_at) - julianday(s.sent_at) <= 7 "
        f"AND r.from_addr NOT IN ({','.join('?' * len(me))})) replied "
        "FROM messages s WHERE s.folder='sent' AND s.sent_at >= ?", (*me, since))
    by: dict[int, list[int]] = {}
    for r in rows:
        try:
            wd = datetime.fromisoformat(r["sent_at"].replace("Z", "+00:00")).weekday()
        except ValueError:
            continue
        by.setdefault(wd, []).append(r["replied"])
    return [{"weekday": WEEKDAY_NAMES[wd], "sent": len(v), "reply_rate": round(sum(v) / len(v), 3)}
            for wd, v in sorted(by.items()) if len(v) >= 8]


def recommendations(cfg: Config, db: DB, plan: dict) -> list[dict]:
    out: list[dict] = []

    def add(severity: str, kind: str, text: str, **extra: Any) -> None:
        out.append({"severity": severity, "kind": kind, "text": text, **extra})

    max_meet = float(cfg.get("planner.max_meeting_hours_per_day", 4)) * 60
    live = [d for d in plan["days"] if not d["past"]]
    for d in live:
        L = d["load"]
        if L["meeting_min"] > max_meet:
            add("warn", "meetings", f"{d['weekday']} has {L['meeting_min'] / 60:.1f} h of meetings. Move one that isn't "
                f"urgent or make it 25 minutes; protect at least one 90-minute block.")
        elif L["meetings"] and L["longest_focus_min"] < 90:
            add("info", "fragmented", f"{d['weekday']} has no 90-minute stretch without meetings "
                f"(longest {L['longest_focus_min']} min). Put outreach batches on another day.")
    meeting_days = [d for d in live if d["meetings"]]
    if len(live) >= 4 and len(meeting_days) == len(live) and sum(len(d["meetings"]) for d in live) >= 5:
        add("info", "cluster", "You have meetings every day. Try clustering them on two days (e.g. Tue/Thu) so the other "
            "days are free for outreach and visits.")
    if plan["overflow"]:
        hrs = plan["totals"]["overflow_hours"]
        top = ", ".join(o["title"] for o in plan["overflow"][:3])
        n = len(plan["overflow"])
        add("warn", "overflow", f"{n} to-do{'s' if n != 1 else ''} ({hrs:g} h) {'don' if n != 1 else 'doesn'}'t fit this week: drop, delegate or snooze "
            f"the low-priority ones. First: {top}.")
    late = [i for d in live for b in d["blocks"] for i in b["items"] if i["late"]]
    if late:
        add("warn", "late", f"{len(late)} overdue to-dos are scheduled; the earliest are on {late[0]['date']}.")
    best = best_outreach_days(cfg, db)
    if len(best) >= 2:
        b = max(best, key=lambda x: x["reply_rate"])
        w = min(best, key=lambda x: x["reply_rate"])
        if b["reply_rate"] - w["reply_rate"] >= 0.05:
            add("tip", "best-day", f"Your emails sent on {b['weekday']} get the most replies ({b['reply_rate']:.0%} vs "
                f"{w['reply_rate']:.0%} on {w['weekday']}). Send first outreach then.", data=best)
    visits = [i for d in live for b in d["blocks"] for i in b["items"] if i["category"] == "visits" and i.get("lead_id")]
    visits += [o for o in plan["overflow"] if o["category"] == "visits"]
    if len(visits) >= 2:
        camp: dict[str, int] = {}
        for v in visits:
            row = db.one("SELECT campus FROM leads WHERE id=(SELECT lead_id FROM tasks WHERE id=?)", (v["task_id"],))
            if row and row["campus"]:
                camp[row["campus"]] = camp.get(row["campus"], 0) + 1
        for c, n in camp.items():
            if n >= 2:
                add("tip", "batch-visits", f"{n} visits are near {c}: do them in one walk (see the route on the Pipeline tab).",
                    campus=c)
    try:
        from .pipeline import auto_start, targets  # noqa: F401  (auto_start used by the dashboard button)

        behind = [t for t in targets(cfg, db) if not t["on_track"]]
        if behind:
            t = behind[0]
            add("warn", "pipeline", f"Pipeline: {t['actual_week']} of {t['target_week']:g} '{t['stage']}' this week. "
                f"Start more outreach sequences (Pipeline → Auto-start) to stay on the partner goal.")
    except Exception:
        pass
    spare = sum(d["load"]["free_min"] - d["load"]["scheduled_min"] for d in live)
    if spare >= 240 and not plan["overflow"]:
        add("tip", "capacity", f"You have about {spare / 60:.0f} h of unplanned focus time this week. That's room for "
            f"~{int(spare // 80)} more outreach sequences or a content batch.")
    return out


def apply_plan(db: DB, plan: dict) -> int:
    """Give undated and overdue to-dos the day the plan scheduled them on, so 'today' matches the plan."""
    today = datetime.now().date().isoformat()
    n = 0
    for d in plan["days"]:
        for b in d["blocks"]:
            for i in b["items"]:
                if not i["due"] or i["due"] < today:
                    n += db.x("UPDATE tasks SET due=? WHERE id=? AND status='open'", (i["date"], i["task_id"])).rowcount
    return n


def scheduled_on(plan: dict, day: str) -> dict[str, list[dict]]:
    """block title -> scheduled items for one day (used by today's plan)."""
    for d in plan["days"]:
        if d["date"] == day:
            return {b["title"]: b["items"] for b in d["blocks"]}
    return {}


def _ics_text(s: str) -> str:
    return re.sub(r"([,;\\])", r"\\\1", s or "").replace("\n", "\\n")


def to_ics(plan: dict, include_blocks: bool = False) -> str:
    """Calendar file of the planned work (meetings are already in your calendar). Times are local (floating)."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    L = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Settleezy//Setz planner//EN", "CALSCALE:GREGORIAN",
         "X-WR-CALNAME:Setz plan"]
    for d in plan["days"]:
        ymd = d["date"].replace("-", "")
        for b in d["blocks"]:
            evs = [(i["start"], i["end"], i["title"], f"setz-task-{i['task_id']}-{ymd}", f"{b['title']} · {i['category']}")
                   for i in b["items"]]
            if include_blocks and not b["items"]:
                evs.append((b["start"], b["end"], f"Focus: {b['title']}", f"setz-block-{ymd}-{b['start']}", ""))
            for s, e, title, uid, desc in evs:
                L += ["BEGIN:VEVENT", f"UID:{uid}@settleezy", f"DTSTAMP:{stamp}",
                      f"DTSTART:{ymd}T{s.replace(':', '')}00", f"DTEND:{ymd}T{e.replace(':', '')}00",
                      f"SUMMARY:{_ics_text(title)}", f"DESCRIPTION:{_ics_text(desc)}", "END:VEVENT"]
    L.append("END:VCALENDAR")
    return "\r\n".join(L) + "\r\n"


def spoken_week(plan: dict) -> str:
    t = plan["totals"]
    parts = [f"This week you have {t['meeting_hours']:g} hours of meetings and {t['focus_hours']:g} hours of focus time",
             f"I scheduled {t['tasks_scheduled']} to-dos"]
    if t["tasks_overflow"]:
        parts.append(f"{t['tasks_overflow']} don't fit, about {t['overflow_hours']:g} hours")
    busiest = max((d for d in plan["days"] if not d["past"]), key=lambda d: d["load"]["meeting_min"], default=None)
    if busiest and busiest["load"]["meeting_min"]:
        parts.append(f"{busiest['weekday']} is your busiest day")
    if plan["insights"]:
        parts.append(plan["insights"][0]["text"])
    return ". ".join(parts) + "."
