"""Setz's notification centre: one call, every screen.

`notify()` stores the notification (the dashboard's bell keeps the history) and delivers it:
  * live to every open dashboard / widget / hologram (server-sent events), which show a browser pop-up even when the
    tab is in the background, and keep their numbers fresh without reloading
  * as a Windows notification (toast) when the dashboard runs on the laptop, so it reaches you in any app
  * as a real push notification to your phone (installed app, even when it's closed) via Web Push, when
    `pywebpush` is installed and you enabled notifications on the phone

Each notification has a dedupe key, so the same alert never pops up twice. Quiet hours (default 22:00-07:30) keep
pop-ups silent at night; they still land in the bell.
"""

from __future__ import annotations

import json
import sys
import threading
from datetime import datetime
from typing import Any

import httpx

from .config import Config
from .db import DB, utcnow

KINDS = {"info", "lead", "meeting", "calendar", "alert", "partner", "voice", "job", "success"}


def _ensure(db: DB) -> None:
    db.conn.execute("CREATE TABLE IF NOT EXISTS notifications (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, title TEXT, body TEXT, "
                    "kind TEXT, url TEXT, key TEXT UNIQUE, read INTEGER DEFAULT 0)")
    db.conn.execute("CREATE TABLE IF NOT EXISTS push_subscriptions (endpoint TEXT PRIMARY KEY, sub TEXT, created_at TEXT, ua TEXT)")
    db.conn.commit()


def quiet_now(cfg: Config, now: datetime | None = None) -> bool:
    q = cfg.get("notifications.quiet_hours", ["22:00", "07:30"])
    if not q:
        return False
    hm = (now or datetime.now()).strftime("%H:%M")
    start, end = q
    return (start <= hm or hm < end) if start > end else (start <= hm < end)


def notify(cfg: Config, db: DB, title: str, body: str = "", *, kind: str = "info", url: str = "/", key: str | None = None,
           desktop: bool = True, push: bool = True) -> int | None:
    """Store + deliver. Returns the id, or None if this key was already notified."""
    _ensure(db)
    kind = kind if kind in KINDS else "info"
    cur = db.x("INSERT OR IGNORE INTO notifications(at,title,body,kind,url,key) VALUES(?,?,?,?,?,?)",
               (utcnow(), title[:160], body[:600], kind, url, key))
    if not cur.rowcount:
        return None
    nid = cur.lastrowid
    event = {"type": "notification", "id": nid, "title": title, "body": body, "kind": kind, "url": url,
             "quiet": quiet_now(cfg)}
    _to_dashboard(cfg, event)
    if not event["quiet"]:
        if desktop and cfg.get("notifications.desktop", True):
            threading.Thread(target=toast, args=(title, body), daemon=True).start()
        if push and cfg.get("notifications.push", True):
            threading.Thread(target=send_push, args=(cfg, db.path, {"title": title, "body": body, "url": url, "tag": key or str(nid)}),
                             daemon=True).start()
    return nid


def refresh(cfg: Config, what: str = "all") -> None:
    """Tell open dashboards that data changed (after a job), so they update live."""
    _to_dashboard(cfg, {"type": "refresh", "what": what})


def _to_dashboard(cfg: Config, event: dict) -> None:
    """In the dashboard process: broadcast directly. Elsewhere (scheduler, voice): POST to the running dashboard."""
    try:
        from .dashboard import app as dash

        if dash._loop is not None and dash._loop.is_running():
            dash.broadcast_notify(event)
            return
    except Exception:
        pass
    try:
        httpx.post(f"http://127.0.0.1:{int(cfg.get('dashboard.port', 8765))}/api/notify/event", json=event,
                   headers={"X-SZ": "1"}, timeout=1.5)
    except httpx.HTTPError:
        pass   # dashboard not running: the bell shows it next time


def toast(title: str, body: str) -> bool:
    """Windows notification (bottom-right pop-up, also over full-screen apps). Silent no-op elsewhere."""
    if sys.platform != "win32":
        return False
    try:
        from win11toast import notify as win_toast  # type: ignore

        win_toast(title, body, app_id="Setz · Settleezy")
        return True
    except Exception:
        pass
    try:
        from plyer import notification  # type: ignore

        notification.notify(title=title, message=body[:250], app_name="Setz", timeout=8)
        return True
    except Exception:
        return False


# -- Web Push (phone, even when the app is closed) ---------------------------------------------------------

def vapid_keys(cfg: Config) -> dict[str, str] | None:
    """Create (once) and return this installation's VAPID key pair, or None if pywebpush isn't installed."""
    path = cfg.data_dir / "vapid.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    try:
        import base64

        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        import pywebpush  # noqa: F401  (only generate keys if we can also send)
    except ImportError:
        return None
    key = ec.generate_private_key(ec.SECP256R1())
    priv = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    pub = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    keys = {"private_pem": priv, "public": base64.urlsafe_b64encode(pub).decode().rstrip("=")}
    path.write_text(json.dumps(keys), encoding="utf-8")
    return keys


def save_subscription(db: DB, sub: dict, ua: str = "") -> None:
    _ensure(db)
    if not sub.get("endpoint", "").startswith("https://"):
        raise ValueError("invalid push subscription")
    db.x("INSERT OR REPLACE INTO push_subscriptions(endpoint,sub,created_at,ua) VALUES(?,?,?,?)",
         (sub["endpoint"], json.dumps(sub), utcnow(), ua[:200]))


def send_push(cfg: Config, db_path: str, payload: dict[str, Any]) -> int:
    keys = vapid_keys(cfg)
    if not keys:
        return 0
    try:
        from pywebpush import WebPushException, webpush
    except ImportError:
        return 0
    sent = 0
    with DB(db_path) as db:
        _ensure(db)
        for row in db.q("SELECT endpoint, sub FROM push_subscriptions"):
            try:
                webpush(json.loads(row["sub"]), json.dumps(payload), vapid_private_key=keys["private_pem"],
                        vapid_claims={"sub": "mailto:" + (next(iter(cfg.my_addresses), "") or "setz@localhost")}, ttl=3600)
                sent += 1
            except WebPushException as exc:
                if getattr(exc, "response", None) is not None and exc.response.status_code in (404, 410):
                    db.x("DELETE FROM push_subscriptions WHERE endpoint=?", (row["endpoint"],))   # phone unsubscribed
    return sent


# -- reading -------------------------------------------------------------------------------------------------

def recent(db: DB, limit: int = 40) -> dict[str, Any]:
    _ensure(db)
    rows = [dict(r) for r in db.q("SELECT * FROM notifications ORDER BY id DESC LIMIT ?", (limit,))]
    return {"items": rows, "unread": db.one("SELECT COUNT(*) n FROM notifications WHERE read=0")["n"]}


def mark_read(db: DB, ids: list[int] | None = None) -> None:
    _ensure(db)
    if ids:
        db.x(f"UPDATE notifications SET read=1 WHERE id IN ({','.join('?' * len(ids))})", ids)
    else:
        db.x("UPDATE notifications SET read=1 WHERE read=0")
