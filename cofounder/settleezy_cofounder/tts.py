"""Text-to-speech with ElevenLabs (falls back to Windows voices via pyttsx3).

ElevenLabs returns raw 16-bit mono PCM, so we can both play it and compute a loudness envelope that the
hologram animates to, in sync with the speech.
"""

from __future__ import annotations

import array
import io
import math
import wave

import httpx

from .config import Config, secret

API = "https://api.elevenlabs.io/v1"
SAMPLE_RATE = 22050


class TTSError(RuntimeError):
    pass


def elevenlabs_enabled(cfg: Config) -> bool:
    return cfg.get("voice.tts", "elevenlabs") == "elevenlabs" and bool(secret("ELEVENLABS_API_KEY"))


def voice_id(cfg: Config, alt: bool = False) -> str:
    return cfg.get("voice.elevenlabs_voice_id_alt" if alt else "voice.elevenlabs_voice_id", "CUvmi6RSy4BQr6vnMyEw")


def synthesize(cfg: Config, text: str, *, alt: bool = False) -> bytes:
    """Return raw PCM (int16, mono, 22.05 kHz)."""
    r = httpx.post(
        f"{API}/text-to-speech/{voice_id(cfg, alt)}",
        params={"output_format": f"pcm_{SAMPLE_RATE}"},
        headers={"xi-api-key": secret("ELEVENLABS_API_KEY", required=True), "Content-Type": "application/json"},
        json={
            "text": text[:4500],
            "model_id": cfg.get("voice.elevenlabs_model", "eleven_multilingual_v2"),
            "voice_settings": {
                "stability": float(cfg.get("voice.stability", 0.45)),
                "similarity_boost": float(cfg.get("voice.similarity", 0.8)),
            },
        },
        timeout=60,
    )
    if r.status_code >= 400:
        detail = r.text[:300]
        if r.status_code in (400, 404) and "voice" in detail.lower():
            detail += " — add this voice to 'My Voices' in ElevenLabs (Voice Library → + button) and try again."
        raise TTSError(f"ElevenLabs {r.status_code}: {detail}")
    return r.content


def envelope(pcm: bytes, sample_rate: int = SAMPLE_RATE, frame_ms: int = 40) -> list[float]:
    """Loudness per frame, normalised 0..1, for driving the hologram."""
    samples = array.array("h")
    samples.frombytes(pcm[: len(pcm) - len(pcm) % 2])
    step = max(1, int(sample_rate * frame_ms / 1000))
    levels = []
    for i in range(0, len(samples), step):
        chunk = samples[i : i + step]
        if not chunk:
            break
        rms = math.sqrt(sum(s * s for s in chunk[::4]) / max(1, len(chunk[::4])))
        levels.append(rms)
    peak = max(levels, default=0) or 1.0
    return [round(min(1.0, (v / peak) ** 0.7), 3) for v in levels]


def wav_bytes(pcm: bytes, sample_rate: int = SAMPLE_RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


def check(cfg: Config) -> str:
    """Cheap connectivity check: the configured voice exists for this API key."""
    r = httpx.get(f"{API}/voices/{voice_id(cfg)}", headers={"xi-api-key": secret("ELEVENLABS_API_KEY", required=True)}, timeout=20)
    if r.status_code == 401:
        raise TTSError("API key rejected")
    if r.status_code >= 400:
        raise TTSError(f"voice {voice_id(cfg)} not available ({r.status_code}); add it to My Voices in ElevenLabs")
    return r.json().get("name", voice_id(cfg))
