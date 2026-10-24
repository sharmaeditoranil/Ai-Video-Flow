"""
AI Video Flow - multi-camera sync without audio

Weddings are often shot on 2-3 cameras with no usable audio. To put the same moment from every camera
together, each clip gets a real recording time:

1. Start time from the file's metadata (creation_time / timecode), or the file date as a fallback.
2. Camera from its sub-folder (Cam1, Cam2...) or the camera model in the metadata.
3. Camera clocks are never set exactly alike. Photographers' flashes light every camera at the same
   instant, so the clock difference of each camera is found by lining up the flash times.

`plan_multicam_film` then walks through real time and cuts between the cameras that cover each moment.
"""

import os
import re
import subprocess
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from packages.ai_engine.audio_io import ffmpeg_path

MATCH_TOLERANCE = 0.12      # seconds two flashes may differ and still be the same flash
MAX_CLOCK_OFFSET = 45 * 60  # cameras whose clocks differ more than this are not auto-corrected


def _probe(path: str) -> str:
    try:
        proc = subprocess.run([ffmpeg_path(), "-hide_banner", "-i", path], capture_output=True, text=True,
                              timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return proc.stderr
    except Exception:
        return ""


def recording_start(path: str, duration: float) -> Tuple[float, str]:
    """Returns (unix seconds of the first frame, source of that time)."""
    info = _probe(path)
    m = re.search(r"creation_time\s*:\s*(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?)Z?", info)
    if m:
        try:
            dt = datetime.fromisoformat(m.group(1)).replace(tzinfo=timezone.utc)
            if dt.year > 2000:  # cameras with an unset clock write 1970/1904
                return dt.timestamp(), "metadata"
        except ValueError:
            pass
    # Camera files are written when recording stops, so the file date minus the length is the start
    try:
        return os.path.getmtime(path) - duration, "file date"
    except OSError:
        return 0.0, "unknown"


def camera_of(path: str, root: str) -> str:
    rel = os.path.relpath(os.path.dirname(path), root)
    if rel not in (".", ""):
        return rel.split(os.sep)[0]
    info = _probe(path)
    m = re.search(r"(?:com\.apple\.quicktime\.model|model|make|encoder)\s*:\s*(.+)", info, re.IGNORECASE)
    if m:
        return m.group(1).strip()[:40]
    prefix = re.match(r"[A-Za-z_]+", os.path.basename(path))
    return prefix.group(0).rstrip("_") if prefix else "Camera"


def _best_offset(ref: List[float], other: List[float]) -> Tuple[Optional[float], int]:
    """Clock offset that makes the most flashes of `other` line up with `ref`, and how many matched."""
    if not ref or not other:
        return None, 0
    bins: Dict[int, List[float]] = {}
    for a in other:
        for b in ref:
            d = b - a
            if abs(d) <= MAX_CLOCK_OFFSET:
                bins.setdefault(int(round(d / MATCH_TOLERANCE)), []).append(d)
    if not bins:
        return None, 0
    # Merge each bin with its neighbours so a flash on a bin edge still counts
    best_key = max(bins, key=lambda k: len(bins.get(k - 1, [])) + len(bins[k]) + len(bins.get(k + 1, [])))
    diffs = bins.get(best_key - 1, []) + bins[best_key] + bins.get(best_key + 1, [])
    diffs.sort()
    offset = diffs[len(diffs) // 2]
    matches = sum(1 for a in other if any(abs(a + offset - b) <= MATCH_TOLERANCE for b in ref))
    return offset, matches


def sync(analyzed: List[Dict[str, Any]], root: str) -> Dict[str, Any]:
    """Adds `camera` and `abs_start` (synced unix seconds) to every clip. Returns a report per camera."""
    for clip in analyzed:
        clip["camera"] = camera_of(clip["file_path"], root)
        clip["rec_start"], clip["time_source"] = recording_start(clip["file_path"], float(clip.get("duration", 0)))

    cams: Dict[str, List[Dict[str, Any]]] = {}
    for clip in analyzed:
        cams.setdefault(clip["camera"], []).append(clip)

    flashes = {cam: [c["rec_start"] + t for c in clips for t in c.get("flash_times", [])] for cam, clips in cams.items()}
    ref_cam = max(cams, key=lambda c: (len(flashes[c]), len(cams[c])))
    report: Dict[str, Any] = {"reference": ref_cam, "cameras": {}}

    for cam, clips in cams.items():
        offset, matches, method = 0.0, 0, "same camera" if cam == ref_cam else "clock"
        if cam != ref_cam:
            found, matches = _best_offset(flashes[ref_cam], flashes[cam])
            # Need several flashes that agree before trusting it over the camera clock
            if found is not None and matches >= 3 and matches >= 0.25 * min(len(flashes[cam]), len(flashes[ref_cam])):
                offset, method = found, "flash sync"
        for c in clips:
            c["abs_start"] = c["rec_start"] + offset
        report["cameras"][cam] = {"clips": len(clips), "offset_sec": round(offset, 2), "method": method,
                                  "flash_matches": matches, "time_source": clips[0]["time_source"]}
    return report


def plan_multicam_film(analyzed: List[Dict[str, Any]], shot_seconds: float = 5.0, min_shot: float = 3.0) -> Dict[str, Any]:
    """Full film in real-time order: one camera at a time, switching between cameras that cover the same moment."""
    windows = []
    for c in analyzed:
        start = c["abs_start"] + float(c.get("full_in_seconds", c.get("in_seconds", 0.0)))
        end = c["abs_start"] + float(c.get("full_out_seconds", c.get("out_seconds", c.get("duration", 0.0))))
        if end - start >= 0.5:
            windows.append((start, end, c))
    windows.sort(key=lambda w: w[0])

    entries: List[Dict[str, Any]] = []
    timeline = 0.0
    current = None  # (start, end, clip)
    t = windows[0][0] if windows else 0.0
    end_all = max((w[1] for w in windows), default=0.0)

    while t < end_all - 0.5:
        covering = [w for w in windows if w[0] <= t + 0.01 and w[1] - t >= 0.5]
        if not covering:
            nxt = [w[0] for w in windows if w[0] > t]
            if not nxt:
                break
            t, current = min(nxt), None
            continue

        others = [w for w in covering if current is None or w[2]["camera"] != current[2]["camera"]]
        if current in covering and not others:
            chosen = current
        else:
            pool = others or covering
            chosen = max(pool, key=lambda w: float(w[2].get("quality_score", 0))
                         + (8 if current is not None and w[2].get("shot_type") != current[2].get("shot_type") else 0))

        # Hold one camera for ~5 s while others are rolling, otherwise until another camera starts
        other_cam_start = [w[0] for w in windows if w[0] > t + min_shot and w[2]["camera"] != chosen[2]["camera"]]
        if any(w is not chosen for w in covering):
            seg_end = t + shot_seconds
        else:
            seg_end = min(other_cam_start) if other_cam_start else chosen[1]
        seg_end = min(chosen[1], max(seg_end, t + min_shot))
        if chosen[1] - seg_end < min_shot / 2:
            seg_end = chosen[1]  # don't leave a tiny leftover of this clip

        clip = chosen[2]
        fps = float(clip.get("native_fps") or 25.0)
        src_in, src_out = t - clip["abs_start"], seg_end - clip["abs_start"]
        last = entries[-1] if entries else None
        if last and last["file_path"] == clip["file_path"] and abs(last["_src_out"] - src_in) < 0.05:
            last["out_frame"] = int(src_out * fps)
            last["_src_out"] = src_out
            last["duration"] = round(last["duration"] + seg_end - t, 2)
            last["timeline_end"] = round(timeline + seg_end - t, 2)
        else:
            entries.append({
                "clip_name": os.path.basename(clip["file_path"]),
                "file_path": clip["file_path"],
                "camera": clip["camera"],
                "timeline_start": round(timeline, 2),
                "timeline_end": round(timeline + seg_end - t, 2),
                "in_frame": int(src_in * fps),
                "out_frame": int(src_out * fps),
                "duration": round(seg_end - t, 2),
                "shot_type": clip.get("shot_type", "mid"),
                "role": "Multi-camera real-time edit",
                "_src_out": src_out,
            })
        timeline += seg_end - t
        current, t = chosen, seg_end

    for e in entries:
        e.pop("_src_out", None)
    return {
        "format": "Full_Wedding_Film",
        "aspect_ratio": "16:9",
        "total_duration": round(timeline, 2),
        "total_minutes": round(timeline / 60.0, 1),
        "clip_count": len(entries),
        "sequence_name": "03_Full_Wedding_Film",
        "multicam": True,
        "clips": entries,
    }


def plan_multicam_tracks(analyzed: List[Dict[str, Any]], max_gap: float = 2.0) -> Dict[str, Any]:
    """Every camera on its own video track, all clips at their synced real time, nothing cut out.
    Stretches where no camera was recording are shortened to `max_gap` seconds."""
    items = []
    for c in analyzed:
        s_in = float(c.get("full_in_seconds", c.get("in_seconds", 0.0)))
        s_out = float(c.get("full_out_seconds", c.get("out_seconds", c.get("duration", 0.0))))
        if s_out - s_in >= 0.5:
            items.append((c["abs_start"] + s_in, c["abs_start"] + s_out, s_in, s_out, c))
    items.sort(key=lambda x: x[0])

    # Real time -> timeline time: keep everything while any camera rolls, squeeze idle gaps
    blocks: List[List[float]] = []
    for start, end, *_ in items:
        if blocks and start <= blocks[-1][1]:
            blocks[-1][1] = max(blocks[-1][1], end)
        else:
            blocks.append([start, end])
    block_pos, pos = [], 0.0
    for i, (bs, be) in enumerate(blocks):
        if i:
            pos += min(max_gap, bs - blocks[i - 1][1])
        block_pos.append((bs, be, pos))
        pos += be - bs

    def to_timeline(t: float) -> float:
        for bs, be, p in block_pos:
            if bs <= t <= be:
                return p + (t - bs)
        return 0.0

    cameras = sorted({c["camera"] for *_, c in items}, key=lambda n: [int(x) if x.isdigit() else x.lower() for x in re.split(r"(\d+)", n)])
    tracks: List[List[Dict[str, Any]]] = [[] for _ in cameras]
    for start, end, s_in, s_out, c in items:
        fps = float(c.get("native_fps") or 25.0)
        ts = to_timeline(start)
        tracks[cameras.index(c["camera"])].append({
            "clip_name": os.path.basename(c["file_path"]),
            "file_path": c["file_path"],
            "camera": c["camera"],
            "track": cameras.index(c["camera"]) + 1,
            "timeline_start": round(ts, 3),
            "timeline_end": round(ts + (end - start), 3),
            "in_seconds": round(s_in, 3),
            "out_seconds": round(s_out, 3),
            "in_frame": int(s_in * fps),
            "out_frame": int(s_out * fps),
            "duration": round(end - start, 2),
            "shot_type": c.get("shot_type", "mid"),
            "role": f"{c['camera']} (synced)",
        })
    flat = sorted((clip for t in tracks for clip in t), key=lambda x: (x["timeline_start"], x["track"]))
    return {
        "format": "Full_Wedding_Film",
        "aspect_ratio": "16:9",
        "total_duration": round(pos, 2),
        "total_minutes": round(pos / 60.0, 1),
        "clip_count": len(flat),
        "sequence_name": "03_Full_Wedding_Film_Multicam",
        "multicam": True,
        "camera_tracks": cameras,
        "tracks": tracks,
        "clips": flat,
    }
