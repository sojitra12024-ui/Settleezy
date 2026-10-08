# Setz: Settleezy's AI chief of staff

**Setz** is your personal assistant and growth co-founder. It runs on your laptop on top of [OpenJarvis](https://github.com/open-jarvis/OpenJarvis). Say **"Hey Setz"**, or open the dashboard.

Settleezy helps students in Berlin, especially international students, save money and settle in: a membership app (savings at restaurants, cafés, grocery stores, activities and events, plus guides, an expense and savings tracker, a document vault and emergency numbers), workshops, and the Buddy platform for universities. The assistant knows this from `knowledge/settleezy.md`.

| It does | How |
|---|---|
| **Learns how you write** (English + German) and what gets replies | Reads your sent mail, measures greetings, sign-offs, length, du/Sie, response times, and outreach reply rates by day/hour/follow-up, then writes `data/voice_profile.md` + `data/email_playbook.md` |
| **Drafts replies, follow-ups and partner outreach in your voice** | Saved in **Outlook → Drafts**, tagged with the category **"Settleezy AI"**. It **cannot send**: the app never gets Microsoft's send permission |
| **Daily brief** | Meetings (Outlook + Calendly), replies owed, follow-ups due, Instagram comments, new competitor listings, best leads, and the 5 things to do next. Saved as Markdown, shown on the dashboard, read aloud if you want |
| **Watches competitors** | Groupon, vspots, Top10 Berlin, UNiDAYS, Student Beans: new Berlin listings, stored in a database |
| **Builds your lead list** | Every merchant/brand found becomes a scored lead; it finds email/Instagram from their website and Impressum; plus 30+ Berlin universities and student-service seeds |
| **Thinks like a co-founder** | Weekly growth review: scoreboard, what's working, competitor read, 3 experiments, next week's marketing plan (`playbook/berlin-growth-playbook.md` is its strategy; `knowledge/settleezy.md` is what it knows about Settleezy) |
| **A team of specialist agents** | Nine agents each watch one area and **report to Setz**: **Hermes** (inbox & follow-ups), **Atlas** (service partners), **Scout** (competitor watch), **Hunter** (leads & outreach), **Nova** (Instagram & content), **Quant** (growth analyst: goal forecasts, week-over-week drops), **Chrono** (schedule, prep, overdue to-dos), **Campus** (international students, university intake calendar, Buddy platform) and **Sentinel** (connections & stale data). Setz merges their reports into one briefing and priority list and turns alerts into to-dos. Runs every 2 hours, or `sz agents run` |
| **Command center** | http://127.0.0.1:8765/command: a mission-control screen showing Setz's neural "brain" with the nine agents orbiting it (pulses fly in as each one reports), the agent roster with status lights, Setz's briefing and priorities, goal gauges, a live thought stream of findings, charts (members, Instagram, competitor activity, operations), an ask-Setz console, and a report drawer per agent with one-click to-dos |
| **Runs your day** | **Today's plan**: your daily routine (config `[routine]`) laid around today's meetings, with the right people and tasks slotted into each block, plus "focus now". **To-dos** you can add by typing or voice ("Hey Setz, remind me to send HTW the deck on Friday"); Setz also adds its own (stuck onboarding, renewals, follow-up after every meeting). **Who to reach out to**: everyone you should contact now, ranked, each with the reason and a one-click draft |
| **Tracks your service partners** | Every partner moves through onboarding: agreed → agreement signed → member offer set up → listed in the app → launch promo → live. A **health score** flags stuck steps, partners you haven't spoken to in 30 days, live partners without redemptions, and renewals due. Shows partners onboarded this week/month, average days to go live, and live partners by type (venues, universities on the Buddy platform, housing, brands, services) |
| **Prepares you for meetings** | One-page prep for any meeting: who's coming, what you know about them (lead/partner record), recent emails, and a suggested goal, agenda, ask and objections |
| **Weekly scorecard** | This week vs last week: outreach sent, replies, meetings, partners gone live, new leads, to-dos done |
| **Imports your past work** | `sz import <folder>` pulls in exported contacts, leads and partner lists (CSV/XLSX/JSON) and earlier outreach emails, social posts and strategy notes (MD/TXT/DOCX), e.g. from your Claude projects. Setz learns from them |
| **Voice + hologram** | **"Hey Setz"** wake phrase, Whisper speech recognition on your GPU, answers spoken in your chosen **ElevenLabs** voice (English or German). Setz appears as an animated **low-poly hologram robot**: it floats, blinks, follows your cursor, tilts its head and waves when listening, looks up with orbiting dots while thinking, and its eyes, mouth and fins move with its voice. Full screen at http://127.0.0.1:8765/hologram also shows your channels (Outlook, Instagram, calendar, competitors, partners, AI, voice) flowing into Setz with their live status |
| **Dashboard** | http://127.0.0.1:8765, in five tabs. **Today**: Setz, KPIs, plan, to-dos, who to contact, inbox, brief. **Partners**: onboarding board, partner health, add/edit partners, log contacts. **Growth**: goals, scorecard, trends for any metric, numbers from the app, AI-draft funnel, reply rate by weekday, Instagram, weekly review. **Leads & competitors**: searchable leads with notes and one-click drafts, competitor charts. **Insights**: connections, Instagram posts and comments, upcoming meetings with prep, import |
| **Connection doctor** | `sz doctor` (and the dashboard's Connections card) tests Outlook, Instagram, Calendly, Claude, Ollama and ElevenLabs with real calls and tells you exactly how to fix anything broken |

**Privacy:** Raw email is read by a local model (Ollama on your RTX 3060). Only what's needed for drafting and analysis goes to the Claude API, and email addresses, phone numbers and IBANs are replaced with placeholders first. Everything is stored locally in `cofounder/data/` (git-ignored).

---

## Setup (Windows, about 45 minutes)

### 1. Install the basics
- **Python 3.11 or 3.12**: <https://www.python.org/downloads/>. Tick **"Add python.exe to PATH"**.
- **Ollama**: <https://ollama.com/download/windows>
- **Git**: <https://git-scm.com/download/win>
- **OpenJarvis** (optional but recommended, for the chat/voice brain):
  `irm https://open-jarvis.github.io/OpenJarvis/install.ps1 | iex`

### 2. Install the co-founder
```powershell
git clone https://github.com/sojitra12024-ui/Settleezy.git
cd Settleezy\cofounder
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned   # once, if PowerShell blocks scripts
.\scripts\install.ps1
```
This creates `.venv`, installs everything (including voice), downloads the local model `qwen2.5:7b-instruct` (about 4.7 GB, which fits your 6 GB GPU), and creates `config.toml` and `.env`.

Open **`config.toml`** and set your name and **every email address you send from** under `[me]`. Adjust `[growth] goals`.
Then fill in the TODO lines in **`knowledge/settleezy.md`** (website, how members redeem, what listing costs a venue, ...). Drafts only state facts from that file, so the more you fill in, the better the outreach.

### 3. Connect Outlook (Microsoft 365 business)
Register a small app in your Microsoft tenant (you only do this once):
1. Go to <https://entra.microsoft.com> → **App registrations** → **New registration**.
   Name: `Settleezy Cofounder`. Account type: **this organizational directory only**. Redirect URI: leave empty. **Register**.
2. **Authentication** → *Advanced settings* → **Allow public client flows: Yes** → Save.
3. **API permissions** → *Add a permission* → **Microsoft Graph** → **Delegated** → tick `User.Read`, `Mail.ReadWrite`, `Calendars.ReadWrite` (lets Setz book meetings you approve; never tick `Mail.Send`) → Add → **Grant admin consent**.
   (Do **not** add `Mail.Send`. This is what makes "drafts only" guaranteed.)
4. Copy **Application (client) ID** and **Directory (tenant) ID** into `.env` as `MS_CLIENT_ID` and `MS_TENANT_ID`.
5. Sign in: `.\.venv\Scripts\sz.exe auth outlook` and follow the code shown.

### 4. Claude API key
Create a key at <https://platform.claude.com> and put it in `.env` as `ANTHROPIC_API_KEY`.
Rough cost at default settings: about €15–30/month (about 15 drafts per day plus briefs and a weekly review). For about half that, set `cloud_model = "claude-sonnet-5-5"` in `config.toml`.

### 5. Calendly
Calendly → **Integrations & apps** → **API and webhooks** → **Personal access tokens** → generate → `.env` `CALENDLY_TOKEN`.

### 6. Instagram Business
The account must be an Instagram **professional** (Business or Creator) account **linked to a Facebook Page** (Instagram app → Settings → Accounts Center).
1. <https://developers.facebook.com> → **Create app** (type *Business*) → add **Instagram** (Instagram API with Facebook Login). Copy the **App ID** and **App Secret** (App settings → Basic) into `.env` as `FB_APP_ID` and `FB_APP_SECRET`.
2. Run `.\.venv\Scripts\sz.exe auth instagram` and follow the prompt: in the **Graph API Explorer** pick your app, add `instagram_basic`, `instagram_manage_insights`, `instagram_manage_comments`, `pages_show_list`, `pages_read_engagement`, click **Generate Access Token**, and paste it.
3. The command exchanges it for a **Page token that doesn't expire** (unless you change your Facebook password or remove the app), finds your Instagram account id, and writes both to `.env`.
4. Check: `sz doctor` should show `OK  Instagram  @yourhandle · … · token never expires`.

DMs are not included: Meta requires app review for message access.

### 6b. Voice (ElevenLabs)
1. In ElevenLabs, open the voice you chose and click **+ / Add to My Voices** (library voices must be in your account before the API can use them):
   - main: <https://elevenlabs.io/voices/CUvmi6RSy4BQr6vnMyEw>
   - alternative: <https://elevenlabs.io/voices/r1KmysJdVYZjJCm4mL3b>
2. Profile → **API keys** → create a key → `.env` `ELEVENLABS_API_KEY`.
3. `config.toml` `[voice]` already points at both voices; swap `elevenlabs_voice_id` and `elevenlabs_voice_id_alt` to change which one speaks. `eleven_multilingual_v2` speaks English and German; `eleven_flash_v2_5` answers faster.
4. Test: `sz brief --speak`. Open http://127.0.0.1:8765/hologram (with the dashboard running) to watch Setz while it speaks. Without a key, Setz falls back to Windows voices and the hologram still animates.

### 7. Connect OpenJarvis (chat + voice brain)
Copy `openjarvis\config.toml` and `openjarvis\mcp-servers.json` to `%USERPROFILE%\.openjarvis\`, then edit `mcp-servers.json` so `"command"` is the full path, e.g.
`"C:\\Users\\you\\Settleezy\\cofounder\\.venv\\Scripts\\sz.exe"`.
Now `jarvis` (the OpenJarvis chat) can use Setz's tools: *"what's my plan today?"*, *"who should I contact?"*, *"add a to-do: call Kranz tomorrow"*, *"how are my partners doing?"*, *"prep my next meeting"*, *"draft replies"*, *"what did Groupon add in Berlin?"*.

### 7b. Import your past work (Claude projects and other files)
Setz can't open your Claude projects directly, so export their files once:
1. In each project (**Settleezy**, **Partnership and outreach**, **Social media**), download the files you want Setz to learn from: contact and lead lists, partner sheets, outreach emails and templates, captions and content plans, strategy docs. For results that only exist in a chat, ask Claude in that project to "export all contacts and leads from this project as a CSV" and to "put the outreach emails we wrote into one document", then download them.
2. Put everything in one folder, e.g. `Downloads\settleezy-exports`.
3. Run `.\.venv\Scripts\sz.exe import "C:\Users\you\Downloads\settleezy-exports"`.

What happens: contacts and leads are merged into your lead list (columns like *Company, E-Mail, Instagram, Category, Status, Contact person, Notes* are recognised in English and German). Rows whose status says signed or live become **partners** with an onboarding record. Outreach emails teach Setz your voice and are reused when drafting. Social posts and strategy notes feed the weekly growth review and content plan. Run it again whenever you have more; nothing is duplicated.

### 8. First run
```powershell
.\.venv\Scripts\sz.exe doctor            # every connection tested for real; fix anything marked !!
.\.venv\Scripts\sz.exe sync              # pulls 12 months of mail (first time: a few minutes)
.\.venv\Scripts\sz.exe learn             # builds your voice profile + outreach playbook  -> data\
.\.venv\Scripts\sz.exe scrape --inspect  # check what each competitor page yields, saves nothing
.\.venv\Scripts\sz.exe scrape            # first real scan = baseline; later scans report what's NEW
.\.venv\Scripts\sz.exe morning           # Outlook + Calendly + Instagram + drafts + brief
.\.venv\Scripts\sz.exe dashboard         # http://127.0.0.1:8765
```
Read `data\voice_profile.md`. If anything sounds off, edit it; the drafts follow that file.

### 9. Put it on autopilot
```powershell
.\scripts\register_tasks.ps1 -Speak -Voice
```
| When | What |
|---|---|
| Weekdays 07:45 | `morning`: check connections, sync, track, write drafts, build the brief (read aloud with `-Speak`) |
| Every 2 h, 09–19 | Outlook sync + tracking (which AI drafts you sent, which got replies) |
| 08:15, 14:15, 20:15 | Competitor scan → new listings → leads |
| Daily 13:00 | Lead enrichment (website + Impressum contacts) |
| Weekdays 14:30 | Second round of drafts |
| Daily 18:00 | Instagram metrics + unanswered comments |
| Sunday 20:00 | Re-learn your voice from the week's sent mail |
| Monday 07:15 | Co-founder growth review |
| At logon | Dashboard, and Setz ("Hey Setz") with `-Voice` |

Logs are in `data\logs\`.

---

## Everyday commands
```
sz agents run               run the agent team; Setz briefs you   (sz agents | sz agents run --agent atlas)
sz plan                     today's plan: routine + meetings + what to do in each block
sz todo add call Kranz tomorrow       sz todo        sz todo done 3        sz todo snooze 3 2
sz reach                    who to contact now, and why
sz partners                 partner list with stage + health   (sz partners stats | add <name> --kind venue | advance <id> | set <id> offer "10% off")
sz prep                     one-page prep for your next meeting
sz scorecard                this week vs last week
sz import <folder>          bring in exported contacts, leads, outreach and social files
sz doctor                   test every connection, with fixes
sz kpi paying_members 240   record a number from the app (also on the dashboard)
sz brief --speak            today's brief, read aloud
sz triage                   what needs you (no drafting)
sz drafts --limit 5         write drafts now
sz leads list --kind merchant
sz leads draft 42           outreach draft for lead #42 (Outlook → Drafts)
sz leads status 42 contacted --note "met at Kranz, follow up Friday"
sz leads add --name "Café X" --kind-new merchant --website cafe-x.de
sz leads export             data\leads.csv
sz growth                   co-founder weekly review now
sz voice                    Setz: say "Hey Setz …" (or --no-wake for push-to-talk); hologram at /hologram
```

## Lead finder: ask for leads in plain words

Type or say what you want, in English or German. Setz filters the whole lead database and gives you the full list:

```
sz find vegan cafés near HU with email, not contacted
sz find "cheap restaurants in Kreuzberg within 500 m" --csv kreuzberg.csv
sz find Spätis in Neukölln mit Telefonnummer --enrich
"Hey Setz, find me gyms near TU that aren't on any competitor"
```

The dashboard has the same search at the top of **Leads & competitors**, with example chips, an Excel export, and
buttons to read the Impressum or start outreach for the venues you tick.

**Every row shows:**
- name, address, district and postcode
- distance to the nearest campus
- category and sub-category (cuisine, vegan, wifi, outdoor seating…)
- price level (from the menu on the venue's site, or estimated, marked \*)
- email and phone, plus the owner / managing director and legal name from the **Impressum**
- Instagram handle and followers
- which platforms it is already listed on (Groupon, vspots, Top10, UNiDAYS, Student Beans, OSM, Instagram)
- whether you've **contacted it before** (searched by address *and* domain in your mailbox, drafts and pipeline)
- its **likelihood of joining** (neural reply model × your real conversion rates)

**It understands:**
- categories and cuisines
- campuses ("near HU"), districts, postcodes and distances
- "with email/phone/Instagram"
- "not contacted"
- "on Groupon" / "not on any competitor" / "only on Instagram"
- "cheap" / "€€" / "upscale"
- "popular" / "over 5k followers"
- "closest" / "most likely to join" / "top 20"

If there are few results near a campus, `--discover` (or the checkbox) scans OpenStreetMap there first. Existing
partners are hidden unless you add "include partners".

**Impressum enrichment** runs by itself every day for the best leads with a website. It reads the homepage, the menu
page and the Impressum.

## Instagram-only venues and brands

Many of the best student spots and small Berlin brands with their own products live on Instagram and barely exist on
Google or Maps. `sz instagram discover` (and automatically on Tuesdays and Fridays) finds them through the official
Instagram Graph API with your Business account, so there's no scraping and no risk to the account:

1. It reads the top posts of Berlin hashtags (#berlinfood, #kreuzbergfood, #berlinvegan, #madeinberlin…; configurable
   under `[instagram] discovery_hashtags`).
2. It collects the accounts those posts @mention.
3. It looks each account up with **business discovery**: followers, bio, website, recent engagement.
4. It sorts them into a venue (address, opening hours…), a brand with its own product (shop, Versand…) or a creator
   (blogger, collab, PR…). An address in the bio becomes the lead's address and district. Accounts under 300
   followers are skipped and not looked up again for a month.

Instagram allows 30 different hashtags per 7 days; Setz rotates within that budget (`sz instagram` shows what's
left). To add accounts you spotted yourself, use `sz instagram add @handle https://instagram.com/other` or the box
in the Lead finder.

Then ask for them:
- "popular venues only on Instagram"
- "vegan brands with own products"
- "food creators with over 10k followers"

Creators are for marketing collaborations and only appear when you ask for them. Follower counts of Instagram leads
refresh with every Instagram sync, and big followings raise a lead's score.

*Meta setup:* hashtag search and business discovery work for your own app in development mode. Add the
**Instagram Public Content Access** feature in the Meta app dashboard if hashtag search answers "permission denied".

## Meeting requests → your calendar

After every Outlook sync Setz finds messages that ask to meet, in English or German ("can we have a quick call?",
"hätten Sie nächste Woche Zeit für ein Telefonat?", "are you free Tuesday at 2?"). It also catches Instagram comments
asking to collaborate.

**On the Today tab (or `sz meetings`, or "Hey Setz, any meeting requests?"):**
- **Times they proposed** are checked against your calendar and shown with ✓ if you're free.
- **Otherwise, 3 free slots** on different days: within working hours, with a 15-minute buffer around other
  meetings, preferring your calls block, and skipping days that already have too many meetings.
- **"Draft reply with slots"** writes the answer in your language into Outlook **Drafts**; Setz never sends. For
  Instagram it copies the text for you to paste into the DM.
- **Clicking a slot books it** in Outlook, with a Teams link and an invitation to them (untick "send them the
  invite" to only block your own calendar). The meeting appears in today's plan, the lead moves to *meeting*, and a
  prep to-do is created. By voice: "book it" / "book option 2".

Booking needs the `Calendars.ReadWrite` permission (README step 3), then run `sz auth outlook` once more. Settings are
in `[scheduling]`; set `[outlook] calendar_write = false` to keep the calendar read-only. Instagram DMs can't be read
(Meta restricts that API), so Setz uses the comments it can see.

## Always on: app, pop-ups, phone and widget

**It's always running.** `register_tasks.ps1` starts the dashboard at logon with no time limit and restarts it if it
ever stops. Add `-Widget` (floating window) and `-Voice` ("Hey Setz") to start those too.

**Pop-ups wherever you are.** Setz notifies you about:
- new meeting requests
- leads that replied
- meetings starting in 10 minutes
- the agents' top alerts
- venues it found
- failed jobs

They appear:
- as **Windows notifications** (`pip install -e ".[notify]"`), even over other apps
- as browser pop-ups when the dashboard tab is in the background
- in the 🔔 bell, which keeps the history

The dashboard and command center update live the moment a job finishes. Quiet hours (22:00–07:30) keep pop-ups
silent. Test it with `sz notify`.

**Floating Setz widget.** `sz widget` opens a small always-on-top window with the animated robot, what Setz is doing
or saying, the latest notification and an ask box. It stays in front while you work in other apps (install with
`pip install -e ".[widget]"`; without it the widget opens as a small Edge app window).

**Install it as an app.** In Edge/Chrome click *Install app* in the dashboard header. Setz gets its own window, a taskbar
icon and shortcuts to the command center and Lead finder.

**Setz on your phone.** Use [Tailscale](https://tailscale.com) (free): a private network between *your* devices that
gives you HTTPS and opens nothing to the internet.
1. Install Tailscale on the laptop and the phone and sign in with the same account.
2. On the laptop: `tailscale serve --bg 8765`. It prints an address like `https://laptop.tailXXXX.ts.net`.
3. Open that address on the phone and use *Add to Home Screen* / *Install app*.
4. In the 🔔 bell, tap **Enable pop-ups**. With `pip install -e ".[notify]"` on the laptop, Setz then sends real push
   notifications to the phone, even when the app is closed.

Tailscale identifies you, so no PIN is needed. To add one anyway, set `[dashboard] access_pin`. Any device that isn't
the laptop must then enter it once (5 tries per 5 minutes). On plain home Wi-Fi without Tailscale you can set
`host = "0.0.0.0"` (a PIN is then required), but browsers allow push notifications only over HTTPS.

## Lead generation, pipeline and your week

**Find leads near campuses.** `sz leadgen scan` asks OpenStreetMap for every café, restaurant, supermarket, bakery,
gym, cinema, copyshop and bookshop within 800 m of 15 Berlin campuses and adds them as leads with address,
distance and any website/email/Instagram on the map. Venues close to a campus score higher. Chains are skipped.
Nothing to install: it uses the free Overpass API (with two mirrors as fallback), at most once a day per campus, and
runs by itself every Sunday night. Then `sz run enrich` finds missing emails from each website's Impressum.

**Pipeline.** Every stage change is recorded, so Setz knows your real conversion rates (contacted → replied →
meeting → partner) and how long each step takes. From `[pipeline] partner_goal_per_month` it works back to how
many first contacts, replies and meetings you need *this week*, forecasts how many partners the current pipeline will
bring, flags deals stuck too long in a stage and every deal without a next step.

- **Outreach sequences:** day 0 email → day 2 Instagram DM → day 5 follow-up → day 9 visit → day 14 last email
  (universities get their own cadence). Each step becomes a to-do on its day; ticking off the first one marks the lead
  as contacted; a reply stops the rest automatically. "Auto-start" (or every Monday 07:30) starts sequences for your
  best new leads up to the weekly capacity your goal needs.
- **Visit routes:** a walking loop from each campus through the best venues to visit, with a Google Maps link.

**Your week.** `sz week` (or the Schedule tab) time-blocks your open to-dos into your routine around real meetings:
outreach into the outreach block, visits into the calls block, overdue and high-priority first, long tasks split
into 30+ minute chunks. It shows the load per day, what doesn't fit, and suggestions: days with too many meetings,
no 90-minute focus stretch, the weekday your emails get the most replies, visits to batch near one campus, pipeline
targets you're behind on. "Apply to to-dos" gives undated to-dos their planned day; "Download .ics" imports the plan
into Outlook. Today's plan on the Today tab shows the to-dos scheduled into each block.

```
sz leadgen scan [--campus TU]     sz leadgen coverage       sz leadgen list --campus HTW
sz pipeline                       sz pipeline stale         sz pipeline routes TU
sz pipeline auto                  sz pipeline start 42      sz pipeline next 42 send the agreement on Friday
sz week                           sz week --next            sz week --apply   sz week --ics week.ics
```
Voice: "Hey Setz, plan my week", "how's the pipeline?".

## Speaking coach and voice features

Setz listens to *how* you speak, not just what you say: pace (words per minute), pauses, filler words in English and
German (ähm, halt, quasi, um, like, you know…), hedging ("I think", "vielleicht"), pitch variation (monotone or
engaging), loudness, background noise and clarity. You get a 0–100 delivery score with the three most useful tips.

- **Practise a pitch:** "Hey Setz, practise my pitch" (or the *Record a practice pitch* button under Insights, or
  `sz voice practice`). Speak for 30–90 s. Setz scores your delivery and Claude coaches the content: structure
  (hook → problem → offer → proof → ask), the line to say instead, one delivery fix. Progress is charted over time.
- **Recorded calls:** `sz voice analyse call.m4a --coach`.
- **Ask:** "Hey Setz, how's my speaking?" → your trend over the last 30 days.
- **Speech detection (Silero VAD):** a small neural network tells speech from background noise (typing, fans,
  traffic). Setz starts recording when you start talking and doesn't cut you off at short pauses. Only real speech, not
  a cough or a door, can interrupt it. It comes with the voice extras (faster-whisper ships the model; no PyTorch
  needed). `[voice] vad = "energy"` switches back to the loudness threshold.
- **Smarter listening:**
  - Setz measures the room noise at start and sets the mic threshold above it.
  - With `[voice] barge_in = true` (use a headset) you can talk over Setz and it stops to listen.
  - Every normal command is measured quietly, so the trend builds up without extra effort.

## Operations: Setz runs the business with you

The **command center** (`/command`) is Setz's operations room.

**Brain animation:**
- breathes calmly when idle
- ripples inward while it **listens**
- fires fast violet synapses while it **thinks**
- sends out waves in time with its real voice volume while it **speaks**

Every agent that's running a job gets a rotating ring with data flowing into the brain.

**Operations panel:**
- **Missions:** the open work across the business, most urgent first, each with its owner agent and a Run/Open
  button:
  - meeting requests and leads waiting for you
  - outreach steps due and replies owed
  - partners at risk
  - leads without contact details
  - pipeline behind target
  - campuses not scanned yet
  - brain freshness
- **Jobs:** every scheduled job with last run, next run, live status and the last error.
- **Playbooks:** multi-step runs Setz does on its own, with live progress:
  - *Prospecting run*: campus scan → Impressum enrichment → Instagram discovery → brain → start outreach
  - *Inbox zero*: sync → match replies → drafts
  - *Market intelligence*: competitors, Instagram, agent reports, growth review
  - *Brain refresh*

The same from anywhere:
- `sz ops`, `sz ops --playbook prospecting`
- "Hey Setz, run prospecting", "what are you working on?"
- the MCP tools `operations_status` / `run_playbook`

You get a pop-up when a playbook finishes.

## Setz's brain: memory and a neural network

**Memory.** Setz keeps long-term memories (facts, preferences, episodes, insights and your conversations) and finds them
by meaning: ask "how should I contact Brew Lab?" and it recalls that Lea prefers WhatsApp. It learns by itself every
morning from partners, lead notes, stage changes, meetings, the knowledge base and your pipeline/planner analytics, and
you can teach it: "Hey Setz, remember that Anna at Café Kranz wants a monthly report", or the Remember box in the
command center. Every answer from Setz (voice, dashboard, OpenJarvis) includes the relevant memories.
"What do you know about HTW?" reads them back.

For the best recall in English *and* German, install the multilingual embedder (CPU only, no GPU needed):
`pip install -e ".[brain]"`. The model (`paraphrase-multilingual-MiniLM-L12-v2`, ~220 MB) downloads once into
`data/models/` and then works offline, so a German question ("Wann soll ich Unis kontaktieren?") finds an English
note. Without it Setz uses Ollama (`ollama pull bge-m3`) or a built-in offline embedder. `sz doctor` shows which one is
active. If the download fails (offline), Setz waits a day before retrying instead of slowing down every start;
`sz doctor` retries straight away.

**Neural network.** A small neural network (14 inputs → 8 hidden units → reply chance) learns from your own outreach
which leads answer: kind, category, contact channels, distance to campus, presence on competitor sites. It is
cross-validated before it is trusted (AUC ≥ 0.55), shows what predicts a reply, ranks which leads get the next
outreach sequences, and shows each lead's reply chance on the Pipeline tab. It starts once ~20 contacted leads have an
outcome. The command center draws the network with its real weights, and the brain's neurons are your memories:
they light up when Setz recalls them.

```
sz brain                     sz brain learn               sz brain model
sz brain recall who prefers WhatsApp                     sz brain remember Anna wants monthly reports --subject "Café Kranz"
```

## What's next
See [`ROADMAP.md`](ROADMAP.md) for researched open-source projects that could be added next: a WhatsApp + Instagram DM inbox, partner impact reports, a content engine, OpenStreetMap venue discovery, app analytics, CRM, e-signatures, newsletters, memory and more, ranked by business impact.

## Tuning
- **Competitor patterns:** `competitors.toml`. Run `sz scrape --inspect --site groupon` after any change. If a site only renders with JavaScript, set `render = true` and run `pip install -e ".[render]"` then `playwright install chromium`.
- **Follow-up cadence:** `config.toml` `[followup] cadence_days`. `sz learn` reports what your own data says works.
- **German speech:** set `whisper_model = "medium"` for better German recognition. (Only if you don't use ElevenLabs: install a German Windows voice under Settings → Time & language → Speech.)
- **Daily routine:** `config.toml` `[[routine.blocks]]` (start, end, title, focus = inbox | outreach | partners | content | calls | review, days). With `announce = true`, `sz voice` tells you when each block starts and who to begin with.
- **Partners:** `[partners] stage_stuck_days` / `checkin_days` decide when a partner needs attention.
- **Wake phrase:** "Hey Setz" works out of the box (Whisper listens for it). Raise `voice.mic_threshold` if background noise keeps waking the mic. For an always-on, lower-power wake word you can train a custom "hey setz" openWakeWord model and set `wake_mode = "openwakeword"` plus `wake_model`.
- **Goals:** `config.toml` `[[growth.targets]]`, where each goal points at a metric (`manual.*` numbers you enter, `ops.*` computed daily, `instagram.*`).
- **Hologram on a second screen:** open http://127.0.0.1:8765/hologram full screen (F11) on a second monitor.
- **GPU speech recognition:** if Whisper falls back to CPU, run `pip install nvidia-cublas-cu12 "nvidia-cudnn-cu12==9.*"` in the venv.
- **What goes to the cloud:** set `cloud_enabled = false` to keep everything local. Drafting quality drops, but nothing leaves your laptop.

## How it fits together
```
Outlook ─┐                                   ┌─> Outlook Drafts ("Settleezy AI")
Calendly ├─> sz jobs ─> SQLite (data/) ──────┼─> Daily brief (md + voice)
Instagram┘      ▲            │               ├─> Dashboard :8765
Competitors ────┘            │               └─> Weekly growth review
Partners · to-dos · routine ─┘└─> MCP server ─> OpenJarvis chat  ·  Setz voice + hologram
local model (Ollama): triage, extraction  ·  Claude API: drafts, analysis (redacted)
```

## Rules it follows
Drafts only. Robots.txt, rate limits and an honest bot name when scraping. Business contact data only. UWG-friendly outreach: personal, relevant, an opt-out line, stop at "no". See `playbook/berlin-growth-playbook.md`.

## Development
```
pip install -e ".[dev]"
pytest
```
