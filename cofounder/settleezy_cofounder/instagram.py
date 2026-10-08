"""Instagram Business account via the Instagram Graph API (read-only).

Tracks followers, reach and post engagement, and finds comments you haven't answered.
DMs need Meta app review (instagram_manage_messages) and are not included.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx

from .config import Config, secret
from .db import DB


class Instagram:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.base = f"https://graph.facebook.com/{cfg.get('instagram.graph_version', 'v23.0')}"
        self.token = secret("IG_ACCESS_TOKEN", required=True)
        self.user_id = secret("IG_USER_ID", required=True)
        self.http = httpx.Client(timeout=30)

    def get(self, path: str, **params) -> dict:
        r = self.http.get(f"{self.base}/{path}", params={**params, "access_token": self.token})
        data = r.json()
        if "error" in data:
            raise RuntimeError(f"Instagram API: {data['error'].get('message')}")
        return data

    def profile(self) -> dict:
        return self.get(self.user_id, fields="username,followers_count,follows_count,media_count")

    def recent_media(self, limit: int = 25) -> list[dict]:
        return self.get(
            f"{self.user_id}/media",
            fields="id,caption,media_type,timestamp,like_count,comments_count,permalink",
            limit=limit,
        ).get("data", [])

    def daily_insights(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for metric in self.cfg.get("instagram.metrics", ["reach", "profile_views", "website_clicks"]):
            try:
                data = self.get(f"{self.user_id}/insights", metric=metric, period="day", metric_type="total_value")
                for m in data.get("data", []):
                    tv = m.get("total_value", {}).get("value")
                    if tv is None and m.get("values"):
                        tv = m["values"][-1].get("value")
                    if tv is not None:
                        out[metric] = float(tv)
            except RuntimeError:
                continue  # metric not available for this account / API version
        return out

    def unanswered_comments(self, media: list[dict], username: str, days: int = 7) -> list[dict]:
        since = datetime.now(timezone.utc) - timedelta(days=days)
        out = []
        for m in media:
            if not m.get("comments_count"):
                continue
            comments = self.get(f"{m['id']}/comments", fields="id,text,username,timestamp,replies{username}").get("data", [])
            for c in comments:
                ts = datetime.fromisoformat(c["timestamp"].replace("+0000", "+00:00"))
                replied = any(r.get("username") == username for r in (c.get("replies") or {}).get("data", []))
                if ts >= since and c.get("username") != username and not replied:
                    out.append({"post": m.get("permalink"), "user": c.get("username"), "text": c.get("text"), "at": c["timestamp"]})
        return out


def sync(cfg: Config, db: DB) -> dict:
    ig = Instagram(cfg)
    prof = ig.profile()
    media = ig.recent_media()
    db.metric("instagram", "followers", prof.get("followers_count", 0))
    db.metric("instagram", "posts", prof.get("media_count", 0))
    for k, v in ig.daily_insights().items():
        db.metric("instagram", k, v)
    last10 = media[:10]
    if last10:
        eng = sum((m.get("like_count") or 0) + (m.get("comments_count") or 0) for m in last10) / len(last10)
        db.metric("instagram", "avg_engagement_last10", round(eng, 1))
        if prof.get("followers_count"):
            db.metric("instagram", "engagement_rate_pct", round(100 * eng / prof["followers_count"], 2))
    comments = ig.unanswered_comments(media, prof.get("username", ""))
    import json

    db.kv_set("instagram:unanswered_comments", json.dumps(comments, ensure_ascii=False))
    db.kv_set(
        "instagram:top_posts",
        json.dumps(sorted(media, key=lambda m: -((m.get("like_count") or 0) + 3 * (m.get("comments_count") or 0)))[:5], ensure_ascii=False),
    )
    return {"followers": prof.get("followers_count"), "unanswered_comments": len(comments)}
