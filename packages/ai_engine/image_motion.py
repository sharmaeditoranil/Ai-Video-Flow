"""
AI Video Flow - motion graphics from still images

A folder with only designs (PSD, PNG, JPG…) becomes a set of short animated clips: every image gets its own
camera move (push in, pull out, pans, a slow rotate) over a blurred copy of itself, with a soft fade in and out.
The clips then go through the normal editing pipeline, so beat sync, captions, the reel render and the
DaVinci / Premiere / EDIUS timelines all work as they do for video footage.
"""

import os
import hashlib
import logging
from typing import Callable, List, Optional, Tuple

import cv2
import numpy as np

logger = logging.getLogger("ai_video_flow.image_motion")

STILL_EXTS = {".psd", ".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp"}
FPS = 25.0
# (zoom start, zoom end, pan x start, pan x end, pan y start, pan y end, rotate degrees start, end)
MOVES = [
    (1.00, 1.14, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),      # push in
    (1.16, 1.02, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),      # pull out
    (1.12, 1.12, -0.05, 0.05, 0.0, 0.0, 0.0, 0.0),   # pan right
    (1.10, 1.16, 0.0, 0.0, 0.05, -0.04, 0.0, 0.0),   # rise up
    (1.06, 1.16, 0.0, 0.0, 0.0, 0.0, -1.6, 1.6),     # slow rotate in
    (1.12, 1.12, 0.05, -0.05, 0.0, 0.0, 0.0, 0.0),   # pan left
]


def _ease(t: float) -> float:
    return t * t * (3.0 - 2.0 * t)  # smoothstep: starts and lands softly


def load_still(path: str) -> Optional[np.ndarray]:
    """Reads any supported still as BGR (PSD: the flattened composite Photoshop saves with the file)."""
    try:
        from PIL import Image
        with Image.open(path) as im:
            im.load()
            if im.mode in ("RGBA", "LA", "P"):
                im = im.convert("RGBA")
                bg = Image.new("RGBA", im.size, (12, 12, 16, 255))
                bg.alpha_composite(im)
                im = bg
            rgb = np.asarray(im.convert("RGB"))
        return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    except Exception as e:
        logger.warning(f"Could not read {path}: {e}")
        return None


def _base_canvas(img: np.ndarray, w: int, h: int) -> Tuple[np.ndarray, np.ndarray]:
    """Background: the image blurred to fill the frame. Foreground: the whole image fitted inside it."""
    ih, iw = img.shape[:2]
    cover = max(w / iw, h / ih)
    bg = cv2.resize(img, (max(w, int(iw * cover)), max(h, int(ih * cover))), interpolation=cv2.INTER_AREA)
    y0, x0 = (bg.shape[0] - h) // 2, (bg.shape[1] - w) // 2
    bg = bg[y0:y0 + h, x0:x0 + w]
    bg = cv2.GaussianBlur(bg, (0, 0), sigmaX=28)
    bg = (bg.astype(np.float32) * 0.55).astype(np.uint8)  # darker so the design stands out
    fit = min(w / iw, h / ih)
    fg = cv2.resize(img, (max(2, int(iw * fit)), max(2, int(ih * fit))), interpolation=cv2.INTER_AREA)
    return bg, fg


def _frame(bg: np.ndarray, fg: np.ndarray, w: int, h: int, zoom: float, px: float, py: float, rot: float) -> np.ndarray:
    out = bg.copy()
    fh, fw = fg.shape[:2]
    m = cv2.getRotationMatrix2D((fw / 2, fh / 2), rot, zoom)
    m[0, 2] += (w - fw) / 2 + px * w
    m[1, 2] += (h - fh) / 2 + py * h
    warped = cv2.warpAffine(fg, m, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0))
    mask = cv2.warpAffine(np.full((fh, fw), 255, np.uint8), m, (w, h), flags=cv2.INTER_LINEAR)
    a = (mask.astype(np.float32) / 255.0)[..., None]
    return (warped.astype(np.float32) * a + out.astype(np.float32) * (1.0 - a)).astype(np.uint8)


def make_motion_clips(images: List[str], out_dir: str, size: Tuple[int, int] = (1080, 1920), seconds: float = 4.0,
                      log_fn: Optional[Callable[[str, str], None]] = None) -> List[str]:
    """Writes one animated .mp4 per image into `out_dir` (reused when the image has not changed)."""
    def log(msg: str, level: str = "normal"):
        if log_fn:
            try:
                log_fn(msg, level)
            except Exception:
                pass

    w, h = int(size[0]) & ~1, int(size[1]) & ~1
    n = max(2, int(round(seconds * FPS)))
    fade = int(FPS * 0.35)
    os.makedirs(out_dir, exist_ok=True)
    clips: List[str] = []
    for i, path in enumerate(images):
        try:
            st = os.stat(path)
        except OSError:
            continue
        tag = hashlib.sha1(f"{path}|{st.st_mtime}|{st.st_size}|{w}x{h}|{seconds}|{i % len(MOVES)}".encode()).hexdigest()[:10]
        out = os.path.join(out_dir, f"{i + 1:03d}_{os.path.splitext(os.path.basename(path))[0][:40]}_{tag}.mp4")
        if os.path.isfile(out) and os.path.getsize(out) > 0:
            clips.append(out)
            continue
        img = load_still(path)
        if img is None:
            log(f"  Skipped {os.path.basename(path)} (could not read it; for PSD save with 'Maximize compatibility')", "warning")
            continue
        bg, fg = _base_canvas(img, w, h)
        z0, z1, x0, x1, y0, y1, r0, r1 = MOVES[i % len(MOVES)]
        writer = cv2.VideoWriter(out, cv2.VideoWriter_fourcc(*"mp4v"), FPS, (w, h))
        if not writer.isOpened():
            raise RuntimeError("Could not create the motion clip video file.")
        try:
            for f in range(n):
                t = _ease(f / (n - 1))
                frame = _frame(bg, fg, w, h, z0 + (z1 - z0) * t, x0 + (x1 - x0) * t, y0 + (y1 - y0) * t, r0 + (r1 - r0) * t)
                if f < fade or f >= n - fade:  # dip to black at both ends so cuts between designs feel finished
                    k = min(f, n - 1 - f) / float(fade)
                    frame = (frame.astype(np.float32) * (0.25 + 0.75 * k)).astype(np.uint8)
                writer.write(frame)
        finally:
            writer.release()
        clips.append(out)
        log(f"  ✨ Motion clip {len(clips)}/{len(images)}: {os.path.basename(path)}", "normal")
    return clips
