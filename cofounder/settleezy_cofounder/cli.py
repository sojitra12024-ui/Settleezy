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


def cmd_dashboard(args) -> None:
    from .dashboard.app import serve

    args.port = args.port or int(load_config().get("dashboard.port", 8765))
    print(f"Settleezy HQ on http://127.0.0.1:{args.port}   ·   hologram: http://127.0.0.1:{args.port}/hologram")
    serve(args.port)


def cmd_voice(args) -> None:
    from .voice import run

    run(load_config(), wake_word=not args.no_wake)


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

    db_ = sub.add_parser("dashboard", help="open the local dashboard")
    db_.add_argument("--port", type=int, default=None)
    db_.set_defaults(fn=cmd_dashboard)

    v = sub.add_parser("voice", help="hands-free voice assistant ('Hey Jarvis')")
    v.add_argument("--no-wake", action="store_true", help="push-to-talk with Enter instead of the wake word")
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
