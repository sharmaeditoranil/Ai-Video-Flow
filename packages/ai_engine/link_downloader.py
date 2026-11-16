"""
AI Video Flow - reference video from a link

Editors can paste a link (Instagram, Facebook, Vimeo, a direct .mp4 ...) instead of choosing a file. The video
is fetched with yt-dlp's normal behaviour and only used to study the reference's cuts, pace, colour and beats.
Sites that block downloads (YouTube currently needs extra tools) get a clear message asking for the file instead.
"""

import os
import re
import logging
from typing import Callable, Optional

logger = logging.getLogger("ai_video_flow.link_downloader")

MAX_BYTES = 400 * 1024 * 1024
_URL = re.compile(r"^https?://\S+$", re.I)
# Picture-only stream the Android decoder and muxer both handle (H.264 in MP4), plain HTTPS download first
_VIDEO_ONLY = ("bv*[height<=1080][vcodec^=avc1][ext=mp4][protocol^=http]"
               "/bv*[height<=1080][vcodec^=avc1][ext=mp4]")
_AUDIO_ONLY = "ba[ext=m4a][protocol^=http]/ba[ext=m4a]/ba[acodec^=mp4a]"


class LinkError(RuntimeError):
    pass


def is_link(text: str) -> bool:
    return bool(_URL.match((text or "").strip()))


def _ffmpeg() -> Optional[str]:
    try:
        from packages.ai_engine.audio_io import ffmpeg_path
        return ffmpeg_path()
    except Exception:
        return None


def _add_sound(url: str, video_path: str, opts: dict, log: Callable[[str, str], None]) -> str:
    """The link gave a picture-only file: fetches its sound and joins the two on the phone.
    The reference's cuts, pace and colour need only the picture, so any failure here keeps the silent file."""
    try:
        from packages.ai_engine import android_media
        if not android_media.ON_ANDROID:
            return video_path
        import yt_dlp
        base = os.path.splitext(video_path)[0]
        audio_opts = dict(opts, format=_AUDIO_ONLY, outtmpl=base + "_sound.%(ext)s")
        with yt_dlp.YoutubeDL(audio_opts) as ydl:
            ainfo = ydl.extract_info(url, download=True)
            audio_path = ydl.prepare_filename(ainfo)
        joined = base + "_full.mp4"
        if os.path.isfile(audio_path) and android_media.mux_audio(video_path, audio_path, joined) \
                and os.path.isfile(joined) and os.path.getsize(joined) >= 10_000:
            for p in (video_path, audio_path):
                try:
                    os.remove(p)
                except OSError:
                    pass
            return joined
    except Exception as e:
        logger.warning(f"Reference sound could not be added: {e!r}")
    log("  The reference's sound could not be fetched; its cuts and colour are still used.", "warning")
    return video_path


def download_reference(url: str, out_dir: str, log_fn: Optional[Callable[[str, str], None]] = None) -> str:
    """Downloads the video at `url` into `out_dir` and returns the file path. Raises LinkError with a user message."""
    def log(msg: str, level: str = "info"):
        if log_fn:
            try:
                log_fn(msg, level)
            except Exception:
                pass

    url = (url or "").strip()
    if not is_link(url):
        raise LinkError("This does not look like a video link (it should start with https://).")
    try:
        import yt_dlp
    except Exception as e:
        logger.warning(f"yt-dlp unavailable: {e!r}")
        raise LinkError("Links are not supported in this version of the app. Choose the video file instead.")

    os.makedirs(out_dir, exist_ok=True)
    ffmpeg = _ffmpeg()
    progress = {"pct": -10}

    def hook(d):
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            if total:
                pct = int(d.get("downloaded_bytes", 0) * 100 / total)
                if pct >= progress["pct"] + 20:
                    progress["pct"] = pct
                    log(f"  Downloading reference… {pct}%", "normal")

    opts = {
        "outtmpl": os.path.join(out_dir, "ref_%(extractor)s_%(id)s.%(ext)s"),
        # One file with sound up to 1080p; with FFmpeg (desktop) separate video + audio streams can be joined
        # ("b" is a single file that already has picture and sound; direct links carry no codec details)
        # Without FFmpeg (the phone) YouTube and many Instagram reels only offer separate streams: take the H.264
        # picture on its own, then fetch the sound below and join the two with Android's own muxer
        "format": ("bv*[height<=1080]+ba/b[height<=1080]/b" if ffmpeg else f"b[height<=1080]/{_VIDEO_ONLY}/b"),
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "socket_timeout": 30,
        "retries": 2,
        "max_filesize": MAX_BYTES,
        "progress_hooks": [hook],
    }
    if ffmpeg:
        opts["ffmpeg_location"] = ffmpeg

    log(f"🔗 Fetching the reference video from the link…", "info")
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            path = ydl.prepare_filename(info)
            if not os.path.isfile(path):
                path = os.path.splitext(path)[0] + ".mp4"
    except Exception as e:
        msg = str(e)
        logger.warning(f"Reference link failed: {msg}")
        if "youtube" in url.lower() or "youtu.be" in url.lower():
            raise LinkError("YouTube links can't be fetched right now. Download the video and choose the file instead.")
        if "login" in msg.lower() or "private" in msg.lower() or "cookies" in msg.lower():
            raise LinkError("This video is private or needs a login. Download it and choose the file instead.")
        raise LinkError("Could not get a video from this link. Download the video and choose the file instead.")
    if not os.path.isfile(path) or os.path.getsize(path) < 10_000:
        raise LinkError("The link did not give a playable video. Download the video and choose the file instead.")
    if not ffmpeg and info.get("acodec") == "none":
        path = _add_sound(url, path, opts, log)
    title = (info.get("title") or os.path.basename(path))[:60]
    log(f"  ✓ Reference ready: {title}", "info")
    return path
