"""Scribe: drafts every email the way the founder would write it, and decides what to say the way they would.

Learning (`sz scribe learn`, weekly, all local)
  From the synced mailbox (default: the last 90 days) Scribe builds pairs: the email someone sent -> the founder's
  reply, plus first emails and follow-ups the founder started. From them it learns
    style      greetings by situation (Dear / Hello / Hi, first name or Mr./Ms. surname), the opener
               ("I hope you are doing well."), sign-off and signature, length per situation, paragraphing,
               bullets, recurring phrases, words they never use, German du/Sie
    decisions  for each situation (they want to reschedule, they have doubts, they sent contract changes, they went
               quiet, ...) which moves the founder makes and how often: propose a concrete day, share the booking
               link, offer to visit in person, apologise first, answer point by point, ask a clarifying question,
               restate next steps, defer until something arrives, thank for a quick reply ...
    examples   every past reply is kept as an example; the closest ones are shown to the writer for each new draft
  The profile lives in data/scribe_profile.json (never in the repository). data/style_seed.json, if present, adds
  priors from a one-off analysis of the founder's mail, used where the mailbox has too few examples.

Drafting (`plan` -> `compose` -> `check`)
  plan     classify the incoming email (situation, language, formality, open questions, proposed times) and decide
           the moves, using the founder's own frequencies; facts Setz has (free calendar slots, booking link,
           partner record, contract status) are filled in; anything only the founder can decide becomes
           [[CHECK: ...]] or two options to pick from
  compose  Claude (or the local model) writes the reply with the style rules, the decision and the founder's 3 most
           similar past replies; without any model a template writer uses the profile directly
  check    scores the draft against the profile (greeting, sign-off, length, phrases they never use) and fixes
           what can be fixed mechanically
Learning from edits: when the founder sends a reply in a thread where Scribe drafted, the difference is recorded;
phrases they keep deleting are added to "never use" and acceptance per situation sets how confident Scribe is.
Drafts only: nothing is ever sent.
"""

from __future__ import annotations

import difflib
import json
import re
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import Config
from .db import DB, utcnow
from .mailtext import detect_language, greeting, sign_off

# -- situations (intents) of an incoming email --------------------------------------------------------------

INTENTS: dict[str, dict[str, Any]] = {
    "reschedule_request": {"label": "They want to move a meeting",
                           "rx": r"(reschedul|postpone|move (our|the) (call|meeting)|another (day|time)|can'?t make it|cannot make it|"
                                 r"something came up|verschieben|anderen termin|neuen termin|kann (leider )?nicht|absagen müssen|"
                                 r"unable to (attend|join)|need to cancel|have to cancel)"},
    "meeting_request": {"label": "They want to meet",
                        "rx": r"(would (you|it) be (possible|available)|are you available|when (are|would) you|schedule a (call|meeting)|"
                              r"set up a (call|meeting)|hop on a call|meet (up|next week)|kennenlernen|treffen|telefonat|"
                              r"termin vereinbaren|zeit für ein|let'?s (meet|talk|have a call)|visit (us|our)|come by)"},
    "meeting_confirm": {"label": "They confirmed a time",
                        "rx": r"(see you (then|on|tomorrow)|confirmed|that works|works for me|passt (mir|uns)|bis dann|sounds good)"},
    "contract_changes": {"label": "They sent changes to an agreement",
                         "rx": r"(contract|agreement|vertrag|vereinbarung).{0,120}(change|correct|amend|adjust|revis|änder|korrigier|anpass)|"
                               r"(change|correct|amend|adjust|änder|korrigier).{0,120}(contract|agreement|vertrag|vereinbarung)"},
    "signed": {"label": "They signed / sent the agreement back",
               "rx": r"(signed|unterschrieben|unterzeichnet|attached the (signed|contract|agreement)|anbei .{0,40}(vertrag|vereinbarung))"},
    "doubt": {"label": "They have doubts or questions about the offer",
              "rx": r"(how does (it|this) work|what (exactly|would)|could you (explain|clarify)|not sure|unsure|concern|"
                    r"question|wie funktioniert|was genau|frage|unklar|bedenken|what are the (costs|fees)|is there a fee|"
                    r"kostet|provision|commission|why should|what do we get|benefit for us)"},
    "problem": {"label": "Something doesn't work",
                "rx": r"(doesn'?t work|not working|error|issue with|problem with|can'?t (open|access|submit|log)|unable to (open|access|submit)|"
                      r"funktioniert nicht|fehler|geht nicht|klappt nicht|broken)"},
    "interested": {"label": "They are interested",
                   "rx": r"(interested|happy to (join|partner|collaborate)|sounds (great|interesting|good)|would love to|"
                         r"gerne (dabei|mitmachen|kooperieren)|interesse|klingt (gut|spannend|interessant)|let'?s do it)"},
    "decline": {"label": "They decline",
                "rx": r"(not interested|no interest|not (a )?(good )?fit|decline|won'?t be able to|not at this time|kein interesse|"
                      r"leider (nicht|keine)|absage|passt (leider )?nicht|derzeit nicht|we (have|will) decided? not)"},
    "low_offer": {"label": "They offer a weak member benefit / negotiate",
                  "rx": r"(we (can|could) (only )?offer|our (standard|usual) (student )?discount|(5|10) ?% (discount|off|rabatt)|"
                        r"können (nur|lediglich)|bieten (wir )?(nur|lediglich)|maximal \d+ ?%|we offer the same .{0,30}(to all|every))"},
    "exclusivity": {"label": "They ask for exclusivity", "rx": r"(exclusiv|exklusiv|only partner|sole partner|einziger partner)"},
    "no_show": {"label": "They didn't show up / are late", "rx": r"(i'?m (in|waiting in) the meeting|are you joining|waiting for you|warte auf sie)"},
    "info_request": {"label": "They ask for information or documents",
                     "rx": r"(send (me|us) (more )?(info|details|information|the deck|a proposal)|more information|unterlagen|"
                           r"informationen (zu|über)|details (zu|über)|media kit|one.?pager|presentation)"},
    "invitation": {"label": "An invitation (event, panel, fair)",
                   "rx": r"(invite you|invitation|einladung|laden (sie|dich) .{0,20}ein|join us (at|for)|you are invited)"},
    "payment": {"label": "Invoice, payment or billing",
                "rx": r"(invoice|rechnung|payment|zahlung|bank transfer|überweisung|billing|refund|erstattung)"},
    "thanks": {"label": "Thanks / acknowledgement only",
               "rx": r"(?s)^(?!.*\?).{0,400}\b(thank you|thanks|danke|vielen dank|noted|got it|perfect|perfekt)\b"},
}
INTENT_ORDER = ["reschedule_request", "no_show", "contract_changes", "signed", "problem", "decline", "exclusivity", "low_offer", "doubt", "meeting_request",
                "meeting_confirm", "info_request", "invitation", "payment", "interested", "thanks"]
OWN_INTENTS = {"first_outreach": "First email to someone new", "follow_up": "Following up after no reply",
               "recap": "Recap after a meeting with next steps", "other": "Other reply"}

# -- the moves a reply can make (detected in the founder's own replies) ------------------------------------

MOVES: dict[str, dict[str, str]] = {
    "thank_quick": {"label": "Thanks them (for the quick reply / their time)",
                    "rx": r"(thank ?you|thankyou|thanks|vielen dank|danke)( (so|very) much)? (for|für)"},
    "apologize": {"label": "Apologises first", "rx": r"(apolog|sorry|entschuldig|pardon|my bad)"},
    "propose_day": {"label": "Proposes a concrete day/time",
                    "rx": r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday|tomorrow|next week|montag|dienstag|mittwoch|"
                          r"donnerstag|freitag|samstag|sonntag|morgen|nächste woche)\b.{0,40}(\d{1,2}(:\d\d)?\s?(am|pm|uhr)?|morning|afternoon|vormittag|nachmittag)?"},
    "booking_link": {"label": "Shares the booking link",
                     "rx": r"(link below|booking link|book a (discovery )?call|schedule .{0,30}(link|availability)|calendly|"
                           r"as per your availability|über den link|buchungslink)"},
    "offer_visit": {"label": "Offers to visit in person", "rx": r"\b(visit|vorbeikommen|besuchen|stop by|drop by|in person|persönlich)\b"},
    "ask_options": {"label": "Asks them for time options",
                    "rx": r"(what time would work|which (day|time)|advise (a )?few options|let me know (what|which) time|"
                          r"when (would|are) you (be )?available|welche zeit|wann (passt|hätten))"},
    "confirm": {"label": "Confirms / accepts", "rx": r"(works (for me|well)|see you (then|on|tomorrow)|confirmed|passt (mir|gut)|gerne|sounds good)"},
    "answer_points": {"label": "Answers point by point", "rx": r"(regarding|as for|about the|bezüglich|zu (ihrer|deiner) frage|^\s*(\d+[.)]|[-•])\s)"},
    "clarify_question": {"label": "Asks a clarifying question", "rx": r"(could you (please )?(confirm|clarify|share|let me know)|can you (please )?(help )?(check|confirm)|könnten sie|kannst du)"},
    "next_steps": {"label": "Restates next steps", "rx": r"(next steps?|as discussed|to begin|please (complete|fill|sign)|nächste schritte|wie besprochen)"},
    "attach": {"label": "Attaches / sends a document", "rx": r"(attached|please find|anbei|im anhang|one.?page|overview)"},
    "defer": {"label": "Defers until something arrives", "rx": r"(once i (receive|have|get)|as soon as|sobald|i will (review|get back|send))"},
    "form_link": {"label": "Points to the onboarding form", "rx": r"(form|formular|application)"},
    "decline_politely": {"label": "Declines politely", "rx": r"(unfortunately|leider|not able to|won'?t be able|at this time)"},
    "empathy": {"label": "Shows empathy first (illness, holiday, bad news)", "rx": r"(sorry to hear|hope you (feel|are feeling) better|gute besserung|don'?t worry about the delay)"},
    "as_discussed": {"label": "Restates what was agreed", "rx": r"(as discussed|as agreed|wie besprochen|wie vereinbart)"},
    "extra_asks": {"label": "Adds the standard extra asks (filming visit, HD photo, exclusive code)",
                   "rx": r"(film(ing)?|hd (interior )?photo|exclusive (voucher )?code|orientation week|booth)"},
    "deadline": {"label": "Sets a clear deadline", "rx": r"(by \d{1,2}(st|nd|rd|th)? \w+|after this date|within the next \d+ (hours|days)|bis zum \d)"},
    "pitch": {"label": "Restates the Settleezy model (verified students, no commission, no hardware)",
              "rx": r"(verified (students|members|community)|no commission|commission model|no hardware|member privilege)"},
    "keep_door_open": {"label": "Keeps the door open", "rx": r"(in the future|future collaboration|stay in touch|feel free to reach out|gerne in zukunft|melden sie sich gerne)"},
}

# What a sensible founder does when the mailbox has no examples yet for a situation (overridden by learned data).
DEFAULT_POLICY: dict[str, list[str]] = {
    "reschedule_request": ["thank_quick", "propose_day", "booking_link"],
    "meeting_request": ["thank_quick", "propose_day", "booking_link"],
    "meeting_confirm": ["confirm"],
    "contract_changes": ["thank_quick", "defer", "next_steps"],
    "signed": ["thank_quick", "next_steps"],
    "doubt": ["thank_quick", "answer_points", "offer_visit"],
    "problem": ["apologize", "clarify_question", "offer_visit"],
    "interested": ["thank_quick", "next_steps", "form_link", "attach"],
    "decline": ["thank_quick", "decline_politely", "keep_door_open"],
    "info_request": ["thank_quick", "attach", "propose_day"],
    "low_offer": ["thank_quick", "pitch", "clarify_question", "deadline"],
    "exclusivity": ["thank_quick", "pitch", "propose_day"],
    "no_show": ["clarify_question", "propose_day"],
    "invitation": ["thank_quick", "confirm"],
    "payment": ["thank_quick", "clarify_question"],
    "thanks": [],
    "follow_up": ["next_steps", "propose_day"],
    "first_outreach": ["next_steps", "propose_day"],
    "other": ["thank_quick"],
}
SYSTEM_PREFIXES = ("Accepted:", "Declined:", "Canceled:", "Cancelled:", "Tentative:", "Updated invitation", "Automatic reply")


def classify(text: str, subject: str = "") -> list[str]:
    """Situations in an incoming email, most important first."""
    t = f"{subject}\n{text}".lower()
    hits = [k for k in INTENT_ORDER if re.search(INTENTS[k]["rx"], t, re.I | re.M)]
    if "thanks" in hits and len(hits) > 1:
        hits.remove("thanks")
    return hits or ["other"]


def moves_in(text: str) -> list[str]:
    t = text.lower()
    return [k for k, m in MOVES.items() if re.search(m["rx"], t, re.I | re.M)]


def questions_in(text: str) -> list[str]:
    """The questions they asked (so the reply can answer each)."""
    qs = [q.strip() for q in re.findall(r"[^.?!\n]{8,240}\?", text or "")]
    return [q for q in qs if not re.match(r"(?i)(how are you|wie geht)", q)][:6]


# -- signature / body helpers ------------------------------------------------------------------------------

_SIGNOFF = re.compile(r"^(kind regards|best regards|warm regards|regards|best|many thanks|thanks|cheers|viele grüße|beste grüße|"
                      r"liebe grüße|mit freundlichen grüßen|freundliche grüße|lg|vg)\b", re.I)


def split_signature(text: str) -> tuple[str, str, str]:
    """(body, sign-off line, signature block)."""
    lines = (text or "").strip().splitlines()
    for i in range(len(lines) - 1, -1, -1):
        if _SIGNOFF.match(lines[i].strip()) and i >= max(0, len(lines) - 14):
            return "\n".join(lines[:i]).strip(), lines[i].strip(), "\n".join(lines[i + 1:]).strip()
    return (text or "").strip(), "", ""


def addressing(greet_line: str) -> str:
    """first_name | title_surname | team | none"""
    g = greet_line.strip()
    if re.search(r"\b(mr\.?|ms\.?|mrs\.?|dr\.?|herr|frau|prof\.?)\s", g, re.I):
        return "title_surname"
    if re.search(r"\b(team|all|everyone|zusammen|damen und herren)\b", g, re.I):
        return "team"
    return "first_name" if len(g.split()) >= 2 else "none"


def _words(s: str) -> int:
    return len(re.findall(r"\w+", s or ""))


# -- learning ------------------------------------------------------------------------------------------------

def _conversations(db: DB, since: str) -> dict[str, list[dict]]:
    convs: dict[str, list[dict]] = defaultdict(list)
    for r in db.q("SELECT * FROM messages WHERE conversation_id IN (SELECT conversation_id FROM messages WHERE folder='sent' AND sent_at >= ?) "
                  "ORDER BY sent_at", (since,)):
        convs[r["conversation_id"]].append(dict(r))
    return convs


def build_pairs(cfg: Config, db: DB, days: int = 90) -> list[dict]:
    """Every email the founder wrote in the window, with the situation it answered."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    me = {a.lower() for a in cfg.my_addresses}
    pairs = []
    for cid, msgs in _conversations(db, since).items():
        prev = None
        for m in msgs:
            if m["folder"] == "sent" and m["sent_at"] >= since and (m["body_text"] or "").strip() \
                    and not (m["subject"] or "").startswith(SYSTEM_PREFIXES):
                if prev is not None and prev["folder"] == "inbox" and not prev["automated"] and (prev["from_addr"] or "") not in me:
                    intents = classify(prev["body_text"], prev["subject"])
                    kind = "reply"
                elif prev is None:
                    intents, kind = ["first_outreach"], "own"
                elif prev["folder"] == "sent":
                    intents, kind = ["follow_up"], "own"
                else:
                    intents, kind = ["other"], "reply"
                if kind == "own" and re.search(r"(summary|next steps|recap|zusammenfassung)", m["subject"] or "", re.I) and prev is None:
                    intents = ["recap"]
                body, so, sig = split_signature(m["body_text"])
                pairs.append({"id": m["id"], "conversation_id": cid, "intent": intents[0], "intents": intents, "kind": kind,
                              "lang": m["language"] if m["language"] in ("en", "de") else "en",
                              "incoming": (prev or {}).get("body_text", "")[:3000] if kind == "reply" else "",
                              "incoming_subject": (prev or {}).get("subject", ""), "subject": m["subject"] or "",
                              "reply": body, "sign_off": so, "signature": sig, "sent_at": m["sent_at"],
                              "to": json.loads(m["to_addrs"] or "[]")[:3],
                              "hours_to_reply": round((_dt(m["sent_at"]) - _dt(prev["sent_at"])).total_seconds() / 3600, 1)
                              if prev is not None and kind == "reply" else None})
            prev = m
    return pairs


def _dt(s: str) -> datetime:
    d = datetime.fromisoformat((s or "").replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


_GENERIC = set("the and to of a in for is that with on you your we our i it be are this as at by from will would can could "
               "please thank thanks regards kind best hope well doing der die das und zu den ich sie wir ein eine mit für von".split())


def phrase_bank(texts: list[str], n: int = 30) -> list[str]:
    """Phrases (3-6 words) the founder reuses, most frequent first."""
    c: Counter = Counter()
    for t in texts:
        seen = set()
        for sent in re.split(r"(?<=[.!?])\s+|\n", t):
            w = re.findall(r"[A-Za-zÄÖÜäöüß'’]+", sent)
            for k in (6, 5, 4, 3):
                for i in range(0, max(0, len(w) - k + 1)):
                    g = " ".join(w[i:i + k])
                    if sum(x.lower() not in _GENERIC for x in w[i:i + k]) >= 1 and g.lower() not in seen:
                        seen.add(g.lower())
                        c[g] += 1
    out: list[str] = []
    for g, cnt in c.most_common(400):
        if cnt < 3:
            break
        if any(g.lower() in o.lower() for o in out):
            continue
        out = [o for o in out if o.lower() not in g.lower()] + [g]
        if len(out) >= n:
            break
    return out


def learn(cfg: Config, db: DB, days: int | None = None) -> dict[str, Any]:
    days = days or int(cfg.get("scribe.learn_days", 90))
    pairs = build_pairs(cfg, db, days)
    prof: dict[str, Any] = {"learned_at": utcnow(), "days": days, "emails": len(pairs), "style": {}, "decisions": {}, "lengths": {}}
    for lang in ("en", "de"):
        ps = [p for p in pairs if p["lang"] == lang]
        if not ps:
            continue
        bodies = [p["reply"] for p in ps]
        greets = Counter(g for p in ps if (g := greeting(p["reply"])))
        by_addr: dict[str, Counter] = defaultdict(Counter)
        for p in ps:
            g = (p["reply"].strip().splitlines() or [""])[0]
            if greeting(p["reply"]):
                by_addr[p["kind"] + ":" + addressing(g)][greeting(p["reply"])] += 1
        openers = Counter()
        for b in bodies:
            lines = [ln.strip() for ln in b.splitlines() if ln.strip()]
            if len(lines) > 1 and len(lines[1]) < 90:
                openers[lines[1]] += 1
        st = {
            "emails": len(ps),
            "greetings": greets.most_common(6),
            "greeting_by_situation": {k: v.most_common(3) for k, v in by_addr.items()},
            "addressing": Counter(addressing((p["reply"].splitlines() or [""])[0]) for p in ps).most_common(),
            "openers": [o for o, n in openers.most_common(8) if n >= 2],
            "sign_offs": Counter(p["sign_off"] for p in ps if p["sign_off"]).most_common(4),
            "signature": Counter(p["signature"] for p in ps if p["signature"]).most_common(1)[0][0] if any(p["signature"] for p in ps) else "",
            "median_words": int(statistics.median([_words(b) for b in bodies])) if bodies else 0,
            "paragraph_lines": round(statistics.mean([b.count("\n") + 1 for b in bodies]), 1) if bodies else 0,
            "bullets_rate": round(sum(bool(re.search(r"^\s*([-•*]|\d+[.)])\s", b, re.M)) for b in bodies) / len(bodies), 2),
            "exclamation_rate": round(sum("!" in b for b in bodies) / len(bodies), 2),
            "phrases": phrase_bank(bodies),
            "never": [],
        }
        if lang == "de":
            st["du_vs_sie"] = {"du": sum(bool(re.search(r"\b(du|dir|dich|dein\w*)\b", b, re.I)) for b in bodies),
                               "Sie": sum(bool(re.search(r"\b(Sie|Ihnen|Ihr\w*)\b", b)) for b in bodies)}
        # spelling habits worth knowing (reproduced only if scribe.keep_quirks = true)
        quirks = {}
        for rx, label in ((r"\bThankyou\b", "Thankyou"), (r"\bthankyou\b", "thankyou")):
            n = sum(bool(re.search(rx, b)) for b in bodies)
            if n >= 2:
                quirks[label] = n
        st["quirks"] = quirks
        prof["style"][lang] = st
    # decisions: per situation, how often each move appears in the founder's reply
    by_intent: dict[str, list[dict]] = defaultdict(list)
    for p in pairs:
        by_intent[p["intent"]].append(p)
    for intent, ps in by_intent.items():
        mv = Counter(m for p in ps for m in moves_in(p["reply"]))
        prof["decisions"][intent] = {"n": len(ps), "moves": {k: round(v / len(ps), 2) for k, v in mv.most_common()},
                                     "median_words": int(statistics.median([_words(p["reply"]) for p in ps])),
                                     "median_hours_to_reply": statistics.median([p["hours_to_reply"] for p in ps if p["hours_to_reply"] is not None])
                                     if any(p["hours_to_reply"] is not None for p in ps) else None}
    # remember the examples (local only) for retrieval
    _save_examples(cfg, db, pairs)
    fb = feedback_summary(db)
    for lang, st in prof["style"].items():
        st["never"] = sorted(set(st.get("never", [])) | set(fb.get("deleted_phrases", {}).get(lang, [])))[:40]
    prof["acceptance"] = fb.get("acceptance", {})
    path = cfg.data_dir / "scribe_profile.json"
    path.write_text(json.dumps(prof, ensure_ascii=False, indent=1), encoding="utf-8")
    db.kv_set("last_run:scribe", utcnow())
    return {"emails": len(pairs), "situations": {k: v["n"] for k, v in prof["decisions"].items()},
            "languages": {k: v["emails"] for k, v in prof["style"].items()}, "path": str(path)}


def _ensure(db: DB) -> None:
    db.conn.execute("CREATE TABLE IF NOT EXISTS scribe_examples (id TEXT PRIMARY KEY, intent TEXT, lang TEXT, incoming TEXT, "
                    "reply TEXT, subject TEXT, sent_at TEXT)")
    db.conn.execute("CREATE TABLE IF NOT EXISTS scribe_drafts (id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT, intent TEXT, "
                    "moves TEXT, lang TEXT, draft TEXT, created_at TEXT, sent_text TEXT, similarity REAL, compared_at TEXT)")
    db.conn.commit()


def _save_examples(cfg: Config, db: DB, pairs: list[dict]) -> None:
    _ensure(db)
    db.conn.executemany("INSERT OR REPLACE INTO scribe_examples(id,intent,lang,incoming,reply,subject,sent_at) VALUES(?,?,?,?,?,?,?)",
                        [(p["id"], p["intent"], p["lang"], p["incoming"], p["reply"], p["subject"], p["sent_at"]) for p in pairs])
    db.conn.commit()


def profile(cfg: Config) -> dict[str, Any]:
    """Learned profile merged over the seed (data/style_seed.json) and the built-in defaults."""
    base: dict[str, Any] = {"style": {}, "decisions": {}, "seed": {}}
    seed = cfg.data_dir / "style_seed.json"
    if seed.exists():
        try:
            base["seed"] = json.loads(seed.read_text(encoding="utf-8"))
        except ValueError:
            pass
    p = cfg.data_dir / "scribe_profile.json"
    if p.exists():
        try:
            base.update(json.loads(p.read_text(encoding="utf-8")))
        except ValueError:
            pass
    return base


def policy(cfg: Config, intent: str) -> dict[str, Any]:
    """The moves for a situation: learned frequencies when there are >= 4 examples, else seed, else defaults."""
    prof = profile(cfg)
    d = prof["decisions"].get(intent)
    min_n = int(cfg.get("scribe.min_examples", 4))
    s = (prof.get("seed") or {}).get("decisions", {}).get(intent)
    if d and d["n"] >= min_n:
        moves = [m for m, f in d["moves"].items() if f >= 0.34]
        return {"moves": moves, "source": f"learned from {d['n']} of your replies", "freq": d["moves"], "median_words": d.get("median_words"),
                "notes": (s or {}).get("notes", "")}
    if s:
        return {"moves": s.get("moves", []), "source": "from the analysis of your mail", "freq": {}, "median_words": s.get("median_words"),
                "notes": s.get("notes", "")}
    return {"moves": DEFAULT_POLICY.get(intent, DEFAULT_POLICY["other"]), "source": "default (not enough examples yet)", "freq": {},
            "median_words": None}


# -- retrieval of the founder's own similar replies ---------------------------------------------------------

def similar_examples(cfg: Config, db: DB, incoming: str, intent: str, lang: str, k: int = 3) -> list[dict]:
    _ensure(db)
    rows = [dict(r) for r in db.q("SELECT * FROM scribe_examples WHERE lang=? ORDER BY sent_at DESC LIMIT 600", (lang,))]
    if not rows:
        return []
    try:
        from .brain import _dot, embedder

        emb = embedder(cfg)
        qv = emb.embed([incoming or intent], query=True)[0]
        vecs = emb.embed([(r["incoming"] or r["subject"] or r["reply"])[:1500] for r in rows])
        scored = [(_dot(qv, v) + (0.15 if r["intent"] == intent else 0), r) for r, v in zip(rows, vecs)]
    except Exception:
        qw = set(re.findall(r"\w{4,}", (incoming or "").lower()))
        scored = [(len(qw & set(re.findall(r"\w{4,}", (r["incoming"] or "").lower()))) / (1 + len(qw)) + (0.3 if r["intent"] == intent else 0), r)
                  for r in rows]
    scored.sort(key=lambda x: -x[0])
    return [r for _, r in scored[:k]]


# -- planning a reply -----------------------------------------------------------------------------------------

def _first_name(name: str) -> str:
    n = re.sub(r"[\"'<>()]", "", name or "").strip()
    if "," in n:   # "Doe, Jane"
        n = n.split(",", 1)[1].strip()
    return n.split()[0] if n else ""


# The founder writes in two registers: quick (2-4 sentences, logistics, often from the phone) and formal (structured,
# polished: terms, contracts, recaps, onboarding, declines, first emails).
QUICK = {"meeting_confirm", "thanks", "reschedule_request", "meeting_request", "invitation", "other"}
REGISTER_WORDS = {"quick": (25, 70), "formal": (120, 320)}


def register_for(intent: str, incoming: str) -> str:
    if intent in QUICK and len(re.findall(r"\w+", incoming or "")) < 160 and len(questions_in(incoming)) <= 1:
        return "quick"
    return "formal"


FOLLOW_UP_LADDER = {   # the founder's escalation: soft nudge -> new reason/timeline -> deadline with a consequence
    1: (["next_steps", "form_link"], "Soft and short: 'I wanted to kindly follow up...', restate the one next step (with the link)."),
    2: (["next_steps", "propose_day", "pitch"], "Add a business reason or timeline (semester start, launch date); offer a call or a visit."),
    3: (["deadline", "next_steps", "keep_door_open"], "Firm and polite: say this is the third time, give a date to reply by and what happens "
                                                      "after it (we approach other venues in this category); no grovelling."),
}


def plan(cfg: Config, db: DB, incoming: str, *, subject: str = "", sender_name: str = "", sender_email: str = "",
         conversation_id: str = "", kind: str = "reply", followup_number: int = 0) -> dict[str, Any]:
    """Decide what the reply should do, the way the founder decides."""
    lang = reply_language(cfg, incoming)
    intents = classify(incoming, subject) if kind == "reply" else [kind]
    intent = intents[0]
    pol = policy(cfg, intent)
    moves = list(pol["moves"])
    if intent == "follow_up" and followup_number:
        ladder, note = FOLLOW_UP_LADDER[min(3, max(1, followup_number))]
        moves = list(dict.fromkeys(ladder + [m for m in moves if m not in ladder and m != "deadline"]))
        pol = {**pol, "notes": (pol.get("notes", "") + " " + note).strip(), "source": pol["source"] + f", follow-up #{followup_number}"}
    if re.search(r"\b(sick|unwell|ill|hospital|krank|beim arzt|im urlaub|on vacation|on holiday|family emergency|condolence|passed away|verstorben)\b", incoming or "", re.I) and "empathy" not in moves:
        moves.insert(0, "empathy")
    facts: dict[str, Any] = {}
    checks: list[str] = []
    prior = []
    if conversation_id:
        prior = [dict(r) for r in db.q("SELECT folder, body_text, sent_at FROM messages WHERE conversation_id=? ORDER BY sent_at", (conversation_id,))]
    first_contact = not any(r["folder"] == "sent" for r in prior)
    # formality: how the founder addressed this person before, else the founder's habit for this situation
    prev_greet = next((greeting(r["body_text"]) for r in reversed(prior) if r["folder"] == "sent" and greeting(r["body_text"])), "")
    prev_line = next(((r["body_text"].strip().splitlines() or [""])[0] for r in reversed(prior) if r["folder"] == "sent"), "")
    facts["greeting"] = choose_greeting(cfg, lang, first_contact, prev_greet, prev_line, sender_name, incoming)
    # scheduling facts
    if intent in ("reschedule_request", "meeting_request") or "propose_day" in moves:
        try:
            from .scheduling import fmt_slot, proposed_times, suggest_slots

            props = [p for p in proposed_times(incoming, datetime.now()) if p > datetime.now()]
            if props:
                from .scheduling import is_free

                mins = int(cfg.get("scheduling.meeting_minutes", 30))
                free = [p for p in props if is_free(cfg, db, p, mins)]
                if free:
                    facts["accept_time"] = free[0].strftime("%A %d %B, %H:%M") if lang == "en" else free[0].strftime("%d.%m. %H:%M Uhr")
                    moves = ["confirm"] + [m for m in moves if m not in ("propose_day", "ask_options")]
                else:
                    checks.append("They proposed a time you're busy at: offer the slots below instead")
            slots = suggest_slots(cfg, db, n=2)
            facts["slots"] = [fmt_slot(s, lang) for s in slots]
        except Exception:
            pass
    link = cfg.get("scribe.booking_link", "") or cfg.get("calendly.scheduling_url", "")
    if "booking_link" in moves:
        if link:
            facts["booking_link"] = link
        else:
            moves.remove("booking_link")
    if intent == "doubt" or "answer_points" in moves:
        facts["questions"] = questions_in(incoming)
        if facts["questions"]:
            checks.append("Check the answers to their questions")
    if intent == "contract_changes":
        checks.append("Decide which of their changes you accept")
    if intent == "payment":
        checks.append("Check the amount / invoice before replying")
    # partner / lead context
    if sender_email:
        dom = sender_email.split("@")[-1].lower()
        row = db.one("SELECT name, status FROM leads WHERE lower(email)=lower(?) OR lower(website) LIKE ? LIMIT 1", (sender_email, f"%{dom}%")) \
            if dom and dom not in ("gmail.com", "outlook.com", "web.de", "gmx.de", "yahoo.com", "hotmail.com") else \
            db.one("SELECT name, status FROM leads WHERE lower(email)=lower(?) LIMIT 1", (sender_email,))
        if row:
            facts["relationship"] = f"{row['name']} (pipeline: {row['status']})"
    confidence = _confidence(cfg, db, intent, pol)
    register = register_for(intent, incoming) if kind == "reply" else "formal"
    lo, hi = REGISTER_WORDS[register]
    target = pol.get("median_words") or (lo + hi) // 2
    target = max(lo, min(hi, target)) if register == "quick" else target
    return {"intent": intent, "register": register, "intents": intents, "situation": INTENTS.get(intent, {}).get("label") or OWN_INTENTS.get(intent, intent),
            "lang": lang, "first_contact": first_contact, "moves": moves,
            "move_labels": [MOVES[m]["label"] for m in moves if m in MOVES], "policy_source": pol["source"],
            "target_words": target, "facts": facts, "checks": checks, "confidence": confidence,
            "sender_first_name": _first_name(sender_name), "notes": pol.get("notes", "")}


def reply_language(cfg: Config, incoming: str) -> str:
    """The founder answers in English, also to German emails, unless the other side writes 'du' (informal) or says
    they only speak German. scribe.reply_language = "mirror" answers German mail in German."""
    lang = detect_language(incoming) if incoming else "en"
    if lang != "de":
        return "en"
    mode = str(cfg.get("scribe.reply_language", "founder")).lower()
    if mode == "mirror":
        return "de"
    if re.search(r"\b(nur (auf )?deutsch|kein(e)? englisch|spreche(n)? (leider )?kein englisch|only (speak )?german)\b", incoming, re.I):
        return "de"
    if re.search(r"\b(du|dir|dich|dein\w*|euch)\b", incoming, re.I) and not re.search(r"\b(Sie|Ihnen|Ihr\w*)\b", incoming):
        return "de"
    return "en"


def choose_greeting(cfg: Config, lang: str, first_contact: bool, prev_greet: str, prev_line: str, sender_name: str,
                    incoming: str = "") -> str:
    """Greeting line the founder would use with this person: what they used before with them, else their habit for
    a reply to a named person, mirroring 'Hi' only when the other side wrote 'Hi' first."""
    first = _first_name(sender_name)
    if prev_line and prev_greet:   # keep what you already used with them
        return prev_line.strip() if len(prev_line) < 50 else prev_greet.replace("{name}", first or "").strip()
    prof = profile(cfg)
    st = prof["style"].get(lang, {})
    seed = (prof.get("seed") or {}).get("greetings", {}).get(lang, {})
    their = (incoming.strip().splitlines() or [""])[0].strip().lower()
    full = re.sub(r"[\"'<>()]", "", sender_name or "").strip()
    if re.search(r"\b(mr|ms|mrs|dr|prof|herr|frau)\.?\s", their) and len(full.split()) >= 2:
        # they write formally: stay formal (title + surname needs a gender we don't guess, so the full name)
        return f"Dear {full}," if lang == "en" else f"Guten Tag {full},"
    if first and their.startswith(("hi ", "hey ")) and seed.get("mirror_hi", True) and lang == "en":
        return f"Hi {first},"
    if first and their.startswith(("hallo ", "hi ")) and lang == "de":
        return f"Hallo {first},"
    tpl = ""
    if first:
        learned = st.get("greeting_by_situation", {}).get("reply:first_name") or st.get("greeting_by_situation", {}).get("own:first_name")
        tpl = (learned[0][0] if learned else "") or seed.get("first" if first_contact else "ongoing", "")
    else:
        learned = st.get("greeting_by_situation", {}).get("own:team") or st.get("greeting_by_situation", {}).get("reply:team")
        tpl = (learned[0][0] if learned else "") or seed.get("team", "")
    if not tpl:
        tpl = ("Dear {name}," if lang == "en" else "Sehr geehrte Damen und Herren,") if not first else \
              ("Dear {name}," if lang == "en" else "Hallo {name},")
    if "{name}" not in tpl and "{fullname}" not in tpl and first and not re.search(r"team|damen", tpl, re.I):
        tpl = tpl.rstrip(",") + " {name},"
    if "{fullname}" in tpl:
        return tpl.replace("{fullname}", full).strip() if full else ("Dear Sir or Madam," if lang == "en" else "Sehr geehrte Damen und Herren,")
    return tpl.replace("{name}", first or ("Team" if lang == "en" else "Team")).strip()


def _confidence(cfg: Config, db: DB, intent: str, pol: dict) -> float:
    acc = profile(cfg).get("acceptance", {}).get(intent)
    base = 0.85 if pol["source"].startswith("learned") else 0.7 if pol["source"].startswith("from the analysis") else 0.55
    if acc and acc.get("n", 0) >= 3:
        base = 0.5 * base + 0.5 * acc["mean_similarity"]
    return round(base, 2)


# -- writing ----------------------------------------------------------------------------------------------------

def style_rules(cfg: Config, lang: str) -> str:
    prof = profile(cfg)
    st = prof["style"].get(lang, {})
    seed = prof.get("seed") or {}
    rules = list(seed.get("rules", {}).get("all", [])) + list(seed.get("rules", {}).get(lang, []))
    if seed.get("phrases") and lang == "en":
        rules.append("Your own phrases (reuse where natural): " + "; ".join(seed["phrases"][:16]))
    if st:
        rules += [f"Typical length: about {st['median_words']} words; short paragraphs (often one sentence each).",
                  f"Greetings you use: {', '.join(g for g, _ in st['greetings'][:4])}.",
                  f"Openers you use: {' | '.join(st['openers'][:3])}." if st.get("openers") else "",
                  f"Sign-off: {st['sign_offs'][0][0]}" if st.get("sign_offs") else "",
                  "Bullet points are rare." if st.get("bullets_rate", 0) < 0.15 else "Use short bullet points for lists of steps.",
                  "Almost never use exclamation marks." if st.get("exclamation_rate", 0) < 0.1 else "",
                  f"Phrases you actually use (reuse where natural): {'; '.join(st['phrases'][:18])}." if st.get("phrases") else "",
                  f"Never use: {'; '.join(st['never'][:20])}." if st.get("never") else ""]
    if lang == "de":
        ds = st.get("du_vs_sie") or {}
        rules.append("Use 'Sie' unless the other person wrote 'du' first." if ds.get("Sie", 1) >= ds.get("du", 0) else "Use 'du' if they do.")
    if seed.get("never_phrases"):
        rules.append("Never use: " + "; ".join(seed["never_phrases"]))
    return "\n".join(f"- {r}" for r in rules if r)


def signature(cfg: Config, lang: str) -> tuple[str, str]:
    st = profile(cfg)["style"].get(lang) or profile(cfg)["style"].get("en") or {}
    seed = profile(cfg).get("seed") or {}
    so = (st.get("sign_offs") or [[seed.get("sign_off", {}).get(lang) or ("Kind regards," if lang == "en" else "Viele Grüße")]])[0][0]
    name = cfg.me.get("name", "")
    return so, name


def compose(cfg: Config, db: DB, incoming: str, pl: dict, *, subject: str = "", thread: str = "", llm=None,
            extra: str = "") -> str:
    """Write the reply. Claude when configured, else the local model, else the template writer."""
    from .knowledge import facts

    lang = pl["lang"]
    examples = similar_examples(cfg, db, incoming, pl["intent"], lang)
    ex_text = "\n\n".join(f"--- Example {i + 1} ({OWN_INTENTS.get(e['intent']) or INTENTS.get(e['intent'], {}).get('label', e['intent'])}) ---\n"
                          + (f"They wrote:\n{e['incoming'][:900]}\n\n" if e["incoming"] else "") + f"You replied:\n{e['reply'][:1200]}"
                          for i, e in enumerate(examples))
    so, name = signature(cfg, lang)
    f = pl["facts"]
    decision = "\n".join(f"- {MOVES[m]['label']}" for m in pl["moves"] if m in MOVES) or "- Reply briefly"
    prompt = f"""Draft the founder's reply. Write exactly as the founder writes (rules and examples below) and make exactly the decisions listed.

Situation: {pl['situation']} (language: {'German' if lang == 'de' else 'English'})
Register: {'QUICK: 2-4 short sentences, no opener line, straight to the point, like a reply from the phone' if pl.get('register') == 'quick' else 'FORMAL: polished, one idea per short paragraph, numbered/bold structure only if there are several points'}
Decision (what the founder does in this situation, {pl['policy_source']}):
{decision}
Start with this greeting line: {f.get('greeting')}
{('Time they proposed that is free in the calendar (accept it): ' + f['accept_time']) if f.get('accept_time') else ''}
{('Free slots to offer: ' + '; '.join(f['slots'])) if f.get('slots') and 'propose_day' in pl['moves'] else ''}
{('Booking link to share: ' + f['booking_link']) if f.get('booking_link') else ''}
{('Their questions to answer one by one: ' + ' | '.join(f['questions'])) if f.get('questions') else ''}
{('Relationship: ' + f['relationship']) if f.get('relationship') else ''}
{('Target length: about ' + str(pl['target_words']) + ' words.') if pl.get('target_words') else ''}
{pl.get('notes', '')}
{extra}

Style rules:
{style_rules(cfg, lang)}

The founder's own past replies in similar situations (match tone, structure and length; never copy names or facts):
{ex_text or '(none yet)'}

Rules:
- Only use facts from the thread, the facts above or the Settleezy facts. Never invent dates, prices, partners or promises.
- Where a decision only the founder can make is needed, write [[CHECK: ...]].
- End with "{so}" and the name "{name}" on the next line. Do not add the signature block (Outlook adds it).
- Output only the email body.

Settleezy facts:
{facts()[:3000]}

{('Thread (oldest first):' + chr(10) + thread[-6000:]) if thread else ('Their email:' + chr(10) + incoming[:4000])}"""
    system = (f"You are {cfg.me.get('name', 'the founder')}'s writing double: you draft emails that the founder could send without "
              f"editing, in their exact tone, structure and wording habits.")
    body = ""
    llm = llm
    if llm is None:
        from .llm import LLM

        llm = LLM(cfg)
    try:
        if getattr(llm, "cloud_enabled", False):
            body = llm.cloud(prompt, system, effort="medium", max_tokens=3000)
        elif cfg.get("scribe.local_model", True):
            body = llm.local(prompt, system, timeout=120)
    except Exception:
        body = ""
    if not body.strip():
        body = template_reply(cfg, pl)
    return check(cfg, body, pl)["text"]


TEMPLATES = {
    "en": {"thank_quick": "Thank you for your quick response.", "apologize": "Please accept my apologies for the delayed response.",
           "propose_day": "Would {slot} work for you? I am happy to work around your schedule.", "booking_link": "Please feel free to book a convenient time using the link below:\n{link}\n\nAlternatively, please let me know your preferred availability.",
           "offer_visit": "I would be happy to visit you in person, or we can go through it online, whichever suits you best.",
           "ask_options": "Could you advise a few options so I can mark them in my calendar?",
           "confirm": "{accept} works for me.", "answer_points": "Regarding your questions:\n{questions}",
           "clarify_question": "Could you please share a few more details so I can look into it?",
           "next_steps": "As a next step, could you please complete our Community Partner Application Form at your earliest convenience? Once we receive it, we will prepare the partnership agreement and send it to you for review and signature.",
           "attach": "Please find attached a short overview of Settleezy and our partnership model.",
           "defer": "Once I receive your comments, I will review them and send you the updated version.",
           "form_link": "", "decline_politely": "I fully understand and respect your decision.",
           "keep_door_open": "I would be glad to revisit this in the future, and I wish you continued success.",
           "empathy": "I'm sorry to hear that. Please don't worry about the delay.", "as_discussed": "As discussed, here is a short summary of what we agreed.",
           "extra_asks": "", "deadline": "", "pitch": "As a reminder, there are no listing fees and no commission.",
           "closing": "I look forward to hearing from you.",
           "opener": "I hope you are doing well."},
    "de": {"thank_quick": "Vielen Dank für Ihre schnelle Rückmeldung.", "apologize": "Bitte entschuldigen Sie meine verspätete Antwort.",
           "propose_day": "Würde Ihnen {slot} passen?", "booking_link": "Gerne können Sie auch über den folgenden Link einen passenden Termin wählen:\n{link}",
           "offer_visit": "Gerne komme ich auch persönlich vorbei, um alles gemeinsam zu besprechen.",
           "ask_options": "Könnten Sie mir ein paar Terminvorschläge nennen?", "confirm": "{accept} passt mir gut.",
           "answer_points": "Zu Ihren Fragen:\n{questions}", "clarify_question": "Könnten Sie mir bitte noch ein paar Details schicken?",
           "next_steps": "Als nächsten Schritt füllen Sie bitte unser Onboarding-Formular aus.",
           "attach": "Anbei finden Sie eine kurze Übersicht über Settleezy und unser Partnermodell.",
           "defer": "Sobald ich Ihre Anmerkungen erhalten habe, sende ich Ihnen die überarbeitete Version.",
           "form_link": "", "decline_politely": "Leider ist das derzeit nicht möglich.",
           "keep_door_open": "Ich würde mich freuen, in Zukunft in Kontakt zu bleiben.", "opener": "Ich hoffe, es geht Ihnen gut.",
           "empathy": "Das tut mir leid zu hören. Machen Sie sich wegen der Verzögerung bitte keine Gedanken.",
           "as_discussed": "Wie besprochen, hier eine kurze Zusammenfassung.", "extra_asks": "", "deadline": "",
           "pitch": "Zur Erinnerung: Es fallen keine Listungsgebühren und keine Provision an.", "closing": "Ich freue mich auf Ihre Rückmeldung."},
}


def template_reply(cfg: Config, pl: dict) -> str:
    """Offline writer: the founder's structure from the profile, filled with the decided moves."""
    lang = pl["lang"]
    T = TEMPLATES[lang]
    f = pl["facts"]
    lines = [f.get("greeting") or ("Hello," if lang == "en" else "Hallo,")]
    if pl.get("register") != "quick" and (pl.get("first_contact") or pl["intent"] in ("first_outreach", "follow_up", "recap")):
        lines.append(T["opener"])
    for m in pl["moves"]:
        t = T.get(m, "")
        if not t:
            continue
        if m == "propose_day":
            if not f.get("slots"):
                continue
            t = t.format(slot=f["slots"][0] + (f" or {f['slots'][1]}" if len(f["slots"]) > 1 and lang == "en" else
                                                   (f" oder {f['slots'][1]}" if len(f["slots"]) > 1 else "")))
        elif m == "booking_link":
            t = t.format(link=f.get("booking_link", ""))
        elif m == "confirm":
            t = t.format(accept=f.get("accept_time") or ("That" if lang == "en" else "Das"))
        elif m == "answer_points":
            qs = f.get("questions") or []
            if not qs:
                continue
            t = t.format(questions="\n".join(f"- {q} [[CHECK: answer]]" for q in qs))
        lines.append(t)
    if pl.get("register") != "quick" and T.get("closing"):
        lines.append(T["closing"])
    so, name = signature(cfg, lang)
    return "\n\n".join(lines) + f"\n\n{so}\n{name}"


BANNED_DEFAULT = ["—", "circle back", "touch base", "synergy", "Hope you're well!"]   # generic AI tells the founder never uses


def check(cfg: Config, text: str, pl: dict) -> dict[str, Any]:
    """Score a draft against the founder's style and fix what can be fixed mechanically."""
    lang = pl["lang"]
    st = profile(cfg)["style"].get(lang, {})
    issues, fixed = [], text.strip()
    want = (pl["facts"].get("greeting") or "").strip()
    first = (fixed.splitlines() or [""])[0].strip()
    if want and first != want and len(first) < 60 and greeting(fixed):
        fixed = want + fixed[len(first) + (fixed.find(first)):] if fixed.startswith(first) else want + "\n\n" + fixed
        issues.append("greeting adjusted")
    so, name = signature(cfg, lang)
    if so and so.rstrip(",").lower() not in fixed[-160:].lower():
        body, old_so, _ = split_signature(fixed)
        fixed = body.rstrip() + f"\n\n{so}\n{name}"
        issues.append("sign-off adjusted")
    for b in list(BANNED_DEFAULT) + list(st.get("never", [])) + list((profile(cfg).get("seed") or {}).get("never_phrases", [])):
        if b and b in fixed:
            fixed = fixed.replace(b, "-" if b == "—" else "")
            issues.append(f"removed '{b}'")
    if not cfg.get("scribe.keep_quirks", False):
        fixed = re.sub(r"\bThankyou\b", "Thank you", fixed)
    words = _words(split_signature(fixed)[0])
    target = pl.get("target_words") or st.get("median_words")
    score = 100
    if target and words > 2.2 * target:
        score -= 20
        issues.append(f"long: {words} words vs your usual {target}")
    if "[[CHECK" in fixed:
        score -= 5
    return {"text": fixed.strip(), "score": score - 3 * len([i for i in issues if i.startswith("removed")]), "issues": issues, "words": words}


# -- one call for the rest of Setz -----------------------------------------------------------------------------

def draft_reply(cfg: Config, db: DB, *, incoming: str, subject: str = "", sender_name: str = "", sender_email: str = "",
                conversation_id: str = "", thread: str = "", kind: str = "reply", llm=None, record: bool = True,
                followup_number: int = 0) -> dict[str, Any]:
    pl = plan(cfg, db, incoming, subject=subject, sender_name=sender_name, sender_email=sender_email,
              conversation_id=conversation_id, kind=kind, followup_number=followup_number)
    text = compose(cfg, db, incoming, pl, subject=subject, thread=thread, llm=llm)
    if record:
        _ensure(db)
        db.x("INSERT INTO scribe_drafts(conversation_id,intent,moves,lang,draft,created_at) VALUES(?,?,?,?,?,?)",
             (conversation_id, pl["intent"], json.dumps(pl["moves"]), pl["lang"], text, utcnow()))
    return {"plan": pl, "text": text}


# -- learning from the founder's edits --------------------------------------------------------------------------

def compare_sent(cfg: Config, db: DB) -> int:
    """For drafts whose thread now has a sent reply from the founder: record how much they changed."""
    _ensure(db)
    n = 0
    for d in db.q("SELECT * FROM scribe_drafts WHERE compared_at IS NULL AND conversation_id != ''"):
        sent = db.one("SELECT body_text FROM messages WHERE conversation_id=? AND folder='sent' AND sent_at > ? ORDER BY sent_at LIMIT 1",
                      (d["conversation_id"], d["created_at"]))
        if not sent:
            continue
        a, b = split_signature(d["draft"])[0], split_signature(sent["body_text"])[0]
        sim = difflib.SequenceMatcher(None, a, b).ratio()
        db.x("UPDATE scribe_drafts SET sent_text=?, similarity=?, compared_at=? WHERE id=?", (sent["body_text"], round(sim, 3), utcnow(), d["id"]))
        n += 1
    return n


def feedback_summary(db: DB) -> dict[str, Any]:
    _ensure(db)
    acc: dict[str, list[float]] = defaultdict(list)
    deleted: dict[str, Counter] = defaultdict(Counter)
    for d in db.q("SELECT intent, lang, draft, sent_text, similarity FROM scribe_drafts WHERE similarity IS NOT NULL"):
        acc[d["intent"]].append(d["similarity"])
        if d["similarity"] >= 0.3:   # same email, edited (not a full rewrite): what got removed?
            sm = difflib.SequenceMatcher(None, d["draft"], d["sent_text"] or "")
            for op, i1, i2, _, _ in sm.get_opcodes():
                if op in ("delete", "replace"):
                    frag = d["draft"][i1:i2].strip()
                    if 8 <= len(frag) <= 60 and not re.search(r"\d", frag):
                        deleted[d["lang"]][frag] += 1
    return {"acceptance": {k: {"n": len(v), "mean_similarity": round(statistics.mean(v), 2)} for k, v in acc.items()},
            "deleted_phrases": {lang: [f for f, n in c.items() if n >= 2] for lang, c in deleted.items()}}


def report(cfg: Config, db: DB) -> dict[str, Any]:
    """What Scribe knows (dashboard / agent)."""
    prof = profile(cfg)
    _ensure(db)
    stats = db.one("SELECT COUNT(*) n, AVG(similarity) s, SUM(similarity >= 0.8) kept FROM scribe_drafts WHERE similarity IS NOT NULL")
    decisions = []
    for intent in INTENT_ORDER + list(OWN_INTENTS):
        pol = policy(cfg, intent)
        d = prof["decisions"].get(intent, {})
        decisions.append({"intent": intent, "situation": INTENTS.get(intent, {}).get("label") or OWN_INTENTS.get(intent, intent),
                          "examples": d.get("n", 0), "moves": [{"key": m, "label": MOVES[m]["label"], "freq": pol["freq"].get(m)}
                                                                for m in pol["moves"] if m in MOVES],
                          "source": pol["source"], "median_words": pol.get("median_words"),
                          "median_hours": d.get("median_hours_to_reply")})
    return {"learned_at": prof.get("learned_at"), "emails": prof.get("emails", 0), "style": prof.get("style", {}),
            "seed": bool(prof.get("seed")), "decisions": decisions,
            "seed_style": {"rules": (prof.get("seed") or {}).get("rules", {}).get("all", []),
                           "phrases": (prof.get("seed") or {}).get("phrases", []),
                           "sign_off": (prof.get("seed") or {}).get("sign_off", {}),
                           "greetings": (prof.get("seed") or {}).get("greetings", {})} if prof.get("seed") else None,
            "drafts_compared": stats["n"] or 0, "avg_similarity": round(stats["s"] or 0, 2), "kept_mostly": stats["kept"] or 0}
