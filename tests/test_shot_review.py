"""
Tests for STEP 3: A/B/C shot tiers (local and Smart AI), contact sheets, the review cache, credits only
for new reviews, score bookkeeping with the people bonus, and the planners leaving out rejected shots.
The Smart AI call is replaced by a fake, so these tests never use credits or the network.
"""

import json
import os
import sys
from types import SimpleNamespace

import numpy as np
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import packages.ai_engine.in_clip_trimmer as ict  # noqa: E402
from packages.ai_engine.story_planner import drop_rejects, story_planner  # noqa: E402


def _video(path, fps=25, seconds=6.0):
    import cv2
    out = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (160, 90))
    for i in range(int(fps * seconds)):
        f = np.full((90, 160, 3), (i * 4) % 255, dtype=np.uint8)
        out.write(f)
    out.release()


def _clip(path, ws=60.0, **kw):
    c = {"file_path": path, "duration": 6.0, "native_fps": 25.0, "total_frames": 150, "in_frame": 25, "out_frame": 100,
         "in_seconds": 1.0, "out_seconds": 4.0, "full_in_seconds": 0.5, "full_out_seconds": 5.5,
         "action_start_seconds": 0.5, "action_end_seconds": 5.5, "action_peak_seconds": 3.0,
         "quality_score": ws, "wedding_score": ws, "shot_type": "mid", "camera_move": "static",
         "faces": {"count": 1, "eyes_open": 0.9, "biggest": 0.2}}
    c.update(kw)
    return c


# ---------------------------------------------------------------- local tiers

def test_local_tiers():
    assert ict.local_tier(_clip("a", 85))[0] == "A"
    assert ict.local_tier(_clip("a", 60))[0] == "B"
    assert ict.local_tier(_clip("a", 30))[0] == "C"
    assert ict.local_tier(_clip("a", 55, camera_move="shaky"))[0] == "C"
    closed = _clip("a", 80, shot_type="close_up", faces={"count": 1, "eyes_open": 0.05, "biggest": 0.35})
    assert ict.local_tier(closed) == ("C", "eyes look closed")
    assert ict.local_tier(_clip("a", 30, is_still=True))[0] == "B"


def test_scores_combine_people_and_tier_without_adding_up():
    c = _clip("a", 80)
    ict.assign_local_tiers([c])
    assert c["tier"] == "A" and c["quality_score"] == 90.0
    c["people_bonus"] = 8.0
    ict.recompute_scores(c)
    ict.recompute_scores(c)
    assert c["quality_score"] == 98.0 and c["quality_score_base"] == 80.0
    c["tier"] = "C"
    ict.recompute_scores(c)
    assert c["quality_score"] == 80.0 - 25 + 8


def test_people_and_tiers_together():
    from packages.ai_engine import people
    c = _clip("/w/a.mp4", 70)
    ict.assign_local_tiers([c])                       # B: no tier bonus
    store = {"persons": [{"id": "p1", "role": "bride", "clips": ["/w/a.mp4"]}], "auto_couple": []}
    people.apply_people([c], store)
    assert c["quality_score"] == 78.0
    c["tier"] = "A"
    people.apply_people([c], store)
    assert c["quality_score"] == 88.0
    people.apply_people([c], None)
    assert c["quality_score"] == 80.0               # tier stays, people bonus gone


# ---------------------------------------------------------------- planners

def test_drop_rejects_keeps_order_and_fills():
    clips = [{"id": i, "tier": t, "quality_score": q} for i, (t, q) in enumerate([("A", 90), ("C", 40), ("B", 60), ("C", 50)])]
    assert [c["id"] for c in drop_rejects(clips, 2)] == [0, 2]
    assert [c["id"] for c in drop_rejects(clips, 3)] == [0, 2, 3]   # best reject added back, order kept


def test_teaser_leaves_out_rejects(tmp_path):
    clips = []
    for i in range(24):
        p = str(tmp_path / f"c{i}.mp4")
        clips.append(_clip(p, 60, tier="C" if i % 4 == 0 else "B", rec_start=i * 60.0))
    plan = story_planner.plan_teaser(clips, target_duration=20.0)
    rejected = {c["file_path"] for c in clips if c["tier"] == "C"}
    assert plan["clips"] and not any(c["file_path"] in rejected for c in plan["clips"])


# ---------------------------------------------------------------- contact sheet + cache key

def test_contact_sheet(tmp_path):
    p = str(tmp_path / "a.mp4")
    _video(p)
    jpeg, times = ict.contact_sheet(_clip(p))
    assert jpeg and jpeg[:2] == b"\xff\xd8"
    assert len(times) == 6 and 0.5 <= times[0] < times[-1] <= 5.5


def test_review_key_depends_on_window(tmp_path):
    p = str(tmp_path / "a.mp4")
    _video(p)
    a = ict.review_cache_key(_clip(p))
    b = ict.review_cache_key(_clip(p, full_in_seconds=1.0))
    assert a and b and a != b


# ---------------------------------------------------------------- Smart AI client parsing

def test_review_segments_parses_and_filters(monkeypatch):
    from packages.ai_engine.cloud_ai_client import CloudAIClient
    payload = {"segments": [
        {"segment_id": 0, "tier": "A", "moment": "varmala", "people": "couple", "emotion": 140, "action": "garland",
         "note": "hero", "hero_frame_time": 2.5},
        {"segment_id": 7, "tier": "B", "moment": "x", "people": "", "emotion": 10, "action": "", "note": "", "hero_frame_time": 1},
    ], "story_note": "Open on the varmala."}
    fake = SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=json.dumps(payload))])
    client = CloudAIClient()
    monkeypatch.setattr(client, "_create", lambda **kw: fake)
    res = client.review_segments([{"id": 0, "jpeg": b"\xff\xd8x", "times": [1.0, 2.0], "hint": "varmala"}])
    assert list(res["segments"]) == [0] and res["segments"][0]["emotion"] == 100
    assert res["story_note"] == "Open on the varmala."


# ---------------------------------------------------------------- backend flow (fake Smart AI)

@pytest.fixture
def backend(tmp_path, monkeypatch):
    b = pytest.importorskip("packages.desktop_app.backend")
    monkeypatch.setattr(ict.in_clip_trimmer, "cache_dir", str(tmp_path / "cache"))
    credits = []
    monkeypatch.setattr(b, "_use_credit", lambda job_id, name: credits.append(name) or True)
    b._credits = credits
    return b


def test_ai_review_applies_tiers_caches_and_charges_once(backend, tmp_path, monkeypatch):
    clips = []
    for i in range(3):
        p = str(tmp_path / f"c{i}.mp4")
        _video(p, seconds=6.0 + i)       # different files
        clips.append(_clip(p, 60 + i))
    calls = []

    def fake_review(segs, context=""):
        calls.append(len(segs))
        return {"segments": {s["id"]: {"segment_id": s["id"], "tier": "A" if s["id"] == 0 else "C", "moment": "haldi",
                                        "people": "bride", "emotion": 80, "action": "laughing", "note": "great",
                                        "hero_frame_time": 2.2} for s in segs}, "story_note": "Start with haldi."}

    monkeypatch.setattr(backend.cloud_ai_client, "review_segments", fake_review)
    job = backend._new_job()
    used, note = backend.ai_review_clips(job, clips, "", "Test")
    assert used and note == "Start with haldi." and calls == [3] and len(backend._credits) == 1
    tiers = sorted(c["tier"] for c in clips)
    assert tiers == ["A", "C", "C"] and all(c["tier_source"] == "ai" for c in clips)
    assert all(c["action_peak_seconds"] == 2.2 for c in clips)
    assert os.listdir(tmp_path / "cache" / "review")

    # second run: everything comes from the cache, no request, no credit
    fresh = [_clip(c["file_path"], c["wedding_score_base"]) for c in clips]
    used2, _ = backend.ai_review_clips(job, fresh, "", "Test")
    assert not used2 and calls == [3] and len(backend._credits) == 1
    assert sorted(c["tier"] for c in fresh) == ["A", "C", "C"]


def test_ai_review_failure_keeps_local_and_no_credit(backend, tmp_path, monkeypatch):
    p = str(tmp_path / "c.mp4")
    _video(p)
    clips = [_clip(p, 85)]

    def boom(segs, context=""):
        raise RuntimeError("offline")

    monkeypatch.setattr(backend.cloud_ai_client, "review_segments", boom)
    used, _ = backend.ai_review_clips(backend._new_job(), clips, "", "Test")
    assert not used and not backend._credits
    assert clips[0]["tier"] == "A" and clips[0]["tier_source"] == "local"
