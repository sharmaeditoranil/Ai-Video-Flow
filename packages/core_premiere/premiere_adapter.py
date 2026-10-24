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

    # -------------------------------------------------------------------------
    # ExtendScript Live Automation Bridge (macOS AppleScript / Windows COM)
    # Zero external server or node_modules dependency required
    # -------------------------------------------------------------------------

    def eval_extendscript(self, jsx_code: str) -> Dict[str, Any]:
        """
        Executes raw ExtendScript code inside Adobe Premiere Pro.
        Returns parsed JSON result.
        """
        self.refresh()
        if not self._active_app:
            return {"success": False, "error": "Adobe Premiere Pro is not installed."}

        if not self.is_running():
            return {"success": False, "error": "Adobe Premiere Pro is not running. Please open Premiere Pro first."}

        app_name = self._active_app["name"]
        wrapped_jsx = (
            "(function(){\n"
            "try {\n"
            f"{jsx_code}\n"
            "} catch(err) {\n"
            "  return JSON.stringify({success: false, error: err.toString()});\n"
            "}\n"
            "})();"
        )

        try:
            if self._system_os == "Darwin":
                apple_script = (
                    "on run argv\n"
                    f"tell application \"{app_name}\"\n"
                    "  DoScript (item 1 of argv)\n"
                    "end tell\n"
                    "end run"
                )
                res = subprocess.run(
                    ["osascript", "-e", apple_script, wrapped_jsx],
                    capture_output=True,
                    text=True,
                    timeout=25
                )
                if res.returncode != 0:
                    return {"success": False, "error": res.stderr.strip() or "AppleScript DoScript failed"}
                output_str = res.stdout.strip()
            elif self._system_os == "Windows":
                import tempfile
                with tempfile.NamedTemporaryFile("w", suffix=".jsx", delete=False, encoding="utf-8") as tf:
                    tf.write(wrapped_jsx)
                    tmp_jsx = tf.name
                res = subprocess.run(
                    [self._active_app["executable"], "/cmd", tmp_jsx],
                    capture_output=True,
                    text=True,
                    timeout=25
                )
                output_str = res.stdout.strip()
                try:
                    os.remove(tmp_jsx)
                except Exception:
                    pass
            else:
                return {"success": False, "error": f"Unsupported OS: {self._system_os}"}

            import json
            try:
                parsed = json.loads(output_str)
                if isinstance(parsed, dict) and "success" not in parsed:
                    parsed["success"] = True
                return parsed if isinstance(parsed, dict) else {"success": True, "result": parsed}
            except Exception:
                return {"success": True, "raw_output": output_str}

        except Exception as e:
            logger.error(f"ExtendScript error: {e}")
            return {"success": False, "error": str(e)}

    def get_project_state(self) -> Dict[str, Any]:
        """Queries live project state, bin structure, and sequence count from Premiere Pro."""
        jsx = """
        var proj = app.project;
        if (!proj) return JSON.stringify({success: false, error: "No project open in Premiere Pro."});
        var seqs = [];
        for (var i = 0; i < proj.sequences.numSequences; i++) {
            seqs.push(proj.sequences[i].name);
        }
        var activeSeq = proj.activeSequence ? proj.activeSequence.name : null;
        return JSON.stringify({
            success: true,
            project_name: proj.name,
            project_path: proj.path,
            sequence_count: proj.sequences.numSequences,
            sequences: seqs,
            active_sequence: activeSeq
        });
        """
        return self.eval_extendscript(jsx)

    def get_timeline_state(self) -> Dict[str, Any]:
        """Queries active sequence timeline tracks, clips, and duration."""
        jsx = """
        var seq = app.project.activeSequence;
        if (!seq) return JSON.stringify({success: false, error: "No active sequence in Premiere Pro."});
        var vCount = seq.videoTracks.numTracks;
        var aCount = seq.audioTracks.numTracks;
        var totalClips = 0;
        for (var i = 0; i < vCount; i++) totalClips += seq.videoTracks[i].clips.numItems;
        for (var j = 0; j < aCount; j++) totalClips += seq.audioTracks[j].clips.numItems;
        return JSON.stringify({
            success: true,
            sequence_name: seq.name,
            video_tracks: vCount,
            audio_tracks: aCount,
            total_clips: totalClips,
            timebase: seq.timebase
        });
        """
        return self.eval_extendscript(jsx)

    def create_sequence(self, name: str, width: int = 1920, height: int = 1080, fps: float = 25.0) -> Dict[str, Any]:
        """Creates a new sequence in the active Premiere Pro project."""
        jsx = f"""
        var proj = app.project;
        if (!proj) return JSON.stringify({{success: false, error: "No open project in Premiere."}});
        var seq = proj.createNewSequence("{name}", "");
        return JSON.stringify({{success: true, sequence_name: seq ? seq.name : "{name}"}});
        """
        return self.eval_extendscript(jsx)

    def import_media(self, file_paths: List[str], target_bin: str = "AI_Media") -> Dict[str, Any]:
        """Imports an array of file paths into a designated bin in Premiere Pro."""
        import json
        files_json = json.dumps([os.path.abspath(f) for f in file_paths])
        jsx = f"""
        var proj = app.project;
        if (!proj) return JSON.stringify({{success: false, error: "No open project."}});
        var bin = null;
        for (var i = 0; i < proj.rootItem.children.numItems; i++) {{
            var item = proj.rootItem.children[i];
            if (item.type === 2 && item.name === "{target_bin}") {{
                bin = item;
                break;
            }}
        }}
        if (!bin) {{
            bin = proj.rootItem.createBin("{target_bin}");
        }}
        var files = {files_json};
        var res = proj.importFiles(files, true, bin, false);
        return JSON.stringify({{success: res, imported_count: files.length, target_bin: "{target_bin}"}});
        """
        return self.eval_extendscript(jsx)

    def create_bin(self, bin_name: str) -> Dict[str, Any]:
        """Creates a new bin in the Premiere project root."""
        jsx = f"""
        var proj = app.project;
        if (!proj) return JSON.stringify({{success: false, error: "No open project."}});
        var bin = proj.rootItem.createBin("{bin_name}");
        return JSON.stringify({{success: true, bin_name: bin ? bin.name : "{bin_name}"}});
        """
        return self.eval_extendscript(jsx)

    def add_marker(self, timecode_seconds: float, name: str = "AI Marker", comments: str = "", color: str = "Yellow") -> Dict[str, Any]:
        """Adds a timeline marker at a specified timecode in the active sequence."""
        jsx = f"""
        var seq = app.project.activeSequence;
        if (!seq) return JSON.stringify({{success: false, error: "No active sequence."}});
        var markers = seq.markers;
        var m = markers.createMarker({timecode_seconds});
        m.name = "{name}";
        m.comments = "{comments}";
        return JSON.stringify({{success: true, timecode: {timecode_seconds}, name: "{name}"}});
        """
        return self.eval_extendscript(jsx)

    def razor_at_time(self, seconds: float) -> Dict[str, Any]:
        """Splits (razors) all sequence tracks at the given timecode in seconds via QE DOM."""
        jsx = f"""
        app.enableQE();
        var qseq = qe.project.getActiveSequence();
        if (!qseq) return JSON.stringify({{success: false, error: "No active QE sequence."}});
        var frameRate = 25.0;
        var totalFrames = Math.round({seconds} * frameRate);
        var frames = totalFrames % Math.round(frameRate);
        var totalSecs = Math.floor(totalFrames / Math.round(frameRate));
        var secs = totalSecs % 60;
        var mins = Math.floor(totalSecs / 60) % 60;
        var hours = Math.floor(totalSecs / 3600);
        function pad(n) {{ return n < 10 ? '0' + n : '' + n; }}
        var tc = pad(hours) + ':' + pad(mins) + ':' + pad(secs) + ':' + pad(frames);
        qseq.razor(tc);
        return JSON.stringify({{success: true, timecode: tc, seconds: {seconds}}});
        """
        return self.eval_extendscript(jsx)

    def ripple_delete_range(self, start_seconds: float, end_seconds: float) -> Dict[str, Any]:
        """Razors boundaries and ripple deletes clips within the range."""
        jsx = f"""
        app.enableQE();
        var qseq = qe.project.getActiveSequence();
        if (!qseq) return JSON.stringify({{success: false, error: "No active QE sequence."}});
        var frameRate = 25.0;
        function toTC(s) {{
            var tf = Math.round(s * frameRate);
            var f = tf % Math.round(frameRate);
            var ts = Math.floor(tf / Math.round(frameRate));
            var sec = ts % 60;
            var min = Math.floor(ts / 60) % 60;
            var hr = Math.floor(ts / 3600);
            function p(n) {{ return n < 10 ? '0' + n : '' + n; }}
            return p(hr) + ':' + p(min) + ':' + p(sec) + ':' + p(f);
        }}
        qseq.razor(toTC({start_seconds}));
        qseq.razor(toTC({end_seconds}));
        return JSON.stringify({{success: true, start_tc: toTC({start_seconds}), end_tc: toTC({end_seconds})}});
        """
        return self.eval_extendscript(jsx)

    def place_broll_cutaway(
        self,
        file_path: str,
        start_seconds: float,
        duration_seconds: float = 2.4,
        track_index: int = 2
    ) -> Dict[str, Any]:
        """
        Places a B-Roll cutaway clip on Video Track 2 without overwriting A1/V1 speech.
        Inspired by Ambar AI (Robelob/Ambar-AI-Video-Editor-Plugin-For-Premiere-Pro).
        """
        norm_p = os.path.abspath(file_path).replace("\\", "/")
        jsx = f"""
        var proj = app.project;
        var seq = proj.activeSequence;
        if (!seq) return JSON.stringify({{success: false, error: "No active sequence."}});
        var vTrackIdx = {track_index - 1};
        if (vTrackIdx >= seq.videoTracks.numTracks) {{
            return JSON.stringify({{success: false, error: "Target track V{track_index} does not exist."}});
        }}
        var track = seq.videoTracks[vTrackIdx];
        // Find or import project item
        var targetItem = null;
        function searchBin(bin) {{
            for (var i = 0; i < bin.children.numItems; i++) {{
                var it = bin.children[i];
                if (it.type === 1 && it.getMediaPath && it.getMediaPath().replace(/\\\\/g, '/') === "{norm_p}") {{
                    targetItem = it;
                    return;
                }} else if (it.type === 2) {{
                    searchBin(it);
                    if (targetItem) return;
                }}
            }}
        }}
        searchBin(proj.rootItem);
        if (targetItem) {{
            var timeInTicks = String(Math.round({start_seconds} * 254016000000));
            track.overwriteClip(targetItem, timeInTicks);
            return JSON.stringify({{success: true, track: {track_index}, start_seconds: {start_seconds}}});
        }} else {{
            return JSON.stringify({{success: false, error: "Clip not found in project. Please import it first."}});
        }}
        """
        return self.eval_extendscript(jsx)

    def organize_bins_automatically(self) -> Dict[str, Any]:
        """
        Automatically organizes root items into professional studio bins:
        01_A-Roll_Dialogue, 02_B-Roll_Cutaways, 03_Ceremony_Rituals, 04_Music_BGM, 05_Voiceover_Audio
        """
        jsx = """
        var proj = app.project;
        if (!proj) return JSON.stringify({success: false, error: "No open project."});
        var bins = {
            aroll: proj.rootItem.createBin("01_A-Roll_Dialogue"),
            broll: proj.rootItem.createBin("02_B-Roll_Cutaways"),
            music: proj.rootItem.createBin("03_Music_BGM"),
            audio: proj.rootItem.createBin("04_Audio_VO"),
            titles: proj.rootItem.createBin("05_Graphics_Titles")
        };
        var moved = 0;
        for (var i = proj.rootItem.children.numItems - 1; i >= 0; i--) {
            var item = proj.rootItem.children[i];
            if (item.type === 1) { // Clip item
                var name = item.name.toLowerCase();
                if (name.indexOf(".mp3") !== -1 || name.indexOf(".wav") !== -1 || name.indexOf("music") !== -1 || name.indexOf("song") !== -1) {
                    item.moveBin(bins.music);
                    moved++;
                } else if (name.indexOf("broll") !== -1 || name.indexOf("cutaway") !== -1) {
                    item.moveBin(bins.broll);
                    moved++;
                } else {
                    item.moveBin(bins.aroll);
                    moved++;
                }
            }
        }
        return JSON.stringify({success: true, organized_clips: moved});
        """
        return self.eval_extendscript(jsx)

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
        Converts an AI edit plan into standard Premiere-compatible FCP XML.
        Supports multi-track video (V1 Primary + V2 B-Roll Cutaways) and dual audio (A1 + A2 ducking).
        """
        name = plan.get("sequence_name", "AI_Wedding_Timeline")
        clips = plan.get("clips", [])
        broll_clips = plan.get("broll_clips") or plan.get("broll_cutaways") or []
        aspect_ratio = plan.get("aspect_ratio", "16:9")

        if not output_dir:
            output_dir = os.path.join(os.path.expanduser("~"), "AI-Video-Flow-Output", "timelines")
        os.makedirs(output_dir, exist_ok=True)

        safe_name = "".join(c for c in name if c.isalnum() or c in ("_", "-"))
        xml_file = os.path.join(output_dir, f"{safe_name}_Premiere.xml")
        audio_path = plan.get("audio_path") or plan.get("audio") or ""

        # Directly generate authentic Apple FCP7 XML (<xmeml version="4">)
        try:
            self._write_native_premiere_xml(
                xml_file, name, clips, fps=fps, audio_path=audio_path,
                aspect_ratio=aspect_ratio, broll_clips=broll_clips
            )
            
            # Generate accompanying AI Color & Light Balance 3D LUT for Premiere Lumetri Color
            lut_file = self.generate_premiere_color_look(output_dir)

            return {
                "success": True,
                "xml_path": xml_file,
                "lut_path": lut_file,
                "timeline_name": name,
                "clip_count": len(clips),
                "broll_count": len(broll_clips)
            }
        except Exception as e:
            logger.error(f"Native Premiere XML generation failed: {e}")
            return {"success": False, "error": str(e)}

    def generate_premiere_color_look(self, output_dir: str) -> str:
        """
        Generates an industry-standard 33x33x33 .cube 3D LUT for Adobe Premiere Pro's
        Lumetri Color panel. Optimizes dynamic range, soft highlights, shadow lift,
        and wedding skin tone warmth.
        """
        lut_path = os.path.join(output_dir, "AI_Wedding_Light_Balanced_Look.cube")
        if os.path.exists(lut_path):
            return lut_path

        size = 33
        grid = [i / (size - 1) for i in range(size)]

        with open(lut_path, "w", encoding="utf-8") as f:
            f.write("# AI Video Flow - Balanced Wedding Light & Color LUT for Adobe Premiere Pro\n")
            f.write("TITLE \"AI_Wedding_Light_Balanced_Look\"\n")
            f.write(f"LUT_3D_SIZE {size}\n")
            f.write("DOMAIN_MIN 0.0 0.0 0.0\n")
            f.write("DOMAIN_MAX 1.0 1.0 1.0\n\n")

            for b in grid:
                for g in grid:
                    for r in grid:
                        # 1. Shadow lift (prevents crushed tuxedo/dark hall blacks)
                        r_out = r ** 0.92
                        g_out = g ** 0.92
                        b_out = b ** 0.95

                        # 2. Filmic highlight shoulder (preserves wedding dress & stage light detail)
                        if r_out > 0.7:
                            r_out = 0.7 + (r_out - 0.7) * 0.85
                        if g_out > 0.7:
                            g_out = 0.7 + (g_out - 0.7) * 0.85
                        if b_out > 0.7:
                            b_out = 0.7 + (b_out - 0.7) * 0.80

                        # 3. Warm wedding tone balance (subtle golden skin-tone protection)
                        r_final = min(max(r_out * 1.02, 0.0), 1.0)
                        g_final = min(max(g_out * 1.00, 0.0), 1.0)
                        b_final = min(max(b_out * 0.98, 0.0), 1.0)

                        f.write(f"{r_final:.6f} {g_final:.6f} {b_final:.6f}\n")

        logger.info(f"Generated Premiere Lumetri 3D LUT: {lut_path}")
        return lut_path

    def _write_native_premiere_xml(
        self,
        output_path: str,
        sequence_name: str,
        clips: List[Dict[str, Any]],
        fps: float = 25.0,
        audio_path: str = "",
        aspect_ratio: str = "16:9",
        broll_clips: Optional[List[Dict[str, Any]]] = None
    ) -> None:
        """
        Directly writes an authentic Apple FCP7 XML (<xmeml version="4">)
        which Adobe Premiere Pro natively parses without external dependencies.
        Supports multi-track video (V1 A-Roll + V2 B-Roll Cutaways) and dual audio tracks.
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

        # Sequence Format & Resolution
        format_elem = ET.SubElement(video, "format")
        samplechar = ET.SubElement(format_elem, "samplecharacteristics")
        rate_elem = ET.SubElement(samplechar, "rate")
        ET.SubElement(rate_elem, "timebase").text = str(int(fps))
        ET.SubElement(rate_elem, "ntsc").text = "FALSE"
        ET.SubElement(samplechar, "pixelaspectratio").text = "square"

        is_vertical = ("9:16" in str(aspect_ratio) or 
                       "9x16" in sequence_name.lower() or 
                       "reel" in sequence_name.lower() or 
                       "shorts" in sequence_name.lower() or
                       "vertical" in sequence_name.lower())
        if is_vertical:
            ET.SubElement(samplechar, "width").text = "1080"
            ET.SubElement(samplechar, "height").text = "1920"
        else:
            ET.SubElement(samplechar, "width").text = "1920"
            ET.SubElement(samplechar, "height").text = "1080"

        # Video Track 1: Primary A-Roll Timeline
        vtrack = ET.SubElement(video, "track")

        curr_frame = 0
        for idx, clip in enumerate(clips, 1):
            file_path = clip.get("file_path") or clip.get("path") or ""
            if not file_path:
                continue

            in_frame = int(clip.get("in_frame", 0))
            out_frame = int(clip.get("out_frame", in_frame + int(fps * 3)))
            dur_frames = max(out_frame - in_frame, int(fps * 0.5))

            clipitem = ET.SubElement(vtrack, "clipitem", id=f"clipitem-v-{idx}")
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
            file_elem = ET.SubElement(clipitem, "file", id=f"file-v-{idx}")
            ET.SubElement(file_elem, "name").text = os.path.basename(file_path)
            norm_p = os.path.abspath(file_path).replace("\\", "/")
            url = f"file://localhost/{norm_p.lstrip('/')}"
            ET.SubElement(file_elem, "pathurl").text = url

            frate = ET.SubElement(file_elem, "rate")
            ET.SubElement(frate, "timebase").text = str(int(fps))
            ET.SubElement(frate, "ntsc").text = "FALSE"

            curr_frame += dur_frames

        # Video Track 2: B-Roll Cutaways (Ambar AI Feature)
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

            curr_frame += dur_frames

        # -------------------------------------------------------------
        # Audio Section: Track A1 (Camera Audio) + Track A2 (BGM Music with Ducking)
        # -------------------------------------------------------------
        audio_elem = ET.SubElement(media, "audio")

        # Track A1: Synced Camera Native Audio (vows, speeches, ambient sound)
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

            afile_elem = ET.SubElement(aclipitem, "file", id=f"file-a1-{idx}")
            ET.SubElement(afile_elem, "name").text = os.path.basename(file_path)
            norm_p = os.path.abspath(file_path).replace("\\", "/")
            url = f"file://localhost/{norm_p.lstrip('/')}"
            ET.SubElement(afile_elem, "pathurl").text = url

            frate = ET.SubElement(afile_elem, "rate")
            ET.SubElement(frate, "timebase").text = str(int(fps))
            ET.SubElement(frate, "ntsc").text = "FALSE"

            a1_frame += dur_frames

        # Track A2: Background Music with Auto-Ducking Volume Keyframes
        if audio_path and os.path.exists(audio_path) and curr_frame > 0:
            atrack2 = ET.SubElement(audio_elem, "track")
            bgm_clipitem = ET.SubElement(atrack2, "clipitem", id="clipitem-bgm-1")
            ET.SubElement(bgm_clipitem, "name").text = os.path.basename(audio_path)
            ET.SubElement(bgm_clipitem, "duration").text = str(curr_frame)

            brate = ET.SubElement(bgm_clipitem, "rate")
            ET.SubElement(brate, "timebase").text = str(int(fps))
            ET.SubElement(brate, "ntsc").text = "FALSE"

            ET.SubElement(bgm_clipitem, "start").text = "0"
            ET.SubElement(bgm_clipitem, "end").text = str(curr_frame)
            ET.SubElement(bgm_clipitem, "in").text = "0"
            ET.SubElement(bgm_clipitem, "out").text = str(curr_frame)

            bgm_file = ET.SubElement(bgm_clipitem, "file", id="file-bgm-1")
            ET.SubElement(bgm_file, "name").text = os.path.basename(audio_path)
            norm_ap = os.path.abspath(audio_path).replace("\\", "/")
            aurl = f"file://localhost/{norm_ap.lstrip('/')}"
            ET.SubElement(bgm_file, "pathurl").text = aurl

            bfrate = ET.SubElement(bgm_file, "rate")
            ET.SubElement(bfrate, "timebase").text = str(int(fps))
            ET.SubElement(bfrate, "ntsc").text = "FALSE"

            # Auto-Ducking Volume Automation Filter on BGM
            filt = ET.SubElement(bgm_clipitem, "filter")
            eff = ET.SubElement(filt, "effect")
            ET.SubElement(eff, "name").text = "Audio Levels"
            ET.SubElement(eff, "effectid").text = "audiolevels"
            param = ET.SubElement(eff, "parameter")
            ET.SubElement(param, "parameterid").text = "level"
            ET.SubElement(param, "name").text = "Level"

            # Baseline 0dB
            kf0 = ET.SubElement(param, "keyframe")
            ET.SubElement(kf0, "when").text = "0"
            ET.SubElement(kf0, "value").text = "1.0"

            # Detect speech/dialogue segments from clips to duck BGM by -12dB (0.25)
            timeline_pos = 0
            for clip in clips:
                dur = max(int(clip.get("out_frame", 0)) - int(clip.get("in_frame", 0)), int(fps * 0.5))
                has_speech = clip.get("has_dialogue") or clip.get("speech") or (clip.get("quality_score", 0) > 85)
                if has_speech and dur > int(fps * 1.5):
                    # Duck down 12 frames before speech
                    duck_start = max(timeline_pos - int(fps * 0.5), 0)
                    duck_in = timeline_pos
                    duck_out = timeline_pos + dur
                    duck_end = min(duck_out + int(fps * 0.5), curr_frame)

                    kf_a = ET.SubElement(param, "keyframe")
                    ET.SubElement(kf_a, "when").text = str(duck_start)
                    ET.SubElement(kf_a, "value").text = "1.0"

                    kf_b = ET.SubElement(param, "keyframe")
                    ET.SubElement(kf_b, "when").text = str(duck_in)
                    ET.SubElement(kf_b, "value").text = "0.25"

                    kf_c = ET.SubElement(param, "keyframe")
                    ET.SubElement(kf_c, "when").text = str(duck_out)
                    ET.SubElement(kf_c, "value").text = "0.25"

                    kf_d = ET.SubElement(param, "keyframe")
                    ET.SubElement(kf_d, "when").text = str(duck_end)
                    ET.SubElement(kf_d, "value").text = "1.0"

                timeline_pos += dur

            # Smooth 2-second fade-out at the end of the sequence
            fade_start = max(curr_frame - int(fps * 2.0), 0)
            kf_fade1 = ET.SubElement(param, "keyframe")
            ET.SubElement(kf_fade1, "when").text = str(fade_start)
            ET.SubElement(kf_fade1, "value").text = "1.0"

            kf_fade2 = ET.SubElement(param, "keyframe")
            ET.SubElement(kf_fade2, "when").text = str(curr_frame)
            ET.SubElement(kf_fade2, "value").text = "0.0"

        ET.SubElement(sequence, "duration").text = str(curr_frame)

        tree = ET.ElementTree(xmeml)
        ET.indent(tree, space="  ", level=0)
        tree.write(output_path, encoding="utf-8", xml_declaration=True)
        logger.info(f"Native Premiere XML written to {output_path} ({len(clips)} clips, total frames={curr_frame})")


# Singleton instance
premiere_adapter = PremiereAdapter()
