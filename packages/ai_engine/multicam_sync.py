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
MAX_CLOCK_OFFSET = 45 * 60  # flash search range around a guess
PATTERN_RANGE = 16 * 3600   # clocks can differ by hours (IST vs UTC, unset clocks)
PATTERN_BIN = 10.0          # cameras start/stop for the same moment within a few seconds


def _probe(path: str) -> str:
    try:
        proc = subprocess.run([ffmpeg_path(), "-hide_banner", "-i", path], capture_output=True, text=True,
                              timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return proc.stderr
    except Exception:
        return ""


def _timecode_seconds(info: str) -> Optional[float]:
    """Start timecode (HH:MM:SS:FF or HH:MM:SS;FF) written by the camera, as seconds of the day."""
    m = re.search(r"timecode\s*:\s*(\d\d):(\d\d):(\d\d)[:;.](\d\d)", info)
    if not m:
        return None
    fps_m = re.search(r"(\d+(?:\.\d+)?) fps", info)
    fps = float(fps_m.group(1)) if fps_m else 25.0
    h, mi, se, fr = (int(x) for x in m.groups())
    return h * 3600 + mi * 60 + se + fr / max(fps, 1.0)


def recording_start(path: str, duration: float) -> Tuple[float, str]:
    """Returns (unix seconds of the first frame, source of that time).
    Timecode is preferred: it is frame-accurate and, on jam-synced or time-of-day cameras, shared by all cameras."""
    info = _probe(path)
    created = None
    m = re.search(r"creation_time\s*:\s*(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?)Z?", info)
    if m:
        try:
            dt = datetime.fromisoformat(m.group(1)).replace(tzinfo=timezone.utc)
            if dt.year > 2000:  # cameras with an unset clock write 1970/1904
                created = dt.timestamp()
        except ValueError:
            pass
    tc = _timecode_seconds(info)
    if tc is not None:
        # Timecode only has the time of day: take the day from the metadata/file date
        anchor = created if created is not None else os.path.getmtime(path) - duration
        midnight = anchor - (anchor % 86400)
        start = midnight + tc
        # Pick the day that keeps it closest to the anchor (recordings around midnight)
        start = min((start - 86400, start, start + 86400), key=lambda x: abs(x - anchor))
        return start, "timecode"
    if created is not None:
        return created, "metadata"
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


def _best_offset(ref: List[float], other: List[float], center: float = 0.0,
                 window: float = MAX_CLOCK_OFFSET) -> Tuple[Optional[float], int]:
    """Clock offset (within `window` of `center`) that makes the most flashes of `other` line up with `ref`."""
    if not ref or not other:
        return None, 0
    bins: Dict[int, List[float]] = {}
    for a in other:
        for b in ref:
            d = b - a
            if abs(d - center) <= window:
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


def _intervals(clips: List[Dict[str, Any]], offset: float) -> List[Tuple[float, float]]:
    return sorted((c["rec_start"] + offset, c["rec_start"] + offset + float(c.get("duration", 0))) for c in clips)


def _overlap(a: List[Tuple[float, float]], b: List[Tuple[float, float]]) -> float:
    """Seconds during which both cameras were recording."""
    total, i, j = 0.0, 0, 0
    while i < len(a) and j < len(b):
        lo, hi = max(a[i][0], b[j][0]), min(a[i][1], b[j][1])
        if hi > lo:
            total += hi - lo
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return total


def _pattern_offset(ref: List[Tuple[float, float]], other: List[Tuple[float, float]]) -> Tuple[Optional[float], int]:
    """Clock offset from when recordings start and stop: cameras covering the same ritual start and stop
    within seconds of each other, whatever their clocks say (works for hour-sized errors too)."""
    bins: Dict[int, List[float]] = {}
    for (os_, oe) in other:
        for (rs, re_) in ref:
            for d in (rs - os_, re_ - oe):
                if abs(d) <= PATTERN_RANGE:
                    bins.setdefault(int(round(d / PATTERN_BIN)), []).append(d)
    if not bins:
        return None, 0
    best = max(bins, key=lambda k: len(bins.get(k - 1, [])) + len(bins[k]) + len(bins.get(k + 1, [])))
    diffs = sorted(bins.get(best - 1, []) + bins[best] + bins.get(best + 1, []))
    offset = diffs[len(diffs) // 2]
    ref_marks = [t for iv in ref for t in iv]
    matched = sum(1 for (s0, e0) in other
                  if any(abs(s0 + offset - t) <= 20 for t in ref_marks) or any(abs(e0 + offset - t) <= 20 for t in ref_marks))
    return offset, matched


LUMA_RATE = 5.0  # samples per second in luma_curve


def _light_signal(clips: List[Dict[str, Any]], offset: float, t0: float, n: int):
    """Brightness changes (first difference) of a camera on a shared 5 Hz grid; NaN where it wasn't recording."""
    import numpy as np
    sig = np.full(n, np.nan)
    for c in clips:
        curve = np.asarray(c.get("luma_curve") or [], dtype=float)
        if len(curve) < 3:
            continue
        d = np.diff(curve)
        # Flashes are huge one-off spikes: clip them so the slower light changes decide the match
        lim = 4 * (np.median(np.abs(d)) + 0.05)
        d = np.clip(d, -lim, lim)
        start = int(round((c["rec_start"] + offset - t0) * LUMA_RATE)) + 1
        lo, hi = max(0, start), min(n, start + len(d))
        if hi > lo:
            sig[lo:hi] = d[lo - start:hi - start]
    return sig


def _light_refine(ref_clips: List[Dict[str, Any]], clips: List[Dict[str, Any]], center: float,
                  window: float = 120.0) -> Optional[Tuple[float, float, float]]:
    """Fine offset near `center` from how scene brightness changes together (stage lights, LED walls, flashes)."""
    import numpy as np
    starts = [c["rec_start"] for c in ref_clips] + [c["rec_start"] + center for c in clips]
    ends = [c["rec_start"] + float(c.get("duration", 0)) for c in ref_clips] + \
           [c["rec_start"] + center + float(c.get("duration", 0)) for c in clips]
    t0 = min(starts) - window - 1
    n = int((max(ends) + window + 1 - t0) * LUMA_RATE)
    if n <= 0 or n > 2_000_000:
        return None
    ref = _light_signal(ref_clips, 0.0, t0, n)
    oth = _light_signal(clips, center, t0, n)
    ref_v, oth_v = ~np.isnan(ref), ~np.isnan(oth)
    ref0, oth0 = np.nan_to_num(ref), np.nan_to_num(oth)
    max_lag = int(window * LUMA_RATE)
    scores = np.full(2 * max_lag + 1, -1.0)
    for i, lag in enumerate(range(-max_lag, max_lag + 1)):
        if lag >= 0:
            a, b, va, vb = ref0[lag:], oth0[:n - lag], ref_v[lag:], oth_v[:n - lag]
        else:
            a, b, va, vb = ref0[:n + lag], oth0[-lag:], ref_v[:n + lag], oth_v[-lag:]
        both = va & vb
        if both.sum() < 20 * LUMA_RATE:  # need 20 s of shared recording
            continue
        x, y = a[both], b[both]
        x, y = x - x.mean(), y - y.mean()
        den = np.sqrt((x * x).sum() * (y * y).sum())
        if den > 1e-6:
            scores[i] = float((x * y).sum() / den)
    best = int(np.argmax(scores))
    peak = scores[best]
    # Must be a clear, unique peak: compare with the best *other* local peak (not the slope of this one)
    idx = np.arange(1, len(scores) - 1)
    local = idx[(scores[idx] >= scores[idx - 1]) & (scores[idx] >= scores[idx + 1])]
    others = local[np.abs(local - best) > 2 * LUMA_RATE]
    runner_up = scores[others].max() if len(others) else -1.0
    if peak < 0.5 or peak < runner_up + 0.01:
        return None
    return center + (best - max_lag) / LUMA_RATE, float(peak), float(peak - runner_up)


def sync(analyzed: List[Dict[str, Any]], root: str) -> Dict[str, Any]:
    """Adds `camera` and `abs_start` (synced unix seconds) to every clip. Returns a report per camera."""
    for clip in analyzed:
        clip["camera"] = camera_of(clip["file_path"], root)
        clip["rec_start"], clip["time_source"] = recording_start(clip["file_path"], float(clip.get("duration", 0)))

    cams: Dict[str, List[Dict[str, Any]]] = {}
    for clip in analyzed:
        cams.setdefault(clip["camera"], []).append(clip)

    flashes = {cam: [c["rec_start"] + t for c in clips for t in c.get("flash_times", [])] for cam, clips in cams.items()}
    # Reference = the camera that recorded the most (usually the main camera)
    ref_cam = max(cams, key=lambda c: (sum(float(x.get("duration", 0)) for x in cams[c]), len(flashes[c])))
    ref_iv = _intervals(cams[ref_cam], 0.0)
    report: Dict[str, Any] = {"reference": ref_cam, "cameras": {}}

    for cam, clips in cams.items():
        offset, matches, method, check = 0.0, 0, "same camera", False
        if cam != ref_cam:
            enough = lambda m: m >= 3 and m >= 0.25 * min(len(flashes[cam]), len(flashes[ref_cam]))
            both_tc = clips[0]["time_source"] == "timecode" and cams[ref_cam][0]["time_source"] == "timecode"
            candidates = [("timecode" if both_tc else "camera clock", 0.0, 0)]
            f_off, f_m = _best_offset(flashes[ref_cam], flashes[cam])
            if f_off is not None and enough(f_m):
                candidates.append(("flash sync", f_off, f_m))
            p_off, p_m = _pattern_offset(ref_iv, _intervals(clips, 0.0))
            if p_off is not None and p_m >= max(1, 0.3 * len(clips)):
                r_off, r_m = _best_offset(flashes[ref_cam], flashes[cam], center=p_off, window=90)
                if r_off is not None and r_m >= 2:
                    candidates.append(("recording pattern + flash", r_off, r_m))
                else:
                    candidates.append(("recording pattern", p_off, 0))
            # The right offset makes this camera's recordings overlap the reference's; flashes are hard evidence
            def score(c):
                bonus = 120 if c[0] == "timecode" else 0  # jam-synced / time-of-day timecode is frame-accurate
                return _overlap(ref_iv, _intervals(clips, c[1])) + 30 * c[2] + bonus
            method, offset, matches = max(candidates, key=score)
            if method == "recording pattern" and abs(offset) >= 1700:
                zone = round(offset / 1800) * 1800  # time-zone differences are whole half-hours (IST = 5:30)
                if abs(offset - zone) <= 90:
                    offset, method = float(zone), "time-zone difference"
            if matches == 0 and method != "timecode":
                # No flash agreement: look for shared brightness changes around every guess, keep the clearest
                tries = []
                for name, guess, _ in candidates:
                    r = _light_refine(cams[ref_cam], clips, offset if name == method else guess)
                    if r is not None:
                        tries.append((r[1], r[0], method if name == method else name, r[2]))
                if tries:
                    _, offset, base, margin = max(tries)
                    method = f"{base} + light sync"
                    check = margin < 0.05  # a close second-best match: worth a quick look by the editor
        for c in clips:
            c["abs_start"] = c["rec_start"] + offset
        report["cameras"][cam] = {"clips": len(clips), "offset_sec": round(offset, 2), "method": method,
                                  "flash_matches": matches, "time_source": clips[0]["time_source"],
                                  "overlap_sec": round(_overlap(ref_iv, _intervals(clips, offset)), 1),
                                  "please_check": check or method in ("camera clock", "recording pattern")}
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


def plan_multicam_tracks(analyzed: List[Dict[str, Any]], max_gap: Optional[float] = 2.0, by: str = "camera") -> Dict[str, Any]:
    """Every camera on its own video track, all clips at their synced real time, nothing cut out.
    Stretches where no camera was recording are shortened to `max_gap` seconds (None = keep the real
    timing, e.g. song time). by="stack" puts overlapping takes on the first free track instead."""
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
        if max_gap is None:
            pos = bs  # song time: position = real offset from the song start
        elif i:
            pos += min(max_gap, bs - blocks[i - 1][1])
        block_pos.append((bs, be, pos))
        pos += be - bs

    def to_timeline(t: float) -> float:
        for bs, be, p in block_pos:
            if bs <= t <= be:
                return p + (t - bs)
        return 0.0

    cameras = sorted({c["camera"] for *_, c in items}, key=lambda n: [int(x) if x.isdigit() else x.lower() for x in re.split(r"(\d+)", n)])
    tracks: List[List[Dict[str, Any]]] = [[] for _ in cameras] if by == "camera" else []
    for start, end, s_in, s_out, c in items:
        fps = float(c.get("native_fps") or 25.0)
        ts = to_timeline(start)
        if by == "camera":
            ti = cameras.index(c["camera"])
        else:  # first track where this take does not overlap another
            ti = next((i for i, t in enumerate(tracks) if not t or t[-1]["timeline_end"] <= ts + 0.01), len(tracks))
            if ti == len(tracks):
                tracks.append([])
        tracks[ti].append({
            "clip_name": os.path.basename(c["file_path"]),
            "file_path": c["file_path"],
            "camera": c["camera"],
            "track": ti + 1,
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


# ---------------------------------------------------------------------------
# Audio sync (podcasts / interviews: every camera records the same voices)
# ---------------------------------------------------------------------------
ENV_RATE = 50  # loudness samples per second


def audio_envelope(y, sr: int = 16000):
    """Log loudness at 50 Hz - the shape every camera's microphone shares."""
    import numpy as np
    hop = sr // ENV_RATE
    n = len(y) // hop
    if n == 0:
        return np.zeros(0)
    rms = np.sqrt((np.asarray(y[: n * hop], dtype=np.float64).reshape(n, hop) ** 2).mean(axis=1))
    return np.log10(rms + 1e-4)


def audio_lag(ref_env, other_env) -> Tuple[float, float]:
    """Seconds to add to the other camera's time to get the reference time, and a confidence score."""
    import numpy as np
    a = ref_env - ref_env.mean()
    b = other_env - other_env.mean()
    n = 1 << int(np.ceil(np.log2(len(a) + len(b) + 1)))
    corr = np.fft.irfft(np.fft.rfft(a, n) * np.conj(np.fft.rfft(b, n)), n)
    k = int(np.argmax(corr))
    if k > n // 2:
        k -= n
    confidence = float((corr.max() - corr.mean()) / (corr.std() + 1e-9))
    return k / ENV_RATE, confidence


def map_segment(start: float, end: float, files: List[Tuple[str, float, float, float]]) -> List[Tuple[str, float, float, float, float]]:
    """Pieces of [start, end) (camera stream seconds) inside that camera's files.
    `files` = [(path, stream_start, duration, fps)]; returns [(path, src_in, src_out, offset_in_segment, fps)]."""
    pieces = []
    for path, fs, dur, fps in files:
        lo, hi = max(start, fs), min(end, fs + dur)
        if hi - lo >= 0.05:
            pieces.append((path, lo - fs, hi - fs, lo - start, fps))
    return pieces



def audio_sync_clips(analyzed: List[Dict[str, Any]], root: str, song_path: Optional[str] = None,
                     min_confidence: float = 8.0) -> Dict[str, Any]:
    """Places clips by their sound. With a song (music videos shot to playback) every clip is placed where
    its audio matches the song; without one, cameras are lined up to the main camera's audio.
    Sets `abs_start` (song seconds or synced real time) and `audio_synced` on each clip."""
    import numpy as np
    from packages.ai_engine.audio_io import load_audio

    envs: Dict[str, Any] = {}
    for c in analyzed:
        c.setdefault("camera", camera_of(c["file_path"], root))
        try:
            y, sr = load_audio(c["file_path"], 16000)
            envs[c["file_path"]] = audio_envelope(y, sr)
        except Exception:
            envs[c["file_path"]] = None
        c["audio_synced"] = False

    if song_path:
        y, sr = load_audio(song_path, 16000)
        song_env = audio_envelope(y, sr)
        for c in analyzed:
            env = envs.get(c["file_path"])
            if env is None or len(env) < 2 * ENV_RATE:
                continue
            lag, conf = audio_lag(song_env, env)
            if conf >= min_confidence and -len(env) / ENV_RATE < lag < len(song_env) / ENV_RATE:
                c["abs_start"], c["audio_synced"] = lag, True
        synced = sum(1 for c in analyzed if c["audio_synced"])
        return {"mode": "song", "synced": synced, "total": len(analyzed), "song_duration": len(y) / sr}

    # No song: rough positions from time/flash/light sync, then the main camera's audio decides
    report = sync(analyzed, root)
    ref_cam = report["reference"]
    ref_clips = [c for c in analyzed if c["camera"] == ref_cam]
    t0 = min(c["abs_start"] for c in analyzed)
    t1 = max(c["abs_start"] + float(c.get("duration", 0)) for c in analyzed)
    grid = np.full(int((t1 - t0) * ENV_RATE) + ENV_RATE, -4.0)  # -4 = silence
    for c in ref_clips:
        env = envs.get(c["file_path"])
        if env is None:
            continue
        i = int((c["abs_start"] - t0) * ENV_RATE)
        grid[i:i + len(env)] = env[: max(0, len(grid) - i)]
        c["audio_synced"] = True
    moved = 0
    for c in analyzed:
        env = envs.get(c["file_path"])
        if c["camera"] == ref_cam or env is None or len(env) < 2 * ENV_RATE:
            continue
        lag, conf = audio_lag(grid, env)
        if conf >= min_confidence:
            c["abs_start"], c["audio_synced"] = t0 + lag, True
            moved += 1
    report.update(mode="cameras", synced=moved, total=len(analyzed) - len(ref_clips))
    return report
