"""
AI Video Flow - audio decoding

librosa/soundfile cannot open video files (mp4, mov, mxf...) or AAC/m4a audio, so every audio read
falls back to the ffmpeg binary bundled with imageio-ffmpeg (or a system ffmpeg).
"""

import shutil
import subprocess
from typing import Tuple

import numpy as np


def ffmpeg_path() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        path = shutil.which("ffmpeg")
        if not path:
            raise RuntimeError("ffmpeg is not available to read the audio.")
        return path


def load_audio(path: str, sr: int = 16000) -> Tuple[np.ndarray, int]:
    """Returns the audio of any audio/video file as mono float32 at `sr` Hz."""
    proc = subprocess.run(
        [ffmpeg_path(), "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"],
        capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if proc.returncode != 0 or not proc.stdout:
        message = (proc.stderr or b"").decode("utf-8", "ignore").strip()[:200]
        raise RuntimeError(message or "This file has no audio track.")
    return np.frombuffer(proc.stdout, dtype=np.float32).copy(), sr
