"""Better tracking: daily metric snapshots, AI-draft outcomes, manual KPIs and goal progress.

Everything lands in the `metrics` table (day, source, key, value), so the dashboard can chart any of it.
"""

from __future__ import annotations

import statistics
from datetime import datetime, timedelta, timezone

from .config import Config
from .db import DB, utcnow


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def track_drafts(cfg: Config, db: DB) -> dict[str, int]:
    """A draft counts as *sent* when a message from you appears in its thread after it was created,
    and as *replied* when someone else writes in that thread after that."""
    me = cfg.my_addresses
    sent = replied = 0
    for d in db.q("SELECT * FROM drafts WHERE conversation_id IS NOT NULL AND conversation_id != '' AND replied_at IS NULL"):
        thread = db.conversation(d["conversation_id"])
        if not d["sent_at"]:
            mine = [m for m in thread if m["folder"] == "sent" and m["sent_at"] and _dt(m["sent_at"]) >= _dt(d["created_at"])]
            if mine:
                db.x("UPDATE drafts SET sent_at=? WHERE id=?", (mine[0]["sent_at"], d["id"]))
                sent += 1
                if d["lead_id"]:
                    _advance_lead(db, d["lead_id"], "contacted", {"new", "drafted"})
                d = db.one("SELECT * FROM drafts WHERE id=?", (d["id"],))
        if d["sent_at"]:
            theirs = [m for m in thread if m["folder"] == "inbox" and not m["automated"] and m["from_addr"] not in me
                      and _dt(m["sent_at"]) > _dt(d["sent_at"])]
            if theirs:
                db.x("UPDATE drafts SET replied_at=? WHERE id=?", (theirs[0]["sent_at"], d["id"]))
                replied += 1
                if d["lead_id"]:
                    _advance_lead(db, d["lead_id"], "replied", {"new", "drafted", "contacted"})
    return {"newly_sent": sent, "newly_replied": replied}


def _advance_lead(db: DB, lead_id: int, status: str, from_statuses: set[str]) -> None:
    from .leads import set_status

    row = db.one("SELECT status FROM leads WHERE id=?", (lead_id,))
    if row and row["status"] in from_statuses:
        set_status(db, lead_id, status)


def draft_funnel(db: DB, days: int = 30) -> dict[str, dict[str, int]]:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    out: dict[str, dict[str, int]] = {}
    for r in db.q(
        "SELECT kind, COUNT(*) drafted, COUNT(sent_at) sent, COUNT(replied_at) replied FROM drafts WHERE created_at >= ? GROUP BY kind",
        (since,),
    ):
        out[r["kind"]] = {"drafted": r["drafted"], "sent": r["sent"], "replied": r["replied"]}
    return out


def response_hours(db: DB, me: set[str], days: int = 7) -> float | None:
    """Median hours between someone writing to you and your reply, over the last N days."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = db.q("SELECT conversation_id, folder, from_addr, sent_at, automated FROM messages WHERE sent_at >= ? ORDER BY conversation_id, sent_at", (since,))
    hours, prev = [], None
    for r in rows:
        if prev and prev["conversation_id"] == r["conversation_id"] and prev["folder"] == "inbox" and not prev["automated"] \
                and prev["from_addr"] not in me and r["folder"] == "sent":
            hours.append((_dt(r["sent_at"]) - _dt(prev["sent_at"])).total_seconds() / 3600)
        prev = r
    return round(statistics.median(hours), 1) if hours else None


def snapshot(cfg: Config, db: DB) -> dict[str, float]:
    """Record today's operating metrics (safe to run many times a day; last value wins)."""
    from .leads import pipeline
    from .triage import followups_due, needs_reply

    values: dict[str, float] = {
        "replies_owed": len(needs_reply(cfg, db, None)),
        "followups_due": len(followups_due(cfg, db)),
    }
    rh = response_hours(db, cfg.my_addresses)
    if rh is not None:
        values["response_hours_median_7d"] = rh
    funnel = draft_funnel(db, 30)
    drafted = sum(v["drafted"] for v in funnel.values())
    sent = sum(v["sent"] for v in funnel.values())
    if drafted:
        values["ai_drafts_sent_pct_30d"] = round(100 * sent / drafted, 1)
    out = funnel.get("outreach", {})
    if out.get("sent"):
        values["outreach_reply_pct_30d"] = round(100 * out["replied"] / out["sent"], 1)
    pipe = pipeline(db)
    for kind, statuses in pipe.items():
        values[f"partners_{kind}"] = statuses.get("partner", 0)
        values[f"leads_open_{kind}"] = sum(n for s, n in statuses.items() if s not in ("partner", "lost"))
    since7 = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    values["new_listings_7d"] = db.one("SELECT COUNT(*) n FROM listings WHERE first_seen != 'baseline' AND first_seen >= ?", (since7,))["n"]
    from .ops import partner_stats

    ps = partner_stats(cfg, db)
    values.update({"partners_live": ps["live"], "partners_onboarding": ps["onboarding"],
                   "partners_onboarded_month": ps["onboarded_this_month"], "partners_at_risk": ps["at_risk"]})
    for kind in ("venue", "brand", "university", "housing", "service"):
        values[f"partners_live_{kind}"] = ps["live_by_kind"].get(kind, 0)
    for k, v in values.items():
        db.metric("ops", k, v)
    return values


def record_kpi(db: DB, key: str, value: float, day: str | None = None) -> None:
    """Numbers only you have (app members, trials, paying, workshop sign-ups...)."""
    db.metric("manual", key, value, day)


def latest(db: DB, source_key: str) -> float | None:
    source, _, key = source_key.partition(".")
    row = db.one("SELECT value FROM metrics WHERE source=? AND key=? ORDER BY day DESC LIMIT 1", (source, key))
    return row["value"] if row else None


def goals(cfg: Config, db: DB) -> list[dict]:
    out = []
    for t in cfg.get("growth.targets", []):
        current = latest(db, t["metric"])
        target = float(t["target"])
        out.append({
            "label": t["label"],
            "metric": t["metric"],
            "current": current,
            "target": target,
            "pct": round(min(100.0, 100 * (current or 0) / target), 1) if target else 0,
            "deadline": t.get("deadline", ""),
        })
    return out
