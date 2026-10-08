import json

from settleezy_cofounder import importer
from settleezy_cofounder.importer import library


def test_import_tables_and_docs(cfg, db, tmp_path):
    folder = tmp_path / "exports"
    (folder / "outreach").mkdir(parents=True)
    (folder / "Partner Leads Berlin.csv").write_text(
        "Company;E-Mail;Instagram;Category;Status;Contact person;Notes\n"
        "Café Kranz;hi@kranz.de;cafe_kranz;Café;Live;Anna;10% on drinks\n"
        "Boulderhalle Ost;team@boulder.de;;Fitness;contacted;;sent deck\n"
        "Späti 24;;;Grocery;not contacted;;\n"
        "Bio Markt;bio@markt.de;;Grocery;declined;;\n",
        encoding="utf-8")
    (folder / "universities.json").write_text(json.dumps({"items": [
        {"name": "HTW Berlin", "email": "international@htw-berlin.de", "type": "University", "status": "meeting booked"}]}), encoding="utf-8")
    (folder / "outreach" / "venue pitch.md").write_text(
        "Subject: Students for Café X\n\nHi Anna,\nwe help international students in Berlin save money ... Best, Raj", encoding="utf-8")
    (folder / "ig captions.txt").write_text("Reel hook: What €10 buys a student in Berlin #berlin #students #studentlife", encoding="utf-8")
    (folder / "strategy.md").write_text("Q4 strategy: focus on universities before the winter intake and sign venues near campuses.", encoding="utf-8")
    (folder / "logo.png").write_bytes(b"\x89PNG")

    rep = importer.run(cfg, db, folder)
    assert rep["tables"]["Partner Leads Berlin.csv"] == {"rows": 4, "leads_new": 4, "leads_updated": 0, "partners": 1}
    assert rep["docs"] == {"outreach": 1, "social": 1, "notes": 1} and rep["skipped"] == ["logo.png"] and not rep["errors"]

    lead = lambda n: dict(db.one("SELECT * FROM leads WHERE name=?", (n,)))  # noqa: E731
    assert lead("Café Kranz")["status"] == "partner" and lead("Café Kranz")["instagram"] == "@cafe_kranz"
    assert lead("Boulderhalle Ost")["status"] == "contacted" and lead("Späti 24")["status"] == "new"
    assert lead("Bio Markt")["status"] == "lost"
    assert lead("HTW Berlin")["kind"] == "university" and lead("HTW Berlin")["status"] == "meeting"
    p = db.one("SELECT * FROM partners WHERE name='Café Kranz'")
    assert p["status"] == "live" and p["contact_name"] == "Anna"

    again = importer.run(cfg, db, folder)                     # idempotent
    assert again["tables"]["Partner Leads Berlin.csv"]["leads_new"] == 0 and again["docs"] == {"outreach": 0, "social": 0, "notes": 0}
    assert db.one("SELECT COUNT(*) n FROM partners")["n"] == 1
    assert "Students for Café X" in library(cfg, "outreach") and "#berlin" in library(cfg, "social")
