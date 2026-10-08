"""Turn pages into listings without depending on fragile CSS selectors.

Strategies (configured per site in competitors.toml, tried in order):
  sitemap  - new URLs in the site's XML sitemap = new listings (cheapest, most stable)
  jsonld   - schema.org Offer / Product / LocalBusiness / Event blocks embedded in pages
  links    - anchors whose href matches a regex (e.g. /deals/...)
  llm      - local model reads the visible page text and returns listings (fallback)
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable
from urllib.parse import urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup
from lxml import etree


def canonical(url: str) -> str:
    p = urlparse(url)
    return urlunparse((p.scheme, p.netloc.lower(), p.path.rstrip("/") or "/", "", "", ""))


def parse_sitemap(xml: str) -> tuple[list[dict], list[str]]:
    """Return (urls with lastmod, child sitemap URLs)."""
    try:
        root = etree.fromstring(xml.encode("utf-8"), parser=etree.XMLParser(recover=True, huge_tree=True))
    except etree.XMLSyntaxError:
        return [], []
    if root is None:
        return [], []
    ns = {"s": root.nsmap.get(None, "http://www.sitemaps.org/schemas/sitemap/0.9")}
    children = [e.text.strip() for e in root.findall(".//s:sitemap/s:loc", ns) if e.text]
    urls = []
    for u in root.findall(".//s:url", ns):
        loc = u.find("s:loc", ns)
        if loc is None or not loc.text:
            continue
        lm = u.find("s:lastmod", ns)
        urls.append({"url": loc.text.strip(), "lastmod": lm.text.strip() if lm is not None and lm.text else None})
    return urls, children


def _walk(node: Any) -> Iterable[dict]:
    """Yield listing-typed nodes; don't descend into a listing (its nested Offer/seller belongs to it)."""
    if isinstance(node, dict):
        types = node.get("@type")
        if set(types if isinstance(types, list) else [types]) & _LISTING_TYPES:
            yield node
            return
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


_LISTING_TYPES = {"Offer", "Product", "LocalBusiness", "Restaurant", "FoodEstablishment", "Event", "Store",
                  "HealthAndBeautyBusiness", "SportsActivityLocation", "ExerciseGym", "BarOrPub", "CafeOrCoffeeShop",
                  "TouristAttraction", "EntertainmentBusiness", "Service"}


def _name(node: Any) -> str:
    if isinstance(node, dict):
        return str(node.get("name") or "")
    return node if isinstance(node, str) else ""


def jsonld_items(html: str, base_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    out = []
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        for node in _walk(data):
            types = node.get("@type")
            types = set(types if isinstance(types, list) else [types])
            if not types & _LISTING_TYPES:
                continue
            location = node.get("location") if isinstance(node.get("location"), dict) else {}
            addr = node.get("address") or location.get("address")
            city = addr.get("addressLocality", "") if isinstance(addr, dict) else (addr if isinstance(addr, str) else "")
            offers = node.get("offers") or {}
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            if not isinstance(offers, dict):
                offers = {}
            merchant = _name(offers.get("seller")) or _name(node.get("brand")) or _name(location)
            if types & (_LISTING_TYPES - {"Offer", "Product", "Event", "Service"}):
                merchant = merchant or node.get("name") or ""
            category = node.get("category") or next(iter(sorted(types - {"Offer", "Product", None})), "")
            out.append(
                {
                    "url": urljoin(base_url, node.get("url") or base_url),
                    "title": node.get("name") or "",
                    "merchant": merchant,
                    "category": category if isinstance(category, str) else "",
                    "city": city or "",
                    "price": str(offers.get("price", "")),
                }
            )
    return out


def link_items(html: str, base_url: str, pattern: str, exclude: str | None = None) -> list[dict]:
    rx = re.compile(pattern)
    ex = re.compile(exclude) if exclude else None
    soup = BeautifulSoup(html, "lxml")
    seen, out = set(), []
    for a in soup.find_all("a", href=True):
        url = canonical(urljoin(base_url, a["href"]))
        if not rx.search(url) or (ex and ex.search(url)) or url in seen:
            continue
        seen.add(url)
        title = " ".join(a.get_text(" ", strip=True).split())[:200]
        if not title:
            img = a.find("img", alt=True)
            title = img["alt"][:200] if img else ""
        out.append({"url": url, "title": title})
    return out


def visible_text(html: str, limit: int = 12000) -> str:
    soup = BeautifulSoup(html, "lxml")
    for t in soup(["script", "style", "noscript", "svg", "header", "footer", "nav"]):
        t.decompose()
    return re.sub(r"\s+", " ", soup.get_text(" ", strip=True))[:limit]


def title_from_url(url: str) -> str:
    slug = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]
    slug = re.sub(r"[-_]+", " ", re.sub(r"\.\w+$", "", slug))
    slug = re.sub(r"\b\d{4,}\b", "", slug).strip()
    return slug.title()


_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"(?:\+49|0049|\b0)[\s/.-]?(?:\(?\d{2,5}\)?[\s/.-]?)\d{3,}[\d\s/.-]{2,}")


def contacts_from_html(html: str) -> dict[str, str]:
    """Pull email / phone / instagram from a homepage or Impressum."""
    soup = BeautifulSoup(html, "lxml")
    emails = [a["href"][7:].split("?")[0] for a in soup.select('a[href^="mailto:"]')]
    text = soup.get_text(" ", strip=True)
    text_deob = re.sub(r"\s*[\[(](at|ät)[\])]\s*", "@", text, flags=re.I)
    text_deob = re.sub(r"\s*[\[(](dot|punkt)[\])]\s*", ".", text_deob, flags=re.I)
    emails += _EMAIL.findall(text_deob)
    emails = [e for e in emails if not re.search(r"\.(png|jpg|gif|webp|svg)$|example\.|sentry|wixpress", e, re.I)]
    ig = ""
    for a in soup.select('a[href*="instagram.com/"]'):
        m = re.search(r"instagram\.com/([A-Za-z0-9_.]+)", a["href"])
        if m and m.group(1).lower() not in {"p", "reel", "explore", "accounts"}:
            ig = "@" + m.group(1)
            break
    phone = _PHONE.search(text)
    preferred = sorted(set(emails), key=lambda e: (not re.match(r"(info|hello|hallo|kontakt|contact|partner|marketing|team)@", e, re.I), len(e)))
    return {"email": preferred[0] if preferred else "", "phone": phone.group(0).strip() if phone else "", "instagram": ig}


def impressum_link(html: str, base_url: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for a in soup.find_all("a", href=True):
        label = (a.get_text(" ", strip=True) + " " + a["href"]).lower()
        if "impressum" in label or "imprint" in label or "legal notice" in label:
            return urljoin(base_url, a["href"])
    return ""
