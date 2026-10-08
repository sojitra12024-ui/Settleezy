"""Weekly co-founder review: what's working, what to try next, marketing for the coming week."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from .config import Config
from .db import DB
from .knowledge import ONE_LINER, playbook
from .leads import pipeline
from .llm import LLM
from .voice_profile import compute_stats


def _metric_series(db: DB, days: int = 56) -> dict[str, list]:
    since = (datetime.now() - timedelta(days=days)).date().isoformat()
    series: dict[str, list] = {}
    for r in db.q("SELECT day, source, key, value FROM metrics WHERE day >= ? ORDER BY day", (since,)):
        series.setdefault(f"{r['source']}.{r['key']}", []).append((r["day"], r["value"]))
    # keep it compact: weekly samples
    return {k: v[::7] + ([v[-1]] if v and v[-1] not in v[::7] else []) for k, v in series.items()}


def _imported(cfg: Config) -> str:
    from .importer import library

    return (library(cfg, "notes", 3000) + "\n\n" + library(cfg, "social", 3000)).strip() or "(none imported yet)"


def review(cfg: Config, db: DB, llm: LLM | None = None) -> str:
    llm = llm or LLM(cfg)
    since = (datetime.now() - timedelta(days=7)).isoformat()
    week = {
        "new_listings_by_source": {r["source"]: r["n"] for r in db.q(
            "SELECT source, COUNT(*) n FROM listings WHERE first_seen != 'baseline' AND first_seen >= ? GROUP BY source", (since,))},
        "new_listing_categories": {r["category"] or "unknown": r["n"] for r in db.q(
            "SELECT category, COUNT(*) n FROM listings WHERE first_seen != 'baseline' AND first_seen >= ? GROUP BY category ORDER BY n DESC LIMIT 12", (since,))},
        "drafts_by_kind": {r["kind"]: r["n"] for r in db.q("SELECT kind, COUNT(*) n FROM drafts WHERE created_at >= ? GROUP BY kind", (since,))},
        "lead_pipeline": pipeline(db),
    }
    outreach = compute_stats(db, cfg.my_addresses).get("outreach", {})
    goals = "\n".join(f"- {g}" for g in cfg.get("growth.goals", []))
    report = llm.cloud(
        f"""Weekly co-founder review for Settleezy, {datetime.now():%d %B %Y}.
{ONE_LINER}

Goals:
{goals}

This week:
{json.dumps(week, ensure_ascii=False)}

Outreach performance (all time, from mailbox):
{json.dumps({k: v for k, v in outreach.items() if 'sample' not in k}, ensure_ascii=False)}

Metric trends (weekly samples, last 8 weeks):
{json.dumps(_metric_series(db), ensure_ascii=False)}

Our growth playbook (strategy we agreed on):
{playbook()[:8000]}

Earlier strategy notes and social media work (imported):
{_imported(cfg)}

Write the review in Markdown:
1. **Scoreboard**: 4–6 numbers vs last week, one line each on what it means.
2. **What's working / what isn't**, with evidence from the data above.
3. **Competitor read**: what Groupon/vspots/Top10/UNiDAYS/Student Beans are adding in Berlin and the opening it creates for us.
4. **3 experiments for next week**: hypothesis, exact action, metric, success threshold.
5. **Marketing plan for next week**: 3 Instagram post/reel ideas with hooks, 1 community/uni action, 1 partner action — matched to the
   current point in the semester calendar (enrolment, arrival, exams, holidays).
6. **Stop doing**: one thing.
7. **Partner targets**: the 5 lead types or named leads to prioritise and why.
Be specific to Berlin and international students. Where data is missing, say what to start tracking.""",
        "You are Settleezy's co-founder and head of growth. You think in experiments, unit economics and Berlin-specific "
        "channels. You are direct and practical.",
        effort="high",
    )
    out = cfg.data_dir / "reports"
    out.mkdir(exist_ok=True)
    path = out / f"growth-{datetime.now():%Y-W%V}.md"
    path.write_text(report, encoding="utf-8")
    return report
