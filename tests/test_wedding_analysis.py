"""
Tests for the wedding shot understanding added to InClipTrimmer (STEP 1): shot size from faces,
ritual moments from folder names and colours, camera moves, sound type, the per-file cache and
event-based moment labelling.
"""

import os
import sys

import numpy as np
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import packages.ai_engine.in_clip_trimmer as ict  # noqa: E402


# ---------------------------------------------------------------- shot size

@pytest.mark.parametrize("face_h,count,bokeh,expected", [
    (0.45, 1, 0, "close_up"),
    (0.20, 2, 0, "mid"),
    (0.08, 3, 0, "wide"),
    (0.03, 12, 0, "extreme_wide"),
    (0.0, 0, 4.0, "detail"),
    (0.0, 0, 1.0, "wide"),
])
def test_shot_type_from_faces(face_h, count, bokeh, expected):
    assert ict.shot_type_from_faces(face_h, count, bokeh) == expected


# ---------------------------------------------------------------- moments

@pytest.mark.parametrize("path,expected", [
    ("/Shoot/Day2/Haldi/C0001.MP4", "haldi"),
    ("/Shoot/Mehndi/haldi_extra/C0002.MP4", "haldi"),          # deepest folder wins
    ("D:\\Wedding\\VARMALA\\cam1.mov", "varmala"),
    ("/Wedding/Saat Phere/A001.mp4", "pheras"),
    ("/Wedding/Bidai/A002.mp4", "vidaai"),
    ("/Wedding/DJI_0042.MP4", "drone"),
    ("/Wedding/During evening/A003.mp4", None),                # 'ring' inside a word is not a ring ceremony
    ("/Wedding/Cam 01/C8640.MP4", None),
])
def test_moment_from_path(path, expected):
    assert ict.moment_from_path(path) == expected


def _solid(bgr, h=90, w=160):
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:] = bgr
    return img


def test_visual_cues_and_haldi():
    yellow = _solid((0, 210, 240))          # BGR haldi yellow, bright
    cues = ict.visual_cues(yellow)
    assert cues["yellow"] > 0.9 and cues["brightness"] > 0.5
    assert ict.moment_from_visuals(cues, face_count=2, camera_move="static")[0] == "haldi"


def test_visual_night_stage_is_sangeet():
    img = _solid((10, 10, 10))
    img[:, :60] = (200, 40, 160)            # magenta / purple stage light on a dark frame
    cues = ict.visual_cues(img)
    assert ict.moment_from_visuals(cues, face_count=3, camera_move="pan")[0] == "sangeet"


# ---------------------------------------------------------------- camera move

def _rows(n=20, dx=0.0, dy=0.0, div=0.0, cfx=None, efx=None, stab=90.0):
    return [(i * 5, dx, dy, div, dx if cfx is None else cfx, dx if efx is None else efx, stab) for i in range(n)]


@pytest.mark.parametrize("rows,path,expected", [
    (_rows(), "a.mp4", "static"),
    (_rows(dx=2.0), "a.mp4", "pan"),
    (_rows(dy=-1.5), "a.mp4", "tilt"),
    (_rows(div=0.6), "a.mp4", "push_in"),
    (_rows(div=-0.6), "a.mp4", "pull_out"),
    (_rows(dx=0.2, cfx=1.0, efx=-1.0), "a.mp4", "orbit"),
    (_rows(dx=2.0, stab=30.0), "a.mp4", "shaky"),
    (_rows(dx=1.0), "/Wedding/Drone/DJI_0001.MP4", "drone"),
])
def test_camera_move(rows, path, expected):
    assert ict.classify_camera_move(rows, path) == expected


def test_flow_sample_reads_push_in():
    h, w = 90, 160
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    flow = np.dstack([(xs - w / 2) / (w / 2), (ys - h / 2) / (h / 2)]).astype(np.float32)  # expanding field
    s = ict._flow_sample(flow, 0, 90.0)
    assert s[3] > 0.3                          # positive divergence = moving in


# ---------------------------------------------------------------- sound

SR = 16000


def test_audio_silence():
    assert ict.classify_audio(np.zeros(SR * 3, dtype=np.float32), SR)["audio_type"] == "silence"


def test_audio_music_chord():
    t = np.arange(SR * 4) / SR
    y = 0.2 * (np.sin(2 * np.pi * 220 * t) + np.sin(2 * np.pi * 277 * t) + np.sin(2 * np.pi * 330 * t))
    assert ict.classify_audio(y.astype(np.float32), SR)["audio_type"] == "music"


def test_audio_crowd_noise():
    y = np.random.default_rng(1).normal(0, 0.3, SR * 4)
    assert ict.classify_audio(y.astype(np.float32), SR)["audio_type"] == "crowd"


def test_audio_speech_like():
    t = np.arange(SR * 4) / SR
    voice = sum(np.sin(2 * np.pi * f * t) / k for k, f in enumerate((140, 280, 420, 560), 1))
    syllables = (np.sin(2 * np.pi * 4.5 * t) > 0.1).astype(float)   # ~4.5 syllables per second with gaps
    y = 0.3 * voice * syllables
    assert ict.classify_audio(y.astype(np.float32), SR)["audio_type"] == "speech"


def test_audio_peaks_are_spaced():
    y = np.zeros(SR * 6, dtype=np.float32)
    for sec in (1, 4):
        y[sec * SR: sec * SR + SR // 4] = 0.8
    out = ict.classify_audio(y, SR)
    assert out["loudness_peaks"] and all(b - a >= 1.0 for a, b in zip(out["loudness_peaks"], out["loudness_peaks"][1:]))


# ---------------------------------------------------------------- faces

def test_no_faces_on_blank_frame():
    assert ict.detect_faces(np.zeros((360, 640, 3), dtype=np.uint8)) == []


def test_face_found_on_real_photo():
    photo = os.path.join(ROOT, "website", "assets", "anil-sharma-mentor.webp")
    if not os.path.isfile(photo) or not os.path.isfile(ict.YUNET_MODEL):
        pytest.skip("sample photo or face model not present")
    from PIL import Image
    import cv2
    img = cv2.cvtColor(np.array(Image.open(photo).convert("RGB")), cv2.COLOR_RGB2BGR)
    faces = ict.detect_faces(img)
    assert len(faces) == 1 and faces[0]["confidence"] > 0.8
    assert 0 <= faces[0]["eyes_open"] <= 1 and 0 <= faces[0]["smile"] <= 1


def test_wedding_score_prefers_sharp_hero():
    hero = [{"box": [0.4, 0.2, 0.2, 0.3], "sharpness": 90, "eyes_open": 1.0, "smile": 0.8}]
    blur = [{"box": [0.4, 0.2, 0.05, 0.06], "sharpness": 10, "eyes_open": 0.0, "smile": 0.0}]
    assert ict.wedding_score(80, hero, 1, "close_up", "push_in") > ict.wedding_score(80, blur, 1, "wide", "shaky")


# ---------------------------------------------------------------- full clip + cache

def _make_video(path, fps=25, seconds=4.0, with_text=True):
    import cv2
    w, h = 160, 90
    out = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    for i in range(int(fps * seconds)):
        frame = np.full((h, w, 3), (0, 200, 235), dtype=np.uint8)   # haldi-yellow scene
        cv2.circle(frame, (20 + i % 120, 45), 8, (40, 40, 40), -1)
        out.write(frame)
    out.release()


def test_analyze_clip_adds_fields_and_caches(tmp_path):
    folder = tmp_path / "Haldi"
    folder.mkdir()
    clip = str(folder / "C0001.mp4")
    _make_video(clip)
    t = ict.InClipTrimmer(cache_dir=str(tmp_path / "cache"))
    r1 = t.analyze_clip(clip)
    for k in ("faces", "shot_type", "camera_move", "audio_type", "moment", "wedding_score", "visual_cues"):
        assert k in r1, k
    assert r1["moment"] == "haldi" and r1["moment_source"] == "folder"
    assert "_motion" not in r1
    assert not r1.get("from_cache")
    r2 = t.analyze_clip(clip)
    assert r2.get("from_cache") and r2["wedding_score"] == r1["wedding_score"]
    # a changed file is analysed again
    _make_video(clip, seconds=5.0)
    assert not t.analyze_clip(clip).get("from_cache")


def test_analyze_without_cache_or_wedding(tmp_path):
    clip = str(tmp_path / "a.mp4")
    _make_video(clip)
    r = ict.InClipTrimmer(cache_dir=str(tmp_path / "c")).analyze_clip(clip, use_cache=False, wedding=False)
    assert "wedding_score" not in r and not os.path.exists(tmp_path / "c")


# ---------------------------------------------------------------- events

def test_label_moments_by_shoot_time():
    base = 1_700_000_000.0
    clips = [
        {"abs_start": base, "duration": 30, "moment": "haldi", "moment_source": "folder", "moment_confidence": 0.95},
        {"abs_start": base + 120, "duration": 30, "moment": None, "moment_source": "unknown", "moment_confidence": 0},
        {"abs_start": base + 300, "duration": 30, "moment": "mehndi", "moment_source": "visual", "moment_confidence": 0.45},
        {"abs_start": base + 7200, "duration": 30, "moment": "pheras", "moment_source": "folder", "moment_confidence": 0.95},
        {"abs_start": base + 7300, "duration": 30, "moment": "decor", "moment_source": "visual", "moment_confidence": 0.3},
    ]
    changed = ict.label_moments(clips)
    assert [c["moment"] for c in clips] == ["haldi", "haldi", "haldi", "pheras", "decor"]
    assert changed == 2
    assert clips[0]["event_id"] == clips[2]["event_id"] != clips[3]["event_id"]
