"""Market intelligence: how the current partners perform, what competitors do, and whom to onboard next.

  partner_analysis   every partner's performance (redemptions per week live, health, category, campus) plus the
                     portfolio view: which categories carry the redemptions, where the mix is thin, renewals due
  competitor_analysis  Groupon, vspots, Top10 Berlin, UNiDAYS, Student Beans ... (from `sz scrape` listings):
                     activity per platform, their strongest categories, overlap with your partners (exclusivity
                     risk), and venues that already discount elsewhere but not with you yet (warmest leads)
  onboard_next       one ranked list of who to sign next, with the reasons and a pitch angle per lead:
                     join likelihood x student value x campus proximity x category gap x competitor proof
                     x demand from the app (venue requests from members, via the admin-panel bridge)

Everything is deterministic and explainable; with the Claude API configured `strategy()` adds a written view.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

from .config import Config
from .db import DB

COMPETITORS = {"groupon": "Groupon", "vspots": "vspots", "top10berlin": "Top10 Berlin", "unidays": "UNiDAYS",
               "studentbeans": "Student Beans"}

# Broad buckets used for the portfolio mix (first match wins).
BUCKETS = [
    ("Food & drinks", r"restaurant|food|café|cafe|coffee|bar|pub|pizza|burger|bakery|bäckerei|döner|sushi|ice cream|tea|deli|chocolate|imbiss"),
    ("Groceries", r"supermarket|grocery|späti|bio ?markt|lebensmittel|convenience|variety"),
    ("Sport & fitness", r"gym|fitness|sport|yoga|climb|boulder|swim|dance|trampoline|bike|fahrrad"),
    ("Going out & culture", r"cinema|kino|museum|gallery|escape|event|club|concert|theat|bowling|arcade|minigolf|arts"),
    ("Study & services", r"copyshop|books|stationery|laundry|coworking|study|language|course|school|tutor|optician|phone|sim|computer|electronic|tech"),
    ("Beauty & wellness", r"hair|friseur|barber|beauty|nail|cosmetic|spa|wellness|massage|sauna"),
    ("Shopping", r"fashion|clothing|second hand|shoes|sneaker|music shop|photo|art supplies|outdoor"),
    ("Housing & money", r"housing|wohn|apartment|insurance|versicherung|bank|blocked account"),
    ("Mobility & travel", r"mobility|transport|scooter|travel|reise|bus|train|rental"),
    ("Universities", r"university|universität|hochschule|college"),
]
# Target share of the partner portfolio per bucket (what members use most). Override with [market.target_mix].
TARGET_MIX = {"Food & drinks": 0.35, "Groceries": 0.08, "Sport & fitness": 0.12, "Going out & culture": 0.12,
              "Study & services": 0.12, "Beauty & wellness": 0.07, "Shopping": 0.06, "Mobility & travel": 0.04,
              "Housing & money": 0.04}


def bucket(text: str) -> str:
    t = (text or "").lower()
    for name, rx in BUCKETS:
        if re.search(rx, t):
            return name
    return "Other"


def _days(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).total_seconds() / 86400


# -- partners ------------------------------------------------------------------------------------------

def partner_analysis(cfg: Config, db: DB) -> dict[str, Any]:
    from .ops import partners

    rows = partners(cfg, db)
    lead_by_id = {r["id"]: dict(r) for r in db.q("SELECT id, campus, distance_m, sources, district FROM leads WHERE id IN "
                                                    "(SELECT lead_id FROM partners WHERE lead_id IS NOT NULL)")}
    out = []
    for p in rows:
        live_days = _days(p.get("live_at")) if p["status"] == "live" else None
        weeks = max(1.0, (live_days or 0) / 7) if live_days is not None else None
        per_week = round((p.get("redemptions") or 0) / weeks, 2) if weeks else None
        lead = lead_by_id.get(p.get("lead_id")) or {}
        comp = [COMPETITORS.get(s.split(":")[0], s) for s in json.loads(lead.get("sources") or "[]") if s.split(":")[0] in COMPETITORS]
        out.append({"id": p["id"], "name": p["name"], "status": p["status"], "stage": p["stage_label"], "bucket": bucket(f"{p.get('category')} {p['name']}"),
                    "category": p.get("category") or "", "redemptions": p.get("redemptions") or 0, "per_week": per_week,
                    "health": p["health"], "campus": lead.get("campus") or "", "district": lead.get("district") or "",
                    "also_on": comp, "renewal_date": p.get("renewal_date"), "offer": p.get("offer") or ""})
    live = [p for p in out if p["status"] == "live"]
    ranked = sorted([p for p in live if p["per_week"] is not None], key=lambda p: -p["per_week"])
    total_red = sum(p["redemptions"] for p in live) or 0
    by_bucket: dict[str, dict] = defaultdict(lambda: {"partners": 0, "live": 0, "redemptions": 0})
    for p in out:
        if p["status"] in ("live", "onboarding"):
            b = by_bucket[p["bucket"]]
            b["partners"] += 1
            b["live"] += p["status"] == "live"
            b["redemptions"] += p["redemptions"]
    mix = mix_gaps(cfg, by_bucket)
    insights = []
    for g in mix[:3]:
        if g["gap"] > 0.05:
            insights.append(f"{g['bucket']} is under-represented: {g['share']:.0%} of partners vs a {g['target']:.0%} target.")
    for name, b in by_bucket.items():
        if total_red >= 20 and b["live"] and b["redemptions"] / total_red > 1.5 * (b["live"] / max(1, len(live))):
            insights.append(f"{name} partners punch above their weight: {b['redemptions'] / total_red:.0%} of redemptions "
                            f"from {b['live']} partner(s). Sign more like them.")
    if ranked:
        insights.append(f"Top performer: {ranked[0]['name']} ({ranked[0]['per_week']:g} redemptions/week).")
    weak = [p for p in live if p["per_week"] is not None and p["per_week"] < 0.5 and (p["redemptions"] or 0) < 3
            and (_days(next((r.get("live_at") for r in rows if r["id"] == p["id"]), None)) or 0) >= 28]
    if weak:
        insights.append(f"{len(weak)} live partner(s) barely get used ({', '.join(p['name'] for p in weak[:3])}): "
                        "promote them on Instagram or improve the offer.")
    exclusive_risk = [p for p in live if p["also_on"]]
    if exclusive_risk:
        insights.append(f"{len(exclusive_risk)} partner(s) are also on competitor platforms "
                        f"({', '.join(p['name'] for p in exclusive_risk[:3])}): make sure your member offer is the better one.")
    soon = [p for p in out if p["renewal_date"] and 0 <= (date.fromisoformat(p["renewal_date"]) - date.today()).days <= 45]
    return {"partners": out, "top": ranked[:5], "bottom": list(reversed(ranked[-5:])) if len(ranked) > 5 else [],
            "by_bucket": dict(by_bucket), "mix": mix, "renewals_soon": soon, "insights": insights,
            "totals": {"partners": len(out), "live": len(live), "redemptions": total_red}}


def mix_gaps(cfg: Config, by_bucket: dict) -> list[dict]:
    target = cfg.get("market.target_mix") or TARGET_MIX
    total = sum(b["partners"] for b in by_bucket.values()) or 0
    out = []
    for name, t in target.items():
        share = (by_bucket.get(name, {}).get("partners", 0) / total) if total else 0.0
        out.append({"bucket": name, "target": t, "share": round(share, 3), "gap": round(t - share, 3),
                    "partners": by_bucket.get(name, {}).get("partners", 0)})
    return sorted(out, key=lambda g: -g["gap"])


# -- competitors ---------------------------------------------------------------------------------------

def competitor_analysis(cfg: Config, db: DB, days: int = 30) -> dict[str, Any]:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = [dict(r) for r in db.q("SELECT source, merchant, category, price, first_seen, last_seen FROM listings")]
    per: dict[str, dict] = {}
    for r in rows:
        src = r["source"] or "?"
        d = per.setdefault(src, {"platform": COMPETITORS.get(src, src), "listings": 0, "new": 0, "categories": Counter(), "merchants": set()})
        d["listings"] += 1
        if r["first_seen"] not in (None, "baseline") and r["first_seen"] >= since:
            d["new"] += 1
        d["categories"][bucket(f"{r['category']} {r['merchant']}")] += 1
        if r["merchant"]:
            d["merchants"].add(r["merchant"].strip().casefold())
    partner_names = {r["name"].strip().casefold() for r in db.q("SELECT name FROM partners")}
    platforms = []
    for src, d in per.items():
        overlap = sorted(d["merchants"] & partner_names)
        platforms.append({"source": src, "platform": d["platform"], "listings": d["listings"], f"new_{days}d": d["new"],
                          "top_categories": d["categories"].most_common(4), "overlap_with_partners": overlap,
                          "merchants": len(d["merchants"])})
    platforms.sort(key=lambda p: -p["listings"])
    # venues that discount elsewhere and aren't yours yet (proven discount appetite)
    warm = []
    for l in db.q("SELECT * FROM leads WHERE status NOT IN ('partner','lost') AND kind IN ('merchant','brand') AND sources IS NOT NULL"):
        comp = [s.split(":")[0] for s in json.loads(l["sources"] or "[]") if s.split(":")[0] in COMPETITORS]
        if comp:
            warm.append({"id": l["id"], "name": l["name"], "on": [COMPETITORS[c] for c in dict.fromkeys(comp)],
                         "category": l["category"] or "", "status": l["status"], "score": l["score"],
                         "campus": l["campus"] or "", "distance_m": l["distance_m"]})
    warm.sort(key=lambda w: (-len(w["on"]), -(w["score"] or 0)))
    # where competitors are strong and you're weak
    ours = Counter(bucket(f"{r['category']} {r['name']}") for r in db.q("SELECT name, category FROM partners WHERE status IN ('live','onboarding')"))
    theirs: Counter = Counter()
    for d in per.values():
        theirs.update(d["categories"])
    battlegrounds = [{"bucket": b, "competitor_listings": n, "your_partners": ours.get(b, 0)}
                     for b, n in theirs.most_common() if b != "Other"]
    insights = []
    if platforms:
        top = platforms[0]
        insights.append(f"{top['platform']} is the biggest competitor in your data ({top['listings']} listings).")
    for b in battlegrounds[:3]:
        if b["your_partners"] * 5 < b["competitor_listings"]:
            insights.append(f"Competitors list {b['competitor_listings']} {b['bucket'].lower()} offers, you have {b['your_partners']}: "
                            "a category students already look for.")
    if warm:
        insights.append(f"{len(warm)} venues already give discounts on other platforms but not with you; start with "
                        f"{', '.join(w['name'] for w in warm[:3])}.")
    last = db.kv_get("last_run:scrape")
    return {"platforms": platforms, "warm_leads": warm[:30], "battlegrounds": battlegrounds, "insights": insights,
            "last_scan": last, "stale": (_days(last) or 99) > 3}


# -- whom to onboard next --------------------------------------------------------------------------------

def _demand(db: DB) -> dict[str, int]:
    """Venue requests from app members (bridge.py stores them as kv 'bridge:requests')."""
    try:
        reqs = json.loads(db.kv_get("bridge:requests", "[]") or "[]")
    except ValueError:
        return {}
    c: Counter = Counter()
    for r in reqs:
        if r.get("venue"):
            c[r["venue"].strip().casefold()] += int(r.get("count", 1))
        if r.get("category"):
            c["cat:" + bucket(r["category"])] += int(r.get("count", 1))
    return dict(c)


def onboard_next(cfg: Config, db: DB, limit: int = 15, bucket_filter: str | None = None, campus: str | None = None) -> list[dict]:
    """Ranked recommendations: who to sign next, why, and how to pitch them."""
    from .leadintel import competitor_platforms, join_likelihood
    from .leads import category_weight
    from .pipeline import rates as conv_rates

    rates = conv_rates(db)
    pa = partner_analysis(cfg, db)
    gaps = {g["bucket"]: g["gap"] for g in pa["mix"]}
    strong = {b for b, v in pa["by_bucket"].items() if pa["totals"]["redemptions"] >= 20 and v["redemptions"] / pa["totals"]["redemptions"] > 0.3}
    demand = _demand(db)
    partner_campus = Counter((p["campus"] or "") for p in pa["partners"] if p["status"] in ("live", "onboarding"))
    sql = "SELECT * FROM leads WHERE status IN ('new','drafted','contacted','replied','meeting') AND kind IN ('merchant','brand','service','housing','university')"
    out = []
    for row in db.q(sql):
        lead = dict(row)
        b = bucket(f"{lead.get('category')} {lead['name']}")
        if bucket_filter and bucket_filter.lower() not in b.lower():
            continue
        if campus and campus.lower() not in (lead.get("campus") or "").lower():
            continue
        p_join = join_likelihood(db, lead, rates)
        value = category_weight(f"{lead.get('category')} {lead['name']}")
        d = lead.get("distance_m")
        near = 1.0 if d is not None and d <= 300 else 0.8 if d is not None and d <= 600 else 0.55 if d is not None and d <= 1000 else 0.35
        if lead["kind"] in ("brand", "service", "housing", "university"):
            near = 0.8   # not tied to a location
        comp = competitor_platforms(lead)
        gap = max(0.0, gaps.get(b, 0.0))
        dem = demand.get(lead["name"].strip().casefold(), 0) + demand.get("cat:" + b, 0) // 3
        reach = 1.0 if lead.get("email") or lead.get("phone") else 0.6 if lead.get("instagram") else 0.35
        empty_campus = lead.get("campus") and partner_campus.get(lead["campus"], 0) == 0
        s = (100 * (0.30 * min(1.0, p_join / 0.25) + 0.22 * value + 0.15 * near + 0.12 * min(1.0, gap / 0.15)
                    + 0.08 * min(1.0, len(comp) / 2) + 0.08 * min(1.0, dem / 5) + 0.05 * reach)
             + (4 if empty_campus else 0) + (3 if b in strong else 0))
        reasons = []
        if lead["status"] in ("replied", "meeting"):
            reasons.append(f"already {lead['status']}: close it")
        if comp:
            reasons.append(f"already discounts on {', '.join(comp)}")
        if dem:
            reasons.append(f"members asked for it ({dem}×)" if demand.get(lead["name"].strip().casefold()) else "members ask for this category")
        if gap > 0.05:
            reasons.append(f"fills your {b.lower()} gap")
        if b in strong:
            reasons.append(f"{b.lower()} partners get used most")
        if d is not None and d <= 600:
            reasons.append(f"{d} m from {lead.get('campus')}")
        if empty_campus:
            reasons.append(f"first partner at {lead['campus']}")
        if value >= 0.9:
            reasons.append("high everyday value for students")
        if (lead.get("ig_followers") or 0) >= 5000:
            reasons.append(f"{lead['ig_followers']:,} Instagram followers")
        if reach < 1:
            reasons.append("needs contact details (run Impressum enrichment)")
        out.append({"id": lead["id"], "name": lead["name"], "kind": lead["kind"], "bucket": b, "category": lead.get("category") or "",
                    "status": lead["status"], "campus": lead.get("campus") or "", "distance_m": d,
                    "email": lead.get("email") or "", "phone": lead.get("phone") or "", "instagram": lead.get("instagram") or "",
                    "join_likelihood": p_join, "priority": round(min(100.0, s), 1), "reasons": reasons[:5],
                    "pitch": pitch_angle(lead, b, comp, d)})
    out.sort(key=lambda r: -r["priority"])
    return out[:limit]


def pitch_angle(lead: dict, b: str, comp: list[str], distance: int | None) -> str:
    if lead["kind"] == "university":
        return "Buddy platform for international students: free onboarding help, arrival guides and a local student community."
    if comp:
        return (f"You already offer deals on {comp[0]}; Settleezy brings verified students who come back every week, "
                "with no commission per deal.")
    if distance is not None and distance <= 600:
        return f"Hundreds of students walk past you every day ({lead.get('campus')}); a small member discount turns them into regulars."
    if b == "Food & drinks":
        return "Fill quiet hours with students: a member discount on weekday afternoons, promoted on our Instagram."
    if b == "Sport & fitness":
        return "Student memberships: Settleezy members sign up for the semester, you get a steady new cohort every intake."
    return "Reach thousands of international students in Berlin through the Settleezy app and Instagram, free to join."


def strategy(cfg: Config, db: DB) -> dict[str, Any]:
    """Everything above in one call (dashboard Market tab, Vega agent, voice)."""
    pa, ca, nxt = partner_analysis(cfg, db), competitor_analysis(cfg, db), onboard_next(cfg, db, 10)
    text = ""
    from .llm import LLM

    llm = LLM(cfg)
    if llm.cloud_enabled and cfg.get("market.ai_summary", True):
        try:
            text = llm.cloud(
                "Partner insights:\n" + "\n".join(pa["insights"]) + "\n\nCompetitor insights:\n" + "\n".join(ca["insights"])
                + "\n\nTop onboarding candidates:\n" + "\n".join(f"- {r['name']} ({r['bucket']}): {', '.join(r['reasons'])}" for r in nxt[:8])
                + "\n\nIn under 120 words: whom Settleezy should onboard next and why, and one competitive move for this month.",
                "You are Setz, growth co-founder of Settleezy (Berlin student discount membership). Specific and practical.",
                effort="low", max_tokens=1200)
        except Exception:
            text = ""
    return {"partners": pa, "competitors": ca, "onboard_next": nxt, "summary": text}


def spoken(cfg: Config, db: DB) -> str:
    nxt = onboard_next(cfg, db, 3)
    if not nxt:
        return "I have no open leads to recommend yet. Run a campus scan first."
    parts = [f"{r['name']}, because {', and '.join(r['reasons'][:2]) or 'it scores highest'}" for r in nxt]
    return "Onboard next: " + "; then ".join(parts) + "."
