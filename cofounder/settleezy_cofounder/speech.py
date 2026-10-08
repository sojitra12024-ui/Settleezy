"""Speech analyser: how you speak, not just what you say.

From the audio (16 kHz mono float) and Whisper's word timestamps it measures:

  pace        words per minute overall and while actually speaking (articulation rate)
  pauses      count, long pauses (>= 1 s), average pause
  fillers     ähm / äh / um / uh / like / you know / halt / quasi / sozusagen ... per minute (English + German)
  hedging     "I think", "maybe", "just", "sorry", "vielleicht", "ich glaube" ... (sounds less sure)
  pitch       median, range and variation in semitones (monotone below ~2 st), by autocorrelation
  loudness    level in dBFS, dynamic range, clipping, signal-to-noise
  clarity     Whisper's own word confidence
  tone        positive / negative wording, questions, energy (calm / engaged / energetic / flat)

and turns that into a 0-100 delivery score with concrete tips. Used live by the voice assistant (every command is
measured quietly), by `sz voice practice` / the dashboard's "Practise a pitch" button (with optional AI coaching on
the content) and by `sz voice analyse call.wav` for recorded calls. Only numpy is needed.
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable

from .db import DB, utcnow

SR = 16000

FILLERS = {
    "en": [r"\bu+h+m*\b", r"\bu+m+\b", r"\be+r+m*\b", r"\bah+\b", r"\bhmm+\b", r"\byou know\b", r"\bi mean\b", r"\bbasically\b",
           r"\bactually\b", r"\bliterally\b", r"\bsort of\b", r"\bkind of\b", r"\bso yeah\b", r"(?:^|[,.] )like,"],
    "de": [r"\bä+h+m*\b", r"\bö+h+m*\b", r"\bhm+\b", r"\bhalt\b", r"\bquasi\b", r"\bsozusagen\b", r"\birgendwie\b",
           r"\bgewissermaßen\b", r"\beigentlich\b", r"\bja also\b", r"(?:^|[,.] )also,", r"\bne\?"],
}
HEDGES = {
    "en": [r"\bi think\b", r"\bi guess\b", r"\bmaybe\b", r"\bperhaps\b", r"\bprobably\b", r"\bjust\b", r"\bsorry\b",
           r"\bi'm not sure\b", r"\bkind of\b", r"\bsort of\b", r"\bhopefully\b", r"\bi feel like\b"],
    "de": [r"\bich glaube\b", r"\bich denke\b", r"\bvielleicht\b", r"\bwahrscheinlich\b", r"\bnur\b", r"\bsorry\b",
           r"\bentschuldigung\b", r"\beventuell\b", r"\bich weiß nicht\b", r"\bkönnte\b"],
}
POSITIVE = r"\b(great|good|happy|excited|love|perfect|thanks|thank you|awesome|win|save|benefit|easy|super|toll|gut|gerne|danke|freue|perfekt|klasse|spannend|vorteil|sparen|einfach)\b"
NEGATIVE = r"\b(problem|issue|bad|sorry|worried|difficult|unfortunately|can't|cannot|no time|expensive|leider|schwierig|problem|teuer|keine zeit|schlecht|nicht möglich)\b"
# comfortable conversational ranges (words per minute)
PACE = {"en": (130, 170), "de": (110, 150)}


# -- signal processing -------------------------------------------------------------------

def _frames(x, size: int, hop: int):
    import numpy as np

    if len(x) < size:
        return np.zeros((0, size), dtype=np.float32)
    n = 1 + (len(x) - size) // hop
    idx = np.arange(size)[None, :] + hop * np.arange(n)[:, None]
    return x[idx]


def pitch_track(x, sr: int = SR, fmin: float = 70, fmax: float = 400, voiced_gate=None) -> list[float]:
    """F0 per 20 ms frame (Hz) for voiced frames, by normalised autocorrelation with parabolic interpolation."""
    import numpy as np

    size, hop = 1024, sr // 50
    fr = _frames(np.asarray(x, dtype=np.float32), size, hop)
    if not len(fr):
        return []
    rms = np.sqrt((fr ** 2).mean(axis=1))
    gate = voiced_gate if voiced_gate is not None else max(1e-4, float(np.percentile(rms, 60)) * 0.5)
    lo, hi = int(sr / fmax), int(sr / fmin)
    win = np.hanning(size).astype(np.float32)
    out = []
    for f, e in zip(fr, rms):
        if e < gate:
            continue
        f = (f - f.mean()) * win
        spec = np.fft.rfft(f, 2 * size)
        r = np.fft.irfft(spec * np.conj(spec))[:size]
        if r[0] <= 0:
            continue
        r = r / r[0]
        seg = r[lo:hi]
        k = int(np.argmax(seg))
        if seg[k] < 0.35:
            continue                       # not periodic enough: unvoiced or noise
        lag = lo + k
        if 0 < k < len(seg) - 1:           # parabolic peak refinement
            a, b, c = seg[k - 1], seg[k], seg[k + 1]
            den = a - 2 * b + c
            if den:
                lag += 0.5 * (a - c) / den
        out.append(sr / lag)
    # drop octave errors: values far from the median
    if out:
        med = float(np.median(out))
        out = [f for f in out if 0.6 * med < f < 1.7 * med]
    return out


def level_stats(x) -> dict[str, float]:
    import numpy as np

    x = np.asarray(x, dtype=np.float32)
    fr = _frames(x, 480, 160)            # 30 ms / 10 ms
    if not len(fr):
        return {"speech_seconds": 0.0, "level_db": -120.0, "dynamic_range_db": 0.0, "snr_db": 0.0, "clipping_pct": 0.0}
    rms = np.sqrt((fr ** 2).mean(axis=1)) + 1e-9
    db = 20 * np.log10(rms)
    noise = float(np.percentile(db, 10))
    active = db > max(noise + 10, -55)
    speech_db = db[active] if active.any() else db
    return {
        "speech_seconds": round(float(active.sum()) * 0.01, 2),
        "level_db": round(float(np.median(speech_db)), 1),
        "dynamic_range_db": round(float(np.percentile(speech_db, 90) - np.percentile(speech_db, 10)), 1),
        "snr_db": round(float(np.median(speech_db) - noise), 1),
        "clipping_pct": round(100 * float((np.abs(x) >= 0.99).mean()), 3),
    }


# -- language ---------------------------------------------------------------------------

def _count(patterns: Iterable[str], text: str) -> dict[str, int]:
    """Counts by what was actually said ('ähm', 'you know'), not by pattern."""
    out: dict[str, int] = {}
    for p in patterns:
        for m in re.finditer(p, text, re.I):
            label = m.group(0).strip(" ,.?").lower()
            out[label] = out.get(label, 0) + 1
    return out


def text_features(text: str, lang: str = "en") -> dict[str, Any]:
    t = " " + (text or "").lower() + " "
    words = re.findall(r"[\wäöüß']+", t)
    lang = lang if lang in FILLERS else "en"
    fillers = _count(FILLERS[lang] + (FILLERS["en"][:5] if lang == "de" else []), t)
    hedges = _count(HEDGES[lang], t)
    pos, neg = len(re.findall(POSITIVE, t)), len(re.findall(NEGATIVE, t))
    sentences = [s for s in re.split(r"[.!?]+", text or "") if s.strip()]
    return {
        "words": len(words),
        "fillers": fillers, "filler_count": sum(fillers.values()),
        "hedges": hedges, "hedge_count": sum(hedges.values()),
        "questions": (text or "").count("?"),
        "avg_sentence_words": round(len(words) / max(1, len(sentences)), 1),
        "sentiment": round((pos - neg) / max(1, pos + neg), 2) if pos + neg else 0.0,
        "positive_words": pos, "negative_words": neg,
        "vocabulary_richness": round(len(set(words)) / max(1, len(words)), 2),
    }


# -- the analysis ---------------------------------------------------------------------------

def analyse(audio=None, *, text: str = "", words: list[dict] | None = None, lang: str = "en",
            confidence: float | None = None) -> dict[str, Any]:
    """audio: 16 kHz mono float array (optional). words: [{"start", "end", "word", "probability"}] from Whisper."""
    import numpy as np

    lang = lang if lang in PACE else "en"
    if words and not text:
        text = " ".join(w["word"].strip() for w in words)
    tf = text_features(text, lang)
    m: dict[str, Any] = {"lang": lang, "text": text, **tf}
    duration = len(audio) / SR if audio is not None and len(audio) else (words[-1]["end"] - words[0]["start"] if words else 0.0)
    m["duration_s"] = round(float(duration), 2)

    # pauses + articulation from word timing
    pauses: list[float] = []
    if words:
        for a, b in zip(words, words[1:]):
            gap = float(b["start"]) - float(a["end"])
            if gap >= 0.3:
                pauses.append(gap)
        span = float(words[-1]["end"]) - float(words[0]["start"])
        speaking = max(0.1, span - sum(pauses))
        probs = [float(w.get("probability", 1)) for w in words if w.get("probability") is not None]
        if probs and confidence is None:
            confidence = sum(probs) / len(probs)
    else:
        speaking = None
    m["pauses"] = len(pauses)
    m["long_pauses"] = sum(p >= 1.0 for p in pauses)
    m["avg_pause_s"] = round(sum(pauses) / len(pauses), 2) if pauses else 0.0
    # pace over the time you were talking (first word to last), so silence before/after doesn't count
    talk = (float(words[-1]["end"]) - float(words[0]["start"])) if words and len(words) > 1 else duration
    minutes = max(talk, 1e-6) / 60
    m["wpm"] = round(tf["words"] / minutes) if talk else None
    if audio is not None and len(audio):
        lv = level_stats(audio)
        m.update(lv)
        if speaking is None:
            speaking = max(0.1, lv["speech_seconds"])
        f0 = pitch_track(audio)
        if len(f0) >= 10:
            f = np.asarray(f0)
            st = 12 * np.log2(f / np.median(f))
            m.update({"pitch_hz": round(float(np.median(f)), 1),
                      "pitch_range_hz": [round(float(np.percentile(f, 10))), round(float(np.percentile(f, 90)))],
                      "pitch_variation_st": round(float(np.std(st)), 2),
                      "voiced_s": round(len(f0) * 0.02, 2)})
    m["articulation_wpm"] = round(tf["words"] / (speaking / 60)) if speaking and tf["words"] else None
    m["fillers_per_min"] = round(tf["filler_count"] / minutes, 1) if duration else None
    m["clarity"] = round(confidence, 2) if confidence is not None else None
    m["energy"] = _energy(m)
    m["score"], m["tips"] = _score(m)
    return m


def _energy(m: dict) -> str:
    var, wpm, dyn = m.get("pitch_variation_st"), m.get("wpm") or 0, m.get("dynamic_range_db")
    if var is None:
        return "unknown"
    lo, hi = PACE[m["lang"]]
    if var < 1.6 and (dyn or 0) < 12:
        return "flat"
    if var > 4 and wpm > hi:
        return "energetic (fast)"
    if var >= 2.5 or (dyn or 0) > 18:
        return "engaged"
    return "calm"


def _score(m: dict) -> tuple[int, list[str]]:
    """0-100 delivery score and the 3 most useful tips."""
    lo, hi = PACE[m["lang"]]
    parts: list[tuple[float, float, str | None]] = []   # (points, weight, tip)
    wpm = m.get("wpm")
    if wpm and m["duration_s"] >= 8:
        if wpm > hi + 25:
            parts.append((0.4, 25, f"You spoke at {wpm} words/min. Slow down to {lo}-{hi}: pause after the key number."))
        elif wpm > hi:
            parts.append((0.75, 25, f"A bit fast ({wpm} wpm). Land each benefit before moving on."))
        elif wpm < lo - 25:
            parts.append((0.5, 25, f"Quite slow ({wpm} wpm). Tighten sentences so the listener stays with you."))
        elif wpm < lo:
            parts.append((0.8, 25, None))
        else:
            parts.append((1.0, 25, None))
    fpm = m.get("fillers_per_min")
    if fpm is not None and m["duration_s"] >= 8:
        worst = max(m["fillers"].items(), key=lambda kv: kv[1])[0] if m["fillers"] else ""
        if fpm > 6:
            parts.append((0.3, 25, f"{fpm:g} filler words per minute (mostly '{worst}'). Replace them with a short silent pause."))
        elif fpm > 3:
            parts.append((0.65, 25, f"{fpm:g} fillers per minute ('{worst}'). Aim for under 3."))
        else:
            parts.append((1.0, 25, None))
    var = m.get("pitch_variation_st")
    if var is not None:
        if var < 1.6:
            parts.append((0.4, 20, "Your voice is quite monotone. Lift your pitch on the benefit ('save up to 30%') and drop it at the end."))
        elif var < 2.5:
            parts.append((0.75, 20, "Add a little more melody: stress one word per sentence."))
        else:
            parts.append((1.0, 20, None))
    if m.get("words", 0) >= 20:
        hed = m["hedge_count"] / max(1, m["words"]) * 100
        if hed > 4:
            parts.append((0.45, 15, f"Lots of hedging ({', '.join(list(m['hedges'])[:3])}). State it: 'Students save money with us', not 'I think maybe'."))
        elif hed > 2:
            parts.append((0.75, 15, "Drop a few hedges ('just', 'maybe') to sound more certain."))
        else:
            parts.append((1.0, 15, None))
    if m.get("long_pauses", 0) >= 3 and m["duration_s"] < 120:
        parts.append((0.7, 5, f"{m['long_pauses']} long pauses: know your next point (problem → offer → ask)."))
    if m.get("clarity") is not None:
        c = m["clarity"]
        parts.append((1.0 if c > 0.8 else 0.7 if c > 0.65 else 0.4, 10,
                      None if c > 0.8 else "Some words were hard to recognise: open your mouth more on endings, or move closer to the mic."))
    if m.get("snr_db") is not None and m["snr_db"] < 15:
        parts.append((0.7, 5, "Background noise is high: find a quieter spot or use a headset."))
    if m.get("clipping_pct", 0) > 0.5:
        parts.append((0.6, 5, "The mic is clipping: lower the input volume."))
    if not parts:
        return 0, ["Speak for at least 10 seconds to get a delivery score."]
    total = sum(w for _, w, _ in parts)
    score = round(100 * sum(p * w for p, w, _ in parts) / total)
    tips = [t for p, w, t in sorted(parts, key=lambda x: (x[0] - 1) * x[1]) if t][:3]
    if not tips:
        tips = ["Clear, well-paced delivery. Keep it up."]
    return score, tips


# -- storage, trend, coaching --------------------------------------------------------------------

def _ensure(db: DB) -> None:
    db.conn.execute("CREATE TABLE IF NOT EXISTS speech_sessions (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, mode TEXT, "
                    "lang TEXT, text TEXT, duration_s REAL, score INTEGER, metrics TEXT, coaching TEXT)")
    db.conn.commit()


def save(db: DB, metrics: dict, mode: str = "command", coaching: str = "") -> int:
    _ensure(db)
    slim = {k: v for k, v in metrics.items() if k != "text"}
    cur = db.x("INSERT INTO speech_sessions(at,mode,lang,text,duration_s,score,metrics,coaching) VALUES(?,?,?,?,?,?,?,?)",
               (utcnow(), mode, metrics.get("lang"), (metrics.get("text") or "")[:4000], metrics.get("duration_s"),
                metrics.get("score"), json.dumps(slim, ensure_ascii=False), coaching))
    return cur.lastrowid


def sessions(db: DB, limit: int = 30, mode: str | None = None) -> list[dict]:
    _ensure(db)
    sql, params = "SELECT * FROM speech_sessions", []
    if mode:
        sql += " WHERE mode=?"
        params.append(mode)
    rows = [dict(r) for r in db.q(sql + " ORDER BY id DESC LIMIT ?", (*params, limit))]
    for r in rows:
        r["metrics"] = json.loads(r["metrics"] or "{}")
    return rows


def trend(db: DB, days: int = 30) -> dict[str, Any]:
    """Averages over recent sessions long enough to judge (>= 8 s), split into the earlier and later half."""
    _ensure(db)
    rows = [dict(r) for r in db.q("SELECT * FROM speech_sessions WHERE duration_s >= 8 AND at >= datetime('now', ?) ORDER BY id",
                                  (f"-{days} day",))]
    if not rows:
        return {"sessions": 0}
    ms = [json.loads(r["metrics"]) for r in rows]

    def avg(key: str, part: list[dict]) -> float | None:
        v = [m[key] for m in part if m.get(key) is not None]
        return round(sum(v) / len(v), 1) if v else None

    half = len(ms) // 2 or 1
    keys = ["score", "wpm", "fillers_per_min", "pitch_variation_st", "clarity"]
    return {"sessions": len(rows), "now": {k: avg(k, ms[half:] or ms) for k in keys},
            "before": {k: avg(k, ms[:half]) for k in keys} if len(ms) >= 4 else None,
            "series": [{"at": r["at"], "score": r["score"], "wpm": m.get("wpm"), "fillers_per_min": m.get("fillers_per_min"),
                        "mode": r["mode"]} for r, m in zip(rows, ms)]}


def coach(cfg, metrics: dict, topic: str = "pitch", llm=None) -> str:
    """AI feedback on the content (structure, value proposition, the ask) plus delivery numbers. Needs an LLM."""
    from .knowledge import ONE_LINER
    from .llm import LLM

    llm = llm or LLM(cfg)
    numbers = {k: metrics.get(k) for k in ("wpm", "fillers_per_min", "fillers", "hedges", "pitch_variation_st", "long_pauses",
                                           "score", "energy", "duration_s")}
    return llm.cloud(
        f"{ONE_LINER}\n\nThe founder practised a {topic} out loud ({metrics.get('lang')}). Transcript:\n\"\"\"{metrics.get('text', '')[:4000]}\"\"\"\n\n"
        f"Delivery measurements: {json.dumps(numbers, ensure_ascii=False)}\n\n"
        "Give feedback in 4 short parts: 1) what worked, 2) structure (hook → problem → Settleezy offer → proof → clear ask): "
        "what's missing, 3) one sentence they should say instead (rewrite their weakest line), 4) delivery: one fix from the "
        "numbers. Max 140 words, direct, in the language of the transcript.",
        "You are Setz, a sharp, encouraging pitch coach for a student-savings startup in Berlin.", effort="low", max_tokens=1200)


def spoken_summary(m: dict) -> str:
    bits = [f"Delivery score {m['score']} out of 100"]
    if m.get("wpm"):
        bits.append(f"{m['wpm']} words per minute")
    if m.get("fillers_per_min") is not None:
        bits.append(f"{m['fillers_per_min']:g} filler words per minute")
    if m.get("energy") and m["energy"] != "unknown":
        bits.append(f"your voice sounded {m['energy']}")
    return ", ".join(bits) + ". " + (m["tips"][0] if m.get("tips") else "")


def decode(data: bytes | str):
    """Any audio file or bytes (wav, mp3, m4a, webm...) -> 16 kHz mono float32, via faster-whisper's decoder (PyAV)."""
    import io

    try:
        from faster_whisper.audio import decode_audio
    except ImportError as exc:
        if isinstance(data, str) and data.lower().endswith(".wav"):
            return _read_wav(data)
        raise RuntimeError("Install the voice extras to analyse audio: pip install -e \".[voice]\"") from exc
    return decode_audio(io.BytesIO(data) if isinstance(data, (bytes, bytearray)) else data, sampling_rate=SR)


def _read_wav(path: str):
    import wave

    import numpy as np

    with wave.open(path, "rb") as w:
        sr, ch, n = w.getframerate(), w.getnchannels(), w.getnframes()
        x = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float32) / 32768
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    if sr != SR:   # simple linear resample
        t = np.linspace(0, len(x) / sr, int(len(x) * SR / sr), endpoint=False)
        x = np.interp(t, np.arange(len(x)) / sr, x).astype(np.float32)
    return x


def words_from_segments(segments) -> list[dict]:
    out = []
    for s in segments:
        for w in getattr(s, "words", None) or []:
            out.append({"start": float(w.start), "end": float(w.end), "word": w.word, "probability": float(w.probability)})
    return out
