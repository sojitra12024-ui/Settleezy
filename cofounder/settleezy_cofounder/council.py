"""The council: every agent has a brain of its own, they work at the same time, and they check each other's work.

Own brain (per agent)
  agent_state     what each agent has seen before: how many runs in a row a finding has persisted (a problem
                  that keeps coming back is escalated), when findings were resolved and how long that took,
                  and its own notes. Important findings also go into the shared memory (source "agent:<key>"),
                  so `recall_for(agent, question)` answers from that agent's experience.
Working together
  run_parallel    all agents analyse at the same time, each on its own database connection.
  blackboard      agent_messages: suggestions, bug reports and questions one agent posts to another, with a
                  safe automatic fix where one exists. Messages are deduplicated and closed when the problem
                  disappears.
Peer review
  cross_check     logical checks each agent runs on another agent's area: data that contradicts itself
                  (a partner that is still a "new lead", outreach running for a lost lead, a live partner
                  without a contract), stalled work, an agent or job that crashed (posted as a bug report to its
                  owner with the error) and recommendations that disagree between agents.
  deliberate      optional (Claude API): Setz reads all reports and messages and points out contradictions
                  the rules didn't catch.
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from .config import Config
from .db import DB, utcnow

SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT UNIQUE,
    at TEXT, updated_at TEXT,
    from_agent TEXT, to_agent TEXT,
    kind TEXT,              -- bug | suggestion | question | handoff
    severity TEXT,          -- info | warn | alert
    title TEXT, detail TEXT,
    fix TEXT,               -- name of a safe automatic fix (FIXES), or ''
    count INTEGER DEFAULT 1,
    status TEXT DEFAULT 'open'   -- open | fixed | resolved | dismissed
);
CREATE TABLE IF NOT EXISTS agent_state (
    agent TEXT, key TEXT, value TEXT, updated_at TEXT,
    PRIMARY KEY (agent, key)
);
"""


def _ensure(db: DB) -> None:
    db.conn.executescript(SCHEMA)


def _ago_days(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).total_seconds() / 86400


# -- each agent's own brain ---------------------------------------------------------------------

def state_get(db: DB, agent: str, key: str, default: Any = None) -> Any:
    _ensure(db)
    r = db.one("SELECT value FROM agent_state WHERE agent=? AND key=?", (agent, key))
    return json.loads(r["value"]) if r else default


def state_set(db: DB, agent: str, key: str, value: Any) -> None:
    _ensure(db)
    db.x("INSERT INTO agent_state(agent,key,value,updated_at) VALUES(?,?,?,?) ON CONFLICT(agent,key) DO UPDATE SET "
         "value=excluded.value, updated_at=excluded.updated_at", (agent, key, json.dumps(value, default=str), utcnow()))


def observe(cfg: Config, db: DB, agent: str, findings: list[dict]) -> list[dict]:
    """Update the agent's memory of its findings. Returns the findings with `streak` (runs in a row) and escalation."""
    seen: dict[str, dict] = state_get(db, agent, "streaks", {})
    learned: dict[str, Any] = state_get(db, agent, "learned", {"resolved": 0, "avg_days_to_resolve": None})
    now = utcnow()
    current = {f["key"] for f in findings}
    for key in [k for k in seen if k not in current]:      # resolved since last run
        d = _ago_days(seen[key]["first"]) or 0
        n = learned["resolved"]
        avg = learned["avg_days_to_resolve"]
        learned["avg_days_to_resolve"] = round(d if avg is None else (avg * n + d) / (n + 1), 2)
        learned["resolved"] = n + 1
        seen.pop(key)
    out = []
    for f in findings:
        s = seen.setdefault(f["key"], {"first": now, "runs": 0})
        s["runs"] += 1
        s["last"] = now
        f = {**f, "streak": s["runs"], "since": s["first"]}
        if s["runs"] >= int(cfg.get("council.escalate_after_runs", 6)) and f.get("severity") == "warn":
            f["severity"], f["escalated"] = "alert", True     # a warning that never goes away becomes an alert
            f["detail"] = (f.get("detail") or "") + f" (unresolved for {s['runs']} runs)"
        out.append(f)
    state_set(db, agent, "streaks", seen)
    state_set(db, agent, "learned", learned)
    # remember what matters, in the agent's own voice
    if cfg.get("council.remember_findings", True):
        from .brain import remember

        for f in out:
            if f.get("severity") == "alert" and f["streak"] == 1:
                try:
                    remember(cfg, db, f"{agent.capitalize()} noticed on {date.today():%d %b}: {f['title']}. {f.get('detail', '')}"[:600],
                             kind="insight", subject=f["title"][:60], source=f"agent:{agent}", importance=0.6,
                             key=f"agent:{agent}:{f['key']}")
                except Exception:
                    pass
    return out


def recall_for(cfg: Config, db: DB, agent: str, question: str, k: int = 5) -> list[dict]:
    """What this agent remembers about a question (its own insights first, then shared knowledge)."""
    from .brain import recall

    mems = recall(cfg, db, question, k=30, touch=False)
    own = [m for m in mems if m.get("source") == f"agent:{agent}"]
    return (own + [m for m in mems if m not in own])[:k]


def brain_summary(db: DB, agent: str) -> dict[str, Any]:
    streaks = state_get(db, agent, "streaks", {})
    learned = state_get(db, agent, "learned", {})
    _ensure(db)
    inbox = db.one("SELECT COUNT(*) n FROM agent_messages WHERE to_agent=? AND status='open'", (agent,))["n"]
    sent = db.one("SELECT COUNT(*) n FROM agent_messages WHERE from_agent=?", (agent,))["n"]
    try:
        mem = db.one("SELECT COUNT(*) n FROM memories WHERE source=?", (f"agent:{agent}",))["n"]
    except Exception:
        mem = 0
    longest = max(streaks.items(), key=lambda kv: kv[1]["runs"], default=(None, None))
    return {"agent": agent, "tracking": len(streaks), "memories": mem, "inbox": inbox, "sent": sent,
            "resolved": learned.get("resolved", 0), "avg_days_to_resolve": learned.get("avg_days_to_resolve"),
            "longest_open": {"key": longest[0], "runs": longest[1]["runs"]} if longest[0] else None}


# -- the blackboard -------------------------------------------------------------------------------

@dataclass
class Msg:
    key: str
    from_agent: str
    to_agent: str
    title: str
    detail: str = ""
    kind: str = "suggestion"
    severity: str = "warn"
    fix: str = ""
    meta: dict = field(default_factory=dict)


def post(db: DB, m: Msg) -> bool:
    """Post or refresh a message. Returns True if it is new (or re-opened)."""
    _ensure(db)
    now = utcnow()
    row = db.one("SELECT id, status FROM agent_messages WHERE key=?", (m.key,))
    if row:
        reopen = row["status"] in ("resolved", "fixed")
        db.x("UPDATE agent_messages SET title=?, detail=?, severity=?, fix=?, updated_at=?, count=count+1, "
             "status=CASE WHEN status IN ('resolved','fixed') THEN 'open' ELSE status END WHERE id=?",
             (m.title, m.detail, m.severity, m.fix, now, row["id"]))
        return reopen
    db.x("INSERT INTO agent_messages(key,at,updated_at,from_agent,to_agent,kind,severity,title,detail,fix) VALUES(?,?,?,?,?,?,?,?,?,?)",
         (m.key, now, now, m.from_agent, m.to_agent, m.kind, m.severity, m.title, m.detail, m.fix))
    return True


def messages(db: DB, status: str | None = "open", agent: str | None = None, limit: int = 100) -> list[dict]:
    _ensure(db)
    sql, params = "SELECT * FROM agent_messages WHERE 1=1", []
    if status:
        sql += " AND status=?"
        params.append(status)
    if agent:
        sql += " AND (to_agent=? OR from_agent=?)"
        params += [agent, agent]
    sql += " ORDER BY CASE severity WHEN 'alert' THEN 0 WHEN 'warn' THEN 1 ELSE 2 END, updated_at DESC LIMIT ?"
    return [dict(r) | {"fix_label": FIXES[r["fix"]][0] if r["fix"] in FIXES else ""} for r in db.q(sql, (*params, limit))]


def set_message(db: DB, mid: int, status: str) -> None:
    if status not in ("open", "resolved", "dismissed", "fixed"):
        raise ValueError("status must be open, resolved, dismissed or fixed")
    _ensure(db)
    db.x("UPDATE agent_messages SET status=?, updated_at=? WHERE id=?", (status, utcnow(), mid))


# -- safe automatic fixes (internal data only: nothing is sent, booked or published) ---------------

def _fix_link_partners(cfg: Config, db: DB) -> str:
    from .ops import link_partners_to_leads

    n = link_partners_to_leads(db)
    for r in db.q("SELECT p.lead_id FROM partners p JOIN leads l ON l.id=p.lead_id WHERE l.status != 'partner'"):
        from .leads import set_status

        set_status(db, r["lead_id"], "partner", "")
        n += 1
    return f"linked {n} partner(s) to their leads"


def _fix_stop_sequences(cfg: Config, db: DB) -> str:
    from .pipeline import stop_sequence

    n = sum(stop_sequence(db, r["id"]) for r in db.q(
        "SELECT DISTINCT l.id FROM leads l JOIN tasks t ON t.lead_id=l.id WHERE t.source='sequence' AND t.status='open' "
        "AND l.status IN ('partner','lost')"))
    return f"removed {n} outreach step(s) for closed leads"


def _fix_prepare_contracts(cfg: Config, db: DB) -> str:
    from .contracts import auto_prepare

    made = auto_prepare(cfg, db)
    return f"drafted {len(made)} contract(s)" + (f": {', '.join(m['title'] for m in made[:3])}" if made else "")


def _fix_enrich(cfg: Config, db: DB) -> str:
    from .jobs import run_job

    r = run_job(cfg, "enrich")
    return f"enrichment ran: {r}"[:200]


FIXES: dict[str, tuple[str, Callable[[Config, DB], str]]] = {
    "link_partners": ("Link partners to their leads", _fix_link_partners),
    "stop_sequences": ("Stop outreach for closed leads", _fix_stop_sequences),
    "prepare_contracts": ("Draft the missing contracts", _fix_prepare_contracts),
    "enrich": ("Read Impressum for missing contacts", _fix_enrich),
}


def apply_fix(cfg: Config, db: DB, mid: int) -> str:
    _ensure(db)
    m = db.one("SELECT * FROM agent_messages WHERE id=?", (mid,))
    if not m or m["fix"] not in FIXES:
        raise ValueError("this message has no automatic fix")
    result = FIXES[m["fix"]][1](cfg, db)
    db.x("UPDATE agent_messages SET status='fixed', detail=detail || ? , updated_at=? WHERE id=?", (f"\nFixed: {result}", utcnow(), mid))
    return result


# -- peer review: logical checks agents run on each other ------------------------------------------

def _check_partner_lead_mismatch(cfg: Config, db: DB) -> list[Msg]:
    out = []
    bad = db.q("SELECT p.name, l.status FROM partners p JOIN leads l ON l.id=p.lead_id WHERE l.status != 'partner'")
    if bad:
        out.append(Msg("partner-lead-status", "atlas", "hunter", f"{len(bad)} partner(s) still look like open leads",
                       "; ".join(f"{b['name']} is '{b['status']}'" for b in bad[:5]) + ". They could get cold outreach.",
                       "bug", "alert", "link_partners"))
    try:
        from .leads import _norm_name

        lead_keys = {}
        for l in db.q("SELECT id, name FROM leads WHERE status != 'partner'"):
            lead_keys.setdefault(_norm_name(l["name"]).casefold(), l["id"])
        orphans = [p["name"] for p in db.q("SELECT name FROM partners WHERE lead_id IS NULL")
                   if _norm_name(p["name"]).casefold() in lead_keys]
    except Exception:
        orphans = []
    if orphans:
        out.append(Msg("partner-orphans", "atlas", "hunter", f"{len(orphans)} partner(s) also exist as unlinked leads",
                       ", ".join(orphans[:5]), "bug", "warn", "link_partners"))
    return out


def _check_sequences_on_closed(cfg: Config, db: DB) -> list[Msg]:
    n = db.one("SELECT COUNT(DISTINCT l.id) n FROM leads l JOIN tasks t ON t.lead_id=l.id WHERE t.source='sequence' "
               "AND t.status='open' AND l.status IN ('partner','lost')")["n"]
    return [Msg("seq-closed", "sentinel", "hunter", f"Outreach is still scheduled for {n} closed lead(s)",
                "Partners or lost leads still have open sequence steps.", "bug", "alert", "stop_sequences")] if n else []


def _check_duplicates(cfg: Config, db: DB) -> list[Msg]:
    from urllib.parse import urlparse

    by: dict[str, list[str]] = {}
    for l in db.q("SELECT id, name, website, phone FROM leads WHERE status != 'lost'"):
        host = urlparse(l["website"] if "//" in (l["website"] or "") else "//" + (l["website"] or "")).hostname or ""
        host = host.removeprefix("www.")
        if host and not re.search(r"instagram|facebook|linktr|google|tiktok|wa\.me|maps|yelp|tripadvisor|lieferando|wolt", host):
            by.setdefault("w:" + host, []).append(l["name"])
        ph = re.sub(r"\D", "", l["phone"] or "")[-9:]
        if len(ph) >= 7:
            by.setdefault("p:" + ph, []).append(l["name"])
    dups = [v for v in by.values() if len(set(v)) > 1]
    if not dups:
        return []
    return [Msg("dup-leads", "sentinel", "hunter", f"{len(dups)} venue(s) appear as more than one lead",
                "; ".join(" = ".join(sorted(set(v))[:3]) for v in dups[:5]) + ". Merge them so nobody is pitched twice.",
                "bug", "warn")]


def _check_bad_emails(cfg: Config, db: DB) -> list[Msg]:
    bad = [r["name"] for r in db.q("SELECT name, email FROM leads WHERE email != '' AND email IS NOT NULL")
           if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-z]{2,}", (r["email"] or "").strip(), re.I)]
    return [Msg("bad-emails", "hermes", "hunter", f"{len(bad)} lead(s) have an invalid email address", ", ".join(bad[:6]),
                "bug", "warn")] if bad else []


def _check_stalled_sequences(cfg: Config, db: DB) -> list[Msg]:
    cut = (date.today() - timedelta(days=3)).isoformat()
    n = db.one("SELECT COUNT(*) n FROM tasks WHERE source='sequence' AND status='open' AND due < ?", (cut,))["n"]
    return [Msg("seq-stalled", "chrono", "hunter", f"{n} outreach step(s) are more than 3 days late",
                "Either block an outreach hour in the week plan or reduce new sequences per week.", "suggestion",
                "warn" if n < 10 else "alert")] if n else []


def _check_contracts(cfg: Config, db: DB) -> list[Msg]:
    from .contracts import _ensure as ensure_contracts

    ensure_contracts(db)
    out = []
    agreed = db.q("SELECT name FROM partners WHERE stage='agreed' AND status='onboarding' AND id NOT IN "
                  "(SELECT partner_id FROM contracts WHERE partner_id IS NOT NULL AND status != 'cancelled')")
    if agreed:
        out.append(Msg("contracts-missing", "lex", "atlas", f"{len(agreed)} agreed partner(s) have no contract draft",
                       ", ".join(a["name"] for a in agreed[:5]), "handoff", "warn", "prepare_contracts"))
    live_unsigned = db.q("SELECT name FROM partners WHERE status='live' AND (signed_at IS NULL OR signed_at='') AND id NOT IN "
                         "(SELECT partner_id FROM contracts WHERE status='signed' AND partner_id IS NOT NULL)")
    if live_unsigned:
        out.append(Msg("live-unsigned", "lex", "atlas", f"{len(live_unsigned)} live partner(s) without a signed agreement",
                       ", ".join(a["name"] for a in live_unsigned[:5]) + ". Get it signed so the offer is binding.", "suggestion", "warn"))
    soon = (date.today() + timedelta(days=30)).isoformat()
    ren = db.q("SELECT name, renewal_date FROM partners WHERE renewal_date != '' AND renewal_date IS NOT NULL AND renewal_date <= ? "
               "AND status IN ('live','onboarding')", (soon,))
    if ren:
        out.append(Msg("renewals", "lex", "atlas", f"{len(ren)} agreement(s) renew within 30 days",
                       ", ".join(f"{r['name']} ({r['renewal_date']})" for r in ren[:5]), "suggestion", "warn"))
    return out


def _check_meeting_requests(cfg: Config, db: DB) -> list[Msg]:
    try:
        from .scheduling import _ensure as ensure_req

        ensure_req(db)
        n = db.one("SELECT COUNT(*) n FROM meeting_requests WHERE status='open' AND detected_at < datetime('now','-2 day')")["n"]
    except Exception:
        return []
    return [Msg("mreq-waiting", "hermes", "chrono", f"{n} meeting request(s) unanswered for 2+ days",
                "Book a suggested slot or send the reply draft.", "suggestion", "alert")] if n else []


def _check_market_vs_pipeline(cfg: Config, db: DB) -> list[Msg]:
    out = []
    last = db.kv_get("last_run:scrape")
    if (_ago_days(last) or 99) > 3:
        out.append(Msg("vega-stale-competitors", "vega", "scout", "Competitor data is too old for onboarding advice",
                       f"Last competitor scan: {last or 'never'}. 'Already discounts on Groupon/vspots' signals are missing.",
                       "question", "info"))
    try:
        from .market import onboard_next

        top = onboard_next(cfg, db, 5)
    except Exception:
        return out
    idle = [r for r in top if r["status"] == "new" and not db.one(
        "SELECT 1 FROM tasks WHERE lead_id=? AND source='sequence' AND status='open'", (r["id"],))]
    if idle:
        out.append(Msg("vega-top-idle", "vega", "hunter", f"{len(idle)} of the top onboarding picks have no outreach running",
                       "; ".join(f"{r['name']} ({', '.join(r['reasons'][:2])})" for r in idle[:3]), "suggestion", "warn"))
    no_contact = [r for r in top if not (r["email"] or r["phone"])]
    if no_contact:
        out.append(Msg("vega-top-nocontact", "vega", "hunter", f"{len(no_contact)} top pick(s) have no email or phone",
                       ", ".join(r["name"] for r in no_contact[:4]), "suggestion", "info", "enrich"))
    return out


def _check_campus_intake(cfg: Config, db: DB) -> list[Msg]:
    today = date.today()
    starts = [date(today.year, 10, 15), date(today.year, 4, 15), date(today.year + 1, 4, 15)]
    nxt = min(d for d in starts if d >= today)
    if (nxt - today).days > 60:
        return []
    n = db.one("SELECT COUNT(*) n FROM leads WHERE kind='university' AND status='new'")["n"]
    return [Msg(f"campus-unis:{nxt}", "campus", "hunter", f"Semester starts in {(nxt - today).days} days; {n} universities not contacted",
                "Pitch the Buddy platform to universities before the intake.", "suggestion", "warn")] if n else []


def _check_crashes(cfg: Config, db: DB) -> list[Msg]:
    """Agents and jobs that failed: a bug report to the owner, with the error."""
    from .operations import JOB_AGENT

    out = []
    try:
        from .agents import latest

        for key, r in latest(db).items():
            if r["status"] == "error":
                out.append(Msg(f"crash:agent:{key}", "sentinel", key, f"{key.capitalize()} crashed on its last run",
                               r["summary"], "bug", "alert"))
    except Exception:
        pass
    for r in db.q("SELECT key, value FROM kv WHERE key LIKE 'last_error:%'"):
        job = r["key"].split(":", 1)[1]
        last = db.kv_get(f"last_run:{job}") or ""
        if r["value"][:19] > last[:19]:
            out.append(Msg(f"crash:job:{job}", "sentinel", JOB_AGENT.get(job, "setz"), f"Job '{job}' failed",
                           r["value"][20:320], "bug", "alert"))
    return out


def _check_bridge(cfg: Config, db: DB) -> list[Msg]:
    try:
        diffs = json.loads(db.kv_get("bridge:diffs", "[]") or "[]")
    except ValueError:
        return []
    return [Msg("bridge-diffs", "bridge", "atlas", f"{len(diffs)} partner(s) differ between Setz and the admin panel",
                "; ".join(d.get("text", "") for d in diffs[:5]), "bug", "warn")] if diffs else []


CHECKS: list[Callable[[Config, DB], list[Msg]]] = [
    _check_partner_lead_mismatch, _check_sequences_on_closed, _check_duplicates, _check_bad_emails, _check_stalled_sequences,
    _check_contracts, _check_meeting_requests, _check_market_vs_pipeline, _check_campus_intake, _check_crashes, _check_bridge,
]


def cross_check(cfg: Config, db: DB, autofix: bool | None = None) -> dict[str, Any]:
    """Run every peer check, post the results, close messages whose problem is gone, apply allowed fixes."""
    _ensure(db)
    found: list[Msg] = []
    errors = []
    for check in CHECKS:
        try:
            found += check(cfg, db)
        except Exception as exc:   # a broken check is itself a bug report
            errors.append(f"{check.__name__}: {type(exc).__name__}: {exc}")
            found.append(Msg(f"check-broken:{check.__name__}", "sentinel", "setz", f"Peer check {check.__name__} failed",
                             f"{type(exc).__name__}: {exc}"[:300], "bug", "warn"))
    keys = {m.key for m in found}
    new = [m for m in found if post(db, m)]
    stale = [r["id"] for r in db.q("SELECT id, key FROM agent_messages WHERE status='open'") if r["key"] not in keys]
    for mid in stale:
        db.x("UPDATE agent_messages SET status='resolved', updated_at=? WHERE id=?", (utcnow(), mid))
    fixed = []
    allowed = set(cfg.get("council.auto_fix", ["link_partners", "stop_sequences", "prepare_contracts"])) if autofix is not False else set()
    if autofix or autofix is None:
        for r in db.q("SELECT id, fix, title FROM agent_messages WHERE status='open' AND fix != ''"):
            if r["fix"] in allowed:
                try:
                    fixed.append({"title": r["title"], "result": apply_fix(cfg, db, r["id"])})
                except Exception as exc:
                    errors.append(f"fix {r['fix']}: {exc}")
    return {"messages": len(found), "new": [m.title for m in new], "resolved": len(stale), "fixed": fixed, "errors": errors}


# -- parallel run --------------------------------------------------------------------------------

def run_parallel(cfg: Config, only: list[str] | None = None, workers: int | None = None) -> list:
    """Run the agents at the same time, each with its own DB connection. Returns their Reports."""
    from .agents import AGENTS

    keys = [k for k in AGENTS if not only or k in only]

    def one(key: str):
        with DB(cfg.db_path) as db:
            db.conn.execute("PRAGMA busy_timeout=10000")
            return AGENTS[key].run(cfg, db)

    with ThreadPoolExecutor(max_workers=workers or min(6, len(keys) or 1), thread_name_prefix="agent") as ex:
        return list(ex.map(one, keys))


def deliberate(cfg: Config, db: DB) -> list[str]:
    """Optional: Setz (Claude) looks for contradictions across all agents. Posts them as messages from Setz."""
    from .agents import latest
    from .llm import LLM, parse_json

    llm = LLM(cfg)
    if not llm.cloud_enabled or not cfg.get("council.deliberate", True):
        return []
    reps = latest(db)
    msgs = messages(db, "open", limit=30)
    try:
        raw = llm.cloud(
            "Agent reports:\n" + "\n".join(f"- {k}: {r['summary']} | " + "; ".join(f['title'] for f in r['findings'][:6]) for k, r in reps.items())
            + "\n\nOpen messages between agents:\n" + "\n".join(f"- {m['from_agent']}→{m['to_agent']}: {m['title']}" for m in msgs)
            + "\n\nFind up to 4 contradictions, blind spots or logic errors between these agents (e.g. one recommends "
              "something another's data rules out). Return JSON: [{\"to\": agent_key, \"title\": short, \"detail\": one sentence}]. "
              "Return [] if there are none.",
            "You are Setz, reviewing your team of agents. Only real, specific problems.", effort="low", max_tokens=1200)
        items = parse_json(raw) or []
    except Exception:
        return []
    out = []
    for it in items[:4] if isinstance(items, list) else []:
        title = str(it.get("title", ""))[:120]
        if not title:
            continue
        post(db, Msg("ai:" + re.sub(r"\W+", "-", title.lower())[:60], "setz", str(it.get("to", "setz")), title,
                     str(it.get("detail", ""))[:400], "question", "info"))
        out.append(title)
    return out


def overview(cfg: Config, db: DB) -> dict[str, Any]:
    from .agents import AGENTS

    _ensure(db)
    links = [dict(r) for r in db.q("SELECT from_agent, to_agent, COUNT(*) n, MAX(CASE severity WHEN 'alert' THEN 3 WHEN 'warn' THEN 2 "
                                    "ELSE 1 END) sev FROM agent_messages WHERE status='open' GROUP BY from_agent, to_agent")]
    return {"messages": messages(db, "open", limit=60), "recent": messages(db, None, limit=20), "links": links,
            "brains": [brain_summary(db, k) for k in AGENTS]}
