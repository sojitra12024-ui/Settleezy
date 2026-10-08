"""Lead intelligence: everything you need to judge and contact a venue, on one row.

For every lead Setz keeps (or works out on demand):
  category / sub-category   from OpenStreetMap tags (cuisine, shop type, diet:vegan...) and the venue's own site
  area                      Berlin district / neighbourhood, postcode, nearest campus + walking distance
  price level               € .. €€€€ from menu prices on the website, else estimated from the category
  platforms                 where it's already listed: Groupon, vspots, Top10 Berlin, UNiDAYS, Student Beans,
                            Instagram, OpenStreetMap (being on a discount platform = proven discount appetite)
  contacts (Impressum)      email, phone, owner / managing director, legal name and address; German sites must
                            publish an Impressum, so this is the most reliable public contact source
  Instagram                 handle, followers, bio (Instagram business discovery)
  contacted before          from your mailbox, drafts and pipeline history (never pitch twice by accident)
  likelihood of joining     neural reply model (if trained) x your real conversion rates
"""

from __future__ import annotations

import json
import re
import statistics
from typing import Any
from urllib.parse import urljoin, urlparse

from .db import DB, utcnow

# Neighbourhood centroids (approximate) used to label an area when OSM has no addr:suburb.
AREAS = [
    ("Mitte", 52.5200, 13.4050), ("Prenzlauer Berg", 52.5390, 13.4240), ("Friedrichshain", 52.5150, 13.4540),
    ("Kreuzberg", 52.4970, 13.4030), ("Neukölln", 52.4810, 13.4350), ("Wedding", 52.5500, 13.3600),
    ("Moabit", 52.5260, 13.3400), ("Tiergarten", 52.5100, 13.3600), ("Charlottenburg", 52.5160, 13.3040),
    ("Wilmersdorf", 52.4870, 13.3150), ("Schöneberg", 52.4830, 13.3530), ("Tempelhof", 52.4660, 13.3850),
    ("Steglitz", 52.4570, 13.3200), ("Dahlem", 52.4570, 13.2880), ("Zehlendorf", 52.4330, 13.2590),
    ("Friedenau", 52.4710, 13.3300), ("Lichtenberg", 52.5150, 13.4990), ("Karlshorst", 52.4840, 13.5280),
    ("Pankow", 52.5690, 13.4020), ("Treptow", 52.4920, 13.4720), ("Oberschöneweide", 52.4600, 13.5180),
    ("Adlershof", 52.4350, 13.5450), ("Köpenick", 52.4450, 13.5760), ("Marzahn", 52.5440, 13.5640),
    ("Hellersdorf", 52.5370, 13.6050), ("Spandau", 52.5360, 13.2000), ("Reinickendorf", 52.5880, 13.3270),
    ("Weißensee", 52.5540, 13.4630), ("Gesundbrunnen", 52.5500, 13.3880), ("Westend", 52.5150, 13.2700),
]
AREA_ALIASES = {"xberg": "Kreuzberg", "fhain": "Friedrichshain", "p-berg": "Prenzlauer Berg", "prenzlberg": "Prenzlauer Berg",
                "nk": "Neukölln", "neukoelln": "Neukölln", "schoeneberg": "Schöneberg", "koepenick": "Köpenick"}

PLATFORM_NAMES = {"groupon": "Groupon", "vspots": "vspots", "top10berlin": "Top10 Berlin", "top10": "Top10 Berlin",
                  "unidays": "UNiDAYS", "studentbeans": "Student Beans", "osm": "OpenStreetMap", "instagram": "Instagram",
                  "seed": "Seed list", "import": "Imported", "manual": "Added by you"}

# category -> typical price level when the menu can't be read (1 = €, 4 = €€€€)
PRICE_PRIORS = [(r"späti|convenience|kiosk|bakery|bäckerei|fast food|döner|imbiss|copyshop", 1),
                (r"cafe|café|coffee|ice cream|supermarket|grocery|books|bike", 2),
                (r"restaurant|bar|pub|cinema|museum|bowling|gym|fitness|sport", 2),
                (r"cocktail|wine|fine dining|steak|sushi|theatre|theater|spa|club", 3)]


def area_for(lat: float | None, lon: float | None) -> str:
    if lat is None or lon is None:
        return ""
    from .leadgen import haversine_m

    name, d = min(((a, haversine_m(lat, lon, la, lo)) for a, la, lo in AREAS), key=lambda t: t[1])
    return name if d <= 3500 else ""


def platforms(lead: dict) -> list[str]:
    out = []
    for s in json.loads(lead.get("sources") or "[]"):
        key = s.split(":")[0].lower()
        name = PLATFORM_NAMES.get(key, s)
        if name not in out:
            out.append(name)
    if lead.get("instagram") and "Instagram" not in out:
        out.append("Instagram")
    return out


def competitor_platforms(lead: dict) -> list[str]:
    return [p for p in platforms(lead) if p in {"Groupon", "vspots", "Top10 Berlin", "UNiDAYS", "Student Beans"}]


# -- price ------------------------------------------------------------------------------------

_PRICE = re.compile(r"(?:€\s?(\d{1,3}(?:[.,]\d{1,2})?)|(\d{1,3}(?:[.,]\d{1,2})?)\s?(?:€|eur\b|euro\b))", re.I)


def prices_from_text(text: str) -> list[float]:
    out = []
    for m in _PRICE.finditer(text or ""):
        v = float((m.group(1) or m.group(2)).replace(",", "."))
        if 0.5 <= v <= 150:
            out.append(v)
    return out


def price_level_from_prices(prices: list[float], category: str = "") -> int | None:
    if len(prices) < 4:
        return None
    med = statistics.median(prices)
    cafe = bool(re.search(r"cafe|café|coffee|bakery|ice cream", category or "", re.I))
    cuts = (3.5, 6, 10) if cafe else (8, 15, 28)
    return 1 + sum(med > c for c in cuts)


def price_prior(category: str) -> int | None:
    for rx, lvl in reversed(PRICE_PRIORS):
        if re.search(rx, category or "", re.I):
            return lvl
    return None


def price_label(level: int | None) -> str:
    return "€" * level if level else ""


# -- Impressum ----------------------------------------------------------------------------------

_OWNER = re.compile(r"(?:Geschäftsführer(?:in)?|Geschäftsführung|Inhaber(?:in)?|Vertreten durch|Vertretungsberechtigt(?:e[rn]?)?"
                    r"|Managing Director|Owner|Verantwortlich(?: für den Inhalt)?(?: nach § ?\d+ ?\w*)?)\s*[:\-]?\s*"
                    r"([A-ZÄÖÜ][\w\-.äöüß]+(?:[ \t]+(?:von|van|de|zu)?[ \t]*[A-ZÄÖÜ][\w\-.äöüß]+){1,3})")
_NOT_NAME = {"kontakt", "telefon", "tel", "e-mail", "email", "anschrift", "adresse", "registereintrag", "umsatzsteuer",
             "handelsregister", "registergericht", "fax", "mail", "internet", "web", "sitz", "ust"}
_LEGAL = re.compile(r"\b([A-ZÄÖÜ0-9][\w&.\- äöüß]{1,60}?\s(?:GmbH(?: & Co\. KG)?|UG(?: \(haftungsbeschränkt\))?|e\.K\.|OHG|KG|AG|GbR|e\.V\.))")
_ADDRESS = re.compile(r"([A-ZÄÖÜ][\w.\-äöüß]+(?:[ \-][A-Za-zÄÖÜäöüß.\-]+){0,3}\s\d{1,4}\s?[a-zA-Z]?)\s*,?\s*(1[0-4]\d{3})\s+Berlin")
_TEL = re.compile(r"(?:Tel(?:efon)?|Phone|Fon|Mobil)\.?\s*[:.]?\s*((?:\+|00|0)[\d\s/().\-]{6,22}\d)", re.I)


def parse_impressum(html: str) -> dict[str, str]:
    """Owner / managing director, legal name, address, phone and email from a German Impressum page."""
    from bs4 import BeautifulSoup

    from .scraping.extractors import contacts_from_html

    text = BeautifulSoup(html, "lxml").get_text("\n", strip=True)
    flat = re.sub(r"\s+", " ", text)
    out = {k: "" for k in ("owner", "legal_name", "address", "postcode", "phone", "email", "instagram")}
    m = _OWNER.search(text) or _OWNER.search(flat)
    if m:
        words = re.sub(r"\s+", " ", m.group(1)).strip(" .,").split(" ")
        while words and words[-1].lower().strip(".:") in _NOT_NAME:   # "Lena Schmidt Kontakt" -> "Lena Schmidt"
            words.pop()
        out["owner"] = " ".join(words) if len(words) >= 2 else ""
    for line in text.splitlines():   # line by line, so "§ 5 TMG" on the line above isn't glued on
        m = _LEGAL.search(line)
        if m and not re.match(r"\s*(angaben|gemäß|§)", m.group(1), re.I):
            out["legal_name"] = m.group(1).strip()
            break
    m = _ADDRESS.search(flat)
    if m:
        out["address"], out["postcode"] = f"{m.group(1).strip()}, {m.group(2)} Berlin", m.group(2)
    m = _TEL.search(flat)
    c = contacts_from_html(html)
    out["phone"] = re.sub(r"\s+", " ", m.group(1)).strip() if m else c["phone"]
    out["email"], out["instagram"] = c["email"], c["instagram"]
    return out


def _menu_link(html: str, base: str) -> str:
    from bs4 import BeautifulSoup

    for a in BeautifulSoup(html, "lxml").find_all("a", href=True):
        label = (a.get_text(" ", strip=True) + " " + a["href"]).lower()
        if re.search(r"speisekarte|menu|karte\b|preise|prices|getränke", label) and not a["href"].startswith(("mailto:", "tel:")):
            return urljoin(base, a["href"])
    return ""


def enrich_lead(db: DB, fetcher, lead_id: int) -> dict[str, Any]:
    """Read the venue's homepage, menu and Impressum; fill in what's missing. Returns what was found."""
    from bs4 import BeautifulSoup

    from .leads import rescore
    from .scraping.extractors import contacts_from_html, impressum_link

    row = db.one("SELECT * FROM leads WHERE id=?", (lead_id,))
    if not row:
        raise ValueError(f"no lead {lead_id}")
    lead = dict(row)
    site = lead["website"] or ""
    if not site:
        return {"error": "no website on file"}
    site = site if site.startswith("http") else "https://" + site
    home = fetcher.get(site, conditional=False)
    if home.status != 200:
        return {"error": f"website answered {home.status}"}
    found: dict[str, Any] = {**contacts_from_html(home.text), "owner": "", "legal_name": "", "address": "", "postcode": ""}
    imp = impressum_link(home.text, home.url)
    if imp:
        page = fetcher.get(imp, conditional=False)
        if page.status == 200:
            im = parse_impressum(page.text)
            found.update({k: v or found.get(k, "") for k, v in im.items()})
    prices = prices_from_text(BeautifulSoup(home.text, "lxml").get_text(" "))
    menu = _menu_link(home.text, home.url)
    if menu and urlparse(menu).netloc == urlparse(home.url).netloc and len(prices) < 6:
        mp = fetcher.get(menu, conditional=False)
        if mp.status == 200:
            prices += prices_from_text(BeautifulSoup(mp.text, "lxml").get_text(" "))
    level = price_level_from_prices(prices, lead["category"] or "")
    upd: dict[str, Any] = {k: found[k] for k in ("email", "phone", "instagram", "owner", "legal_name", "postcode")
                           if found.get(k) and not lead.get(k)}
    if found.get("address") and not lead.get("address"):
        upd["address"] = found["address"]
    if level:
        upd["price_level"], upd["price_source"] = level, "menu"
    upd["enriched_at"] = utcnow()
    db.x(f"UPDATE leads SET {', '.join(f'{k}=?' for k in upd)}, updated_at=? WHERE id=?", [*upd.values(), utcnow(), lead_id])
    rescore(db, lead_id)
    return {k: v for k, v in {**found, "price_level": level, "prices_seen": len(prices)}.items() if v}


def enrich_batch(db: DB, fetcher, limit: int = 20, ids: list[int] | None = None) -> dict[str, int]:
    """Leads with a website that haven't been enriched in 30 days, best first (or the given ids)."""
    if ids:
        rows = [{"id": i} for i in ids]
    else:
        rows = db.q("SELECT id FROM leads WHERE website != '' AND status NOT IN ('partner','lost') AND "
                    "(enriched_at IS NULL OR enriched_at < datetime('now','-30 day')) ORDER BY "
                    "(email IS NULL OR email = '') DESC, score DESC LIMIT ?", (limit,))
    done = found_email = 0
    for r in rows:
        before = db.one("SELECT email FROM leads WHERE id=?", (r["id"],))
        res = enrich_lead(db, fetcher, r["id"])
        if "error" not in res:
            done += 1
            if not (before and before["email"]) and res.get("email"):
                found_email += 1
    return {"enriched": done, "new_emails": found_email}


# -- history + likelihood ---------------------------------------------------------------------------

def _domain(lead: dict) -> str:
    if lead.get("email") and "@" in lead["email"]:
        d = lead["email"].split("@", 1)[1].lower()
        if d not in {"gmail.com", "web.de", "gmx.de", "gmx.net", "outlook.com", "hotmail.com", "yahoo.com", "t-online.de", "icloud.com"}:
            return d
    if lead.get("website"):
        return urlparse(lead["website"] if "://" in lead["website"] else "https://" + lead["website"]).netloc.lower().removeprefix("www.")
    return ""


def contact_history(db: DB, lead: dict) -> dict[str, Any]:
    """Have we been in touch before? Mailbox (by address or domain), drafts and pipeline history."""
    email, dom = (lead.get("email") or "").lower(), _domain(lead)
    msgs: list[Any] = []
    if email or dom:
        like = f"%@{dom}%" if dom else f"%{email}%"
        msgs = db.q("SELECT folder, sent_at FROM messages WHERE (from_addr=? OR from_addr LIKE ? OR to_addrs LIKE ?) "
                    "ORDER BY sent_at DESC LIMIT 50", (email or "-", like, like))
    drafts = db.one("SELECT COUNT(*) n, MAX(created_at) last FROM drafts WHERE lead_id=?", (lead["id"],)) if lead.get("id") else None
    stage = lead.get("status") or "new"
    sent = [m for m in msgs if m["folder"] == "sent"]
    got = [m for m in msgs if m["folder"] == "inbox"]
    last = max([m["sent_at"] for m in msgs] + [lead.get("last_contact_at") or ""] + [(drafts["last"] if drafts else None) or ""]) or None
    contacted = bool(sent or got or (drafts and drafts["n"]) or stage in {"contacted", "replied", "meeting", "partner", "lost"}
                     or lead.get("last_contact_at"))
    return {"contacted": contacted, "emails_sent": len(sent), "emails_received": len(got),
            "drafts": drafts["n"] if drafts else 0, "last_contact": (last or "")[:10] or None, "replied": bool(got) or stage in {"replied", "meeting", "partner"}}


def join_likelihood(db: DB, lead: dict, rates: dict[str, float] | None = None) -> float:
    """P(this lead becomes a partner) = P(reply) x P(meeting | reply) x P(partner | meeting)."""
    from .brain import reply_chance
    from .pipeline import rates as conv_rates

    r = rates or conv_rates(db)
    stage = lead.get("status") or "new"
    if stage == "partner":
        return 1.0
    if stage == "lost":
        return 0.0
    rm, mp = r["replied→meeting"], r["meeting→partner"]
    if stage == "meeting":
        return round(mp, 3)
    if stage == "replied":
        return round(rm * mp, 3)
    p_reply = reply_chance(db, lead)
    if p_reply is None:   # no trusted model yet: base rate nudged by the lead score
        p_reply = r["contacted→replied"] * (0.6 + 0.8 * (lead.get("score") or 50) / 100)
    return round(max(0.0, min(1.0, p_reply * rm * mp)), 3)


def likelihood_label(p: float) -> str:
    return "high" if p >= 0.15 else "medium" if p >= 0.07 else "low"


def profile(db: DB, lead: dict, rates: dict[str, float] | None = None) -> dict[str, Any]:
    """One row with everything (used by the lead finder, exports, the dashboard and Setz's answers)."""
    hist = contact_history(db, lead)
    p = join_likelihood(db, lead, rates)
    attrs = json.loads(lead.get("attributes") or "{}")
    level = lead.get("price_level") or price_prior(lead.get("category") or "")
    return {
        "id": lead["id"], "name": lead["name"], "kind": lead["kind"], "category": lead.get("category") or "",
        "subcategory": lead.get("subcategory") or "", "status": lead.get("status"),
        "address": lead.get("address") or "", "district": lead.get("district") or area_for(lead.get("lat"), lead.get("lon")),
        "postcode": lead.get("postcode") or "", "campus": lead.get("campus") or "", "distance_m": lead.get("distance_m"),
        "price": price_label(level), "price_source": lead.get("price_source") or ("estimated" if level else ""),
        "email": lead.get("email") or "", "phone": lead.get("phone") or "", "website": lead.get("website") or "",
        "instagram": lead.get("instagram") or "", "ig_followers": lead.get("ig_followers"),
        "owner": lead.get("owner") or "", "legal_name": lead.get("legal_name") or "",
        "opening_hours": lead.get("opening_hours") or "", "attributes": attrs,
        "platforms": platforms(lead), "on_competitors": competitor_platforms(lead),
        "contacted_before": hist["contacted"], "last_contact": hist["last_contact"], "history": hist,
        "join_likelihood": p, "likelihood": likelihood_label(p), "score": lead.get("score"),
        "lat": lead.get("lat"), "lon": lead.get("lon"), "enriched_at": lead.get("enriched_at"),
    }


EXPORT_COLUMNS = ["name", "category", "subcategory", "address", "district", "postcode", "campus", "distance_m", "price",
                  "email", "phone", "instagram", "ig_followers", "website", "owner", "legal_name", "opening_hours",
                  "platforms", "contacted_before", "last_contact", "status", "join_likelihood", "score"]
