"""Campus lead discovery: every café, restaurant, supermarket, gym or cinema within walking distance of a Berlin campus.

Students walk past these places every day, which makes them the easiest partners to sign and the most used
discounts. The data comes from OpenStreetMap (free, no key) via the Overpass API: one polite query per campus,
at most once a day. Each venue becomes a merchant lead with its address, distance to the nearest campus and
whatever contacts OSM has (website, email, phone, Instagram); `sz run enrich` fills in the rest from the Impressum.
"""

from __future__ import annotations

import json
import math
import re
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
# Everything a student on a budget uses near campus: food, groceries, sport, culture, services, study supplies.
TAGS: dict[str, dict[str, str]] = {
    "amenity": {"cafe": "cafe", "restaurant": "restaurant", "fast_food": "fast food restaurant", "bar": "bar",
                "pub": "pub", "ice_cream": "ice cream cafe", "cinema": "cinema", "theatre": "theater",
                "nightclub": "club", "language_school": "language school course", "driving_school": "driving school course",
                "coworking_space": "coworking study space", "bicycle_rental": "bike rental mobility",
                "music_school": "music school course", "arts_centre": "arts centre event"},
    "shop": {"supermarket": "supermarket grocery", "convenience": "späti grocery", "bakery": "bakery",
             "books": "books education", "copyshop": "copyshop student service", "bicycle": "bike shop",
             "hairdresser": "hair salon friseur", "beauty": "beauty salon", "optician": "optician",
             "laundry": "laundry student service", "dry_cleaning": "laundry student service",
             "mobile_phone": "mobile phone sim", "computer": "computer laptop tech", "electronics": "electronics tech",
             "stationery": "stationery study supplies", "second_hand": "second hand fashion", "tea": "tea cafe",
             "coffee": "coffee", "deli": "deli food", "organic": "bio markt grocery", "health_food": "bio markt grocery",
             "sports": "sports shop", "outdoor": "outdoor sport shop", "games": "games entertainment",
             "music": "music shop", "photo": "photo shop", "frame": "art supplies", "art": "art supplies",
             "variety_store": "variety store", "pastry": "bakery", "chocolate": "chocolate shop"},
    "leisure": {"fitness_centre": "gym fitness", "sports_centre": "sport", "bowling_alley": "bowling entertainment",
                "escape_game": "escape room entertainment", "amusement_arcade": "arcade entertainment",
                "dance": "dance studio sport", "trampoline_park": "trampoline sport entertainment",
                "miniature_golf": "minigolf entertainment", "climbing": "climbing boulder sport", "sauna": "sauna wellness"},
    "tourism": {"museum": "museum", "gallery": "gallery museum"},
    "office": {"coworking": "coworking study space"},
}

# Universities and colleges, found in OpenStreetMap across the whole city (see discover_universities).
UNI_TAGS = {"amenity": ["university", "college"]}


def campuses(cfg: Config) -> list[dict]:
    """Configured campuses, else the built-in list plus every university/college found in OSM (`sz leadgen unis`)."""
    if cfg.get("leadgen.campuses"):
        return cfg.get("leadgen.campuses")
    if not cfg.get("leadgen.use_discovered_campuses", True):
        return CAMPUSES
    return CAMPUSES + [c for c in discovered_campuses(cfg) if c.get("enabled", True)]


def _campus_file(cfg: Config):
    return cfg.data_dir / "campuses.json"


def discovered_campuses(cfg: Config) -> list[dict]:
    try:
        return json.loads(_campus_file(cfg).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


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
        except (httpx.HTTPError, ValueError) as exc:   # ValueError: an HTML error page instead of JSON
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
    v = re.split(r"[?#]", v)[0].rstrip("/").rsplit("/", 1)[-1].lstrip("@") if v else ""   # drop ?hl=de, #...
    return v


ATTRIBUTE_TAGS = {   # OSM tag -> attribute shown on the lead (yes/only values)
    "diet:vegan": "vegan", "diet:vegetarian": "vegetarian", "diet:halal": "halal", "diet:gluten_free": "gluten-free",
    "outdoor_seating": "outdoor seating", "internet_access": "wifi", "takeaway": "takeaway", "delivery": "delivery",
    "wheelchair": "wheelchair access", "payment:cards": "cards", "smoking": "smoking",
}


def _subcategory(tags: dict) -> str:
    for key in ("cuisine", "sport", "shop", "amenity", "leisure", "tourism"):
        v = tags.get(key)
        if v and key == "cuisine":
            return ", ".join(x.replace("_", " ") for x in v.split(";")[:3])
        if v and key == "sport":
            return v.replace("_", " ").replace(";", ", ")
    return ""


def _attributes(tags: dict) -> dict:
    out = {}
    for k, label in ATTRIBUTE_TAGS.items():
        v = tags.get(k, "")
        if v in ("yes", "only", "wlan", "free", "customers", "limited"):
            out[label] = v
    return out


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
            "street": tags.get("addr:street", ""),
            "category": _category(tags),
            "lat": float(lat), "lon": float(lon),
            "address": address,
            "website": tags.get("website") or tags.get("contact:website") or tags.get("url") or "",
            "email": tags.get("email") or tags.get("contact:email") or "",
            "phone": tags.get("phone") or tags.get("contact:phone") or "",
            "instagram": _instagram(tags),
            "chain": bool(tags.get("brand") or tags.get("brand:wikidata")),
            "subcategory": _subcategory(tags),
            "attributes": _attributes(tags),
            "opening_hours": tags.get("opening_hours", ""),
            "postcode": tags.get("addr:postcode", ""),
            "suburb": tags.get("addr:suburb", "") or tags.get("addr:district", ""),
            "osm_url": f"https://www.openstreetmap.org/{el.get('type', 'node')}/{el.get('id')}",
        })
    return out


SAME_VENUE_M = 150   # two OSM objects with the same name closer than this are one venue (node + building outline)


def _street_label(street: str) -> str:
    return re.sub(r"(?i)(stra(ß|ss)e|str)\.?$", "str.", street.strip())


def _osm_lead_name(db: DB, v: dict) -> str:
    """The lead name for an OSM venue. Generic names ("Späti", "Döner") are shared by many places: they are the same
    lead only for the same OSM object or within ~150 m; otherwise a separate lead named after the street."""
    from .leads import ensure_name_keys, name_key

    ensure_name_keys(db)
    key = name_key(v["name"])
    if not key:
        return v["name"]
    like = key.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + " (%"
    rows = db.q("SELECT name, lat, lon, source_urls FROM leads WHERE kind='merchant' AND (name_key=? OR name_key LIKE ? ESCAPE '\\') "
                "ORDER BY id", (key, like))
    for r in rows:
        if v["osm_url"] in json.loads(r["source_urls"] or "[]"):
            return r["name"]
    for r in rows:
        if r["lat"] is None or r["lon"] is None or haversine_m(v["lat"], v["lon"], r["lat"], r["lon"]) <= SAME_VENUE_M:
            return r["name"]   # no location yet (competitor listing, import), or the same place
    if not rows:
        return v["name"]
    taken = {name_key(r["name"]) for r in rows}
    for label in (_street_label(v.get("street") or ""), v.get("postcode") or "", "#" + v["osm_url"].rsplit("/", 1)[-1]):
        name = f"{v['name']} ({label})"
        if label and name_key(name) not in taken:
            return name
    return f"{v['name']} ({v['osm_url'].rsplit('/', 2)[-2]} {v['osm_url'].rsplit('/', 1)[-1]})"


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
            lead_id, created = upsert(db, _osm_lead_name(db, v), "merchant", source="osm:" + c["name"], source_url=v["osm_url"],
                                      category=v["category"], website=v["website"], email=v["email"], phone=v["phone"],
                                      instagram=v["instagram"], city="Berlin")
            if not lead_id:
                continue
            near, dist = nearest_campus(cfg, v["lat"], v["lon"])
            db.x("UPDATE leads SET lat=?, lon=?, address=coalesce(nullif(address,''), ?), campus=?, distance_m=? "
                 "WHERE id=? AND (distance_m IS NULL OR distance_m > ?)",
                 (v["lat"], v["lon"], v["address"], near["name"], dist, lead_id, dist))
            from .leadintel import area_for

            db.x("UPDATE leads SET subcategory=coalesce(nullif(subcategory,''), ?), opening_hours=coalesce(nullif(opening_hours,''), ?), "
                 "postcode=coalesce(nullif(postcode,''), ?), district=coalesce(nullif(district,''), ?), "
                 "attributes=coalesce(nullif(attributes,''), ?) WHERE id=?",
                 (v["subcategory"], v["opening_hours"], v["postcode"], v["suburb"] or area_for(v["lat"], v["lon"]),
                  json.dumps(v["attributes"], ensure_ascii=False) if v["attributes"] else "", lead_id))
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


# -- universities & colleges across the city (OSM) -----------------------------------------------------------

def universities_query(city: str = "Berlin") -> str:
    parts = "".join(f'nwr(area.c)["{k}"~"^({"|".join(v)})$"]["name"];' for k, v in UNI_TAGS.items())
    return (f'[out:json][timeout:120];area["name"="{city}"]["boundary"="administrative"]["admin_level"~"^(4|6)$"]->.c;'
            f"({parts});out center tags;")


_UNI_NOISE = re.compile(r"(?i)\b(institut[e]?|fakultät|faculty|department|fachbereich|lehrstuhl|bibliothek|library|mensa|"
                        r"cafeteria|hörsaal|lecture hall|gebäude|building|haus|wohnheim|dormitory|sporthalle)\b")


def _uni_name(tags: dict) -> str:
    """The institution, not the building: 'Freie Universität Berlin' rather than 'Institut für Informatik'."""
    for k in ("operator", "brand", "official_name", "name"):
        v = (tags.get(k) or "").strip()
        if v and not (k == "name" and _UNI_NOISE.search(v) and tags.get("operator")):
            return v
    return tags.get("name", "")


def cluster_universities(elements: list[dict], merge_m: int = 600) -> list[dict]:
    """Group the buildings of one institution that are close together into one campus (centroid + building count)."""
    points = []
    for el in elements:
        tags = el.get("tags") or {}
        lat = el.get("lat", (el.get("center") or {}).get("lat"))
        lon = el.get("lon", (el.get("center") or {}).get("lon"))
        if lat is None or lon is None or not tags.get("name"):
            continue
        points.append({"inst": _uni_name(tags), "name": tags["name"], "lat": float(lat), "lon": float(lon),
                       "type": tags.get("amenity", "university"), "website": tags.get("website") or tags.get("contact:website") or "",
                       "email": tags.get("email") or tags.get("contact:email") or "", "phone": tags.get("phone") or tags.get("contact:phone") or "",
                       "osm_url": f"https://www.openstreetmap.org/{el.get('type', 'node')}/{el.get('id')}"})
    clusters: list[dict] = []
    for p in points:
        home = next((c for c in clusters if c["institution"].casefold() == p["inst"].casefold()
                     and haversine_m(p["lat"], p["lon"], c["lat"], c["lon"]) <= merge_m), None)
        if home is None:
            clusters.append({"institution": p["inst"], "lat": p["lat"], "lon": p["lon"], "buildings": 1, "names": [p["name"]],
                             "type": p["type"], "website": p["website"], "email": p["email"], "phone": p["phone"],
                             "osm_url": p["osm_url"]})
            continue
        n = home["buildings"]
        home["lat"], home["lon"] = (home["lat"] * n + p["lat"]) / (n + 1), (home["lon"] * n + p["lon"]) / (n + 1)
        home["buildings"] = n + 1
        home["names"].append(p["name"])
        for k in ("website", "email", "phone"):
            home[k] = home[k] or p[k]
    seen: dict[str, int] = {}
    for c in clusters:
        main = c["institution"]
        area = area_label(c["lat"], c["lon"])
        same = [x for x in clusters if x["institution"] == main]
        name = f"{main} ({area})" if len(same) > 1 and area else main
        seen[name.casefold()] = seen.get(name.casefold(), 0) + 1
        c["name"] = name if seen[name.casefold()] == 1 else f"{name} #{seen[name.casefold()]}"   # names are keys: keep them unique
        c["lat"], c["lon"] = round(c["lat"], 5), round(c["lon"], 5)
    return clusters


def area_label(lat: float, lon: float) -> str:
    from .leadintel import area_for

    return area_for(lat, lon)


def discover_universities(cfg: Config, db: DB, *, fetch: Callable[[str], dict] | None = None, city: str | None = None) -> dict[str, Any]:
    """Find every university and college in the city on OpenStreetMap.

    Each institution becomes a `university` lead (Buddy platform + campus partnerships) and each campus cluster is
    added to the campus list, so the next `sz leadgen scan` also maps the venues around it."""
    urls = cfg.get("leadgen.overpass_urls")
    fetch = fetch or (lambda q: _overpass(q, urls))
    city = city or cfg.get("leadgen.city", "Berlin")
    data = fetch(universities_query(city))
    if not data.get("elements") or data.get("remark"):   # timeout / partial answer: keep the campus list we have
        raise RuntimeError(f"Overpass returned no complete university list for {city}: {data.get('remark') or 'no elements'}"[:300])
    clusters = cluster_universities(data["elements"], int(cfg.get("leadgen.campus_merge_m", 600)))
    known = CAMPUSES + (cfg.get("leadgen.campuses") or [])
    old = {c["name"]: c for c in discovered_campuses(cfg)}
    new_campuses, leads_new = [], 0
    for c in clusters:
        dup = any(haversine_m(c["lat"], c["lon"], k["lat"], k["lon"]) < 400 for k in known)
        if not dup:
            prev = old.get(c["name"], {})
            new_campuses.append({"name": c["name"], "lat": c["lat"], "lon": c["lon"], "buildings": c["buildings"],
                                 "type": c["type"], "source": "osm", "enabled": prev.get("enabled", c["buildings"] >= 1)})
        lead_id, created = upsert(db, c["institution"], "university", source="osm:universities", source_url=c["osm_url"],
                                  category="university" if c["type"] == "university" else "college hochschule",
                                  website=c["website"], email=c["email"], phone=c["phone"], city=city)
        if lead_id:
            db.x("UPDATE leads SET lat=coalesce(lat, ?), lon=coalesce(lon, ?) WHERE id=?", (c["lat"], c["lon"], lead_id))
            rescore(db, lead_id)
            leads_new += created
    # merge: campuses OSM didn't return this time (and the ones you switched off) stay as they were
    merged = {**old, **{c["name"]: c for c in new_campuses}}
    _campus_file(cfg).write_text(json.dumps(list(merged.values()), ensure_ascii=False, indent=1), encoding="utf-8")
    db.kv_set("last_run:universities", datetime.now().isoformat(timespec="seconds"))
    return {"institutions": len({c["institution"] for c in clusters}), "campuses": len(clusters),
            "added_campuses": len(new_campuses), "new_university_leads": leads_new}


def set_campus_enabled(cfg: Config, name: str, enabled: bool) -> bool:
    rows = discovered_campuses(cfg)
    hit = False
    for c in rows:
        if c["name"] == name:
            c["enabled"], hit = enabled, True
    if hit:
        _campus_file(cfg).write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    return hit
