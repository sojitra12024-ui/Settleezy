"""Hands-free voice assistant.

  wake word  : openWakeWord's pre-trained "hey jarvis" model (offline)
  speech->text: faster-whisper on the RTX 3060 (offline, English + German)
  text->speech: Windows' built-in voices via pyttsx3 (offline)
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

SAMPLE_RATE = 16000
CHUNK = 1280  # 80 ms, what openWakeWord expects


class Speaker:
    def __init__(self, cfg: Config):
        import pyttsx3

        self.engine = pyttsx3.init()
        self.engine.setProperty("rate", int(cfg.get("voice.rate", 185)))
        self.voices = {"en": None, "de": None}
        for v in self.engine.getProperty("voices"):
            name = (v.name + " " + " ".join(map(str, getattr(v, "languages", [])))).lower()
            if "german" in name or "deutsch" in name or "de-de" in name or "hedda" in name or "katja" in name:
                self.voices["de"] = self.voices["de"] or v.id
            elif "english" in name or "en-" in name or "zira" in name or "david" in name:
                self.voices["en"] = self.voices["en"] or v.id

    def say(self, text: str, lang: str = "en") -> None:
        text = re.sub(r"[*_#`>\[\]]", "", text)
        if self.voices.get(lang):
            self.engine.setProperty("voice", self.voices[lang])
        self.engine.say(text)
        self.engine.runAndWait()


class Ears:
    def __init__(self, cfg: Config):
        from faster_whisper import WhisperModel

        try:
            self.model = WhisperModel(cfg.get("voice.whisper_model", "small"), device="cuda", compute_type="int8_float16")
        except Exception:  # no CUDA runtime -> CPU
            self.model = WhisperModel(cfg.get("voice.whisper_model", "small"), device="cpu", compute_type="int8")

    def record_until_silence(self, max_seconds: float = 15, silence_seconds: float = 1.2, threshold: float = 0.012):
        import numpy as np
        import sounddevice as sd

        frames, silent_for, started = [], 0.0, False
        with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32", blocksize=CHUNK) as stream:
            t0 = time.monotonic()
            while time.monotonic() - t0 < max_seconds:
                block, _ = stream.read(CHUNK)
                frames.append(block[:, 0].copy())
                level = float(np.sqrt(np.mean(block ** 2)))
                if level > threshold:
                    started, silent_for = True, 0.0
                elif started:
                    silent_for += CHUNK / SAMPLE_RATE
                    if silent_for >= silence_seconds:
                        break
        return np.concatenate(frames) if frames else np.zeros(0, dtype="float32")

    def transcribe(self, audio) -> tuple[str, str]:
        segments, info = self.model.transcribe(audio, beam_size=1, vad_filter=True)
        text = " ".join(s.text for s in segments).strip()
        return text, (info.language if info.language in ("en", "de") else "en")


def answer(cfg: Config, text: str, lang: str) -> str:
    """Route a spoken request."""
    from . import brief as brief_mod
    from .leads import top
    from .scraping.monitor import new_listings

    t = text.lower()
    with DB(cfg.db_path) as db:
        if re.search(r"\b(brief|my day|today|agenda|was steht|mein tag|heute)\b", t):
            _, speech = brief_mod.build(cfg, db, with_actions=True)
            return speech
        if re.search(r"\b(listing|competitor|groupon|vspots|top ?10|konkurren)", t):
            items = new_listings(db, 48)
            if not items:
                return "No new competitor listings in the last two days."
            names = ", ".join((i["merchant"] or i["title"]) for i in items[:6])
            return f"{len(items)} new listings in the last two days, including {names}."
        if re.search(r"\b(lead|leads|partner)\b", t):
            best = top(db, status="new", limit=5)
            if not best:
                return "No new leads right now."
            return "Your best new leads are: " + ", ".join(f"{l['name']}, a {l['kind']}" for l in best) + "."
    return ask_brain(cfg, text, lang)


def ask_brain(cfg: Config, text: str, lang: str) -> str:
    try:
        from openjarvis import Jarvis  # type: ignore

        with Jarvis(config_path=cfg.get("voice.openjarvis_config") or None) as j:
            return j.ask(text + ("\n(Antworte kurz auf Deutsch.)" if lang == "de" else "\n(Answer briefly; it will be spoken.)"))
    except ImportError:
        pass
    from .knowledge import ONE_LINER
    from .leads import pipeline
    from .llm import LLM

    with DB(cfg.db_path) as db:
        latest = cfg.data_dir / "briefs" / f"{time.strftime('%Y-%m-%d')}.md"
        context = latest.read_text(encoding="utf-8") if latest.exists() else ""
        pipe = json.dumps(pipeline(db))
    return LLM(cfg).cloud(
        f"Today's brief:\n{context}\n\nLead pipeline: {pipe}\n\nSpoken question: {text}\n"
        f"Answer in {'German' if lang == 'de' else 'English'} in at most 3 short sentences; it will be read aloud.",
        "You are Settleezy's co-founder and the founder's personal assistant. " + ONE_LINER,
        effort="low",
        max_tokens=1500,
    )


def run(cfg: Config, wake_word: bool = True) -> None:
    import numpy as np
    import sounddevice as sd

    speaker = Speaker(cfg)
    ears = Ears(cfg)
    detector = None
    if wake_word:
        import openwakeword
        from openwakeword.model import Model

        openwakeword.utils.download_models(["hey_jarvis"])
        detector = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
    threshold = float(cfg.get("voice.wake_threshold", 0.5))
    speaker.say("Jarvis is listening." if wake_word else "Press enter and speak.")
    while True:
        if detector is not None:
            with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=CHUNK) as stream:
                while True:
                    block, _ = stream.read(CHUNK)
                    scores = detector.predict(np.frombuffer(block, dtype=np.int16))
                    if max(scores.values()) >= threshold:
                        detector.reset()
                        break
        else:
            input("[enter] to talk, ctrl+c to quit ")
        speaker.say("Yes?")
        text, lang = ears.transcribe(ears.record_until_silence())
        if not text:
            continue
        print(f"> {text}")
        if re.search(r"\b(stop listening|goodbye|tschüss)\b", text.lower()):
            speaker.say("Bye." if lang == "en" else "Tschüss.", lang)
            return
        try:
            reply = answer(cfg, text, lang)
        except Exception as exc:
            reply = f"Sorry, something failed: {exc}"
        print(reply)
        speaker.say(reply, lang)
