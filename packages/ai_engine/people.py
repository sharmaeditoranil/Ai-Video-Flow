"""
AI Video Flow - People recognition for wedding projects (STEP 2)

Groups the faces found in a project's clips into people, so the editor can name the Bride, the Groom
and the family once, and every clip then knows who is in it. The planners use that to prefer the couple.

Why a separate module: nothing else in ai_engine stores per-project personal data. Face embeddings are
biometric data (India DPDP Act 2023), so this module keeps them in one place, only after the editor turns
recognition on for the project, only on this computer, and deletes them with one call.

The face embedding model (OpenCV Zoo SFace, MIT licence, ~37 MB) is downloaded on first use and checked
against its SHA-256 before it is used.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import time
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger("ai_video_flow.people")

OUTPUT_DIR = os.path.join(os.path.expanduser("~"), "AI-Video-Flow-Output")

SFACE = {
    "file": "face_recognition_sface_2021dec.onnx",
    "sha256": "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79",
    "size": 38696353,
    # our own server first (fast in India, no rate limits), GitHub as the fallback; the checksum decides
    "urls": [
        "https://aivideoflow.in/downloads/models/face_recognition_sface_2021dec.onnx",
        "https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx",
    ],
}

ROLES = ["bride", "groom", "bride_mother", "bride_father", "groom_mother", "groom_father",
         "sibling", "relative", "friend", "guest"]
COUPLE = {"bride", "groom"}
FAMILY = {"bride_mother", "bride_father", "groom_mother", "groom_father", "sibling", "relative"}

SAME_PERSON = 0.40      # cosine similarity to join a person (SFace's own threshold is 0.363)
MERGE_PERSONS = 0.45    # two groups closer than this are the same person
COUPLE_BONUS = 8.0      # score points for shots with the couple
FAMILY_BONUS = 3.0
GUEST_ONLY_PENALTY = 4.0
STORE_VERSION = 1
MIN_FACE_HEIGHT = 0.06    # faces smaller than this (fraction of frame height) are not recognised
MIN_FACE_SHARPNESS = 100  # Laplacian variance of the face at 96 px; below this the face is too soft


class PeopleError(RuntimeError):
    pass


# ------------------------------------------------------------------ model download (first use)

def models_dir() -> str:
    return os.path.join(OUTPUT_DIR, ".models")


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure_model(spec: Dict[str, Any] = SFACE, folder: Optional[str] = None,
                 on_progress: Optional[Callable[[int], None]] = None) -> str:
    """Path to the model file, downloading it on first use. A file that does not match the SHA-256 is
    never used. Raises PeopleError when no source works (e.g. offline on first use)."""
    folder = folder or models_dir()
    target = os.path.join(folder, spec["file"])
    if os.path.isfile(target) and _sha256(target) == spec["sha256"]:
        return target
    os.makedirs(folder, exist_ok=True)
    part = target + ".part"
    errors = []
    for url in spec["urls"]:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "AIVideoFlow"})
            h = hashlib.sha256()
            done = 0
            with urllib.request.urlopen(req, timeout=60) as resp, open(part, "wb") as out:
                total = int(resp.headers.get("Content-Length") or spec.get("size") or 0)
                while True:
                    chunk = resp.read(1 << 18)
                    if not chunk:
                        break
                    out.write(chunk)
                    h.update(chunk)
                    done += len(chunk)
                    if on_progress and total:
                        on_progress(min(100, int(done * 100 / total)))
            if h.hexdigest() != spec["sha256"]:
                errors.append(f"{url}: checksum mismatch")
                os.remove(part)
                continue
            os.replace(part, target)
            return target
        except Exception as e:  # try the next source
            errors.append(f"{url}: {e}")
            try:
                os.remove(part)
            except OSError:
                pass
    raise PeopleError("Could not download the face model (needs internet once). " + "; ".join(errors)[:300])


# ------------------------------------------------------------------ per-project store

def store_path(folder: str, base: Optional[str] = None) -> str:
    key = hashlib.sha1(os.path.normpath(folder or "").lower().encode("utf-8")).hexdigest()[:16]
    return os.path.join(base or os.path.join(OUTPUT_DIR, "people"), f"{key}.json")


def load_store(folder: str, base: Optional[str] = None) -> Optional[Dict[str, Any]]:
    try:
        with open(store_path(folder, base), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def save_store(store: Dict[str, Any], base: Optional[str] = None) -> str:
    path = store_path(store["folder"], base)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(store, f)
    os.replace(tmp, path)
    return path


def delete_store(folder: str, base: Optional[str] = None) -> bool:
    """Removes every face embedding, face picture and name kept for this project."""
    try:
        os.remove(store_path(folder, base))
        return True
    except OSError:
        return False


# ------------------------------------------------------------------ faces -> embeddings

_REC: Dict[str, Any] = {}


def _recognizer(model_path: str):
    import cv2
    rec = _REC.get(model_path)
    if rec is None:
        rec = cv2.FaceRecognizerSF.create(model_path, "")
        _REC[model_path] = rec
    return rec


def _thumb(img: np.ndarray, row: np.ndarray, size: int = 96) -> str:
    import cv2
    h, w = img.shape[:2]
    x, y, fw, fh = (float(v) for v in row[:4])
    m = 0.35 * max(fw, fh)
    x0, y0 = int(max(0, x - m)), int(max(0, y - m))
    x1, y1 = int(min(w, x + fw + m)), int(min(h, y + fh + m))
    crop = img[y0:y1, x0:x1]
    if crop.size == 0:
        return ""
    side = max(crop.shape[:2])
    pad = np.zeros((side, side, 3), dtype=np.uint8)
    pad[(side - crop.shape[0]) // 2:(side - crop.shape[0]) // 2 + crop.shape[0],
        (side - crop.shape[1]) // 2:(side - crop.shape[1]) // 2 + crop.shape[1]] = crop
    ok, buf = cv2.imencode(".jpg", cv2.resize(pad, (size, size)), [int(cv2.IMWRITE_JPEG_QUALITY), 82])
    return "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode("ascii") if ok else ""


def collect_faces(analyzed: List[Dict[str, Any]], model_path: str,
                  on_progress: Optional[Callable[[int, int], None]] = None) -> List[Dict[str, Any]]:
    """One record per usable face: clip path, embedding, face height (fraction of frame), quality, thumbnail."""
    import cv2
    from packages.ai_engine.in_clip_trimmer import analysis_frames, detect_faces_raw
    rec = _recognizer(model_path)
    todo = [c for c in analyzed if (c.get("faces") or {}).get("count")]
    out: List[Dict[str, Any]] = []
    for n, clip in enumerate(todo, 1):
        try:
            for frame in analysis_frames(clip):
                img, rows = detect_faces_raw(frame)
                if rows is None:
                    continue
                h = img.shape[0]
                gray = None
                for row in rows:
                    fh = float(row[3]) / h
                    if fh < MIN_FACE_HEIGHT or float(row[14]) < 0.7:
                        continue  # too small or unsure: a bad embedding would merge strangers
                    if gray is None:
                        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
                    x, y, fw, fhh = (int(v) for v in row[:4])
                    crop = gray[max(0, y):y + fhh, max(0, x):x + fw]
                    if crop.size == 0 or cv2.Laplacian(cv2.resize(crop, (96, 96)), cv2.CV_64F).var() < MIN_FACE_SHARPNESS:
                        continue  # blurred (smoke, motion, focus miss): same risk
                    emb = rec.feature(rec.alignCrop(img, row)).flatten().astype(np.float32)
                    norm = float(np.linalg.norm(emb)) or 1.0
                    out.append({"clip": clip["file_path"], "emb": emb / norm, "size": round(fh, 4),
                                "quality": fh * float(row[14]), "img": img, "row": row})
        except Exception as e:
            logger.warning(f"people: skipped {clip.get('file_path')}: {e}")
        if on_progress:
            on_progress(n, len(todo))
    return out


# ------------------------------------------------------------------ clustering

def cluster_embeddings(embs: List[np.ndarray], same: float = SAME_PERSON, merge: float = MERGE_PERSONS) -> List[int]:
    """Greedy grouping on cosine similarity (embeddings must be unit length), then a merge pass."""
    centroids: List[np.ndarray] = []
    members: List[List[int]] = []
    labels: List[int] = []
    for i, e in enumerate(embs):
        best, best_sim = -1, -1.0
        for k, c in enumerate(centroids):
            sim = float(np.dot(e, c / (np.linalg.norm(c) or 1.0)))
            if sim > best_sim:
                best, best_sim = k, sim
        if best >= 0 and best_sim >= same:
            members[best].append(i)
            centroids[best] = centroids[best] + e
            labels.append(best)
        else:
            centroids.append(e.copy())
            members.append([i])
            labels.append(len(centroids) - 1)
    # merge groups that turned out to be the same person
    parent = list(range(len(centroids)))

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    unit = [c / (np.linalg.norm(c) or 1.0) for c in centroids]
    for a in range(len(unit)):
        for b in range(a + 1, len(unit)):
            if float(np.dot(unit[a], unit[b])) >= merge:
                parent[find(b)] = find(a)
    roots = sorted({find(k) for k in range(len(centroids))})
    remap = {r: i for i, r in enumerate(roots)}
    return [remap[find(l)] for l in labels]


def build_store(folder: str, faces: List[Dict[str, Any]], previous: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """People (most seen first) with their clips, a thumbnail of their best face and a centroid.
    Roles named in a previous scan carry over to the matching person."""
    labels = cluster_embeddings([f["emb"] for f in faces]) if faces else []
    groups: Dict[int, List[Dict[str, Any]]] = {}
    for f, l in zip(faces, labels):
        groups.setdefault(l, []).append(f)
    persons = []
    for l, fs in groups.items():
        clips = sorted({f["clip"] for f in fs})
        best = max(fs, key=lambda f: f["quality"])
        centroid = np.mean([f["emb"] for f in fs], axis=0)
        centroid = centroid / (np.linalg.norm(centroid) or 1.0)
        persons.append({"clips": clips, "faces": len(fs), "avg_size": round(float(np.mean([f["size"] for f in fs])), 4),
                        "thumb": _thumb(best["img"], best["row"]), "centroid": [round(float(v), 5) for v in centroid],
                        "role": None})
    persons.sort(key=lambda p: (len(p["clips"]), p["avg_size"]), reverse=True)
    for i, p in enumerate(persons):
        p["id"] = f"p{i + 1}"
    if previous:
        _carry_roles(previous, persons)
    store = {"version": STORE_VERSION, "folder": folder, "consent": True, "updated": int(time.time()),
             "persons": persons, "references": list((previous or {}).get("references", []))}
    match_references(store)
    store["auto_couple"] = auto_couple(persons)
    return store


# ------------------------------------------------------------------ reference photos (optional, most accurate)

def face_from_photo(image_path: str, model_path: str) -> Tuple[np.ndarray, str]:
    """Unit embedding + thumbnail of the biggest face in a photo the editor uploads (e.g. the bride)."""
    import cv2
    from packages.ai_engine.in_clip_trimmer import detect_faces_raw
    try:
        from PIL import Image, ImageOps
        with Image.open(image_path) as im:
            rgb = np.array(ImageOps.exif_transpose(im).convert("RGB"))
    except Exception as e:
        raise PeopleError(f"Could not open the photo: {e}")
    frame = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    img, rows = detect_faces_raw(frame, max_width=1280)
    if rows is None or not len(rows):
        raise PeopleError("No face found in this photo. Use a clear, front-facing photo of one person.")
    row = max(rows, key=lambda r: float(r[2]) * float(r[3]))
    rec = _recognizer(model_path)
    emb = rec.feature(rec.alignCrop(img, row)).flatten().astype(np.float32)
    return emb / (float(np.linalg.norm(emb)) or 1.0), _thumb(img, row)


def add_reference(store: Dict[str, Any], role: str, emb: np.ndarray, thumb: str) -> Dict[str, Any]:
    """Keeps up to 3 reference photos per role; the person most like them gets the role."""
    if role not in ROLES:
        raise PeopleError(f"Unknown role {role}")
    refs = [r for r in store.setdefault("references", []) if r["role"] == role][-2:]
    others = [r for r in store["references"] if r["role"] != role]
    refs.append({"role": role, "emb": [round(float(v), 5) for v in emb], "thumb": thumb, "added": int(time.time())})
    store["references"] = others + refs
    match_references(store)
    store["auto_couple"] = auto_couple(store.get("persons", []))
    store["updated"] = int(time.time())
    return store


def remove_references(store: Dict[str, Any], role: str) -> Dict[str, Any]:
    store["references"] = [r for r in store.get("references", []) if r["role"] != role]
    for p in store.get("persons", []):
        if p.get("role") == role and p.get("role_from") == "photo":
            p["role"], p["role_from"] = None, None
    store["auto_couple"] = auto_couple(store.get("persons", []))
    return store


def match_references(store: Dict[str, Any]) -> List[str]:
    """Gives each uploaded role to the most similar person (if similar enough). A photo beats an
    automatic guess but never overrides a role the editor picked by hand. Returns the roles matched."""
    matched = []
    by_role: Dict[str, List[np.ndarray]] = {}
    for r in store.get("references", []):
        by_role.setdefault(r["role"], []).append(np.asarray(r["emb"], dtype=np.float32))
    persons = store.get("persons", [])
    for role, embs in by_role.items():
        if any(p.get("role") == role and p.get("role_from") != "photo" for p in persons):
            continue  # set by hand
        best, best_sim = None, SAME_PERSON
        for p in persons:
            if p.get("role") and p.get("role") != role and p.get("role_from") != "photo":
                continue
            c = np.asarray(p["centroid"], dtype=np.float32)
            sim = max(float(np.dot(c, e)) for e in embs)
            if sim >= best_sim:
                best, best_sim = p, sim
        for p in persons:
            if p.get("role") == role and p.get("role_from") == "photo" and p is not best:
                p["role"], p["role_from"] = None, None
        if best is not None:
            best["role"], best["role_from"], best["match"] = role, "photo", round(best_sim, 3)
            matched.append(role)
    return matched


def _carry_roles(previous: Dict[str, Any], persons: List[Dict[str, Any]]) -> None:
    named = [p for p in previous.get("persons", []) if p.get("role") and p.get("role_from") != "photo"]
    for old in named:
        oc = np.asarray(old["centroid"], dtype=np.float32)
        best, best_sim = None, MERGE_PERSONS
        for p in persons:
            if p.get("role"):
                continue
            sim = float(np.dot(oc, np.asarray(p["centroid"], dtype=np.float32)))
            if sim >= best_sim:
                best, best_sim = p, sim
        if best is not None:
            best["role"] = old["role"]
            best["role_from"] = old.get("role_from") or "hand"
            if old.get("name"):
                best["name"] = old["name"]


def auto_couple(persons: List[Dict[str, Any]]) -> List[str]:
    """Without names: the two people seen in the most clips with the biggest faces are taken as the couple."""
    if any(p.get("role") in COUPLE for p in persons):
        return []
    ranked = sorted((p for p in persons if len(p["clips"]) >= 3),
                    key=lambda p: len(p["clips"]) * (0.5 + p["avg_size"]), reverse=True)
    return [p["id"] for p in ranked[:2]]


# ------------------------------------------------------------------ apply to clips

def set_role(store: Dict[str, Any], person_id: str, role: Optional[str], name: str = "") -> Dict[str, Any]:
    if role is not None and role not in ROLES:
        raise PeopleError(f"Unknown role {role}")
    for p in store.get("persons", []):
        if p["id"] == person_id:
            p["role"] = role or None
            p["role_from"] = "hand" if role else None
            p["name"] = (name or "").strip()[:40]
        elif role in COUPLE and p.get("role") == role:
            p["role"] = None  # only one bride and one groom
    store["auto_couple"] = auto_couple(store.get("persons", []))
    store["updated"] = int(time.time())
    return store


def apply_people(analyzed: List[Dict[str, Any]], store: Optional[Dict[str, Any]]) -> int:
    """Writes people / people_roles / couple_in_shot on every clip and nudges quality_score and
    wedding_score (from their saved base values, so repeating this never adds up). Returns clips with the couple."""
    roles_of: Dict[str, List[str]] = {}
    ids_of: Dict[str, List[str]] = {}
    auto = set((store or {}).get("auto_couple") or [])
    for p in (store or {}).get("persons", []):
        role = p.get("role") or ("couple" if p["id"] in auto else None)
        for clip in p["clips"]:
            ids_of.setdefault(clip, []).append(p["id"])
            if role:
                roles_of.setdefault(clip, []).append(role)
    from packages.ai_engine.in_clip_trimmer import recompute_scores
    with_couple = 0
    for c in analyzed:
        if store is None:
            for key in ("people", "people_roles", "couple_in_shot", "people_bonus"):
                c.pop(key, None)
            recompute_scores(c)
            continue
        path = c.get("file_path")
        roles = sorted(set(roles_of.get(path, [])))
        c["people"] = ids_of.get(path, [])
        c["people_roles"] = roles
        couple = any(r in COUPLE or r == "couple" for r in roles)
        c["couple_in_shot"] = couple
        if couple:
            bonus = COUPLE_BONUS
            with_couple += 1
        elif any(r in FAMILY for r in roles):
            bonus = FAMILY_BONUS
        elif c["people"] and all(r in ("guest", "friend") for r in roles) and roles:
            bonus = -GUEST_ONLY_PENALTY
        else:
            bonus = 0.0
        c["people_bonus"] = bonus
        recompute_scores(c)
    return with_couple


def public_view(store: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """What the UI shows: no embeddings."""
    if not store:
        return {"consent": False, "persons": [], "auto_couple": []}
    return {"consent": bool(store.get("consent")), "updated": store.get("updated"), "auto_couple": store.get("auto_couple", []),
            "roles": ROLES,
            "references": [{"role": r["role"], "thumb": r["thumb"]} for r in store.get("references", [])],
            "persons": [{"id": p["id"], "role": p.get("role"), "role_from": p.get("role_from"), "name": p.get("name", ""),
                         "clips": len(p["clips"]), "faces": p["faces"], "thumb": p["thumb"]} for p in store.get("persons", [])]}


def scan_project(analyzed: List[Dict[str, Any]], folder: str, model_path: str, base: Optional[str] = None,
                 on_progress: Optional[Callable[[int, int], None]] = None) -> Dict[str, Any]:
    """Finds the people in a project, keeps earlier names, saves the store and tags the clips."""
    previous = load_store(folder, base)
    faces = collect_faces(analyzed, model_path, on_progress)
    store = build_store(folder, faces, previous)
    save_store(store, base)
    apply_people(analyzed, store)
    return store
