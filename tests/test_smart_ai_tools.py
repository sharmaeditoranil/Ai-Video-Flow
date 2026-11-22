"""
Tests for the Smart AI agent tools that expose the whole wedding engine (Claude-Code-style editing):
director_auto_edit (Story Director draft), director_build_timeline with live sound and notes,
director_people, director_vows, and the tool list itself. No cloud calls.
"""

import json
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tests.test_story_director import wedding  # noqa: E402,F401  (fixture)


@pytest.fixture
def project(wedding, tmp_path, monkeypatch):
    b = pytest.importorskip("packages.desktop_app.backend")
    monkeypatch.setattr(b, "OUTPUT_DIR", str(tmp_path))
    data = {"folder": os.path.dirname(wedding[0]["file_path"]), "project_name": "Test", "mode": "wedding",
            "audio": "", "analyzed": [dict(c) for c in wedding], "plans": {}, "target_nle": "resolve"}
    (tmp_path / "last_plan.json").write_text(json.dumps(data))
    return b


def test_tool_list_is_complete_and_valid(project):
    tools = project._assistant_tools()
    names = [t["name"] for t in tools]
    assert len(names) == len(set(names))
    for n in ("director_auto_edit", "director_people", "director_vows", "director_build_timeline", "director_view_clips"):
        assert n in names
    assert all(t["input_schema"].get("type") == "object" for t in tools)
    assert "director_auto_edit" in project.DIRECTOR_GUIDE


@pytest.mark.parametrize("fmt", ["teaser", "highlight", "reel", "same_day"])
def test_auto_edit_builds_a_story_director_draft(project, fmt):
    r = project.director_call("director_auto_edit", {"format": fmt, "seconds": 30, "editor": "none"})
    assert r["cuts"] and all(c["why"] for c in r["cuts"]) and r["cuts"][0]["why"].startswith("Hook")
    assert all(isinstance(c["clip"], int) for c in r["cuts"])
    tl = project.director_call("director_get_timeline", {"key": r["key"]})
    assert len(tl["shots"]) == len(r["cuts"])


def test_auto_edit_with_own_acts(project):
    acts = [{"name": "Hook", "seconds": 2, "mood": "hook"},
            {"name": "Vows only", "seconds": 20, "moments": ["varmala", "pheras", "sindoor"], "mood": "emotion",
             "cut_min": 1.5, "cut_max": 2.5}]
    r = project.director_call("director_auto_edit", {"format": "teaser", "seconds": 24, "acts": acts, "editor": "none"})
    assert [a for a, _ in r["acts"]] == ["Hook", "Vows only"]


def test_build_timeline_live_sound_and_notes(project):
    cuts = [{"clip": 1, "in": 1.0, "out": 3.0, "speed_percent": 50, "note": "hero close-up"},
            {"clip": 15, "in": 2.0, "out": 6.0, "speed_percent": 50, "live_sound": True, "note": "phere vow line"}]
    r = project.director_call("director_build_timeline", {"key": "vows_cut", "cuts": cuts, "editor": "none"})
    assert r["shots"] == 2 and any("live sound" in p for p in r["skipped"])
    _, data = project._dir_project()
    plan = data["plans"]["vows_cut"]
    vow = plan["clips"][1]
    assert vow["keep_audio"] and vow["duck_music"] and vow["speed_percent"] == 100 and vow["duration"] == 4.0
    assert plan["duck_regions"] == [{"start": vow["timeline_start"], "end": vow["timeline_end"], "db": -18.0}]
    assert [m["note"] for m in plan["markers"]] == ["hero close-up", "phere vow line"]
    assert plan["total_duration"] == 8.0      # 2 s at 50% = 4 s, then 4 s of live vows


def test_people_tool_without_face_data(project):
    r = project.director_call("director_people", {})
    assert r["consent"] is False and r["persons"] == []


def test_vows_tool_lists_lines(project, monkeypatch):
    path, data = project._dir_project()
    data["analyzed"][15]["transcript"] = {"lines": [{"text": "Main hamesha saath rahungi", "start": 2.0, "end": 4.0}]}
    data["analyzed"][15]["vow_line"] = {"text": "Main hamesha saath rahungi", "start": 2.0, "end": 4.0, "rank": 0}
    project._save_project_inplace(path, data)
    r = project.director_call("director_vows", {"action": "list"})
    assert r["clips"][0]["clip"] == 15 and r["clips"][0]["chosen"]["text"] == "Main hamesha saath rahungi"
    monkeypatch.setattr(project, "find_vow_lines", lambda job, analyzed, premium, instr="": 0)
    assert project.director_call("director_vows", {"action": "find"})["chosen"] == 0
