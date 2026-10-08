from datetime import datetime

from settleezy_cofounder import operations as op
from settleezy_cofounder.leads import set_status, upsert


def test_next_run_and_schedule_description():
    tue_noon = datetime(2026, 10, 6, 12, 0)
    assert op.next_run("igdiscover", tue_noon) == "2026-10-06T12:15"
    assert op.next_run("igdiscover", datetime(2026, 10, 6, 12, 30)) == "2026-10-09T12:15"     # Friday
    assert op.next_run("pipeline", tue_noon) == "2026-10-12T07:30"                            # next Monday
    assert op.next_run("mail", datetime(2026, 10, 6, 19, 30)) == "2026-10-07T09:00"
    assert op.next_run("track") is None and op._describe("mail") == "daily 09:00 + 5×" and op._describe("drafts") == "weekdays 14:30"


def test_missions_rank_and_resolve(cfg, db):
    lid, _ = upsert(db, "Brew Lab", "merchant", website="https://brew.de")
    upsert(db, "Kaffee Kiez", "merchant", website="https://kk.de")
    set_status(db, lid, "replied")
    ms = op.missions(cfg, db)
    keys = [m["key"] for m in ms]
    assert keys[0] == "replied" and "contacts" in keys and "brain" in keys
    contacts = next(m for m in ms if m["key"] == "contacts")
    assert contacts["count"] == 2 and contacts["action"] == "job" and contacts["target"] == "enrich"   # both have a website, no email
    assert all(m["count"] for m in ms)


def test_jobs_status_reports_errors_and_running(cfg, db):
    db.kv_set("last_run:mail", "2026-10-08T08:00:00+00:00")
    db.kv_set("last_error:mail", "2026-10-08T09:00:00+00:00 GraphError: token expired")
    db.kv_set("running:scrape", "2026-10-08T09:01:00+00:00")
    js = {j["name"]: j for j in op.jobs_status(db)}
    assert js["mail"]["error"] == "GraphError: token expired" and js["scrape"]["running"] and js["mail"]["agent"] == "hermes"
    db.kv_set("last_run:mail", "2026-10-08T10:00:00+00:00")                                  # a later success clears it
    assert {j["name"]: j for j in op.jobs_status(db)}["mail"]["error"] == ""


def test_playbook_runs_all_steps_and_reports_failures(cfg, db, monkeypatch):
    from settleezy_cofounder import jobs, notify

    calls, events = [], []
    monkeypatch.setattr(notify, "toast", lambda *a: True)
    monkeypatch.setattr(notify, "send_push", lambda *a: 0)

    def fake(name):
        def run(cfg, db):
            calls.append(name)
            if name == "igdiscover":
                raise RuntimeError("Instagram not connected")
            return {"ok": name}
        return run

    for step in op.PLAYBOOKS["prospecting"]["steps"]:
        monkeypatch.setitem(jobs.JOBS, step, fake(step))
    res = op.run_playbook(cfg, "prospecting", events.append)
    assert calls == ["leadgen", "enrich", "igdiscover", "brain", "pipeline"] and res["failed"] == ["igdiscover"]
    assert [e["status"] for e in events].count("working") == 5 and any(e["status"] == "alert" for e in events)
    assert db.kv_get("playbook:running") == "{}"
    assert notify.recent(db)["items"][0]["title"] == "Prospecting run finished"


def test_operations_api(cfg):
    from fastapi.testclient import TestClient

    from settleezy_cofounder.dashboard.app import app

    c = TestClient(app)
    o = c.get("/api/operations").json()
    assert {p["name"] for p in o["playbooks"]} == {"prospecting", "inbox", "intelligence", "learning"} and o["jobs"]
    assert c.post("/api/playbook/prospecting").status_code == 403
    assert c.post("/api/playbook/nope", headers={"X-SZ": "1"}).status_code == 404
