"""Setz's team: specialist sub-agents that each watch one part of the business and report to Setz.

Every agent runs on its own (`sz agents run --agent atlas`) or as part of the team (`sz agents run`). It analyses its
area using the shared database, writes a report (status, headline metrics, findings with severity and a suggested
action) and Setz combines all reports into one briefing, a ranked priority list and to-dos.

Agents are deterministic first (they work offline, with no API key); when the Claude API is configured, Setz adds
a written briefing and strategic recommendations on top.
"""

from __future__ import annotations

import json
import statistics
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from .config import Config
from .db import DB, utcnow

SEVERITY = {"info": 1, "warn": 2, "alert": 3}

AGENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent TEXT,
    run_at TEXT,
    status TEXT,          -- ok | warn | alert | error
    summary TEXT,
    metrics TEXT,         -- JSON {label: value}
    findings TEXT,        -- JSON [Finding]
    duration_ms INTEGER
);
CREATE INDEX IF NOT EXISTS ix_agent_reports ON agent_reports(agent, run_at);
"""


@dataclass
class Finding:
    key: str                      # stable id (used to avoid duplicate to-dos)
    title: str
    detail: str = ""
    severity: str = "info"        # info | warn | alert
    action: str = ""              # suggested next step (becomes a to-do for alerts)


@dataclass
class Report:
    agent: str
    status: str = "ok"
    summary: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)
    duration_ms: int = 0
    run_at: str = ""

    def finalize(self) -> "Report":
        worst = max((SEVERITY[f.severity] for f in self.findings), default=1)
        if self.status != "error":
            self.status = {1: "ok", 2: "warn", 3: "alert"}[worst]
        self.findings.sort(key=lambda f: -SEVERITY[f.severity])
        return self


@dataclass
class Agent:
    key: str
    name: str
    role: str
    icon: str
    fn: Callable[[Config, DB, Report], None]

    def run(self, cfg: Config, db: DB) -> Report:
        rep = Report(agent=self.key, run_at=utcnow())
        t0 = time.monotonic()
        try:
            self.fn(cfg, db, rep)
        except Exception as exc:  # one agent failing never stops the team
            rep.status, rep.summary = "error", f"{type(exc).__name__}: {exc}"[:300]
        rep.duration_ms = int((time.monotonic() - t0) * 1000)
        return rep.finalize()


def _ago_days(iso: str | None) -> float | None:
    if not iso:
        return None
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).total_seconds() / 86400


def _series(db: DB, source: str, key: str, days: int = 60) -> list[tuple[str, float]]:
    since = (date.today() - timedelta(days=days)).isoformat()
    return [(r["day"], r["value"]) for r in db.q("SELECT day, value FROM metrics WHERE source=? AND key=? AND day>=? ORDER BY day",
                                                   (source, key, since))]


# -- the agents --------------------------------------------------------------

def _hermes(cfg: Config, db: DB, rep: Report) -> None:
    """Inbox & follow-ups."""
    from .tracking import response_hours
    from .triage import followups_due, needs_reply

    replies, fus = needs_reply(cfg, db, None), followups_due(cfg, db)
    rh = response_hours(db, cfg.my_addresses)
    high = [r for r in replies if r.priority >= 3]
    rep.metrics = {"Replies owed": len(replies), "High priority": len(high), "Follow-ups due": len(fus),
                   "Median reply (h)": rh if rh is not None else "–"}
    for r in high[:4]:
        rep.findings.append(Finding(f"reply:{r.message_id}", f"Urgent reply: {r.counterpart_name or r.counterpart}",
                                    f"{r.subject} ({r.age_days:.0f}d) {r.summary}", "alert", f"Reply to {r.counterpart_name or r.counterpart}"))
    old = [r for r in replies if r.priority < 3 and r.age_days > 3]
    if old:
        rep.findings.append(Finding("replies-old", f"{len(old)} replies waiting more than 3 days",
                                    ", ".join(r.counterpart_name or r.counterpart for r in old[:5]), "warn", "Clear old replies (drafts are ready)"))
    if fus:
        rep.findings.append(Finding("followups", f"{len(fus)} follow-ups due", ", ".join(f.counterpart for f in fus[:5]), "warn",
                                    "Send the follow-up drafts"))
    if rh is not None and rh > 24:
        rep.findings.append(Finding("slow-replies", f"Median reply time is {rh:.0f} h", "Partners and students expect same-day replies.", "warn"))
    rep.summary = f"{len(replies)} replies owed ({len(high)} urgent), {len(fus)} follow-ups due."


def _atlas(cfg: Config, db: DB, rep: Report) -> None:
    """Service partners & onboarding."""
    from .ops import partner_stats, partners

    ps, rows = partner_stats(cfg, db), partners(cfg, db)
    rep.metrics = {"Live": ps["live"], "Onboarding": ps["onboarding"], "New this month": ps["onboarded_this_month"],
                   "At risk": ps["at_risk"], "Days to live": ps["avg_days_to_live"] if ps["avg_days_to_live"] is not None else "–"}
    for p in rows:
        h = p["health"]
        if h["label"] == "at risk":
            rep.findings.append(Finding(f"partner-risk:{p['id']}", f"{p['name']} needs attention", "; ".join(h["reasons"]), "alert",
                                        f"{'Move onboarding forward' if p['status'] == 'onboarding' else 'Check in'} with {p['name']}"))
        elif h["label"] == "watch":
            rep.findings.append(Finding(f"partner-watch:{p['id']}", f"Keep an eye on {p['name']}", "; ".join(h["reasons"]), "warn"))
    if ps["onboarding"] and not ps["onboarded_this_month"] and date.today().day > 15:
        rep.findings.append(Finding("no-golive", "No partner went live this month yet",
                                    f"{ps['onboarding']} are in onboarding; push the furthest one to live.", "warn"))
    rep.summary = f"{ps['live']} live, {ps['onboarding']} onboarding, {ps['at_risk']} need attention."


def _scout(cfg: Config, db: DB, rep: Report) -> None:
    """Competitor watch."""
    since1 = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    since7 = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    since14 = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()
    q = "SELECT COUNT(*) n FROM listings WHERE first_seen != 'baseline' AND first_seen >= ?"
    n1, n7 = db.one(q, (since1,))["n"], db.one(q, (since7,))["n"]
    prev7 = db.one(q + " AND first_seen < ?", (since14, since7))["n"]
    by_src = {r["source"]: r["n"] for r in db.q("SELECT source, COUNT(*) n FROM listings WHERE first_seen != 'baseline' AND first_seen >= ? GROUP BY source", (since7,))}
    rep.metrics = {"New (24h)": n1, "New (7d)": n7, "Prev. 7d": prev7, "Tracked": db.one("SELECT COUNT(*) n FROM listings")["n"]}
    if by_src:
        top = max(by_src, key=by_src.get)
        rep.findings.append(Finding(f"scout-top:{top}", f"{top} is most active: {by_src[top]} new Berlin listings this week",
                                    ", ".join(f"{k} {v}" for k, v in sorted(by_src.items(), key=lambda x: -x[1])), "info"))
    if prev7 and n7 > prev7 * 1.5 and n7 >= 5:
        rep.findings.append(Finding("scout-surge", f"Competitor listings up {100 * (n7 - prev7) / prev7:.0f}% week on week",
                                    "Venues are actively discounting: a good week to pitch them.", "warn", "Pitch this week's new competitor venues"))
    fresh = db.q("""SELECT l.merchant, l.source, l.category FROM listings l WHERE l.first_seen != 'baseline' AND l.first_seen >= ?
                    AND NOT EXISTS (SELECT 1 FROM leads d WHERE lower(d.name)=lower(l.merchant) AND d.status != 'new') LIMIT 5""", (since7,))
    for f in fresh:
        rep.findings.append(Finding(f"scout-new:{f['merchant']}", f"New on {f['source']}: {f['merchant']}", f["category"] or "", "info"))
    last = db.kv_get("last_run:scrape")
    if not last or (_ago_days(last) or 99) > 2:
        rep.findings.append(Finding("scout-stale", "Competitor scan is out of date", f"Last scan: {last or 'never'}", "warn", "Run the competitor scan"))
    rep.summary = f"{n7} new competitor listings this week ({n1} in the last 24 h)."


def _hunter(cfg: Config, db: DB, rep: Report) -> None:
    """Leads & outreach."""
    from .tracking import draft_funnel

    replied = db.q("SELECT * FROM leads WHERE status='replied'")
    hot = db.q("SELECT * FROM leads WHERE status='new' AND email != '' ORDER BY score DESC LIMIT 5")
    no_contact = db.one("SELECT COUNT(*) n FROM leads WHERE status='new' AND (email IS NULL OR email='')")["n"]
    funnel = draft_funnel(db, 30).get("outreach", {"drafted": 0, "sent": 0, "replied": 0})
    rate = round(100 * funnel["replied"] / funnel["sent"]) if funnel["sent"] else None
    rep.metrics = {"Warm (replied)": len(replied), "Ready to pitch": len(hot), "Need contact info": no_contact,
                   "Outreach reply %": rate if rate is not None else "–"}
    for l in replied:
        rep.findings.append(Finding(f"lead-replied:{l['id']}", f"{l['name']} replied", "Book a call while they're warm.", "alert",
                                    f"Book a call with {l['name']}"))
    for l in hot[:3]:
        rep.findings.append(Finding(f"lead-hot:{l['id']}", f"Pitch {l['name']} (score {l['score']:.0f})",
                                    f"{l['kind']} · {l['category'] or ''} · seen on {', '.join(json.loads(l['sources'] or '[]'))}", "info"))
    if no_contact > 20:
        rep.findings.append(Finding("enrich", f"{no_contact} new leads have no email yet", "Run enrichment (website + Impressum).", "info",
                                    "Run lead enrichment"))
    if rate is not None and funnel["sent"] >= 10 and rate < 15:
        rep.findings.append(Finding("low-reply", f"Outreach reply rate is {rate}%", "Try the other subject-line angle from the playbook.", "warn"))
    rep.summary = f"{len(replied)} warm leads, {len(hot)} strong leads ready to pitch."


def _nova(cfg: Config, db: DB, rep: Report) -> None:
    """Instagram & content."""
    fol = _series(db, "instagram", "followers", 30)
    eng = _series(db, "instagram", "engagement_rate_pct", 30)
    comments = json.loads(db.kv_get("instagram:unanswered_comments", "[]"))
    week = [v for d, v in fol if d >= (date.today() - timedelta(days=7)).isoformat()]
    growth7 = (week[-1] - week[0]) if len(week) >= 2 else None
    rep.metrics = {"Followers": int(fol[-1][1]) if fol else "–", "7d growth": f"{growth7:+.0f}" if growth7 is not None else "–",
                   "Engagement %": round(eng[-1][1], 2) if eng else "–", "Comments waiting": len(comments)}
    if comments:
        rep.findings.append(Finding("ig-comments", f"{len(comments)} Instagram comments waiting", ", ".join("@" + c["user"] for c in comments[:5]),
                                    "warn", "Answer Instagram comments"))
    if len(eng) >= 8:
        avg = statistics.mean(v for _, v in eng[:-1])
        if eng[-1][1] < avg * 0.75:
            rep.findings.append(Finding("ig-eng-drop", "Engagement dropped below your 30-day average",
                                        f"{eng[-1][1]:.2f}% vs {avg:.2f}% average. Post a reel with a strong hook.", "warn"))
    launches = db.q("SELECT name FROM partners WHERE live_at >= ?", ((date.today() - timedelta(days=10)).isoformat(),))
    for p in launches[:2]:
        rep.findings.append(Finding(f"ig-spotlight:{p['name']}", f"Post a partner spotlight: {p['name']}", "They went live recently.", "info",
                                    f"Post Instagram spotlight for {p['name']}"))
    if not fol:
        rep.findings.append(Finding("ig-none", "No Instagram data yet", "Connect Instagram (sz auth instagram).", "info"))
    rep.summary = (f"{rep.metrics['Followers']} followers ({rep.metrics['7d growth']} this week), {len(comments)} comments waiting."
                   if fol else "Instagram not connected yet.")


def _forecast(series: list[tuple[str, float]], target: float) -> str | None:
    """Days until a target at the recent rate (simple linear fit over the series)."""
    if len(series) < 5:
        return None
    xs = [(date.fromisoformat(d) - date.fromisoformat(series[0][0])).days for d, _ in series]
    ys = [v for _, v in series]
    mx, my = statistics.mean(xs), statistics.mean(ys)
    den = sum((x - mx) ** 2 for x in xs) or 1
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den
    if slope <= 0:
        return "not growing at the current rate"
    days = (target - ys[-1]) / slope
    if days <= 0:
        return "target reached"
    return f"on track for {(date.today() + timedelta(days=round(days))).strftime('%d %b %Y')} at +{slope:.1f}/day"


def _quant(cfg: Config, db: DB, rep: Report) -> None:
    """Growth analyst: goals, forecasts, anomalies, week-over-week."""
    from .ops import scorecard
    from .tracking import goals

    gs = goals(cfg, db)
    for g in gs:
        src, _, key = g["metric"].partition(".")
        fc = _forecast(_series(db, src, key, 90), g["target"])
        sev = "info"
        if g["current"] is None:
            detail = "No data yet."
        else:
            detail = f"{g['current']:,.0f} of {g['target']:,.0f} ({g['pct']}%)" + (f"; {fc}" if fc else "")
            if fc == "not growing at the current rate":
                sev = "warn"
        rep.findings.append(Finding(f"goal:{g['metric']}", g["label"], detail, sev))
    for r in scorecard(cfg, db):
        if r["last_week"] >= 3 and r["this_week"] < r["last_week"] * 0.6 and date.today().weekday() >= 3:
            rep.findings.append(Finding(f"wow:{r['metric']}", f"{r['metric']} down this week", f"{r['this_week']} vs {r['last_week']} last week", "warn"))
    for src, key, label in (("manual", "paying_members", "Paying members"), ("instagram", "followers", "Instagram followers")):
        s = _series(db, src, key, 21)
        if len(s) >= 8:
            prev, cur = s[-8][1], s[-1][1]
            if prev and cur < prev * 0.95:
                rep.findings.append(Finding(f"drop:{key}", f"{label} fell {100 * (prev - cur) / prev:.0f}% in a week", f"{prev:,.0f} → {cur:,.0f}",
                                            "alert", f"Investigate the drop in {label.lower()}"))
    rep.metrics = {g["label"]: f"{g['pct']}%" for g in gs[:4]}
    on_track = sum(1 for f in rep.findings if f.key.startswith("goal:") and "on track" in f.detail)
    rep.summary = f"{len(gs)} goals tracked, {on_track} on track at the current rate."


def _chrono(cfg: Config, db: DB, rep: Report) -> None:
    """Schedule, prep & to-dos."""
    from .ops import tasks, today_plan

    plan = today_plan(cfg, db)
    meetings = [i for i in plan["items"] if i["type"] == "meeting"]
    overdue = [t for t in tasks(db, "open") if t.get("overdue")]
    rep.metrics = {"Meetings today": len(meetings), "To-dos today": len(plan["tasks_today"]), "Overdue": len(overdue),
                   "Now": plan["current"]["title"] if plan["current"] else "free"}
    now = datetime.now().strftime("%H:%M")
    for m in meetings:
        if m["start"] >= now and m["start"] <= (datetime.now() + timedelta(hours=2)).strftime("%H:%M"):
            rep.findings.append(Finding(f"prep:{m['event_id']}", f"Prep for '{m['title']}' at {m['start']}", "Open the prep from the plan.", "warn",
                                        f"Prepare for {m['title']}"))
    clashes = [i for i in plan["items"] if i["type"] == "block" and i.get("clash")]
    if clashes:
        rep.findings.append(Finding("clashes", f"{len(clashes)} routine blocks overlap meetings", ", ".join(c["title"] for c in clashes), "info"))
    if overdue:
        rep.findings.append(Finding("overdue", f"{len(overdue)} overdue to-dos", ", ".join(t["title"] for t in overdue[:4]), "alert"))
    rep.summary = f"{len(meetings)} meetings, {len(plan['tasks_today'])} to-dos today, {len(overdue)} overdue."


def _campus(cfg: Config, db: DB, rep: Report) -> None:
    """International students & universities (Buddy platform, intake calendar)."""
    today = date.today()
    # German semester rhythm: winter intake arrives Aug–Oct (lectures mid-Oct), summer intake arrives Mar–Apr.
    winter = date(today.year, 10, 15) if today <= date(today.year, 10, 15) else date(today.year + 1, 10, 15)
    summer = date(today.year, 4, 15) if today <= date(today.year, 4, 15) else date(today.year + 1, 4, 15)
    nxt_name, nxt = ("winter semester", winter) if winter < summer else ("summer semester", summer)
    days = (nxt - today).days
    unis = {r["status"]: r["n"] for r in db.q("SELECT status, COUNT(*) n FROM leads WHERE kind='university' GROUP BY status")}
    live_unis = db.one("SELECT COUNT(*) n FROM partners WHERE kind='university' AND status='live'")["n"]
    buddy = _series(db, "manual", "buddy_students", 60)
    rep.metrics = {"Next intake": f"{nxt_name} in {days}d", "Buddy universities": live_unis,
                   "Unis not contacted": unis.get("new", 0), "Buddy students": int(buddy[-1][1]) if buddy else "–"}
    if days <= 120 and unis.get("new", 0):
        sev = "alert" if days <= 60 else "warn"
        rep.findings.append(Finding(f"intake:{nxt.isoformat()}", f"{nxt_name.capitalize()} lectures start in {days} days",
                                    f"{unis.get('new', 0)} universities haven't heard about the Buddy platform yet.", sev,
                                    "Pitch the Buddy platform to 3 more universities"))
    in_meeting = unis.get("meeting", 0) + unis.get("replied", 0)
    if in_meeting:
        rep.findings.append(Finding("uni-warm", f"{in_meeting} universities are in conversation", "Send the Buddy platform proposal and agreement.", "warn"))
    if today.month in (8, 9, 10, 3, 4):
        rep.findings.append(Finding(f"arrival:{today.month}", "Arrival season: students are landing now",
                                    "Push the free trial at welcome weeks, and post daily arrival tips (Anmeldung, SIM, housing).", "info"))
    rep.summary = f"{nxt_name.capitalize()} in {days} days · {live_unis} Buddy-platform universities live."


def _sentinel(cfg: Config, db: DB, rep: Report) -> None:
    """Systems & data health."""
    conn = json.loads(db.kv_get("connections", "{}"))
    bad = [r for r in conn.get("results", []) if not r["ok"] and not r["optional"]]
    stale_rules = {"mail": 0.5, "instagram": 2, "scrape": 2, "brief": 1.2}
    stale = []
    for job, limit in stale_rules.items():
        d = _ago_days(db.kv_get(f"last_run:{job}"))
        if d is None or d > limit:
            stale.append(f"{job} ({'never' if d is None else f'{d:.1f} days ago'})")
    rep.metrics = {"Connections OK": f"{len(conn.get('results', [])) - len(bad)}/{len(conn.get('results', []))}" if conn else "unchecked",
                   "Stale feeds": len(stale), "DB rows": db.one("SELECT (SELECT COUNT(*) FROM messages)+(SELECT COUNT(*) FROM leads)+(SELECT COUNT(*) FROM listings) n")["n"]}
    for r in bad:
        rep.findings.append(Finding(f"conn:{r['name']}", f"{r['name']} is not connected", f"{r['detail']} Fix: {r['fix']}", "alert", f"Fix {r['name']} connection"))
    if stale:
        rep.findings.append(Finding("stale", "Some data is out of date", ", ".join(stale), "warn"))
    if not conn:
        rep.findings.append(Finding("conn-unchecked", "Connections not checked yet", "Run sz doctor.", "info"))
    rep.summary = "All systems normal." if not bad and not stale else f"{len(bad)} connection problems, {len(stale)} stale feeds."


AGENTS: dict[str, Agent] = {a.key: a for a in [
    Agent("hermes", "Hermes", "Inbox & follow-ups", "mail", _hermes),
    Agent("atlas", "Atlas", "Service partners", "partners", _atlas),
    Agent("scout", "Scout", "Competitor watch", "radar", _scout),
    Agent("hunter", "Hunter", "Leads & outreach", "target", _hunter),
    Agent("nova", "Nova", "Instagram & content", "spark", _nova),
    Agent("quant", "Quant", "Growth analyst", "chart", _quant),
    Agent("chrono", "Chrono", "Schedule & to-dos", "clock", _chrono),
    Agent("campus", "Campus", "International students & universities", "globe", _campus),
    Agent("sentinel", "Sentinel", "Systems & data health", "shield", _sentinel),
]}


# -- running + Setz synthesis -------------------------------------------------

def _ensure(db: DB) -> None:
    db.conn.executescript(AGENT_SCHEMA)


def run(cfg: Config, db: DB, only: list[str] | None = None, publish_events: bool = True) -> dict:
    """Run the team, store reports, let Setz synthesise and create to-dos for alerts."""
    _ensure(db)
    reports = []
    for key, agent in AGENTS.items():
        if only and key not in only:
            continue
        rep = agent.run(cfg, db)
        db.x("INSERT INTO agent_reports(agent,run_at,status,summary,metrics,findings,duration_ms) VALUES(?,?,?,?,?,?,?)",
             (rep.agent, rep.run_at, rep.status, rep.summary, json.dumps(rep.metrics, ensure_ascii=False, default=str),
              json.dumps([asdict(f) for f in rep.findings], ensure_ascii=False), rep.duration_ms))
        reports.append(rep)
        if publish_events:
            _publish_agent(cfg, rep)
    db.x("DELETE FROM agent_reports WHERE run_at < ?", ((datetime.now(timezone.utc) - timedelta(days=30)).isoformat(),))
    synthesis = synthesize(cfg, db)
    return {"agents": [{"agent": r.agent, "status": r.status, "summary": r.summary} for r in reports], "setz": synthesis}


def _publish_agent(cfg: Config, rep: Report) -> None:
    import httpx

    try:
        httpx.post(f"http://127.0.0.1:{int(cfg.get('dashboard.port', 8765))}/api/agents/event",
                   json={"agent": rep.agent, "status": rep.status, "summary": rep.summary, "at": time.time()},
                   headers={"X-SZ": "1"}, timeout=1.0)
    except httpx.HTTPError:
        pass


def latest(db: DB) -> dict[str, dict]:
    _ensure(db)
    out = {}
    for r in db.q("""SELECT a.* FROM agent_reports a JOIN (SELECT agent, MAX(run_at) mx FROM agent_reports GROUP BY agent) t
                     ON t.agent=a.agent AND t.mx=a.run_at"""):
        d = dict(r)
        d["metrics"], d["findings"] = json.loads(d["metrics"] or "{}"), json.loads(d["findings"] or "[]")
        out[d["agent"]] = d
    return out


def activity(db: DB, limit: int = 40) -> list[dict]:
    """Recent findings across all agents, newest first: the 'thought stream'."""
    _ensure(db)
    out = []
    for r in db.q("SELECT agent, run_at, status, summary, findings FROM agent_reports ORDER BY run_at DESC LIMIT 60"):
        out.append({"agent": r["agent"], "at": r["run_at"], "severity": "report", "text": r["summary"]})
        for f in json.loads(r["findings"] or "[]")[:3]:
            out.append({"agent": r["agent"], "at": r["run_at"], "severity": f["severity"], "text": f["title"]})
        if len(out) >= limit:
            break
    return out[:limit]


def synthesize(cfg: Config, db: DB, use_ai: bool | None = None) -> dict:
    """Setz merges every agent's latest report into one ranked priority list, a briefing and to-dos."""
    from .ops import add_task

    reps = latest(db)
    items = []
    for key, r in reps.items():
        for f in r["findings"]:
            items.append({**f, "agent": key, "agent_name": AGENTS[key].name if key in AGENTS else key})
    items.sort(key=lambda f: (-SEVERITY.get(f["severity"], 1), f["agent"]))
    created = 0
    for f in items:
        if f["severity"] == "alert" and f.get("action"):
            created += bool(add_task(db, f["action"], priority=3, source=f"agent:{f['agent']}", due=date.today().isoformat(),
                                     notes=f["detail"], dedupe_key=f"agent:{f['key']}:{date.today().isoformat()}"))
    counts = {s: sum(1 for f in items if f["severity"] == s) for s in ("alert", "warn", "info")}
    status = "alert" if counts["alert"] else "warn" if counts["warn"] else "ok"
    top = items[:6]
    briefing = (f"Setz here. {counts['alert']} things need you now, {counts['warn']} to watch. "
                + (" ".join(f"{i + 1}. {f['title']}." for i, f in enumerate(top[:3])) if top else "Everything is calm."))
    if use_ai is None:
        use_ai = bool(cfg.get("agents.ai_briefing", True))
    if use_ai and top:
        from .llm import LLM

        llm = LLM(cfg)
        if llm.cloud_enabled:
            try:
                from .knowledge import ONE_LINER

                briefing = llm.cloud(
                    f"{ONE_LINER}\n\nReports from your specialist agents:\n"
                    + "\n".join(f"- [{r['agent']}] {r['summary']}" for r in reps.values())
                    + "\n\nTop findings:\n" + "\n".join(f"- ({f['severity']}) {f['agent_name']}: {f['title']}: {f['detail']}" for f in items[:15])
                    + "\n\nAs Setz, write the founder's briefing: 2 sentences on the state of the business, then the 3 most "
                      "important moves today and one strategic recommendation for this week. Plain text, under 120 words.",
                    "You are Setz, chief of staff and growth co-founder of Settleezy. Calm, sharp, specific.", effort="low", max_tokens=1500)
            except Exception:
                pass
    result = {"at": utcnow(), "status": status, "counts": counts, "briefing": briefing, "priorities": top, "todos_created": created}
    db.kv_set("setz:synthesis", json.dumps(result, ensure_ascii=False, default=str))
    return result
