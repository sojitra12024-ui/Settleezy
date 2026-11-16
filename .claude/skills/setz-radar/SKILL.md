---
name: setz-radar
description: "Proactive scan for risks and opportunities Mruddual has not asked about - overdue tasks, unanswered emails, quiet partners, pending agreements and forms, upcoming deadlines, decisions due for review, unusual KPI movement, stale knowledge, failed routines. Use for \"what am I missing\", \"radar\", \"anything I should know\", and inside the daily briefs."
---
# Radar

Read-only scan. Report only what crosses a threshold; an empty section is left out. Never invent an item to fill a list.

| Check | Source | Threshold |
|---|---|---|
| Overdue tasks | `Setz/Log/tasks.md` | past due date |
| Emails waiting for his reply | Outlook inbox vs sent | more than 2 days, from a person (not newsletters or system mail) |
| His emails without a reply | Outlook sent, `Setz/Log/followups.md` | follow-up date reached; never propose a fourth follow-up |
| Quiet partners | `Setz/Settleezy/partners.md`, tracker, mail | no contact in 30 days |
| Agreements and application forms | mail, `partners.md` | sent and pending more than 7 days |
| Deadlines | calendar, `tasks.md`, `funding.md` | within 7 days |
| Decisions | `Setz/Log/decision-register.md` | review date passed |
| KPIs | `Setz/Settleezy/kpis.md` | unusual change, or "insufficient data" for a KPI he relies on |
| Knowledge | all knowledge files | `CHECK` markers, status not Verified, or last verified more than 30 days ago |
| Routines | routines list | last run failed, or did not run on schedule |
| Meetings | calendar, Granola | past meeting without a recap; meeting within 24 hours without a brief |
| Opportunities | mail and research already in hand | inbound interest unanswered, event or funding deadline that fits |

## Output
Group by urgency: **Act today**, **This week**, **Keep in view**. Each line: what, evidence (source and date),
proposed action with its permission level, and the agent that would do it. Finish with the count of items checked
per source and any source that could not be reached. Offer to prepare the autonomous actions; do nothing above
Level 1 without approval.
