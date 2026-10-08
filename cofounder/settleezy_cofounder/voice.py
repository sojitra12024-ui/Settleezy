"""Setz, the hands-free voice assistant.

  wake phrase : "Hey Setz", spotted by Whisper on short bursts of speech (offline, no training needed);
                or a custom openWakeWord model if you train one (voice.wake_mode = "openwakeword")
  speech->text: faster-whisper on the RTX 3060 (offline, English + German)
  text->speech: ElevenLabs (your chosen voice), or Windows' built-in voices via pyttsx3 if no key is set
  hologram    : every state change (listening / thinking / speaking + loudness envelope) is sent to the
                dashboard, where the hologram at http://127.0.0.1:8765/hologram animates in sync
  barge-in    : with voice.barge_in = true (use a headset), start talking while Setz speaks and it stops to listen
  speech coach: every command is measured quietly (pace, fillers, pitch...); "Hey Setz, practise my pitch" records a
                pitch and gives a delivery score plus AI coaching (see speech.py, `sz voice practice`)
  brain       : quick intents handled here (brief, new listings, leads); anything else goes to OpenJarvis
                (which can call this project's tools via MCP), or to Claude if OpenJarvis isn't installed.

Install: pip install -e ".[voice]"     Run: sz voice
"""

from __future__ import annotations

import json
import re
import time

from .config import Config
from .db import DB
from .voicebus import publish

SAMPLE_RATE = 16000
CHUNK = 1280  # 80 ms, what openWakeWord expects


def clean_for_speech(text: str) -> str:
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)      # markdown links -> label
    text = re.sub(r"https?://\S+", "", text)
    return re.sub(r"[*_#`>\[\]|]", "", text).strip()


class Speaker:
    """Speaks with ElevenLabs when configured, else Windows voices. Always drives the hologram."""

    def __init__(self, cfg: Config):
        from . import tts

        self.cfg = cfg
        self.tts = tts
        self.eleven = tts.elevenlabs_enabled(cfg)
        self.engine = None
        self.voices = {"en": None, "de": None}
        self.barge_in = bool(cfg.get("voice.barge_in", False))
        self.barge_threshold = float(cfg.get("voice.mic_threshold", 0.012)) * float(cfg.get("voice.barge_factor", 3.0))
        self.interrupted = False
        self.vad = None   # set by run() when barge-in is on, so only real speech interrupts
        if not self.eleven:
            self._init_local()

    def _init_local(self) -> None:
        import pyttsx3

        self.engine = pyttsx3.init()
        self.engine.setProperty("rate", int(self.cfg.get("voice.rate", 185)))
        for v in self.engine.getProperty("voices"):
            name = (v.name + " " + " ".join(map(str, getattr(v, "languages", [])))).lower()
            if "german" in name or "deutsch" in name or "de-de" in name or "hedda" in name or "katja" in name:
                self.voices["de"] = self.voices["de"] or v.id
            elif "english" in name or "en-" in name or "zira" in name or "david" in name:
                self.voices["en"] = self.voices["en"] or v.id

    def say(self, text: str, lang: str = "en") -> None:
        text = clean_for_speech(text)
        if not text:
            return
        if self.eleven:
            try:
                self._say_elevenlabs(text)
                return
            except Exception as exc:  # network/quota problems -> fall back, keep talking
                print(f"(ElevenLabs unavailable: {exc}; using Windows voice)")
                self.eleven = False
                self._init_local()
        publish(self.cfg, "speaking", text)
        if self.voices.get(lang):
            self.engine.setProperty("voice", self.voices[lang])
        self.engine.say(text)
        self.engine.runAndWait()
        publish(self.cfg, "idle")

    def _say_elevenlabs(self, text: str) -> None:
        import numpy as np
        import sounddevice as sd

        pcm = self.tts.synthesize(self.cfg, text)
        env = self.tts.envelope(pcm)
        publish(self.cfg, "speaking", text, env, 40)
        samples = np.frombuffer(pcm, dtype=np.int16)
        sd.play(samples, self.tts.SAMPLE_RATE)
        self.interrupted = False
        if self.barge_in:
            self._watch_for_barge_in(len(samples) / self.tts.SAMPLE_RATE)
        else:
            sd.wait()
        publish(self.cfg, "idle")

    def _watch_for_barge_in(self, seconds: float) -> None:
        """While Setz talks, listen: ~250 ms of the founder's voice above the barge-in level stops playback."""
        import numpy as np
        import sounddevice as sd

        loud = 0
        t0 = time.monotonic()
        vad = self.vad
        if vad is not None:
            vad.reset()
        with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32", blocksize=CHUNK // 2) as mic:
            while time.monotonic() - t0 < seconds + 0.2:
                block, _ = mic.read(CHUNK // 2)
                is_loud = float(np.sqrt(np.mean(block ** 2))) > self.barge_threshold
                # loud AND speech-like: a door, a cough or a clap doesn't interrupt Setz
                is_speech = vad is None or vad(block[:, 0]) >= max(0.6, vad.threshold)
                loud = loud + 1 if is_loud and is_speech else 0
                if loud >= 6 and time.monotonic() - t0 > 0.4:   # ignore the first moment (speaker start-up click)
                    sd.stop()
                    self.interrupted = True
                    return
        sd.wait()


class Ears:
    def __init__(self, cfg: Config):
        from faster_whisper import WhisperModel

        name = cfg.get("voice.whisper_model", "small")
        try:
            self.model = WhisperModel(name, device="cuda", compute_type="int8_float16")
        except Exception:  # no CUDA runtime -> CPU
            self.model = WhisperModel(name, device="cpu", compute_type="int8")
        self.threshold = float(cfg.get("voice.mic_threshold", 0.012))
        from .vad import make_vad

        self.vad = make_vad(cfg)

    def record_until_silence(self, max_seconds: float = 15, silence_seconds: float = 1.2, wait_seconds: float | None = None):
        """Record one utterance. Returns (audio, heard_speech). With wait_seconds, gives up if nobody starts talking.
        Speech is detected by Silero VAD (noise, typing and fans don't count), or by loudness as a fallback."""
        import numpy as np
        import sounddevice as sd

        from .vad import collect_utterance

        self.vad.reset()
        with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32", blocksize=CHUNK) as stream:
            def blocks():
                while True:
                    yield stream.read(CHUNK)[0][:, 0].copy()

            frames, started = collect_utterance(blocks(), self.vad, block_seconds=CHUNK / SAMPLE_RATE,
                                                threshold=self.vad.threshold, silence_seconds=silence_seconds,
                                                max_seconds=max_seconds, wait_seconds=wait_seconds)
        audio = np.concatenate(frames) if frames else np.zeros(0, dtype="float32")
        return audio, started

    def calibrate(self, seconds: float = 1.5) -> float:
        """Measure the room's background noise and set the speech threshold above it."""
        import numpy as np
        import sounddevice as sd

        audio = sd.rec(int(seconds * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1, dtype="float32")
        sd.wait()
        noise = float(np.sqrt(np.mean(audio ** 2)))
        self.threshold = max(self.threshold, noise * 3.5)
        if self.vad.name == "energy":
            self.vad.level = self.threshold
        return noise

    def transcribe_words(self, audio, prompt: str = "") -> tuple[str, str, list[dict]]:
        """Like transcribe(), plus word timestamps and confidence for the speech analyser."""
        from .speech import words_from_segments

        segments, info = self.model.transcribe(audio, beam_size=1, vad_filter=True, word_timestamps=True,
                                               initial_prompt=prompt or None)
        segments = list(segments)
        text = " ".join(s.text for s in segments).strip()
        return text, (info.language if info.language in ("en", "de") else "en"), words_from_segments(segments)

    def transcribe(self, audio, prompt: str = "") -> tuple[str, str]:
        segments, info = self.model.transcribe(audio, beam_size=1, vad_filter=True, initial_prompt=prompt or None)
        text = " ".join(s.text for s in segments).strip()
        return text, (info.language if info.language in ("en", "de") else "en")


WAKE = re.compile(r"(?:^\W*|\b(?:hey|hi|hallo|ok|okay)[\s,]+)(?:setz|sets|setze|sätz|zets|seds|sats|setzi)\b[\s,.!?]*", re.I)


def split_wake(text: str, required: bool = True) -> tuple[bool, str]:
    """('Hey Setz, what's my day?') -> (True, "what's my day?")."""
    m = WAKE.search(text)
    if not m:
        return (not required), text.strip()
    return True, (text[: m.start()] + text[m.end():]).strip(" ,.!?")


def answer(cfg: Config, text: str, lang: str) -> str:
    """Route a request (spoken or typed) and keep the exchange in Setz's memory."""
    reply = _answer(cfg, text, lang)
    if len(text.split()) >= 3:
        try:
            from .brain import remember

            with DB(cfg.db_path) as db:
                remember(cfg, db, f"Founder asked: {text[:200]} | Setz: {reply[:300]}", kind="conversation",
                         source="voice", importance=0.2)
        except Exception:
            pass   # memory must never break answering
    return reply


_REMEMBER = re.compile(r"^\s*(?:please\s+)?(?:remember|keep in mind|note)(?: that)?[:,]?\s+(.+)|^\s*merk dir(?:,? dass)?[:,]?\s+(.+)", re.I)
_KNOW = re.compile(r"\b(?:what do you know about|what do we know about|tell me about|was weißt du über)\s+(.+?)[?.!]*$", re.I)


def _subject_in(db: DB, text: str) -> str:
    low = text.casefold()
    best = ""
    for r in db.q("SELECT name FROM partners UNION SELECT name FROM leads"):
        n = r["name"]
        if len(n) > len(best) and len(n) >= 4 and n.casefold() in low:
            best = n
    return best


def _answer(cfg: Config, text: str, lang: str) -> str:
    """Quick, reliable answers for the common things; the brain for the rest."""
    from . import brain
    from . import brief as brief_mod
    from . import ops
    from .leads import top
    from .scraping.monitor import new_listings

    t = text.lower()
    with DB(cfg.db_path) as db:
        m = _REMEMBER.match(text)
        if m:
            fact = (m.group(1) or m.group(2)).strip(" .")
            brain.remember(cfg, db, fact, kind="preference" if re.search(r"\bprefers?|likes?|mag\b", fact, re.I) else "fact",
                           subject=_subject_in(db, fact), source="voice", importance=0.85)
            return "Got it, I'll remember that." if lang != "de" else "Alles klar, ich merke es mir."
        m = _KNOW.search(text)
        if m:
            mems = brain.recall(cfg, db, m.group(1), 4)
            if not mems:
                return f"I don't know anything about {m.group(1)} yet."
            return " ".join(x["text"].rstrip(".") + "." for x in mems if x["kind"] != "conversation")[:700] or mems[0]["text"]
        m = re.search(r"\b(?:remind me to|add (?:a )?(?:task|to-?do)(?: to)?|note to self|erinnere mich(?: daran)?,?)\s+(.+)", text, re.I)
        if m:
            tid = ops.add_task(db, m.group(1), source="voice")
            task = db.one("SELECT title, due FROM tasks WHERE id=?", (tid,)) if tid else None
            if not task:
                return "That's already on your list."
            return f"Added: {task['title']}" + (f", due {task['due']}." if task["due"] else ".")
        if re.search(r"\b(how(?:'s| is| was) my (?:speaking|voice|delivery|pitch)|speaking (?:stats|score)|wie spreche ich)\b", t):
            from .speech import trend

            tr = trend(db, 30)
            if not tr.get("sessions"):
                return "No practice sessions yet. Say: Hey Setz, practise my pitch."
            n, b = tr["now"], tr.get("before")
            msg = (f"Over {tr['sessions']} sessions your delivery score is {n['score']:g}, at {n['wpm'] or 0:g} words per minute "
                   f"with {n['fillers_per_min'] or 0:g} filler words a minute.")
            if b and b.get("score") is not None:
                msg += f" That's {'up' if n['score'] >= b['score'] else 'down'} from {b['score']:g}."
            return msg
        if re.search(r"\b(plan my week|my week|week plan|this week's plan|next week|meine woche|wochenplan)\b", t):
            from datetime import date, timedelta

            from .planner import plan_week, spoken_week, week_start_for

            start = week_start_for(date.today()) + timedelta(days=7) if re.search(r"next week|nächste woche", t) else None
            return spoken_week(plan_week(cfg, db, start))
        if re.search(r"\b(pipeline|deals?|forecast|conversion|sales)\b", t):
            from . import pipeline as pl

            c, f = pl.summary(cfg, db)["counts"], pl.forecast(cfg, db)
            behind = [x for x in pl.targets(cfg, db) if not x["on_track"]]
            stale = pl.stale(db)
            msg = (f"{c['contacted'] + c['replied'] + c['meeting']} active deals: {c['contacted']} contacted, {c['replied']} replied, "
                   f"{c['meeting']} in meetings. {f['won_this_month']} partners signed this month of {f['goal_month']:g}, "
                   f"and about {f['expected_from_pipeline']:g} more expected from the pipeline.")
            if behind:
                msg += f" You're behind on {behind[0]['stage']}: {behind[0]['actual_week']} of {behind[0]['target_week']:g} this week."
            if stale:
                msg += f" Most urgent: {stale[0]['name']}, {stale[0]['suggestion']}."
            return msg
        if re.search(r"\b(to-?dos?|tasks?|aufgaben)\b", t):
            items = ops.tasks(db, "today")
            if not items:
                return "No to-dos due today."
            return f"{len(items)} to-dos today: " + "; ".join(x["title"] for x in items[:5]) + "."
        if re.search(r"\b(what should i do|focus|right now|what's next|whats next|was jetzt|als nächstes)\b", t):
            return ops.focus_now(cfg, db)
        if re.search(r"\b(who should i (contact|call|email|reach)|reach out|wen soll ich)\b", t):
            items = ops.reach_out(cfg, db, 5)
            if not items:
                return "Nobody urgent right now."
            return "Reach out to: " + "; ".join(f"{i['who']}, {i['action']}" for i in items) + "."
        if re.search(r"\b(prep|prepare|vorbereit)", t):
            nxt = db.one("SELECT id FROM events WHERE start >= ? ORDER BY start LIMIT 1", (time.strftime("%Y-%m-%dT%H:%M"),))
            if not nxt:
                return "You have no upcoming meetings."
            prep = ops.meeting_prep(cfg, db, nxt["id"])
            return clean_for_speech(" ".join(ln for ln in prep.splitlines() if ln and not ln.startswith("#"))[:700])
        if re.search(r"\b(service partners?|partners?|onboard|partner)\b", t) and not re.search(r"\blead", t):
            ps = ops.partner_stats(cfg, db)
            msg = (f"{ps['live']} partners are live, {ps['onboarding']} are onboarding, and {ps['onboarded_this_month']} "
                   f"went live this month.")
            return msg + (f" {ps['at_risk']} need attention." if ps["at_risk"] else "")
        if re.search(r"\b(scorecard|this week|diese woche)\b", t):
            rows = ops.scorecard(cfg, db)
            return "This week: " + "; ".join(f"{r['metric']} {r['this_week']} ({r['delta']:+d})" for r in rows) + "."
        if re.search(r"\b(brief|my day|today|agenda|was steht|mein tag|heute)\b", t):
            _, speech = brief_mod.build(cfg, db, with_actions=True)
            return speech
        if re.search(r"\b(listing|competitor|groupon|vspots|top ?10|konkurren)", t):
            items = new_listings(db, 48)
            if not items:
                return "No new competitor listings in the last two days."
            names = ", ".join((i["merchant"] or i["title"]) for i in items[:6])
            return f"{len(items)} new listings in the last two days, including {names}."
        if re.search(r"\b(connect|connected|connection|connections|verbunden|verbindung)", t):
            from .connections import run_all

            bad = [r for r in run_all(cfg, db) if not r["ok"] and not r["optional"]]
            if not bad:
                return "Everything is connected: Outlook, Instagram and the rest are working."
            return "These need attention: " + "; ".join(f"{r['name']}: {r['detail']}" for r in bad) + "."
        if re.search(r"\b(lead|leads)\b", t):
            best = top(db, status="new", limit=5)
            if not best:
                return "No new leads right now."
            return "Your best new leads are: " + ", ".join(f"{l['name']}, a {l['kind']}" for l in best) + "."
    return ask_brain(cfg, text, lang)


SETZ_PERSONA = ("You are Setz, the AI chief of staff and growth co-founder of Settleezy. You are warm, sharp and brief. "
                "You know the founder's partners, leads, inbox, calendar and goals, and you always suggest the next concrete step.")


def ask_brain(cfg: Config, text: str, lang: str) -> str:
    suffix = "\n(Antworte kurz auf Deutsch.)" if lang == "de" else "\n(Answer briefly; it will be spoken.)"
    try:
        from openjarvis import Jarvis  # type: ignore  # OpenJarvis is the framework Setz runs on

        try:
            from .brain import context_for

            with DB(cfg.db_path) as db:
                mem = context_for(cfg, db, text, 6)
        except Exception:
            mem = ""
        with Jarvis(config_path=cfg.get("voice.openjarvis_config") or None) as j:
            return j.ask(f"[{SETZ_PERSONA}]\n" + (f"[What you remember:\n{mem}]\n" if mem else "") + f"{text}{suffix}")
    except ImportError:
        pass
    from .knowledge import ONE_LINER
    from .leads import pipeline
    from .llm import LLM
    from .ops import partner_stats, reach_out, today_plan

    with DB(cfg.db_path) as db:
        try:
            from .brain import context_for

            memories = context_for(cfg, db, text, 8)
        except Exception:
            memories = ""
        latest = cfg.data_dir / "briefs" / f"{time.strftime('%Y-%m-%d')}.md"
        context = latest.read_text(encoding="utf-8") if latest.exists() else ""
        state = {
            "lead_pipeline": pipeline(db),
            "partners": partner_stats(cfg, db),
            "reach_out": [(r["who"], r["action"]) for r in reach_out(cfg, db, 8)],
            "plan": [(i["start"], i["title"]) for i in today_plan(cfg, db)["items"]],
        }
    return LLM(cfg).cloud(
        f"Today's brief:\n{context}\n\nWhat you remember that may be relevant:\n{memories or '- nothing yet'}\n\nLive state: {json.dumps(state, ensure_ascii=False, default=str)}\n\nQuestion: {text}\n"
        f"Answer in {'German' if lang == 'de' else 'English'} in at most 3 short sentences; it will be read aloud.",
        SETZ_PERSONA + " " + ONE_LINER,
        effort="low",
        max_tokens=1500,
    )


def due_announcement(cfg: Config, announced: set[str], now: float | None = None) -> str | None:
    """When a routine block starts, Setz says what it is and who/what to start with (once per block per day)."""
    if not cfg.get("routine.announce", True):
        return None
    from .ops import today_plan

    lt = time.localtime(now or time.time())
    hm = f"{lt.tm_hour:02d}:{lt.tm_min:02d}"
    with DB(cfg.db_path) as db:
        plan = today_plan(cfg, db)
    for item in plan["items"]:
        key = f"{plan['date']}:{item['start']}:{item['title']}"
        if item["start"] == hm and key not in announced:
            announced.add(key)
            if item["type"] == "meeting":
                return f"It's {hm}. Your meeting '{item['title']}' is starting."
            tip = f" Start with {item['suggestions'][0]}." if item.get("suggestions") else ""
            return f"It's {hm}. Time for {item['title']}.{tip}"
    return None


PRACTICE = re.compile(r"\b(practi[cs]e|rehearse|coach me|analy[sz]e my (voice|speaking|pitch)|üben|probe)\b", re.I)


def practice(cfg: Config, speaker: "Speaker | None" = None, ears: "Ears | None" = None, max_seconds: float = 180,
             topic: str = "pitch", lang_hint: str = "en", use_ai: bool = True) -> dict:
    """Record a pitch (stops after 3 s of silence), analyse delivery, coach the content, save and speak the result."""
    from . import speech

    ears = ears or Ears(cfg)
    if speaker:
        speaker.say("Go ahead, I'm listening. Stop for three seconds when you're done." if lang_hint != "de"
                    else "Leg los, ich höre zu. Mach drei Sekunden Pause, wenn du fertig bist.", lang_hint)
    publish(cfg, "listening")
    audio, heard = ears.record_until_silence(max_seconds=max_seconds, silence_seconds=3.0, wait_seconds=10)
    if not heard:
        publish(cfg, "idle")
        return {"error": "I didn't hear anything."}
    publish(cfg, "thinking", "Analysing your pitch…")
    text, lang, words = ears.transcribe_words(audio)
    m = speech.analyse(audio, text=text, words=words, lang=lang)
    tip = ""
    if use_ai and text:
        try:
            tip = speech.coach(cfg, m, topic)
        except Exception as exc:  # delivery numbers are still useful without the AI part
            tip = f"(AI coaching unavailable: {exc})"
    with DB(cfg.db_path) as db:
        m["session_id"] = speech.save(db, m, "practice", tip)
    m["coaching"] = tip
    if speaker:
        speaker.say(speech.spoken_summary(m), lang)
    publish(cfg, "idle")
    return m


def _measure_command(cfg: Config, audio, words: list[dict], text: str, lang: str) -> None:
    """Quietly keep delivery stats for normal commands (cheap: numpy only)."""
    try:
        from . import speech

        m = speech.analyse(audio, text=text, words=words, lang=lang)
        with DB(cfg.db_path) as db:
            speech.save(db, m, "command")
    except Exception:
        pass


def run(cfg: Config, wake_word: bool = True) -> None:
    import numpy as np
    import sounddevice as sd

    speaker = Speaker(cfg)
    ears = Ears(cfg)
    if speaker.barge_in:
        from .vad import make_vad

        speaker.vad = make_vad(cfg)   # its own instance: separate stream state
    print(f"(speech detection: {ears.vad.name})")
    mode = cfg.get("voice.wake_mode", "phrase") if wake_word else "push"
    detector = None
    if mode == "openwakeword":
        import openwakeword
        from openwakeword.model import Model

        model = cfg.get("voice.wake_model", "")
        if not model:
            raise RuntimeError("Set voice.wake_model to your trained 'hey setz' .onnx file, or use wake_mode = \"phrase\".")
        detector = Model(wakeword_models=[model], inference_framework="onnx")
        threshold = float(cfg.get("voice.wake_threshold", 0.5))
    announced: set[str] = set()
    if cfg.get("voice.calibrate", True):
        noise = ears.calibrate()
        speaker.barge_threshold = max(speaker.barge_threshold, ears.threshold * float(cfg.get("voice.barge_factor", 3.0)))
        print(f"(room noise {noise:.4f}, speech threshold {ears.threshold:.4f})")
    publish(cfg, "idle")
    speaker.say({"push": "Setz here. Press enter and speak."}.get(mode, "Setz is listening. Say: Hey Setz."))
    while True:
        command = ""
        lang = "en"
        if mode == "push":
            input("[enter] to talk, ctrl+c to quit ")
        elif mode == "openwakeword":
            with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=CHUNK) as stream:
                while True:
                    block, _ = stream.read(CHUNK)
                    if max(detector.predict(np.frombuffer(block, dtype=np.int16)).values()) >= threshold:
                        detector.reset()
                        break
        else:  # phrase: listen in short bursts, wake on "Hey Setz", announce routine blocks in between
            while True:
                msg = due_announcement(cfg, announced)
                if msg:
                    speaker.say(msg)
                audio, heard = ears.record_until_silence(max_seconds=7, silence_seconds=0.8, wait_seconds=20)
                if not heard:
                    continue
                text, lang = ears.transcribe(audio, prompt="Hey Setz")
                woke, rest = split_wake(text)
                if woke:
                    command = rest
                    break
        if not command:
            publish(cfg, "listening")
            speaker.say("Yes?" if lang == "en" else "Ja?", lang)
            publish(cfg, "listening")
            audio, heard = ears.record_until_silence(wait_seconds=6)
            if heard:
                command, lang, words = ears.transcribe_words(audio)
                _measure_command(cfg, audio, words, command, lang)
        if not command:
            publish(cfg, "idle")
            continue
        print(f"> {command}")
        publish(cfg, "thinking", command)
        if re.search(r"\b(stop listening|goodbye|tschüss)\b", command.lower()):
            speaker.say("Bye." if lang == "en" else "Tschüss.", lang)
            publish(cfg, "idle")
            return
        if PRACTICE.search(command):
            m = practice(cfg, speaker, ears, lang_hint=lang,
                         topic="university pitch" if re.search(r"uni|buddy", command, re.I) else "venue partnership pitch")
            if m.get("coaching"):
                print(m["coaching"])
            continue
        try:
            reply = answer(cfg, command, lang)
        except Exception as exc:
            reply = f"Sorry, something failed: {exc}"
        print(reply)
        speaker.say(reply, lang)
