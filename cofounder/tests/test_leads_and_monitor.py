import json

import pytest

from settleezy_cofounder import leads
from settleezy_cofounder.config import COFOUNDER_DIR
from settleezy_cofounder.scraping import monitor
from settleezy_cofounder.scraping.fetcher import Page


def test_upsert_merges_sources_and_scores(db):
    lid, created = leads.upsert(db, "Burger Bros GmbH", "merchant", source="groupon", source_url="u1", category="Restaurant")
    assert created
    lid2, created2 = leads.upsert(db, "burger bros", "merchant", source="vspots", source_url="u2", email="hi@bb.de")
    assert lid2 == lid and not created2
    row = dict(db.one("SELECT * FROM leads WHERE id=?", (lid,)))
    assert json.loads(row["sources"]) == ["groupon", "vspots"]
    assert row["email"] == "hi@bb.de"
    spa, _ = leads.upsert(db, "Quiet Spa", "merchant", source="groupon", category="spa")
    assert row["score"] > db.one("SELECT score FROM leads WHERE id=?", (spa,))["score"]


def test_status_validation_and_seeds(db):
    lid, _ = leads.upsert(db, "X", "merchant")
    leads.set_status(db, lid, "contacted", "called them")
    assert db.one("SELECT status, last_contact_at FROM leads WHERE id=?", (lid,))["last_contact_at"]
    with pytest.raises(ValueError):
        leads.set_status(db, lid, "spam")
    assert leads.import_seeds(db, COFOUNDER_DIR / "seeds" / "berlin_partners.csv") > 20
    assert leads.top(db, kind="university", limit=3)[0]["kind"] == "university"


class FakeFetcher:
    pages: dict = {}

    def __init__(self, *a, **k):
        pass

    def get(self, url, conditional=True, render=False):
        return Page(url, 200, self.pages[url]) if url in self.pages else Page(url, 404, "")

    def close(self):
        pass


def test_monitor_baseline_then_new(cfg, db, tmp_path, monkeypatch):
    sites = tmp_path / "sites.toml"
    sites.write_text(
        """[[site]]
name = "groupon"
lead_kind = "merchant"
strategies = ["links"]
start_urls = ["https://www.groupon.de/local/berlin"]
link_pattern = 'groupon\\.de/deals/[a-z0-9-]+'
detail_pages = 5
""",
        encoding="utf-8",
    )
    cfg.raw["scraping"]["sites_file"] = str(sites)
    monkeypatch.setattr(monitor, "Fetcher", FakeFetcher)
    start = "https://www.groupon.de/local/berlin"
    FakeFetcher.pages = {start: '<a href="/deals/cafe-kranz-berlin">Café Kranz – Frühstück für 2</a>'}

    first = monitor.run(cfg, db)[0]
    assert first["baseline"] and first["new"] == 0 and first["leads_created"] == 1
    assert monitor.new_listings(db, 24) == []

    FakeFetcher.pages = {
        start: '<a href="/deals/cafe-kranz-berlin">Café Kranz</a><a href="/deals/boulder-hall-berlin">Boulder Hall</a>',
        "https://www.groupon.de/deals/boulder-hall-berlin": '<script type="application/ld+json">{"@type":"SportsActivityLocation",'
        '"name":"Boulderhalle Ost","address":{"addressLocality":"Berlin"}}</script>',
    }
    second = monitor.run(cfg, db)[0]
    assert second["new"] == 1
    new = monitor.new_listings(db, 24)
    assert new[0]["merchant"] == "Boulderhalle Ost" and new[0]["category"] == "SportsActivityLocation"
    assert db.one("SELECT COUNT(*) n FROM leads")["n"] == 2


def test_default_competitor_file_parses(cfg):
    names = [s["name"] for s in monitor.load_sites(cfg)] if (cfg.home / "competitors.toml").exists() else None
    import tomllib

    data = tomllib.loads((COFOUNDER_DIR / "competitors.toml").read_text(encoding="utf-8"))
    assert {s["name"] for s in data["site"]} == {"groupon", "vspots", "top10berlin", "unidays", "studentbeans"}
    import re

    for s in data["site"]:
        for key in ("link_pattern", "link_exclude", "sitemap_include", "city_filter", "merchant_from_url"):
            if s.get(key):
                re.compile(s[key])
    assert names is None or names
