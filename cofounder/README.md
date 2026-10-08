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
3. **API permissions** → *Add a permission* → **Microsoft Graph** → **Delegated** → tick `User.Read`, `Mail.ReadWrite`, `Calendars.Read` → Add → **Grant admin consent**.
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
