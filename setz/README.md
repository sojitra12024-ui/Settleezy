# SETZ: implementation plan and setup guide

Setz is Claude, configured. Nothing here is an application: it is instructions, agents, skills, knowledge files,
connectors and schedules. This plan is based on what was actually available in Mruddual's Claude account on
15 November 2026.

## What exists today

| Capability | Status |
|---|---|
| Outlook mail, calendar, OneDrive/SharePoint, Teams (Microsoft 365 connector) | Connected. Can read, draft, **send**, create events, read and write files |
| Microsoft To Do | **Not reachable**: the connector has no Tasks permission |
| Gmail, Google Calendar, Google Drive (personal account) | Connected |
| Granola (meeting notes), Notion, Linear, monday.com, Canva, Ahrefs, Firecrawl, GoDaddy | Connected |
| Windsor.ai (Instagram, LinkedIn, Meta analytics), Asana | Installed, connection not finished |
| Instagram | `instagram-dm-outreach` skill plus the browser on the laptop. No analytics connector |
| LinkedIn | No connector |
| Settleezy app and admin panel data | Not connected |
| Scheduled routines | Available. One already runs: "Settleezy venue outreach tracker", Mon/Wed/Fri 08:51 |
| Laptop access from the cloud (Remote Control) | Available, but no folder is shared at the moment |
| Subagents and skills | Available in Claude Code (laptop, web, phone) |
| Voice | Claude apps have voice mode. No wake word on any platform |

## Where each part lives

| Part | Where | Why |
|---|---|---|
| Setz's core instructions | `CLAUDE.md` (Claude Code) and `setz/project-instructions.md` (Claude Project) | Same identity on every surface |
| 25 specialist agents | `.claude/agents/` | Real subagents with their own context |
| Council, decision analysis, radar, accountability report, briefs, inbox workflow | `.claude/skills/` | Reusable workflows |
| Permission enforcement | `.claude/settings.json` | Claude Code must ask before send, invite or delete |
| Knowledge and memory | OneDrive folder `Setz` | One private source, readable by every session, routine and device |
| Schedules | Claude routines | Run in the cloud without the laptop |
| Command Centre (interface, Sett the Fox, voice, council, approvals) | A private Claude artifact with connector access | Live data without a backend; works in the Claude app and on claude.ai |

**Keep business knowledge out of this repository: it is public.** Only instructions live here.

### The knowledge folder (OneDrive)
Created and loaded on 15 Nov 2026 (25 files; index in `Setz/README.md`).
```
Setz/
  README.md    index of collections, record format, statuses
  Personal/    profile.md · writing-style.md · recurring.md
  Settleezy/   company · business-model · product · roadmap · partners · universities · competitors
               growth · kpis · finance · funding · marketing · operations · projects · contracts/
  Log/         decision-register.md · activity.md · discrepancies.md · tasks.md · followups.md
               automation-register.md · briefings/
```
`Personal` and `Settleezy` are the two knowledge environments. Setz reads both; business agents get
`Settleezy` only, plus whatever personal detail their task needs.

## Setz 2.0: phases and state (15 Nov 2026)

| Phase | Content | State |
|---|---|---|
| 1 Core intelligence | Orchestration rules, 25 agents, decision analysis and Decision Register, radar, accountability report, quality gate, knowledge base in OneDrive | Configured. Tested live: knowledge base write and read-back; morning brief on real mailbox and calendar data. Not yet tested: a run with several agents in parallel, the council, a full decision analysis |
| 2 Business intelligence | Dashboard, KPIs, charts, financial intelligence | Not started. KPI definitions exist (`kpis.md`); every value is "Insufficient data" until app, payment and accounting data are available |
| 3 CRM and operations | Lead and partner records, pipeline, tasks and projects | Not started. Open decision: which existing store holds the records |
| 4 Research and growth | Research reports, competitor view, marketing command centre | Agents exist; no reports produced |
| 5 Communication and audio | Executive inbox, meeting intelligence, voice | `setz-inbox` exists, not yet run on the live inbox. Voice: see below |
| 6 Automation | Daily, weekly, monthly routines and alerts | One routine runs (outreach tracker). Three proposed, not created |

Earlier stage list: identity, agents and council are done; the knowledge folder is loaded; routines and the
fox are still open.

## Set-up steps for you

**Laptop (full Setz, with agents and council):**
1. Install Claude Code or use the Code tab of the Claude desktop app, and open this repository.
2. Make sure the Microsoft 365 connector is on. Start with: `morning brief`, `check my Outlook`, or
   `convene the council: should we …`.

**Phone and claude.ai chat:**
1. Create a Project named **SETZ**. Paste `setz/project-instructions.md` into its instructions.
2. Upload the knowledge pack files to the project.
3. Open the SETZ project in the Claude app. The council runs there one voice at a time (no parallel agents).
4. Claude Code sessions on this repository also work from the phone (claude.ai/code), with the full agent set.

## Voice and Sett the Fox (researched November 2026)

**The limit to know first:** no avatar tool can drive or lip-sync the official Claude app. Avatar apps talk to
Claude through an Anthropic API key, billed separately, without the Claude app's projects, memory or connectors.
So the fox is a separate face with a lighter brain unless it is given its own tools.

| Need | Best existing option | Notes |
|---|---|---|
| Animated fox on the desktop | **AIRI** (moeru-ai, free, MIT, `winget install MoeruAI.AIRI`) | Most actively maintained. Supports Claude, Live2D and VRM models. Auto-blink, lip-sync and a floating desktop mode are reported but were not confirmed in official docs |
| Alternative | **Open-LLM-VTuber** (free, MIT) | Confirmed: Claude, transparent always-on-top pet mode, custom Live2D, expressions, hands-free voice, MCP tools. Development has slowed |
| The fox model | Buy or commission a Live2D fox | Ready-made about $30–50; custom about $200–1,500. Ask for expressions and ear and tail movement |
| "Hey Setz" on Windows | No supported wake word in the Claude app | Options: leave the avatar app listening with a mute key (no glue), or a small openWakeWord listener trained on "Hey Setz" that starts voice mode (a small script) |
| Voice with full Setz on the laptop | Claude desktop voice mode, started by a click | Uses your connectors and project |
| Phone | Set Claude as the digital assistant; long-press the side button | One press, then hands-free conversation with connectors. No wake word, phone must be unlocked |
| JARVIS look | Rainmeter skins (free), Wallpaper Engine (paid) | Cosmetic only; not linked to Claude |

Recommended order: phone side button first (two minutes, real Setz). Then decide whether the fox is worth an
API key and a model purchase.

## What stays out of scope
New backends, databases or apps. A dashboard, when Phase 2 starts, is a Claude artifact fed from existing records, not a hosted application. The earlier prototype in `cofounder/` is frozen.
