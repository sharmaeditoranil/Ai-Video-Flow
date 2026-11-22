"""
Regression tests for the wedding edit pipeline (STEP 0 safety net).

They build teaser / highlight / full-film plans from a small fake "analyzed" list backed by tiny
generated video files, then check what must never break: timeline names, in/out points inside each
source clip, durations, slow motion only on high-fps footage, the Premiere / EDIUS XML exports, music
drop detection and the Smart AI Director's timeline builder.

Run:  python -m pytest tests -q
"""

import json
import os
import sys
import xml.etree.ElementTree as ET

import numpy as np
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from packages.ai_engine.story_planner import story_planner, retime_for, parse_prompt_directives  # noqa: E402
from packages.ai_engine.beat_analyzer import find_energy_drops  # noqa: E402

CLIP_SECONDS = 8.0
NAMES = ["bride_portrait", "haldi_dance", "mehndi_hands", "baraat_dhol", "varmala_stage", "phera_mandap",
         "venue_drone", "couple_glance", "sangeet_party", "bidaai_emotion", "groom_entry", "decor_details"]


def _make_video(path: str, fps: int, seconds: float = CLIP_SECONDS) -> None:
    import cv2
    w, h = 64, 36
    out = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    for i in range(int(fps * seconds)):
        frame = np.full((h, w, 3), (i * 3) % 255, dtype=np.uint8)
        out.write(frame)
    out.release()


def _clip(path: str, fps: float, i: int) -> dict:
    total = int(fps * CLIP_SECONDS)
    return {
        "file_path": path, "duration": CLIP_SECONDS, "native_fps": fps, "total_frames": total,
        "in_frame": int(1.0 * fps), "out_frame": int(4.0 * fps), "in_seconds": 1.0, "out_seconds": 4.0,
        "action_start_seconds": 0.5, "action_end_seconds": 7.5, "action_peak_seconds": 4.0,
        "full_in_seconds": 0.5, "full_out_seconds": 7.5, "full_duration": 7.0,
        "quality_score": 60.0 + (i * 7) % 35, "shot_type": ["wide", "mid", "close_up"][i % 3],
        "camera": f"Cam 0{1 + i % 2}", "rec_start": 1_700_000_000.0 + i * 600, "abs_start": 1_700_000_000.0 + i * 600,
        "flash_times": [],
    }


@pytest.fixture(scope="module")
def footage(tmp_path_factory):
    """12 clips: even ones shot at 25 fps, odd ones at 50 fps."""
    folder = tmp_path_factory.mktemp("wedding")
    clips = []
    for i, name in enumerate(NAMES):
        fps = 50 if i % 2 else 25
        path = str(folder / f"{name}.mp4")
        _make_video(path, fps)
        clips.append(_clip(path, float(fps), i))
    return clips


def _check_inside_sources(plan: dict, clips: list) -> None:
    by_path = {c["file_path"]: c for c in clips}
    assert plan["clips"], "plan has no clips"
    for c in plan["clips"]:
        src = by_path[c["file_path"]]
        assert 0 <= c["in_frame"] < c["out_frame"] <= src["total_frames"] + 1, c
        assert c["duration"] > 0


# ---------------------------------------------------------------- timelines

def test_teaser_name_and_bounds(footage):
    plan = story_planner.plan_teaser(footage, target_duration=60.0)
    assert plan["sequence_name"] == "01_Wedding_Teaser_60s"
    _check_inside_sources(plan, footage)
    assert plan["total_duration"] <= 60.0 + 4.0


def test_highlight_name_and_bounds(footage):
    plan = story_planner.plan_highlight(footage, target_duration=240.0)
    assert plan["sequence_name"] == "02_Cinematic_Highlight_4min"
    _check_inside_sources(plan, footage)


def test_full_film_name_and_bounds(footage):
    plan = story_planner.plan_full_film(footage)
    assert plan["sequence_name"] == "03_Full_Wedding_Film"
    _check_inside_sources(plan, footage)


def test_each_clip_used_once_in_teaser(footage):
    plan = story_planner.plan_teaser(footage, target_duration=30.0)
    paths = [c["file_path"] for c in plan["clips"]]
    assert len(paths) == len(set(paths))


# ---------------------------------------------------------------- slow motion

def test_retime_rules():
    assert retime_for(True, False, 25.0) == (1.0, "Normal Speed")
    assert retime_for(True, False, 30.0)[0] == 1.0
    assert retime_for(True, False, 50.0)[0] == 0.5
    assert retime_for(True, False, 120.0)[0] == 0.5
    assert retime_for(True, False, 25.0, gentle_slow=True)[0] == 0.8
    assert retime_for(False, True, 25.0)[0] == 1.3
    assert retime_for(False, False, 60.0)[0] == 1.0
    assert parse_prompt_directives("thoda slow karo").get("gentle_slow_motion")


@pytest.mark.parametrize("planner", ["teaser", "highlight"])
def test_no_half_speed_on_low_fps(footage, planner):
    plan = (story_planner.plan_teaser(footage, target_duration=60.0) if planner == "teaser"
            else story_planner.plan_highlight(footage, target_duration=240.0))
    fps_of = {c["file_path"]: c["native_fps"] for c in footage}
    for c in plan["clips"]:
        if c.get("speed", 1.0) < 1.0:
            assert fps_of[c["file_path"]] >= 48.0, f"slow motion on a {fps_of[c['file_path']]} fps clip: {c['clip_name']}"


def test_slow_motion_still_used_on_high_fps(footage):
    plan = story_planner.plan_teaser(footage, target_duration=60.0)
    assert any(c.get("speed") == 0.5 for c in plan["clips"]), "50 fps hero shots should still get slow motion"


# ---------------------------------------------------------------- exports

def test_premiere_xml(footage, tmp_path):
    from packages.core_premiere.premiere_adapter import premiere_adapter
    plan = story_planner.plan_teaser(footage, target_duration=30.0)
    res = premiere_adapter.export_plan_to_premiere_xml(plan, output_dir=str(tmp_path))
    assert res.get("success"), res
    root = ET.parse(res["xml_path"]).getroot()
    assert root.tag == "xmeml"
    assert len(root.findall(".//clipitem")) >= len(plan["clips"])


def test_edius_xml(footage, tmp_path):
    from packages.core_edius.edius_adapter import edius_adapter
    plan = story_planner.plan_teaser(footage, target_duration=30.0)
    out = str(tmp_path / "teaser_EDIUS.xml")
    res = edius_adapter.export_plan_to_edius_xml(plan=plan, output_xml_path=out, sequence_name=plan["sequence_name"])
    assert res.get("success"), res
    root = ET.parse(out).getroot()
    assert root.tag == "xmeml"
    assert root.findall(".//clipitem")


def test_plan_is_json_serialisable(footage):
    plan = story_planner.plan_highlight(footage, target_duration=120.0)
    json.loads(json.dumps(plan, default=str))


# ---------------------------------------------------------------- music

def test_energy_drops_found_across_the_song():
    step = 0.05
    t = np.arange(0, 120, step)
    e = np.full_like(t, 0.2)
    for start in (30.0, 75.0):  # two choruses
        e[(t >= start) & (t < start + 15)] = 1.0
    drops = find_energy_drops(t.tolist(), e.tolist())
    assert any(abs(d - 30.0) < 1.5 for d in drops), drops
    assert any(abs(d - 75.0) < 1.5 for d in drops), drops
    assert all(b - a >= 8.0 for a, b in zip(drops, drops[1:]))


def test_energy_drops_snap_to_beats():
    t = np.arange(0, 60, 0.05)
    e = np.where(t >= 20.2, 1.0, 0.1)
    beats = [i * 0.5 for i in range(120)]
    drops = find_energy_drops(t.tolist(), e.tolist(), beats)
    assert 20.0 in drops or 20.5 in drops, drops


def test_flat_song_has_no_drops():
    t = np.arange(0, 60, 0.05)
    assert find_energy_drops(t.tolist(), np.full_like(t, 0.5).tolist()) == []


# ---------------------------------------------------------------- Smart AI Director

def test_director_build_timeline(footage, tmp_path, monkeypatch):
    backend = pytest.importorskip("packages.desktop_app.backend")
    monkeypatch.setattr(backend, "OUTPUT_DIR", str(tmp_path))
    project = {"folder": os.path.dirname(footage[0]["file_path"]), "project_name": "Test Wedding", "mode": "wedding",
               "audio": "", "analyzed": footage, "plans": {}, "target_nle": "resolve"}
    (tmp_path / "last_plan.json").write_text(json.dumps(project))

    rows = backend.director_call("director_list_clips", {"limit": 5})
    assert rows["total"] == len(footage)

    cuts = [{"clip": 1, "in": 1.0, "out": 3.0, "speed_percent": 50},
            {"clip": 2, "in": 2.0, "out": 4.0},
            {"clip": 999, "in": 0, "out": 1}]
    res = backend.director_call("director_build_timeline", {"key": "teaser", "cuts": cuts, "editor": "none"})
    assert res["shots"] == 2
    assert res["skipped"] and "999" in res["skipped"][0]
    assert abs(res["duration"] - (4.0 + 2.0)) < 0.01  # 2 s at 50% = 4 s, plus 2 s

    tl = backend.director_call("director_get_timeline", {"key": "teaser"})
    assert [s["clip"] for s in tl["shots"]] == [1, 2]
    assert tl["shots"][0]["speed_percent"] == 50
