"""
AI Video Flow - Adobe Premiere Pro Bridge Adapter
Provides cross-platform connection, version detection, automation,
and seamless timeline interchange (FCP XML / OpenTimelineIO) for Adobe Premiere Pro.
"""

import os
import sys
import glob
import time
import platform
import subprocess
import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger("ai_video_flow.premiere")
if not logger.handlers:
    _h = logging.StreamHandler(sys.stderr)
    _h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    logger.addHandler(_h)
    logger.propagate = False


class PremiereAdapter:
    """
    Manages detection, automation, and timeline import for Adobe Premiere Pro
    across macOS and Windows.
    """

    def __init__(self):
        self._system_os = platform.system()
        self._installed_apps: List[Dict[str, str]] = []
        self._active_app: Optional[Dict[str, str]] = None
        self.refresh()

    def refresh(self) -> None:
        """Refreshes installed and running status."""
        self._installed_apps = self._detect_installed_apps()
        if self._installed_apps:
            # Sort newest first (e.g. 2026 > 2025 > 2024)
            self._installed_apps.sort(key=lambda x: x["version"], reverse=True)
            self._active_app = self._installed_apps[0]
        else:
            self._active_app = None

    def _detect_installed_apps(self) -> List[Dict[str, str]]:
        found = []
        if self._system_os == "Darwin":  # macOS
            patterns = [
                "/Applications/Adobe Premiere Pro*/Adobe Premiere Pro*.app",
                "/Applications/Adobe Premiere Pro*.app",
            ]
            for pat in patterns:
                for app_path in glob.glob(pat):
                    base = os.path.basename(app_path)
                    ver = base.replace(".app", "").replace("Adobe Premiere Pro", "").strip() or "CC"
                    found.append({
                        "name": f"Adobe Premiere Pro {ver}".strip(),
                        "version": ver,
                        "path": app_path,
                        "executable": app_path
                    })
        elif self._system_os == "Windows":
            patterns = [
                r"C:\Program Files\Adobe\Adobe Premiere Pro*\Adobe Premiere Pro.exe",
                r"C:\Program Files (x86)\Adobe\Adobe Premiere Pro*\Adobe Premiere Pro.exe",
            ]
            for pat in patterns:
                for exe_path in glob.glob(pat):
                    folder_name = os.path.basename(os.path.dirname(exe_path))
                    ver = folder_name.replace("Adobe Premiere Pro", "").strip() or "CC"
                    found.append({
                        "name": f"Adobe Premiere Pro {ver}".strip(),
                        "version": ver,
                        "path": exe_path,
                        "executable": exe_path
                    })
        return found

    def is_running(self) -> bool:
        """Checks if Adobe Premiere Pro process is running."""
        try:
            if self._system_os == "Darwin":
                proc = subprocess.run(["pgrep", "-f", "Adobe Premiere Pro"], capture_output=True, text=True)
                return proc.returncode == 0
            elif self._system_os == "Windows":
                proc = subprocess.run(
                    ["tasklist", "/fi", "imagename eq Adobe Premiere Pro.exe"],
                    capture_output=True, text=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                )
                return "Adobe Premiere Pro.exe" in proc.stdout
        except Exception as e:
            logger.debug(f"Process check failed: {e}")
        return False

    def get_status(self) -> Dict[str, Any]:
        """Returns comprehensive Premiere Pro status dictionary."""
        self.refresh()
        running = self.is_running()
        installed = len(self._installed_apps) > 0
        return {
            "installed": installed,
            "running": running,
            "version": self._active_app["version"] if self._active_app else None,
            "app_name": self._active_app["name"] if self._active_app else "Adobe Premiere Pro",
            "app_path": self._active_app["path"] if self._active_app else None,
            "system_os": self._system_os,
            "available_versions": [a["version"] for a in self._installed_apps]
        }

    def launch(self) -> Dict[str, Any]:
        """Launches Adobe Premiere Pro."""
        self.refresh()
        if not self._active_app:
            return {"success": False, "error": "Adobe Premiere Pro is not installed on this system."}

        if self.is_running():
            # Already running, bring to focus
            if self._system_os == "Darwin":
                subprocess.run(["osascript", "-e", f'tell application "{self._active_app["path"]}" to activate'], check=False)
            return {"success": True, "message": "Adobe Premiere Pro is already running and has been brought to front."}

        try:
            if self._system_os == "Darwin":
                subprocess.Popen(["open", "-a", self._active_app["path"]])
            elif self._system_os == "Windows":
                subprocess.Popen([self._active_app["executable"]], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            logger.info(f"Launched Adobe Premiere Pro from {self._active_app['path']}")
            return {
                "success": True,
                "message": f"Launching {self._active_app['name']}...",
                "app_path": self._active_app["path"]
            }
        except Exception as e:
            logger.error(f"Failed to launch Premiere Pro: {e}")
            return {"success": False, "error": str(e)}

    def open_timeline(self, xml_path: str) -> Dict[str, Any]:
        """
        Opens or imports an XML timeline directly in Adobe Premiere Pro.
        """
        if not os.path.exists(xml_path):
            return {"success": False, "error": f"Timeline file not found: {xml_path}"}

        self.refresh()
        if not self._active_app:
            return {"success": False, "error": "Adobe Premiere Pro is not installed."}

        try:
            abs_xml = os.path.abspath(xml_path)
            if self._system_os == "Darwin":
                subprocess.Popen(["open", "-a", self._active_app["path"], abs_xml])
            elif self._system_os == "Windows":
                subprocess.Popen([self._active_app["executable"], abs_xml], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            logger.info(f"Sent timeline to Adobe Premiere Pro: {abs_xml}")
            return {
                "success": True,
                "file_path": abs_xml,
                "message": f"Timeline opened in {self._active_app['name']}."
            }
        except Exception as e:
            logger.error(f"Failed to open timeline in Premiere: {e}")
            return {"success": False, "error": str(e)}

    def export_plan_to_premiere_xml(
        self,
        plan: Dict[str, Any],
        fps: float = 25.0,
        output_dir: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Converts an AI wedding plan into standard Premiere-compatible FCP XML.
        """
        name = plan.get("sequence_name", "AI_Wedding_Timeline")
        clips = plan.get("clips", [])
        if not output_dir:
            output_dir = os.path.join(os.path.expanduser("~"), "AI-Video-Flow-Output", "timelines")
        os.makedirs(output_dir, exist_ok=True)

        safe_name = "".join(c for c in name if c.isalnum() or c in ("_", "-"))
        xml_file = os.path.join(output_dir, f"{safe_name}_Premiere.xml")

        # Try OpenTimelineIO first
        try:
            from packages.ai_engine.otio_exporter import otio_exporter
            res = otio_exporter.export_timeline(name, clips, fps=fps, output_format="xml")
            if res.get("success") and os.path.exists(res.get("file_path", "")):
                # Return the exported xml
                return {
                    "success": True,
                    "xml_path": res["file_path"],
                    "timeline_name": name,
                    "clip_count": len(clips)
                }
        except Exception as e:
            logger.warning(f"OTIO exporter hit error: {e}. Using native XML generator.")

        # Native bulletproof FCP XML generator for Premiere Pro
        try:
            self._write_native_premiere_xml(xml_file, name, clips, fps)
            return {
                "success": True,
                "xml_path": xml_file,
                "timeline_name": name,
                "clip_count": len(clips)
            }
        except Exception as e:
            logger.error(f"Native Premiere XML generation failed: {e}")
            return {"success": False, "error": str(e)}

    def _write_native_premiere_xml(
        self,
        output_path: str,
        sequence_name: str,
        clips: List[Dict[str, Any]],
        fps: float = 25.0
    ) -> None:
        """
        Directly writes an authentic Apple FCP7 XML (<xmeml version="4">)
        which Adobe Premiere Pro natively parses without external dependencies.
        """
        import xml.etree.ElementTree as ET

        xmeml = ET.Element("xmeml", version="4")
        sequence = ET.SubElement(xmeml, "sequence")
        ET.SubElement(sequence, "name").text = sequence_name

        # Rate
        rate = ET.SubElement(sequence, "rate")
        ET.SubElement(rate, "timebase").text = str(int(fps))
        ET.SubElement(rate, "ntsc").text = "FALSE"

        # Media & Tracks
        media = ET.SubElement(sequence, "media")
        video = ET.SubElement(media, "video")
        vtrack = ET.SubElement(video, "track")

        curr_frame = 0
        for idx, clip in enumerate(clips, 1):
            file_path = clip.get("file_path") or clip.get("path") or ""
            if not file_path:
                continue

            in_frame = int(clip.get("in_frame", 0))
            out_frame = int(clip.get("out_frame", in_frame + int(fps * 3)))
            dur_frames = max(out_frame - in_frame, int(fps * 0.5))

            clipitem = ET.SubElement(vtrack, "clipitem", id=f"clipitem-{idx}")
            ET.SubElement(clipitem, "name").text = os.path.basename(file_path)
            ET.SubElement(clipitem, "duration").text = str(dur_frames)

            crate = ET.SubElement(clipitem, "rate")
            ET.SubElement(crate, "timebase").text = str(int(fps))
            ET.SubElement(crate, "ntsc").text = "FALSE"

            ET.SubElement(clipitem, "start").text = str(curr_frame)
            ET.SubElement(clipitem, "end").text = str(curr_frame + dur_frames)
            ET.SubElement(clipitem, "in").text = str(in_frame)
            ET.SubElement(clipitem, "out").text = str(out_frame)

            # File node
            file_elem = ET.SubElement(clipitem, "file", id=f"file-{idx}")
            ET.SubElement(file_elem, "name").text = os.path.basename(file_path)
            # URL format file://localhost/...
            norm_p = os.path.abspath(file_path).replace("\\", "/")
            url = f"file://localhost/{norm_p.lstrip('/')}"
            ET.SubElement(file_elem, "pathurl").text = url

            frate = ET.SubElement(file_elem, "rate")
            ET.SubElement(frate, "timebase").text = str(int(fps))
            ET.SubElement(frate, "ntsc").text = "FALSE"

            curr_frame += dur_frames

        ET.SubElement(sequence, "duration").text = str(curr_frame)

        tree = ET.ElementTree(xmeml)
        ET.indent(tree, space="  ", level=0)
        tree.write(output_path, encoding="utf-8", xml_declaration=True)
        logger.info(f"Native Premiere XML written to {output_path} ({len(clips)} clips, total frames={curr_frame})")


# Singleton instance
premiere_adapter = PremiereAdapter()
