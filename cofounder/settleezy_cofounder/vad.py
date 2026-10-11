"""Voice activity detection: is someone speaking in this bit of audio?

Silero VAD (https://github.com/snakers4/silero-vad, MIT) is a small neural network that tells speech from noise
(keyboard, fan, traffic, music) far better than a loudness threshold. faster-whisper already ships its ONNX model,
so this needs no PyTorch: it runs on onnxruntime on the CPU in about 0.2 ms per 32 ms of audio.

Setz uses it to
  * start recording when you start talking and stop when you stop (with hysteresis, so short pauses don't cut you off)
  * ignore background noise in hands-free mode, so Whisper isn't woken up by every sound
  * barge-in: only real speech (not a door or a cough) interrupts Setz while it talks

If the model isn't available, `EnergyVAD` falls back to the old loudness threshold with the same interface.
"""

from __future__ import annotations

import glob
import os
from typing import Any, Callable, Iterator

from .config import Config

SR = 16000


def silero_model_path() -> str | None:
    try:
        import faster_whisper
    except ImportError:
        return None
    found = sorted(glob.glob(os.path.join(os.path.dirname(faster_whisper.__file__), "assets", "silero_vad*.onnx")))
    return found[-1] if found else None


class SileroVAD:
    """Streaming Silero VAD (v6 ONNX: 512-sample chunks + 64 samples of context, LSTM state carried across calls)."""

    name = "silero"
    CHUNK, CONTEXT = 512, 64

    def __init__(self, path: str | None = None, threshold: float = 0.5):
        import numpy as np
        import onnxruntime as ort

        path = path or silero_model_path()
        if not path:
            raise RuntimeError("Silero VAD model not found (install faster-whisper: pip install -e \".[voice]\")")
        opts = ort.SessionOptions()
        opts.inter_op_num_threads = opts.intra_op_num_threads = 1
        opts.log_severity_level = 4
        self.session = ort.InferenceSession(path, providers=["CPUExecutionProvider"], sess_options=opts)
        names = {i.name for i in self.session.get_inputs()}
        if names != {"input", "h", "c"}:
            raise RuntimeError(f"unexpected Silero model inputs {sorted(names)}")
        self.np = np
        self.threshold = threshold
        self.reset()

    def reset(self) -> None:
        np = self.np
        self.h = np.zeros((1, 1, 128), dtype=np.float32)
        self.c = np.zeros((1, 1, 128), dtype=np.float32)
        self.context = np.zeros(self.CONTEXT, dtype=np.float32)
        self.buffer = np.zeros(0, dtype=np.float32)
        self.last = 0.0

    def __call__(self, block: Any) -> float:
        """Feed any amount of 16 kHz float audio; returns the highest speech probability seen in it (0..1)."""
        np = self.np
        x = np.asarray(block, dtype=np.float32).reshape(-1)
        self.buffer = np.concatenate([self.buffer, x])
        best = None
        while len(self.buffer) >= self.CHUNK:
            chunk, self.buffer = self.buffer[:self.CHUNK], self.buffer[self.CHUNK:]
            inp = np.concatenate([self.context, chunk])[None, :]
            prob, self.h, self.c = self.session.run(None, {"input": inp, "h": self.h, "c": self.c})
            self.context = chunk[-self.CONTEXT:]
            p = float(np.ravel(prob)[0])
            best = p if best is None else max(best, p)
        if best is not None:
            self.last = best
        return self.last


class EnergyVAD:
    """Loudness threshold with the same interface (fallback when Silero isn't installed)."""

    name = "energy"

    def __init__(self, threshold: float = 0.012):
        self.level = threshold
        self.threshold = 0.5

    def reset(self) -> None:
        pass

    def __call__(self, block: Any) -> float:
        import numpy as np

        rms = float(np.sqrt(np.mean(np.asarray(block, dtype=np.float32) ** 2))) if len(block) else 0.0
        return min(1.0, 0.5 * rms / self.level) if self.level else 0.0   # == 0.5 exactly at the threshold


def make_vad(cfg: Config) -> SileroVAD | EnergyVAD:
    mode = cfg.get("voice.vad", "auto")   # auto | silero | energy
    if mode in ("auto", "silero"):
        try:
            return SileroVAD(threshold=float(cfg.get("voice.vad_threshold", 0.5)))
        except Exception:
            if mode == "silero":
                raise
    return EnergyVAD(float(cfg.get("voice.mic_threshold", 0.012)))


def collect_utterance(blocks: Iterator[Any], vad: Callable[[Any], float], *, block_seconds: float,
                      threshold: float = 0.5, silence_seconds: float = 1.2, max_seconds: float = 15,
                      wait_seconds: float | None = None, min_speech_seconds: float = 0.15,
                      pre_roll_blocks: int = 3) -> tuple[list[Any], bool]:
    """Cut one utterance out of a stream of audio blocks.

    Speech starts after `min_speech_seconds` above `threshold`; it ends after `silence_seconds` below
    `threshold - 0.15` (Silero's recommended hysteresis), so brief pauses inside a sentence don't end it.
    Returns (blocks incl. a little pre-roll, whether speech was heard)."""
    off = max(0.05, threshold - 0.15)
    eps = 1e-6   # 10 x 0.08 s adds up to 0.7999999 in floating point
    frames: list[Any] = []
    started, voiced, silent, t = False, 0.0, 0.0, 0.0
    for block in blocks:
        t += block_seconds
        p = vad(block)
        if not started:
            frames = (frames + [block])[-(pre_roll_blocks + 1):]
            voiced = voiced + block_seconds if p >= threshold else 0.0
            if voiced >= min_speech_seconds - eps:
                started = True
            elif wait_seconds is not None and t >= wait_seconds - eps:
                break
        else:
            frames.append(block)
            silent = silent + block_seconds if p < off else 0.0
            if silent >= silence_seconds - eps:
                break
        if t >= max_seconds - eps:
            break
    return (frames if started else []), started


def speech_seconds(audio: Any, vad: SileroVAD | EnergyVAD | None = None) -> float | None:
    """Seconds of actual speech in a recording (used by the speech analyser when the model is available)."""
    if vad is None:
        try:
            vad = SileroVAD()
        except Exception:
            return None
    vad.reset()
    n = 0
    step = 512
    for i in range(0, len(audio) - step + 1, step):
        if vad(audio[i:i + step]) >= vad.threshold:
            n += 1
    return round(n * step / SR, 2)
