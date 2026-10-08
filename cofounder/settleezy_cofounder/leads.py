"""Lead database: create, score, enrich (website / Impressum contacts) and export."""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path

from .db import DB, utcnow

# How valuable a partner category is for international students on a budget (0..1).
CATEGORY_WEIGHTS = [
    (r"restaurant|food|essen|café|cafe|coffee|bar|pub|pizza|burger|brunch|bakery|bäckerei|döner|sushi|grocery|supermarkt|supermarket|lebensmittel|bio ?markt|späti", 1.0),
    (r"gym|fitness|sport|yoga|climb|boulder|swim|bike|fahrrad", 0.9),
    (r"cinema|kino|museum|escape|event|club|concert|theater|entertainment|freizeit|bowling|karaoke", 0.9),
    (r"mobility|transport|scooter|travel|reise|bus|train|flix", 0.85),
    (r"university|hochschule|universität|college|international office", 1.0),
    (r"language|sprach|course|kurs|education|bildung|coding|tutor|career|cv|portfolio|workshop", 0.85),
    (r"tech|electronic|phone|handy|laptop|software|sim|mobile", 0.8),
    (r"housing|wohn|apartment|room|zimmer|coliving|student residence|insurance|versicherung|bank|blocked account", 0.95),
    (r"fashion|clothing|mode|shoes|sneaker", 0.7),
    (r"beauty|hair|friseur|barber|nail|cosmetic|kosmetik", 0.6),
    (r"spa|wellness|massage|sauna|therme", 0.5),
]
KIND_BASE = {"university": 30, "housing": 28, "service": 25, "brand": 22, "merchant": 20}


def category_weight(text: str) -> float:
    text = (text or "").lower()
    for rx, w in CATEGORY_WEIGHTS:
        if re.search(rx, text):
            return w
    return 0.4


def score(lead: dict) -> float:
    sources = json.loads(lead.get("sources") or "[]")
    s = KIND_BASE.get(lead.get("kind", ""), 15)
    s += 40 * category_weight(f"{lead.get('category', '')} {lead.get('name', '')}")
    s += min(len(sources), 3) * 6          # on several competitor platforms = proven discount appetite
    s += 8 if lead.get("email") else 0
    s += 4 if lead.get("instagram") else 0
    s += 4 if lead.get("website") else 0
    return round(min(s, 100), 1)


def _norm_name(name: str) -> str:
    name = re.sub(r"\s+", " ", name or "").strip(" -|,.")
    name = re.sub(r"\b(gmbh|ug|e\.?k\.?|ohg|kg|ag)\b\.?", "", name, flags=re.I).strip()
    return name[:120]


def upsert(db: DB, name: str, kind: str, *, source: str = "", source_url: str = "", **fields) -> tuple[int, bool]:
    name = _norm_name(name)
    if not name:
        return 0, False
    row = db.one("SELECT * FROM leads WHERE lower(name)=lower(?) AND kind=?", (name, kind))
    now = utcnow()
    if row:
        lead = dict(row)
        sources = set(json.loads(lead["sources"] or "[]"))
        urls = set(json.loads(lead["source_urls"] or "[]"))
        if source:
            sources.add(source)
        if source_url:
            urls.add(source_url)
        lead.update({k: v for k, v in fields.items() if v and not lead.get(k)})
        lead["sources"] = json.dumps(sorted(sources))
        lead["source_urls"] = json.dumps(sorted(urls)[:20])
        lead["score"] = score(lead)
        db.x(
            "UPDATE leads SET category=?,website=?,email=?,phone=?,instagram=?,city=?,sources=?,source_urls=?,score=?,updated_at=? WHERE id=?",
            (lead["category"], lead["website"], lead["email"], lead["phone"], lead["instagram"], lead["city"],
             lead["sources"], lead["source_urls"], lead["score"], now, lead["id"]),
        )
        return lead["id"], False
    lead = {"name": name, "kind": kind, "sources": json.dumps([source] if source else []),
            "source_urls": json.dumps([source_url] if source_url else []), **fields}
    lead["score"] = score(lead)
    cur = db.x(
        "INSERT INTO leads(name,kind,category,website,email,phone,instagram,city,sources,source_urls,score,status,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,'new',?,?)",
        (name, kind, fields.get("category", ""), fields.get("website", ""), fields.get("email", ""), fields.get("phone", ""),
         fields.get("instagram", ""), fields.get("city", "Berlin"), lead["sources"], lead["source_urls"], lead["score"], now, now),
    )
    return cur.lastrowid, True


def upsert_from_listing(db: DB, site: dict, item: dict) -> int:
    _, created = upsert(db, item["merchant"], site.get("lead_kind", "merchant"), source=site["name"], source_url=item["url"],
                        category=item.get("category", ""), city=item.get("city", "") or "Berlin")
    return int(created)


def import_seeds(db: DB, path: Path) -> int:
    n = 0
    with path.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            _, created = upsert(db, row["name"], row["kind"], source="seed:" + path.stem, category=row.get("category", ""),
                                website=row.get("website", ""), city=row.get("city", "Berlin"))
            n += created
    return n


def enrich(db: DB, fetcher, limit: int = 20) -> int:
    """For leads with a website but no email: read homepage + Impressum (legally required in Germany)."""
    from .scraping.extractors import contacts_from_html, impressum_link

    rows = db.q("SELECT * FROM leads WHERE website != '' AND (email IS NULL OR email = '') ORDER BY score DESC LIMIT ?", (limit,))
    done = 0
    for r in rows:
        site = r["website"] if r["website"].startswith("http") else "https://" + r["website"]
        home = fetcher.get(site, conditional=False)
        if home.status != 200:
            continue
        found = contacts_from_html(home.text)
        imp = impressum_link(home.text, home.url)
        if imp and not found["email"]:
            page = fetcher.get(imp, conditional=False)
            if page.status == 200:
                more = contacts_from_html(page.text)
                found = {k: found[k] or more[k] for k in found}
        if any(found.values()):
            upsert(db, r["name"], r["kind"], **found)
            done += 1
    return done


def top(db: DB, status: str | None = None, kind: str | None = None, limit: int = 20) -> list[dict]:
    sql, params = "SELECT * FROM leads WHERE 1=1", []
    if status:
        sql += " AND status=?"
        params.append(status)
    if kind:
        sql += " AND kind=?"
        params.append(kind)
    sql += " ORDER BY score DESC, created_at DESC LIMIT ?"
    params.append(limit)
    return [dict(r) for r in db.q(sql, params)]


def set_status(db: DB, lead_id: int, status: str, note: str = "") -> None:
    valid = {"new", "drafted", "contacted", "replied", "meeting", "partner", "lost"}
    if status not in valid:
        raise ValueError(f"status must be one of {sorted(valid)}")
    db.x(
        "UPDATE leads SET status=?, notes=trim(coalesce(notes,'') || ' ' || ?), updated_at=?, "
        "last_contact_at=CASE WHEN ? IN ('contacted','replied','meeting') THEN ? ELSE last_contact_at END WHERE id=?",
        (status, note, utcnow(), status, utcnow(), lead_id),
    )


def export_csv(db: DB, path: Path) -> int:
    rows = db.q("SELECT id,name,kind,category,score,status,email,phone,instagram,website,sources,source_urls,created_at FROM leads ORDER BY score DESC")
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(rows[0].keys() if rows else ["id"])
        for r in rows:
            w.writerow(list(r))
    return len(rows)


def pipeline(db: DB) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for r in db.q("SELECT kind, status, COUNT(*) n FROM leads GROUP BY kind, status"):
        out.setdefault(r["kind"], {})[r["status"]] = r["n"]
    return out
