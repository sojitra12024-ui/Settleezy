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

### 4. ✅ Find every venue near every campus: OpenStreetMap (built: `sz leadgen`)
- Built in October 2026: `leadgen.py` asks the public Overpass API (with two mirrors as fallback) for cafés,
  restaurants, supermarkets, bakeries, gyms, cinemas, copyshops and bookshops within 800 m of 15 campuses. It stores
  each venue's distance, address and contacts, scores walking distance, and plans walking visit routes.
- Possible upgrade: [osmnx](https://github.com/gboeing/osmnx) (MIT) for real walking-network distances instead of
  straight-line distance, and for pulling student residences (`building=dormitory`) as housing-partner leads.

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

## Next: make Setz itself smarter (brain + voice)

Already built (October 2026):
- long-term semantic memory (`brain.py`, SQLite + embeddings)
- a neural lead-reply model
- a speech analyser (`speech.py`)
- barge-in, noise calibration
- pipeline and week planner

The list below is the researched upgrade path. Stars and licences were checked on GitHub in October 2026; anything
marked *(check)* wasn't confirmed and needs a look before you install.

**Hardware rule:** qwen2.5:7b already uses about 5 of the RTX 3060's 6 GB. Everything below runs on the **CPU**
(ONNX or int8 builds), or uses the GPU only while the LLM is idle.

### Brain (memory, reasoning, learning)

| Order | Project | Licence | What it adds to Setz | How |
|---|---|---|---|---|
| ✅ | [fastembed](https://github.com/qdrant/fastembed) | Apache-2.0 | Multilingual embeddings (`paraphrase-multilingual-MiniLM-L12-v2`; fastembed has no `multilingual-e5-small`), so memory recall works across English and German | **Built:** `pip install -e ".[brain]"` |
| 2 | [sqlite-vec](https://github.com/asg017/sqlite-vec) | MIT/Apache | Vector search *inside* the existing SQLite database. Today's search reads every memory, which is fine up to ~20k; this scales further | Swap `brain._scan` for a `vec0` virtual table |
| 3 | [mem0](https://github.com/mem0ai/mem0) | Apache-2.0 | Extracts facts from emails and chats automatically ("Anna prefers WhatsApp") instead of only from structured records | Local mode with Ollama + sqlite-vec; write its facts into `memories` |
| 4 | [pydantic-ai](https://github.com/pydantic/pydantic-ai) | MIT | Typed tool calling and validated outputs across Ollama and Claude: fewer parsing bugs in triage and extraction | Replace the hand-parsed JSON in `llm.py` callers |
| 5 | [scikit-learn](https://github.com/scikit-learn/scikit-learn) / [CatBoost](https://github.com/catboost/catboost) | BSD / Apache | Calibrated probabilities and better handling of categories (district, source) once there are 200+ outcomes | Train alongside `LeadNet`; keep whichever cross-validates better |
| later | [Graphiti](https://github.com/getzep/graphiti) or [cognee](https://github.com/topoteretes/cognee) | Apache-2.0 | A time-aware knowledge graph (who knows whom, how a partnership evolved) | Only if flat memories aren't enough; Graphiti needs a graph database |
| later | [LangGraph](https://github.com/langchain-ai/langgraph) | MIT | Durable, resumable multi-step flows (sequences with approval steps) with SQLite checkpoints | If sequences grow beyond to-dos |

Skip for now:
- **Letta, CrewAI and AutoGen:** whole agent platforms that would duplicate Setz's deterministic sub-agents.
- **basic-memory:** AGPL licence.

### Voice

| Order | Project | Licence | What it adds | Notes |
|---|---|---|---|---|
| ✅ | [silero-vad](https://github.com/snakers4/silero-vad) | MIT | Real speech detection instead of an energy threshold: fewer false wakes, cleaner barge-in | **Built** (`vad.py`): uses the ONNX model bundled with faster-whisper, no PyTorch, ~0.2 ms per 32 ms chunk |
| 2 | [RealtimeSTT](https://github.com/KoljaB/RealtimeSTT) | MIT | Streaming faster-whisper + VAD + wake word: Setz starts thinking while you're still talking | Replaces `Ears` |
| 3 | [smart-turn](https://github.com/pipecat-ai/smart-turn) | BSD | Detects that you've *finished* a thought (not just paused), so Setz stops cutting you off | Small model, CPU |
| 4 | [kokoro-onnx](https://github.com/thewh1teagle/kokoro-onnx) via [RealtimeTTS](https://github.com/KoljaB/RealtimeTTS) | MIT | Free offline voice as a fallback to ElevenLabs | German voices are limited *(check)*; [Piper](https://github.com/OHF-Voice/piper1-gpl) (GPL-3.0) has good German voices |
| 5 | [openWakeWord](https://github.com/dscripka/openWakeWord) | Apache (code) | A trained "Hey Setz" model: lighter than Whisper phrase spotting | Already supported (`wake_mode = "openwakeword"`); pre-trained models are non-commercial *(check)*, but your own trained model is fine |
| 6 | [whisperX](https://github.com/m-bain/whisperX) + [pyannote](https://github.com/pyannote/pyannote-audio) | BSD / MIT | Analyse recorded partner calls per speaker (your talk ratio, their objections) | Run offline after calls; pyannote models need a free Hugging Face token |
| 7 | [SpeechBrain](https://github.com/speechbrain/speechbrain) emotion model | Apache-2.0 | Model-based emotion/arousal on practice pitches (today: pitch + loudness heuristics) | After a session, never in the live loop |
| later | [Pipecat](https://github.com/pipecat-ai/pipecat) | BSD | A full voice-agent framework (VAD, interruptions, turn-taking) if the custom loop gets hard to maintain | Runs locally; preferred over LiveKit, which needs a WebRTC server |

Licence notes:
- [Parselmouth](https://github.com/YannickJadoul/Parselmouth) (Praat-quality pitch, jitter, shimmer) is **GPL-3.0**. Keep it out of the package and use it only as a separate script.
- openSMILE's licence is **not for commercial use**.
- Setz's analyser uses its own numpy pitch tracker for this reason.

### Dashboard and visualisation
- [3d-force-graph](https://github.com/vasturiano/3d-force-graph) (MIT): a 3D memory graph from `/api/brain`. The current
  command center maps memories onto the 2D brain.
- [sigma.js](https://github.com/jacomyal/sigma.js) (MIT): a fast WebGL view of the lead/partner/university network.
- [MapLibre GL](https://github.com/maplibre/maplibre-gl-js): heatmaps and clustering if the Leaflet campus map gets
  crowded.

### Scheduling and operations
- [OR-Tools](https://github.com/google/or-tools) (Apache): constraint-based week planning (energy levels, travel
  between campuses) and multi-day visit routes (VRP). It would replace the greedy planner once the rules get complex.
- [caldav](https://github.com/python-caldav/caldav) (Apache) / Microsoft Graph `Calendars.ReadWrite`: write the
  planned week straight into your calendar instead of importing the `.ics` file. Setz deliberately keeps read-only
  calendar access today.

### Other tools that still apply

| Need | Project | Why |
|---|---|---|
| See what the AI costs and where it fails | [Langfuse](https://github.com/langfuse/langfuse) (MIT core) | Traces every draft and analysis with cost per day; keeps the Claude bill predictable |
| Better competitor and venue-website reading | [Crawl4AI](https://github.com/unclecode/crawl4ai) | LLM-ready Markdown from JavaScript-heavy sites (UNiDAYS, Student Beans) |
| Agent that can operate websites | [browser-use](https://github.com/browser-use/browser-use) | Occasional form-filling, always with your confirmation |
| Meeting notes → follow-ups | [Meetily](https://github.com/Zackriya-Solutions/meetily) or **Granola** (already connected to your Claude account) | Summary, next steps, follow-up draft and to-dos after every call |
| Your Notion workspace | Notion's hosted MCP server (`https://mcp.notion.com/mcp`) | Setz reads and writes playbooks and partner notes |

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

## More features for Setz (proposed, October 2026)

Setz now covers:
- leads: OpenStreetMap, Instagram, Impressum, lead finder
- the pipeline and outreach sequences
- scheduling and calendar booking
- memory and the neural lead model
- the speech coach
- notifications, phone and widget
- the agents and playbooks

The next ones, ranked by impact on partners and members:

1. **Partner impact reports (retention).** A monthly one-page PDF per partner: redemptions, students reached, Instagram
   mentions, versus last month. Partners renew when they see value. Built from data Setz already has; send as a draft.
2. **Campus ambassador manager.** One student per university with a referral code. Setz tracks sign-ups per code, a
   leaderboard and payouts, and reminds ambassadors before intake weeks. This is the cheapest member acquisition for
   international students.
3. **Intake-season autopilot.** University calendars (Sept/Oct, March/April intakes) trigger pre-arrival campaigns:
   Buddy-platform pitches to International Offices 8 weeks before, welcome-week content, orientation-event
   partnerships.
4. **Offer benchmark.** For each partner, compare the Settleezy offer with the same venue's Groupon/vspots deal, so you
   negotiate "at least as good as Groupon, but for students all semester".
5. **Field-sales mode on the phone.** The visit route of the day, check-in at each venue, a 20-second voice note after
   the visit. Whisper turns it into the lead note, next step and follow-up draft.
6. **Content engine for new partners.** Every partner going live gets an Instagram post/story draft (Canva connector
   with brand templates) and a slot in the content calendar.
7. **Member metrics from the app.** Trials → paid (semester vs year), churn, activation (first saving within 7 days),
   CAC per channel. Connect the app's database or PostHog instead of typing numbers.
8. **Workshop manager.** CV and portfolio workshops: sign-ups, reminders, attendance, and attendee → member conversion.
9. **Investor / board update.** The monthly update drafts itself from the scorecard, pipeline, members and partners.
10. **WhatsApp Business inbox (Chatwoot).** Students already write on WhatsApp; triage and draft answers there too.

## Ground rules for every integration

- **You approve anything outward-facing:** emails, posts, WhatsApp messages and signatures are prepared by Setz and approved by you.
- **Prefer official APIs** (Meta, Google, Microsoft). Unofficial ones get accounts banned.
- **GDPR:** students are the product's users. Keep data in the EU, record consent, have a data processing agreement (AVV) with each processor, and delete on request.
- **Licences:** AGPL tools (Twenty, Documenso, Listmonk, Metabase, Formbricks) are fine to self-host for your own use. Only modifying them *and offering them to others as a service* triggers source-sharing obligations.
