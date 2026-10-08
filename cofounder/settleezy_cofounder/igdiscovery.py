"""Instagram venue discovery: popular places and small brands that live on Instagram, not on Google or Maps.

Uses only the official Instagram Graph API with your Business account (no scraping, so no risk to the account):

  1. hashtag search   top posts for Berlin hashtags (#berlinfood, #kreuzbergcafe, #berlinvegan ...). Instagram
                      allows 30 different hashtags per 7 days per account; Setz rotates within that budget.
  2. mentions         the accounts those posts @mention: usually the venue or brand being shown.
  3. business discovery  each account's public profile: followers, bio, website, recent posts and engagement
                      (works for business/creator accounts, which is what venues and brands use).
  4. classify         venue (address, opening hours, "Reservierung"...), brand with its own product (shop,
                      online, Versand...) or creator (blogger, "collab", "PR"...). Creators become marketing
                      collaboration leads; venues and brands become partner leads with followers + engagement.

You can also add accounts you spot yourself: `sz instagram add @handle ...`.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from .config import Config
from .db import DB, utcnow

DEFAULT_HASHTAGS = ["berlinfood", "berlincafe", "berlincoffee", "berlinbrunch", "berlinvegan", "kreuzbergfood",
                    "neukoellnfood", "berlinbakery", "berlineats", "berlinfoodie", "studentberlin", "berlinnightlife",
                    "friedrichshainfood", "prenzlauerbergfood", "berlinstartup", "madeinberlin"]
HASHTAG_BUDGET = 30          # Instagram: max unique hashtags per 7 rolling days
MENTION = re.compile(r"(?<![\w.])@([A-Za-z0-9_.]{2,30})(?<!\.)")
VENUE_HINTS = r"📍|öffnungszeiten|opening hours|mo[-–]|reserv|tisch|table|café|cafe|coffee|kaffee|restaurant|bar\b|bakery|bäckerei|" \
              r"brunch|kitchen|küche|eatery|bistro|späti|weinbar|cocktail|pizza|ramen|sushi|döner|burger|vegan food|\b1[0-4]\d{3}\b|straße|str\.|platz"
BRAND_HINTS = r"\bshop\b|online ?shop|store|versand|shipping|bestell|order now|made in berlin|handmade|handgemacht|" \
              r"small business|label|brand|sustainable|nachhaltig|link in bio.*shop|kollektion|collection|drop\b"
CREATOR_HINTS = r"blogger|creator|influencer|foodie\b|food lover|content|collab|kooperation|\bpr\b|📩|for business inquiries|" \
                r"ugc|travel|reviews? of|i eat|ich esse|mein leben|my life|photographer"


class IGClient(Protocol):
    def get(self, path: str, **params: Any) -> dict: ...


def _ig(cfg: Config) -> Any:
    from .instagram import Instagram

    return Instagram(cfg)


# -- budget ---------------------------------------------------------------------------------------

def _hashtag_log(db: DB) -> dict[str, str]:
    log = json.loads(db.kv_get("igdisc:hashtags_used", "{}") or "{}")
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    return {h: at for h, at in log.items() if at >= cutoff}


def hashtags_left(db: DB) -> int:
    return max(0, HASHTAG_BUDGET - len(_hashtag_log(db)))


def pick_hashtags(cfg: Config, db: DB, n: int) -> list[str]:
    """Hashtags already used this week are free to repeat; new ones only while the 30/week budget lasts."""
    tags = [t.lstrip("#").lower() for t in cfg.get("instagram.discovery_hashtags", DEFAULT_HASHTAGS)]
    used = _hashtag_log(db)
    last_run = json.loads(db.kv_get("igdisc:last_tag_run", "{}") or "{}")
    # least recently scanned first
    tags.sort(key=lambda t: last_run.get(t, ""))
    out, budget = [], HASHTAG_BUDGET - len(used)
    for t in tags:
        if len(out) >= n:
            break
        if t in used:
            out.append(t)
        elif budget > 0:
            out.append(t)
            budget -= 1
    return out


# -- classification ---------------------------------------------------------------------------------

def classify(profile: dict) -> tuple[str, str]:
    """-> (kind, category). kind: merchant (venue) | brand (own product) | creator | skip."""
    from .leads import CATEGORY_WEIGHTS

    bio = f"{profile.get('name') or ''} {profile.get('biography') or ''}".lower()
    venue = len(re.findall(VENUE_HINTS, bio))
    brand = len(re.findall(BRAND_HINTS, bio))
    creator = len(re.findall(CREATOR_HINTS, bio))
    category = ""
    for rx, _ in CATEGORY_WEIGHTS:
        m = re.search(rx, bio)
        if m:
            category = m.group(0)
            break
    if creator > max(venue, brand):
        return "creator", "content creator"
    if venue >= 1 and venue >= brand:
        return "merchant", category or "venue"
    if brand >= 1:
        return "brand", category or "own product"
    return ("merchant", category) if category else ("skip", "")


def address_from_bio(bio: str) -> tuple[str, str]:
    m = re.search(r"([A-ZÄÖÜ][\w.\-äöüß]+(?:[ \-][A-Za-zÄÖÜäöüß.\-]+){0,2}\s\d{1,4}\s?[a-z]?)[,\s]+(1[0-4]\d{3})", bio or "")
    return (f"{m.group(1).strip()}, {m.group(2)} Berlin", m.group(2)) if m else ("", "")


def engagement(profile: dict) -> float | None:
    media = ((profile.get("media") or {}).get("data")) or []
    f = profile.get("followers_count") or 0
    if not media or not f:
        return None
    avg = sum((m.get("like_count") or 0) + 2 * (m.get("comments_count") or 0) for m in media) / len(media)
    return round(100 * avg / f, 2)


# -- API calls ------------------------------------------------------------------------------------

def business_profile(ig: IGClient, user_id: str, username: str) -> dict | None:
    fields = ("business_discovery.username({u}){{username,name,biography,website,followers_count,media_count,"
              "media.limit(6){{caption,like_count,comments_count,timestamp,permalink}}}}").format(u=username)
    try:
        return ig.get(user_id, fields=fields).get("business_discovery")
    except RuntimeError:   # personal account, private, renamed, or rate limit
        return None


def hashtag_mentions(ig: IGClient, user_id: str, tag: str, limit: int = 50) -> dict[str, dict]:
    """@mentions in the top posts of a hashtag -> {handle: {"posts": n, "engagement": likes+comments, "example": url}}."""
    ids = ig.get("ig_hashtag_search", user_id=user_id, q=tag).get("data", [])
    if not ids:
        return {}
    media = ig.get(f"{ids[0]['id']}/top_media", user_id=user_id, limit=limit,
                   fields="id,caption,permalink,like_count,comments_count,timestamp").get("data", [])
    out: dict[str, dict] = {}
    for m in media:
        for h in set(x.lower() for x in MENTION.findall(m.get("caption") or "")):
            e = out.setdefault(h, {"posts": 0, "engagement": 0, "example": m.get("permalink", "")})
            e["posts"] += 1
            e["engagement"] += (m.get("like_count") or 0) + (m.get("comments_count") or 0)
    return out


def _save(db: DB, prof: dict, source: str, extra_note: str = "") -> tuple[int, bool, str]:
    from .leadintel import AREAS
    from .leads import rescore, upsert

    kind, category = classify(prof)
    if kind == "skip":
        return 0, False, kind
    handle = "@" + prof["username"]
    name = (prof.get("name") or prof["username"]).strip()[:120]
    addr, postcode = address_from_bio(prof.get("biography") or "")
    lid, created = upsert(db, name, kind, source=source,
                          source_url=f"https://instagram.com/{prof['username']}", category=category,
                          website=prof.get("website") or "", instagram=handle, city="Berlin")
    if not lid:
        return 0, False, kind
    bio = prof.get("biography") or ""
    district = next((a for a, _, _ in AREAS if a.lower() in bio.lower()), "")
    attrs = {"instagram_engagement_pct": engagement(prof), "instagram_posts": prof.get("media_count")}
    if kind == "creator":
        attrs["creator"] = "yes"   # marketing collaboration, not a discount partner
    db.x("UPDATE leads SET ig_followers=?, ig_bio=?, address=coalesce(nullif(address,''), ?), postcode=coalesce(nullif(postcode,''), ?), "
         "district=coalesce(nullif(district,''), ?), attributes=?, notes=trim(coalesce(notes,'') || ' ' || ?), updated_at=? WHERE id=?",
         (prof.get("followers_count"), bio[:500], addr, postcode, district,
          json.dumps({k: v for k, v in attrs.items() if v is not None}, ensure_ascii=False),
          extra_note if created and extra_note else "", utcnow(), lid))
    rescore(db, lid)
    return lid, created, kind


def discover(cfg: Config, db: DB, hashtags: list[str] | None = None, *, ig: IGClient | None = None,
             max_profiles: int = 40, min_followers: int = 300, pause: float = 1.0) -> dict[str, Any]:
    """Hashtags -> mentioned accounts -> business profiles -> leads. Respects the 30 hashtags / 7 days limit."""
    from .config import secret

    ig = ig or _ig(cfg)
    user_id = getattr(ig, "user_id", None) or secret("IG_USER_ID", required=True)
    tags = [t.lstrip("#").lower() for t in hashtags] if hashtags else pick_hashtags(cfg, db, int(cfg.get("instagram.discovery_tags_per_run", 4)))
    used, last_run = _hashtag_log(db), json.loads(db.kv_get("igdisc:last_tag_run", "{}") or "{}")
    own = (db.kv_get("instagram:username") or "").lower()
    pool: dict[str, dict] = {}
    report: dict[str, Any] = {"hashtags": {}, "new": 0, "updated": 0, "skipped": 0, "creators": 0, "brands": 0, "venues": 0}
    for tag in tags:
        if tag not in used and len(used) >= HASHTAG_BUDGET:
            report["hashtags"][tag] = "skipped: weekly hashtag limit reached"
            continue
        try:
            found = hashtag_mentions(ig, user_id, tag)
        except RuntimeError as exc:
            report["hashtags"][tag] = f"error: {exc}"[:160]
            continue
        now = utcnow()
        used.setdefault(tag, now)
        last_run[tag] = now
        report["hashtags"][tag] = f"{len(found)} accounts mentioned"
        for h, v in found.items():
            p = pool.setdefault(h, {"posts": 0, "engagement": 0, "tags": [], "example": v["example"]})
            p["posts"] += v["posts"]
            p["engagement"] += v["engagement"]
            p["tags"].append(tag)
    db.kv_set("igdisc:hashtags_used", json.dumps(used))
    db.kv_set("igdisc:last_tag_run", json.dumps(last_run))
    known = {(r["instagram"] or "").lstrip("@").lower() for r in db.q("SELECT instagram FROM leads WHERE instagram != ''")}
    month_ago = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    skipped = {h: at for h, at in json.loads(db.kv_get("igdisc:skipped", "{}") or "{}").items() if at >= month_ago}
    ranked = sorted((h for h in pool if h != own and h not in known and h not in skipped),
                    key=lambda h: (-pool[h]["posts"], -pool[h]["engagement"]))
    for i, h in enumerate(ranked[:max_profiles]):
        if i and pause:
            time.sleep(pause)
        prof = business_profile(ig, user_id, h)
        if not prof or (prof.get("followers_count") or 0) < min_followers:
            report["skipped"] += 1
            skipped[h] = utcnow()   # don't spend API calls on it again for a month
            continue
        p = pool[h]
        note = f"Found on Instagram via #{', #'.join(p['tags'][:3])} ({p['posts']} top posts, e.g. {p['example']})."
        lid, created, kind = _save(db, prof, "instagram:#" + p["tags"][0], note)
        if not lid:
            report["skipped"] += 1
            skipped[h] = utcnow()
            continue
        report["new" if created else "updated"] += 1
        report[{"merchant": "venues", "brand": "brands", "creator": "creators"}[kind]] += 1
    db.kv_set("igdisc:skipped", json.dumps(skipped))
    report["hashtag_budget_left"] = hashtags_left(db)
    return report


def add_handles(cfg: Config, db: DB, handles: list[str], *, ig: IGClient | None = None) -> list[dict]:
    """Accounts you spotted yourself -> profile -> lead."""
    from .config import secret

    ig = ig or _ig(cfg)
    user_id = getattr(ig, "user_id", None) or secret("IG_USER_ID", required=True)
    out = []
    for h in handles:
        h = re.sub(r"^https?://(www\.)?instagram\.com/", "", h.strip()).strip("/@ ").split("/")[0].split("?")[0]
        if not h:
            continue
        prof = business_profile(ig, user_id, h)
        if not prof:
            out.append({"handle": h, "error": "not found, private, or a personal account"})
            continue
        lid, created, kind = _save(db, prof, "instagram:manual")
        out.append({"handle": h, "lead_id": lid, "created": created, "kind": kind, "followers": prof.get("followers_count")})
    return out


def refresh_profiles(cfg: Config, db: DB, limit: int = 30, *, ig: IGClient | None = None) -> dict[str, int]:
    """Followers / bio for leads that have a handle but no (or 30-day-old) Instagram data."""
    from .config import secret

    ig = ig or _ig(cfg)
    user_id = getattr(ig, "user_id", None) or secret("IG_USER_ID", required=True)
    rows = db.q("SELECT id, instagram FROM leads WHERE instagram != '' AND status NOT IN ('lost') AND "
                "(ig_followers IS NULL OR updated_at < datetime('now','-30 day')) ORDER BY score DESC LIMIT ?", (limit,))
    n = 0
    for r in rows:
        prof = business_profile(ig, user_id, r["instagram"].lstrip("@"))
        if prof:
            db.x("UPDATE leads SET ig_followers=?, ig_bio=?, website=coalesce(nullif(website,''), ?), updated_at=? WHERE id=?",
                 (prof.get("followers_count"), (prof.get("biography") or "")[:500], prof.get("website") or "", utcnow(), r["id"]))
            n += 1
    return {"refreshed": n, "checked": len(rows)}
