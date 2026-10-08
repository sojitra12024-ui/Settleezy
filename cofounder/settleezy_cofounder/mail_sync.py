"""Pull Outlook inbox + sent mail into the local database (incremental)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .config import Config
from .db import DB
from .mailtext import detect_language, is_automated, strip_quoted
from .msgraph import Graph


def _addr(rec: dict | None) -> tuple[str, str]:
    ea = (rec or {}).get("emailAddress") or {}
    return (ea.get("address") or "").lower(), ea.get("name") or ""


def normalise(msg: dict, folder: str) -> dict:
    from_addr, from_name = _addr(msg.get("from"))
    body = (msg.get("body") or {}).get("content") or ""
    own = strip_quoted(body)
    subject = msg.get("subject") or ""
    sent_at = msg.get("sentDateTime") if folder == "sent" else msg.get("receivedDateTime")
    return {
        "id": msg["id"],
        "conversation_id": msg.get("conversationId") or msg["id"],
        "folder": folder,
        "from_addr": from_addr,
        "from_name": from_name,
        "to_addrs": [_addr(r)[0] for r in msg.get("toRecipients") or []],
        "cc_addrs": [_addr(r)[0] for r in msg.get("ccRecipients") or []],
        "subject": subject,
        "body_text": own[:20000],
        "sent_at": sent_at,
        "is_read": int(bool(msg.get("isRead", True))),
        "language": detect_language(own),
        "automated": int(folder == "inbox" and is_automated(from_addr, subject, msg.get("internetMessageHeaders"))),
    }


def sync(cfg: Config, db: DB, graph: Graph | None = None, history_days: int | None = None) -> dict[str, int]:
    """First run pulls `history_days` (default from config); later runs only fetch what's new."""
    graph = graph or Graph(cfg)
    history_days = history_days or int(cfg.get("outlook.history_days", 365))
    counts = {}
    for folder, label in (("inbox", "inbox"), ("sentitems", "sent")):
        cursor = db.kv_get(f"sync:{folder}")
        since = (
            datetime.fromisoformat(cursor) - timedelta(hours=1)
            if cursor
            else datetime.now(timezone.utc) - timedelta(days=history_days)
        )
        newest = cursor
        n = 0
        for msg in graph.messages(folder, since, limit=int(cfg.get("outlook.max_messages_per_sync", 5000))):
            if msg.get("isDraft"):
                continue
            row = normalise(msg, label)
            db.upsert_message(row)
            n += 1
            if row["sent_at"] and (newest is None or row["sent_at"] > newest):
                newest = row["sent_at"]
        db.conn.commit()
        if newest:
            db.kv_set(f"sync:{folder}", newest.replace("Z", "+00:00"))
        counts[label] = n
    return counts


def sync_calendar(cfg: Config, db: DB, graph: Graph | None = None, days: int = 7) -> int:
    graph = graph or Graph(cfg)
    start = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    events = graph.calendar_view(start, start + timedelta(days=days))
    db.x("DELETE FROM events WHERE source='outlook' AND start >= ?", (start.strftime("%Y-%m-%dT%H:%M"),))
    for e in events:
        loc = (e.get("location") or {}).get("displayName") or ""
        if e.get("onlineMeeting"):
            loc = loc or "Online (Teams)"
        db.x(
            "INSERT OR REPLACE INTO events(id,source,title,start,end,location,attendees,url) VALUES(?,?,?,?,?,?,?,?)",
            (
                "outlook:" + e["id"],
                "outlook",
                e.get("subject") or "(no title)",
                e["start"]["dateTime"][:16],
                e["end"]["dateTime"][:16],
                loc,
                ", ".join(f"{_addr(a)[1]} <{_addr(a)[0]}>" if _addr(a)[1] else _addr(a)[0] for a in e.get("attendees") or []),
                e.get("webLink") or "",
            ),
        )
    return len(events)
