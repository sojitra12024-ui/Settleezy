"""Polite, efficient HTTP fetching.

* honours robots.txt (per host, cached)
* per-host rate limit (default 1 request / 4 s)
* conditional GET with ETag / Last-Modified, so unchanged pages cost almost nothing
* optional headless rendering (Playwright) for JavaScript-only pages
"""

from __future__ import annotations

import gzip
import json
import time
import urllib.robotparser
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from ..db import DB

DEFAULT_UA = "SettleezyResearchBot/1.0 (+https://settleezy.de; partnerships research)"


@dataclass
class Page:
    url: str
    status: int
    text: str
    not_modified: bool = False


class Fetcher:
    def __init__(self, db: DB, user_agent: str = DEFAULT_UA, min_delay: float = 4.0, respect_robots: bool = True):
        self.db = db
        self.ua = user_agent
        self.min_delay = min_delay
        self.respect_robots = respect_robots
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._last: dict[str, float] = {}
        self.http = httpx.Client(
            timeout=30,
            follow_redirects=True,
            headers={"User-Agent": self.ua, "Accept-Language": "de-DE,de;q=0.9,en;q=0.8"},
        )
        self._browser = None

    def allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        host = urlparse(url).scheme + "://" + urlparse(url).netloc
        if host not in self._robots:
            rp = urllib.robotparser.RobotFileParser()
            try:
                r = self.http.get(host + "/robots.txt")
                rp.parse(r.text.splitlines() if r.status_code == 200 else [])
                self._robots[host] = rp
            except httpx.HTTPError:
                self._robots[host] = None
        rp = self._robots[host]
        return True if rp is None else rp.can_fetch(self.ua, url)

    def _wait(self, url: str) -> None:
        host = urlparse(url).netloc
        delta = time.monotonic() - self._last.get(host, 0)
        if delta < self.min_delay:
            time.sleep(self.min_delay - delta)
        self._last[host] = time.monotonic()

    def get(self, url: str, *, conditional: bool = True, render: bool = False) -> Page:
        if not self.allowed(url):
            return Page(url, 403, "")
        self._wait(url)
        if render:
            return Page(url, 200, self._render(url))
        headers = {}
        meta_key = f"http:{url}"
        if conditional and (meta := self.db.kv_get(meta_key)):
            m = json.loads(meta)
            if m.get("etag"):
                headers["If-None-Match"] = m["etag"]
            if m.get("last_modified"):
                headers["If-Modified-Since"] = m["last_modified"]
        for attempt in range(3):
            try:
                r = self.http.get(url, headers=headers)
                break
            except httpx.HTTPError:
                if attempt == 2:
                    return Page(url, 0, "")
                time.sleep(2 ** attempt * 3)
        if r.status_code == 304:
            return Page(url, 304, "", not_modified=True)
        if r.status_code in (429, 503):
            time.sleep(30)
        if conditional and r.status_code == 200:
            self.db.kv_set(meta_key, json.dumps({"etag": r.headers.get("etag"), "last_modified": r.headers.get("last-modified")}))
        content = r.content
        if url.endswith(".gz") and content[:2] == b"\x1f\x8b":
            content = gzip.decompress(content)
            return Page(str(r.url), r.status_code, content.decode("utf-8", "replace"))
        return Page(str(r.url), r.status_code, r.text)

    def _render(self, url: str) -> str:
        if self._browser is None:
            from playwright.sync_api import sync_playwright

            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(headless=True)
        page = self._browser.new_page(user_agent=self.ua)
        try:
            page.goto(url, wait_until="networkidle", timeout=45000)
            return page.content()
        finally:
            page.close()

    def close(self) -> None:
        self.http.close()
        if self._browser is not None:
            self._browser.close()
            self._pw.stop()
