"""Autopilot: the responsibilities Setz takes off your plate, each with a mode you choose.

  auto   Setz does it and tells you (only internal, reversible work: tidy data, draft contracts and emails,
         enrich leads, add to-dos)
  ask    Setz prepares it and puts it in your approval queue (dashboard, phone push); one tap runs it
  off    not done

Anything that reaches another person (booking a meeting = an invite, creating a listing in the admin panel,
starting an outreach sequence) is "ask" by default, and emails are only ever drafts.
Modes live in [autopilot] in config.toml, e.g. `start_sequences = "auto"`.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any, Callable

from .config import Config
from .db import DB, utcnow

SCHEMA = """
CREATE TABLE IF NOT EXISTS approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT UNIQUE, rule TEXT, title TEXT, detail TEXT, payload TEXT,
    status TEXT DEFAULT 'pending',   -- pending | approved | rejected | done | failed | expired
    result TEXT, created_at TEXT, decided_at TEXT, expires_at TEXT
);
"""


def _ensure(db: DB) -> None:
    db.conn.executescript(SCHEMA)


# -- actions (what an approval runs) -----------------------------------------------------------------

def _act_start_sequences(cfg: Config, db: DB, p: dict) -> str:
    from .pipeline import start_sequence

    n = 0
    for lid in p.get("lead_ids", []):
        try:
            n += bool(start_sequence(cfg, db, int(lid)))
        except ValueError:
            pass
    return f"started {n} sequence(s)"


def _act_book(cfg: Config, db: DB, p: dict) -> str:
    from .scheduling import book

    r = book(cfg, db, int(p["request_id"]), p["start"])
    return f"booked {r.get('start', p['start'])}"


def _act_push_partner(cfg: Config, db: DB, p: dict) -> str:
    from .bridge import push_partner

    return json.dumps(push_partner(cfg, db, int(p["partner_id"])))


def _act_apply_week(cfg: Config, db: DB, p: dict) -> str:
    from .planner import apply_plan, plan_week

    return f"scheduled {apply_plan(db, plan_week(cfg, db))} to-do(s) into this week"


ACTIONS: dict[str, Callable[[Config, DB, dict], str]] = {
    "start_sequences": _act_start_sequences, "book": _act_book, "push_partner": _act_push_partner, "apply_week": _act_apply_week,
}


def request(db: DB, key: str, rule: str, title: str, detail: str, action: str, payload: dict, hours: int = 72) -> int | None:
    """Queue an approval (once per key). Returns its id if new."""
    _ensure(db)
    if db.one("SELECT 1 FROM approvals WHERE key=?", (key,)):
        return None
    cur = db.x("INSERT INTO approvals(key,rule,title,detail,payload,created_at,expires_at) VALUES(?,?,?,?,?,?,?)",
               (key, rule, title, detail, json.dumps({"action": action, **payload}), utcnow(),
                (datetime.now() + timedelta(hours=hours)).isoformat(timespec="minutes")))
    return cur.lastrowid


def pending(db: DB) -> list[dict]:
    _ensure(db)
    now = datetime.now().isoformat(timespec="minutes")
    db.x("UPDATE approvals SET status='expired' WHERE status='pending' AND expires_at < ?", (now,))
    return [dict(r) | {"payload": json.loads(r["payload"] or "{}")} for r in db.q("SELECT * FROM approvals WHERE status='pending' ORDER BY id DESC")]


def history(db: DB, limit: int = 30) -> list[dict]:
    _ensure(db)
    return [dict(r) for r in db.q("SELECT id, rule, title, status, result, decided_at FROM approvals WHERE status != 'pending' "
                                    "ORDER BY coalesce(decided_at, created_at) DESC LIMIT ?", (limit,))]


def decide(cfg: Config, db: DB, approval_id: int, approve: bool) -> dict[str, Any]:
    _ensure(db)
    row = db.one("SELECT * FROM approvals WHERE id=?", (approval_id,))
    if not row:
        raise ValueError(f"no approval {approval_id}")
    if row["status"] != "pending":
        raise ValueError(f"already {row['status']}")
    now = utcnow()
    if not approve:
        db.x("UPDATE approvals SET status='rejected', decided_at=? WHERE id=?", (now, approval_id))
        return {"status": "rejected"}
    payload = json.loads(row["payload"] or "{}")
    try:
        result = ACTIONS[payload.pop("action")](cfg, db, payload)
        db.x("UPDATE approvals SET status='done', result=?, decided_at=? WHERE id=?", (result[:500], now, approval_id))
        return {"status": "done", "result": result}
    except Exception as exc:
        db.x("UPDATE approvals SET status='failed', result=?, decided_at=? WHERE id=?", (f"{type(exc).__name__}: {exc}"[:500], now, approval_id))
        return {"status": "failed", "result": f"{type(exc).__name__}: {exc}"}


# -- rules -----------------------------------------------------------------------------------------

RULES: dict[str, dict[str, Any]] = {
    "tidy_data": {"label": "Fix data the agents flag (link partners, stop outreach to closed leads)", "default": "auto"},
    "prepare_contracts": {"label": "Draft the agreement when a partner agrees", "default": "auto"},
    "enrich_new_leads": {"label": "Read the Impressum of new leads", "default": "auto"},
    "partner_checkins": {"label": "To-do to check in with partners at risk", "default": "auto"},
    "start_sequences": {"label": "Start outreach for the best new leads", "default": "ask"},
    "book_meetings": {"label": "Book meeting requests when you're free at the proposed time", "default": "ask"},
    "push_partners": {"label": "Create draft listings in the admin panel", "default": "ask"},
    "plan_week": {"label": "Lay this week's to-dos into the plan (Monday)", "default": "ask"},
}


def mode(cfg: Config, rule: str) -> str:
    m = str(cfg.get(f"autopilot.{rule}", RULES[rule]["default"])).lower()
    return m if m in ("auto", "ask", "off") else RULES[rule]["default"]


def rules(cfg: Config) -> list[dict]:
    return [{"key": k, "label": v["label"], "mode": mode(cfg, k), "default": v["default"]} for k, v in RULES.items()]


def run(cfg: Config, db: DB) -> dict[str, Any]:
    """One autopilot round (hourly job). Every rule is isolated: one failing never stops the rest."""
    done: list[str] = []
    asked: list[str] = []
    errors: list[str] = []

    def step(rule: str, fn: Callable[[], None]) -> None:
        if mode(cfg, rule) == "off":
            return
        try:
            fn()
        except Exception as exc:
            errors.append(f"{rule}: {type(exc).__name__}: {exc}"[:200])

    def tidy() -> None:
        from .council import FIXES

        for key in ("link_partners", "stop_sequences"):
            r = FIXES[key][1](cfg, db)
            if not r.startswith(("linked 0", "removed 0")):
                done.append(r)

    def contracts() -> None:
        from .contracts import auto_prepare

        made = auto_prepare(cfg, db) if mode(cfg, "prepare_contracts") == "auto" else []
        done.extend(f"Contract drafted: {m['title']}" + (f" (missing: {', '.join(m['missing'][:4])})" if m["missing"] else "") for m in made)

    def enrich() -> None:
        n = db.one("SELECT COUNT(*) n FROM leads WHERE website != '' AND (email IS NULL OR email='') AND enriched_at IS NULL "
                   "AND status NOT IN ('partner','lost')")["n"]
        if n >= int(cfg.get("autopilot.enrich_min", 5)):
            from .leadintel import enrich_batch
            from .scraping.fetcher import Fetcher

            r = enrich_batch(db, Fetcher(db, min_delay=float(cfg.get("scraping.min_delay_seconds", 4))), limit=int(cfg.get("autopilot.enrich_batch", 15)))
            done.append(f"Enriched leads: {r}")

    def checkins() -> None:
        from .ops import add_task, partners

        for p in partners(cfg, db, "live"):
            if p["health"]["label"] == "at risk":
                if add_task(db, f"Check in with {p['name']}", priority=3, source="autopilot", partner_id=p["id"],
                            due=date.today().isoformat(), notes="; ".join(p["health"]["reasons"]),
                            dedupe_key=f"autopilot:checkin:{p['id']}:{date.today():%Y-%W}"):
                    done.append(f"To-do: check in with {p['name']}")

    def sequences() -> None:
        from .market import onboard_next
        from .pipeline import capacity, started_this_week

        room = capacity(cfg, db) - started_this_week(db)
        if room <= 0:
            return
        picks = [r for r in onboard_next(cfg, db, 30) if r["status"] == "new" and (r["email"] or r["phone"] or r["instagram"])][:room]
        if not picks:
            return
        if mode(cfg, "start_sequences") == "auto":
            done.append(_act_start_sequences(cfg, db, {"lead_ids": [r["id"] for r in picks]}))
            return
        wk = date.today().strftime("%G-%V")
        if request(db, f"seq:{wk}:{len(picks)}", "start_sequences", f"Start outreach for {len(picks)} top lead(s)",
                   "; ".join(f"{r['name']} – {', '.join(r['reasons'][:2])}" for r in picks[:6]), "start_sequences",
                   {"lead_ids": [r["id"] for r in picks]}):
            asked.append(f"Start outreach for {len(picks)} lead(s)")

    def meetings() -> None:
        from .scheduling import fmt_slot, requests

        for r in requests(cfg, db, ("open",)):
            free = r.get("proposed_free") or []
            if not free:
                continue
            start = free[0]
            mins = int(cfg.get("scheduling.meeting_minutes", 30))
            slot = {"start": start, "end": (datetime.fromisoformat(start) + timedelta(minutes=mins)).isoformat(timespec="minutes")}
            if mode(cfg, "book_meetings") == "auto" and cfg.get("autopilot.allow_auto_booking", False):
                done.append(_act_book(cfg, db, {"request_id": r["id"], "start": start}))
                continue
            if request(db, f"book:{r['id']}:{start}", "book_meetings", f"Book {r['who']} · {fmt_slot(slot)}",
                       f"{r['subject']} — they proposed it and you're free.", "book", {"request_id": r["id"], "start": start}, hours=48):
                asked.append(f"Book {r['who']}")

    def pushes() -> None:
        from .bridge import configured, pending_pushes

        if not configured(cfg):
            return
        for p in pending_pushes(db):
            if request(db, f"push:{p['id']}", "push_partners", f"Create draft listing for {p['name']} in the admin panel",
                       f"Stage: {p['stage']}. You publish it in the panel after checking.", "push_partner", {"partner_id": p["id"]}):
                asked.append(f"Admin listing for {p['name']}")

    def week() -> None:
        if date.today().weekday() != 0:
            return
        if mode(cfg, "plan_week") == "auto":
            done.append(_act_apply_week(cfg, db, {}))
        elif request(db, f"week:{date.today().isoformat()}", "plan_week", "Lay this week's to-dos into your plan",
                     "Undated and overdue to-dos get the day the planner picked.", "apply_week", {}, hours=24):
            asked.append("Week plan")

    step("tidy_data", tidy)
    step("prepare_contracts", contracts)
    step("enrich_new_leads", enrich)
    step("partner_checkins", checkins)
    step("start_sequences", sequences)
    step("book_meetings", meetings)
    step("push_partners", pushes)
    step("plan_week", week)
    if asked or done:
        from .notify import notify

        notify(cfg, db, "Setz autopilot", (f"{len(asked)} waiting for your OK: " + "; ".join(asked[:3]) if asked else "")
               + (" · " if asked and done else "") + (f"done: " + "; ".join(done[:3]) if done else ""),
               kind="job" if not asked else "info", url="/#approvals", key=f"autopilot:{utcnow()[:13]}", push=bool(asked))
    db.kv_set("last_run:autopilot", utcnow())
    return {"done": done, "asked": asked, "errors": errors, "pending": len(pending(db))}
