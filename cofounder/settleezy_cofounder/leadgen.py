"""Campus lead discovery: every café, restaurant, supermarket, gym or cinema within walking distance of a Berlin campus.

Students walk past these places every day, which makes them the easiest partners to sign and the most used
discounts. The data comes from OpenStreetMap (free, no key) via the Overpass API: one polite query per campus,
at most once a day. Each venue becomes a merchant lead with its address, distance to the nearest campus and
whatever contacts OSM has (website, email, phone, Instagram); `sz run enrich` fills in the rest from the Impressum.
"""

from __future__ import annotations

import math
import time
from datetime import datetime
from typing import Any, Callable

from .config import Config
from .db import DB
from .leads import rescore, upsert

# Public Overpass servers, tried in order (the main one is sometimes busy). Override with leadgen.overpass_urls.
OVERPASS_URLS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter",
                 "https://overpass.private.coffee/api/interpreter"]

# Approximate main-entrance coordinates. Override or extend with [[leadgen.campuses]] in config.toml.
CAMPUSES = [
    {"name": "FU Berlin (Dahlem)", "lat": 52.4527, "lon": 13.2896},
    {"name": "HU Berlin (Mitte)", "lat": 52.5179, "lon": 13.3936},
    {"name": "HU Berlin (Adlershof)", "lat": 52.4330, "lon": 13.5300},
    {"name": "TU Berlin (Charlottenburg)", "lat": 52.5125, "lon": 13.3266},
    {"name": "UdK Berlin", "lat": 52.5093, "lon": 13.3283},
    {"name": "HTW Berlin (Treskowallee)", "lat": 52.4930, "lon": 13.5260},
    {"name": "HTW Berlin (Wilhelminenhof)", "lat": 52.4571, "lon": 13.5271},
    {"name": "BHT Berlin (Wedding)", "lat": 52.5442, "lon": 13.3517},
    {"name": "HWR Berlin (Schöneberg)", "lat": 52.4848, "lon": 13.3420},
    {"name": "Charité (Campus Mitte)", "lat": 52.5263, "lon": 13.3770},
    {"name": "ASH Berlin (Hellersdorf)", "lat": 52.5370, "lon": 13.6050},
    {"name": "Hertie School", "lat": 52.5097, "lon": 13.3893},
    {"name": "ESMT Berlin", "lat": 52.5166, "lon": 13.4007},
    {"name": "CODE University", "lat": 52.4930, "lon": 13.4470},
    {"name": "SRH Berlin", "lat": 52.5127, "lon": 13.3221},
]

# OSM tag -> category text (the category text feeds leads.score's student-value weighting).
TAGS: dict[str, dict[str, str]] = {
    "amenity": {"cafe": "cafe", "restaurant": "restaurant", "fast_food": "fast food restaurant", "bar": "bar",
                "pub": "pub", "ice_cream": "ice cream cafe", "cinema": "cinema", "theatre": "theater",
                "nightclub": "club"},
    "shop": {"supermarket": "supermarket grocery", "convenience": "späti grocery", "bakery": "bakery",
             "books": "books education", "copyshop": "copyshop student service", "bicycle": "bike shop"},
    "leisure": {"fitness_centre": "gym fitness", "sports_centre": "sport", "bowling_alley": "bowling entertainment"},
    "tourism": {"museum": "museum"},
}


def campuses(cfg: Config) -> list[dict]:
    return cfg.get("leadgen.campuses") or CAMPUSES


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def nearest_campus(cfg: Config, lat: float, lon: float) -> tuple[dict, int]:
    best = min(campuses(cfg), key=lambda c: haversine_m(lat, lon, c["lat"], c["lon"]))
    return best, int(haversine_m(lat, lon, best["lat"], best["lon"]))


def overpass_query(lat: float, lon: float, radius: int) -> str:
    parts = [f'nwr(around:{radius},{lat},{lon})["{key}"~"^({"|".join(vals)})$"]["name"];' for key, vals in TAGS.items()]
    return "[out:json][timeout:60];(" + "".join(parts) + ");out center tags;"


def _overpass(query: str, urls: list[str] | None = None) -> dict:
    import httpx

    last: Exception | None = None
    for url in urls or OVERPASS_URLS:
        try:
            r = httpx.post(url, data={"data": query}, timeout=90,
                           headers={"User-Agent": "Setz/1.0 (Settleezy partner discovery)"})
            if r.status_code in (429, 502, 503, 504):   # busy: try the next mirror
                last = RuntimeError(f"{url} answered {r.status_code}")
                continue
            r.raise_for_status()
            return r.json()
        except httpx.HTTPError as exc:
            last = exc
    raise RuntimeError(f"all Overpass servers failed: {last}")


def _category(tags: dict) -> str:
    for key, vals in TAGS.items():
        if tags.get(key) in vals:
            cuisine = tags.get("cuisine", "").replace(";", " ").replace("_", " ")
            return f"{vals[tags[key]]} {cuisine}".strip()
    return ""


def _instagram(tags: dict) -> str:
    v = tags.get("contact:instagram") or tags.get("instagram") or ""
    v = v.rstrip("/").rsplit("/", 1)[-1].lstrip("@") if v else ""
    return v


def parse_elements(data: dict) -> list[dict]:
    out = []
    for el in data.get("elements", []):
        tags = el.get("tags") or {}
        lat = el.get("lat", (el.get("center") or {}).get("lat"))
        lon = el.get("lon", (el.get("center") or {}).get("lon"))
        if not tags.get("name") or lat is None or lon is None:
            continue
        street = " ".join(x for x in (tags.get("addr:street", ""), tags.get("addr:housenumber", "")) if x)
        address = ", ".join(x for x in (street, " ".join(x for x in (tags.get("addr:postcode", ""), tags.get("addr:city", "")) if x)) if x)
        out.append({
            "name": tags["name"],
            "category": _category(tags),
            "lat": float(lat), "lon": float(lon),
            "address": address,
            "website": tags.get("website") or tags.get("contact:website") or tags.get("url") or "",
            "email": tags.get("email") or tags.get("contact:email") or "",
            "phone": tags.get("phone") or tags.get("contact:phone") or "",
            "instagram": _instagram(tags),
            "chain": bool(tags.get("brand") or tags.get("brand:wikidata")),
            "osm_url": f"https://www.openstreetmap.org/{el.get('type', 'node')}/{el.get('id')}",
        })
    return out


def discover(cfg: Config, db: DB, campus: str | None = None, *, fetch: Callable[[str], dict] | None = None,
             force: bool = False, pause: float = 2.0) -> dict[str, Any]:
    """Query OSM around each campus (or one, by substring) and upsert the venues as merchant leads."""
    urls = cfg.get("leadgen.overpass_urls")
    fetch = fetch or (lambda q: _overpass(q, urls))
    radius = int(cfg.get("leadgen.radius_m", 800))
    include_chains = bool(cfg.get("leadgen.include_chains", False))
    today = datetime.now().date().isoformat()
    targets = [c for c in campuses(cfg) if not campus or campus.lower() in c["name"].lower()]
    if not targets:
        raise ValueError(f"no campus matches '{campus}'")
    report: dict[str, Any] = {"campuses": {}, "new": 0, "updated": 0, "skipped_chains": 0}
    for i, c in enumerate(targets):
        key = f"leadgen:{c['name']}:{today}"
        if not force and db.kv_get(key):
            report["campuses"][c["name"]] = "already scanned today"
            continue
        if i and pause:
            time.sleep(pause)   # Overpass is a shared, free service
        try:
            venues = parse_elements(fetch(overpass_query(c["lat"], c["lon"], radius)))
        except Exception as exc:  # one campus failing must not stop the rest
            report["campuses"][c["name"]] = f"error: {exc}"
            continue
        new = 0
        for v in venues:
            if v["chain"] and not include_chains:
                report["skipped_chains"] += 1
                continue
            lead_id, created = upsert(db, v["name"], "merchant", source="osm:" + c["name"], source_url=v["osm_url"],
                                      category=v["category"], website=v["website"], email=v["email"], phone=v["phone"],
                                      instagram=v["instagram"], city="Berlin")
            if not lead_id:
                continue
            near, dist = nearest_campus(cfg, v["lat"], v["lon"])
            db.x("UPDATE leads SET lat=?, lon=?, address=coalesce(nullif(address,''), ?), campus=?, distance_m=? "
                 "WHERE id=? AND (distance_m IS NULL OR distance_m > ?)",
                 (v["lat"], v["lon"], v["address"], near["name"], dist, lead_id, dist))
            rescore(db, lead_id)
            new += created
            report["new" if created else "updated"] += 1
        db.kv_set(key, str(len(venues)))
        report["campuses"][c["name"]] = f"{len(venues)} venues, {new} new"
    return report


def near_campus(db: DB, campus: str | None = None, limit: int = 50, status: str | None = None) -> list[dict]:
    sql, params = "SELECT * FROM leads WHERE campus IS NOT NULL", []
    if campus:
        sql += " AND campus LIKE ?"
        params.append(f"%{campus}%")
    if status:
        sql += " AND status=?"
        params.append(status)
    sql += " ORDER BY score DESC, distance_m LIMIT ?"
    params.append(limit)
    return [dict(r) for r in db.q(sql, params)]


def coverage(cfg: Config, db: DB) -> list[dict]:
    """Per campus: venues found, contacted, partners. Shows where the map is still empty for members."""
    rows = {r["campus"]: dict(r) for r in db.q(
        "SELECT campus, COUNT(*) found, SUM(status IN ('contacted','replied','meeting')) active, SUM(status='partner') partners, "
        "SUM(email != '') with_email FROM leads WHERE campus IS NOT NULL GROUP BY campus")}
    out = []
    for c in campuses(cfg):
        r = rows.get(c["name"], {})
        out.append({"campus": c["name"], "lat": c["lat"], "lon": c["lon"], "found": r.get("found", 0) or 0,
                    "active": r.get("active", 0) or 0, "partners": r.get("partners", 0) or 0,
                    "with_email": r.get("with_email", 0) or 0})
    return out
