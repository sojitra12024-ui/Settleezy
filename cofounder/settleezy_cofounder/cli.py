"""`sz` command line. Run `sz --help`."""

from __future__ import annotations

import argparse
import json
import shutil
import sys

from .config import COFOUNDER_DIR, load_config
from .db import DB


def _print(obj) -> None:
    print(obj if isinstance(obj, str) else json.dumps(obj, indent=2, ensure_ascii=False, default=str))


def cmd_init(args) -> None:
    for src, dst in (("config.example.toml", "config.toml"), (".env.example", ".env")):
        if not (COFOUNDER_DIR / dst).exists():
            shutil.copy(COFOUNDER_DIR / src, COFOUNDER_DIR / dst)
            print(f"created {dst} — edit it now")
    cfg = load_config()
    from .leads import import_seeds

    with DB(cfg.db_path) as db:
        n = import_seeds(db, COFOUNDER_DIR / "seeds" / "berlin_partners.csv")
    print(f"database ready at {cfg.db_path}; {n} seed leads imported")


def cmd_auth(args) -> None:
    cfg = load_config()
    if args.service == "outlook":
        from .msgraph import Graph

        who = Graph(cfg).login()
        print(f"Signed in as {who}. Permissions: read mail, create drafts, read calendar. (No send permission.)")
        return
    import getpass

    from .config import secret
    from .connections import instagram_setup, write_env

    print("1. Open https://developers.facebook.com/tools/explorer, pick your app, add permissions:\n"
          "   instagram_basic, instagram_manage_insights, instagram_manage_comments, pages_show_list, pages_read_engagement\n"
          "2. Click 'Generate Access Token', log in, and paste the token here.")
    short = getpass.getpass("Short-lived token: ").strip()
    app_id = secret("FB_APP_ID") or input("Meta App ID: ").strip()
    app_secret = secret("FB_APP_SECRET") or getpass.getpass("Meta App Secret: ").strip()
    res = instagram_setup(short, app_id, app_secret)
    write_env(cfg.home / ".env", {"IG_ACCESS_TOKEN": res["IG_ACCESS_TOKEN"], "IG_USER_ID": res["IG_USER_ID"],
                                  "FB_APP_ID": app_id, "FB_APP_SECRET": app_secret})
    print(f"Connected @{res['username']} (via Facebook Page '{res['page']}'). Saved to .env. "
          "This Page token doesn't expire unless you change your password or remove the app.")


def cmd_doctor(args) -> None:
    from .connections import run_all

    cfg = load_config()
    with DB(cfg.db_path) as db:
        results = run_all(cfg, db)
    width = max(len(r["name"]) for r in results)
    for r in results:
        mark = "OK " if r["ok"] else ("-- " if r["optional"] else "!! ")
        print(f"{mark} {r['name']:<{width}}  {r['detail']}")
        if not r["ok"] and r["fix"]:
            print(f"    {'':<{width}}  fix: {r['fix']}")
    if not all(r["ok"] or r["optional"] for r in results):
        sys.exit(1)


def cmd_plan(args) -> None:
    from .ops import focus_now, today_plan

    cfg = load_config()
    with DB(cfg.db_path) as db:
        plan = today_plan(cfg, db)
        for i in plan["items"]:
            mark = "▶" if plan["current"] is i else " "
            if i["type"] == "meeting":
                print(f"{mark} {i['start']}-{i['end']}  [meeting] {i['title']}")
            else:
                print(f"{mark} {i['start']}-{i['end']}  {i['title']}" + (f"  (clash: {', '.join(i['clash'])})" if i["clash"] else ""))
                for it in i.get("scheduled", []):
                    print(f"      ☐ {it['start']} {it['title']} ({it['minutes']} min)")
                for sug in i["suggestions"][:4]:
                    print(f"      · {sug}")
        print("\n" + focus_now(cfg, db))


def cmd_todo(args) -> None:
    from . import ops

    cfg = load_config()
    with DB(cfg.db_path) as db:
        if args.action == "add":
            tid = ops.add_task(db, " ".join(args.text), source="manual")
            print(f"added #{tid}")
        elif args.action in {"done", "snooze", "reopen", "delete"}:
            ops.set_task(db, int(args.text[0]), args.action, int(args.text[1]) if len(args.text) > 1 else 1)
            print("ok")
        else:
            for t in ops.tasks(db, "done" if args.action == "done-list" else "open"):
                flag = "!" if t["priority"] >= 3 else " "
                print(f"#{t['id']:<4} {flag} {t['due'] or '          '} {t['title']}{'  (overdue)' if t.get('overdue') else ''}")


def cmd_partners(args) -> None:
    from . import ops

    cfg = load_config()
    with DB(cfg.db_path) as db:
        if args.action == "add":
            pid = ops.add_partner(db, " ".join(args.rest), args.kind or "venue")
            print(f"added partner #{pid}")
        elif args.action == "advance":
            p = ops.advance(db, int(args.rest[0]))
            print(f"{p['name']} -> {p['stage']}")
        elif args.action == "set":
            pid, field, value = int(args.rest[0]), args.rest[1], " ".join(args.rest[2:])
            ops.update_partner(db, pid, **{field: value})
            print("ok")
        elif args.action == "stats":
            _print(ops.partner_stats(cfg, db))
        else:
            for p in ops.partners(cfg, db):
                print(f"#{p['id']:<4} {p['health']['label']:<8} {p['status']:<10} {p['stage_label']:<26} {p['kind'] or '':<10} {p['name']}")


def cmd_reach(args) -> None:
    from .ops import reach_out

    cfg = load_config()
    with DB(cfg.db_path) as db:
        for r in reach_out(cfg, db, args.limit):
            print(f"{r['priority']:>5.0f}  {r['who'][:32]:<32} {r['action']:<24} {r['reason']}")


def cmd_prep(args) -> None:
    from .llm import LLM
    from .ops import meeting_prep

    cfg = load_config()
    with DB(cfg.db_path) as db:
        event_id = args.event
        if not event_id:
            from datetime import datetime

            nxt = db.one("SELECT id FROM events WHERE start >= ? ORDER BY start LIMIT 1", (datetime.now().strftime("%Y-%m-%dT%H:%M"),))
            if not nxt:
                sys.exit("No upcoming meetings.")
            event_id = nxt["id"]
        print(meeting_prep(cfg, db, event_id, None if args.no_ai else LLM(cfg)))


def cmd_scorecard(args) -> None:
    from .ops import scorecard

    cfg = load_config()
    with DB(cfg.db_path) as db:
        for r in scorecard(cfg, db):
            print(f"{r['metric']:<24} this week {r['this_week']:>4}   last week {r['last_week']:>4}   ({r['delta']:+d})")


def cmd_agents(args) -> None:
    from . import agents

    cfg = load_config()
    with DB(cfg.db_path) as db:
        if args.action == "run":
            res = agents.run(cfg, db, args.agent or None, publish_events=True)
            for a in res["agents"]:
                print(f"{a['status'].upper():<6} {agents.AGENTS[a['agent']].name:<9} {a['summary']}")
            print("\nSetz:", res["setz"]["briefing"])
            if res["setz"]["todos_created"]:
                print(f"(+{res['setz']['todos_created']} to-dos from agent alerts)")
        else:
            reps = agents.latest(db)
            for key, a in agents.AGENTS.items():
                r = reps.get(key)
                print(f"{a.name:<9} {a.role:<38} {(r['status'] + ' · ' + r['summary']) if r else 'not run yet'}")


def cmd_import(args) -> None:
    from .importer import run

    cfg = load_config()
    with DB(cfg.db_path) as db:
        _print(run(cfg, db, args.folder))


def cmd_kpi(args) -> None:
    from .tracking import record_kpi

    cfg = load_config()
    with DB(cfg.db_path) as db:
        record_kpi(db, args.key, args.value, args.day)
    print(f"recorded {args.key} = {args.value:g}")


def cmd_run(args) -> None:
    from .jobs import run_job

    _print(run_job(load_config(), args.job))


def cmd_morning(args) -> None:
    from .jobs import MORNING, run_job

    cfg = load_config()
    for job in MORNING:
        try:
            print(f"· {job}: ", end="", flush=True)
            print(json.dumps(run_job(cfg, job), default=str, ensure_ascii=False)[:300])
        except Exception as exc:  # unconfigured sources are skipped
            print(f"skipped ({exc})")
    if args.speak:
        cmd_brief(argparse.Namespace(speak=True, no_actions=False, quiet=True))


def cmd_triage(args) -> None:
    from .llm import LLM
    from .triage import followups_due, needs_reply

    cfg = load_config()
    with DB(cfg.db_path) as db:
        _print({"needs_reply": [i.as_dict() for i in needs_reply(cfg, db, LLM(cfg))],
                "followups_due": [i.as_dict() for i in followups_due(cfg, db)]})


def cmd_drafts(args) -> None:
    from .drafts import run

    cfg = load_config()
    with DB(cfg.db_path) as db:
        _print(run(cfg, db, limit=args.limit, min_priority=args.min_priority))


def cmd_brief(args) -> None:
    from .brief import build

    cfg = load_config()
    with DB(cfg.db_path) as db:
        md, speech = build(cfg, db, with_actions=not args.no_actions)
    if not getattr(args, "quiet", False):
        print(md)
    if args.speak:
        from .voice import Speaker

        Speaker(cfg).say(speech)


def cmd_scrape(args) -> None:
    from .llm import LLM
    from .scraping.monitor import run

    cfg = load_config()
    with DB(cfg.db_path) as db:
        _print(run(cfg, db, LLM(cfg), only=args.site, inspect=args.inspect))


def cmd_leads(args) -> None:
    from . import leads

    cfg = load_config()
    with DB(cfg.db_path) as db:
        if args.action == "list":
            for l in leads.top(db, status=args.status, kind=args.kind, limit=args.limit):
                print(f"#{l['id']:<5} {l['score']:>5.0f}  {l['kind']:<10} {l['status']:<9} {l['name'][:40]:<40} {l['email'] or ''}")
        elif args.action == "seed":
            print(leads.import_seeds(db, COFOUNDER_DIR / "seeds" / "berlin_partners.csv"), "new seed leads")
        elif args.action == "enrich":
            from .jobs import run_job

            _print(run_job(cfg, "enrich"))
        elif args.action == "export":
            path = cfg.data_dir / "leads.csv"
            print(leads.export_csv(db, path), "leads ->", path)
        elif args.action == "status":
            leads.set_status(db, args.id, args.value, args.note or "")
            print("ok")
        elif args.action == "draft":
            from .drafts import outreach_draft

            _print(outreach_draft(cfg, db, args.id))
        elif args.action == "add":
            _print(leads.upsert(db, args.name, args.kind_new, source="manual", website=args.website or "", email=args.email or ""))


def cmd_find(args) -> None:
    from .leadquery import find, parse, to_csv

    cfg = load_config()
    q = " ".join(args.question)
    lq = parse(q)
    if args.limit:
        lq.limit = args.limit
    with DB(cfg.db_path) as db:
        res = find(cfg, db, lq, discover=args.discover)
        if args.enrich:
            from .leadintel import enrich_lead, profile
            from .scraping.fetcher import Fetcher

            f = Fetcher(db, min_delay=float(cfg.get("scraping.min_delay_seconds", 4)))
            try:
                for i, p in enumerate(res["results"]):
                    if p["website"] and (not p["email"] or not p["phone"]):
                        enrich_lead(db, f, p["id"])
                        res["results"][i] = profile(db, dict(db.one("SELECT * FROM leads WHERE id=?", (p["id"],))))
            finally:
                f.close()
    if args.csv:
        from pathlib import Path

        Path(args.csv).write_text(to_csv(res["results"]), encoding="utf-8-sig")
        print(f"{len(res['results'])} leads -> {args.csv}")
        return
    print(f"Understood: {', '.join(res['understood']) or '(free text)'}   ·   {res['total']} match"
          + (f", showing {len(res['results'])}" if res["total"] > len(res["results"]) else ""))
    for p in res["results"]:
        dist = f"{p['distance_m']} m from {p['campus'].split(' (')[0]}" if p["distance_m"] is not None else ""
        print(f"\n#{p['id']} {p['name']}  ·  {p['category']}{' / ' + p['subcategory'] if p['subcategory'] else ''}  ·  {p['price'] or '–'}"
              f"  ·  join {p['join_likelihood']:.0%} ({p['likelihood']})")
        print(f"     {p['address'] or 'no address'}{' · ' + p['district'] if p['district'] else ''}{' · ' + dist if dist else ''}")
        print(f"     ✉ {p['email'] or '–'}   ☎ {p['phone'] or '–'}   IG {p['instagram'] or '–'}"
              f"{' (' + format(p['ig_followers'], ',') + ')' if p['ig_followers'] else ''}{'   owner ' + p['owner'] if p['owner'] else ''}")
        print(f"     on: {', '.join(p['platforms']) or '–'}   ·   {'contacted ' + (p['last_contact'] or '') if p['contacted_before'] else 'never contacted'}")
    if res["missing_contacts"]:
        print(f"\n{res['missing_contacts']} have no email/phone yet: add --enrich to read their website + Impressum.")


def cmd_leadgen(args) -> None:
    from . import leadgen

    cfg = load_config()
    with DB(cfg.db_path) as db:
        if args.action == "scan":
            _print(leadgen.discover(cfg, db, args.campus, force=args.force))
        elif args.action == "coverage":
            for c in leadgen.coverage(cfg, db):
                print(f"{c['campus'][:32]:<32} found {c['found']:>4}  with email {c['with_email']:>3}  active {c['active']:>3}  partners {c['partners']:>3}")
        else:
            for l in leadgen.near_campus(db, args.campus, args.limit, status=args.status):
                print(f"#{l['id']:<5} {l['score']:>4.0f}  {l['distance_m'] or 0:>4} m  {l['status']:<9} {l['name'][:34]:<34} {l['category'][:22]:<22} {l['email'] or ''}")


def cmd_pipeline(args) -> None:
    from . import pipeline as pl

    cfg = load_config()
    with DB(cfg.db_path) as db:
        a = args.action
        if a == "start":
            print(pl.start_sequence(cfg, db, int(args.args[0])), "sequence steps added")
        elif a == "auto":
            _print(pl.auto_start(cfg, db, int(args.args[0]) if args.args else None))
        elif a == "next":
            _print(pl.set_next_step(db, int(args.args[0]), " ".join(args.args[1:]))["next_step_due"])
        elif a == "stale":
            for l in pl.stale(db):
                print(f"#{l['id']:<5} {l['status']:<9} {l['days_in_stage']:>5.1f}d  {l['name'][:34]:<34} → {l['suggestion']}")
        elif a == "routes":
            for r in pl.routes(cfg, db, args.args[0] if args.args else None):
                print(f"{r['campus']}: {len(r['stops'])} stops, {r['walk_km']} km (~{r['walk_min']} min walk)")
                print("   " + " → ".join(s["name"] for s in r["stops"]))
                print("   " + r["maps_url"])
        else:
            print("Stage counts:", ", ".join(f"{k} {v}" for k, v in pl.summary(cfg, db)["counts"].items()))
            print("\nConversion")
            for c in pl.conversion(db):
                print(f"  {c['step']:<20} {c['rate']:.0%}{' (assumed)' if c['assumed'] else ' of ' + str(c['sample'])}"
                      + (f", median {c['median_days']} days" if c["median_days"] is not None else ""))
            print("\nThis week vs target")
            for t in pl.targets(cfg, db):
                print(f"  {t['stage']:<10} {t['actual_week']:>3} / {t['target_week']:<5g} {'✓' if t['on_track'] else '✗ behind'}")
            f = pl.forecast(cfg, db)
            print(f"\nPartners this month: {f['won_this_month']} won + {f['expected_from_pipeline']} expected from pipeline "
                  f"(goal {f['goal_month']:g})")
            h = pl.hygiene(db)
            if h:
                print(f"\n{len(h)} active leads have no next step: " + ", ".join(f"#{l['id']} {l['name']}" for l in h[:8]))


def cmd_week(args) -> None:
    from datetime import date, timedelta

    from . import planner

    cfg = load_config()
    with DB(cfg.db_path) as db:
        start = date.fromisoformat(args.start) if args.start else None
        if args.next:
            start = planner.week_start_for(date.today()) + timedelta(days=7)
        plan = planner.plan_week(cfg, db, start)
        if args.ics:
            from pathlib import Path

            Path(args.ics).write_text(planner.to_ics(plan), encoding="utf-8")
            print("calendar file ->", args.ics)
            return
        if args.apply:
            print(planner.apply_plan(db, plan), "to-dos got a planned day")
        for d in plan["days"]:
            L = d["load"]
            print(f"\n{d['weekday']} {d['date']}  ·  meetings {L['meeting_min'] / 60:.1f} h  ·  planned {L['scheduled_min'] / 60:.1f} h"
                  f" of {L['free_min'] / 60:.1f} h free{'  (past)' if d['past'] else ''}")
            rows = [(m["start"], f"[meeting] {m['title']}") for m in d["meetings"]]
            rows += [(i["start"], f"{i['title']} ({i['minutes']}m)") for b in d["blocks"] for i in b["items"]]
            for t, txt in sorted(rows):
                print(f"   {t}  {txt}")
        if plan["overflow"]:
            print("\nDoesn't fit: " + "; ".join(f"#{o['task_id']} {o['title']} ({o['minutes']}m)" for o in plan["overflow"]))
        if plan["insights"]:
            print("\nSetz suggests:")
            for i in plan["insights"]:
                print(f"  - {i['text']}")


def cmd_brain(args) -> None:
    from . import brain

    cfg = load_config()
    with DB(cfg.db_path) as db:
        a, text = args.action, " ".join(args.text)
        if a == "learn":
            _print(brain.learn(cfg, db))
        elif a == "remember":
            print("remembered #", brain.remember(cfg, db, text, kind=args.kind, subject=args.subject or "", importance=0.85))
        elif a == "recall":
            for m in brain.recall(cfg, db, text, args.k):
                print(f"{m['score']:.2f}  [{m['kind']}{' · ' + m['subject'] if m['subject'] else ''}] {m['text']}")
        elif a == "forget":
            print("removed" if brain.forget(db, int(text)) else "not found")
        elif a == "model":
            g = brain.graph(cfg, db, 10)["model"]
            if not g:
                print("Not trained yet: needs about 20 contacted leads with a known outcome. Run `sz brain learn` later.")
            else:
                print(f"Lead reply model: {g['n']} leads ({g['positives']} replied), cross-validated AUC {g['auc']}"
                      f"{'' if g['trusted'] else ' (not trusted yet: below 0.55)'}")
                for name, v in g["importance"][:8]:
                    print(f"  {name:<22} {v:+.3f}")
        else:
            _print(brain.stats(db))


def cmd_dashboard(args) -> None:
    from .dashboard.app import serve

    args.port = args.port or int(load_config().get("dashboard.port", 8765))
    print(f"Settleezy HQ on http://127.0.0.1:{args.port}   ·   hologram: http://127.0.0.1:{args.port}/hologram")
    serve(args.port)


def cmd_voice(args) -> None:
    cfg = load_config()
    if args.action == "practice":
        from .voice import practice

        m = practice(cfg, max_seconds=args.seconds, topic=args.topic, use_ai=not args.no_ai)
        _print_speech(m)
    elif args.action == "analyse":
        from . import speech

        if not args.file:
            sys.exit("usage: sz voice analyse recording.wav [--coach]")
        audio = speech.decode(args.file)
        from faster_whisper import WhisperModel

        model = WhisperModel(cfg.get("voice.whisper_model", "small"), device="auto", compute_type="int8")
        segs, info = model.transcribe(audio, word_timestamps=True, vad_filter=True)
        segs = list(segs)
        m = speech.analyse(audio, words=speech.words_from_segments(segs), lang=info.language if info.language in ("en", "de") else "en")
        m["coaching"] = speech.coach(cfg, m, args.topic) if args.coach else ""
        with DB(cfg.db_path) as db:
            speech.save(db, m, "file", m["coaching"])
        _print_speech(m)
    elif args.action == "stats":
        from . import speech

        with DB(cfg.db_path) as db:
            _print(speech.trend(db, 60))
    else:
        from .voice import run

        run(cfg, wake_word=not args.no_wake)


def _print_speech(m: dict) -> None:
    if m.get("error"):
        sys.exit(m["error"])
    print(f"Delivery score {m['score']}/100  ·  {m['duration_s']} s  ·  {m.get('wpm') or '–'} wpm  ·  "
          f"fillers {m.get('fillers_per_min') or 0}/min {m.get('fillers') or ''}")
    print(f"Pitch {m.get('pitch_hz', '–')} Hz, variation {m.get('pitch_variation_st', '–')} st ({m.get('energy')})  ·  "
          f"pauses {m.get('pauses')} ({m.get('long_pauses')} long)  ·  clarity {m.get('clarity') or '–'}  ·  hedges {m.get('hedges') or 'none'}")
    for t in m.get("tips", []):
        print("  - " + t)
    if m.get("coaching"):
        print("\n" + m["coaching"])


def cmd_mcp(args) -> None:
    from .mcp_server import main

    main()


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="sz", description="Settleezy co-founder: assistant, growth engine, dashboard.")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="create config.toml/.env, database and seed leads").set_defaults(fn=cmd_init)
    a = sub.add_parser("auth", help="sign in to Outlook (device code)")
    a.add_argument("service", choices=["outlook", "instagram"])
    a.set_defaults(fn=cmd_auth)

    from .jobs import JOBS

    r = sub.add_parser("run", help="run one job: " + ", ".join(JOBS))
    r.add_argument("job", choices=list(JOBS))
    r.set_defaults(fn=cmd_run)
    for name, help_ in (("sync", "sync Outlook mail + calendar"), ("learn", "learn your voice + outreach patterns"),
                        ("growth", "weekly co-founder growth review")):
        s = sub.add_parser(name, help=help_)
        s.set_defaults(fn=cmd_run, job={"sync": "mail"}.get(name, name))

    m = sub.add_parser("morning", help="sync everything, write drafts, build the brief")
    m.add_argument("--speak", action="store_true")
    m.set_defaults(fn=cmd_morning)

    sub.add_parser("doctor", help="check Outlook, Instagram, Calendly, Claude, Ollama, ElevenLabs end to end").set_defaults(fn=cmd_doctor)
    k = sub.add_parser("kpi", help="record a number only you have, e.g. sz kpi paying_members 120")
    k.add_argument("key")
    k.add_argument("value", type=float)
    k.add_argument("--day", help="YYYY-MM-DD (default today)")
    k.set_defaults(fn=cmd_kpi)

    sub.add_parser("plan", help="today's plan: routine + meetings + what to do in each block").set_defaults(fn=cmd_plan)
    t = sub.add_parser("todo", help="to-dos: sz todo | sz todo add call Kranz tomorrow | sz todo done 3 | sz todo snooze 3 2")
    t.add_argument("action", nargs="?", default="list", choices=["list", "add", "done", "snooze", "reopen", "delete", "done-list"])
    t.add_argument("text", nargs="*")
    t.set_defaults(fn=cmd_todo)
    pa = sub.add_parser("partners", help="service partners: list | stats | add <name> | advance <id> | set <id> <field> <value>")
    pa.add_argument("action", nargs="?", default="list", choices=["list", "stats", "add", "advance", "set"])
    pa.add_argument("rest", nargs="*")
    pa.add_argument("--kind", help="venue | brand | university | housing | service")
    pa.set_defaults(fn=cmd_partners)
    rc = sub.add_parser("reach", help="who to reach out to now, and why")
    rc.add_argument("--limit", type=int, default=20)
    rc.set_defaults(fn=cmd_reach)
    pr = sub.add_parser("prep", help="one-page prep for your next (or a given) meeting")
    pr.add_argument("event", nargs="?")
    pr.add_argument("--no-ai", action="store_true")
    pr.set_defaults(fn=cmd_prep)
    sub.add_parser("scorecard", help="this week vs last week").set_defaults(fn=cmd_scorecard)
    ag = sub.add_parser("agents", help="Setz's specialist agents: sz agents | sz agents run [--agent atlas --agent scout]")
    ag.add_argument("action", nargs="?", default="list", choices=["list", "run"])
    ag.add_argument("--agent", action="append", help="hermes, atlas, scout, hunter, nova, quant, chrono, campus, sentinel")
    ag.set_defaults(fn=cmd_agents)
    im = sub.add_parser("import", help="import exported contacts/leads (CSV/XLSX/JSON) and outreach/social docs (MD/TXT/DOCX)")
    im.add_argument("folder")
    im.set_defaults(fn=cmd_import)

    sub.add_parser("triage", help="show replies owed + follow-ups due").set_defaults(fn=cmd_triage)

    d = sub.add_parser("drafts", help="write reply/follow-up drafts in Outlook (never sends)")
    d.add_argument("--limit", type=int, default=None)
    d.add_argument("--min-priority", type=int, default=2)
    d.set_defaults(fn=cmd_drafts)

    b = sub.add_parser("brief", help="daily brief")
    b.add_argument("--speak", action="store_true")
    b.add_argument("--no-actions", action="store_true", help="skip the AI next-actions section")
    b.set_defaults(fn=cmd_brief)

    sc = sub.add_parser("scrape", help="scan competitor sites for new Berlin listings")
    sc.add_argument("--site")
    sc.add_argument("--inspect", action="store_true", help="show what would be extracted; save nothing")
    sc.set_defaults(fn=cmd_scrape)

    l = sub.add_parser("leads", help="lead database")
    l.add_argument("action", choices=["list", "seed", "enrich", "export", "status", "draft", "add"])
    l.add_argument("id", nargs="?", type=int)
    l.add_argument("value", nargs="?")
    l.add_argument("--note")
    l.add_argument("--status")
    l.add_argument("--kind")
    l.add_argument("--limit", type=int, default=30)
    l.add_argument("--name")
    l.add_argument("--kind-new", default="merchant")
    l.add_argument("--website")
    l.add_argument("--email")
    l.set_defaults(fn=cmd_leads)

    fd = sub.add_parser("find", help='find leads in plain words: sz find "vegan cafés near HU with email, not contacted"')
    fd.add_argument("question", nargs="+")
    fd.add_argument("--csv", help="write the full list to a CSV file (opens in Excel)")
    fd.add_argument("--enrich", action="store_true", help="read website + Impressum for results missing email/phone")
    fd.add_argument("--discover", action="store_true", help="scan OpenStreetMap around the campus if there are few results")
    fd.add_argument("--limit", type=int, default=0)
    fd.set_defaults(fn=cmd_find)

    lg = sub.add_parser("leadgen", help="campus venues from OpenStreetMap: sz leadgen scan [--campus TU] | list | coverage")
    lg.add_argument("action", nargs="?", default="list", choices=["scan", "list", "coverage"])
    lg.add_argument("--campus")
    lg.add_argument("--status")
    lg.add_argument("--limit", type=int, default=40)
    lg.add_argument("--force", action="store_true", help="scan again even if done today")
    lg.set_defaults(fn=cmd_leadgen)

    pp = sub.add_parser("pipeline", help="sz pipeline | stale | routes [campus] | start <lead> | auto [n] | next <lead> <text>")
    pp.add_argument("action", nargs="?", default="show", choices=["show", "stale", "routes", "start", "auto", "next"])
    pp.add_argument("args", nargs="*")
    pp.set_defaults(fn=cmd_pipeline)

    wk = sub.add_parser("week", help="plan the week: time-block to-dos around meetings, load and suggestions")
    wk.add_argument("--next", action="store_true", help="plan next week")
    wk.add_argument("--start", help="any date in the week to plan (YYYY-MM-DD)")
    wk.add_argument("--apply", action="store_true", help="give undated/overdue to-dos their planned day")
    wk.add_argument("--ics", help="write the plan as a calendar file to import into Outlook")
    wk.set_defaults(fn=cmd_week)

    br = sub.add_parser("brain", help="Setz's memory + neural lead model: stats | learn | recall <q> | remember <text> | forget <id> | model")
    br.add_argument("action", nargs="?", default="stats", choices=["stats", "learn", "recall", "remember", "forget", "model"])
    br.add_argument("text", nargs="*")
    br.add_argument("--subject")
    br.add_argument("--kind", default="fact", choices=["fact", "preference", "episode", "insight"])
    br.add_argument("-k", type=int, default=8)
    br.set_defaults(fn=cmd_brain)

    db_ = sub.add_parser("dashboard", help="open the local dashboard")
    db_.add_argument("--port", type=int, default=None)
    db_.set_defaults(fn=cmd_dashboard)

    v = sub.add_parser("voice", help="Setz voice: sz voice | sz voice practice | sz voice analyse call.wav --coach | sz voice stats")
    v.add_argument("action", nargs="?", default="run", choices=["run", "practice", "analyse", "stats"])
    v.add_argument("file", nargs="?")
    v.add_argument("--no-wake", action="store_true", help="push-to-talk with Enter instead of the wake word")
    v.add_argument("--seconds", type=float, default=180, help="practice: maximum length")
    v.add_argument("--topic", default="venue partnership pitch", help="what you're practising, for the AI coach")
    v.add_argument("--no-ai", action="store_true", help="practice: delivery numbers only, no AI coaching")
    v.add_argument("--coach", action="store_true", help="analyse: add AI coaching on the content")
    v.set_defaults(fn=cmd_voice)

    sub.add_parser("mcp", help="run the MCP server (used by OpenJarvis)").set_defaults(fn=cmd_mcp)

    args = p.parse_args(argv)
    try:
        args.fn(args)
    except KeyboardInterrupt:
        sys.exit(130)
    except RuntimeError as exc:
        sys.exit(f"error: {exc}")


if __name__ == "__main__":
    main()
