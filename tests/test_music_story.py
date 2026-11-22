"""
Tests for STEP 5 (music intelligence): downbeats / bars, song sections, vocal vs instrumental parts,
the audible end, and how the Story Director uses them (bars for emotion, best shot on the drop,
vows in instrumental parts with -18 dB ducking, ending on the song's end) plus the Premiere levels.
"""

import os
import sys
import xml.etree.ElementTree as ET

import numpy as np
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from packages.ai_engine import beat_analyzer as ba  # noqa: E402
from packages.ai_engine.story_planner import story_planner  # noqa: E402
from tests.test_story_director import wedding  # noqa: E402,F401  (fixture)


# ---------------------------------------------------------------- analysis helpers

def test_pick_downbeats_finds_the_strong_phase():
    beats = [i * 0.5 for i in range(32)]
    strength = [3.0 if i % 4 == 1 else 1.0 for i in range(32)]
    assert ba.pick_downbeats(beats, strength) == [0.5 + 2.0 * k for k in range(8)]


def test_label_sections():
    secs = ba.label_sections([0, 10, 30, 50, 60, 80], [0.4, 0.6, 1.0, 0.5, 0.95, 0.45], drops=[29.0], duration=90)
    assert [x["label"] for x in secs] == ["intro", "verse", "drop", "bridge", "chorus", "outro"]
    assert secs[-1]["end"] == 90


def test_vocal_and_instrumental_regions():
    t = np.arange(0, 60, 0.05)
    score = np.where(((t > 10) & (t < 25)) | ((t > 40) & (t < 50)), 1.0, 0.1)
    vocal, inst = ba.regions_from_score(t.tolist(), score.tolist(), 60.0)
    assert len(vocal) == 2 and abs(vocal[0][0] - 10) < 1.2 and abs(vocal[1][1] - 50) < 1.2
    assert any(a < 1 and b > 8 for a, b in inst) and any(b == 60.0 for a, b in inst)


def _song(path, bpm=120, seconds=40.0, quiet_until=10.0, sr=22050):
    import soundfile as sf
    t = np.arange(int(seconds * sr)) / sr
    y = np.zeros_like(t)
    beat = 60.0 / bpm
    for i in range(int(seconds / beat)):
        start = int(i * beat * sr)
        n = int(0.05 * sr)
        amp = (1.0 if i % 4 == 0 else 0.45) * (0.25 if i * beat < quiet_until else 1.0)
        y[start:start + n] += amp * np.sin(2 * np.pi * 80 * t[:n]) * np.exp(-t[:n] * 40)
    y += 0.05 * np.sin(2 * np.pi * 220 * t) * np.where(t < quiet_until, 0.3, 1.0)
    y[int((seconds - 3) * sr):] = 0.0                       # silent tail
    sf.write(path, y.astype(np.float32), sr)


def test_song_structure_on_a_click_track(tmp_path):
    pytest.importorskip("librosa")
    path = str(tmp_path / "song.wav")
    _song(path)
    r = ba.beat_analyzer.analyze_audio(path)
    assert abs(r["bpm"] - 120) < 6
    db = r["downbeat_times"]
    assert len(db) > 5 and abs(np.median(np.diff(db)) - 2.0) < 0.15
    assert r["sections"] and r["sections"][0]["start"] == 0.0
    assert 35.0 <= r["audible_end"] <= 37.6
    assert "vocal_regions" in r and "instrumental_regions" in r


# ---------------------------------------------------------------- Story Director with music

def _music(duration=40.0):
    beats = [round(0.5 * i, 2) for i in range(int(duration * 2))]
    return {
        "duration": duration, "beat_times": beats, "downbeat_times": beats[::4],
        "drop_times": [16.0], "sections": [{"start": 0.0, "end": 16.0, "label": "verse", "energy": 0.6},
                                          {"start": 16.0, "end": duration, "label": "drop", "energy": 1.0}],
        "instrumental_regions": [[20.0, 32.0]], "vocal_regions": [[0.0, 20.0]], "audible_end": 38.5,
    }


def _speechify(clips):
    for c in clips:
        c["audio_type"] = "speech" if c["moment"] in ("pheras", "vidaai") else "music"
        c.setdefault("faces", {"count": 1, "boxes": []})


def test_drop_gets_the_best_shot(wedding):
    _speechify(wedding)
    m = _music()
    plan = story_planner.plan_teaser(wedding, target_duration=36.0, story_mode="wedding", beat_times=m["beat_times"], music_info=m)
    at_drop = [e for e in plan["clips"] if abs(e["timeline_start"] - 16.0) < 0.06]
    assert at_drop, [(e["timeline_start"], e["why"]) for e in plan["clips"]]
    assert "on the drop" in at_drop[0]["why"], at_drop[0]["why"]


def test_cut_lands_exactly_on_the_drop(wedding):
    m = _music()
    plan = story_planner.plan_teaser(wedding, target_duration=36.0, story_mode="wedding", beat_times=m["beat_times"], music_info=m)
    assert any(abs(e["timeline_end"] - 16.0) < 0.06 for e in plan["clips"])


def test_emotional_cuts_on_bars(wedding):
    m = _music()
    bars = m["downbeat_times"]
    plan = story_planner.plan_teaser(wedding, target_duration=36.0, story_mode="wedding", beat_times=m["beat_times"], music_info=m)
    emo = [e for e in plan["clips"][1:-1] if e.get("act") in ("Union", "Emotion & Farewell") and not e.get("duck_music")]
    on_bar = [e for e in emo if min(abs(e["timeline_end"] - b) for b in bars) < 0.06]
    assert emo and len(on_bar) >= len(emo) - 1, [(e["timeline_end"], e["act"]) for e in emo]


def test_vows_in_instrumental_with_ducking(wedding):
    _speechify(wedding)
    m = _music()
    plan = story_planner.plan_teaser(wedding, target_duration=36.0, story_mode="wedding", beat_times=m["beat_times"], music_info=m)
    speech = [e for e in plan["clips"] if e.get("duck_music")]
    assert speech, [(e["timeline_start"], e["act"], e["why"]) for e in plan["clips"]]
    for e in speech:
        assert 19.5 <= e["timeline_start"] and e["timeline_end"] <= 32.01 and e["speed"] == 1.0 and e["keep_audio"]
        acts = {"Union": ("varmala", "pheras", "sindoor"), "Emotion & Farewell": ("vidaai", "reception", "couple_shoot")}
        assert e["moment"] in acts.get(e["act"], (e["moment"],))   # vows of the act's own ritual
    assert plan["duck_regions"] and plan["duck_regions"][0]["db"] == -18.0


def test_ends_with_the_song(wedding):
    m = _music()
    plan = story_planner.plan_teaser(wedding, target_duration=36.0, story_mode="wedding", beat_times=m["beat_times"], music_info=m)
    assert plan["music_out"]["how"] in ("song end", "bar", "fade")
    if plan["music_out"]["how"] == "song end":
        assert abs(plan["total_duration"] - 38.5) < 0.1 and plan["music_out"]["fade_seconds"] == 0.0
    else:
        assert min(abs(plan["total_duration"] - b) for b in m["downbeat_times"]) < 0.06   # ends on a bar
        assert plan["music_out"]["fade_seconds"] == 2.0


def test_no_music_keeps_old_behaviour(wedding):
    plan = story_planner.plan_teaser(wedding, target_duration=30.0, story_mode="wedding")
    assert plan["duck_regions"] == [] and plan["music_out"] is None
    assert not any(e.get("duck_music") for e in plan["clips"])


def test_premiere_levels_follow_the_vows(wedding, tmp_path, monkeypatch):
    from packages.core_premiere.premiere_adapter import premiere_adapter
    from packages.ai_engine import fcp_xml
    real = fcp_xml.media_info
    monkeypatch.setattr(fcp_xml, "media_info", lambda p: dict(real(p), audio=True))   # test clips have no sound track
    _speechify(wedding)
    m = _music()
    song = str(tmp_path / "song.wav")
    import soundfile as sf
    sf.write(song, np.zeros(22050 * 2, dtype=np.float32), 22050)
    plan = story_planner.plan_teaser(wedding, target_duration=36.0, story_mode="wedding", beat_times=m["beat_times"], music_info=m)
    plan["audio"] = song
    res = premiere_adapter.export_plan_to_premiere_xml(plan, output_dir=str(tmp_path))
    assert res["success"], res
    root = ET.parse(res["xml_path"]).getroot()
    values = [v.text for v in root.iter("value")]
    assert "0.126" in values                       # music ducked -18 dB under the vows
    levels = [c.find("filter/effect/parameter/value").text for c in root.iter("clipitem")
              if c.get("id", "").startswith("clipitem-a1-") and c.find("filter") is not None]
    assert "1.0" in levels and "0.1" in levels     # vows at full level, other camera sound soft
