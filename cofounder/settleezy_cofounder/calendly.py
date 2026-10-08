"""Calendly: upcoming bookings (personal access token, read-only)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx

from .config import Config, secret
from .db import DB

API = "https://api.calendly.com"


def _client() -> httpx.Client:
    return httpx.Client(base_url=API, timeout=30, headers={"Authorization": f"Bearer {secret('CALENDLY_TOKEN', required=True)}"})


def sync(cfg: Config, db: DB, days: int = 14) -> int:
    with _client() as c:
        user_uri = c.get("/users/me").raise_for_status().json()["resource"]["uri"]
        now = datetime.now(timezone.utc)
        params = {
            "user": user_uri,
            "status": "active",
            "min_start_time": now.replace(hour=0, minute=0).isoformat(),
            "max_start_time": (now + timedelta(days=days)).isoformat(),
            "count": 100,
            "sort": "start_time:asc",
        }
        events = c.get("/scheduled_events", params=params).raise_for_status().json()["collection"]
        db.x("DELETE FROM events WHERE source='calendly'")
        for e in events:
            uuid = e["uri"].rsplit("/", 1)[-1]
            invitees = c.get(f"/scheduled_events/{uuid}/invitees").json().get("collection", [])
            start = datetime.fromisoformat(e["start_time"].replace("Z", "+00:00")).astimezone()
            end = datetime.fromisoformat(e["end_time"].replace("Z", "+00:00")).astimezone()
            db.x(
                "INSERT OR REPLACE INTO events(id,source,title,start,end,location,attendees,url) VALUES(?,?,?,?,?,?,?,?)",
                (
                    "calendly:" + uuid,
                    "calendly",
                    e.get("name") or "Calendly booking",
                    start.strftime("%Y-%m-%dT%H:%M"),
                    end.strftime("%Y-%m-%dT%H:%M"),
                    (e.get("location") or {}).get("join_url") or (e.get("location") or {}).get("type", ""),
                    ", ".join(f"{i.get('name')} <{i.get('email')}>" for i in invitees),
                    e["uri"],
                ),
            )
        # meeting momentum: meetings held in the last 7 days
        recent = c.get(
            "/scheduled_events",
            params={"user": user_uri, "status": "active", "min_start_time": (now - timedelta(days=7)).isoformat(),
                    "max_start_time": now.isoformat(), "count": 100},
        ).json()["collection"]
        db.metric("calendly", "meetings_last_7d", len(recent))
        db.metric("calendly", "upcoming", len(events))
        return len(events)
