# Connecting Setz to the Settleezy admin panel and app

Your website's admin panel controls the app: partners, offers, members, redemptions. Setz runs on your laptop.
The bridge lets them work together **without exposing the laptop to the internet**:

```
 Mobile app ──► Website backend / admin panel ◄── pull every hour ── Setz (laptop)
                         │                                              ▲
                         └──── signed webhooks (optional, instant) ─────┘  via Tailscale Funnel
```

* **Pull (default).** Every hour `sz run bridge` reads partners, member numbers and venue requests.
* **Webhooks (optional).** The panel sends events (new member, conversion, redemption, venue request, support
  ticket) the moment they happen.
* **Push, only after your OK.** When a partner reaches "Member offer set up", Setz offers to create a **draft**
  listing in the panel. You publish it there yourself. Setz never publishes, edits live listings or deletes anything.

What Setz does with the data:

| Data from the panel | Used by |
|---|---|
| Partner status, redemptions (total / 30 days), views | Atlas (partner health), Vega (partner performance, "punch above their weight"), Lex (live without contract) |
| Members, trials, conversions, MRR | Quant (goals, forecasts, week-over-week drops), command-center gauges |
| Venue requests from members | New leads, plus a demand boost in **Onboard next** |
| Support tickets | Pop-up and phone push |
| Differences between Setz and the panel | Bridge → Atlas message in the agent council |

## 1. Endpoints your backend exposes (read-only, plus one draft create)

Base URL, for example `https://settleezy.com/api/setz/v1`. Every request carries `Authorization: Bearer <token>`.
Create one long random token for Setz in the panel and give it **only** these permissions.

### `GET /partners?updated_since=<ISO date>`
```json
{"items": [
  {"id": "p_123", "name": "Café Kranz", "status": "live", "category": "cafe", "address": "Torstr. 1, 10119 Berlin",
   "offer": "15% on all drinks", "website": "https://...", "instagram": "cafekranz",
   "redemptions_total": 412, "redemptions_30d": 57, "views_30d": 1890, "live_at": "2026-03-01", "updated_at": "2026-10-10T09:00:00Z"}
], "next": null}
```
`status` is one of: `draft`, `pending`, `review`, `live`, `published`, `active`, `paused`, `ended`, `archived`.
A plain array also works. With pagination, return `next` (a cursor) and accept `?cursor=`.

### `GET /stats/members`
Return a flat object of numbers. Any number you add shows up in Setz as metric `app.<key>`:
```json
{"paying_members": 1240, "trials_active": 310, "trials_started_7d": 95, "conversions_7d": 41,
 "churned_30d": 22, "semester_plans": 800, "yearly_plans": 440, "mrr_eur": 9300}
```

### `GET /venue-requests?since=<ISO date>`
Places members asked for in the app ("I'd love a discount at …"):
```json
[{"venue": "Mustafa's Gemüse Kebap", "category": "döner", "count": 17, "last_requested_at": "2026-10-09"}]
```

### `POST /partners` (draft only)
Setz sends this only after you approve it in Autopilot:
```json
{"external_ref": "setz:42", "name": "Yoga Mitte", "category": "yoga fitness", "address": "...", "offer": "20% off 10-class card",
 "website": "...", "instagram": "...", "lat": 52.52, "lon": 13.40, "status": "draft"}
```
Respond with `{"id": "p_456"}`. Your backend must force `status = draft` whatever it receives.

## 2. Webhooks (optional)

Send events in the [Standard Webhooks](https://www.standardwebhooks.com/) format to
`https://<laptop>.ts.net/api/bridge/webhook`, which is reachable through Tailscale Funnel:

```powershell
tailscale funnel --bg --set-path /api/bridge/webhook http://127.0.0.1:8765/api/bridge/webhook
```

Headers:
* `webhook-id`: a unique message id
* `webhook-timestamp`: unix seconds
* `webhook-signature`: `v1,<base64(HMAC-SHA256(secret, id + "." + timestamp + "." + body))>`

Body:
```json
{"type": "member.converted", "data": {"plan": "semester", "university": "TU Berlin"}}
```

| type | data |
|---|---|
| `member.signed_up` / `member.trial_started` / `member.converted` / `member.cancelled` | `plan`, `university` (optional) |
| `redemption.created` | `partner_id` (your id) |
| `partner.updated` | `id`, `status` |
| `venue.requested` | `venue`, `category` |
| `support.ticket.created` | `id`, `subject` |

Put the secret (`whsec_` + base64) in the panel and in `cofounder/.env` as `SZ_ADMIN_WEBHOOK_SECRET`. Setz rejects:
* unsigned or wrongly signed requests,
* requests more than 5 minutes old,
* repeated `webhook-id`s.

The webhook path is the only part of Setz that Funnel exposes. Everything else still needs Tailscale or the PIN.

Example sender (Node.js, using the official `standardwebhooks` package):
```js
import { Webhook } from "standardwebhooks";
const wh = new Webhook(process.env.SETZ_WEBHOOK_SECRET);           // "whsec_..."
const id = crypto.randomUUID(), ts = new Date(), body = JSON.stringify({ type: "redemption.created", data: { partner_id: "p_123" } });
await fetch(SETZ_URL, { method: "POST", body, headers: { "content-type": "application/json",
  "webhook-id": id, "webhook-timestamp": String(Math.floor(ts / 1000)), "webhook-signature": wh.sign(id, ts, body) } });
```

## 3. Setz → admin panel (read Setz's recommendations)

The panel can show Setz's ranked "onboard next" list. Set `SZ_BRIDGE_TOKEN` in `.env`, then call:
```
GET https://<laptop>.ts.net/api/bridge/v1/recommendations      Authorization: Bearer <SZ_BRIDGE_TOKEN>
```
This needs either a Funnel path for it, or a backend that is itself on your tailnet.

## 4. Turn it on

```toml
# config.toml
[admin]
base_url = "https://settleezy.com/api/setz/v1"
partner_url = "https://settleezy.com/admin/partners/{id}"
```
```
# .env
SZ_ADMIN_API_KEY=...
SZ_ADMIN_WEBHOOK_SECRET=whsec_...
```
Then:
1. Run `sz bridge sync` once.
2. Run `sz bridge` to see the status. It is also on the dashboard under **Autopilot → Admin panel & app**.
3. Re-run `scripts\register_tasks.ps1` so the hourly sync is scheduled.

The machine-readable version of this spec is `docs/admin-bridge.openapi.yaml`.
