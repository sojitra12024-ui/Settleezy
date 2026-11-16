---
name: setz-accountability
description: "Report what the Setz agents and routines actually did in a period - tasks, results, failures, audit verdicts, approvals given and refused, time saved. Use for \"what did the agents do last week\", \"agent report\", \"which automations failed\"."
---
# Agent accountability report

Sources, in this order of trust:
1. The routines list and each routine's run history (what really ran, when, and whether it succeeded).
2. `Setz/Log/activity.md` (one line per delegated task: date, agent, task, state, output location, audit verdict,
   approvals and refusals).
3. Evidence of the outputs: Outlook Drafts, files in `Setz/Log/briefings/`, changed knowledge files, the tracker.

## Method
- List work per agent for the period: tasks, state (done / partial / failed), where the output is.
- Verify a sample against the evidence (ask `quality-audit` for more than a handful). An entry with no evidence is
  reported as **unverified**, not as done.
- List failures and partial results first, with cause and fix.
- List actions that were approved, refused, or are still AWAITING APPROVAL.
- Time saved is an **Estimate**: give the method (minutes per task type times count) or leave it out.

## Rules
- If the activity log is empty or does not cover the period, say so. Do not reconstruct activity from memory, and
  do not credit agents with work that a routine or Mruddual did.
- An agent with no logged work is reported as "no activity", not left out.
- End with: which agents are earning their place, which are idle or blocked on missing data, and one change to make.
