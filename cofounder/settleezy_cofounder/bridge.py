"""Bridge to the Settleezy admin panel (website) and, through it, the mobile app.

The admin panel controls the app (partners, offers, members, redemptions). Setz connects to it in two ways
(the API the panel needs to expose is specified in docs/ADMIN_BRIDGE.md, with an OpenAPI file):

  pull (default, works even though the laptop isn't reachable from the internet)
      every 30 min `sz run bridge` reads partners + their redemptions/views, member numbers (trials,
      conversions, paying members, MRR), and venue requests from app users. Setz then
        * updates each partner's redemptions and status (the panel is the source of truth for what's live)
        * records member/app metrics for Quant (growth goals, forecasts)
        * turns venue requests into leads and demand signals for "whom to onboard next"
        * reports differences between Setz and the panel to the council (Bridge -> Atlas)
  webhooks (optional, instant)
      the panel POSTs events to /api/bridge/webhook (Standard Webhooks signature: webhook-id,
      webhook-timestamp, webhook-signature = v1,<base64 HMAC-SHA256>). Reach the laptop with
      `tailscale funnel --set-path /api/bridge/webhook 8765` or let your server call it over Tailscale.
  push (always through your approval)
      when a partner reaches "Member offer set up", Setz proposes creating a *draft* listing in the panel;
      you approve it in Setz, then publish it in the panel yourself. Setz never publishes or deletes.

Secrets come from cofounder/.env: SZ_ADMIN_API_KEY (Bearer token for the panel API) and
SZ_ADMIN_WEBHOOK_SECRET (whsec_... shared with the panel).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from datetime import date, datetime, timezone
from typing import Any, Callable

import httpx

from .config import Config, secret
from .db import DB, utcnow

EVENTS = {"member.signed_up", "member.trial_started", "member.converted", "member.cancelled", "redemption.created",
          "partner.updated", "venue.requested", "support.ticket.created"}
PANEL_STATUS = {"live": "live", "published": "live", "active": "live", "paused": "paused", "ended": "ended",
                "archived": "ended", "draft": "onboarding", "pending": "onboarding", "review": "onboarding"}


def _ensure(db: DB) -> None:
    have = {r["name"] for r in db.conn.execute("PRAGMA table_info(partners)")}
    for col in ("external_id", "panel_status", "panel_synced_at", "views_30d", "redemptions_30d"):
        if col not in have:
            db.conn.execute(f"ALTER TABLE partners ADD COLUMN {col} {'INTEGER' if col.endswith('30d') else 'TEXT'}")
    db.conn.execute("CREATE TABLE IF NOT EXISTS bridge_events (id TEXT PRIMARY KEY, type TEXT, at TEXT, payload TEXT)")
    db.conn.commit()


def configured(cfg: Config) -> bool:
    return bool(cfg.get("admin.base_url") and secret("SZ_ADMIN_API_KEY"))


class Panel:
    """Thin client for the admin-panel API (see docs/ADMIN_BRIDGE.md)."""

    def __init__(self, cfg: Config, client: httpx.Client | None = None):
        self.base = str(cfg.get("admin.base_url", "")).rstrip("/")
        if not self.base:
            raise RuntimeError("Set [admin] base_url in config.toml and SZ_ADMIN_API_KEY in .env (see docs/ADMIN_BRIDGE.md)")
        self.http = client or httpx.Client(timeout=20, headers={"Authorization": f"Bearer {secret('SZ_ADMIN_API_KEY', required=True)}",
                                                                "User-Agent": "Setz/1.0"})

    def _req(self, method: str, path: str, **kw) -> Any:
        try:
            r = self.http.request(method, self.base + path, **kw)
        except httpx.HTTPError as exc:
            raise RuntimeError(f"admin panel unreachable: {exc}") from exc
        if r.status_code >= 400:
            raise RuntimeError(f"admin panel {method} {path}: {r.status_code} {r.text[:200]}")
        try:
            return r.json()
        except ValueError as exc:
            raise RuntimeError(f"admin panel {path} returned no JSON") from exc

    def _items(self, path: str, params: dict | None = None) -> list[dict]:
        """Lists may be plain arrays or {"items": [...], "next": cursor}."""
        out, params = [], dict(params or {})
        for _ in range(50):
            data = self._req("GET", path, params=params)
            if isinstance(data, list):
                return out + data
            out += data.get("items", [])
            if not data.get("next"):
                return out
            params["cursor"] = data["next"]
        return out

    def partners(self, since: str | None = None) -> list[dict]:
        return self._items("/partners", {"updated_since": since} if since else None)

    def member_stats(self) -> dict:
        return self._req("GET", "/stats/members")

    def venue_requests(self, since: str | None = None) -> list[dict]:
        return self._items("/venue-requests", {"since": since} if since else None)

    def create_draft_partner(self, body: dict) -> dict:
        return self._req("POST", "/partners", json={**body, "status": "draft"})


# -- pull ---------------------------------------------------------------------------------------------

def sync(cfg: Config, db: DB, panel: Panel | None = None) -> dict[str, Any]:
    from .leads import _norm_name

    _ensure(db)
    panel = panel or Panel(cfg)
    report: dict[str, Any] = {"partners": 0, "updated": 0, "linked": 0, "diffs": 0, "requests": 0, "metrics": 0}
    trust = bool(cfg.get("admin.trust_panel_status", True))
    locals_ = [dict(r) for r in db.q("SELECT * FROM partners")]
    by_ext = {p["external_id"]: p for p in locals_ if p.get("external_id")}
    by_name = {_norm_name(p["name"]).casefold(): p for p in locals_}
    diffs = []
    for rp in panel.partners():
        report["partners"] += 1
        ext = str(rp.get("id", ""))
        local = by_ext.get(ext) or by_name.get(_norm_name(str(rp.get("name", ""))).casefold())
        status = PANEL_STATUS.get(str(rp.get("status", "")).lower(), "")
        if local is None:
            diffs.append({"name": rp.get("name"), "text": f"{rp.get('name')} is in the panel ({rp.get('status')}) but not in Setz"})
            if cfg.get("admin.import_unknown_partners", True) and rp.get("name"):
                from .ops import add_partner

                pid = add_partner(db, str(rp["name"]), "venue", category=rp.get("category") or "", offer=rp.get("offer") or "",
                                  website=rp.get("website") or "", instagram=rp.get("instagram") or "")
                db.x("UPDATE partners SET external_id=?, status=?, stage=? WHERE id=?",
                     (ext, status or "onboarding", "live" if status == "live" else "listed", pid))
            continue
        upd: dict[str, Any] = {"external_id": ext, "panel_status": rp.get("status"), "panel_synced_at": utcnow()}
        if not local.get("external_id"):
            report["linked"] += 1
        for k_panel, k_local in (("redemptions_total", "redemptions"), ("redemptions_30d", "redemptions_30d"), ("views_30d", "views_30d")):
            if rp.get(k_panel) is not None:
                upd[k_local] = int(rp[k_panel])
        if rp.get("offer") and not local.get("offer"):
            upd["offer"] = rp["offer"]
        if status and status != local["status"]:
            if trust:
                upd["status"] = status
                if status == "live":
                    upd["stage"] = "live"
                    upd["live_at"] = local.get("live_at") or str(rp.get("live_at") or date.today().isoformat())[:10]
            else:
                diffs.append({"name": local["name"], "text": f"{local['name']}: '{local['status']}' in Setz, '{rp.get('status')}' in the panel"})
        db.x(f"UPDATE partners SET {', '.join(f'{k}=?' for k in upd)}, updated_at=? WHERE id=?", [*upd.values(), utcnow(), local["id"]])
        report["updated"] += 1
    db.kv_set("bridge:diffs", json.dumps(diffs, ensure_ascii=False))
    report["diffs"] = len(diffs)
    try:
        stats = panel.member_stats()
        for k, v in (stats or {}).items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                db.metric("app", k, v)
                report["metrics"] += 1
    except RuntimeError as exc:
        report["stats_error"] = str(exc)
    try:
        reqs = panel.venue_requests()
        db.kv_set("bridge:requests", json.dumps(reqs[:500], ensure_ascii=False))
        report["requests"] = len(reqs)
        report["request_leads"] = _requests_to_leads(db, reqs)
    except RuntimeError as exc:
        report["requests_error"] = str(exc)
    db.kv_set("last_run:bridge", utcnow())
    return report


def _requests_to_leads(db: DB, reqs: list[dict]) -> int:
    """Venues members ask for become leads (demand-proven, so they rank high in onboard_next)."""
    from .leads import rescore, upsert

    n = 0
    for r in reqs:
        name = str(r.get("venue") or "").strip()
        if len(name) < 2:
            continue
        lead_id, created = upsert(db, name, "merchant", source="app:request", category=str(r.get("category") or ""), city="Berlin")
        if lead_id:
            db.x("UPDATE leads SET notes=coalesce(notes,'') || ? WHERE id=? AND coalesce(notes,'') NOT LIKE '%requested by members%'",
                 (f"\nRequested by members in the app ({int(r.get('count', 1))}×).", lead_id))
            rescore(db, lead_id)
            n += created
    return n


# -- webhooks (Standard Webhooks) ------------------------------------------------------------------------

def _secret_bytes(s: str) -> bytes:
    if s.startswith("whsec_"):
        return base64.b64decode(s[6:])
    return s.encode()


def sign(secret_: str, msg_id: str, ts: str, body: bytes) -> str:
    mac = hmac.new(_secret_bytes(secret_), f"{msg_id}.{ts}.".encode() + body, hashlib.sha256).digest()
    return "v1," + base64.b64encode(mac).decode()


def verify(headers: dict[str, str], body: bytes, secret_: str | None = None, tolerance: int = 300, now: float | None = None) -> None:
    """Raise ValueError unless the request carries a valid, fresh Standard Webhooks signature."""
    secret_ = secret_ or secret("SZ_ADMIN_WEBHOOK_SECRET")
    if not secret_:
        raise ValueError("webhooks are off: set SZ_ADMIN_WEBHOOK_SECRET in .env")
    h = {k.lower(): v for k, v in headers.items()}
    msg_id, ts, sigs = h.get("webhook-id", ""), h.get("webhook-timestamp", ""), h.get("webhook-signature", "")
    if not (msg_id and ts and sigs):
        raise ValueError("missing webhook headers")
    try:
        t = int(ts)
    except ValueError as exc:
        raise ValueError("bad timestamp") from exc
    if abs((now or time.time()) - t) > tolerance:
        raise ValueError("timestamp too old or in the future")
    expected = sign(secret_, msg_id, ts, body).encode()
    if not any(hmac.compare_digest(expected, s.encode()) for s in sigs.split()):
        raise ValueError("invalid signature")


def handle_event(cfg: Config, db: DB, msg_id: str, event: dict) -> dict[str, Any]:
    """Apply one verified webhook event. Idempotent per webhook-id."""
    from .notify import notify

    _ensure(db)
    etype = str(event.get("type", ""))
    data = event.get("data") or {}
    if db.one("SELECT 1 FROM bridge_events WHERE id=?", (msg_id,)):
        return {"duplicate": True}
    db.x("INSERT INTO bridge_events(id,type,at,payload) VALUES(?,?,?,?)", (msg_id, etype, utcnow(), json.dumps(event)[:4000]))
    if etype not in EVENTS:
        return {"ignored": etype}
    today = datetime.now().date().isoformat()

    def bump(key: str, by: float = 1) -> None:
        cur = db.one("SELECT value FROM metrics WHERE day=? AND source='app' AND key=?", (today, key))
        db.metric("app", key, (cur["value"] if cur else 0) + by)

    if etype == "member.signed_up":
        bump("signups_today")
    elif etype == "member.trial_started":
        bump("trials_started_today")
    elif etype == "member.converted":
        bump("conversions_today")
        plan = str(data.get("plan", ""))
        notify(cfg, db, "New paying member 🎉", f"{plan or 'membership'} · {data.get('university') or ''}".strip(" ·"), kind="success",
               url="/#growth", key=f"conv:{msg_id}", push=bool(cfg.get("admin.push_conversions", False)))
    elif etype == "member.cancelled":
        bump("cancellations_today")
    elif etype == "redemption.created":
        bump("redemptions_today")
        ext = str(data.get("partner_id", ""))
        db.x("UPDATE partners SET redemptions=coalesce(redemptions,0)+1, updated_at=? WHERE external_id=?", (utcnow(), ext))
    elif etype == "partner.updated":
        status = PANEL_STATUS.get(str(data.get("status", "")).lower())
        if status and cfg.get("admin.trust_panel_status", True):
            db.x("UPDATE partners SET status=?, panel_status=?, updated_at=? WHERE external_id=?",
                 (status, data.get("status"), utcnow(), str(data.get("id", ""))))
    elif etype == "venue.requested":
        reqs = json.loads(db.kv_get("bridge:requests", "[]") or "[]")
        name = str(data.get("venue", "")).strip()
        hit = next((r for r in reqs if str(r.get("venue", "")).casefold() == name.casefold()), None)
        if hit:
            hit["count"] = int(hit.get("count", 1)) + 1
        elif name:
            reqs.append({"venue": name, "category": data.get("category", ""), "count": 1})
        db.kv_set("bridge:requests", json.dumps(reqs[-500:], ensure_ascii=False))
        _requests_to_leads(db, [{"venue": name, "category": data.get("category", ""), "count": 1}])
        notify(cfg, db, f"Members want: {name}", "Requested in the app · added to your leads", kind="lead", url="/#market",
               key=f"vreq:{name.casefold()}:{today}")
    elif etype == "support.ticket.created":
        notify(cfg, db, "App support ticket", str(data.get("subject", ""))[:160], kind="alert", url=str(cfg.get("admin.support_url", "/")),
               key=f"ticket:{data.get('id', msg_id)}")
    return {"ok": True, "type": etype}


# -- push (via approval) -----------------------------------------------------------------------------------

def draft_listing_payload(db: DB, partner_id: int) -> dict:
    p = db.one("SELECT * FROM partners WHERE id=?", (partner_id,))
    if not p:
        raise ValueError(f"no partner {partner_id}")
    lead = db.one("SELECT * FROM leads WHERE id=?", (p["lead_id"],)) if p["lead_id"] else None
    return {"external_ref": f"setz:{p['id']}", "name": p["name"], "category": p["category"] or (lead["category"] if lead else ""),
            "address": (lead["address"] if lead else "") or "", "offer": p["offer"] or "", "website": p["website"] or "",
            "instagram": p["instagram"] or "", "lat": lead["lat"] if lead else None, "lon": lead["lon"] if lead else None,
            "status": "draft"}   # Setz never publishes: you do, in the panel


def push_partner(cfg: Config, db: DB, partner_id: int, panel: Panel | None = None) -> dict:
    """Create a *draft* listing in the admin panel (called only after you approve it)."""
    _ensure(db)
    p = db.one("SELECT external_id FROM partners WHERE id=?", (partner_id,))
    if p and p["external_id"]:
        return {"skipped": "already in the panel", "external_id": p["external_id"]}
    res = (panel or Panel(cfg)).create_draft_partner(draft_listing_payload(db, partner_id))
    ext = str(res.get("id", ""))
    db.x("UPDATE partners SET external_id=?, panel_status='draft', panel_synced_at=? WHERE id=?", (ext, utcnow(), partner_id))
    return {"external_id": ext, "url": panel_url(cfg, ext)}


def panel_url(cfg: Config, external_id: str | None) -> str:
    tpl = cfg.get("admin.partner_url", "")
    return tpl.format(id=external_id) if tpl and external_id else ""


def pending_pushes(db: DB) -> list[dict]:
    _ensure(db)
    return [dict(r) for r in db.q("SELECT id, name, stage FROM partners WHERE (external_id IS NULL OR external_id='') "
                                    "AND stage IN ('offer','listed','promoted','live') AND status != 'ended'")]


def status(cfg: Config, db: DB) -> dict[str, Any]:
    _ensure(db)
    n = db.one("SELECT COUNT(*) n, SUM(external_id IS NOT NULL AND external_id != '') linked FROM partners")
    return {"configured": configured(cfg), "base_url": cfg.get("admin.base_url", ""), "webhooks": bool(secret("SZ_ADMIN_WEBHOOK_SECRET")),
            "last_sync": db.kv_get("last_run:bridge"), "partners": n["n"], "linked": n["linked"] or 0,
            "diffs": json.loads(db.kv_get("bridge:diffs", "[]") or "[]"),
            "requests": json.loads(db.kv_get("bridge:requests", "[]") or "[]")[:20],
            "events": [dict(r) for r in db.q("SELECT type, at FROM bridge_events ORDER BY at DESC LIMIT 15")]}
