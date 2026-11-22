"""
Tests for the Wedding Story Director (STEP 4, part 1): Edit Plan + Assembler + "why chosen" per cut,
in plan_teaser / plan_highlight with story_mode="wedding". Without story_mode the classic planner runs.
"""

import os
import sys
import xml.etree.ElementTree as ET

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from packages.ai_engine import story_planner as sp  # noqa: E402
from packages.ai_engine.story_planner import story_planner  # noqa: E402
from tests.test_wedding_regression import _make_video  # noqa: E402

# shoot order: getting ready -> haldi -> mehndi -> sangeet -> baraat -> varmala -> pheras -> sindoor -> vidaai
SHOOT = [("getting_ready", "bride", "close_up"), ("getting_ready", "groom", "mid"), ("decor", None, "detail"),
         ("drone", None, "extreme_wide"), ("haldi", "bride", "mid"), ("haldi", "bride", "mid"), ("haldi", "groom", "close_up"),
         ("mehndi", "bride", "close_up"), ("mehndi", None, "detail"), ("sangeet", "couple", "wide"), ("sangeet", None, "wide"),
         ("baraat", "groom", "wide"), ("baraat", "groom", "mid"), ("varmala", "couple", "mid"), ("varmala", "couple", "close_up"),
         ("pheras", "couple", "wide"), ("pheras", "couple", "close_up"), ("sindoor", "couple", "close_up"),
         ("vidaai", "bride", "close_up"), ("vidaai", "couple", "mid"), ("couple_shoot", "couple", "close_up")]
ORDER = ["getting_ready", "decor", "drone", "haldi", "mehndi", "sangeet", "baraat", "varmala", "pheras", "sindoor",
         "vidaai", "couple_shoot", "reception"]


@pytest.fixture(scope="module")
def wedding(tmp_path_factory):
    folder = tmp_path_factory.mktemp("story")
    clips = []
    for i, (moment, who, shot) in enumerate(SHOOT):
        fps = 50 if i % 3 == 0 else 25
        path = str(folder / f"{i:02d}_{moment}.mp4")
        _make_video(path, fps, seconds=8.0)
        roles = {"couple": ["couple"], "bride": ["bride"], "groom": ["groom"]}.get(who, [])
        clips.append({
            "file_path": path, "duration": 8.0, "native_fps": float(fps), "total_frames": 8 * fps,
            "in_frame": fps, "out_frame": 4 * fps, "in_seconds": 1.0, "out_seconds": 4.0,
            "action_start_seconds": 0.5, "action_end_seconds": 7.5, "action_peak_seconds": 4.0,
            "full_in_seconds": 0.5, "full_out_seconds": 7.5, "quality_score": 60.0 + (i * 11) % 30,
            "shot_type": shot, "moment": moment, "moment_source": "folder", "people_roles": roles,
            "people": ["p1"] if who == "bride" else ["p2"] if who == "groom" else ["p1", "p2"] if who == "couple" else [],
            "couple_in_shot": who in ("couple", "bride", "groom"),
            "tier": "C" if i == 5 else ("A" if i in (14, 17, 20) else "B"), "tier_note": "", "rec_start": i * 600.0,
        })
    return clips


def _teaser(clips, **kw):
    return story_planner.plan_teaser(clips, story_mode="wedding", **kw)


def test_names_and_bounds(wedding):
    t = _teaser(wedding, target_duration=30.0)
    h = story_planner.plan_highlight(wedding, story_mode="wedding", target_duration=90.0)
    assert t["sequence_name"] == "01_Wedding_Teaser_30s" and _teaser(wedding)["sequence_name"] == "01_Wedding_Teaser_60s"
    assert h["sequence_name"] == "02_Cinematic_Highlight_1.5min"
    assert story_planner.plan_highlight(wedding, story_mode="wedding")["sequence_name"] == "02_Cinematic_Highlight_4min"
    by = {c["file_path"]: c for c in wedding}
    for plan in (t, h):
        assert plan["story_director"] and plan["edit_plan"]["acts"]
        paths = [e["file_path"] for e in plan["clips"]]
        assert len(paths) == len(set(paths))
        for e in plan["clips"]:
            src = by[e["file_path"]]
            assert 0 <= e["in_frame"] < e["out_frame"] <= src["total_frames"] + 1
            assert e["why"]
        assert plan["total_duration"] <= (30.0 if plan is t else 90.0) + 4.0


def test_hook_and_hero_ending(wedding):
    t = _teaser(wedding, target_duration=30.0)
    first, last = t["clips"][0], t["clips"][-1]
    assert first["why"].startswith("Hook") and first["tier"] == "A"
    assert last["why"].startswith("Hero ending") and "couple" in last["why"]
    assert first["file_path"] != last["file_path"]


def test_rituals_follow_the_wedding_order(wedding):
    t = _teaser(wedding, target_duration=40.0)
    body = [e["moment"] for e in t["clips"][1:-1] if e["moment"] in ORDER]
    acts = []
    for m in body:
        act = next(i for i, (_, _, ms, _, _) in enumerate(sp.STORY_TEMPLATES["teaser"]) if m in ms)
        acts.append(act)
    assert acts == sorted(acts), body


def test_no_same_person_same_framing_back_to_back(wedding):
    by = {c["file_path"]: c for c in wedding}
    for plan in (_teaser(wedding, target_duration=40.0), story_planner.plan_highlight(wedding, story_mode="wedding", target_duration=90.0)):
        seq = [by[e["file_path"]] for e in plan["clips"]]
        for a, b in zip(seq, seq[1:]):
            same_person = sp.person_key(a) and sp.person_key(a) == sp.person_key(b)
            assert not (same_person and a["shot_type"] == b["shot_type"]), (a["file_path"], b["file_path"])


def test_rejects_left_out(wedding):
    rejected = next(c["file_path"] for c in wedding if c["tier"] == "C")
    assert all(e["file_path"] != rejected for e in _teaser(wedding, target_duration=30.0)["clips"])


def test_slow_motion_only_high_fps(wedding):
    fps = {c["file_path"]: c["native_fps"] for c in wedding}
    plan = story_planner.plan_highlight(wedding, story_mode="wedding", target_duration=90.0)
    assert all(fps[e["file_path"]] >= 48 for e in plan["clips"] if e["speed"] < 1.0)


def test_markers_carry_why(wedding):
    t = _teaser(wedding, target_duration=30.0)
    assert len(t["markers"]) == len(t["clips"])
    assert all(m["note"] and m["seconds"] == e["timeline_start"] for m, e in zip(t["markers"], t["clips"]))


def test_smart_ai_edit_plan_is_used(wedding):
    plan = {"acts": [{"name": "Hook", "seconds": 2, "moments": [], "mood": "hook", "cut": [1.8, 2.2]},
                     {"name": "Only vows", "seconds": 20, "moments": ["varmala", "pheras", "sindoor", "made_up"],
                      "mood": "emotion", "cut": [1.5, 2.5]}],
            "director_note": "All about the vows", "source": "smart_ai"}
    t = _teaser(wedding, target_duration=30.0, edit_plan=plan)
    assert t["edit_plan"]["source"] == "smart_ai"
    assert t["edit_plan"]["acts"][1]["moments"] == ["varmala", "pheras", "sindoor"]   # unknown moment dropped
    assert abs(sum(a["seconds"] for a in t["edit_plan"]["acts"]) - 30.0) < 0.1          # scaled to the length
    vows = [e for e in t["clips"] if e.get("act") == "Only vows"]
    assert vows and all(e["moment"] in ("varmala", "pheras", "sindoor") for e in vows), [e["moment"] for e in vows]


def test_bride_groom_balance(wedding):
    t = story_planner.plan_highlight(wedding, story_mode="wedding", target_duration=90.0)
    by = {c["file_path"]: c for c in wedding}
    b = sum(e["duration"] for e in t["clips"] if sp.person_key(by[e["file_path"]]) == "bride")
    g = sum(e["duration"] for e in t["clips"] if sp.person_key(by[e["file_path"]]) == "groom")
    assert b > 0 and g > 0


def test_classic_planner_unchanged_without_story_mode(wedding):
    t = story_planner.plan_teaser(wedding, target_duration=30.0)
    assert "story_director" not in t and "markers" not in t


def test_premiere_xml_gets_markers(wedding, tmp_path):
    from packages.core_premiere.premiere_adapter import premiere_adapter
    t = _teaser(wedding, target_duration=20.0)
    res = premiere_adapter.export_plan_to_premiere_xml(t, output_dir=str(tmp_path))
    assert res["success"], res
    root = ET.parse(res["xml_path"]).getroot()
    markers = root.findall("./sequence/marker")
    assert len(markers) == len(t["markers"]) and markers[0].find("comment").text


# ---------------------------------------------------------------- STEP 4b: Reel and Same-Day Edit

def test_reel_plan(wedding):
    for i, c in enumerate(wedding):
        c["faces"] = {"count": 1, "boxes": [[0.1 + 0.02 * i, 0.2, 0.1, 0.2]]} if i % 2 else {"count": 0, "boxes": []}
    r = story_planner.plan_reel(wedding)
    t = _teaser(wedding, target_duration=30.0)
    assert r["sequence_name"] == "04_Instagram_Reel_30s_9x16" and r["aspect_ratio"].startswith("9:16")
    assert r["total_duration"] <= 34.0 and r["clips"][0]["why"].startswith("Hook")
    for e in r["clips"]:
        assert 0 <= e["reframe"]["x"] <= 1 and 0 <= e["reframe"]["y"] <= 1
    assert any(e["reframe"]["source"] == "face" for e in r["clips"])
    avg = lambda p: sum(e["duration"] for e in p["clips"][1:-1]) / max(1, len(p["clips"]) - 2)
    assert avg(r) <= avg(t) + 0.05          # reels cut at least as fast as the teaser


def test_same_day_edit_plan(wedding):
    p = story_planner.plan_same_day_edit(wedding)
    assert p["sequence_name"] == "05_Same_Day_Edit_2.5min" and p["story_director"]
    assert p["clips"] and p["markers"]
    paths = [e["file_path"] for e in p["clips"]]
    assert len(paths) == len(set(paths))


def test_light_analysis_has_its_own_cache(tmp_path):
    import packages.ai_engine.in_clip_trimmer as ict
    clip = str(tmp_path / "a.mp4")
    _make_video(clip, 25, seconds=4.0)
    full = ict.InClipTrimmer(cache_dir=str(tmp_path / "c"))
    light = ict.InClipTrimmer(sample_fps=4, cache_dir=str(tmp_path / "c"))
    full.analyze_clip(clip)
    assert not light.analyze_clip(clip).get("from_cache")   # light run does not reuse (or overwrite) the full one
    assert full.analyze_clip(clip).get("from_cache")
