"""Setz, the hands-free voice assistant.

  wake phrase : "Hey Setz", spotted by Whisper on short bursts of speech (offline, no training needed);
                or a custom openWakeWord model if you train one (voice.wake_mode = "openwakeword")
  speech->text: faster-whisper on the RTX 3060 (offline, English + German)
  text->speech: ElevenLabs (your chosen voice), or Windows' built-in voices via pyttsx3 if no key is set
  hologram    : every state change (listening / thinking / speaking + loudness envelope) is sent to the
                dashboard, where the hologram at http://127.0.0.1:8765/hologram animates in sync
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
        sd.play(np.frombuffer(pcm, dtype=np.int16), self.tts.SAMPLE_RATE)
        sd.wait()
        publish(self.cfg, "idle")


class Ears:
    def __init__(self, cfg: Config):
        from faster_whisper import WhisperModel

        name = cfg.get("voice.whisper_model", "small")
        try:
            self.model = WhisperModel(name, device="cuda", compute_type="int8_float16")
        except Exception:  # no CUDA runtime -> CPU
            self.model = WhisperModel(name, device="cpu", compute_type="int8")
        self.threshold = float(cfg.get("voice.mic_threshold", 0.012))

    def record_until_silence(self, max_seconds: float = 15, silence_seconds: float = 1.2, wait_seconds: float | None = None):
        """Record one utterance. Returns (audio, heard_speech). With wait_seconds, gives up if nobody starts talking."""
        import numpy as np
        import sounddevice as sd

        frames, silent_for, started = [], 0.0, False
        with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32", blocksize=CHUNK) as stream:
            t0 = time.monotonic()
            while time.monotonic() - t0 < max_seconds:
                block, _ = stream.read(CHUNK)
                level = float(np.sqrt(np.mean(block ** 2)))
                if level > self.threshold:
                    started, silent_for = True, 0.0
                elif started:
                    silent_for += CHUNK / SAMPLE_RATE
                    if silent_for >= silence_seconds:
                        break
                elif wait_seconds is not None and time.monotonic() - t0 > wait_seconds:
                    break
                if started or len(frames) < 4:          # keep a little pre-roll
                    frames.append(block[:, 0].copy())
                else:
                    frames = frames[-3:] + [block[:, 0].copy()]
        audio = np.concatenate(frames) if frames else np.zeros(0, dtype="float32")
        return audio, started

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
    """Route a request (spoken or typed). Quick, reliable answers for the common things; the brain for the rest."""
    from . import brief as brief_mod
    from . import ops
    from .leads import top
    from .scraping.monitor import new_listings

    t = text.lower()
    with DB(cfg.db_path) as db:
        m = re.search(r"\b(?:remind me to|add (?:a )?(?:task|to-?do)(?: to)?|note to self|erinnere mich(?: daran)?,?)\s+(.+)", text, re.I)
        if m:
            tid = ops.add_task(db, m.group(1), source="voice")
            task = db.one("SELECT title, due FROM tasks WHERE id=?", (tid,)) if tid else None
            if not task:
                return "That's already on your list."
            return f"Added: {task['title']}" + (f", due {task['due']}." if task["due"] else ".")
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

        with Jarvis(config_path=cfg.get("voice.openjarvis_config") or None) as j:
            return j.ask(f"[{SETZ_PERSONA}]\n{text}{suffix}")
    except ImportError:
        pass
    from .knowledge import ONE_LINER
    from .leads import pipeline
    from .llm import LLM
    from .ops import partner_stats, reach_out, today_plan

    with DB(cfg.db_path) as db:
        latest = cfg.data_dir / "briefs" / f"{time.strftime('%Y-%m-%d')}.md"
        context = latest.read_text(encoding="utf-8") if latest.exists() else ""
        state = {
            "lead_pipeline": pipeline(db),
            "partners": partner_stats(cfg, db),
            "reach_out": [(r["who"], r["action"]) for r in reach_out(cfg, db, 8)],
            "plan": [(i["start"], i["title"]) for i in today_plan(cfg, db)["items"]],
        }
    return LLM(cfg).cloud(
        f"Today's brief:\n{context}\n\nLive state: {json.dumps(state, ensure_ascii=False, default=str)}\n\nQuestion: {text}\n"
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


def run(cfg: Config, wake_word: bool = True) -> None:
    import numpy as np
    import sounddevice as sd

    speaker = Speaker(cfg)
    ears = Ears(cfg)
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
            command, lang = ears.transcribe(audio) if heard else ("", lang)
        if not command:
            publish(cfg, "idle")
            continue
        print(f"> {command}")
        publish(cfg, "thinking", command)
        if re.search(r"\b(stop listening|goodbye|tschüss)\b", command.lower()):
            speaker.say("Bye." if lang == "en" else "Tschüss.", lang)
            publish(cfg, "idle")
            return
        try:
            reply = answer(cfg, command, lang)
        except Exception as exc:
            reply = f"Sorry, something failed: {exc}"
        print(reply)
        speaker.say(reply, lang)
