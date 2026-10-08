# Settleezy co-founder

Your personal assistant and growth co-founder. It runs on your laptop next to [OpenJarvis](https://github.com/open-jarvis/OpenJarvis).

Settleezy helps students in Berlin, especially international students, save money and settle in: a membership app (savings at restaurants, cafés, grocery stores, activities and events, plus guides, an expense and savings tracker, a document vault and emergency numbers), workshops, and the Buddy platform for universities. The assistant knows this from `knowledge/settleezy.md`.

| It does | How |
|---|---|
| **Learns how you write** (English + German) and what gets replies | Reads your sent mail, measures greetings, sign-offs, length, du/Sie, response times, and outreach reply rates by day/hour/follow-up, then writes `data/voice_profile.md` + `data/email_playbook.md` |
| **Drafts replies, follow-ups and partner outreach in your voice** | Saved in **Outlook → Drafts**, tagged with the category **"Settleezy AI"**. It **cannot send**: the app never gets Microsoft's send permission |
| **Daily brief** | Meetings (Outlook + Calendly), replies owed, follow-ups due, Instagram comments, new competitor listings, best leads, and the 5 things to do next. Saved as Markdown, shown on the dashboard, read aloud if you want |
| **Watches competitors** | Groupon, vspots, Top10 Berlin, UNiDAYS, Student Beans: new Berlin listings, stored in a database |
| **Builds your lead list** | Every merchant/brand found becomes a scored lead; it finds email/Instagram from their website and Impressum; plus 30+ Berlin universities and student-service seeds |
| **Thinks like a co-founder** | Weekly growth review: scoreboard, what's working, competitor read, 3 experiments, next week's marketing plan (`playbook/berlin-growth-playbook.md` is its strategy; `knowledge/settleezy.md` is what it knows about Settleezy) |
| **Voice** | "Hey Jarvis" wake word, Whisper speech recognition on your GPU, spoken answers in English or German |
| **Dashboard** | http://127.0.0.1:8765: Outlook, Calendly, Instagram, leads pipeline, competitor charts, brief, weekly review |

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
The account must be an Instagram **professional** account linked to a Facebook Page.
1. <https://developers.facebook.com> → **Create app** (type *Business*) → add **Instagram** (Instagram API with Facebook Login).
2. **Graph API Explorer** → pick your app → generate a user token with `instagram_basic`, `instagram_manage_insights`, `instagram_manage_comments`, `pages_show_list`, `pages_read_engagement`.
3. Exchange it for a 60-day token:
   `https://graph.facebook.com/v23.0/oauth/access_token?grant_type=fb_exchange_token&client_id=APP_ID&client_secret=APP_SECRET&fb_exchange_token=SHORT_TOKEN`
4. Find your IG account id: `https://graph.facebook.com/v23.0/me/accounts?fields=instagram_business_account&access_token=LONG_TOKEN`
5. Put them in `.env` as `IG_ACCESS_TOKEN` and `IG_USER_ID`. Renew the token every ~60 days (the dashboard's Instagram panel goes empty when it expires).

DMs are not included: Meta requires app review for message access.

### 7. Connect OpenJarvis (chat + voice brain)
Copy `openjarvis\config.toml` and `openjarvis\mcp-servers.json` to `%USERPROFILE%\.openjarvis\`, then edit `mcp-servers.json` so `"command"` is the full path, e.g.
`"C:\\Users\\you\\Settleezy\\cofounder\\.venv\\Scripts\\sz.exe"`.
Now `jarvis` can call your tools: *"what's my day?"*, *"draft replies"*, *"top merchant leads"*, *"what did Groupon add in Berlin?"*.

### 8. First run
```powershell
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
| Weekdays 07:45 | `morning`: sync, write drafts, build the brief (read aloud with `-Speak`) |
| Every 2 h, 09–19 | Outlook sync |
| 08:15, 14:15, 20:15 | Competitor scan → new listings → leads |
| Daily 13:00 | Lead enrichment (website + Impressum contacts) |
| Weekdays 14:30 | Second round of drafts |
| Daily 18:00 | Instagram metrics + unanswered comments |
| Sunday 20:00 | Re-learn your voice from the week's sent mail |
| Monday 07:15 | Co-founder growth review |
| At logon | Dashboard, and "Hey Jarvis" with `-Voice` |

Logs are in `data\logs\`.

---

## Everyday commands
```
sz brief --speak            today's brief, read aloud
sz triage                   what needs you (no drafting)
sz drafts --limit 5         write drafts now
sz leads list --kind merchant
sz leads draft 42           outreach draft for lead #42 (Outlook → Drafts)
sz leads status 42 contacted --note "met at Kranz, follow up Friday"
sz leads add --name "Café X" --kind-new merchant --website cafe-x.de
sz leads export             data\leads.csv
sz growth                   co-founder weekly review now
sz voice                    "Hey Jarvis" (or --no-wake for push-to-talk)
```

## Tuning
- **Competitor patterns:** `competitors.toml`. Run `sz scrape --inspect --site groupon` after any change. If a site only renders with JavaScript, set `render = true` and run `pip install -e ".[render]"` then `playwright install chromium`.
- **Follow-up cadence:** `config.toml` `[followup] cadence_days`. `sz learn` reports what your own data says works.
- **German speech:** set `whisper_model = "medium"`, and install a German Windows voice (Settings → Time & language → Speech → Add voices → Deutsch).
- **GPU speech recognition:** if Whisper falls back to CPU, run `pip install nvidia-cublas-cu12 "nvidia-cudnn-cu12==9.*"` in the venv.
- **What goes to the cloud:** set `cloud_enabled = false` to keep everything local. Drafting quality drops, but nothing leaves your laptop.

## How it fits together
```
Outlook ─┐                                   ┌─> Outlook Drafts ("Settleezy AI")
Calendly ├─> sz jobs ─> SQLite (data/) ──────┼─> Daily brief (md + voice)
Instagram┘      ▲            │               ├─> Dashboard :8765
Competitors ────┘            │               └─> Weekly growth review
                             └─> MCP server ─> OpenJarvis (chat, "Hey Jarvis")
local model (Ollama): triage, extraction  ·  Claude API: drafts, analysis (redacted)
```

## Rules it follows
Drafts only. Robots.txt, rate limits and an honest bot name when scraping. Business contact data only. UWG-friendly outreach: personal, relevant, an opt-out line, stop at "no". See `playbook/berlin-growth-playbook.md`.

## Development
```
pip install -e ".[dev]"
pytest
```
