"""Lead finder: "find me vegan cafés near HU with email that we haven't contacted" -> a full, ranked list.

Understands English and German, without needing an AI model:
  category / cuisine   café, restaurant, bar, späti, bakery, gym, yoga, climbing, cinema, museum, sushi, pizza,
                       vegan, vietnamese, italian, döner, burger, brunch, coworking, barber, beauty, housing ...
  area                 a campus ("near HU", "around TU", "FU Dahlem"), a district ("in Kreuzberg", "Neukölln"),
                       a postcode ("10997"), a distance ("within 500 m", "unter 300m")
  contacts             "with email / phone / instagram", "mit E-Mail"
  history              "not contacted", "new", "never pitched" | "contacted", "replied"
  platforms            "on Groupon", "not on any competitor", "only on Instagram"
  price                "cheap", "budget", "günstig", "€", "€€", "upscale"
  popularity           "popular", "over 5k followers", "> 2000 followers"
  sort + size          "most likely to join", "closest", "top 20", "all"
Anything it can't place is kept as free text and matched against name, category and sub-category.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from .config import Config
from .db import DB

CATEGORY_WORDS: list[tuple[str, str]] = [   # (pattern in the question, pattern in category/subcategory)
    (r"caf[eé]s?|coffee|kaffee|coffeeshop", r"cafe|café|coffee"),
    (r"restaurants?|food|essen|eat|lunch|dinner", r"restaurant|fast food|food"),
    (r"brunch|breakfast|frühstück", r"brunch|breakfast|cafe|café"),
    (r"bars?|pubs?|kneipen?|cocktails?|nightlife|clubs?", r"bar|pub|club|nightclub"),
    (r"sp[äa]tis?|kiosks?|convenience", r"späti|convenience|kiosk"),
    (r"bakery|bakeries|bäckerei(en)?|backerei", r"bakery|bäckerei"),
    (r"supermarkets?|grocer(y|ies)|supermarkt|bio ?markt|organic", r"supermarket|grocery|organic"),
    (r"gyms?|fitness", r"gym|fitness"),
    (r"yoga|pilates", r"yoga|pilates"),
    (r"climbing|boulder(ing)?|kletter", r"climb|boulder"),
    (r"sports?|sport", r"sport|gym|fitness|climb|boulder"),
    (r"cinemas?|kinos?|movies?", r"cinema|kino"),
    (r"museums?|galler(y|ies)", r"museum|gallery"),
    (r"theat(er|re)s?", r"theat"),
    (r"events?|entertainment|freizeit|activities|aktivitäten|escape ?rooms?|bowling|karaoke", r"cinema|museum|theat|bowling|escape|event|club|karaoke|entertainment"),
    (r"books?|bücher|bookshops?|buchhandlung", r"book"),
    (r"copy ?shops?|print(ing)?|druck", r"copy"),
    (r"bikes?|fahrrad|bicycles?", r"bike|bicycle|fahrrad"),
    (r"barbers?|hair|friseur(e)?|salons?", r"barber|hair|friseur"),
    (r"beauty|nails?|cosmetics?|kosmetik", r"beauty|nail|cosmetic"),
    (r"cowork(ing)?", r"cowork"),
    (r"housing|apartments?|wohnungen?|rooms?|zimmer|wg|student residences?", r"housing|apartment|room|wohn|residence"),
    (r"language schools?|sprachschulen?|courses?|kurse", r"language|course|kurs|sprach"),
]
CUISINES = ["vegan", "vegetarian", "vietnamese", "thai", "japanese", "sushi", "ramen", "korean", "chinese", "indian",
            "italian", "pizza", "pasta", "turkish", "döner", "kebab", "falafel", "arabic", "lebanese", "syrian",
            "mexican", "burger", "american", "greek", "spanish", "french", "german", "asian", "ice cream", "bubble tea",
            "poke", "salad", "healthy", "halal", "gluten"]
CAMPUS_ALIASES = {
    r"\bhu\b|humboldt": "HU Berlin (Mitte)", r"adlershof": "HU Berlin (Adlershof)", r"\btu\b|technische": "TU Berlin",
    r"\bfu\b|freie universit|dahlem": "FU Berlin", r"\budk\b|universität der künste|arts": "UdK Berlin",
    r"\bhtw\b|treskowallee": "HTW Berlin (Treskowallee)", r"wilhelminenhof|schöneweide": "HTW Berlin (Wilhelminenhof)",
    r"\bbht\b|beuth": "BHT Berlin", r"\bhwr\b": "HWR Berlin", r"charit[eé]": "Charité", r"\bash\b|alice salomon": "ASH Berlin",
    r"hertie": "Hertie School", r"\besmt\b": "ESMT Berlin", r"\bcode\b": "CODE University", r"\bsrh\b": "SRH Berlin",
}
COMPETITORS = {"groupon": "Groupon", "vspots": "vspots", "top ?10": "Top10 Berlin", "unidays": "UNiDAYS", "student ?beans": "Student Beans"}


@dataclass
class LeadQuery:
    categories: list[str] = field(default_factory=list)     # regexes on category/subcategory
    cuisines: list[str] = field(default_factory=list)
    kinds: list[str] = field(default_factory=list)
    campus: str = ""
    districts: list[str] = field(default_factory=list)
    postcodes: list[str] = field(default_factory=list)
    max_distance_m: int | None = None
    need: list[str] = field(default_factory=list)            # email | phone | instagram | website
    contacted: bool | None = None                            # False = never contacted
    statuses: list[str] = field(default_factory=list)
    on_platforms: list[str] = field(default_factory=list)
    not_on_competitors: bool = False
    instagram_only: bool = False
    max_price: int | None = None
    min_price: int | None = None
    min_followers: int | None = None
    sort: str = "likelihood"                                 # likelihood | distance | score | followers
    limit: int = 50
    include_partners: bool = False
    text: list[str] = field(default_factory=list)
    understood: list[str] = field(default_factory=list)


def _num(s: str) -> int:
    s = s.lower().replace(".", "").replace(",", "")
    mult = 1000 if s.endswith("k") else 1
    return int(float(s.rstrip("k")) * mult)


def parse(question: str) -> LeadQuery:
    from .leadintel import AREA_ALIASES, AREAS

    q = LeadQuery()
    t = " " + question.lower().strip() + " "
    used: list[tuple[int, int]] = []

    def take(m: re.Match | None, note: str) -> bool:
        if m:
            used.append(m.span())
            q.understood.append(note)
        return bool(m)

    for qp, cat in CATEGORY_WORDS:
        m = re.search(rf"\b(?:{qp})\b", t)
        if m and take(m, "category: " + m.group(0).strip()):
            q.categories.append(cat)
    for c in CUISINES:
        m = re.search(rf"\b{re.escape(c)}\w*", t)
        if m and take(m, "cuisine: " + c):
            q.cuisines.append("döner|kebab" if c in ("döner", "kebab") else "sushi|japanese" if c == "sushi" else c)
    for rx, kind in [(r"\buniversit(y|ies)\b|\bhochschulen?\b|international offices?", "university"),
                     (r"\bbrands?\b|\bonline\b|\bmarken\b|own products?|eigene produkte", "brand"),
                     (r"\bservices?\b|\bdienstleist\w*", "service"),
                     (r"\b(creators?|influencers?|bloggers?|content creators?)\b", "creator")]:
        if kind == "university" and re.search(r"\b(near|around|at|close to|bei|nahe|an der)\s+\w*\s*universit", t):
            continue
        for m in re.finditer(rx, t):   # consume every mention ("brands with own products")
            used.append(m.span())
            if kind not in q.kinds:
                q.kinds.append(kind)
                q.understood.append("kind: " + kind)
    for rx, campus in CAMPUS_ALIASES.items():
        m = re.search(rx, t)
        if m:
            take(m, "campus: " + campus)
            q.campus = campus
            break
    for name, _, _ in AREAS:
        m = re.search(rf"\b{re.escape(name.lower())}\b", t)
        if m and take(m, "area: " + name):
            q.districts.append(name)
    for alias, name in AREA_ALIASES.items():
        m = re.search(rf"\b{re.escape(alias)}\b", t)
        if m and name not in q.districts and take(m, "area: " + name):
            q.districts.append(name)
    for m in re.finditer(r"\b1[0-4]\d{3}\b", t):
        take(m, "postcode: " + m.group(0))
        q.postcodes.append(m.group(0))
    m = re.search(r"(?:within|under|less than|max(?:imum)?|unter|innerhalb(?: von)?|bis)\s*(\d+(?:[.,]\d+)?)\s*(km|m|meter|metres|meters)\b", t) \
        or re.search(r"\b(\d+(?:[.,]\d+)?)\s*(km|m)\s*(?:radius|walk|from|von|um)\b", t)
    if m and take(m, "distance"):
        v = float(m.group(1).replace(",", "."))
        q.max_distance_m = int(v * 1000 if m.group(2) == "km" else v)
    elif re.search(r"\b(walking distance|fußläufig|nearby|in der nähe)\b", t):
        q.max_distance_m = 800
    for word, key in [(r"e-?mails?|mails?", "email"), (r"phones?|telefon(nummer)?|numbers?", "phone"),
                      (r"instagram|insta|\big\b", "instagram"), (r"websites?|webseiten?", "website")]:
        m = re.search(rf"\b(?:with|mit|having|has|incl\.?|including)\s+(?:an?\s+|eine?r?\s+)?(?:\w+\s+)?(?:{word})\b", t)
        if m and take(m, "needs " + key):
            q.need.append(key)
    m = re.search(r"\b(not|never|un|nicht|noch nicht|nie|haven'?t|have not|hasn'?t|didn'?t|did not)\s*(yet\s+|been\s+|ever\s+)?"
                  r"(contacted|contact|pitched|pitch|reached(?: out to)?|kontaktiert|angeschrieben)\b(\s+yet)?|\bfresh leads?\b|\bnew leads?\b", t)
    if m and take(m, "not contacted before"):
        q.contacted = False
    else:
        m = re.search(r"\b(already\s+)?(contacted|pitched|kontaktiert|angeschrieben)\b", t)
        if m and take(m, "contacted before"):
            q.contacted = True
    for st, rx in [("replied", r"\b(replied|answered|geantwortet)\b"), ("meeting", r"\b(meeting stage|met with)\b")]:
        m = re.search(rx, t)
        if m and take(m, "status: " + st):
            q.statuses.append(st)
    m = re.search(r"\b(not|no|nicht|keine?)\s+(on|listed on|auf|bei)?\s*(any\s+)?(competitors?|other platforms?|discount platforms?|konkurrenz)", t)
    if m and take(m, "not on competitor platforms"):
        q.not_on_competitors = True
    m = re.search(r"\b(only on instagram|instagram[- ]only|nur auf instagram|not on google)\b", t)
    if m and take(m, "Instagram-only venues"):
        q.instagram_only = True
    for rx, name in COMPETITORS.items():
        m = re.search(rf"\b(?:on|listed on|auf|bei|from)\s+{rx}", t)
        if m and take(m, "listed on " + name):
            q.on_platforms.append(name)
    if (m := re.search(r"\b(cheap|budget|affordable|günstig|billig|preiswert)\b", t)) and take(m, "price: €"):
        q.max_price = 1
    elif (m := re.search(r"\b(mid[- ]?range|moderate)\b", t)) and take(m, "price: €€"):
        q.max_price, q.min_price = 2, 2
    elif (m := re.search(r"\b(upscale|expensive|fancy|premium|teuer|gehoben)\b", t)) and take(m, "price: €€€+"):
        q.min_price = 3
    elif (m := re.search(r"(?<![\w€])(€{1,4})(?![\w€])", t)) and take(m, "price: " + m.group(1)):
        q.max_price = q.min_price = len(m.group(1))
    m = re.search(r"(?:over|more than|above|>|mehr als|über)\s*(\d+(?:[.,]\d+)?k?)\s*followers?", t) or \
        re.search(r"(\d+(?:[.,]\d+)?k?)\+?\s*followers?", t)
    if m and take(m, "min followers"):
        q.min_followers = _num(m.group(1))
    elif (m := re.search(r"\b(popular|beliebt|trending|hyped|well[- ]known)\b", t)) and take(m, "popular"):
        q.min_followers, q.sort = 1000, "followers"
    if (m := re.search(r"\b(closest|nearest|nächste)\b", t)) and take(m, "sort: closest"):
        q.sort = "distance"
    elif (m := re.search(r"\b(?:(?:most|best|highest)\s+(?:likely|chance|potential)(?:\s+to\s+(?:join|sign|convert))?|likely to join)\b", t)) \
            and take(m, "sort: likelihood"):
        q.sort = "likelihood"
    elif (m := re.search(r"\b(most followers|biggest)\b", t)) and take(m, "sort: followers"):
        q.sort = "followers"
    if (m := re.search(r"\b(?:top|first|erste[n]?)\s*(\d{1,3})\b|\b(\d{1,3})\s+(?:leads|venues|places|cafes|cafés|restaurants|orte)\b", t)) and take(m, "limit"):
        q.limit = int(m.group(1) or m.group(2))
    elif (m := re.search(r"\b(all|every|alle)\b", t)) and take(m, "all results"):
        q.limit = 1000
    if (m := re.search(r"\b(?:include|including|incl\.?|mit)\s+(?:existing\s+)?partners?\b|\bpartners? too\b", t)) \
            and take(m, "including partners"):
        q.include_partners = True
    # leftover words become free-text matching (names, unusual categories)
    rest = list(t)
    for a, b in used:
        rest[a:b] = " " * (b - a)
    stop = {"find", "me", "show", "list", "get", "give", "leads", "lead", "venues", "venue", "places", "place", "in", "near",
            "around", "at", "the", "a", "an", "and", "or", "with", "that", "who", "which", "we", "i", "have", "haven't", "for",
            "of", "to", "on", "from", "all", "some", "any", "please", "berlin", "finde", "zeig", "mir", "alle", "in", "der",
            "die", "das", "mit", "und", "oder", "von", "bei", "nahe", "um", "für", "is", "are", "not", "yet", "close", "by",
            "students", "student", "studenten", "partner", "potential", "good", "best", "new", "options", "spots", "shops",
            "stores", "businesses", "nähe", "der", "den", "dem"}
    q.text = [w for w in re.findall(r"[\wäöüß'&]{3,}", "".join(rest)) if w not in stop]
    return q


def find(cfg: Config, db: DB, question: str | LeadQuery, *, discover: bool = False, fetch=None) -> dict[str, Any]:
    """Run a question against the lead database. With discover=True and too few results near a campus,
    scan OpenStreetMap around that campus first (once a day)."""
    from .leadintel import area_for, competitor_platforms, platforms, price_prior, profile
    from .pipeline import rates as conv_rates

    q = parse(question) if isinstance(question, str) else question
    discovered = None

    def query() -> list[dict]:
        sql, params = "SELECT * FROM leads WHERE status != 'lost'", []
        if "creator" not in q.kinds:
            sql += " AND kind != 'creator'"   # creators are for marketing collabs; only shown when asked for
        if not q.include_partners:
            sql += " AND status != 'partner' AND id NOT IN (SELECT lead_id FROM partners WHERE lead_id IS NOT NULL)"
        if q.kinds:
            sql += f" AND kind IN ({','.join('?' * len(q.kinds))})"
            params += q.kinds
        if q.campus:
            sql += " AND campus LIKE ?"
            params.append(q.campus.split(" (")[0] + "%")
        if q.max_distance_m is not None:
            sql += " AND distance_m IS NOT NULL AND distance_m <= ?"
            params.append(q.max_distance_m)
        for k in q.need:
            sql += f" AND {k} IS NOT NULL AND {k} != ''"
        if q.statuses:
            sql += f" AND status IN ({','.join('?' * len(q.statuses))})"
            params += q.statuses
        if q.postcodes:
            sql += f" AND (postcode IN ({','.join('?' * len(q.postcodes))}) OR " + " OR ".join("address LIKE ?" for _ in q.postcodes) + ")"
            params += q.postcodes + [f"%{p}%" for p in q.postcodes]
        if q.min_followers:
            sql += " AND ig_followers >= ?"
            params.append(q.min_followers)
        rows = [dict(r) for r in db.q(sql + " ORDER BY score DESC LIMIT 5000", params)]
        out = []
        for r in rows:
            hay = f"{r['name']} {r['category'] or ''} {r['subcategory'] or ''} {r.get('attributes') or ''}".lower()
            if q.categories and not any(re.search(c, f"{r['category'] or ''} {r['subcategory'] or ''} {r['name']}".lower()) for c in q.categories):
                continue
            if q.cuisines and not all(re.search(c, hay) for c in q.cuisines):
                continue
            if q.text and not all(w in hay for w in q.text):
                continue
            if q.districts:
                d = (r.get("district") or area_for(r.get("lat"), r.get("lon")) or "").lower()
                addr = (r.get("address") or "").lower()
                if not any(x.lower() in d or x.lower() in addr for x in q.districts):
                    continue
            comp = competitor_platforms(r)
            if q.not_on_competitors and comp:
                continue
            if q.on_platforms and not all(p in comp for p in q.on_platforms):
                continue
            if q.instagram_only and not (r.get("instagram") and set(platforms(r)) <= {"Instagram", "Added by you", "Imported"}):
                continue
            lvl = r.get("price_level") or price_prior(r.get("category") or "")
            if q.max_price and (not lvl or lvl > q.max_price):
                continue
            if q.min_price and (not lvl or lvl < q.min_price):
                continue
            out.append(r)
        return out

    rows = query()
    if discover and len(rows) < 5 and q.campus:
        from .leadgen import discover as osm

        try:
            discovered = osm(cfg, db, q.campus.split(" (")[0], fetch=fetch)
            rows = query()
        except Exception as exc:   # discovery is a bonus; never fail the search
            discovered = {"error": str(exc)}
    r8 = conv_rates(db)
    profiles = [profile(db, r, r8) for r in rows]
    if q.contacted is not None:
        profiles = [p for p in profiles if p["contacted_before"] == q.contacted]
    key = {"distance": lambda p: (p["distance_m"] is None, p["distance_m"] or 0),
           "followers": lambda p: -(p["ig_followers"] or 0),
           "score": lambda p: -(p["score"] or 0)}.get(q.sort, lambda p: (-p["join_likelihood"], -(p["score"] or 0)))
    profiles.sort(key=key)
    total = len(profiles)
    return {"question": question if isinstance(question, str) else "", "understood": q.understood, "query": asdict(q),
            "total": total, "results": profiles[:q.limit], "discovered": discovered,
            "missing_contacts": sum(1 for p in profiles[:q.limit] if not p["email"] and not p["phone"])}


def to_csv(results: list[dict]) -> str:
    from .leadintel import EXPORT_COLUMNS

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(EXPORT_COLUMNS)
    for p in results:
        w.writerow(["; ".join(p[c]) if isinstance(p.get(c), list) else ("yes" if p.get(c) is True else "no" if p.get(c) is False else
                    (f"{p[c]:.0%}" if c == "join_likelihood" else p.get(c, ""))) for c in EXPORT_COLUMNS])
    return buf.getvalue()


def spoken(res: dict) -> str:
    n, rs = res["total"], res["results"]
    if not n:
        return "I couldn't find any leads matching that. Try a wider area, or let me scan the campus on OpenStreetMap."
    top = "; ".join(f"{p['name']}{', ' + p['district'] if p['district'] else ''}" for p in rs[:3])
    extra = f" {res['missing_contacts']} of them still need contact details: I can read their Impressum." if res["missing_contacts"] else ""
    return f"I found {n} lead{'s' if n != 1 else ''}. Top: {top}. The full list is on the dashboard.{extra}"
