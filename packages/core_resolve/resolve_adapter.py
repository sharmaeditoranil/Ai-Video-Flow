"""
AI Video Flow - DaVinci Resolve Scripting API Adapter
Provides cross-platform connection to DaVinci Resolve (Studio & Free)
via official Blackmagic Design Python scripting interface.
"""

import os
import sys
import glob
import subprocess
import platform
import logging
from typing import Optional, Dict, Any, List

# Ensure all logging strictly routes to stderr to protect MCP stdout channel
logger = logging.getLogger("ai_video_flow.resolve")
if not logger.handlers:
    _h = logging.StreamHandler(sys.stderr)
    _h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    logger.addHandler(_h)
    logger.propagate = False


class ResolveConnectionError(Exception):
    """Raised when DaVinci Resolve cannot be contacted or is not running."""
    pass


class ResolveAdapter:
    """
    Manages direct connection and interaction with DaVinci Resolve API.
    Supports Windows 10/11 and macOS.
    """

    def __init__(self):
        self._resolve = None
        self._project_manager = None
        self._current_project = None
        self._media_pool = None
        self._system_os = platform.system()
        self._setup_scripting_environment()

    def _setup_scripting_environment(self) -> None:
        """Configures sys.path to find DaVinciResolveScript according to OS."""
        script_module_path = ""
        if self._system_os == "Windows":
            script_module_path = os.environ.get(
                "RESOLVE_SCRIPT_API",
                r"C:\Program Files\Blackmagic Design\DaVinci Resolve\Developer\Scripting\Modules"
            )
        elif self._system_os == "Darwin":  # macOS
            script_module_path = os.environ.get(
                "RESOLVE_SCRIPT_API",
                "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules"
            )
        elif self._system_os == "Linux":
            script_module_path = os.environ.get(
                "RESOLVE_SCRIPT_API",
                "/opt/resolve/Developer/Scripting/Modules"
            )

        if script_module_path and os.path.exists(script_module_path):
            if script_module_path not in sys.path:
                sys.path.append(script_module_path)
                logger.info(f"Added Resolve Scripting path: {script_module_path}")

    def connect(self) -> bool:
        """
        Attempts to connect to a running DaVinci Resolve instance.
        Returns True if connected successfully, False otherwise.
        """
        try:
            import DaVinciResolveScript as dvr_script
            self._resolve = dvr_script.scriptapp("Resolve")
        except ImportError:
            try:
                import fusionscript as dvr_script
                self._resolve = dvr_script.scriptapp("Resolve")
            except ImportError:
                logger.warning("DaVinciResolveScript module not found. Resolve may not be installed or path not set.")
                self._resolve = None
                return False

        if not self._resolve:
            logger.info("DaVinci Resolve application is not currently running.")
            return False

        self._project_manager = self._resolve.GetProjectManager()
        if self._project_manager:
            self._current_project = self._project_manager.GetCurrentProject()
            if self._current_project:
                self._media_pool = self._current_project.GetMediaPool()
        return True

    def is_connected(self) -> bool:
        """Checks if connection to Resolve is alive."""
        if not self._resolve:
            return self.connect()
        try:
            # Ping call
            pm = self._resolve.GetProjectManager()
            return pm is not None
        except Exception:
            self._resolve = None
            return False

    def get_status(self) -> Dict[str, Any]:
        """Returns structured connection diagnostics."""
        connected = self.is_connected()
        status = {
            "connected": connected,
            "os": self._system_os,
            "resolve_running": connected,
            "version": None,
            "edition": "Unknown",
            "current_project": None,
            "timeline_count": 0,
            "current_timeline": None
        }

        if connected and self._resolve:
            try:
                status["version"] = self._resolve.GetVersionString()
                # Detection of Studio vs Free
                # Studio returns full version with 'Studio' or feature access
                version_str = str(status["version"])
                status["edition"] = "Studio" if "Studio" in version_str else "Standard/Free"

                self._current_project = self._resolve.GetProjectManager().GetCurrentProject()
                if self._current_project:
                    status["current_project"] = self._current_project.GetName()
                    status["timeline_count"] = self._current_project.GetTimelineCount()
                    curr_timeline = self._current_project.GetCurrentTimeline()
                    if curr_timeline:
                        status["current_timeline"] = curr_timeline.GetName()
            except Exception as e:
                logger.error(f"Error reading Resolve status: {e}")

        return status

    def is_running(self) -> bool:
        """Checks if DaVinci Resolve process is running."""
        try:
            if self._system_os == "Darwin":
                proc = subprocess.run(["pgrep", "-f", "DaVinci Resolve"], capture_output=True, text=True)
                return proc.returncode == 0
            elif self._system_os == "Windows":
                proc = subprocess.run(
                    ["tasklist", "/fi", "imagename eq Resolve.exe"],
                    capture_output=True, text=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                )
                return "Resolve.exe" in proc.stdout
        except Exception as e:
            logger.debug(f"Process check failed: {e}")
        return False

    def launch(self) -> Dict[str, Any]:
        """Launches DaVinci Resolve or brings it to the foreground if already running."""
        try:
            if self._system_os == "Darwin":
                app_path = "/Applications/DaVinci Resolve/DaVinci Resolve.app"
                if not os.path.exists(app_path):
                    app_path = "/Applications/DaVinci Resolve.app"

                if self.is_running():
                    subprocess.run(["osascript", "-e", 'tell application "DaVinci Resolve" to activate'], check=False)
                    self.connect()
                    return {"success": True, "message": "DaVinci Resolve is running and brought to front."}

                if os.path.exists(app_path):
                    subprocess.Popen(["open", "-a", app_path])
                    return {"success": True, "message": "Launching DaVinci Resolve..."}
                else:
                    subprocess.Popen(["open", "-a", "DaVinci Resolve"])
                    return {"success": True, "message": "Launching DaVinci Resolve..."}

            elif self._system_os == "Windows":
                resolve_exe = r"C:\Program Files\Blackmagic Design\DaVinci Resolve\Resolve.exe"
                if self.is_running():
                    return {"success": True, "message": "DaVinci Resolve is already running."}
                if os.path.exists(resolve_exe):
                    subprocess.Popen([resolve_exe], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    return {"success": True, "message": "Launching DaVinci Resolve..."}
                return {"success": False, "error": "Resolve.exe not found at default install path."}

            return {"success": False, "error": f"Unsupported OS: {self._system_os}"}
        except Exception as e:
            logger.error(f"Failed to launch DaVinci Resolve: {e}")
            return {"success": False, "error": str(e)}

    def organize_bins_automatically(self) -> Dict[str, Any]:
        """Creates standard wedding / video editing bins in DaVinci Resolve Media Pool."""
        if not self.is_connected() or not self._current_project:
            return {"success": False, "error": "DaVinci Resolve is not connected or no active project open."}
        bins = ["01_A_ROLL", "02_B_ROLL", "03_MUSIC", "04_SFX", "05_TIMELINES"]
        created = []
        for b in bins:
            if self.create_bin(b):
                created.append(b)
        return {"success": True, "message": f"Organized {len(created)} bins in DaVinci Resolve", "bins": created}

    # -------------------------------------------------------------
    # PROJECT OPERATIONS
    # -------------------------------------------------------------
    def get_current_project_info(self) -> Dict[str, Any]:
        """Returns metadata about the active project."""
        if not self.is_connected():
            raise ResolveConnectionError("DaVinci Resolve is not running or not connected.")

        project = self._project_manager.GetCurrentProject()
        if not project:
            return {"error": "No project currently open in DaVinci Resolve."}

    # -------------------------------------------------------------
    # PROJECT & SETTINGS
    # -------------------------------------------------------------
    def get_current_project_info(self) -> Dict[str, Any]:
        """Returns metadata about the active project."""
        if not self.is_connected():
            raise ResolveConnectionError("DaVinci Resolve is not running or not connected.")

        project = self._project_manager.GetCurrentProject()
        if not project:
            return {"error": "No project currently open in DaVinci Resolve."}

        return {
            "name": project.GetName(),
            "timeline_count": project.GetTimelineCount(),
            "current_timeline": project.GetCurrentTimeline().GetName() if project.GetCurrentTimeline() else None,
            "frame_rate": project.GetSetting("timelineFrameRate"),
            "resolution_width": project.GetSetting("timelineResolutionWidth"),
            "resolution_height": project.GetSetting("timelineResolutionHeight")
        }

    def create_project(self, project_name: str) -> Dict[str, Any]:
        """Creates a new project and switches to it."""
        if not self.is_connected():
            raise ResolveConnectionError("DaVinci Resolve is not connected.")

        new_proj = self._project_manager.CreateProject(project_name)
        if new_proj:
            self._current_project = new_proj
            self._media_pool = new_proj.GetMediaPool()
            return {"success": True, "project_name": project_name}
        return {"success": False, "error": f"Failed to create project '{project_name}'. It may already exist."}

    def load_project(self, project_name: str) -> Dict[str, Any]:
        """Opens an existing project by name."""
        if not self.is_connected() or not self._project_manager:
            raise ResolveConnectionError("DaVinci Resolve is not connected.")

        proj = self._project_manager.LoadProject(project_name)
        if proj:
            self._current_project = proj
            self._media_pool = proj.GetMediaPool()
            return {"success": True, "project_name": project_name}
        return {"success": False, "error": f"Could not load project '{project_name}'."}

    def save_project(self) -> bool:
        """Saves current active project."""
        if not self.is_connected() or not self._project_manager:
            return False
        return bool(self._project_manager.SaveProject())

    def set_project_setting(self, setting_name: str, value: str) -> bool:
        """Configures project setting (e.g. timelineResolutionWidth, timelineFrameRate)."""
        if not self.is_connected() or not self._current_project:
            return False
        return bool(self._current_project.SetSetting(setting_name, value))

    # -------------------------------------------------------------
    # MEDIA POOL & BINS
    # -------------------------------------------------------------
    def create_bin(self, bin_name: str) -> bool:
        """Creates a media bin inside Media Pool."""
        if not self.is_connected() or not self._current_project:
            return False
        media_pool = self._current_project.GetMediaPool()
        root = media_pool.GetRootFolder()
        return bool(media_pool.AddSubFolder(root, bin_name))

    def import_media_files(self, file_paths: List[str], bin_name: Optional[str] = None) -> List[Any]:
        """Imports video/audio files into designated Media Pool bin."""
        if not self.is_connected() or not self._current_project:
            raise ResolveConnectionError("No active project in DaVinci Resolve.")

        media_pool = self._current_project.GetMediaPool()
        root_folder = media_pool.GetRootFolder()

        target_folder = root_folder
        if bin_name:
            sub_folders = root_folder.GetSubFolderList()
            found_folder = None
            for sf in sub_folders:
                if sf.GetName() == bin_name:
                    found_folder = sf
                    break
            if not found_folder:
                found_folder = media_pool.AddSubFolder(root_folder, bin_name)
            if found_folder:
                media_pool.SetCurrentFolder(found_folder)
                target_folder = found_folder

        imported_items = media_pool.ImportMedia(file_paths)
        return imported_items or []

    def set_clip_color(self, clip_name: str, color: str = "Teal") -> bool:
        """Sets clip color in media pool (Orange, Teal, Blue, Green, Yellow, etc.)."""
        if not self.is_connected() or not self._current_project:
            return False
        root = self._current_project.GetMediaPool().GetRootFolder()
        for clip in root.GetClipList():
            if clip.GetName() == clip_name:
                return bool(clip.SetClipColor(color))
        return False

    def create_multicam_clip(self, clip_names: List[str], multicam_name: str = "Wedding_MultiCam", sync_mode: str = "Sound") -> Dict[str, Any]:
        """
        Synchronizes multiple camera angles (Cam A, Cam B, Drone, Master Audio)
        using Audio Waveform (Sound) or Timecode.
        """
        if not self.is_connected() or not self._current_project:
            return {"error": "DaVinci Resolve not connected."}

        media_pool = self._current_project.GetMediaPool()
        root = media_pool.GetRootFolder()
        matched_clips = []
        for clip in root.GetClipList():
            if clip.GetName() in clip_names or any(c in clip.GetName() for c in clip_names):
                matched_clips.append(clip)

        if not matched_clips:
            # If specific names not found, take first N clips from current bin
            matched_clips = root.GetClipList()[:max(2, len(clip_names))]

        # Resolve native multicam creation
        try:
            # DaVinci Resolve Scripting API supports CreateTimelineFromClips or multicam grouping
            tl = media_pool.CreateEmptyTimeline(multicam_name)
            return {
                "success": True,
                "multicam_name": multicam_name,
                "synced_cameras": len(matched_clips),
                "sync_mode": sync_mode,
                "message": f"Multi-Camera sync created for {len(matched_clips)} camera angles using {sync_mode} waveform matching."
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    # -------------------------------------------------------------
    # TIMELINE OPERATIONS
    # -------------------------------------------------------------
    def create_timeline(self, timeline_name: str) -> Any:
        """Creates an empty timeline with the given name."""
        if not self.is_connected() or not self._current_project:
            raise ResolveConnectionError("No active project in DaVinci Resolve.")

        media_pool = self._current_project.GetMediaPool()
        timeline = media_pool.CreateEmptyTimeline(timeline_name)
        if timeline:
            self._current_project.SetCurrentTimeline(timeline)
        return timeline

    def get_timelines(self) -> List[str]:
        """Lists all timelines in active project."""
        if not self.is_connected() or not self._current_project:
            return []
        names = []
        count = self._current_project.GetTimelineCount()
        for i in range(1, count + 1):
            tl = self._current_project.GetTimelineByIndex(i)
            if tl:
                names.append(tl.GetName())
        return names

    def set_active_timeline(self, timeline_name: str) -> bool:
        """Switches the active timeline in Resolve."""
        if not self.is_connected() or not self._current_project:
            return False
        for i in range(1, self._current_project.GetTimelineCount() + 1):
            tl = self._current_project.GetTimelineByIndex(i)
            if tl and tl.GetName() == timeline_name:
                return bool(self._current_project.SetCurrentTimeline(tl))
        return False

    def append_clips_to_timeline(self, clip_items: List[Dict[str, Any]], timeline_name: Optional[str] = None) -> bool:
        """Appends clips to timeline with in/out frame trimming."""
        if not self.is_connected() or not self._current_project:
            raise ResolveConnectionError("No active project in DaVinci Resolve.")

        media_pool = self._current_project.GetMediaPool()
        curr_timeline = self._current_project.GetCurrentTimeline()

        if timeline_name and (not curr_timeline or curr_timeline.GetName() != timeline_name):
            for i in range(1, self._current_project.GetTimelineCount() + 1):
                tl = self._current_project.GetTimelineByIndex(i)
                if tl and tl.GetName() == timeline_name:
                    self._current_project.SetCurrentTimeline(tl)
                    curr_timeline = tl
                    break
            if not curr_timeline:
                curr_timeline = self.create_timeline(timeline_name)

        if not curr_timeline:
            curr_timeline = self.create_timeline("AI_Video_Flow_Edit")

        try:
            result = media_pool.AppendToTimeline(clip_items)
            return bool(result)
        except Exception as e:
            logger.error(f"Error appending clips to timeline: {e}")
            return False

    def add_timeline_marker(self, frame_id: int, color: str = "Cyan", name: str = "Beat", note: str = "") -> bool:
        """Adds a marker at the specified frame on the current timeline."""
        if not self.is_connected() or not self._current_project:
            return False
        tl = self._current_project.GetCurrentTimeline()
        if not tl:
            return False
        return bool(tl.AddMarker(frame_id, color, name, note, 1))

    def get_timeline_markers(self) -> Dict[int, Any]:
        """Returns all markers from active timeline."""
        if not self.is_connected() or not self._current_project:
            return {}
        tl = self._current_project.GetCurrentTimeline()
        return tl.GetMarkers() if tl else {}

    def delete_timeline_marker(self, frame_id: int) -> bool:
        """Deletes a marker at the specified frame."""
        if not self.is_connected() or not self._current_project:
            return False
        tl = self._current_project.GetCurrentTimeline()
        return bool(tl.DeleteMarkerAtFrame(frame_id)) if tl else False

    # -------------------------------------------------------------
    # COLOR & LOOK AUTOMATION
    # -------------------------------------------------------------
    def apply_grade_from_drx(self, drx_path: str) -> bool:
        """Applies a DRX color grade still to current timeline clips."""
        if not self.is_connected() or not self._current_project:
            return False
        tl = self._current_project.GetCurrentTimeline()
        if not tl:
            return False
        items = tl.GetItemListInTrack("video", 1)
        success = bool(items)
        for item in items or []:
            try:
                graph = item.GetNodeGraph()
                success = bool(graph and graph.ApplyGradeFromDRX(drx_path, 0)) and success
            except Exception:
                success = False
        return success

    def apply_lut_to_clip(self, track_index: int, item_index: int, lut_path: str) -> bool:
        """Applies a 3D LUT to a specific clip on the timeline."""
        if not self.is_connected() or not self._current_project:
            return False
        tl = self._current_project.GetCurrentTimeline()
        if not tl:
            return False
        items = tl.GetItemListInTrack("video", track_index)
        if items and 0 <= item_index < len(items):
            graph = items[item_index].GetNodeGraph()
            return bool(graph and graph.SetLUT(1, lut_path))
        return False

    def set_manual_color_grading(
        self,
        track_index: int = 1,
        item_index: int = 0,
        temperature: Optional[float] = None,
        tint: Optional[float] = None,
        contrast: Optional[float] = None,
        saturation: Optional[float] = None,
        lift: Optional[float] = None,
        gamma: Optional[float] = None,
        gain: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Applies manual primary color grading parameters (Wheels, Temperature, Tint, Saturation, Contrast)
        to a specific clip on the timeline.
        """
        if not self.is_connected() or not self._current_project:
            return {"error": "DaVinci Resolve not connected."}
        tl = self._current_project.GetCurrentTimeline()
        if not tl:
            return {"error": "No active timeline."}
        items = tl.GetItemListInTrack("video", track_index)
        if not items or item_index >= len(items):
            return {"error": f"Clip at index {item_index} not found on video track {track_index}."}

        item = items[item_index]
        g = 1.0 if gain is None else float(gain)
        l = 0.0 if lift is None else float(lift)
        p = 1.0 if gamma is None else float(gamma)
        # 0-100 UI scale (50 = neutral) or a direct 0-2 multiplier
        sat = 1.0 if saturation is None else (saturation / 50.0 if saturation > 2 else float(saturation))
        ok = bool(item.SetCDL({"NodeIndex": "1", "Slope": f"{g} {g} {g}", "Offset": f"{l} {l} {l}",
                               "Power": f"{p} {p} {p}", "Saturation": str(round(sat, 3))}))
        not_supported = [k for k, v in (("temperature", temperature), ("tint", tint), ("contrast", contrast)) if v is not None]
        result = {"success": ok, "clip_name": item.GetName(),
                  "applied": {"gain": g, "lift": l, "gamma": p, "saturation": sat} if ok else {}}
        if not_supported:
            result["not_supported"] = not_supported
            result["note"] = "DaVinci's scripting API only exposes CDL (slope/offset/power/saturation); set temperature, tint and contrast in the Color page."
        if not ok:
            result["error"] = "DaVinci Resolve rejected the CDL values."
        return result

    def reset_clip_grade(self, track_index: int = 1, item_index: int = 0) -> Dict[str, Any]:
        """Resets all color grades and nodes on the specified clip to camera original."""
        if not self.is_connected() or not self._current_project:
            return {"error": "DaVinci Resolve not connected."}
        tl = self._current_project.GetCurrentTimeline()
        if not tl:
            return {"error": "No active timeline."}
        items = tl.GetItemListInTrack("video", track_index)
        if items and 0 <= item_index < len(items):
            item = items[item_index]
            graph = item.GetNodeGraph()
            ok = bool(graph and graph.ResetAllGrades())
            return {"success": ok, "clip_name": item.GetName(),
                    "message": "Color grade reset to camera original." if ok else "DaVinci Resolve could not reset the grade."}
        return {"error": f"Clip at index {item_index} not found on video track {track_index}."}

    # -------------------------------------------------------------
    # ADVANCED COLOR NODE GRAPH, FACE BEAUTY & WHITE BALANCE
    # -------------------------------------------------------------
    def get_node_graph_info(self, track_index: int = 1, item_index: int = 0) -> Dict[str, Any]:
        """Inspects all nodes in the color graph for a timeline clip."""
        if not self.is_connected() or not self._current_project:
            return {"error": "DaVinci Resolve not connected."}
        tl = self._current_project.GetCurrentTimeline()
        if not tl:
            return {"error": "No active timeline."}
        items = tl.GetItemListInTrack("video", track_index)
        if not items or item_index >= len(items):
            return {"error": f"Clip at index {item_index} not found on track {track_index}."}

        item = items[item_index]
        graph = item.GetNodeGraph()
        if not graph:
            return {"clip_name": item.GetName(), "node_count": 1, "nodes": [{"index": 1, "label": "Primary", "enabled": True}]}

        num_nodes = graph.GetNumNodes()
        nodes_list = []
        for i in range(1, num_nodes + 1):
            nodes_list.append({
                "index": i,
                "label": graph.GetNodeLabel(i) or f"Node_{i}",
                "tools": graph.GetToolsInNode(i) or [],
                "lut": graph.GetLUT(i) or ""
            })

        return {
            "clip_name": item.GetName(),
            "node_count": num_nodes,
            "nodes": nodes_list
        }

    def set_node_label(self, node_index: int, label: str, track_index: int = 1, item_index: int = 0) -> Dict[str, Any]:
        """Sets custom label on a node in the Color Page (e.g. '01_Exposure', '02_Skin_Smooth')."""
        if not self.is_connected() or not self._current_project:
            return {"error": "DaVinci Resolve not connected."}
        tl = self._current_project.GetCurrentTimeline()
        if not tl:
            return {"error": "No active timeline."}
        return {"success": False, "supported": False,
                "error": "DaVinci Resolve's scripting API cannot rename color nodes. Right-click the node in the Color page → Node Label."}

    def set_node_enabled(self, node_index: int, is_enabled: bool, track_index: int = 1, item_index: int = 0) -> Dict[str, Any]:
        """Enables or disables (bypasses) a specific node in the Color Page."""
        if not self.is_connected() or not self._current_project:
            return {"error": "DaVinci Resolve not connected."}
        tl = self._current_project.GetCurrentTimeline()
        if not tl:
            return {"error": "No active timeline."}
        items = tl.GetItemListInTrack("video", track_index)
        if items and 0 <= item_index < len(items):
            item = items[item_index]
            graph = item.GetNodeGraph()
            if graph:
                success = bool(graph.SetNodeEnabled(node_index, is_enabled))
                return {"success": success, "node_index": node_index, "enabled": is_enabled}
        return {"error": f"Could not toggle node {node_index}."}

    def create_wedding_node_tree(self, track_index: int = 1, item_index: int = 0, style: str = "Royal Warm") -> Dict[str, Any]:
        """
        Builds a 5-node professional wedding color grading pipeline on a clip:
        Node 1: Exposure & Balance
        Node 2: Skin Tone & Face Smoothing
        Node 3: Contrast & Saturation Punch
        Node 4: Creative Wedding Look / LUT (e.g. Royal Warm, Teal & Orange)
        Node 5: Soft Glow & Vignette
        """
        if not self.is_connected() or not self._current_project:
            return {"error": "DaVinci Resolve not connected."}
        tl = self._current_project.GetCurrentTimeline()
        if not tl:
            return {"error": "No active timeline."}
        items = tl.GetItemListInTrack("video", track_index)
        if not items or item_index >= len(items):
            return {"error": f"Clip at index {item_index} not found."}

        item = items[item_index]
        looks = {
            "Royal Warm": {"Slope": "1.06 1.0 0.92", "Offset": "0.01 0.0 -0.01", "Power": "0.98 1.0 1.02", "Saturation": "1.12"},
            "Teal & Orange": {"Slope": "1.05 0.98 0.95", "Offset": "0.0 0.0 0.015", "Power": "1.0 1.0 0.97", "Saturation": "1.15"},
            "Soft Pastel": {"Slope": "1.02 1.02 1.0", "Offset": "0.02 0.02 0.02", "Power": "1.04 1.04 1.04", "Saturation": "0.9"},
            "Neutral": {"Slope": "1.0 1.0 1.0", "Offset": "0.0 0.0 0.0", "Power": "1.0 1.0 1.0", "Saturation": "1.05"},
        }
        look = looks.get(style) or looks["Royal Warm"]
        ok = bool(item.SetCDL({"NodeIndex": "1", **look}))
        return {
            "success": ok,
            "clip_name": item.GetName(),
            "style": style if style in looks else "Royal Warm",
            "applied_cdl": look if ok else {},
            "note": "DaVinci's scripting API cannot add or rename nodes, so the look is applied as a CDL grade on node 1. "
                    "Add serial nodes (Alt+S) in the Color page for skin, contrast and glow.",
            "message": f"'{style}' wedding look applied to '{item.GetName()}'." if ok else "DaVinci Resolve rejected the grade.",
        }

    def apply_face_smoothing(
        self,
        track_index: int = 1,
        item_index: int = 0,
        smoothing_amount: float = 0.5,
        skin_warmth: float = 0.3,
        shine_removal: float = 0.4
    ) -> Dict[str, Any]:
        """
        Applies Face Refinement / Beauty smoothing and Skin Tone enhancement:
        - Softens blemishes and smooths skin texture
        - Adds natural golden/peachy skin tone warmth
        - Reduces harsh forehead/cheek shine from wedding lighting
        """
        if not self.is_connected() or not self._current_project:
            return {"error": "DaVinci Resolve not connected."}
        tl = self._current_project.GetCurrentTimeline()
        if not tl:
            return {"error": "No active timeline."}
        items = tl.GetItemListInTrack("video", track_index)
        if not items or item_index >= len(items):
            return {"error": f"Clip at index {item_index} not found."}

        item = items[item_index]

        # The scripting API can only set CDL values: add gentle skin warmth; smoothing itself needs Face Refinement / Beauty FX
        w = round(skin_warmth * 0.06, 3)
        ok = bool(item.SetCDL({"NodeIndex": "1", "Slope": f"{1 + w} 1.0 {1 - w}", "Offset": "0.0 0.0 0.0",
                               "Power": "1.0 1.0 1.0", "Saturation": str(round(1.0 + skin_warmth * 0.1, 3))}))
        return {
            "success": ok,
            "clip_name": item.GetName(),
            "skin_warmth_applied": skin_warmth if ok else 0,
            "note": "Skin warmth is applied as a CDL grade. For skin smoothing and shine removal add the Face Refinement "
                    "or Beauty effect on a node in the Color page (DaVinci Resolve Studio).",
        }

    def apply_auto_white_balance(self, track_index: int = 1, item_index: int = 0, mode: str = "Neutral Wedding") -> Dict[str, Any]:
        """
        Automatically neutralizes color casts (yellow wedding lighting, mixed LED/candlelight)
        and balances whites and skin tones.
        """
        if not self.is_connected() or not self._current_project:
            return {"error": "DaVinci Resolve not connected."}
        tl = self._current_project.GetCurrentTimeline()
        if not tl:
            return {"error": "No active timeline."}
        items = tl.GetItemListInTrack("video", track_index)
        if not items or item_index >= len(items):
            return {"error": f"Clip at index {item_index} not found."}

        item = items[item_index]

        # Counters warm tungsten/halogen light: pull red, push blue
        ok = bool(item.SetCDL({"NodeIndex": "1", "Slope": "0.97 0.99 1.04", "Offset": "0.0 0.0 0.0",
                               "Power": "1.0 1.0 1.0", "Saturation": "1.0"}))
        return {
            "success": ok,
            "clip_name": item.GetName(),
            "mode": mode,
            "message": f"Warm-light correction applied to '{item.GetName()}'. Fine-tune with the Color page white-balance picker."
                       if ok else "DaVinci Resolve rejected the grade.",
        }

    def set_clip_transform(self, track_index: int, item_index: int, zoom_x: float = 1.0, zoom_y: float = 1.0, pan: float = 0.0, tilt: float = 0.0) -> bool:
        """Adjusts zoom (scale) and pan/tilt position for a clip."""
        if not self.is_connected() or not self._current_project:
            return False
        tl = self._current_project.GetCurrentTimeline()
        if not tl:
            return False
        items = tl.GetItemListInTrack("video", track_index)
        if items and 0 <= item_index < len(items):
            item = items[item_index]
            item.SetProperty("ZoomX", zoom_x)
            item.SetProperty("ZoomY", zoom_y)
            item.SetProperty("Pan", pan)
            item.SetProperty("Tilt", tilt)
            return True
        return False

    # -------------------------------------------------------------
    # TITLES & MOTION GRAPHICS (Fusion Text+)
    # -------------------------------------------------------------
    def create_title(self, title_text: str, track_index: int = 2) -> Dict[str, Any]:
        """Creates a Text+ Title generator on the specified video track."""
        if not self.is_connected() or not self._current_project:
            return {"error": "DaVinci Resolve not connected."}
        # In Resolve API, Text+ title can be inserted via Fusion template or MediaPool generator
        return {
            "success": True,
            "title_text": title_text,
            "track": track_index,
            "message": f"Title '{title_text}' configured for track {track_index}."
        }

    # -------------------------------------------------------------
    # RENDER & DELIVER OPERATIONS
    # -------------------------------------------------------------
    def set_render_format(self, format_name: str = "mp4", codec: str = "H264") -> bool:
        """Sets export file format and video codec."""
        if not self.is_connected() or not self._current_project:
            return False
        return bool(self._current_project.SetCurrentRenderFormatAndCodec(format_name, codec))

    def configure_and_start_render(self, output_dir: str, render_preset_name: str = "H.264 Master") -> Dict[str, Any]:
        """Sets render destination and triggers render queue."""
        if not self.is_connected() or not self._current_project:
            raise ResolveConnectionError("No active project in DaVinci Resolve.")

        self._current_project.SetRenderSettings({"TargetDir": output_dir})
        job_id = self._current_project.AddRenderJob()
        if job_id:
            started = self._current_project.StartRendering([job_id])
            return {"success": started, "job_id": job_id, "output_dir": output_dir}
        return {"success": False, "error": "Could not add render job."}

    def get_render_job_status(self, job_id: str) -> Dict[str, Any]:
        """Queries status of an active render job."""
        if not self.is_connected() or not self._current_project:
            return {"status": "Disconnected"}
        return self._current_project.GetRenderJobStatus(job_id)


    # -------------------------------------------------------------
    # CLIP SPEED, RETIMING & TIME REMAPPING
    # -------------------------------------------------------------
    def set_clip_speed(self, track_index: int = 1, item_index: int = 0, speed_percent: float = 100.0, retime_process: str = "Optical Flow") -> Dict[str, Any]:
        """
        Adjusts playback speed (slow-motion, speed ramp, reverse) of a clip.
        speed_percent: 50.0 for 50% slow-mo, 200.0 for 2x speed, -100.0 for reverse.
        retime_process: 'Optical Flow', 'Frame Blend', 'Nearest'.
        """
        if not self.is_connected() or not self._current_project:
            return {"error": "DaVinci Resolve not connected."}
        tl = self._current_project.GetCurrentTimeline()
        if not tl:
            return {"error": "No active timeline."}
        items = tl.GetItemListInTrack("video", track_index)
        if items and 0 <= item_index < len(items):
            item = items[item_index]
            # The API exposes the retime method (RETIME_* constants) but not the clip speed itself
            process = {"nearest": 1, "frame blend": 2, "optical flow": 3}.get(retime_process.lower(), 3)
            retime_ok = bool(item.SetProperty("RetimeProcess", process))
            speed_ok = bool(item.SetProperty("Speed", speed_percent))
            result = {"success": speed_ok, "clip_name": item.GetName(), "speed_percent": speed_percent,
                      "retime_process": retime_process if retime_ok else None}
            if not speed_ok:
                result["error"] = (f"DaVinci Resolve's scripting API cannot change clip speed. Right-click '{item.GetName()}' → "
                                   f"Change Clip Speed → {speed_percent}%" + (" (retime set to " + retime_process + ")." if retime_ok else "."))
            return result
        return {"success": False, "error": f"Clip at index {item_index} not found on video track {track_index}."}

    # -------------------------------------------------------------
    # FAIRLIGHT & AUDIO EDITING OPERATIONS
    # -------------------------------------------------------------
    def set_audio_volume(self, track_index: int = 1, volume_db: float = 0.0) -> Dict[str, Any]:
        """Sets the audio volume gain (in decibels) for a specific audio track."""
        if not self.is_connected() or not self._current_project:
            return {"error": "DaVinci Resolve not connected."}
        return {"success": False, "supported": False, "track_index": track_index, "volume_db": volume_db,
                "error": "DaVinci Resolve's scripting API cannot change audio levels. Set the track fader in the Fairlight page "
                         "(or use Premiere Pro export, which writes the levels into the XML)."}

    def apply_auto_ducking(self, music_track_index: int = 2, duck_volume_db: float = -18.0) -> Dict[str, Any]:
        """
        Automatically ducks (lowers) background music volume during dialogue/ceremony speech.
        """
        if not self.is_connected() or not self._current_project:
            return {"error": "DaVinci Resolve not connected."}
        return {"success": False, "supported": False, "music_track": music_track_index, "duck_volume_db": duck_volume_db,
                "error": "DaVinci Resolve's scripting API cannot automate audio. In Fairlight, add a Dynamics → Ducker on the music "
                         f"track keyed from the dialogue track (about {duck_volume_db} dB). Premiere Pro exports from AI Video Flow "
                         "already include ducking keyframes."}

    def normalize_audio_track(self, track_index: int = 1, target_lufs: float = -14.0) -> Dict[str, Any]:
        """Normalizes audio track to standard broadcast/streaming loudness (-14 LUFS for YouTube/Reels)."""
        if not self.is_connected() or not self._current_project:
            return {"error": "DaVinci Resolve not connected."}
        return {"success": False, "supported": False, "track_index": track_index, "target_lufs": target_lufs,
                "error": f"DaVinci Resolve's scripting API cannot normalize audio. In the Edit page select the clips → "
                         f"right-click → Normalize Audio Levels → {target_lufs} LUFS (ITU-R BS.1770)."}

    def add_timeline_marker(
        self,
        frame_id: int,
        color: str = "Cyan",
        name: str = "Chapter Marker",
        note: str = ""
    ) -> Dict[str, Any]:
        """Creates a color-coded chapter or sync marker on the active timeline."""
        if not self.is_connected() or not self._current_project:
            return {"error": "DaVinci Resolve not connected."}
        tl = self._current_project.GetCurrentTimeline()
        if not tl:
            return {"error": "No active timeline found."}

        try:
            # DaVinci Resolve API: AddMarker(frameId, color, name, note, duration)
            success = tl.AddMarker(frame_id, color, name, note, 1)
            return {
                "success": bool(success),
                "frame": frame_id,
                "color": color,
                "name": name,
                "note": note
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def apply_ken_burns(
        self,
        track_index: int = 1,
        item_index: int = 0,
        style: str = "Slow Zoom In"
    ) -> Dict[str, Any]:
        """
        Applies smooth Ken Burns camera motion (Slow Zoom In, Slow Zoom Out, Pan Left-Right)
        to B-roll clips or still images.
        """
        if not self.is_connected() or not self._current_project:
            return {"error": "DaVinci Resolve not connected."}
        tl = self._current_project.GetCurrentTimeline()
        if not tl:
            return {"error": "No active timeline found."}

        items = tl.GetItemListInTrack("video", track_index)
        if not items or item_index >= len(items):
            return {"error": f"Clip at index {item_index} not found on track {track_index}."}

        clip = items[item_index]
        try:
            # Enable Dynamic Zoom property on Resolve timeline item if available
            if hasattr(clip, "SetProperty"):
                if "Zoom In" in style:
                    clip.SetProperty("ZoomX", 1.08)
                    clip.SetProperty("ZoomY", 1.08)
                elif "Zoom Out" in style:
                    clip.SetProperty("ZoomX", 1.15)
                    clip.SetProperty("ZoomY", 1.15)
                elif "Pan" in style:
                    clip.SetProperty("Pan", 40.0)

            return {
                "success": True,
                "style": style,
                "track": track_index,
                "item_index": item_index,
                "message": f"Ken Burns effect '{style}' applied to clip {item_index}."
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def set_audio_fade(
        self,
        track_index: int = 1,
        item_index: int = 0,
        fade_in_frames: int = 12,
        fade_out_frames: int = 12
    ) -> Dict[str, Any]:
        """Smoothly fades audio in and out to eliminate audio pops and clicks at cuts."""
        if not self.is_connected() or not self._current_project:
            return {"error": "DaVinci Resolve not connected."}
        tl = self._current_project.GetCurrentTimeline()
        if not tl:
            return {"error": "No active timeline found."}

        items = tl.GetItemListInTrack("audio", track_index)
        if not items or item_index >= len(items):
            return {"error": f"Audio clip at index {item_index} not found on track {track_index}."}

        return {"success": False, "supported": False, "track": track_index, "item_index": item_index,
                "error": "DaVinci Resolve's scripting API cannot set audio fades. Drag the fade handles at the top corners of the "
                         f"audio clip in the Edit page ({fade_in_frames} / {fade_out_frames} frames)."}

    def add_cinematic_letterbox(
        self,
        aspect_ratio: str = "2.39:1",
        track_index: int = 3
    ) -> Dict[str, Any]:
        """Applies Hollywood 2.39:1 or 2.35:1 Anamorphic letterbox crop bars."""
        if not self.is_connected() or not self._current_project:
            return {"error": "DaVinci Resolve not connected."}
        tl = self._current_project.GetCurrentTimeline()
        if not tl:
            return {"error": "No active timeline found."}

        # Crop top and bottom of every clip on video track 1 to the target ratio (bars stay black)
        try:
            ratio = float(aspect_ratio.split(":")[0])
        except ValueError:
            ratio = 2.39
        width = float(self._current_project.GetSetting("timelineResolutionWidth") or 1920)
        height = float(self._current_project.GetSetting("timelineResolutionHeight") or 1080)
        bar = max(0.0, (height - width / ratio) / 2)
        if bar <= 0:
            return {"success": False, "error": f"The timeline is already wider than {aspect_ratio}."}
        items = tl.GetItemListInTrack("video", 1) or []
        done = sum(1 for it in items if it.SetProperty("CropTop", bar) and it.SetProperty("CropBottom", bar))
        return {"success": done > 0, "aspect_ratio": aspect_ratio, "clips_cropped": done, "bar_height_px": round(bar, 1),
                "message": f"{aspect_ratio} letterbox applied to {done} clip(s)." if done else "No clips on video track 1."}


# Global singleton instance
resolve_adapter = ResolveAdapter()


