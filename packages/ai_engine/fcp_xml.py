"""
AI Video Flow - multi-track FCP 7 XML (xmeml v4) for Premiere Pro and EDIUS

Writes one video track (and a matching camera-audio track) per camera, each clip at its synced
position, so the same moment from every camera sits on top of each other.
"""

import os
import xml.etree.ElementTree as ET
from typing import Any, Dict, List


def _rate(parent: ET.Element, fps: int) -> None:
    r = ET.SubElement(parent, "rate")
    ET.SubElement(r, "timebase").text = str(fps)
    ET.SubElement(r, "ntsc").text = "FALSE"


def _pathurl(path: str) -> str:
    return "file://localhost/" + os.path.abspath(path).replace("\\", "/").lstrip("/")


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

    os.makedirs(os.path.dirname(xml_path) or ".", exist_ok=True)
    ET.ElementTree(root).write(xml_path, encoding="UTF-8", xml_declaration=True)
    with open(xml_path, "r+", encoding="utf-8") as fh:  # FCP 7 importers expect the DOCTYPE line
        body = fh.read()
        fh.seek(0)
        fh.write(body.replace("?>", "?>\n<!DOCTYPE xmeml>", 1))
    return count
