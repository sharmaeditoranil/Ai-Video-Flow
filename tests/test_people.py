"""
Tests for people recognition (STEP 2): grouping faces into people, keeping names across scans,
matching uploaded bride / groom photos, preferring couple shots, deleting all face data, and the
checksum-verified model download.
"""

import hashlib
import os
import sys

import numpy as np
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from packages.ai_engine import people  # noqa: E402

RNG = np.random.default_rng(7)


def _unit(v):
    return (v / np.linalg.norm(v)).astype(np.float32)


IDENTITIES = {name: _unit(RNG.normal(size=128)) for name in ("bride", "groom", "mother", "guest")}


def _face(name, clip, size=0.2, noise=0.25):
    emb = _unit(IDENTITIES[name] + RNG.normal(scale=noise / np.sqrt(128), size=128) * np.sqrt(128) * 0.05)
    img = np.zeros((90, 160, 3), dtype=np.uint8)
    row = np.array([60, 20, 30, 40] + [0] * 10 + [0.95], dtype=np.float32)
    return {"clip": clip, "emb": emb, "size": size, "quality": size, "img": img, "row": row}


def _faces():
    out = []
    for i in range(6):
        out.append(_face("bride", f"/w/c{i}.mp4", 0.3))
    for i in range(3, 9):
        out.append(_face("groom", f"/w/c{i}.mp4", 0.25))
    out += [_face("mother", "/w/c2.mp4", 0.15), _face("mother", "/w/c9.mp4", 0.15)]
    out += [_face("guest", "/w/c10.mp4", 0.05)]
    return out


def test_identities_are_distinct():
    sims = [float(np.dot(IDENTITIES[a], IDENTITIES[b])) for a in IDENTITIES for b in IDENTITIES if a < b]
    assert max(sims) < people.SAME_PERSON


def test_clustering_groups_people():
    faces = _faces()
    labels = people.cluster_embeddings([f["emb"] for f in faces])
    assert len(set(labels)) == 4
    assert len(set(labels[:6])) == 1 and len(set(labels[6:12])) == 1


def test_build_store_orders_and_guesses_couple():
    store = people.build_store("/w", _faces())
    persons = store["persons"]
    assert [len(p["clips"]) for p in persons][:2] == [6, 6]
    assert set(store["auto_couple"]) == {persons[0]["id"], persons[1]["id"]}
    assert all(p["thumb"].startswith("data:image/jpeg") for p in persons)
    assert "emb" not in str(people.public_view(store)) and "centroid" not in str(people.public_view(store))


def test_roles_carry_over_to_a_new_scan():
    first = people.build_store("/w", _faces())
    bride_id = next(p["id"] for p in first["persons"] if "/w/c0.mp4" in p["clips"])
    people.set_role(first, bride_id, "bride", "Annu")
    second = people.build_store("/w", _faces(), previous=first)
    bride = next(p for p in second["persons"] if p.get("role") == "bride")
    assert "/w/c0.mp4" in bride["clips"] and bride["name"] == "Annu"
    assert second["auto_couple"] == []           # named couple: no guessing any more


def test_only_one_bride():
    store = people.build_store("/w", _faces())
    a, b = store["persons"][0]["id"], store["persons"][1]["id"]
    people.set_role(store, a, "bride")
    people.set_role(store, b, "bride")
    assert [p["id"] for p in store["persons"] if p.get("role") == "bride"] == [b]


def test_reference_photo_matches_person():
    store = people.build_store("/w", _faces())
    people.add_reference(store, "groom", IDENTITIES["groom"], "data:image/jpeg;base64,x")
    groom = next(p for p in store["persons"] if p.get("role") == "groom")
    assert groom["role_from"] == "photo" and "/w/c8.mp4" in groom["clips"]
    # a hand-picked role is never overridden by a photo
    other = next(p for p in store["persons"] if "/w/c0.mp4" in p["clips"])
    people.set_role(store, other["id"], "bride")
    people.add_reference(store, "bride", IDENTITIES["mother"], "data:image/jpeg;base64,y")
    assert other["role"] == "bride" and other["role_from"] == "hand"
    # removing the photo removes the role it gave
    people.remove_references(store, "groom")
    assert groom.get("role") is None


def test_reference_without_match_does_nothing():
    store = people.build_store("/w", _faces())
    stranger = _unit(RNG.normal(size=128))
    assert people.match_references({**store, "references": [{"role": "bride", "emb": stranger.tolist()}]}) == []


def test_apply_people_bonus_is_not_cumulative():
    store = people.build_store("/w", _faces())
    clips = [{"file_path": f"/w/c{i}.mp4", "quality_score": 70.0, "wedding_score": 60.0} for i in range(11)]
    people.apply_people(clips, store)
    once = [c["quality_score"] for c in clips]
    people.apply_people(clips, store)
    assert [c["quality_score"] for c in clips] == once
    assert clips[0]["couple_in_shot"] and clips[0]["quality_score"] == 70.0 + people.COUPLE_BONUS
    assert clips[10]["quality_score"] == 70.0     # unnamed guest: no change
    people.apply_people(clips, None)             # face data deleted: back to the originals
    assert all(c["quality_score"] == 70.0 and "people" not in c for c in clips)


def test_store_save_load_delete(tmp_path):
    store = people.build_store("/Users/x/Wedding", _faces())
    people.save_store(store, base=str(tmp_path))
    assert people.load_store("/Users/x/Wedding", base=str(tmp_path))["persons"]
    assert people.delete_store("/Users/x/Wedding", base=str(tmp_path))
    assert people.load_store("/Users/x/Wedding", base=str(tmp_path)) is None


def test_ensure_model_checks_sha(tmp_path):
    src = tmp_path / "model.onnx"
    src.write_bytes(b"model-bytes")
    good = {"file": "m.onnx", "sha256": hashlib.sha256(b"model-bytes").hexdigest(), "size": 11,
            "urls": ["file:///nonexistent/m.onnx", src.as_uri()]}
    path = people.ensure_model(good, folder=str(tmp_path / "models"))
    assert open(path, "rb").read() == b"model-bytes"
    bad = dict(good, file="b.onnx", sha256="0" * 64, urls=[src.as_uri()])
    with pytest.raises(people.PeopleError):
        people.ensure_model(bad, folder=str(tmp_path / "models"))
    assert not os.path.exists(tmp_path / "models" / "b.onnx")


def _sface_path():
    for p in (os.path.join(people.models_dir(), people.SFACE["file"]), os.environ.get("AVF_SFACE_MODEL", "")):
        if p and os.path.isfile(p):
            return p
    return None


def test_real_photo_embedding():
    model = _sface_path()
    photo = os.path.join(ROOT, "website", "assets", "anil-sharma-mentor.webp")
    if not model or not os.path.isfile(photo):
        pytest.skip("SFace model or sample photo not present")
    emb, thumb = people.face_from_photo(photo, model)
    assert emb.shape == (128,) and abs(float(np.linalg.norm(emb)) - 1.0) < 1e-3
    assert thumb.startswith("data:image/jpeg")
