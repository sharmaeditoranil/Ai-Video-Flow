"""
Tests for STEP 6 (vows & speeches): lines from words, local line scoring, choosing lines, SRT captions,
the automatic vows pipeline in the backend (fake transcription), and the Story Director cutting the exact
spoken line with a caption. A real transcription test runs only when a local Whisper model is available.
"""

import json
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from packages.ai_engine import ai_audio  # noqa: E402
from packages.ai_engine.story_planner import story_planner  # noqa: E402
from tests.test_story_director import wedding  # noqa: E402,F401  (fixture)
from tests.test_music_story import _music  # noqa: E402


def _words(text, start=0.0, step=0.35):
    out, t = [], start
    for w in text.split():
        out.append({"text": w, "start": round(t, 2), "end": round(t + step - 0.05, 2)})
        t += step
    return out


# ---------------------------------------------------------------- lines, scoring, choice, SRT

def test_split_lines_on_pause_and_sentence():
    words = _words("Main hamesha tumhare saath rahungi.") + _words("Thank you papa", start=4.0)
    lines = ai_audio.split_lines(words)
    assert [l["text"] for l in lines] == ["Main hamesha tumhare saath rahungi.", "Thank you papa"]
    assert lines[1]["start"] == 4.0


def test_score_prefers_heartfelt_lines():
    assert ai_audio.score_line("I promise to love you forever", 2.5) > ai_audio.score_line("umm haan ok so", 2.0)
    assert ai_audio.score_line("हम हमेशा साथ रहेंगे, यह मेरा वादा है", 3.0) >= 70
    assert ai_audio.score_line("Yes", 0.5) < 45


def test_choose_one_line_per_clip():
    cands = [{"id": 0, "text": "I promise to love you forever", "start": 0, "end": 2.5, "bonus": 0, "clip": "a"},
             {"id": 0, "text": "You are my family now, always", "start": 3, "end": 5.5, "bonus": 0},
             {"id": 1, "text": "Beti, hamesha khush rehna", "start": 0, "end": 2.2, "bonus": 6},
             {"id": 2, "text": "ok", "start": 0, "end": 0.5, "bonus": 0}]
    picked = ai_audio.choose_lines_local(cands, 5)
    assert [c["id"] for c in picked].count(0) == 1 and 1 in [c["id"] for c in picked] and 2 not in [c["id"] for c in picked]


def test_write_srt(tmp_path):
    path = ai_audio.write_srt([{"start": 61.5, "end": 64.25, "text": "Hamesha saath"},
                               {"start": 3.0, "end": 5.0, "text": "I promise"}], str(tmp_path / "c" / "x.srt"))
    text = open(path, encoding="utf-8").read()
    assert text.startswith("1\n00:00:03,000 --> 00:00:05,000\nI promise\n\n2\n00:01:01,500 --> 00:01:04,250\nHamesha saath")


# ---------------------------------------------------------------- backend pipeline (fake transcription)

@pytest.fixture
def backend(tmp_path, monkeypatch):
    b = pytest.importorskip("packages.desktop_app.backend")
    import hashlib
    import packages.ai_engine.in_clip_trimmer as ict
    monkeypatch.setattr(ict.in_clip_trimmer, "cache_dir", str(tmp_path / "cache"))
    # the generated test videos share their content, so key the cache by path here
    monkeypatch.setattr(ict, "review_cache_key", lambda c: hashlib.sha1(c["file_path"].encode()).hexdigest())
    return b


def _speech_clips(wedding):
    out = []
    for c in wedding:
        c = dict(c)
        c["audio_type"] = "speech" if c["moment"] in ("pheras", "vidaai", "varmala") else "music"
        c.setdefault("faces", {"count": 1, "boxes": []})
        out.append(c)
    return out


def test_vows_pipeline_local(backend, wedding, monkeypatch):
    clips = _speech_clips(wedding)
    calls = []

    def fake_local(path, start=0.0, end=None, **kw):
        calls.append(path)
        name = os.path.basename(path)
        line = "Main hamesha tumhare saath rahungi." if "pheras" in name else "Haan ok."
        return {"words": _words(line, start + 1.0), "text": line, "language": "hi", "engine": "local"}

    monkeypatch.setattr(ai_audio, "local_transcription_available", lambda: True)
    monkeypatch.setattr(ai_audio, "local_model_ready", lambda *a: True)
    monkeypatch.setattr(ai_audio, "transcribe_local", fake_local)
    n = backend.find_vow_lines(backend._new_job(), clips, is_premium=False)
    chosen = [c for c in clips if c.get("vow_line")]
    assert n >= 1 and all("pheras" in c["file_path"] for c in chosen)
    assert chosen[0]["vow_line"]["text"] == "Main hamesha tumhare saath rahungi."
    first = len(calls)
    backend.find_vow_lines(backend._new_job(), _speech_clips(wedding), is_premium=False)
    assert len(calls) == first          # second run: transcripts come from the cache


def test_vows_premium_cloud_falls_back_to_local(backend, wedding, monkeypatch):
    clips = _speech_clips(wedding)

    def cloud_down(*a, **k):
        raise ai_audio.AIAudioError("offline")

    monkeypatch.setattr(ai_audio, "transcribe", cloud_down)
    monkeypatch.setattr(ai_audio, "local_transcription_available", lambda: True)
    monkeypatch.setattr(ai_audio, "local_model_ready", lambda *a: True)
    monkeypatch.setattr(ai_audio, "transcribe_local",
                        lambda path, start=0.0, end=None, **kw: {"words": _words("Thank you for being my family forever", start + 0.5),
                                                                 "engine": "local"})
    monkeypatch.setattr(backend.cloud_ai_client, "pick_vow_lines", lambda lines, n, instr="": [lines[-1]["id"]])
    assert backend.find_vow_lines(backend._new_job(), clips, is_premium=True, instructions="") == 1


# ---------------------------------------------------------------- Story Director uses the exact line

def test_director_cuts_the_whole_line_with_caption(wedding):
    clips = _speech_clips(wedding)
    pher = next(c for c in clips if c["moment"] == "pheras" and c["audio_type"] == "speech")
    pher["vow_line"] = {"text": "Main hamesha tumhare saath rahungi.", "start": 3.0, "end": 5.2, "rank": 0}
    m = _music()
    plan = story_planner.plan_teaser(clips, target_duration=36.0, story_mode="wedding", beat_times=m["beat_times"], music_info=m)
    vow = [e for e in plan["clips"] if e.get("vow_text")]
    assert len(vow) == 1, [(e["timeline_start"], e["why"]) for e in plan["clips"]]
    e = vow[0]
    fps = pher["native_fps"]
    assert e["in_frame"] == int(2.75 * fps) and e["out_frame"] == int(5.6 * fps)   # 0.25 s before, 0.4 s after
    assert abs(e["duration"] - 2.85) < 0.01 and e["speed"] == 1.0 and e["duck_music"]
    cap = plan["captions"][0]
    assert abs(cap["start"] - (e["timeline_start"] + 0.25)) < 0.02 and abs(cap["end"] - (e["timeline_start"] + 2.45)) < 0.02
    # only chosen lines are used as vows once any line is chosen
    assert all(x.get("vow_text") for x in plan["clips"] if x.get("duck_music"))


# ---------------------------------------------------------------- real local transcription (optional)

def test_real_local_transcription(tmp_path):
    model_dir = os.environ.get("AVF_WHISPER_TEST_DIR")
    if not model_dir or not ai_audio.local_transcription_available() or not shutil.which("say"):
        pytest.skip("set AVF_WHISPER_TEST_DIR to a folder with a Whisper model (macOS 'say' needed)")
    audio = str(tmp_path / "vow.aiff")
    subprocess.run(["say", "-o", audio, "I promise to love you forever."], check=True)
    r = ai_audio.transcribe_local(audio, model_size="tiny", download_root=model_dir)
    assert "promise" in r["text"].lower() and r["words"][0]["start"] >= 0
