from datetime import date, datetime, timedelta, timezone
import pytest

from settleezy_cofounder import leadgen, ops, pipeline, planner
from settleezy_cofounder.leads import set_status, upsert

OVERPASS = {"elements": [
    {"type": "node", "id": 1, "lat": 52.5130, "lon": 13.3270,
     "tags": {"amenity": "cafe", "name": "Café Campus", "website": "https://cafecampus.de", "addr:street": "Hardenbergstraße",
              "addr:housenumber": "5", "addr:postcode": "10623", "contact:instagram": "https://instagram.com/cafecampus/"}},
    {"type": "way", "id": 2, "center": {"lat": 52.5140, "lon": 13.3290},
     "tags": {"shop": "supermarket", "name": "Bio Markt Ernst", "email": "info@bioernst.de"}},
    {"type": "node", "id": 3, "lat": 52.5120, "lon": 13.3260, "tags": {"amenity": "fast_food", "name": "McDonald's", "brand": "McDonald's"}},
    {"type": "node", "id": 4, "lat": 52.5120, "lon": 13.3260, "tags": {"amenity": "cafe"}},   # no name
]}


def ago(days: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).replace(microsecond=0).isoformat()


def test_leadgen_parses_osm_scores_proximity_and_caches(cfg, db):
    queries = []

    def fetch(q):
        queries.append(q)
        return OVERPASS

    rep = leadgen.discover(cfg, db, "TU Berlin", fetch=fetch, pause=0)
    assert rep["new"] == 2 and rep["skipped_chains"] == 1 and len(queries) == 1
    assert 'around:800,52.5125,13.3266' in queries[0] and "out center tags" in queries[0]
    cafe = db.one("SELECT * FROM leads WHERE name='Café Campus'")
    assert cafe["instagram"] == "cafecampus" and cafe["address"] == "Hardenbergstraße 5, 10623"
    assert cafe["campus"].startswith("TU Berlin") and cafe["distance_m"] < 200
    # proximity boost, but an OSM source is not counted as "seen on a competitor platform"
    far, _ = upsert(db, "Café Far", "merchant", category="cafe", website="https://far.de")
    assert cafe["score"] > db.one("SELECT score FROM leads WHERE id=?", (far,))["score"]
    # second run today is cached; force re-runs without duplicating
    assert leadgen.discover(cfg, db, "TU Berlin", fetch=fetch, pause=0)["campuses"]["TU Berlin (Charlottenburg)"] == "already scanned today"
    assert leadgen.discover(cfg, db, "TU Berlin", fetch=fetch, pause=0, force=True)["new"] == 0
    assert db.one("SELECT COUNT(*) n FROM leads WHERE campus IS NOT NULL")["n"] == 2
    cov = {c["campus"]: c for c in leadgen.coverage(cfg, db)}
    assert cov["TU Berlin (Charlottenburg)"]["found"] == 2 and cov["FU Berlin (Dahlem)"]["found"] == 0


def test_sequence_lifecycle_and_stage_history(cfg, db):
    lid, _ = upsert(db, "Brew Lab", "merchant", email="lea@brewlab.de")
    monday = date.today() + timedelta(days=7 - date.today().weekday())
    assert pipeline.start_sequence(cfg, db, lid, monday) == 5
    steps = db.q("SELECT * FROM tasks WHERE lead_id=? ORDER BY due", (lid,))
    assert steps[0]["due"] == monday.isoformat() and steps[0]["category"] == "outreach" and steps[0]["priority"] == 3
    assert "Find their Instagram" in steps[1]["title"]                      # no handle on file
    assert all(date.fromisoformat(s["due"]).weekday() < 5 for s in steps)    # never on a weekend
    assert pipeline.start_sequence(cfg, db, lid, monday) == 0                # idempotent

    ops.set_task(db, steps[0]["id"], "done")                                 # first email sent -> contacted
    lead = db.one("SELECT * FROM leads WHERE id=?", (lid,))
    assert lead["status"] == "contacted" and lead["next_step"].startswith("Find their Instagram")
    assert [s["done"] for s in pipeline.sequences(db)] == [1]

    set_status(db, lid, "replied")                                           # a reply stops the sequence
    assert db.one("SELECT COUNT(*) n FROM tasks WHERE lead_id=? AND status='open'", (lid,))["n"] == 0
    ev = [(e["from_status"], e["to_status"]) for e in db.q("SELECT * FROM lead_events WHERE lead_id=? ORDER BY id", (lid,))]
    assert ev == [("new", "contacted"), ("contacted", "replied")]


def test_conversion_targets_forecast_and_hygiene(cfg, db):
    # 10 contacted leads 2 weeks ago; 4 replied, 2 met, 1 partner
    ids = []
    for i in range(10):
        lid, _ = upsert(db, f"Venue {i}", "merchant")
        ids.append(lid)
        db.x("INSERT INTO lead_events(lead_id,from_status,to_status,at) VALUES(?,?,?,?)", (lid, "new", "contacted", ago(14)))
        db.x("UPDATE leads SET status='contacted', stage_changed_at=? WHERE id=?", (ago(14), lid))
    for lid, stages in zip(ids, [["replied", "meeting", "partner"], ["replied", "meeting"], ["replied"], ["replied"]]):
        for j, s in enumerate(stages):
            db.x("INSERT INTO lead_events(lead_id,from_status,to_status,at) VALUES(?,?,?,?)", (lid, "x", s, ago(12 - 2 * j)))
        db.x("UPDATE leads SET status=?, stage_changed_at=? WHERE id=?", (stages[-1], ago(12 - 2 * (len(stages) - 1)), lid))
    conv = {c["step"]: c for c in pipeline.conversion(db)}
    assert conv["contacted→replied"]["observed"] == 0.4 and not conv["contacted→replied"]["assumed"]
    assert conv["contacted→replied"]["median_days"] == 2.0
    assert conv["meeting→partner"]["assumed"] and conv["meeting→partner"]["rate"] == 0.5   # sample too small

    t = {x["stage"]: x for x in pipeline.targets(cfg, db)}
    assert t["partner"]["target_week"] > 0 and t["contacted"]["target_week"] > t["replied"]["target_week"]
    fc = pipeline.forecast(cfg, db)
    assert fc["by_stage"]["meeting"]["leads"] == 1 and fc["expected_from_pipeline"] > 0

    stale = {l["name"]: l for l in pipeline.stale(db)}
    assert "Venue 9" in stale and "Venue 2" in stale and "two call slots" in stale["Venue 2"]["suggestion"]
    assert {l["name"] for l in pipeline.hygiene(db)} >= {"Venue 2", "Venue 9"}
    pipeline.set_next_step(db, ids[2], "send the offer sheet tomorrow")
    lead = db.one("SELECT * FROM leads WHERE id=?", (ids[2],))
    assert lead["next_step"] == "send the offer sheet" and lead["next_step_due"] == (date.today() + timedelta(days=1)).isoformat()
    assert "Venue 2" not in {l["name"] for l in pipeline.hygiene(db)}


def test_auto_start_respects_weekly_capacity_and_routes(cfg, db):
    cfg.raw.setdefault("pipeline", {})["new_sequences_per_week"] = 2
    leadgen.discover(cfg, db, "TU Berlin", fetch=lambda q: OVERPASS, pause=0)
    upsert(db, "No Contact GmbH", "merchant")
    started = pipeline.auto_start(cfg, db)
    assert len(started) == 2 and pipeline.auto_start(cfg, db) == []
    r = pipeline.routes(cfg, db, "TU")
    assert r and len(r[0]["stops"]) == 2 and r[0]["maps_url"].startswith("https://www.google.com/maps/dir/?api=1")
    assert "waypoints=" in r[0]["maps_url"] and r[0]["walk_min"] >= 1
    s = pipeline.summary(cfg, db)
    assert len(s["map"]) == 2 and s["sequence_capacity"] == {"started_this_week": 2, "per_week": 2}


def test_infer_category_and_estimate():
    assert planner.infer_category("Reply to Lea") == "inbox"
    assert planner.infer_category("Drop by Café Kranz with a flyer") == "visits"
    assert planner.infer_category("Post the welcome reel") == "content"
    assert planner.infer_category("Onboard Boulderhalle: set up offer") == "partners"
    assert planner.estimate("Write newsletter 90 min") == 90
    assert planner.estimate("Workshop prep 1.5h") == 90
    assert planner.estimate("Post the welcome reel") == 45


def test_week_plan_blocks_tasks_around_meetings(cfg, db):
    monday = date.today() + timedelta(days=7 - date.today().weekday())
    m = monday.isoformat()
    # Monday: meeting eats most of the outreach block and the whole afternoon calls block
    db.x("INSERT INTO events(id,source,title,start,end,location,attendees,url) VALUES(?,?,?,?,?,?,?,?)",
         ("e1", "outlook", "HTW International Office", f"{m}T09:00", f"{m}T10:15", "", "", ""))
    db.x("INSERT INTO events(id,source,title,start,end,location,attendees,url) VALUES(?,?,?,?,?,?,?,?)",
         ("e2", "outlook", "Workshop day", f"{m}T13:00", f"{m}T17:00", "", "", ""))
    a = ops.add_task(db, "Follow up with Brew Lab", due=m)
    b = ops.add_task(db, "Follow up with Café Kranz", due=m)
    c = ops.add_task(db, "Post the welcome reel", due=m)
    d = ops.add_task(db, "Write the partner pitch deck 4h")       # deep work: split across slots
    big = ops.add_task(db, "Rebuild the website 60h")              # can't fit at all
    e = ops.add_task(db, "Reply to Lea")
    plan = planner.plan_week(cfg, db, start=monday, now=datetime.combine(monday - timedelta(days=3), datetime.min.time()))
    day0 = plan["days"][0]
    items = {i["task_id"]: (blk["title"], i) for blk in day0["blocks"] for i in blk["items"]}
    assert items[a][0] == "Partner outreach" and items[a][1]["start"] == "10:15"     # after the meeting
    assert items[b][1]["start"] == "10:30" or items[b][0] != "Partner outreach"
    assert c not in items or items[c][0] != "Content & Instagram"                     # content block is in a meeting
    parts = [i for dd in plan["days"] for blk in dd["blocks"] for i in blk["items"] if i["task_id"] == d]
    assert len(parts) >= 2 and sum(p["minutes"] for p in parts) == 240 and all(p["minutes"] >= 30 for p in parts)
    assert "(part 1/" in parts[0]["title"]
    assert [o["task_id"] for o in plan["overflow"]] == [big] and plan["overflow"][0]["reason"] == "too long for any free slot"
    assert any(e in {i["task_id"] for blk in dd["blocks"] for i in blk["items"]} for dd in plan["days"])
    assert day0["load"]["meeting_min"] == 315 and day0["load"]["longest_focus_min"] < 180
    assert any(x["kind"] == "meetings" for x in plan["insights"])   # 5.25 h > 4 h of meetings
    ics = planner.to_ics(plan)
    assert ics.startswith("BEGIN:VCALENDAR") and f"DTSTART:{m.replace('-', '')}T101500" in ics and ics.count("BEGIN:VEVENT") >= 3
    assert "Follow up with Brew Lab" in planner.spoken_week(plan) or "hours of meetings" in planner.spoken_week(plan)


def test_task_update_and_apply_plan(cfg, db):
    t = ops.add_task(db, "Sort receipts")
    with __import__("pytest").raises(ValueError):
        ops.update_task(db, t, category="party")
    assert ops.update_task(db, t, category="admin", est_minutes=25)["est_minutes"] == 25
    plan = planner.plan_week(cfg, db)
    if plan["totals"]["tasks_scheduled"]:
        assert planner.apply_plan(db, plan) == 1
        assert db.one("SELECT due FROM tasks WHERE id=?", (t,))["due"] is not None


def test_existing_partners_never_get_cold_outreach(cfg, db):
    lid, _ = upsert(db, "Café Kranz", "merchant", email="a@kranz.de", category="cafe")
    other, _ = upsert(db, "Brew Lab", "merchant", email="b@brew.de", category="cafe")
    ops.add_partner(db, "café kranz", "venue")                     # added by hand, different case, no lead link
    assert db.one("SELECT status FROM leads WHERE id=?", (lid,))["status"] == "partner"
    assert db.one("SELECT lead_id FROM partners")["lead_id"] == lid
    db.x("INSERT INTO partners(name, kind, status) VALUES('Brew Lab', 'venue', 'onboarding')")   # e.g. from an old import
    started = [s["name"] for s in pipeline.auto_start(cfg, db)]
    assert started == []                                            # linked first, then excluded
    with pytest.raises(ValueError):
        pipeline.start_sequence(cfg, db, other)
