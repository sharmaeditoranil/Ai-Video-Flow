"""
AI Video Flow - video reading / writing on Android

The OpenCV wheel for Android (Chaquopy) is built without FFmpeg, so cv2.VideoCapture cannot open phone
videos and cv2.VideoWriter cannot write .mp4. On Android, install() swaps both for drop-in versions backed
by the phone's own MediaCodec (com.aivideoflow.reels.MediaBridge), so the desktop editing code runs unchanged.
"""

import os
import logging
from typing import Optional, Tuple

import numpy as np
import cv2

logger = logging.getLogger("ai_video_flow.android_media")

try:
    from java import jclass  # only exists inside the Android app (Chaquopy)
    _Bridge = jclass("com.aivideoflow.reels.MediaBridge")
    _Reader = jclass("com.aivideoflow.reels.MediaBridge$Reader")
    _Writer = jclass("com.aivideoflow.reels.MediaBridge$Writer")
    ON_ANDROID = True
except Exception:
    ON_ANDROID = False

_NativeCapture = cv2.VideoCapture
_NativeWriter = cv2.VideoWriter
# Forward gaps smaller than this are decoded through instead of seeking (seeking restarts at a keyframe)
_MAX_SKIP_FRAMES = 90


class AndroidVideoCapture:
    """The parts of cv2.VideoCapture the engine uses: isOpened, get, set(POS_FRAMES), read, grab, release."""

    def __init__(self, path, *args):
        self._r = None
        self._pos = 0          # index of the frame the next read() returns
        self._decoded = -1     # index of the frame the decoder produced last
        self._last: Optional[np.ndarray] = None
        try:
            self._r = _Reader(str(path))
            self._fps = float(self._r.fps) or 30.0
            self._count = int(round(self._r.durationUs / 1e6 * self._fps)) if self._r.durationUs else 0
            self._rot = int(self._r.rotation) % 360
        except Exception as e:
            logger.warning(f"Android reader could not open {path}: {e}")
            self._r = None

    def isOpened(self) -> bool:
        return self._r is not None

    def get(self, prop) -> float:
        if self._r is None:
            return 0.0
        w, h = int(self._r.width), int(self._r.height)
        if self._rot in (90, 270):
            w, h = h, w
        return {
            cv2.CAP_PROP_FPS: self._fps,
            cv2.CAP_PROP_FRAME_COUNT: float(self._count),
            cv2.CAP_PROP_POS_FRAMES: float(self._pos),
            cv2.CAP_PROP_FRAME_WIDTH: float(w),
            cv2.CAP_PROP_FRAME_HEIGHT: float(h),
            cv2.CAP_PROP_POS_MSEC: self._pos / self._fps * 1000.0,
        }.get(prop, 0.0)

    def set(self, prop, value) -> bool:
        if prop == cv2.CAP_PROP_POS_FRAMES:
            self._pos = max(0, int(value))
            return True
        if prop == cv2.CAP_PROP_POS_MSEC:
            self._pos = max(0, int(round(float(value) / 1000.0 * self._fps)))
            return True
        return False

    def _decode_next(self) -> Optional[np.ndarray]:
        data = self._r.next()
        if data is None:
            return None
        pts = int(self._r.lastPtsUs())
        self._decoded = int(round(pts / 1e6 * self._fps)) if pts >= 0 else self._decoded + 1
        w, h = int(self._r.frameWidth()), int(self._r.frameHeight())
        yuv = np.frombuffer(bytes(data), dtype=np.uint8).reshape(h * 3 // 2, w)
        frame = cv2.cvtColor(yuv, cv2.COLOR_YUV2BGR_I420)
        if self._rot == 90:
            frame = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
        elif self._rot == 180:
            frame = cv2.rotate(frame, cv2.ROTATE_180)
        elif self._rot == 270:
            frame = cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
        return frame

    def read(self) -> Tuple[bool, Optional[np.ndarray]]:
        if self._r is None:
            return False, None
        target = self._pos
        if target == self._decoded and self._last is not None:
            self._pos += 1
            return True, self._last.copy()
        if target < self._decoded or target > self._decoded + _MAX_SKIP_FRAMES:
            self._r.seek(int(target / self._fps * 1e6))
            self._decoded = -1
        frame = None
        while True:
            frame = self._decode_next()
            if frame is None:
                return False, None
            if self._decoded >= target:
                break
        self._last = frame
        self._pos = target + 1
        return True, frame

    def grab(self) -> bool:
        ok, _ = self.read()
        return ok

    def release(self) -> None:
        if self._r is not None:
            try:
                self._r.release()
            finally:
                self._r = None


class AndroidVideoWriter:
    """cv2.VideoWriter replacement that writes H.264 .mp4 through the phone's encoder."""

    def __init__(self, path, fourcc=None, fps=25.0, size=(1080, 1920), *args):
        self._w, self._h = int(size[0]) & ~1, int(size[1]) & ~1
        try:
            self._writer = _Writer(str(path), self._w, self._h, float(fps))
        except Exception as e:
            logger.warning(f"Android writer could not start: {e}")
            self._writer = None

    def isOpened(self) -> bool:
        return self._writer is not None

    def write(self, frame: np.ndarray) -> None:
        if self._writer is None:
            return
        if frame.shape[1] != self._w or frame.shape[0] != self._h:
            frame = cv2.resize(frame, (self._w, self._h))
        self._writer.write(cv2.cvtColor(frame, cv2.COLOR_BGR2YUV_I420).tobytes())

    def release(self) -> None:
        if self._writer is not None:
            try:
                self._writer.release()
            finally:
                self._writer = None


def _capture(path, *args):
    native = _NativeCapture(path, *args)
    if native.isOpened() and int(native.get(cv2.CAP_PROP_FRAME_COUNT) or 0) > 0:
        return native
    native.release()
    return AndroidVideoCapture(path)


def install() -> bool:
    """On Android: route cv2.VideoCapture / cv2.VideoWriter through MediaCodec. No-op elsewhere."""
    if not ON_ANDROID:
        return False
    cv2.VideoCapture = _capture
    cv2.VideoWriter = AndroidVideoWriter
    return True


def mux_audio(video_path: str, audio_path: str, out_path: str) -> bool:
    """Adds `audio_path`'s sound (an audio file or a video) to `video_path`, cut to the video's length."""
    if not ON_ANDROID or not (audio_path and os.path.isfile(audio_path)):
        return False
    try:
        _Bridge.muxAudio(video_path, audio_path, out_path)
        return os.path.isfile(out_path) and os.path.getsize(out_path) > 0
    except Exception as e:
        logger.warning(f"Android audio mux failed: {e}")
        return False


def apply_lut(frame: np.ndarray, lut: np.ndarray) -> np.ndarray:
    """Applies a .cube 3D LUT (N x N x N x 3 floats, R fastest) to a BGR frame, nearest neighbour."""
    n = lut.shape[0]
    idx = (frame.astype(np.uint16) * (n - 1) + 127) // 255
    rgb = lut[idx[..., 0], idx[..., 1], idx[..., 2]]  # indexed [b][g][r]
    return np.clip(rgb[..., ::-1] * 255.0 + 0.5, 0, 255).astype(np.uint8)


def load_cube(path: str) -> Optional[np.ndarray]:
    """Reads a .cube file into an array indexed [b][g][r] -> (r, g, b)."""
    try:
        size, rows = 0, []
        with open(path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                parts = line.split()
                if not parts or parts[0].startswith("#"):
                    continue
                if parts[0] == "LUT_3D_SIZE":
                    size = int(parts[1])
                elif len(parts) == 3:
                    try:
                        rows.append([float(p) for p in parts])
                    except ValueError:
                        pass
        if size < 2 or len(rows) != size ** 3:
            return None
        return np.asarray(rows, dtype=np.float32).reshape(size, size, size, 3)
    except Exception:
        return None
