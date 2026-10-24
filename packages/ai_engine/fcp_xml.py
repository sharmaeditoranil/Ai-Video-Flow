"""
AI Video Flow - multi-track FCP 7 XML (xmeml v4) for Premiere Pro and EDIUS

Writes one video track (and a matching camera-audio track) per camera, each clip at its synced
position, so the same moment from every camera sits on top of each other.
"""

import os
import re
import subprocess
import urllib.parse
import xml.etree.ElementTree as ET
from typing import Any, Dict, List


def _rate(parent: ET.Element, fps: int) -> None:
    r = ET.SubElement(parent, "rate")
    ET.SubElement(r, "timebase").text = str(fps)
    ET.SubElement(r, "ntsc").text = "FALSE"


def _pathurl(path: str) -> str:
    return "file://localhost/" + os.path.abspath(path).replace("\\", "/").lstrip("/")


def _url_path(url: str) -> str:
    p = urllib.parse.unquote(url[len("file://localhost"):] if url.startswith("file://localhost") else url)
    return p.lstrip("/") if re.match(r"^/[A-Za-z]:/", p) else p  # Windows: /C:/... -> C:/...


_INFO: Dict[str, Dict[str, Any]] = {}


def media_info(path: str) -> Dict[str, Any]:
    """Duration, picture size, frame rate and audio layout of a media file (read with ffmpeg, cached)."""
    if path in _INFO:
        return _INFO[path]
    info: Dict[str, Any] = {"duration": 0.0, "video": False, "width": 1920, "height": 1080, "fps": 25.0,
                            "audio": False, "samplerate": 48000, "channels": 2}
    try:
        from packages.ai_engine.audio_io import ffmpeg_path
        err = subprocess.run([ffmpeg_path(), "-hide_banner", "-i", path], capture_output=True, text=True,
                             errors="ignore", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stderr
        m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err)
        if m:
            info["duration"] = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])
        v = re.search(r"Stream #.*?Video: .*?(\d{2,5})x(\d{2,5})(?:.*?([\d.]+) fps)?", err)
        if v:
            info.update(video=True, width=int(v[1]), height=int(v[2]))
            if v[3]:
                info["fps"] = float(v[3])
        a = re.search(r"Stream #.*?Audio: [^,]+, (\d+) Hz, ([^,]+)", err)
        if a:
            layout = a[2].strip()
            n = re.match(r"(\d+) channels", layout)
            info.update(audio=True, samplerate=int(a[1]),
                        channels=1 if layout == "mono" else int(n[1]) if n else 2)
    except Exception:
        pass
    _INFO[path] = info
    return info


def describe_files(root: ET.Element) -> None:
    """Premiere Pro drops a whole sequence when a <file> does not describe its media, so every
    file definition gets its duration and video/audio characteristics. Clips on audio tracks whose
    file has no sound (e.g. a camera that recorded without audio) are left out for the same reason."""
    silent = set()
    for f in root.iter("file"):
        url = f.findtext("pathurl")
        if url and not media_info(_url_path(url))["audio"] and media_info(_url_path(url))["video"]:
            silent.add(f.get("id"))
    for audio in root.iter("audio"):
        for track in audio.findall("track"):
            for item in track.findall("clipitem"):
                ref = item.find("file")
                if ref is not None and ref.get("id") in silent:
                    track.remove(item)
    for f in root.iter("file"):
        url = f.findtext("pathurl")
        if not url or f.find("media") is not None:
            continue
        info = media_info(_url_path(url))
        fps = int((f.findtext("rate/timebase") or "").strip() or round(info["fps"]) or 25)
        if f.find("duration") is None:
            ET.SubElement(f, "duration").text = str(max(1, int(round(info["duration"] * fps))))
        media = ET.SubElement(f, "media")
        if info["video"]:
            sc = ET.SubElement(ET.SubElement(media, "video"), "samplecharacteristics")
            _rate(sc, fps)
            ET.SubElement(sc, "width").text = str(info["width"])
            ET.SubElement(sc, "height").text = str(info["height"])
            ET.SubElement(sc, "anamorphic").text = "FALSE"
            ET.SubElement(sc, "pixelaspectratio").text = "square"
            ET.SubElement(sc, "fielddominance").text = "none"
        if info["audio"] or not info["video"]:
            audio = ET.SubElement(media, "audio")
            sc = ET.SubElement(audio, "samplecharacteristics")
            ET.SubElement(sc, "depth").text = "16"
            ET.SubElement(sc, "samplerate").text = str(info["samplerate"])
            ET.SubElement(audio, "channelcount").text = str(info["channels"])


def write_track_sequence(xml_path: str, name: str, tracks: List[List[Dict[str, Any]]], fps: float = 25.0,
                         vertical: bool = False, music_path: str = "", music_seconds: float = 0.0) -> int:
    """`tracks[i]` holds the clips of video track i+1 with timeline_start / in_seconds / out_seconds.
    Returns the number of clips written."""
    fps_i = int(round(fps))
    root = ET.Element("xmeml", version="4")
    seq = ET.SubElement(root, "sequence", id="sequence-1")
    ET.SubElement(seq, "name").text = name
    total = max((c["timeline_end"] for t in tracks for c in t), default=0.0)
    ET.SubElement(seq, "duration").text = str(int(total * fps_i))
    _rate(seq, fps_i)
    media = ET.SubElement(seq, "media")

    video = ET.SubElement(media, "video")
    fmt = ET.SubElement(ET.SubElement(video, "format"), "samplecharacteristics")
    _rate(fmt, fps_i)
    ET.SubElement(fmt, "width").text = "1080" if vertical else "1920"
    ET.SubElement(fmt, "height").text = "1920" if vertical else "1080"
    ET.SubElement(fmt, "pixelaspectratio").text = "square"

    files_written: Dict[str, str] = {}
    count = 0

    def clipitem(track_el: ET.Element, clip: Dict[str, Any], item_id: str, kind: str) -> None:
        start = int(round(clip["timeline_start"] * fps_i))
        src_in = int(round(clip["in_seconds"] * fps_i))
        src_out = max(src_in + 1, int(round(clip["out_seconds"] * fps_i)))
        item = ET.SubElement(track_el, "clipitem", id=item_id)
        ET.SubElement(item, "name").text = os.path.basename(clip["file_path"])
        ET.SubElement(item, "duration").text = str(src_out - src_in)
        _rate(item, fps_i)
        ET.SubElement(item, "start").text = str(start)
        ET.SubElement(item, "end").text = str(start + src_out - src_in)
        ET.SubElement(item, "in").text = str(src_in)
        ET.SubElement(item, "out").text = str(src_out)
        path = clip["file_path"]
        if path in files_written:
            ET.SubElement(item, "file", id=files_written[path])  # reference the file defined earlier
        else:
            fid = f"file-{len(files_written) + 1}"
            files_written[path] = fid
            f = ET.SubElement(item, "file", id=fid)
            ET.SubElement(f, "name").text = os.path.basename(path)
            ET.SubElement(f, "pathurl").text = _pathurl(path)
            _rate(f, fps_i)
        if kind == "audio":
            st = ET.SubElement(item, "sourcetrack")
            ET.SubElement(st, "mediatype").text = "audio"
            ET.SubElement(st, "trackindex").text = "1"

    for ti, track in enumerate(tracks, 1):
        t_el = ET.SubElement(video, "track")
        for ci, clip in enumerate(track, 1):
            clipitem(t_el, clip, f"v{ti}-{ci}", "video")
            count += 1

    audio = ET.SubElement(media, "audio")
    for ti, track in enumerate(tracks, 1):
        t_el = ET.SubElement(audio, "track")
        for ci, clip in enumerate(track, 1):
            clipitem(t_el, clip, f"a{ti}-{ci}", "audio")
    if music_path and music_seconds > 0:
        # The song on its own audio track from 0:00 - the takes above are placed in song time
        clipitem(ET.SubElement(audio, "track"), {"file_path": music_path, "timeline_start": 0.0, "in_seconds": 0.0,
                                                 "out_seconds": music_seconds}, "music-1", "audio")

    _write(root, xml_path)
    return count


def _write(root: ET.Element, xml_path: str) -> None:
    describe_files(root)
    os.makedirs(os.path.dirname(xml_path) or ".", exist_ok=True)
    ET.ElementTree(root).write(xml_path, encoding="UTF-8", xml_declaration=True)
    with open(xml_path, "r+", encoding="utf-8") as fh:  # FCP 7 importers expect the DOCTYPE line
        body = fh.read()
        fh.seek(0)
        fh.write(body.replace("?>", "?>\n<!DOCTYPE xmeml>", 1))


def add_audio_track(xml_path: str, audio_path: str, seconds: float, start_seconds: float = 0.0) -> bool:
    """Adds `audio_path` (e.g. an AI voiceover) on a new audio track of an existing FCP 7 XML sequence."""
    root = ET.parse(xml_path).getroot()
    seq = root.find(".//sequence")  # EDIUS files wrap it in <project><children>
    media = seq.find("media") if seq is not None else None
    if media is None:
        return False
    audio = media.find("audio")
    if audio is None:
        audio = ET.SubElement(media, "audio")
    fps_i = int((seq.findtext("rate/timebase") or "25").strip() or 25)
    start, length = int(round(start_seconds * fps_i)), max(1, int(round(seconds * fps_i)))
    item = ET.SubElement(ET.SubElement(audio, "track"), "clipitem", id="clipitem-ai-audio-1")
    ET.SubElement(item, "name").text = os.path.basename(audio_path)
    ET.SubElement(item, "duration").text = str(length)
    _rate(item, fps_i)
    ET.SubElement(item, "start").text = str(start)
    ET.SubElement(item, "end").text = str(start + length)
    ET.SubElement(item, "in").text = "0"
    ET.SubElement(item, "out").text = str(length)
    f = ET.SubElement(item, "file", id="file-ai-audio-1")
    ET.SubElement(f, "name").text = os.path.basename(audio_path)
    ET.SubElement(f, "pathurl").text = _pathurl(audio_path)
    _rate(f, fps_i)
    total = seq.find("duration")
    if total is not None and (total.text or "").isdigit():
        total.text = str(max(int(total.text), start + length))
    _write(root, xml_path)
    return True
