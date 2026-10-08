import sys
import types

import numpy as np
import pytest

from settleezy_cofounder import brain, vad
from settleezy_cofounder.connections import _memory, _vad


class FakeTextEmbedding:
    """Stand-in for fastembed.TextEmbedding: records what it was given, returns deterministic vectors."""
    calls: list = []

    def __init__(self, model, cache_dir=None):
        if model == "broken/model":
            raise ValueError("Could not load model broken/model from any source.")
        self.model, self.cache_dir = model, cache_dir

    def embed(self, texts, batch_size=32):
        FakeTextEmbedding.calls.append(list(texts))
        for t in texts:
            yield np.asarray(brain._hash_embed(t.removeprefix("query: ").removeprefix("passage: ")), dtype=np.float32)


@pytest.fixture()
def fake_fastembed(monkeypatch, cfg):
    monkeypatch.setitem(sys.modules, "fastembed", types.SimpleNamespace(TextEmbedding=FakeTextEmbedding))
    monkeypatch.setattr(brain, "_EMBEDDERS", {})
    FakeTextEmbedding.calls = []
    cfg.raw["brain"]["embedder"] = "auto"
    return cfg


def test_fastembed_is_used_with_a_persistent_cache_and_no_prefixes(fake_fastembed, db):
    e = brain.embedder(fake_fastembed)
    assert e.name == "fastembed:" + brain.FASTEMBED_MODEL
    assert e._fe.cache_dir.replace("\\", "/").endswith("data/models/fastembed")
    brain.remember(fake_fastembed, db, "Lea prefers WhatsApp", subject="Brew Lab")
    brain.recall(fake_fastembed, db, "how to reach Lea")
    assert all(not t.startswith(("query:", "passage:")) for call in FakeTextEmbedding.calls for t in call)
    assert db.one("SELECT embedder FROM memories")["embedder"].startswith("fastembed:")
    assert _memory(fake_fastembed).ok


def test_e5_models_get_their_prefixes(fake_fastembed):
    fake_fastembed.raw["brain"]["fastembed_model"] = "intfloat/multilingual-e5-large"
    e = brain.embedder(fake_fastembed)
    e.embed(["hello"], query=True)
    e.embed(["note"])
    assert FakeTextEmbedding.calls[-2:] == [["query: hello"], ["passage: note"]]


def test_failed_download_falls_back_and_backs_off(fake_fastembed, monkeypatch):
    fake_fastembed.raw["brain"]["fastembed_model"] = "broken/model"
    tries = []
    orig = FakeTextEmbedding.__init__

    def counting(self, model, cache_dir=None):
        tries.append(model)
        orig(self, model, cache_dir)

    monkeypatch.setattr(FakeTextEmbedding, "__init__", counting)
    e = brain.embedder(fake_fastembed)
    assert not e.name.startswith("fastembed") and "fastembed failed" in e.note and len(tries) == 1
    marker = fake_fastembed.data_dir / "models" / "fastembed" / ".download_failed"
    assert marker.exists()
    monkeypatch.setattr(brain, "_EMBEDDERS", {})
    e2 = brain.embedder(fake_fastembed)                         # a new process within a day: no 40-second retry
    assert len(tries) == 1 and "failed recently" in e2.note
    check = _memory(fake_fastembed)                             # sz doctor retries on purpose
    assert len(tries) == 2 and not check.ok and check.optional


def test_energy_vad_and_utterance_cutting():
    ev = vad.EnergyVAD(0.01)
    assert ev(np.zeros(1280)) == 0 and abs(ev(np.full(1280, 0.01)) - 0.5) < 1e-6 and ev(np.full(1280, 0.5)) == 1.0
    # fake probabilities: 0.5 s silence, 1 s speech with a 0.4 s pause inside, then silence
    probs = [0.0] * 6 + [0.9] * 6 + [0.2] * 5 + [0.9] * 6 + [0.0] * 30
    blocks = [np.full(1280, i, dtype=np.float32) for i in range(len(probs))]
    p = iter(probs)
    frames, started = vad.collect_utterance(iter(blocks), lambda b: next(p), block_seconds=0.08, silence_seconds=0.8)
    ids = [int(f[0]) for f in frames]
    assert started and ids[0] == 4 and 17 in ids                 # 3 blocks of pre-roll kept; the short pause didn't end it
    assert ids[-1] == 23 + 9                                     # stopped after 0.8 s of silence
    p = iter([0.1] * 100)
    frames, started = vad.collect_utterance(iter(blocks * 2), lambda b: next(p), block_seconds=0.08, wait_seconds=1.0)
    assert not started and frames == []


def test_make_vad_falls_back_to_energy(cfg, monkeypatch):
    monkeypatch.setattr(vad, "silero_model_path", lambda: None)
    assert vad.make_vad(cfg).name == "energy"
    cfg.raw["voice"]["vad"] = "silero"
    with pytest.raises(RuntimeError):
        vad.make_vad(cfg)
    cfg.raw["voice"]["vad"] = "auto"
    assert not _vad(cfg).ok


@pytest.mark.skipif(vad.silero_model_path() is None, reason="faster-whisper (voice extras) not installed")
def test_silero_streams_and_ignores_noise():
    v = vad.SileroVAD()
    rng = np.random.default_rng(0)
    assert v(np.zeros(SR := 16000, dtype=np.float32)) < 0.2
    v.reset()
    assert max(v(b) for b in np.array_split(0.05 * rng.standard_normal(SR).astype(np.float32), 13)) < 0.3
    v.reset()
    assert v(np.zeros(100, dtype=np.float32)) == 0.0              # less than one 512-sample chunk: buffered
    assert len(v.buffer) == 100
    assert vad.speech_seconds(np.zeros(SR, dtype=np.float32), v) == 0.0
