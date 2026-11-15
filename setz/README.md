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
| 15 specialist agents | `.claude/agents/` | Real subagents with their own context |
| Council, briefs, inbox workflow | `.claude/skills/` | Reusable workflows |
| Permission enforcement | `.claude/settings.json` | Claude Code must ask before send, invite or delete |
| Knowledge and memory | OneDrive folder `Setz` | One private source, readable by every session, routine and device |
| Schedules | Claude routines | Run in the cloud without the laptop |

**Keep business knowledge out of this repository: it is public.** Only instructions live here.

### The knowledge folder (OneDrive)
```
Setz/
  Personal/    profile.md · writing-style.md · recurring.md
  Settleezy/   company.md · business-model.md · product.md · partners.md · universities.md
               competitors.md · growth.md · finance.md · roadmap.md · contracts/
  Log/         decisions.md · tasks.md · followups.md · briefings/
```
`Personal` and `Settleezy` are the two knowledge environments. Setz reads both; business agents get
`Settleezy` only, plus whatever personal detail their task needs.

## Stages

1. **Configure Setz.** Done in this repository: identity, hierarchy, permissions, knowledge rules.
2. **Specialist agents.** Done: 15 agents with scope, tools as they really are, and a fixed report format.
3. **Council.** Done: ten personas, blind first round, debate, consolidated recommendation with dissent.
4. **Connect applications**, in this order:
   1. Outlook and calendar: already connected.
   2. Create the `Setz` folder in OneDrive and load the knowledge pack. *Needs your go-ahead.*
   3. Tasks: use `Setz/Log/tasks.md`. Microsoft To Do needs a connector with the Tasks permission; none is
      available today.
   4. Settleezy website and app: public pages can be monitored now. App and admin data need an export or API.
   5. Instagram and LinkedIn analytics: finish connecting Windsor.ai in claude.ai → Settings → Connectors.
5. **Automations** (Claude routines, Europe/Berlin). *Needs your go-ahead.*
   - Morning brief, weekdays 07:45 → `setz-morning-brief`
   - Evening review, weekdays 18:45 → `setz-evening-review`
   - Weekly intelligence, Mondays 07:15 → `setz-weekly-intelligence`
   - Existing outreach tracker stays as it is.
6. **Voice and Sett the Fox.** See below. Optional, after the rest works.
7. **Test whole workflows**, for example: "Setz, check my Outlook, identify all unanswered partner emails,
   retrieve their previous conversations, prepare personalised responses and organise follow-ups in my calendar."

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
New backends, databases, dashboards or apps. The earlier prototype in `cofounder/` is frozen.
