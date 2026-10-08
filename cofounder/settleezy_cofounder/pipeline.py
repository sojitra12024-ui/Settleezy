"""Sales pipeline engine: conversion rates, velocity, stale leads, next steps, outreach sequences, weekly targets,
partner forecast and walking routes for venue visits.

The numbers come from the stage history in `lead_events`, so they get more accurate the more you use
`sz leads --set ID STATUS` (or the dashboard buttons). Until there is enough history, sensible defaults for
local partnership sales are used and marked as assumed.
"""

from __future__ import annotations

import json
import statistics
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

from .config import Config
from .db import DB, utcnow

FUNNEL = ["contacted", "replied", "meeting", "partner"]
ORDER = ["new", "drafted", *FUNNEL]
ACTIVE = ("drafted", "contacted", "replied", "meeting")
# Typical local B2B partnership rates; replaced by your own once 5+ leads have passed a stage.
DEFAULT_RATES = {"contacted→replied": 0.25, "replied→meeting": 0.5, "meeting→partner": 0.5}
STALE_DAYS = {"drafted": 3, "contacted": 7, "replied": 2, "meeting": 7}
MIN_SAMPLE = 5

# Multi-touch outreach. Each step becomes a to-do on its day; a reply stops the rest.
DEFAULT_SEQUENCE = [
    {"day": 0, "channel": "email", "title": "Send first outreach email", "minutes": 15},
    {"day": 2, "channel": "instagram", "title": "Follow on Instagram + short DM", "minutes": 5},
    {"day": 5, "channel": "email", "title": "Follow-up email with a new angle (member numbers, nearby campus)", "minutes": 10},
    {"day": 9, "channel": "visit", "title": "Drop by in person with a flyer", "minutes": 30},
    {"day": 14, "channel": "email", "title": "Last email: close the loop politely", "minutes": 10},
]
UNIVERSITY_SEQUENCE = [
    {"day": 0, "channel": "email", "title": "Pitch the Buddy platform to the International Office", "minutes": 25},
    {"day": 4, "channel": "linkedin", "title": "Connect on LinkedIn with the International Office lead", "minutes": 10},
    {"day": 7, "channel": "email", "title": "Follow-up: share a one-pager and intake-season timing", "minutes": 15},
    {"day": 12, "channel": "call", "title": "Call the International Office", "minutes": 20},
    {"day": 20, "channel": "email", "title": "Last email: offer a 15-min intro before the next intake", "minutes": 10},
]
CHANNEL_CATEGORY = {"email": "outreach", "instagram": "outreach", "linkedin": "outreach", "call": "calls", "visit": "visits"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _days_since(iso: str | None) -> float | None:
    if not iso:
        return None
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (_now() - dt).total_seconds() / 86400


def _week_start(d: date | None = None) -> date:
    d = d or datetime.now().date()
    return d - timedelta(days=d.weekday())


# -- conversion + velocity -------------------------------------------------------

def _reached(db: DB) -> dict[str, dict[int, str]]:
    """stage -> {lead_id: first time it got there}. Reaching a later stage implies the earlier ones."""
    first: dict[int, dict[str, str]] = {}
    for e in db.q("SELECT lead_id, to_status, at FROM lead_events ORDER BY at"):
        first.setdefault(e["lead_id"], {}).setdefault(e["to_status"], e["at"])
    for l in db.q("SELECT id, status, coalesce(stage_changed_at, updated_at, created_at) at FROM leads"):
        if l["status"] in ORDER:
            first.setdefault(l["id"], {}).setdefault(l["status"], l["at"])
    out: dict[str, dict[int, str]] = {s: {} for s in FUNNEL}
    for lid, stages in first.items():
        top = max((FUNNEL.index(s) for s in stages if s in FUNNEL), default=-1)
        for i in range(top + 1):
            s = FUNNEL[i]
            # time for an implied stage = time of the next stage that is known
            at = stages.get(s) or next(stages[t] for t in FUNNEL[i + 1:] if t in stages)
            out[s][lid] = at
    return out


def conversion(db: DB) -> list[dict]:
    reached = _reached(db)
    rows = []
    for a, b in zip(FUNNEL, FUNNEL[1:]):
        key = f"{a}→{b}"
        base = reached[a]
        # only count leads that had time to convert (or already did), so fresh contacts don't drag the rate down
        mature = {lid for lid, at in base.items() if lid in reached[b] or (_days_since(at) or 0) >= STALE_DAYS.get(a, 7)}
        won = len(mature & set(reached[b]))
        observed = won / len(mature) if mature else None
        assumed = len(mature) < MIN_SAMPLE
        rate = DEFAULT_RATES[key] if assumed else observed
        days = [(_ts(reached[b][lid]) - _ts(base[lid])).total_seconds() / 86400 for lid in mature & set(reached[b])]
        rows.append({"step": key, "from": a, "to": b, "entered": len(base), "sample": len(mature), "converted": won,
                     "observed": round(observed, 3) if observed is not None else None, "rate": round(rate, 3),
                     "assumed": assumed, "median_days": round(statistics.median(days), 1) if days else None})
    return rows


def _ts(iso: str) -> datetime:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def rates(db: DB) -> dict[str, float]:
    return {r["step"]: r["rate"] for r in conversion(db)}


# -- board, stale leads, next steps ---------------------------------------------------

def board(db: DB, limit: int = 60) -> list[dict]:
    rows = [dict(r) for r in db.q(
        f"SELECT * FROM leads WHERE status IN ({','.join('?' * len(ACTIVE))}) ORDER BY "
        "CASE status WHEN 'meeting' THEN 0 WHEN 'replied' THEN 1 WHEN 'contacted' THEN 2 ELSE 3 END, score DESC LIMIT ?",
        (*ACTIVE, limit))]
    open_steps = {}
    for t in db.q("SELECT * FROM tasks WHERE status='open' AND lead_id IS NOT NULL ORDER BY due"):
        open_steps.setdefault(t["lead_id"], dict(t))
    for l in rows:
        d = _days_since(l["stage_changed_at"] or l["updated_at"])
        l["days_in_stage"] = round(d, 1) if d is not None else None
        l["stale"] = bool(d is not None and d > STALE_DAYS.get(l["status"], 7))
        l["open_task"] = open_steps.get(l["id"])
        l["has_next_step"] = bool(l["next_step"] or l["open_task"])
    _with_reply_chance(db, rows)
    return rows


def _with_reply_chance(db: DB, rows: list[dict]) -> None:
    """Add the neural lead model's reply probability (None until it has enough history to be trusted)."""
    try:
        from .brain import reply_chance

        for l in rows:
            l["reply_chance"] = reply_chance(db, l)
    except Exception:
        for l in rows:
            l["reply_chance"] = None


def stale(db: DB) -> list[dict]:
    out = [l for l in board(db, 500) if l["stale"]]
    for l in out:
        l["suggestion"] = {
            "drafted": "the draft is waiting in Outlook: send it",
            "contacted": "no reply yet: start or continue the follow-up sequence",
            "replied": "they answered: propose two call slots today",
            "meeting": "you met: send the agreement and a start date, or mark as lost",
        }.get(l["status"], "decide the next step")
    return sorted(out, key=lambda l: -(l["days_in_stage"] or 0))


def hygiene(db: DB) -> list[dict]:
    """Active leads with no next step and no open to-do: deals die here silently."""
    return [l for l in board(db, 500) if not l["has_next_step"]]


def set_next_step(db: DB, lead_id: int, text: str, due: str | None = None, create_task: bool = True) -> dict:
    from .ops import add_task, parse_due

    lead = db.one("SELECT * FROM leads WHERE id=?", (lead_id,))
    if not lead:
        raise ValueError(f"no lead {lead_id}")
    if due is None:
        due, text = parse_due(text)
    due = due or datetime.now().date().isoformat()
    db.x("UPDATE leads SET next_step=?, next_step_due=?, updated_at=? WHERE id=?", (text, due, utcnow(), lead_id))
    if create_task:
        add_task(db, f"{lead['name']}: {text}", due=due, priority=2, source="pipeline", lead_id=lead_id,
                 dedupe_key=f"next:{lead_id}:{due}:{text[:40].lower()}")
    return dict(db.one("SELECT * FROM leads WHERE id=?", (lead_id,)))


# -- outreach sequences ------------------------------------------------------------------

def sequence_for(cfg: Config, kind: str) -> list[dict]:
    if kind == "university":
        return cfg.get("pipeline.university_sequence") or UNIVERSITY_SEQUENCE
    return cfg.get("pipeline.sequence") or DEFAULT_SEQUENCE


def start_sequence(cfg: Config, db: DB, lead_id: int, start: date | None = None) -> int:
    from .ops import add_task

    lead = db.one("SELECT * FROM leads WHERE id=?", (lead_id,))
    if not lead:
        raise ValueError(f"no lead {lead_id}")
    if lead["status"] in {"replied", "meeting", "partner", "lost"}:
        raise ValueError(f"lead is already '{lead['status']}': no cold sequence needed")
    if db.one("SELECT 1 FROM partners WHERE lead_id=? OR lower(name)=lower(?)", (lead_id, lead["name"])):
        raise ValueError(f"{lead['name']} is already a partner: no cold sequence")
    start = start or datetime.now().date()
    while start.weekday() >= 5:      # sequences run on working days
        start += timedelta(days=1)
    n = 0
    for i, step in enumerate(sequence_for(cfg, lead["kind"])):
        ch = step.get("channel", "email")
        title = step["title"]
        if ch == "instagram" and not lead["instagram"]:
            title = "Find their Instagram, follow + short DM"
        if ch == "email" and i == 0 and not lead["email"]:
            title = "Find the contact email (Impressum/website), then " + title[0].lower() + title[1:]
        if ch == "visit" and lead["address"]:
            title += f" ({lead['address']})"
        due = start + timedelta(days=int(step.get("day", 0)))
        while due.weekday() >= 5:
            due += timedelta(days=1)
        n += bool(add_task(db, f"{lead['name']}: {title}", due=due.isoformat(), priority=3 if i == 0 else 2, source="sequence",
                           lead_id=lead_id, dedupe_key=f"seq:{lead_id}:{i}", category=CHANNEL_CATEGORY.get(ch, "outreach"),
                           est_minutes=int(step.get("minutes", 15)),
                           notes=json.dumps({"step": i, "channel": ch, "of": len(sequence_for(cfg, lead["kind"]))})))
    db.x("UPDATE leads SET sequence_started=?, next_step=?, next_step_due=?, updated_at=? WHERE id=?",
         (utcnow(), "Outreach sequence", start.isoformat(), utcnow(), lead_id))
    return n


def stop_sequence(db: DB, lead_id: int) -> int:
    """They replied (or it's over): remove the remaining sequence to-dos."""
    cur = db.x("DELETE FROM tasks WHERE lead_id=? AND source='sequence' AND status='open'", (lead_id,))
    return cur.rowcount


def on_task_done(db: DB, task_id: int) -> None:
    t = db.one("SELECT * FROM tasks WHERE id=?", (task_id,))
    if not t or not t["lead_id"]:
        return
    lead = db.one("SELECT status FROM leads WHERE id=?", (t["lead_id"],))
    if not lead:
        return
    if t["source"] == "sequence":
        db.x("UPDATE leads SET last_contact_at=?, updated_at=? WHERE id=?", (utcnow(), utcnow(), t["lead_id"]))
        if lead["status"] in {"new", "drafted"}:
            from .leads import set_status

            set_status(db, t["lead_id"], "contacted", "")
        nxt = db.one("SELECT title, due FROM tasks WHERE lead_id=? AND source='sequence' AND status='open' ORDER BY due", (t["lead_id"],))
        db.x("UPDATE leads SET next_step=?, next_step_due=? WHERE id=?",
             ((nxt["title"].split(": ", 1)[-1], nxt["due"]) if nxt else (None, None)) + (t["lead_id"],))
    elif t["source"] == "pipeline":
        db.x("UPDATE leads SET next_step=NULL, next_step_due=NULL WHERE id=? AND next_step IS NOT NULL", (t["lead_id"],))


def sequences(db: DB) -> list[dict]:
    """Running sequences with their progress, plus the steps due today or overdue."""
    today = datetime.now().date().isoformat()
    out = []
    for l in db.q("SELECT * FROM leads WHERE sequence_started IS NOT NULL ORDER BY sequence_started DESC"):
        steps = db.q("SELECT * FROM tasks WHERE lead_id=? AND source='sequence' ORDER BY due", (l["id"],))
        if not steps:
            continue
        open_ = [s for s in steps if s["status"] == "open"]
        out.append({"lead_id": l["id"], "name": l["name"], "status": l["status"], "done": len(steps) - len(open_),
                    "total": len(steps), "next": dict(open_[0]) if open_ else None,
                    "due_now": bool(open_ and open_[0]["due"] and open_[0]["due"] <= today)})
    return out


def capacity(cfg: Config, db: DB) -> int:
    """New sequences per week: your setting, else what the partner goal needs (capped so it stays doable)."""
    set_ = cfg.get("pipeline.new_sequences_per_week")
    if set_:
        return int(set_)
    need = next(t["target_week"] for t in targets(cfg, db) if t["stage"] == "contacted")
    return max(5, min(25, round(need)))


def started_this_week(db: DB) -> int:
    return db.one("SELECT COUNT(*) n FROM leads WHERE sequence_started >= ?", (_week_start().isoformat(),))["n"]


def auto_start(cfg: Config, db: DB, limit: int | None = None) -> list[dict]:
    """Start sequences for the best uncontacted leads, up to the weekly capacity."""
    cap = capacity(cfg, db)
    room = max(0, cap - started_this_week(db))
    if limit is not None:
        room = min(room, limit)
    from .ops import link_partners_to_leads

    link_partners_to_leads(db)
    pool = [dict(r) for r in db.q("SELECT * FROM leads WHERE status IN ('new','drafted') AND sequence_started IS NULL "
                                  "AND (email != '' OR instagram != '' OR website != '' OR address != '') "
                                  "AND id NOT IN (SELECT lead_id FROM partners WHERE lead_id IS NOT NULL) "
                                  "AND lower(name) NOT IN (SELECT lower(name) FROM partners) ORDER BY score DESC LIMIT ?",
                                  (max(room * 5, 1),))]
    _with_reply_chance(db, pool)
    # with a trusted model, rank by expected value: score x how likely they are to answer
    pool.sort(key=lambda l: -(l["score"] * (0.5 + l["reply_chance"]) if l["reply_chance"] is not None else l["score"]))
    picked = pool[:room]
    out = []
    for l in picked:
        out.append({"lead_id": l["id"], "name": l["name"], "steps": start_sequence(cfg, db, l["id"])})
    return out


# -- targets + forecast ------------------------------------------------------------------------

def targets(cfg: Config, db: DB) -> list[dict]:
    """Work back from the monthly partner goal to how many contacts, replies and meetings you need per week."""
    goal_month = float(cfg.get("pipeline.partner_goal_per_month", 8))
    r = rates(db)
    need = {"partner": goal_month * 12 / 52}
    need["meeting"] = need["partner"] / max(r["meeting→partner"], 0.01)
    need["replied"] = need["meeting"] / max(r["replied→meeting"], 0.01)
    need["contacted"] = need["replied"] / max(r["contacted→replied"], 0.01)
    ws = _week_start().isoformat()
    actual = {s: db.one("SELECT COUNT(DISTINCT lead_id) n FROM lead_events WHERE to_status=? AND at >= ?", (s, ws))["n"] for s in FUNNEL}
    weekday = datetime.now().weekday()
    pace = min(1.0, (weekday + 1) / 5)          # how much of the working week has passed
    out = []
    for s in FUNNEL:
        target = round(need[s], 1)
        expected_by_now = target * pace
        out.append({"stage": s, "target_week": target, "actual_week": actual[s],
                    "on_track": actual[s] >= expected_by_now - 0.5, "gap": max(0, round(target - actual[s], 1))})
    return out


def forecast(cfg: Config, db: DB) -> dict:
    """Expected partners from today's pipeline: each active lead times its chance of closing."""
    r = rates(db)
    p_from = {
        "meeting": r["meeting→partner"],
        "replied": r["replied→meeting"] * r["meeting→partner"],
        "contacted": r["contacted→replied"] * r["replied→meeting"] * r["meeting→partner"],
        "drafted": 0.8 * r["contacted→replied"] * r["replied→meeting"] * r["meeting→partner"],
    }
    counts = {s: db.one("SELECT COUNT(*) n FROM leads WHERE status=?", (s,))["n"] for s in p_from}
    expected = sum(counts[s] * p_from[s] for s in p_from)
    month_start = datetime.now().date().replace(day=1).isoformat()
    won = db.one("SELECT COUNT(DISTINCT lead_id) n FROM lead_events WHERE to_status='partner' AND at >= ?", (month_start,))["n"]
    goal = float(cfg.get("pipeline.partner_goal_per_month", 8))
    return {"expected_from_pipeline": round(expected, 1), "won_this_month": won, "goal_month": goal,
            "by_stage": {s: {"leads": counts[s], "p_close": round(p_from[s], 3)} for s in p_from},
            "gap": round(max(0.0, goal - won - expected), 1)}


# -- visit routes --------------------------------------------------------------------------------

def routes(cfg: Config, db: DB, campus: str | None = None, stops: int = 8) -> list[dict]:
    """Walking routes per campus through venues worth visiting (uncontacted or no reply), nearest-neighbour order."""
    from .leadgen import campuses, haversine_m

    out = []
    for c in campuses(cfg):
        if campus and campus.lower() not in c["name"].lower():
            continue
        rows = [dict(r) for r in db.q(
            "SELECT * FROM leads WHERE campus=? AND lat IS NOT NULL AND status IN ('new','drafted','contacted') "
            "ORDER BY score DESC LIMIT ?", (c["name"], stops * 3))]
        if not rows:
            continue
        rows = rows[:stops]
        path, here = [], (c["lat"], c["lon"])
        while rows:
            nxt = min(rows, key=lambda l: haversine_m(here[0], here[1], l["lat"], l["lon"]))
            rows.remove(nxt)
            path.append(nxt)
            here = (nxt["lat"], nxt["lon"])
        dist = sum(haversine_m(a[0], a[1], b[0], b[1]) for a, b in zip(
            [(c["lat"], c["lon"])] + [(p["lat"], p["lon"]) for p in path[:-1]], [(p["lat"], p["lon"]) for p in path]))
        pts = [f"{p['lat']:.6f},{p['lon']:.6f}" for p in path]
        url = (f"https://www.google.com/maps/dir/?api=1&travelmode=walking&origin={c['lat']},{c['lon']}"
               f"&destination={pts[-1]}" + (f"&waypoints={quote('|'.join(pts[:-1]))}" if len(pts) > 1 else ""))
        out.append({"campus": c["name"], "stops": [{"lead_id": p["id"], "name": p["name"], "category": p["category"],
                                                     "address": p["address"], "status": p["status"], "lat": p["lat"],
                                                     "lon": p["lon"]} for p in path],
                    "walk_km": round(dist / 1000, 2), "walk_min": round(dist / 80), "maps_url": url})
    return sorted(out, key=lambda r: -len(r["stops"]))


# -- everything for the dashboard -------------------------------------------------------------------

def summary(cfg: Config, db: DB) -> dict[str, Any]:
    from .leadgen import coverage

    counts = {s: db.one("SELECT COUNT(*) n FROM leads WHERE status=?", (s,))["n"] for s in [*ORDER, "lost"]}
    b = board(db)
    return {
        "counts": counts,
        "conversion": conversion(db),
        "targets": targets(cfg, db),
        "forecast": forecast(cfg, db),
        "board": b,
        "stale": stale(db)[:20],
        "hygiene": [{"id": l["id"], "name": l["name"], "status": l["status"]} for l in b if not l["has_next_step"]][:20],
        "sequences": sequences(db)[:40],
        "sequence_capacity": {"started_this_week": started_this_week(db),
                              "per_week": capacity(cfg, db)},
        "coverage": coverage(cfg, db),
        "map": [{"id": r["id"], "name": r["name"], "lat": r["lat"], "lon": r["lon"], "status": r["status"],
                 "score": r["score"], "category": r["category"], "campus": r["campus"]}
                for r in db.q("SELECT * FROM leads WHERE lat IS NOT NULL ORDER BY score DESC LIMIT 600")],
        "routes": routes(cfg, db)[:6],
    }
