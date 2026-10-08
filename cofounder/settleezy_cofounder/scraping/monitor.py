"""Watch competitor sites for new Berlin listings and turn them into leads."""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from pathlib import Path
from typing import Any

from ..config import COFOUNDER_DIR, Config
from ..db import DB, utcnow
from ..llm import LLM, LLMError
from .. import leads as leads_mod
from .extractors import canonical, jsonld_items, link_items, parse_sitemap, title_from_url, visible_text
from .fetcher import DEFAULT_UA, Fetcher


def load_sites(cfg: Config) -> list[dict]:
    path = Path(cfg.get("scraping.sites_file", COFOUNDER_DIR / "competitors.toml"))
    if not path.is_absolute():
        path = cfg.home / path
    return [s for s in tomllib.loads(path.read_text(encoding="utf-8")).get("site", []) if s.get("enabled", True)]


def _id(source: str, url: str) -> str:
    return hashlib.sha1(f"{source}|{canonical(url)}".encode()).hexdigest()[:20]


def _sitemap_candidates(site: dict, f: Fetcher) -> list[dict]:
    include = re.compile(site.get("sitemap_include", "."))
    child_rx = re.compile(site.get("sitemap_child_include", "."))
    queue, seen, out = list(site.get("sitemaps", [])), set(), []
    max_maps = int(site.get("max_sitemaps", 20))
    while queue and len(seen) < max_maps:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        page = f.get(url, conditional=False)
        if page.status != 200:
            continue
        urls, children = parse_sitemap(page.text)
        queue += [c for c in children if child_rx.search(c)]
        out += [{"url": u["url"], "title": title_from_url(u["url"]), "lastmod": u["lastmod"]} for u in urls if include.search(u["url"])]
    return out


def _llm_candidates(html: str, base_url: str, llm: LLM) -> list[dict]:
    text = visible_text(html)
    try:
        data = llm.local_json(
            f"""This is the text of a deals/listings page ({base_url}). Extract every individual offer or partner listed.
Return JSON {{"items": [{{"title": "...", "merchant": "business or brand name", "category": "food|fitness|beauty|entertainment|wellness|education|travel|fashion|tech|other", "city": "..."}}]}}

PAGE TEXT:
{text}"""
        )
    except (LLMError, ValueError):
        return []
    items = data.get("items", []) if isinstance(data, dict) else []
    return [{**i, "url": base_url + "#" + re.sub(r"\W+", "-", (i.get("merchant") or i.get("title") or "")).lower()} for i in items if isinstance(i, dict)]


def collect(site: dict, f: Fetcher, llm: LLM | None) -> list[dict]:
    strategies = site.get("strategies", ["sitemap", "links", "jsonld"])
    found: dict[str, dict] = {}

    def add(items: list[dict]) -> None:
        for it in items:
            key = canonical(it["url"])
            found[key] = {**found.get(key, {}), **{k: v for k, v in it.items() if v}}

    if "sitemap" in strategies and site.get("sitemaps"):
        add(_sitemap_candidates(site, f))
    pages = []
    if {"links", "jsonld", "llm"} & set(strategies):
        for url in site.get("start_urls", []):
            page = f.get(url, conditional=False, render=bool(site.get("render")))
            if page.status == 200:
                pages.append(page)
    for page in pages:
        if "links" in strategies and site.get("link_pattern"):
            add(link_items(page.text, page.url, site["link_pattern"], site.get("link_exclude")))
        if "jsonld" in strategies:
            add([i for i in jsonld_items(page.text, page.url) if i.get("url")])
    if "llm" in strategies and llm is not None and not found:
        for page in pages:
            add(_llm_candidates(page.text, page.url, llm))
    return list(found.values())


def _matches_city(site: dict, item: dict) -> bool:
    rx = site.get("city_filter")
    if not rx:
        return True
    hay = " ".join(str(item.get(k, "")) for k in ("url", "title", "city", "merchant"))
    return bool(re.search(rx, hay, re.I))


def _merchant_from(site: dict, item: dict) -> str:
    if item.get("merchant"):
        return item["merchant"].strip()
    if site.get("merchant_from_url"):
        m = re.search(site["merchant_from_url"], item["url"])
        if m:
            return title_from_url("/" + m.group(1))
    title = item.get("title") or title_from_url(item["url"])
    title = re.sub(r"\s*[-|–:]\s*(berlin|groupon|top10|vspots).*$", "", title, flags=re.I)
    title = re.sub(r"\b(berlin|in berlin)\b", "", title, flags=re.I)
    return title.strip(" -|,")[:120]


def run(cfg: Config, db: DB, llm: LLM | None = None, only: str | None = None, inspect: bool = False) -> list[dict[str, Any]]:
    sc = cfg.section("scraping")
    f = Fetcher(db, sc.get("user_agent") or DEFAULT_UA, float(sc.get("min_delay_seconds", 4)), bool(sc.get("respect_robots", True)))
    results = []
    try:
        for site in load_sites(cfg):
            if only and site["name"] != only:
                continue
            items = collect(site, f, llm)
            items = [i for i in items if _matches_city(site, i)]
            if inspect:
                results.append({"site": site["name"], "found": len(items), "sample": items[:15]})
                continue
            first_run = db.kv_get(f"baseline:{site['name']}") is None
            now = utcnow()
            new = []
            for it in items:
                lid = _id(site["name"], it["url"])
                row = db.one("SELECT id FROM listings WHERE id=?", (lid,))
                merchant = _merchant_from(site, it)
                if row:
                    db.x("UPDATE listings SET last_seen=? WHERE id=?", (now, lid))
                    continue
                db.x(
                    "INSERT INTO listings(id,source,url,title,merchant,category,city,price,first_seen,last_seen,raw) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (lid, site["name"], it["url"], it.get("title", ""), merchant, it.get("category", ""), it.get("city", ""),
                     it.get("price", ""), "baseline" if first_run else now, now, json.dumps(it, ensure_ascii=False)),
                )
                new.append({**it, "merchant": merchant})
            enrich_budget = int(site.get("detail_pages", 10))
            for it in new[:enrich_budget] if not first_run else []:
                page = f.get(it["url"], conditional=False, render=bool(site.get("render")))
                details = jsonld_items(page.text, page.url) if page.status == 200 else []
                if details:
                    d = details[0]
                    it["merchant"] = d.get("merchant") or it["merchant"]
                    it["category"] = d.get("category") or it.get("category", "")
                    it["city"] = d.get("city") or it.get("city", "")
                    db.x("UPDATE listings SET merchant=?, category=?, city=? WHERE id=?",
                         (it["merchant"], it["category"], it["city"], _id(site["name"], it["url"])))
            created = 0
            for it in new:
                if it["merchant"]:
                    created += leads_mod.upsert_from_listing(db, site, it)
            if first_run:
                db.kv_set(f"baseline:{site['name']}", now)
            db.metric("competitors", f"{site['name']}_listings_seen", len(items))
            db.metric("competitors", f"{site['name']}_new", 0 if first_run else len(new))
            results.append({"site": site["name"], "seen": len(items), "new": 0 if first_run else len(new),
                            "baseline": first_run, "baseline_size": len(new) if first_run else None, "leads_created": created})
    finally:
        f.close()
    return results


def new_listings(db: DB, hours: int = 24) -> list[dict]:
    from datetime import datetime, timedelta, timezone

    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    return [dict(r) for r in db.q(
        "SELECT source,title,merchant,category,url,first_seen FROM listings WHERE first_seen != 'baseline' AND first_seen >= ? ORDER BY first_seen DESC",
        (since,),
    )]
