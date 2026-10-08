# Setz roadmap: what to add next

Research done October 2026. Licences and versions change, so check each repository's LICENSE file and README before installing.

Setz today covers: email (voice learning, drafts, follow-ups), the daily plan, to-dos, who to contact, service partners and onboarding, meeting prep, competitor and lead monitoring, Instagram metrics, the dashboard, "Hey Setz" voice and the hologram.

This roadmap fills the gaps in a student-membership business. It is ordered by **impact on revenue and partners per hour of setup**.

---

## The 5 to build next (highest impact)

### 1. One inbox for students: WhatsApp + Instagram DMs + website chat → **Chatwoot**
- **Why:** students already message you on WhatsApp and Instagram, and Setz can't see Instagram DMs today (Meta restricts DM API access to approved apps). Chatwoot is an approved, self-hosted inbox for these channels.
- **Project:** [chatwoot/chatwoot](https://github.com/chatwoot/chatwoot): open-source, self-hosted. It puts website live chat, email, WhatsApp, Instagram, Messenger and API channels in one inbox.
- **What Setz adds on top:**
  - triages every conversation (housing, Anmeldung, discount question, partner enquiry)
  - drafts answers from `knowledge/settleezy.md` as **private notes for you to approve**
  - tracks response times
  - turns repeated questions into new FAQ and guide topics
- **Cost/legal:**
  - WhatsApp Business Platform charges per message by category; for German numbers that is roughly €0.11 for marketing and €0.05 for utility per message (2026 rate card).
  - Replies inside the 24-hour customer-service window are free.
  - If you'd rather build it directly, [PyWa](https://github.com/david-lev/pywa) (MIT) is a well-typed Python client for the official Cloud API.
  - Avoid unofficial WhatsApp Web automation (whatsapp-web.js, Baileys): it breaks WhatsApp's terms and numbers get banned.

### 2. Proof of value for partners: redemption tracking + monthly impact reports
- **Why:** partners renew when they can see what you bring them. Today "redemptions" is a number you type in.
- **What to build** (no new platform needed):
  - a unique **QR code / code per venue** (Python `segno`), which the app or partner logs on use
  - a monthly **impact report per partner**: redemptions, estimated new customers, reach of your posts about them
  - each report as a PDF attached to an **Outlook draft** for the partner
  - redemptions feed the partner health score automatically
- **Bonus:** the same data gives every member a **"you saved €X this month"** message. This is the strongest retention lever for a €40/€70 membership.

### 3. Content engine: plan → design → schedule → measure
- **Why:** Instagram is your main student channel. Setz already knows which posts perform, your partners, competitor deals and the semester calendar.
- **Projects:**
  - [Postiz](https://github.com/gitroomhq/postiz-app): open-source social scheduler for Instagram, TikTok, LinkedIn, Facebook and more, with a public API. Self-hosting newer versions needs Temporal; v2.11.3 was the last release without it.
  - **Canva MCP** (`https://mcp.canva.com/mcp`, official): Setz can generate and edit designs in your Canva brand kit. Your Claude account already has Canva connected.
  - [Remotion](https://github.com/remotion-dev/remotion) for code-generated Reels (e.g. a "What €10 buys you in Berlin" template). Free for companies of up to about 3 people; check its license page as you grow.
- **What Setz adds on top:** a weekly content plan in the growth review, then designs and captions created as **drafts** in Postiz. Nothing posts until you approve it.

### 4. Find every venue near every campus: OpenStreetMap
- **Why:** competitor sites only show venues that already discount. OpenStreetMap shows **every café, restaurant, supermarket, gym and Späti within walking distance of each university**. It is open data, so there's no scraping-ToS risk.
- **How:**
  - query the public Overpass API with `around:<radius>,<lat>,<lon>` for `amenity=cafe|restaurant` and `shop=supermarket`
  - store each venue's distance to the nearest campus
  - pitch with it: "you're 4 minutes from TU Berlin's main campus"
- **Python:** `overpy` wraps the Overpass API. Keep queries small and set a timeout (it's a shared public service).

### 5. Real numbers from the app, no typing: product analytics
- **Why:** the dashboard asks you to type in paying members and trials. Pulling them from the app gives exact trial → paid conversion, churn and savings per member.
- **Projects:**
  - [PostHog](https://github.com/PostHog/posthog): product analytics with iOS, Android and web SDKs, funnels, retention, feature flags and A/B tests. The cloud plan includes a free monthly event allowance.
  - [Metabase](https://github.com/metabase/metabase) (AGPL, free to self-host): if the app has its own database, point Metabase at a read replica for SQL dashboards. Setz then reads the numbers instead of you typing them.
- **Bonus:** student verification. [Hipo/university-domains-list](https://github.com/Hipo/university-domains-list) maps universities to their email domains (e.g. `tu-berlin.de`), so the app can verify students by email the way UNiDAYS does.

---

## Next: grow the partner and university machine

| Need | Project | What it does for Settleezy |
|---|---|---|
| Partner agreements signed online | [Documenso](https://github.com/documenso/documenso) (AGPL, self-host, REST API + webhooks) | "Agreement signed" in the onboarding board moves automatically when the partner signs; templates for venue and Buddy-platform contracts |
| A real CRM when you hire your first sales/partnership person | [Twenty](https://github.com/twentyhq/twenty) (AGPL, REST + GraphQL API) | Setz syncs leads, partners and notes so a team can work the pipeline; custom objects (e.g. "Venue", "University") get APIs automatically |
| Newsletter for members and students | [Listmonk](https://github.com/knadh/listmonk) (AGPL, double opt-in, needs an SMTP provider) | Weekly "new deals near your campus" email, welcome series for trial members, GDPR-friendly double opt-in. You still have to record consent (time, IP, the text shown) |
| Student feedback / NPS | [Formbricks](https://github.com/formbricks/formbricks) (AGPL core) | In-app NPS after 14 days of trial; asks which venues they want next. Setz turns answers into lead priorities |
| Website analytics without cookie banners | [Umami](https://github.com/umami-software/umami) (MIT, cookieless) | Which guides and landing pages bring trials; Setz adds it to the weekly review |
| Glue between everything | [n8n](https://github.com/n8n-io/n8n) (free for internal use under its fair-code licence) or [Activepieces](https://github.com/activepieces/activepieces) (MIT core) | e.g. a new app signup triggers a welcome email, adds the student to Listmonk and updates the dashboard. Setz can call these flows as tools |
| Scheduling page for students and partners | Calendly (already connected) or [Cal.diy](https://github.com/calcom/cal.diy), the MIT community fork of Cal.com, if you want to self-host | Booking links in outreach emails; bookings flow into today's plan |

## Next: make Setz itself smarter

| Need | Project | Why |
|---|---|---|
| Long-term memory of people and preferences | [Mem0](https://github.com/mem0ai/mem0) | Setz remembers "Anna at Café Kranz prefers WhatsApp, renewal talks in January" across sessions and channels |
| See what the AI costs and where it fails | [Langfuse](https://github.com/langfuse/langfuse) (MIT core) | Traces every draft and analysis, with cost per day and quality checks; keeps the Claude bill predictable |
| Better competitor and venue-website reading | [Crawl4AI](https://github.com/unclecode/crawl4ai) | LLM-ready Markdown from JavaScript-heavy sites (UNiDAYS, Student Beans) instead of hand-tuned patterns |
| Agent that can operate websites | [browser-use](https://github.com/browser-use/browser-use) | Fill in partner-portal forms, check listings in the app store, etc. Use for occasional tasks, always with your confirmation, and never for logging into other people's accounts |
| Natural voice with interruptions | [Pipecat](https://github.com/pipecat-ai/pipecat) (BSD-2) | Talk over Setz, interrupt it, have real back-and-forth; works with local Whisper |
| Offline German/English voice (backup for ElevenLabs) | [Piper](https://github.com/OHF-Voice/piper1-gpl) (GPL-3.0, active fork; the old rhasspy repo is archived) | Setz keeps talking without internet or ElevenLabs credits; German voices available |
| Meeting notes → follow-ups | [Meetily](https://github.com/Zackriya-Solutions/meetily) (local transcription) or **Granola** (already connected to your Claude account) | After every partner or university call: summary, agreed next steps, follow-up draft and to-dos created automatically |
| Your Notion workspace | Notion's hosted MCP server (`https://mcp.notion.com/mcp`); the old open-source server is no longer maintained | Setz reads and writes your Notion docs (playbooks, partner notes) |

## Later: run the company

- **Invoicing and bookkeeping (German):** connect lexoffice or sevDesk (both have APIs) if partners pay listing fees. Setz drafts invoices and watches overdue payments.
- **Hiring ambassadors:** a simple applicant pipeline in Twenty or Notion. Setz screens applications against your criteria and books interviews.
- **Fundraising readiness:** Setz keeps a live investor-update draft (members, growth, partners, retention) generated from the same metrics.
- **Backups and secrets:**
  - Back up `cofounder/data/` nightly with [restic](https://github.com/restic/restic) to an encrypted cloud bucket.
  - Move API keys from `.env` into Windows Credential Manager (Python `keyring`).
- **Second laptop / team access:** move Setz to a small EU server (e.g. Hetzner, Germany) so the dashboard and voice work for a team, with sign-in in front of it.

## Business ideas the data makes possible

1. **Campus heatmap:** where members live and study vs where your partners are. Sign venues where students are, not where you happen to be.
2. **Semester autopilot:** a calendar of every partner university's intake, enrolment and exam dates drives outreach, content and Buddy-platform onboarding automatically.
3. **Partner tiers:** venues with the most redemptions get featured placement and you can charge them for it. This is a second revenue line besides memberships.
4. **Trial rescue:** a trial member who hasn't saved anything by day 10 gets a personal nudge with the 3 best deals near their campus.
5. **Referral loop:** each member gets a code ("give a friend a free month"), and Setz reports which nationalities and universities refer the most.
6. **Price testing:** test €40 vs €45 per semester, or a cheaper "first month" offer, with feature flags in PostHog, and let the data decide.

## Ground rules for every integration

- **You approve anything outward-facing:** emails, posts, WhatsApp messages and signatures are prepared by Setz and approved by you.
- **Prefer official APIs** (Meta, Google, Microsoft). Unofficial ones get accounts banned.
- **GDPR:** students are the product's users. Keep data in the EU, record consent, have a data processing agreement (AVV) with each processor, and delete on request.
- **Licences:** AGPL tools (Twenty, Documenso, Listmonk, Metabase, Formbricks) are fine to self-host for your own use. Only modifying them *and offering them to others as a service* triggers source-sharing obligations.
