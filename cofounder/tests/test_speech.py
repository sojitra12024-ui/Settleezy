import numpy as np
import pytest

from settleezy_cofounder import speech

SR = 16000


def voice(seconds=20, f0=180.0, semitones=3.0, seed=0):
    """Harmonic 'voice' with melody and gaps, plus a little noise."""
    t = np.arange(int(SR * seconds)) / SR
    f = f0 * 2 ** (semitones * np.sin(2 * np.pi * 0.4 * t) / 12)
    ph = 2 * np.pi * np.cumsum(f) / SR
    env = (np.sin(2 * np.pi * 1.3 * t) > -0.4).astype(float)
    rng = np.random.default_rng(seed)
    return (0.3 * env * (np.sin(ph) + 0.4 * np.sin(2 * ph) + 0.2 * np.sin(3 * ph)) + 0.002 * rng.standard_normal(len(t))).astype(np.float32)


def words_from(text, rate_wps=2.5, long_pause_after=None):
    out, t = [], 0.5
    for i, w in enumerate(text.split()):
        out.append({"start": t, "end": t + 0.8 / rate_wps, "word": w, "probability": 0.92})
        t += 1 / rate_wps + (1.5 if long_pause_after and i == long_pause_after else 0)
    return out


def test_pitch_tracking_finds_f0_and_variation():
    f0 = speech.pitch_track(voice(f0=180, semitones=0))
    assert abs(np.median(f0) - 180) < 6
    m = speech.analyse(voice(semitones=3), text="hello there")
    assert 1.5 < m["pitch_variation_st"] < 2.8       # a ±3 st sine has std ≈ 2.1 st
    flat = speech.analyse(voice(semitones=0.2), text="hello there")
    assert flat["pitch_variation_st"] < 0.5


def test_fillers_hedges_and_tone_in_both_languages():
    de = speech.text_features("Ähm, wir sind halt quasi eine App, ähm, ich glaube Studierende können vielleicht sparen.", "de")
    assert de["fillers"] == {"ähm": 2, "halt": 1, "quasi": 1} and set(de["hedges"]) == {"ich glaube", "vielleicht"}
    en = speech.text_features("Um, I think we just, you know, help students save money. Great!", "en")
    assert en["fillers"]["um"] == 1 and en["fillers"]["you know"] == 1 and "i think" in en["hedges"] and en["sentiment"] > 0


def test_delivery_score_pace_pauses_and_tips():
    clean = "Settleezy helps international students in Berlin save money on food events and groceries every single week " * 3
    good = speech.analyse(voice(seconds=22), words=words_from(clean, rate_wps=2.5), lang="en")
    assert 130 <= good["wpm"] <= 170 and good["score"] >= 80
    messy = ("um so like we are uh basically a an app you know um for students and um I think maybe we could just "
             "sort of help them save um money kind of ") * 2
    bad = speech.analyse(voice(seconds=22, semitones=0.3), words=words_from(messy, rate_wps=4.2, long_pause_after=5), lang="en")
    assert bad["score"] < good["score"] - 25
    assert bad["fillers_per_min"] > 6 and bad["long_pauses"] == 1 and bad["energy"] == "flat"
    assert any("filler" in t for t in bad["tips"]) and any("monotone" in t or "fast" in t.lower() for t in bad["tips"])
    assert "Delivery score" in speech.spoken_summary(bad)


def test_sessions_and_trend(db):
    for score_words in (2.5, 2.5, 2.4, 2.5):
        m = speech.analyse(voice(seconds=12), words=words_from("we help students in Berlin save money " * 4, score_words), lang="en")
        speech.save(db, m, "practice", "coach says hi")
    speech.save(db, speech.analyse(text="hey setz what's next"), "command")
    tr = speech.trend(db)
    assert tr["sessions"] == 4 and tr["now"]["score"] is not None and tr["before"] is not None
    assert speech.sessions(db, mode="practice")[0]["coaching"] == "coach says hi"


def test_dashboard_speech_endpoints(cfg, monkeypatch):
    from fastapi.testclient import TestClient

    from settleezy_cofounder.dashboard.app import app

    c = TestClient(app)
    assert c.get("/api/speech").json()["trend"] == {"sessions": 0}
    assert c.post("/api/speech/analyse", content=b"x").status_code == 403            # CSRF guard
    monkeypatch.setattr(speech, "decode", lambda data: (_ for _ in ()).throw(RuntimeError("no voice extras")))
    r = c.post("/api/speech/analyse", content=b"RIFF....", headers={"X-SZ": "1"})
    assert r.status_code == 501 and "voice extras" in r.json()["detail"]
