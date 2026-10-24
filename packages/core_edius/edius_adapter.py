"""
AI Video Flow - Grass Valley EDIUS Adapter (Windows & Cross-Platform XML Export)
Provides native timeline export and automation support for Grass Valley EDIUS (v7, v8, v9, X, 11).
EDIUS natively imports Apple FCP XML with full in/out cut points, multi-track video (V1/V2),
and synced/ducked audio tracks.
"""

import os
import sys
import glob
import logging
import platform
import subprocess
import xml.etree.ElementTree as ET
from xml.dom import minidom
from typing import Dict, Any, List, Optional

logger = logging.getLogger("ai_video_flow.edius")
if not logger.handlers:
    _h = logging.StreamHandler(sys.stderr)
    _h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    logger.addHandler(_h)
    logger.propagate = False


class EdiusAdapter:
    """
    Manages EDIUS integration, Windows application detection,
    and 100% EDIUS-compatible XML project export.
    """

    def __init__(self):
        self._system_os = platform.system()
        self._installed_apps: List[Dict[str, str]] = []
        self._active_app: Optional[Dict[str, str]] = None
        self.refresh()

    def refresh(self) -> None:
        """Detects installed EDIUS versions on Windows."""
        self._installed_apps = []
        if self._system_os == "Windows":
            search_paths = [
                r"C:\Program Files\Grass Valley\EDIUS 11\EDIUS.exe",
                r"C:\Program Files\Grass Valley\EDIUS X\EDIUS.exe",
                r"C:\Program Files\Grass Valley\EDIUS 9\EDIUS.exe",
                r"C:\Program Files\Grass Valley\EDIUS 8\EDIUS.exe",
                r"C:\Program Files\Grass Valley\EDIUS 7\EDIUS.exe",
                r"C:\Program Files (x86)\Grass Valley\EDIUS*\EDIUS.exe",
            ]
            for pat in search_paths:
                for exe in glob.glob(pat):
                    if os.path.isfile(exe):
                        ver = os.path.basename(os.path.dirname(exe)).replace("EDIUS", "").strip() or "Pro"
                        self._installed_apps.append({
                            "name": f"Grass Valley EDIUS {ver}".strip(),
                            "version": ver,
                            "path": exe,
                            "executable": exe
                        })
            if self._installed_apps:
                self._installed_apps.sort(key=lambda x: x["version"], reverse=True)
                self._active_app = self._installed_apps[0]
            else:
                self._active_app = None
        else:
            self._active_app = None

    def is_running(self) -> bool:
        """Checks if EDIUS process is running on Windows."""
        if self._system_os != "Windows":
            return False
        try:
            proc = subprocess.run(
                ["tasklist", "/fi", "imagename eq EDIUS.exe"],
                capture_output=True, text=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
            )
            return "EDIUS.exe" in proc.stdout
        except Exception as e:
            logger.debug(f"Process check failed: {e}")
        return False

    def get_status(self) -> Dict[str, Any]:
        """Returns EDIUS installation and readiness diagnostics."""
        self.refresh()
        running = self.is_running()
        installed = len(self._installed_apps) > 0
        return {
            "installed": installed,
            "running": running,
            "version": self._active_app["version"] if self._active_app else None,
            "app_name": self._active_app["name"] if self._active_app else "Grass Valley EDIUS",
            "app_path": self._active_app["path"] if self._active_app else None,
            "system_os": self._system_os,
            "xml_export_supported": True,
            "note": "EDIUS natively supports FCP XML import via File -> Import Project -> FCP XML on Windows."
        }

    def launch(self) -> Dict[str, Any]:
        """Launches Grass Valley EDIUS on Windows."""
        self.refresh()
        if self._system_os != "Windows":
            return {
                "success": False,
                "error": "Grass Valley EDIUS runs on Windows. You can still export EDIUS FCP XML files and transfer them to your Windows EDIUS machine."
            }

        if not self._active_app:
            return {
                "success": False,
                "error": "Grass Valley EDIUS installation not found in C:\\Program Files\\Grass Valley\\. Please ensure EDIUS is installed."
            }

        if self.is_running():
            return {"success": True, "message": "EDIUS is already running on Windows."}

        try:
            subprocess.Popen([self._active_app["executable"]], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return {
                "success": True,
                "message": f"Launching {self._active_app['name']}...",
                "app_path": self._active_app["path"]
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to launch EDIUS: {e}"}

    def export_plan_to_edius_xml(
        self,
        plan: Dict[str, Any],
        output_xml_path: str,
        audio_file_path: Optional[str] = None,
        sequence_name: Optional[str] = None,
        fps: float = 25.0,
        aspect_ratio: str = "16:9",
        broll_clips: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        """
        Generates an authentic Apple Final Cut Pro XML specifically formatted for Grass Valley EDIUS.
        EDIUS reads <xmeml version="4"> seamlessly, preserving all cutpoints and tracks.
        """
        try:
            clips = plan.get("clips", [])
            if not clips:
                return {"success": False, "error": "No clips present in plan to export."}

            broll = broll_clips or plan.get("broll_clips") or []
            seq_name = sequence_name or plan.get("sequence_name") or "EDIUS_Wedding_Timeline"
            os.makedirs(os.path.dirname(os.path.abspath(output_xml_path)), exist_ok=True)

            xml_content = self._build_edius_xml(
                clips=clips,
                broll_clips=broll,
                sequence_name=seq_name,
                audio_file_path=audio_file_path or plan.get("audio"),
                fps=fps,
                aspect_ratio=aspect_ratio
            )

            with open(output_xml_path, "w", encoding="utf-8") as f:
                f.write(xml_content)

            logger.info(f"Exported EDIUS XML to {output_xml_path} ({len(clips)} A-roll, {len(broll)} B-roll)")
            return {
                "success": True,
                "xml_path": output_xml_path,
                "sequence_name": seq_name,
                "clip_count": len(clips),
                "broll_count": len(broll),
                "target_nle": "edius",
                "message": f"EDIUS XML successfully exported ({len(clips)} clips)!"
            }
        except Exception as e:
            logger.error(f"Failed to export EDIUS XML: {e}")
            return {"success": False, "error": str(e)}

    def _build_edius_xml(
        self,
        clips: List[Dict[str, Any]],
        broll_clips: List[Dict[str, Any]],
        sequence_name: str,
        audio_file_path: Optional[str],
        fps: float = 25.0,
        aspect_ratio: str = "16:9"
    ) -> str:
        """Constructs XML tree structured for EDIUS Pro 7/8/9/X/11."""
        is_vertical = "9:16" in aspect_ratio or "vertical" in aspect_ratio.lower()
        width = 1080 if is_vertical else 1920
        height = 1920 if is_vertical else 1080

        root = ET.Element("xmeml", version="4")
        project = ET.SubElement(root, "project")
        ET.SubElement(project, "name").text = sequence_name

        children = ET.SubElement(project, "children")
        sequence = ET.SubElement(children, "sequence", id="sequence-edius-1")
        ET.SubElement(sequence, "name").text = sequence_name

        rate = ET.SubElement(sequence, "rate")
        ET.SubElement(rate, "timebase").text = str(int(fps))
        ET.SubElement(rate, "ntsc").text = "FALSE"

        media = ET.SubElement(sequence, "media")
        video = ET.SubElement(media, "video")

        vformat = ET.SubElement(video, "format")
        sample_char = ET.SubElement(vformat, "samplecharacteristics")
        ET.SubElement(sample_char, "width").text = str(width)
        ET.SubElement(sample_char, "height").text = str(height)
        ET.SubElement(sample_char, "anamorphic").text = "FALSE"
        ET.SubElement(sample_char, "pixelaspectratio").text = "square"

        # -------------------------------------------------------------
        # Video Track 1: Primary A-Roll Story Cuts
        # -------------------------------------------------------------
        vtrack1 = ET.SubElement(video, "track")
        curr_frame = 0

        for idx, clip in enumerate(clips, 1):
            file_path = clip.get("file_path") or clip.get("path") or ""
            if not file_path:
                continue

            in_frame = int(clip.get("in_frame", 0))
            out_frame = int(clip.get("out_frame", in_frame + int(fps * 3)))
            dur_frames = max(out_frame - in_frame, int(fps * 0.5))

            clipitem = ET.SubElement(vtrack1, "clipitem", id=f"clipitem-v1-{idx}")
            ET.SubElement(clipitem, "name").text = os.path.basename(file_path)
            ET.SubElement(clipitem, "duration").text = str(dur_frames)

            crate = ET.SubElement(clipitem, "rate")
            ET.SubElement(crate, "timebase").text = str(int(fps))
            ET.SubElement(crate, "ntsc").text = "FALSE"

            ET.SubElement(clipitem, "start").text = str(curr_frame)
            ET.SubElement(clipitem, "end").text = str(curr_frame + dur_frames)
            ET.SubElement(clipitem, "in").text = str(in_frame)
            ET.SubElement(clipitem, "out").text = str(out_frame)

            # File node with standard URI pathurl that EDIUS parses cleanly
            file_elem = ET.SubElement(clipitem, "file", id=f"file-v1-{idx}")
            ET.SubElement(file_elem, "name").text = os.path.basename(file_path)
            norm_p = os.path.abspath(file_path).replace("\\", "/")
            ET.SubElement(file_elem, "pathurl").text = f"file://localhost/{norm_p.lstrip('/')}"

            frate = ET.SubElement(file_elem, "rate")
            ET.SubElement(frate, "timebase").text = str(int(fps))
            ET.SubElement(frate, "ntsc").text = "FALSE"

            curr_frame += dur_frames

        # -------------------------------------------------------------
        # Video Track 2: B-Roll Cutaways
        # -------------------------------------------------------------
        if broll_clips:
            vtrack2 = ET.SubElement(video, "track")
            for b_idx, bclip in enumerate(broll_clips, 1):
                b_path = bclip.get("file_path") or ""
                if not b_path:
                    continue
                b_start = int(bclip.get("timeline_start", 0.0) * fps)
                b_in = int(bclip.get("in_frame", 0))
                b_dur = int(bclip.get("duration", 2.4) * fps)
                b_out = b_in + b_dur

                b_item = ET.SubElement(vtrack2, "clipitem", id=f"clipitem-v2-{b_idx}")
                ET.SubElement(b_item, "name").text = os.path.basename(b_path)
                ET.SubElement(b_item, "duration").text = str(b_dur)

                bcrate = ET.SubElement(b_item, "rate")
                ET.SubElement(bcrate, "timebase").text = str(int(fps))
                ET.SubElement(bcrate, "ntsc").text = "FALSE"

                ET.SubElement(b_item, "start").text = str(b_start)
                ET.SubElement(b_item, "end").text = str(b_start + b_dur)
                ET.SubElement(b_item, "in").text = str(b_in)
                ET.SubElement(b_item, "out").text = str(b_out)

                bfile_elem = ET.SubElement(b_item, "file", id=f"file-v2-{b_idx}")
                ET.SubElement(bfile_elem, "name").text = os.path.basename(b_path)
                norm_bp = os.path.abspath(b_path).replace("\\", "/")
                ET.SubElement(bfile_elem, "pathurl").text = f"file://localhost/{norm_bp.lstrip('/')}"

                bfrate = ET.SubElement(bfile_elem, "rate")
                ET.SubElement(bfrate, "timebase").text = str(int(fps))
                ET.SubElement(bfrate, "ntsc").text = "FALSE"

        # -------------------------------------------------------------
        # Audio Tracks: A1 (Dialogue / Ambient) + A2 (BGM Music)
        # -------------------------------------------------------------
        audio_elem = ET.SubElement(media, "audio")

        # Track A1: Camera sync audio
        atrack1 = ET.SubElement(audio_elem, "track")
        a1_frame = 0
        for idx, clip in enumerate(clips, 1):
            file_path = clip.get("file_path") or clip.get("path") or ""
            if not file_path:
                continue

            in_frame = int(clip.get("in_frame", 0))
            out_frame = int(clip.get("out_frame", in_frame + int(fps * 3)))
            dur_frames = max(out_frame - in_frame, int(fps * 0.5))

            aclipitem = ET.SubElement(atrack1, "clipitem", id=f"clipitem-a1-{idx}")
            ET.SubElement(aclipitem, "name").text = os.path.basename(file_path)
            ET.SubElement(aclipitem, "duration").text = str(dur_frames)

            arate = ET.SubElement(aclipitem, "rate")
            ET.SubElement(arate, "timebase").text = str(int(fps))
            ET.SubElement(arate, "ntsc").text = "FALSE"

            ET.SubElement(aclipitem, "start").text = str(a1_frame)
            ET.SubElement(aclipitem, "end").text = str(a1_frame + dur_frames)
            ET.SubElement(aclipitem, "in").text = str(in_frame)
            ET.SubElement(aclipitem, "out").text = str(out_frame)

            afile_elem = ET.SubElement(aclipitem, "file", id=f"file-v1-{idx}")
            ET.SubElement(afile_elem, "name").text = os.path.basename(file_path)
            norm_p = os.path.abspath(file_path).replace("\\", "/")
            ET.SubElement(afile_elem, "pathurl").text = f"file://localhost/{norm_p.lstrip('/')}"

            a1_frame += dur_frames

        # Track A2: Background Music Bed
        if audio_file_path and os.path.isfile(audio_file_path):
            atrack2 = ET.SubElement(audio_elem, "track")
            total_dur = max(curr_frame, int(fps * 30))

            mclipitem = ET.SubElement(atrack2, "clipitem", id="clipitem-bgm-1")
            ET.SubElement(mclipitem, "name").text = os.path.basename(audio_file_path)
            ET.SubElement(mclipitem, "duration").text = str(total_dur)

            mrate = ET.SubElement(mclipitem, "rate")
            ET.SubElement(mrate, "timebase").text = str(int(fps))
            ET.SubElement(mrate, "ntsc").text = "FALSE"

            ET.SubElement(mclipitem, "start").text = "0"
            ET.SubElement(mclipitem, "end").text = str(total_dur)
            ET.SubElement(mclipitem, "in").text = "0"
            ET.SubElement(mclipitem, "out").text = str(total_dur)

            mfile = ET.SubElement(mclipitem, "file", id="file-bgm-1")
            ET.SubElement(mfile, "name").text = os.path.basename(audio_file_path)
            norm_ap = os.path.abspath(audio_file_path).replace("\\", "/")
            ET.SubElement(mfile, "pathurl").text = f"file://localhost/{norm_ap.lstrip('/')}"

        rough = ET.tostring(root, encoding="utf-8")
        parsed = minidom.parseString(rough)
        return parsed.toprettyxml(indent="  ")


# Global instance
edius_adapter = EdiusAdapter()
