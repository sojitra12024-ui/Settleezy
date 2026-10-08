"""SQLite store for mail, drafts, competitor listings, leads and metrics."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT,
    folder TEXT,                -- inbox | sent
    from_addr TEXT,
    from_name TEXT,
    to_addrs TEXT,              -- JSON list
    cc_addrs TEXT,              -- JSON list
    subject TEXT,
    body_text TEXT,             -- own text only, quoted history stripped
    sent_at TEXT,               -- ISO UTC
    is_read INTEGER,
    language TEXT,
    automated INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_messages_conv ON messages(conversation_id, sent_at);

CREATE TABLE IF NOT EXISTS triage (
    message_id TEXT PRIMARY KEY,
    needs_reply INTEGER,
    priority INTEGER,           -- 1 (low) .. 3 (high)
    category TEXT,
    summary TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT,                  -- reply | followup | outreach
    conversation_id TEXT,
    source_message_id TEXT,
    lead_id INTEGER,
    graph_draft_id TEXT,
    subject TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS listings (
    id TEXT PRIMARY KEY,        -- source + canonical url
    source TEXT,
    url TEXT,
    title TEXT,
    merchant TEXT,
    category TEXT,
    city TEXT,
    price TEXT,
    first_seen TEXT,
    last_seen TEXT,
    raw TEXT
);
CREATE INDEX IF NOT EXISTS ix_listings_first_seen ON listings(first_seen);

CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,         -- merchant | brand | university | housing | service
    category TEXT,
    website TEXT,
    email TEXT,
    phone TEXT,
    instagram TEXT,
    city TEXT,
    sources TEXT,               -- JSON list of competitor/seed sources
    source_urls TEXT,           -- JSON list
    score REAL DEFAULT 0,
    status TEXT DEFAULT 'new',  -- new | drafted | contacted | replied | meeting | partner | lost
    notes TEXT,
    created_at TEXT,
    updated_at TEXT,
    last_contact_at TEXT,
    UNIQUE(name, kind)
);

CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    source TEXT,                -- outlook | calendly
    title TEXT,
    start TEXT,
    end TEXT,
    location TEXT,
    attendees TEXT,
    url TEXT
);

CREATE TABLE IF NOT EXISTS metrics (
    day TEXT,
    source TEXT,
    key TEXT,
    value REAL,
    PRIMARY KEY (day, source, key)
);

CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class DB:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        """Add columns introduced after the first release (SQLite has no ADD COLUMN IF NOT EXISTS)."""
        have = {r["name"] for r in self.conn.execute("PRAGMA table_info(drafts)")}
        for col in ("sent_at", "replied_at"):
            if col not in have:
                self.conn.execute(f"ALTER TABLE drafts ADD COLUMN {col} TEXT")
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "DB":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.conn.commit()
        self.close()

    # -- generic helpers -------------------------------------------------
    def q(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, tuple(params)).fetchall()

    def one(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
        return self.conn.execute(sql, tuple(params)).fetchone()

    def x(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        cur = self.conn.execute(sql, tuple(params))
        self.conn.commit()
        return cur

    def kv_get(self, key: str, default: str | None = None) -> str | None:
        row = self.one("SELECT value FROM kv WHERE key=?", (key,))
        return row["value"] if row else default

    def kv_set(self, key: str, value: str) -> None:
        self.x("INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    def metric(self, source: str, key: str, value: float, day: str | None = None) -> None:
        day = day or datetime.now().date().isoformat()
        self.x(
            "INSERT INTO metrics(day,source,key,value) VALUES(?,?,?,?) "
            "ON CONFLICT(day,source,key) DO UPDATE SET value=excluded.value",
            (day, source, key, float(value)),
        )

    # -- messages -------------------------------------------------------
    def upsert_message(self, m: dict[str, Any]) -> None:
        self.conn.execute(
            """INSERT INTO messages(id,conversation_id,folder,from_addr,from_name,to_addrs,cc_addrs,
                   subject,body_text,sent_at,is_read,language,automated)
               VALUES(:id,:conversation_id,:folder,:from_addr,:from_name,:to_addrs,:cc_addrs,
                   :subject,:body_text,:sent_at,:is_read,:language,:automated)
               ON CONFLICT(id) DO UPDATE SET is_read=excluded.is_read, folder=excluded.folder""",
            {**m, "to_addrs": json.dumps(m.get("to_addrs", [])), "cc_addrs": json.dumps(m.get("cc_addrs", []))},
        )

    def conversation(self, conversation_id: str) -> list[sqlite3.Row]:
        return self.q("SELECT * FROM messages WHERE conversation_id=? ORDER BY sent_at", (conversation_id,))
