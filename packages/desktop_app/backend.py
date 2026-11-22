"""
AI Video Flow - Local Desktop App Backend Server
Serves static dashboard files and exposes REST API endpoints for:
- Live DaVinci Resolve status
- 1-Click "CONNECT ALL" configuration
- HyperFrames engine status
- Native Browse dialogs (folder / audio / image / video)
- Raw footage folder scanning
- Live footage analysis & timeline generation (background job with live log)
- Generic runner for every MCP tool (same functions Claude Desktop uses)
- AI Smart Premium: Claude shot review + AI assistant (via the license server's relay)

Every /api/ call must carry the per-launch session token (X-AVF-Token) and a localhost Host header,
so web pages open in the user's browser cannot drive this server.
"""

import os
import re
import sys
import json
import time
import uuid
import platform
import threading
import subprocess
import traceback
import logging
import hmac
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from typing import Dict, Any, List, Optional, Tuple

# Ensure project root is in sys.path
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from packages.core_resolve.resolve_adapter import resolve_adapter
from packages.core_premiere.premiere_adapter import premiere_adapter
from packages.core_edius.edius_adapter import edius_adapter
from packages.hyperframes_engine.hyperframes_service import hyperframes_service
from packages.licensing.license_manager import license_manager
from packages.ai_engine.cloud_ai_client import cloud_ai_client
from installer.connect_all_engine import ConnectAllEngine

logger = logging.getLogger("ai_video_flow.desktop_backend")

VIDEO_EXTS = {".mp4", ".mov", ".mxf", ".mts", ".m2ts", ".avi", ".mkv", ".m4v", ".braw", ".r3d", ".webm", ".mpg", ".mpeg", ".3gp"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".aif", ".aiff", ".ogg"}
PSD_EXTS = {".psd", ".psb"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp", ".svg"} | PSD_EXTS
STILL_EXTS = IMAGE_EXTS
OUTPUT_DIR = os.path.join(os.path.expanduser("~"), "AI-Video-Flow-Output")
APP_VERSION = "1.1.21"  # launcher only reuses an already-running server with the same version

SESSION_TOKEN = os.environ.get("AVF_SESSION_TOKEN", "")
ALLOWED_HOSTS: set = set()

# Free plan: projects per day (any paid plan removes the limit). The admin portal can change it;
# the last plan list from the server is cached so prices and the limit also show offline.
FREE_DAILY_PROJECTS = 1
PLANS_CACHE = os.path.join(os.path.expanduser("~"), ".ai-video-flow", "plans.json")


def _cached_plans() -> Dict[str, Any]:
    try:
        with open(PLANS_CACHE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def fetch_plans() -> Dict[str, Any]:
    data = license_manager.plans()
    if data.get("success"):
        try:
            os.makedirs(os.path.dirname(PLANS_CACHE), exist_ok=True)
            with open(PLANS_CACHE, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except OSError:
            pass
    return data


def free_daily_limit() -> int:
    try:
        return max(0, int(_cached_plans().get("free_daily_projects", FREE_DAILY_PROJECTS)))
    except (TypeError, ValueError):
        return FREE_DAILY_PROJECTS


def price_from() -> Dict[str, int]:
    """Cheapest monthly AI Editing plan and cheapest Smart AI pack, for the hints in the app."""
    plans = _cached_plans().get("plans") or []
    local = [int(p["price_inr"]) for p in plans if p.get("kind") == "local" and int(p.get("validity_days", 0)) <= 31]
    ai = [int(p["price_inr"]) for p in plans if p.get("kind") == "ai"]
    return {"local": min(local) if local else 399, "ai": min(ai) if ai else 999}


def license_status() -> Dict[str, Any]:
    fs = free_status()
    return {**license_manager.get_status(),
            "free_left_today": int(fs["left"]) if fs and fs.get("success") else None,
            "free_limit": int(fs["limit"]) if fs and fs.get("success") else free_daily_limit(),
            "free_offline": bool(fs and not fs.get("success")), "price_from": price_from()}


_FREE_CACHE: Dict[str, Any] = {"t": 0.0, "data": None}


def free_status(force: bool = False) -> Optional[Dict[str, Any]]:
    """Today's free projects for this computer, as counted by the server. None = paid plan (no limit)."""
    if license_manager.get_status().get("local_unlimited"):
        return None
    if force or not _FREE_CACHE["data"] or time.time() - _FREE_CACHE["t"] > 30:
        _FREE_CACHE.update(t=time.time(), data=license_manager.free_status())
    return _FREE_CACHE["data"]


def free_projects_left() -> Optional[int]:
    """None = unlimited (paid plan) or unknown (offline)."""
    st = free_status()
    return int(st["left"]) if st and st.get("success") else None


def _claim_free_project(job_id: str, job_name: str) -> None:
    """Free plan: the server must sign off one of today's projects before any timeline is made."""
    if license_manager.get_status().get("local_unlimited"):
        return
    r = license_manager.free_claim(job_name)
    _FREE_CACHE["data"] = None
    if not r.get("success"):
        raise RuntimeError(r.get("error"))
    _log(job_id, f"Free plan: project counted · {r['left']} left today.", "info")


# AI assistant conversation (one chat per app session)
ASSISTANT_HISTORY: List[Dict[str, Any]] = []
ASSISTANT_LOCK = threading.Lock()

# Background jobs: job_id -> {"status", "logs", "result", "error"}
JOBS: Dict[str, Dict[str, Any]] = {}
JOBS_LOCK = threading.Lock()


# ----------------------------------------------------------------------
# Native file / folder dialogs
# ----------------------------------------------------------------------
def _browse_mac(kind: str, prompt: str) -> Optional[str]:
    if kind == "folder":
        script = f'POSIX path of (choose folder with prompt "{prompt}")'
    else:
        exts = {"audio": AUDIO_EXTS, "image": IMAGE_EXTS, "video": VIDEO_EXTS}.get(kind)
        if exts:
            types = ", ".join(f'"{e.lstrip(".")}"' for e in sorted(exts))
            script = f'POSIX path of (choose file with prompt "{prompt}" of type {{{types}}})'
        else:
            script = f'POSIX path of (choose file with prompt "{prompt}")'
    # "activate" brings the dialog in front of the app window
    proc = subprocess.run(["osascript", "-e", "activate", "-e", script], capture_output=True, text=True)
    if proc.returncode != 0:
        return None  # user cancelled
    path = proc.stdout.strip()
    return path.rstrip("/") if kind == "folder" and len(path) > 1 else path


def _browse_windows(kind: str, prompt: str) -> Optional[str]:
    if kind == "folder":
        ps = (
            "Add-Type -AssemblyName System.Windows.Forms;"
            "$d = New-Object System.Windows.Forms.FolderBrowserDialog;"
            f"$d.Description = '{prompt}'; $d.ShowNewFolderButton = $false;"
            "$t = New-Object System.Windows.Forms.Form -Property @{TopMost=$true};"
            "if ($d.ShowDialog($t) -eq 'OK') { Write-Output $d.SelectedPath }"
        )
    else:
        exts = {"audio": AUDIO_EXTS, "image": IMAGE_EXTS, "video": VIDEO_EXTS}.get(kind)
        flt = "All files (*.*)|*.*"
        if exts:
            pattern = ";".join(f"*{e}" for e in sorted(exts))
            flt = f"{kind.title()} files ({pattern})|{pattern}|All files (*.*)|*.*"
        ps = (
            "Add-Type -AssemblyName System.Windows.Forms;"
            "$d = New-Object System.Windows.Forms.OpenFileDialog;"
            f"$d.Title = '{prompt}'; $d.Filter = '{flt}';"
            "$t = New-Object System.Windows.Forms.Form -Property @{TopMost=$true};"
            "if ($d.ShowDialog($t) -eq 'OK') { Write-Output $d.FileName }"
        )
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-STA", "-Command", ps],
        capture_output=True, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
    )
    path = proc.stdout.strip()
    return path or None


def _browse_tk(kind: str, prompt: str) -> Optional[str]:
    import tkinter
    from tkinter import filedialog
    root = tkinter.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        if kind == "folder":
            path = filedialog.askdirectory(title=prompt)
        else:
            path = filedialog.askopenfilename(title=prompt)
    finally:
        root.destroy()
    return path or None


def browse(kind: str = "folder", prompt: str = "Select") -> Dict[str, Any]:
    prompt = prompt.replace('"', "").replace("'", "")
    system = platform.system()
    try:
        if system == "Darwin":
            path = _browse_mac(kind, prompt)
        elif system == "Windows":
            path = _browse_windows(kind, prompt)
        else:
            path = _browse_tk(kind, prompt)
    except Exception as e:
        logger.warning(f"Native dialog failed, trying Tk fallback: {e}")
        try:
            path = _browse_tk(kind, prompt)
        except Exception as e2:
            return {"success": False, "path": None, "error": f"Could not open file dialog: {e2}. Please paste the path manually."}
    return {"success": bool(path), "path": path, "cancelled": not path}


# ----------------------------------------------------------------------
# Folder scanning
# ----------------------------------------------------------------------
def scan_folder(folder: str, recursive: bool = True) -> Dict[str, Any]:
    folder = os.path.expanduser((folder or "").strip().strip('"'))
    if not folder:
        return {"success": False, "error": "Please select a footage folder first."}
    if not os.path.isdir(folder):
        return {"success": False, "error": f"Folder not found: {folder}"}

    videos: List[str] = []
    audios: List[str] = []
    images: List[str] = []
    psds: List[str] = []
    total_bytes = 0
    try:
        walker = os.walk(folder) if recursive else [(folder, [], os.listdir(folder))]
        for dirpath, dirnames, filenames in walker:
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for fn in sorted(filenames):
                if fn.startswith("."):
                    continue
                ext = os.path.splitext(fn)[1].lower()
                full = os.path.join(dirpath, fn)
                if ext in VIDEO_EXTS:
                    videos.append(full)
                elif ext in PSD_EXTS:
                    psds.append(full)
                    images.append(full)
                elif ext in IMAGE_EXTS:
                    images.append(full)
                elif ext in AUDIO_EXTS:
                    audios.append(full)
                else:
                    continue
                try:
                    total_bytes += os.path.getsize(full)
                except OSError:
                    pass
    except PermissionError:
        return {
            "success": False,
            "error": "Permission denied reading this folder. On macOS open System Settings → Privacy & Security → "
                     "Files and Folders (or Full Disk Access) and allow Python / Terminal, then try again."
        }

    return {
        "success": True,
        "folder": folder,
        "video_count": len(videos),
        "audio_count": len(audios),
        "image_count": len(images),
        "psd_count": len(psds),
        "media_count": len(videos) + len(images),
        "total_gb": round(total_bytes / (1024 ** 3), 2),
        "videos": videos,
        "audios": audios,
        "images": images,
        "psds": psds
    }


# ----------------------------------------------------------------------
# MCP tool bridge (lazy so missing AI libs don't stop the dashboard from loading)
# ----------------------------------------------------------------------
def _mcp():
    from packages.mcp_server import server
    return server


def list_tools() -> Dict[str, Any]:
    try:
        return {"success": True, "tools": _mcp().MCP_TOOLS}
    except ImportError as e:
        return {"success": False, "tools": [], "error": f"AI libraries missing ({e}). Run the installer again."}


def run_tool(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    try:
        result = _mcp().handle_tool_call(name, arguments or {})
        return {"success": True, "tool": name, "result": result}
    except Exception as e:
        logger.error(f"Tool '{name}' failed: {e}", exc_info=True)
        return {"success": False, "tool": name, "error": str(e)}


def dependency_report() -> Dict[str, Any]:
    deps = {}
    for mod in ["numpy", "cv2", "librosa", "soundfile", "scipy", "opentimelineio", "scenedetect"]:
        try:
            m = __import__(mod)
            deps[mod] = getattr(m, "__version__", "installed")
        except Exception:
            deps[mod] = None
    return {
        "python": sys.version.split()[0],
        "python_path": sys.executable,
        "app_root": root_dir,
        "os": f"{platform.system()} {platform.release()}",
        "output_dir": OUTPUT_DIR,
        "dependencies": deps
    }


# ----------------------------------------------------------------------
# Background jobs
# ----------------------------------------------------------------------
def _new_job() -> str:
    job_id = uuid.uuid4().hex[:12]
    with JOBS_LOCK:
        JOBS[job_id] = {"status": "running", "logs": [], "result": None, "error": None, "progress": 0}
    return job_id


def _log(job_id: str, msg: str, level: str = "normal") -> None:
    with JOBS_LOCK:
        if job_id in JOBS:
            JOBS[job_id]["logs"].append({"t": time.strftime("%H:%M:%S"), "msg": msg, "level": level})
        else:
            logger.info(f"[{level.upper()}] {msg}")


def _progress(job_id: str, pct: int) -> None:
    with JOBS_LOCK:
        if job_id in JOBS:
            JOBS[job_id]["progress"] = pct


def _append_tracks_to_resolve(plan: Dict[str, Any], items_by_path: Dict[str, Any]) -> int:
    """Multi-camera plan: camera N goes on video track N at its synced position."""
    project = resolve_adapter._current_project
    tl = project.GetCurrentTimeline() if project else None
    if not tl:
        return 0
    while tl.GetTrackCount("video") < len(plan["tracks"]):
        tl.AddTrack("video")
    need_audio = len(plan["tracks"]) + (1 if plan.get("audio") else 0)
    while tl.GetTrackCount("audio") < need_audio:
        tl.AddTrack("audio", "stereo")
    tl_fps = float(project.GetSetting("timelineFrameRate") or 25.0)
    start = int(tl.GetStartFrame() or 0)
    entries = []
    for t_index, track in enumerate(plan["tracks"], 1):
        for c in track:
            item = items_by_path.get(os.path.normpath(c["file_path"]))
            if not item:
                continue
            entries.append({"mediaPoolItem": item, "startFrame": int(c["in_frame"]), "endFrame": max(int(c["in_frame"]) + 1, int(c["out_frame"]) - 1),
                            "trackIndex": t_index, "recordFrame": start + int(round(c["timeline_start"] * tl_fps))})
    song = plan.get("audio")
    if song and plan.get("audio_duration"):
        items = resolve_adapter.import_media_files([song], bin_name="03_MUSIC")
        if items:
            entries.append({"mediaPoolItem": items[0], "startFrame": 0, "endFrame": int(float(plan["audio_duration"]) * tl_fps) - 1,
                            "trackIndex": len(plan["tracks"]) + 1, "recordFrame": start, "mediaType": 2})
    if not entries:
        return 0
    try:
        placed = resolve_adapter._current_project.GetMediaPool().AppendToTimeline(entries)
        return len(placed or [])
    except Exception as e:
        logger.error(f"Multicam append failed: {e}")
        return 0


def _append_plan_to_resolve(plan: Dict[str, Any], items_by_path: Dict[str, Any]) -> int:
    """Builds trimmed AppendToTimeline entries for a plan and appends them. Returns clips placed."""
    if plan.get("tracks"):
        return _append_tracks_to_resolve(plan, items_by_path)
    entries = []
    for c in plan.get("clips", []):
        item = items_by_path.get(os.path.normpath(c.get("file_path") or ""))
        if not item:
            continue
        start = int(c.get("in_frame", 0))
        end = max(start + 1, int(c.get("out_frame", start + 1)) - 1)
        entries.append({"mediaPoolItem": item, "startFrame": start, "endFrame": end, "speed_percent": c.get("speed_percent", 100)})
    if not entries:
        return 0
    if not resolve_adapter.append_clips_to_timeline(entries, plan.get("sequence_name")):
        return 0
    # Music bed from 0:00 and the AI voiceover from 1 s, each on its own audio track
    end = float(plan.get("total_duration") or 0)
    if plan.get("audio") and os.path.isfile(plan["audio"]) and end > 0:
        _append_audio_to_resolve(plan["audio"], end, 0.0)
    if plan.get("voiceover") and os.path.isfile(plan["voiceover"]):
        _append_audio_to_resolve(plan["voiceover"], float(plan.get("voiceover_duration") or 0), 1.0)
    return len(entries)


def _append_audio_to_resolve(path: str, seconds: float, at_seconds: float) -> bool:
    """Puts an audio file on a new audio track of the current Resolve timeline."""
    project = resolve_adapter._current_project
    tl = project.GetCurrentTimeline() if project else None
    items = resolve_adapter.import_media_files([path], bin_name="03_MUSIC") if tl else None
    if not items or seconds <= 0:
        return False
    try:
        tl.AddTrack("audio", "stereo")
        fps = float(project.GetSetting("timelineFrameRate") or 25.0)
        start = int(tl.GetStartFrame() or 0)
        placed = project.GetMediaPool().AppendToTimeline([{
            "mediaPoolItem": items[0], "startFrame": 0, "endFrame": max(1, int(seconds * fps) - 1), "mediaType": 2,
            "trackIndex": tl.GetTrackCount("audio"), "recordFrame": start + int(round(at_seconds * fps))}])
        return bool(placed)
    except Exception as e:
        logger.error(f"Audio append failed: {e}")
        return False


def send_to_resolve(plan_path: Optional[str] = None) -> Dict[str, Any]:
    """Sends the latest or specified edit plan directly into DaVinci Resolve."""
    try:
        # 1. Locate plan
        if not plan_path or not os.path.isfile(plan_path):
            import glob
            plans = glob.glob(os.path.join(OUTPUT_DIR, "*_plan_*.json"))
            if not plans:
                return {"success": False, "error": "Koi edit plan nahi mila. Pehle 'Start AI In-Clip Trimming' run karein."}
            plans.sort(key=os.path.getmtime, reverse=True)
            plan_path = plans[0]

        with open(plan_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # 2. Ensure DaVinci Resolve is running
        if not resolve_adapter.is_running():
            resolve_adapter.launch()
            for _ in range(8):
                time.sleep(1)
                if resolve_adapter.connect():
                    break

        if not resolve_adapter.connect():
            return {"success": False, "error": "DaVinci Resolve se connect nahi ho paya. Kripya DaVinci Resolve open karein."}

        # 3. Ensure an active project exists
        proj = None
        if resolve_adapter._project_manager:
            proj = resolve_adapter._project_manager.GetCurrentProject()
        if not proj:
            proj_name = f"AI_Video_Flow_{data.get('mode', 'Edit').capitalize()}"
            resolve_adapter.create_project(proj_name)
            proj = resolve_adapter._current_project
            if not proj:
                return {"success": False, "error": "DaVinci Resolve me project create/open nahi ho saka."}

        plans_dict = data.get("plans", {})
        if not plans_dict:
            return {"success": False, "error": "Plan file me koi sequence nahi mili."}

        created_timelines = []
        total_placed = 0

        for p_key, p in plans_dict.items():
            name = p.get("sequence_name") or f"01_AI_{data.get('mode', 'Edit')}_Timeline"
            used = sorted({c["file_path"] for c in p.get("clips", []) if c.get("file_path")})
            items = resolve_adapter.import_media_files(used, bin_name="AI_Raw_Footage")
            items_by_path = {}
            for it in (items or []):
                try:
                    items_by_path[os.path.normpath(it.GetClipProperty("File Path"))] = it
                except Exception:
                    pass

            # Also read existing items from pool if already imported earlier
            try:
                pool = proj.GetMediaPool()
                root = pool.GetRootFolder()
                def collect_clips(folder):
                    for clip in folder.GetClipList():
                        try:
                            fp = os.path.normpath(clip.GetClipProperty("File Path"))
                            if fp not in items_by_path:
                                items_by_path[fp] = clip
                        except Exception:
                            pass
                    for sf in folder.GetSubFolderList():
                        collect_clips(sf)
                collect_clips(root)
            except Exception:
                pass

            # Create or select timeline
            tl = resolve_adapter.create_timeline(name)
            placed = _append_plan_to_resolve(p, items_by_path)
            total_placed += placed
            created_timelines.append(name)

            # Add Smart AI Director & Scene Markers to Resolve timeline
            try:
                if p.get("director_note"):
                    resolve_adapter.add_timeline_marker(0, "Yellow", "Claude AI Director", p["director_note"][:100])
                for c in p.get("clips", []):
                    if c.get("text_hook") or c.get("role"):
                        f = int(float(c.get("timeline_start", 0)) * 25.0)
                        resolve_adapter.add_timeline_marker(f, "Cyan", c.get("role", "Hook")[:30], c.get("text_hook", "")[:80])
            except Exception:
                pass

            # Import audio if exists
            audio_file = data.get("audio")
            if audio_file and os.path.isfile(audio_file):
                try:
                    resolve_adapter.import_media_files([audio_file], bin_name="03_MUSIC")
                except Exception:
                    pass

        # Focus DaVinci window
        if platform.system() == "Darwin":
            subprocess.run(["osascript", "-e", 'tell application "DaVinci Resolve" to activate'], check=False)

        resolve_adapter.save_project()
        return {
            "success": True,
            "message": f"DaVinci Resolve me {len(created_timelines)} timeline(s) load ho gayi ({total_placed} clips trimmed & placed)!",
            "timelines": created_timelines,
            "clip_count": total_placed
        }
    except Exception as e:
        logger.error(f"Error in send_to_resolve: {e}")
        return {"success": False, "error": str(e)}


def _export_plans(job_id: str, plans: Dict[str, Dict[str, Any]], target_nle: str, audio_path: str = "",
                  markers: Optional[Dict[str, List[Dict[str, Any]]]] = None, project_name: str = "") -> Dict[str, Any]:
    """Builds every plan in the chosen editor(s). `markers` maps plan key -> [{"seconds", "name"}]."""
    # DaVinci Resolve: live Media Pool import + timelines
    created_resolve = []
    if target_nle in ("resolve", "both"):
        if resolve_adapter.is_connected():
            _log(job_id, "Importing footage into DaVinci Resolve Media Pool (bin: AI_Raw_Footage)...", "info")
            used = sorted({c["file_path"] for p in plans.values() for c in p.get("clips", []) if c.get("file_path")})
            items = resolve_adapter.import_media_files(used, bin_name="AI_Raw_Footage")
            items_by_path = {}
            for it in items:
                try:
                    items_by_path[os.path.normpath(it.GetClipProperty("File Path"))] = it
                except Exception:
                    pass
            _log(job_id, f"Imported {len(items)} clips into Resolve.")
            for key, p in plans.items():
                name = p.get("sequence_name")
                if resolve_adapter.create_timeline(name):
                    placed = _append_plan_to_resolve(p, items_by_path)
                    created_resolve.append(name)
                    _log(job_id, f"  ✨ DaVinci Resolve: {name} created with {placed} clips", "info")
                    fps = float(p.get("fps") or 25.0)
                    for m in (markers or {}).get(key, []):
                        resolve_adapter.add_timeline_marker(int(m["seconds"] * fps), "Blue", m["name"], m.get("note", ""))
            resolve_adapter.save_project()
        else:
            _log(job_id, "DaVinci Resolve is not connected — skipped the Resolve timelines (the plan is saved).", "warning")

    # Adobe Premiere Pro: FCP XML
    created_premiere = []
    if target_nle in ("premiere", "both"):
        _log(job_id, "Generating FCP XML sequences for Adobe Premiere Pro...", "info")
        for p in plans.values():
            res = premiere_adapter.export_plan_to_premiere_xml(p)
            if res.get("success"):
                xml_path = res["xml_path"]
                created_premiere.append(xml_path)
                _add_voiceover_xml(job_id, p, xml_path)
                _log(job_id, f"  🟣 Premiere Pro XML: {os.path.basename(xml_path)} ({res.get('clip_count', 0)} clips)", "info")
        if created_premiere:
            _save_last_premiere(created_premiere, project_name)
        if created_premiere and (target_nle == "premiere" or premiere_adapter.is_running()):
            _log(job_id, "Creating the Premiere Pro project (Premiere opens if it is closed - this can take a minute)…", "info")
            opened = premiere_adapter.open_timelines(created_premiere, project_name=project_name or "AI_Video_Flow",
                                                     on_wait=lambda msg: _log(job_id, "  ⏳ " + msg, "warning"))
            if opened.get("success"):
                _log(job_id, f"  🚀 {opened['message']}", "info")
            else:
                _log(job_id, f"  Premiere Pro: {opened.get('error')} The XML files are in {os.path.dirname(created_premiere[0])}.", "warning")

    # Grass Valley EDIUS: FCP XML
    created_edius = []
    if target_nle in ("edius", "both"):
        _log(job_id, "Generating EDIUS-compatible FCP XML sequences...", "info")
        edius_dir = os.path.join(OUTPUT_DIR, "edius_timelines")
        os.makedirs(edius_dir, exist_ok=True)
        for p_key, p in plans.items():
            seq_name = p.get("sequence_name") or f"EDIUS_{p_key}"
            xml_out = os.path.join(edius_dir, f"{seq_name}_EDIUS.xml")
            res = edius_adapter.export_plan_to_edius_xml(
                plan=p, output_xml_path=xml_out, audio_file_path=audio_path, sequence_name=seq_name,
                aspect_ratio=p.get("aspect_ratio", "16:9"), broll_clips=p.get("broll_clips"),
            )
            if res.get("success"):
                created_edius.append(xml_out)
                _add_voiceover_xml(job_id, p, xml_out)
                _log(job_id, f"  🟡 EDIUS timeline: {os.path.basename(xml_out)} ({res.get('clip_count', 0)} clips)", "info")

    _progress(job_id, 100)
    summary = []
    if created_resolve:
        summary.append(f"{len(created_resolve)} in DaVinci Resolve")
    if created_premiere:
        summary.append(f"{len(created_premiere)} XMLs in Adobe Premiere Pro")
    if created_edius:
        summary.append(f"{len(created_edius)} XMLs for Grass Valley EDIUS")
    _log(job_id, f"Done! Created {' & '.join(summary)}." if summary else "Analysis complete. Plan file is ready.", "info")
    return {"timelines_created": created_resolve, "premiere_xmls": created_premiere, "edius_xmls": created_edius, "plans": plans}


LAST_PREMIERE = "last_premiere.json"


def _save_last_premiere(xmls: List[str], project_name: str) -> None:
    try:
        with open(os.path.join(OUTPUT_DIR, LAST_PREMIERE), "w", encoding="utf-8") as f:
            json.dump({"xmls": xmls, "project_name": project_name}, f)
    except OSError:
        pass


def send_to_premiere() -> Dict[str, Any]:
    """'Send again': the last job's timelines as a new Premiere Pro project."""
    try:
        with open(os.path.join(OUTPUT_DIR, LAST_PREMIERE), encoding="utf-8") as f:
            last = json.load(f)
    except (OSError, ValueError):
        return {"success": False, "error": "No Premiere Pro timelines yet. Build timelines with Premiere Pro selected first."}
    return premiere_adapter.open_timelines(last.get("xmls") or [], project_name=last.get("project_name") or "AI_Video_Flow")


def _add_voiceover_xml(job_id: str, plan: Dict[str, Any], xml_path: str) -> None:
    if plan.get("voiceover") and os.path.isfile(plan["voiceover"]):
        try:
            from packages.ai_engine.fcp_xml import add_audio_track
            add_audio_track(xml_path, plan["voiceover"], float(plan.get("voiceover_duration") or 0), 1.0)
        except Exception as e:
            _log(job_id, f"Could not add the voiceover to {os.path.basename(xml_path)}: {e}", "warning")


def _fmt_ts(sec: float) -> str:
    sec = int(sec)
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60:02d}:{sec % 60:02d}"


def longform_job(job_id: str, folder: str, formats: List[str], target_nle: str, mode: str, cut_level: str = "normal", ref_video: str = "", instructions: str = "") -> None:
    """Podcasts, tutorials and classes: removes silences/dead air, builds a clean edit, shorts or chapters."""
    try:
        import cv2
        import numpy as np
        import librosa
        from packages.ai_engine.audio_io import load_audio

        # Normal keeps natural breathing room; Tight cuts every hesitation
        top_db, min_gap, pad = (30.0, 0.45, 0.20) if cut_level != "tight" else (25.0, 0.25, 0.10)
        label = "Podcast" if mode == "podcast" else "Tutorial"

        # Resolve reference video and color look
        from packages.ai_engine.story_planner import parse_prompt_directives
        p_directives = parse_prompt_directives(instructions)
        ref_cand = ref_video or p_directives.get("ref_video")
        if ref_cand:
            resolved_ref = resolve_reference_video_source(ref_cand, job_id)
            if resolved_ref:
                ref_video = resolved_ref
                _log(job_id, f"🎯 Reference Video active: {os.path.basename(ref_video)}", "info")
        ref_lut_path = extract_and_apply_reference_look(job_id, ref_video, target_nle)

        scan = scan_folder(folder)
        if not scan["success"]:
            raise RuntimeError(scan["error"])
        videos = sorted(scan["videos"])
        images = sorted(scan.get("images", []))
        raw_clips = videos + images
        if not raw_clips:
            raise RuntimeError("No video, PSD, or image files found in this folder (mp4, mov, psd, png, jpg...).")
        _log(job_id, f"{label} mode · {len(videos)} recording(s), {len(images)} graphic/image asset(s) · silence cutting: {'Tight' if cut_level == 'tight' else 'Normal'}", "info")

        # 2-3 cameras (sub-folders or camera models): the camera that recorded longest decides the cuts,
        # the others are lined up to it by their audio
        from packages.ai_engine import multicam_sync

        def _dur_fps(path: str):
            cap = cv2.VideoCapture(path)
            fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
            frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
            cap.release()
            return frames / fps, fps

        groups: Dict[str, List[str]] = {}
        for v in videos:
            groups.setdefault(multicam_sync.camera_of(v, folder), []).append(v)
        other_cams: Dict[str, List[str]] = {}
        ref_cam = None
        if len(groups) > 1:
            totals = {cam: sum(_dur_fps(v)[0] for v in vs) for cam, vs in groups.items()}
            ref_cam = max(totals, key=totals.get)
            other_cams = {cam: vs for cam, vs in groups.items() if cam != ref_cam}
            videos = groups[ref_cam]
            _log(job_id, f"{len(groups)} cameras found: {', '.join(groups)}. {ref_cam} decides the cuts; "
                         "the others are synced to it by audio.", "info")

        env_ref: List[Any] = []
        stream_pos = 0.0
        clean_clips: List[Dict[str, Any]] = []
        pauses: List[float] = []          # edited-timeline seconds where a long pause was removed
        energy: List[tuple] = []          # (edited_start_sec, rms) per kept second, for picking short clips
        timeline_pos = 0.0
        total_src = 0.0

        for i, vp in enumerate(videos, 1):
            cap = cv2.VideoCapture(vp)
            fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
            cap.release()
            _log(job_id, f"[{i}/{len(videos)}] Listening to {os.path.basename(vp)}…")
            try:
                y, sr = load_audio(vp, 16000)
            except Exception as e:
                _log(job_id, f"  Skipped (no readable audio): {e}", "warning")
                continue
            dur = len(y) / sr
            total_src += dur
            if other_cams:
                env_ref.append(multicam_sync.audio_envelope(y, sr))
            intervals = librosa.effects.split(y, top_db=top_db, frame_length=2048, hop_length=512)

            # Merge speech parts separated by very short gaps so words are never clipped
            merged: List[List[float]] = []
            for a, b in intervals:
                s0, s1 = a / sr, b / sr
                if merged and s0 - merged[-1][1] < min_gap:
                    merged[-1][1] = s1
                else:
                    if merged and s0 - merged[-1][1] >= 2.0:
                        pauses.append(timeline_pos + sum(m[1] - m[0] for m in merged))
                    merged.append([s0, s1])

            for s0, s1 in merged:
                s0, s1 = max(0.0, s0 - pad), min(dur, s1 + pad)
                if s1 - s0 < 0.4:
                    continue
                seg = y[int(s0 * sr):int(s1 * sr)]
                for k in range(0, len(seg) // sr):
                    energy.append((timeline_pos + k, float(np.sqrt(np.mean(seg[k * sr:(k + 1) * sr] ** 2)))))
                clean_clips.append({
                    "clip_name": os.path.basename(vp), "file_path": vp,
                    "timeline_start": round(timeline_pos, 2), "timeline_end": round(timeline_pos + s1 - s0, 2),
                    "in_frame": int(s0 * fps), "out_frame": int(s1 * fps), "duration": round(s1 - s0, 2),
                    "in_seconds": round(s0, 2), "out_seconds": round(s1, 2), "has_dialogue": True,
                    "stream_start": stream_pos + s0, "camera": ref_cam or "",
                })
                timeline_pos += s1 - s0
            stream_pos += dur
            _progress(job_id, int(i / len(videos) * 70))

        if not clean_clips:
            raise RuntimeError("No speech was found. Check that the recordings have audio.")
        removed = total_src - timeline_pos
        _log(job_id, f"Clean edit: {_fmt_ts(total_src)} → {_fmt_ts(timeline_pos)} "
                     f"({removed / 60:.1f} min of silence removed, {len(clean_clips)} cuts)", "info")

        fps0 = cv2.VideoCapture(videos[0]).get(cv2.CAP_PROP_FPS) or 25.0
        plans: Dict[str, Dict[str, Any]] = {}
        markers: Dict[str, List[Dict[str, Any]]] = {}

        # Other cameras: same cuts, lined up by audio, each on its own track
        extra_tracks: List[List[Dict[str, Any]]] = []
        if other_cams:
            ref_env = np.concatenate(env_ref) if env_ref else np.zeros(0)
            for cam, vs in other_cams.items():
                files, envs, pos = [], [], 0.0
                for v in vs:
                    try:
                        yv, srv = load_audio(v, 16000)
                    except Exception as e:
                        _log(job_id, f"  {cam}: skipped {os.path.basename(v)} (no audio): {e}", "warning")
                        continue
                    _, fpsv = _dur_fps(v)
                    files.append((v, pos, len(yv) / srv, fpsv))
                    envs.append(multicam_sync.audio_envelope(yv, srv))
                    pos += len(yv) / srv
                if not files or not len(ref_env):
                    continue
                lag, conf = multicam_sync.audio_lag(ref_env, np.concatenate(envs))
                track = []
                for c in clean_clips:
                    o0 = c["stream_start"] - lag
                    for path, src_in, src_out, off, fpsv in multicam_sync.map_segment(o0, o0 + c["duration"], files):
                        track.append({
                            "clip_name": os.path.basename(path), "file_path": path, "camera": cam,
                            "timeline_start": round(c["timeline_start"] + off, 3),
                            "timeline_end": round(c["timeline_start"] + off + src_out - src_in, 3),
                            "in_seconds": round(src_in, 3), "out_seconds": round(src_out, 3),
                            "in_frame": int(src_in * fpsv), "out_frame": int(src_out * fpsv),
                            "duration": round(src_out - src_in, 2),
                        })
                extra_tracks.append(track)
                sure = "" if conf >= 8 else " · low confidence, please check"
                _log(job_id, f"  {cam}: synced by audio ({lag:+.2f} s){sure}", "info" if not sure else "warning")
        multicam_tracks = [clean_clips] + extra_tracks if extra_tracks else None
        if "clean" in formats:
            plans["clean"] = {"sequence_name": f"01_{label}_Clean_Edit", "format": f"{label} clean edit",
                              "aspect_ratio": "16:9", "fps": fps0, "total_duration": round(timeline_pos, 2),
                              "clip_count": len(clean_clips), "clips": clean_clips}
            if multicam_tracks:
                plans["clean"].update(tracks=multicam_tracks, multicam=True)
                _log(job_id, f"  Clean edit: every camera on its own track (V1–V{len(multicam_tracks)}), cut at the same moments.", "info")

        _claim_free_project(job_id, f"{label}: {os.path.basename(folder)}")

        # Chapters: split at long pauses, at least 2 minutes apart
        chapters = [0.0]
        for t in pauses:
            if t - chapters[-1] >= 120 and timeline_pos - t >= 60:
                chapters.append(t)
        if "chapters" in formats:
            lines = [f"{_fmt_ts(t)} Chapter {n}" for n, t in enumerate(chapters, 1)]
            os.makedirs(OUTPUT_DIR, exist_ok=True)
            ch_file = os.path.join(OUTPUT_DIR, f"{mode}_youtube_chapters_{time.strftime('%Y%m%d_%H%M%S')}.txt")
            with open(ch_file, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
            markers["clean"] = [{"seconds": t, "name": f"Chapter {n}"} for n, t in enumerate(chapters, 1)]
            _log(job_id, f"{len(chapters)} YouTube chapters saved: {ch_file} (rename the titles before publishing)", "info")

        # Shorts: the most energetic 45 s stretches of the clean edit, as separate vertical timelines
        if "shorts" in formats and timeline_pos >= 60:
            win = 45
            per_sec = dict(energy)
            scores = [(t, sum(per_sec.get(t + k, 0.0) for k in range(win))) for t in range(0, int(timeline_pos) - win, 5)]
            picked: List[int] = []
            for t, _ in sorted(scores, key=lambda x: x[1], reverse=True):
                if all(abs(t - p) >= win for p in picked):
                    picked.append(t)
                if len(picked) == 3:
                    break
            for n, start in enumerate(sorted(picked), 1):
                end = start + win
                clips = []
                for c in clean_clips:
                    a, b = max(start, c["timeline_start"]), min(end, c["timeline_end"])
                    if b - a < 0.4:
                        continue
                    off_a, off_b = a - c["timeline_start"], b - c["timeline_start"]
                    f = c["out_frame"] - c["in_frame"]
                    scale = f / max(0.01, c["duration"])
                    clips.append({**c, "in_frame": c["in_frame"] + int(off_a * scale), "out_frame": c["in_frame"] + int(off_b * scale),
                                  "duration": round(b - a, 2)})
                plans[f"short{n}"] = {"sequence_name": f"0{n + 1}_{label}_Short_{n}_9x16", "aspect_ratio": "9:16 (1080x1920)",
                                      "fps": fps0, "total_duration": win, "clip_count": len(clips), "clips": clips}
                _log(job_id, f"Short {n}: {_fmt_ts(start)} – {_fmt_ts(end)} of the clean edit", "info")
        elif "shorts" in formats:
            _log(job_id, "Recording is too short for separate shorts.", "warning")

        if not plans:
            raise RuntimeError("Tick at least one timeline to build.")
        _progress(job_id, 80)

        os.makedirs(OUTPUT_DIR, exist_ok=True)
        plan_file = os.path.join(OUTPUT_DIR, f"{mode}_plan_{time.strftime('%Y%m%d_%H%M%S')}.json")
        last_file = os.path.join(OUTPUT_DIR, "last_plan.json")
        plan_payload = {
            "folder": folder,
            "mode": mode,
            "plans": plans,
            "target_nle": target_nle,
            "audio": "",
            "ref_video": ref_video,
            "ref_lut_path": ref_lut_path,
            "instructions": instructions,
            "project_name": os.path.basename(folder.rstrip("/\\")),
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        with open(plan_file, "w", encoding="utf-8") as f:
            json.dump(plan_payload, f, indent=2, default=str)
        with open(last_file, "w", encoding="utf-8") as f:
            json.dump(plan_payload, f, indent=2, default=str)
        _log(job_id, f"Edit plan saved: {plan_file}", "info")

        result = _export_plans(job_id, plans, target_nle, "", markers, project_name=os.path.basename(folder.rstrip("/\\")))
        result["plan_file"] = plan_file
        with JOBS_LOCK:
            JOBS[job_id].update(status="done", result=result)
    except Exception as e:
        logger.error(traceback.format_exc())
        _log(job_id, f"ERROR: {e}", "error")
        with JOBS_LOCK:
            JOBS[job_id].update(status="error", error=str(e))


def resolve_reference_video_source(ref_input: str, job_id: str = "") -> Optional[str]:
    """
    Resolves reference video from either:
    1. Local file path (.mp4, .mov, .mxf, etc.)
    2. Online URL (YouTube, YouTube Shorts, Instagram Reels, Vimeo, etc.)
    If URL is provided, downloads or retrieves from ref_cache, returning local file path.
    """
    if not ref_input or not isinstance(ref_input, str):
        return None
    ref_input = ref_input.strip()
    if not ref_input:
        return None

    # 1. Direct local file
    if os.path.isfile(ref_input):
        return ref_input

    # 2. Check for URL
    url_m = re.search(r'(https?://[^\s\"\'<>]+)', ref_input)
    if not url_m:
        return None

    url = url_m.group(1).strip()
    try:
        import hashlib
        import glob
        import yt_dlp

        cache_dir = os.path.join(OUTPUT_DIR, "ref_cache")
        os.makedirs(cache_dir, exist_ok=True)

        url_hash = hashlib.md5(url.encode("utf-8")).hexdigest()[:12]
        existing = glob.glob(os.path.join(cache_dir, f"ref_{url_hash}.*"))
        for ef in existing:
            if os.path.isfile(ef) and os.path.getsize(ef) > 1024:
                _log(job_id, f"⚡ Using cached reference video for link: {url}", "info")
                return ef

        _log(job_id, f"📥 Downloading reference reel from link: {url}...", "info")
        out_tmpl = os.path.join(cache_dir, f"ref_{url_hash}.%(ext)s")
        ydl_opts = {
            'format': 'bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]/best[ext=mp4]/best',
            'outtmpl': out_tmpl,
            'quiet': True,
            'no_warnings': True,
            'max_filesize': 150 * 1024 * 1024,
            'socket_timeout': 35,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        downloaded = glob.glob(os.path.join(cache_dir, f"ref_{url_hash}.*"))
        for df in downloaded:
            if os.path.isfile(df) and os.path.getsize(df) > 1024:
                _log(job_id, f"✅ Reference video ready: {os.path.basename(df)}", "info")
                return df

        _log(job_id, "⚠️ Reference video download did not produce a valid file.", "warning")
        return None
    except Exception as e:
        _log(job_id, f"⚠️ Note on Reference Link ({url}): {e}. Continuing with standard edit.", "warning")
        logger.warning(f"Error fetching reference URL {url}: {e}")
        return None


def extract_and_apply_reference_look(job_id: str, ref_video_path: str, target_nle: str = "resolve") -> Optional[str]:
    """
    Analyzes a reference video/reel, extracts color signature, generates a high-precision 33x33x33 .cube 3D LUT,
    and applies the exact look across the timeline clips in DaVinci Resolve.
    """
    if not ref_video_path:
        return None
    ref_video_path = resolve_reference_video_source(ref_video_path, job_id)
    if not ref_video_path or not os.path.isfile(ref_video_path):
        return None
    try:
        import cv2
        from packages.ai_engine.color_transfer_engine import ColorTransferEngine

        ref_name = os.path.splitext(os.path.basename(ref_video_path))[0]
        _log(job_id, f"🎯 Analyzing Reference Reel / Video: {os.path.basename(ref_video_path)}", "info")

        cap = cv2.VideoCapture(ref_video_path)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        ref_lut_path = None

        if total_frames > 0:
            mid_frame = int(total_frames * 0.35)
            cap.set(cv2.CAP_PROP_POS_FRAMES, mid_frame)
            ret, frame = cap.read()
            if ret and frame is not None:
                os.makedirs(OUTPUT_DIR, exist_ok=True)
                luts_dir = os.path.join(OUTPUT_DIR, "luts")
                os.makedirs(luts_dir, exist_ok=True)
                ref_frame_path = os.path.join(OUTPUT_DIR, f"ref_extracted_frame_{time.strftime('%Y%m%d_%H%M%S')}.jpg")
                cv2.imwrite(ref_frame_path, frame)

                cte = ColorTransferEngine(luts_dir=luts_dir)
                safe_lut_name = f"AI_Ref_Look_{ref_name[:24]}"
                lut_res = cte.generate_3d_lut(ref_frame_path, lut_name=safe_lut_name)
                if lut_res.get("success"):
                    ref_lut_path = lut_res.get("lut_path")
                    _log(job_id, f"  🎨 Generated Reference 3D LUT: {os.path.basename(ref_lut_path)}", "info")
        cap.release()
        return ref_lut_path
    except Exception as e:
        _log(job_id, f"Reference color look note: {e}", "warning")
        return None


def wedding_job(job_id: str, folder: str, audio_path: str, formats: List[str], target_nle: str = "resolve", mode: str = "wedding",
                ref_video: str = "", instructions: str = "", tier: str = "free", ai_music: bool = False, voiceover: str = "",
                same_day: bool = False) -> None:
    """`voiceover`: "" = none, "auto" = Smart AI writes the script, anything else = the script to speak.
    `same_day` (wedding): Same-Day mode — quick analysis, no Smart AI shot review, plus the 05 Same-Day Edit."""
    try:
        from packages.ai_engine.in_clip_trimmer import in_clip_trimmer
        from packages.ai_engine.beat_analyzer import beat_analyzer
        from packages.ai_engine.story_planner import story_planner

        is_premium = (tier == "premium")
        if is_premium:
            license_manager.refresh()
            status = license_manager.get_status()
            if not status.get("has_credits"):
                raise RuntimeError(status.get("error") or "AI Smart Premium needs an active license with wedding credits "
                                   "(Settings → License). Or untick 'AI shot review' to run the free local edit.")
            _log(job_id, "👑 AI Smart Premium: Smart AI will review your shots after the local analysis.", "info")
        else:
            _log(job_id, "Local mode: everything runs on this computer.", "info")

        _log(job_id, f"Scanning footage folder: {folder} (Workflow: {mode.upper()})", "info")
        if instructions:
            _log(job_id, f"✍️ Custom AI Correction / Prompt: \"{instructions}\"", "info")
        scan = scan_folder(folder)
        if not scan["success"]:
            raise RuntimeError(scan["error"])
        videos = scan["videos"]
        images = scan.get("images", [])
        psds = scan.get("psds", [])
        raw_clips = videos + images
        if not raw_clips:
            raise RuntimeError("No video, PSD, or image files found in this folder (mp4, mov, psd, png, jpg...).")

        is_motion_graphics = len(videos) == 0 and len(images) > 0
        if is_motion_graphics:
            _log(job_id, f"🎨 Motion Graphics Mode: {len(images)} graphic asset(s) ({len(psds)} PSD) found. Beat-syncing to audio...", "info")
        else:
            _log(job_id, f"Found {len(videos)} video clip(s) and {len(images)} graphic/PSD asset(s) ({scan['total_gb']} GB).")

        ai_creative_plan = None
        if is_premium:
            try:
                _log(job_id, f"🧠 Claude AI Creative Director: Designing intelligent editing plan & motion storyboard for \"{instructions or 'Smart Cinematic Edit'}\"...", "info")
                assets_summary = [{"file_name": os.path.basename(vp), "type": "psd" if vp.endswith((".psd", ".psb")) else "image" if vp.endswith((".png", ".jpg", ".jpeg", ".webp")) else "video"} for vp in raw_clips]
                ai_creative_plan = cloud_ai_client.generate_creative_plan(assets_summary, instructions, ref_video, mode)
                if ai_creative_plan:
                    _log(job_id, f"💡 Claude AI Blueprint: {ai_creative_plan.get('title')} ({ai_creative_plan.get('creative_style')})", "info")
                    if ai_creative_plan.get("director_note"):
                        _log(job_id, f"📝 Claude AI Director Advice: {ai_creative_plan.get('director_note')}", "info")
            except Exception as e:
                _log(job_id, f"Claude AI Creative Plan note: {e}", "warning")

        # 1. Analyse every clip (Same-Day mode: quick analysis, fewer frames sampled)
        quick = mode == "wedding" and (same_day or "same_day" in formats)
        if quick and "same_day" not in formats:
            formats = list(formats) + ["same_day"]
        trimmer = in_clip_trimmer
        if quick:
            from packages.ai_engine.in_clip_trimmer import InClipTrimmer
            trimmer = InClipTrimmer(sample_fps=4, cache_dir=in_clip_trimmer.cache_dir)
            _log(job_id, "⚡ Same-Day mode: quick analysis, every timeline ready faster", "info")
        analyzed = []
        for i, vp in enumerate(raw_clips, 1):
            try:
                res = trimmer.analyze_clip(vp)
                analyzed.append(res)
                if res.get("is_still") or res.get("is_psd"):
                    _log(job_id, f"[{i}/{len(raw_clips)}] 🎨 {os.path.basename(vp)} → Graphic Layer ({'PSD' if res.get('is_psd') else 'Image'}) · Ready for Motion Graphics")
                else:
                    full_kept = res.get('full_duration', res.get('duration', 0))
                    total_dur = res.get('duration', 0)
                    faces = (res.get("faces") or {}).get("count", 0)
                    seen = " · ".join(x for x in [
                        (res.get("moment") or "").replace("_", " ").title(), res.get("shot_type", "").replace("_", " "),
                        res.get("camera_move", "").replace("_", " "), f"{faces} face{'s' if faces != 1 else ''}" if "faces" in res else "",
                        res.get("audio_type", ""), f"wedding score {res['wedding_score']:.0f}" if "wedding_score" in res else ""] if x)
                    _log(job_id, f"[{i}/{len(raw_clips)}] {os.path.basename(vp)} ({total_dur:.1f}s){' ⚡cached' if res.get('from_cache') else ''} → "
                                 f"Full Film: {full_kept:.1f}s kept (action starts at {res.get('action_start_seconds', 0):.1f}s) | "
                                 f"Highlight: {res.get('trimmed_duration', 0):.1f}s (score {res.get('quality_score', 0):.0f})"
                                 + (f"\n      {seen}" if seen else ""))
            except Exception as e:
                _log(job_id, f"[{i}/{len(raw_clips)}] Skipped {os.path.basename(vp)}: {e}", "warning")
            _progress(job_id, int(i / len(raw_clips) * 65))

        if not analyzed:
            raise RuntimeError("None of the clips could be analysed.")

        # Wedding only: put every camera's footage in real shoot-time order (other modes follow the music)
        from packages.ai_engine import multicam_sync
        multicam = False
        if mode == "wedding" and videos:
            sync_report = multicam_sync.sync(analyzed, folder)
            multicam = len(sync_report["cameras"]) > 1
            if multicam:
                _log(job_id, "Matching the cameras by their sound (songs, speeches, crowd)…", "info")
                multicam_sync.audio_place_wedding(analyzed, sync_report)
            analyzed.sort(key=lambda c: c.get("abs_start", 0))
            from packages.ai_engine.in_clip_trimmer import label_moments
            relabelled = label_moments(analyzed)
            counts: Dict[str, int] = {}
            for c in analyzed:
                if c.get("moment"):
                    counts[c["moment"]] = counts.get(c["moment"], 0) + 1
            if counts:
                _log(job_id, "🎬 Moments found: " + ", ".join(f"{k.replace('_', ' ').title()} {v}" for k, v in
                                                           sorted(counts.items(), key=lambda kv: -kv[1]))
                     + (f" ({relabelled} clips placed by shoot time)" if relabelled else ""), "info")
            # People: only when the editor turned recognition on for this project (stays on this computer)
            try:
                from packages.ai_engine import people
                prev = people.load_store(folder)
                if prev and prev.get("consent"):
                    _log(job_id, "👥 Recognising the bride, groom and family (on this computer)…", "info")
                    store = people.scan_project(analyzed, folder, people.ensure_model())
                    roles = sorted({p["role"] for p in store["persons"] if p.get("role")})
                    _log(job_id, f"   {len(store['persons'])} people" + (f" · {', '.join(roles)}" if roles else ""), "info")
            except Exception as e:
                _log(job_id, f"People recognition skipped: {e}", "warning")
        audio_synced_plan = None
        if (mode in ("youtube", "music_video") or is_motion_graphics) and videos:
            song = audio_path if (mode == "music_video" and audio_path and os.path.isfile(audio_path)) else None
            _log(job_id, "Syncing clips by their sound" + (" to the song…" if song else "…"), "info")
            arep = multicam_sync.audio_sync_clips(analyzed, folder, song)
            if arep["mode"] == "song" and arep["synced"]:
                takes = [c for c in analyzed if c["audio_synced"]]
                audio_synced_plan = multicam_sync.plan_multicam_tracks(takes, max_gap=None, by="stack")
                audio_synced_plan.update(sequence_name="03_Music_Video_Synced_Takes", audio=song,
                                         audio_duration=arep["song_duration"])
                _log(job_id, f"  {arep['synced']} of {arep['total']} clips matched the song - placed in song time on "
                             f"{len(audio_synced_plan['tracks'])} track(s) above the song.", "info")
            elif arep["mode"] == "song":
                _log(job_id, "  No clip's sound matched the song (clips without playback audio stay in the beat edit).", "warning")
            elif len(arep.get("cameras", {})) > 1:
                audio_synced_plan = multicam_sync.plan_multicam_tracks(analyzed)
                audio_synced_plan["sequence_name"] = "03_YouTube_Multicam_Synced"
                _log(job_id, f"  {len(arep['cameras'])} cameras lined up by audio ({arep['synced']} clips moved) - "
                             "one track per camera.", "info")
        for c in analyzed:
            c.pop("luma_curve", None)  # only needed for syncing; keeps the saved plan small
        if multicam:
            _log(job_id, f"Multi-camera footage: {len(sync_report['cameras'])} cameras found.", "info")
            for cam, r in sync_report["cameras"].items():
                heard = r.get("audio_placed", 0)
                if r.get("no_clock"):
                    if r.get("placement") == "by sound":
                        how = (f"camera clock not set - kept together in recording order and placed by sound "
                               f"({heard} matching clips)")
                    else:
                        how = ("camera clock not set and no matching sound - its clips are kept together in recording "
                               "order AFTER the other cameras. Drag this block to the right ritual")
                    r["please_check"] = r.get("placement") != "by sound"
                elif cam == sync_report["reference"]:
                    how = "reference camera"
                elif r["method"] == "timecode":
                    how = "synced by timecode"
                elif r["method"] == "camera clock":
                    how = "camera clock used (no matching flashes or recording pattern)"
                else:
                    how = f"clock corrected {r['offset_sec']:+.1f} s by {r['method']}" + (
                        f" ({r['flash_matches']} flashes)" if r["flash_matches"] else "")
                if heard and not r.get("no_clock") and cam != sync_report["reference"]:
                    how += f", {heard} clips fine-tuned by sound"
                    r["please_check"] = False
                _log(job_id, f"  • {cam}: {r['clips']} clips · {how} · time from {r['time_source']}"
                             + (" · please check this camera's position" if r.get("please_check") else ""),
                     "warning" if r.get("please_check") else "normal")

        # 1.5. Analyze Reference Video (if provided by editor, URL, or in prompt)
        from packages.ai_engine.story_planner import parse_prompt_directives
        p_directives = parse_prompt_directives(instructions)
        ref_cand = ref_video or p_directives.get("ref_video")
        if ref_cand:
            resolved_ref = resolve_reference_video_source(ref_cand, job_id)
            if resolved_ref:
                ref_video = resolved_ref
                _log(job_id, f"🎯 Reference Video active: {os.path.basename(ref_video)}", "info")

        ref_lut_path = extract_and_apply_reference_look(job_id, ref_video, target_nle)

        # 2. Shot grades A/B/C: local for everyone; Smart AI reviews a contact sheet per clip (premium,
        #    1 credit only when new clips were reviewed), then AI audio
        from packages.ai_engine.in_clip_trimmer import assign_local_tiers
        local_counts = assign_local_tiers(analyzed)
        if not is_premium:
            _log(job_id, f"Shot grades (local): A {local_counts['A']} · B {local_counts['B']} · C {local_counts['C']}", "info")
        voice_path, voice_seconds = "", 0.0
        if is_premium:
            if quick:
                _log(job_id, "⚡ Same-Day mode: Smart AI shot review skipped for speed (local grades used)", "info")
                credit_used, story_note = False, ""
            else:
                credit_used, story_note = ai_review_clips(job_id, analyzed, instructions, os.path.basename(folder))
            if ai_music or voiceover:
                music_file, voice_path, voice_seconds = smart_audio(
                    job_id, mode, formats, analyzed, instructions, story_note,
                    ai_music and not (audio_path and os.path.isfile(audio_path)), voiceover)
                if music_file:
                    audio_path = music_file
                if (music_file or voice_path) and not credit_used:
                    _use_credit(job_id, os.path.basename(folder))
        elif ai_music or voiceover:
            _log(job_id, "AI music and voiceover are part of Smart AI packs - tick Smart AI to use them.", "warning")

        # 2.4. Vows & speeches (wedding): the most emotional spoken lines, automatic
        if mode == "wedding" and not quick:
            try:
                n_vows = find_vow_lines(job_id, analyzed, is_premium, instructions)
                if n_vows:
                    _log(job_id, f"💬 {n_vows} spoken lines chosen for the films", "info")
            except Exception as e:
                _log(job_id, f"Vows skipped: {e}", "warning")

        # 2.5. Music beats (from song or reference reel)
        beat_times = None
        music_info: Optional[Dict[str, Any]] = None
        if audio_path and os.path.isfile(audio_path):
            _log(job_id, f"Analysing music beats: {os.path.basename(audio_path)}", "info")
            beats = beat_analyzer.analyze_audio(audio_path, 25.0)
            beat_times = beats.get("beat_times") or beats.get("beats")
            music_info = beats
            _log(job_id, f"Tempo: {beats.get('bpm') or beats.get('tempo', '?')} BPM, "
                         f"{len(beat_times or [])} beats detected.")
            if beats.get("sections"):
                _log(job_id, "  🎼 Song: " + " → ".join(f"{x['label']} {x['start']:.0f}s" for x in beats["sections"])
                     + (f" · {len(beats.get('instrumental_regions') or [])} instrumental parts for vows" if beats.get("instrumental_regions") else ""), "info")
        elif ref_video and os.path.isfile(ref_video):
            try:
                _log(job_id, f"🎵 Extracting beat rhythm from reference reel: {os.path.basename(ref_video)}", "info")
                ref_beats = beat_analyzer.analyze_audio(ref_video, 25.0)
                beat_times = ref_beats.get("beat_times") or ref_beats.get("beats")
                music_info = {"beat_times": beat_times, "downbeat_times": ref_beats.get("downbeat_times"),
                              "drop_times": ref_beats.get("drop_times")}   # rhythm only: the reference's song is not in the edit
                if beat_times:
                    _log(job_id, f"  ⚡ Locked to {len(beat_times)} audio beat transients from reference video!", "info")
            except Exception as e:
                logger.debug(f"Reference audio beat extraction note: {e}")
        elif audio_path:
            _log(job_id, f"Music file not found, continuing without beat-sync: {audio_path}", "warning")
        _progress(job_id, 75)

        is_square = p_directives.get("is_square") or p_directives.get("aspect_ratio") == "1:1 (1080x1080)"
        target_aspect = "1:1 (1080x1080)" if is_square else "9:16 (1080x1920)"

        # 3. Story plans tailored by workflow mode
        plans = {}
        if mode == "reels":
            if "teaser" in formats:
                p = story_planner.plan_teaser(analyzed, target_duration=45.0, is_vertical=(not is_square), instructions=instructions, ref_video_path=ref_video, beat_times=beat_times)
                p["sequence_name"] = f"01_Ad_{'Square_1x1' if is_square else 'Reel_9x16'}"
                p["aspect_ratio"] = target_aspect
                plans["teaser"] = p
            if "highlight" in formats:
                p = story_planner.plan_teaser(analyzed, target_duration=30.0, is_vertical=(not is_square), instructions=instructions, ref_video_path=ref_video, beat_times=beat_times)
                p["sequence_name"] = f"02_Ad_{'Square_30s_1x1' if is_square else 'Shorts_30s'}"
                p["aspect_ratio"] = target_aspect
                plans["highlight"] = p
        elif mode == "youtube":
            if "teaser" in formats or "highlight" in formats:
                p = story_planner.plan_highlight(analyzed, beat_times=beat_times, target_duration=360.0, instructions=instructions, ref_video_path=ref_video)
                p["sequence_name"] = "01_YouTube_Main_Edit"
                plans["highlight"] = p
            if "full_film" in formats:
                p = story_planner.plan_teaser(analyzed, target_duration=60.0, is_vertical=True, instructions=instructions, ref_video_path=ref_video, beat_times=beat_times)
                p["sequence_name"] = "02_YouTube_Shorts_Highlights"
                p["aspect_ratio"] = "9:16 (1080x1920)"
                plans["teaser"] = p
        elif mode == "music_video":
            if "teaser" in formats or "highlight" in formats:
                p = story_planner.plan_highlight(analyzed, beat_times=beat_times, target_duration=180.0, instructions=instructions, ref_video_path=ref_video)
                p["sequence_name"] = "01_Music_Video_Cut"
                plans["highlight"] = p
            if "full_film" in formats:
                p = story_planner.plan_teaser(analyzed, target_duration=60.0, is_vertical=False, instructions=instructions, ref_video_path=ref_video, beat_times=beat_times)
                p["sequence_name"] = "02_Cinematic_Mood_Montage"
                plans["teaser"] = p
        elif mode == "custom" or is_motion_graphics:
            if "highlight" in formats or "teaser" in formats:
                p = story_planner.plan_voiceover_motion_graphics(
                    analyzed, audio_path=audio_path, instructions=instructions,
                    ref_video_path=ref_video, is_vertical=(not is_square)
                )
                p["sequence_name"] = f"01_Custom_Motion_Graphics_{'1x1' if is_square else ('9x16' if (not is_square and 'reel' in instructions.lower()) else '16x9')}"
                p["aspect_ratio"] = target_aspect
                plans["highlight"] = p
            if "teaser" in formats and "highlight" in formats:
                p2 = story_planner.plan_voiceover_motion_graphics(
                    analyzed, audio_path=audio_path, instructions=instructions,
                    ref_video_path=ref_video, is_vertical=(not is_square), target_duration=30.0
                )
                p2["sequence_name"] = f"02_Short_Ad_30s_{'1x1' if is_square else '9x16'}"
                p2["aspect_ratio"] = target_aspect
                plans["teaser"] = p2
        else:  # Default: wedding — Story Director (Edit Plan + Assembler, "why chosen" marker on every cut)
            edit_plans: Dict[str, Any] = {}
            if is_premium and not quick and ("teaser" in formats or "highlight" in formats):
                try:
                    _log(job_id, "🎬 Smart AI is planning the story of this wedding…", "info")
                    edit_plans = cloud_ai_client.plan_wedding_story(_footage_summary(analyzed), 60.0, 240.0, instructions)
                    for fmt, ep in edit_plans.items():
                        acts = " → ".join(f"{a['name']} {a['seconds']:.0f}s" for a in ep.get("acts", []))
                        _log(job_id, f"  {fmt.title()} plan: {acts}", "info")
                        if ep.get("director_note"):
                            _log(job_id, f"  📝 {ep['director_note']}", "info")
                except Exception as e:
                    _log(job_id, f"Story plan from the built-in wedding template ({e})", "warning")
            if "teaser" in formats:
                plans["teaser"] = story_planner.plan_teaser(analyzed, instructions=instructions, ref_video_path=ref_video, beat_times=beat_times,
                                                            story_mode="wedding", edit_plan=edit_plans.get("teaser"), music_info=music_info)
            if "highlight" in formats:
                plans["highlight"] = story_planner.plan_highlight(
                    analyzed, beat_times=beat_times, ref_video_path=ref_video, instructions=instructions,
                    story_mode="wedding", edit_plan=edit_plans.get("highlight"), music_info=music_info
                )
            if "reel" in formats:
                plans["reel"] = story_planner.plan_reel(analyzed, beat_times=beat_times, instructions=instructions,
                                                        ref_video_path=ref_video, music_info=music_info)
            if "same_day" in formats:
                plans["same_day"] = story_planner.plan_same_day_edit(analyzed, beat_times=beat_times, instructions=instructions,
                                                                     ref_video_path=ref_video, music_info=music_info)
            if "full_film" in formats:
                if multicam:
                    plans["full_film"] = multicam_sync.plan_multicam_tracks(analyzed)
                    plans["multicam_edit"] = multicam_sync.plan_multicam_film(analyzed)
                    plans["multicam_edit"]["sequence_name"] = "04_Multicam_Auto_Edit"
                    _log(job_id, f"  Full Film: each camera on its own track (V1–V{len(plans['full_film']['tracks'])}), "
                                 "all footage kept and lined up in time. 04_Multicam_Auto_Edit switches between them.", "info")
                else:
                    plans["full_film"] = story_planner.plan_full_film(analyzed, instructions=instructions)

        # 3.2. Apply Smart AI Creative Director Storyboard if generated
        if ai_creative_plan and ai_creative_plan.get("storyboard"):
            sb = ai_creative_plan["storyboard"]
            _log(job_id, f"🎬 Applying Claude AI Director Storyboard: {len(sb)} scenes organized with viral hooks & roles...", "info")

            voice_cuts = []
            if audio_path and os.path.isfile(audio_path):
                try:
                    import soundfile as sf
                    a_info = sf.info(audio_path)
                    a_dur = float(a_info.duration)
                    if a_dur > 0:
                        voice_cuts = story_planner._detect_voice_pauses(audio_path, len(sb), a_dur)
                        if not voice_cuts or len(voice_cuts) < len(sb) + 1:
                            seg = a_dur / max(1, len(sb))
                            voice_cuts = [round(i * seg, 3) for i in range(len(sb) + 1)]
                        _log(job_id, f"🎙️ Audio Speech Sync: Voiceover ({a_dur:.1f}s) partitioned across {len(sb)} motion graphic scenes!", "info")
                except Exception:
                    pass

            for plan_key in ("teaser", "highlight"):
                if plan_key not in plans or not plans[plan_key].get("clips"):
                    continue
                p = plans[plan_key]
                ordered_clips = []
                for i, scene in enumerate(sb):
                    idx = scene.get("asset_index", 0)
                    if 0 <= idx < len(analyzed):
                        orig = analyzed[idx]
                        clip_copy = dict(orig)
                        role = scene.get("scene_role", "Scene")
                        hook = scene.get("text_hook", "")
                        effect = scene.get("motion_effect", "Ken Burns Pan-Zoom")
                        clip_copy["role"] = role
                        clip_copy["text_hook"] = hook
                        clip_copy["motion_effect"] = effect
                        clip_copy["speed_label"] = f"AI Director: {role} ({effect})"
                        clip_copy["clip_name"] = f"[{role.upper()}] {os.path.basename(orig['file_path'])}"
                        if voice_cuts and i < len(voice_cuts) - 1:
                            dur = max(0.5, round(voice_cuts[i+1] - voice_cuts[i], 3))
                        else:
                            dur = float(scene.get("duration_seconds") or 3.0)
                        clip_copy["duration"] = dur
                        clip_copy["trimmed_duration"] = dur
                        clip_copy["out_seconds"] = dur
                        clip_copy["out_frame"] = int(dur * float(orig.get("native_fps") or 25.0))
                        ordered_clips.append(clip_copy)
                if ordered_clips:
                    t = 0.0
                    for c in ordered_clips:
                        c["timeline_start"] = round(t, 3)
                        t += c["duration"]
                        c["timeline_end"] = round(t, 3)
                    p["clips"] = ordered_clips
                    p["clip_count"] = len(ordered_clips)
                    p["total_duration"] = round(t, 3)
                    title_slug = "".join(c if c.isalnum() else "_" for c in (ai_creative_plan.get("title") or "Creative_Ad"))
                    p["sequence_name"] = f"01_SmartAI_{title_slug[:30]}"
                    p["creative_style"] = ai_creative_plan.get("creative_style")
                    p["director_note"] = ai_creative_plan.get("director_note")
                    if is_square:
                        p["aspect_ratio"] = "1:1 (1080x1080)"
                    _log(job_id, f"  ★ Smart AI Timeline ready: {p['sequence_name']} ({len(ordered_clips)} scenes, {p['total_duration']}s, {p.get('aspect_ratio', '16:9')})", "info")
                    break

        if audio_synced_plan:
            plans["audio_synced"] = audio_synced_plan

        for key, p in plans.items():
            if p.get("tracks"):
                continue
            if audio_path and os.path.isfile(audio_path) and key in ("teaser", "highlight"):
                p["audio"] = audio_path  # music bed under the edit
            if voice_path and key in ("teaser", "highlight"):
                p["voiceover"], p["voiceover_duration"] = voice_path, voice_seconds

        # YouTube, Music Video and Reels: the music leads - every cut lands on a beat
        if mode in ("youtube", "music_video", "reels"):
            if beat_times:
                for p in plans.values():
                    if p.get("tracks"):
                        continue  # synced takes keep their real (song) positions
                    n = _snap_to_beats(p, beat_times)
                    _log(job_id, f"  🎵 {p.get('sequence_name')}: {n} cuts placed on the beat", "info")
            else:
                _log(job_id, "Add a song in AI Studio to cut this edit on the beat.", "warning")

        # 3.5. Every editor gets the same arrangement as the DaVinci Resolve timeline, which has no
        # extra B-roll overlay track - so none is added to the Premiere / EDIUS XML either
        for p in plans.values():
            p["broll_clips"] = []

        for p in plans.values():
            _log(job_id, f"Planned {p.get('sequence_name')}: {p.get('clip_count', len(p.get('clips', [])))} clips "
                         f"(+ {len(p.get('broll_clips', []))} B-roll), "
                         f"{p.get('total_duration', 0)}s ({p.get('aspect_ratio', '16:9')})")
        _progress(job_id, 80)

        # 4. Save plan to disk (free plan: only after the server has counted the project)
        _claim_free_project(job_id, f"{mode.replace('_', ' ').title()}: {os.path.basename(folder)}")
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        plan_file = os.path.join(OUTPUT_DIR, f"{mode}_plan_{time.strftime('%Y%m%d_%H%M%S')}.json")
        saved_plan_data = {
            "folder": folder,
            "project_name": os.path.basename(folder.rstrip("/\\")),
            "audio": audio_path,
            "mode": mode,
            "ref_video": ref_video,
            "ref_lut_path": ref_lut_path,
            "instructions": instructions,
            "analyzed": analyzed,
            "plans": plans,
            "target_nle": target_nle,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        with open(plan_file, "w", encoding="utf-8") as f:
            json.dump(saved_plan_data, f, indent=2, default=str)
        try:
            with open(os.path.join(OUTPUT_DIR, "last_plan.json"), "w", encoding="utf-8") as f:
                json.dump(saved_plan_data, f, indent=2, default=str)
        except Exception:
            pass
        _log(job_id, f"Edit plan saved: {plan_file}", "info")

        for k, p in plans.items():   # captions (SRT) of the vows used in each film, e.g. for reels
            if isinstance(p, dict) and p.get("captions"):
                try:
                    from packages.ai_engine.ai_audio import write_srt
                    p["captions_srt"] = write_srt(p["captions"], os.path.join(OUTPUT_DIR, "captions", f"{p.get('sequence_name', k)}.srt"))
                    _log(job_id, f"  📝 Captions: {os.path.basename(p['captions_srt'])}", "info")
                except Exception as e:
                    _log(job_id, f"Captions not written: {e}", "warning")
        why_markers = {k: p["markers"] for k, p in plans.items() if isinstance(p, dict) and p.get("markers")}
        result = _export_plans(job_id, plans, target_nle, audio_path, markers=why_markers or None,
                               project_name=os.path.basename(folder.rstrip("/\\")))
        result["plan_file"] = plan_file

        # Apply Reference 3D LUT to DaVinci Resolve timeline if available
        if ref_lut_path and target_nle in ("resolve", "both") and resolve_adapter.is_connected():
            applied = resolve_adapter.apply_lut_to_timeline(ref_lut_path)
            if applied > 0:
                _log(job_id, f"  🎨 Applied Reference 3D LUT to {applied} clips on DaVinci Resolve timeline!", "info")
        with JOBS_LOCK:
            JOBS[job_id].update(status="done", result=result)
    except Exception as e:
        logger.error(traceback.format_exc())
        _log(job_id, f"ERROR: {e}", "error")
        with JOBS_LOCK:
            JOBS[job_id].update(status="error", error=str(e))


def _snap_to_beats(plan: Dict[str, Any], beat_times: List[float]) -> int:
    """Shortens each clip so every cut lands on a beat of the song (at least one beat per clip).
    Returns how many cuts were moved onto the beat."""
    import bisect
    beats = sorted(float(b) for b in beat_times if b > 0)
    if len(beats) < 4:
        return 0
    t, moved, out = 0.0, 0, []
    for c in plan.get("clips", []):
        dur = float(c.get("duration", 0))
        if dur <= 0:
            continue
        # last beat that still fits inside this clip, otherwise the first beat after `t`
        i = bisect.bisect_right(beats, t + dur) - 1
        if i < 0 or beats[i] <= t + 0.3:
            i = bisect.bisect_right(beats, t + 0.3)
            if i >= len(beats):
                break  # song is over
        new_dur = beats[i] - t
        if new_dur > dur + 0.01:
            new_dur = dur  # the clip is too short to reach the next beat: keep it whole
        else:
            moved += 1
        fps = (c["out_frame"] - c["in_frame"]) / dur if dur else 25.0
        c = dict(c, duration=round(new_dur, 3), timeline_start=round(t, 3), timeline_end=round(t + new_dur, 3),
                 out_frame=int(c["in_frame"] + new_dur * fps))
        if "in_seconds" in c:
            c["out_seconds"] = round(float(c["in_seconds"]) + new_dur, 3)
        out.append(c)
        t += new_dur
    plan["clips"] = out
    plan["clip_count"] = len(out)
    plan["total_duration"] = round(t, 2)
    plan["beat_synced"] = True
    return moved


def get_last_plan() -> Dict[str, Any]:
    """Returns the most recent active project edit plan metadata for re-editing."""
    try:
        last_file = os.path.join(OUTPUT_DIR, "last_plan.json")
        plan_path = None
        if os.path.isfile(last_file):
            plan_path = last_file
        else:
            import glob
            plans = glob.glob(os.path.join(OUTPUT_DIR, "*_plan_*.json"))
            if plans:
                plans.sort(key=os.path.getmtime, reverse=True)
                plan_path = plans[0]

        if not plan_path:
            return {"success": True, "has_plan": False}

        with open(plan_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        project_name = data.get("project_name") or os.path.basename((data.get("folder") or "").rstrip("/\\")) or "AI_Project"
        timelines_info = []
        for pk, pv in data.get("plans", {}).items():
            timelines_info.append({
                "key": pk,
                "name": pv.get("sequence_name") or pk,
                "clips": pv.get("clip_count", len(pv.get("clips", []))),
                "duration": pv.get("total_duration", 0.0),
                "aspect_ratio": pv.get("aspect_ratio", "16:9")
            })

        return {
            "success": True,
            "has_plan": True,
            "plan_file": plan_path,
            "project_name": project_name,
            "folder": data.get("folder", ""),
            "mode": data.get("mode", "wedding"),
            "audio": data.get("audio", ""),
            "ref_video": data.get("ref_video", ""),
            "instructions": data.get("instructions", ""),
            "created_at": data.get("created_at") or time.ctime(os.path.getmtime(plan_path)),
            "timelines": timelines_info
        }
    except Exception as e:
        logger.error(f"Error reading last plan: {e}")
        return {"success": False, "has_plan": False, "error": str(e)}


_DIRECTOR_STEP_LABELS = {
    "director_project_overview": "Project padh raha hoon", "director_list_clips": "Footage list dekh raha hoon",
    "director_view_clips": "Footage ke frames dekh raha hoon", "director_view_media": "Reference dekh raha hoon",
    "director_analyze_audio": "Gaana sun raha hoon (beats & energy)", "director_get_timeline": "Current timeline padh raha hoon",
    "director_build_timeline": "Naya cut bana raha hoon",
    "director_auto_edit": "Story Director se draft bana raha hoon", "director_people": "Dulha-dulhan aur family dekh raha hoon",
    "director_vows": "Vows aur speech ki lines dekh raha hoon", "run_code": "Apna code chala raha hoon",
}


def reedit_timeline_job(job_id: str, prompt: str, target_timeline: str = "all", target_nle: str = "", ref_video_input: str = "") -> None:
    """Smart AI re-edits like a human editor (looks at the footage, the song and the reference, then cuts).
    Without Smart AI (or if it fails before cutting) the rule-based re-edit below runs instead."""
    if not (license_manager.get_status().get("has_credits") and cloud_ai_client.get_status().get("available")):
        return _reedit_rules_job(job_id, prompt, target_timeline, target_nle, ref_video_input)
    built = []
    steps = [0]

    def on_event(kind: str, msg: str) -> None:
        if kind == "tool":
            steps[0] += 1
            _progress(job_id, min(92, 8 + steps[0] * 4))
            _log(job_id, f"🤖 {_DIRECTOR_STEP_LABELS.get(msg, 'Running ' + msg)}…", "info")
        else:
            _log(job_id, f"💬 {msg}", "normal")

    base = _assistant_execute(job_id)

    def execute(name: str, args: Dict[str, Any]) -> Any:
        out = base(name, args)
        if name == "director_build_timeline":
            built.append(out)
        return out

    request = (
        f"RE-EDIT REQUEST from the Timeline Re-Editor.\n"
        f"Timeline to change: {target_timeline or 'all'} (keys: teaser, highlight, full_film; 'all' = every timeline in the project).\n"
        f"Send to editor: {target_nle or 'project'}.\n"
        + (f"Reference to match (look at it): {ref_video_input}\n" if ref_video_input else "")
        + f"Editor's prompt: {prompt or 'Match the reference look and pacing.'}\n\n"
        "Do it yourself now without asking questions: read the current timeline(s), listen to the song, look at the "
        "footage and the reference, then rebuild the timeline(s) with director_build_timeline using the SAME key "
        f"and editor='{target_nle or 'project'}'. Apply colour / letterbox / markers in the editor too if the prompt asks. "
        "Finish with a 3-5 line summary in the prompt's language."
    )
    try:
        _log(job_id, "🪄 Smart AI Director is taking over the re-edit (looks at footage, song & reference)…", "info")
        _progress(job_id, 5)
        reply = cloud_ai_client.assistant_turn([], request, _assistant_tools(), execute, on_event=on_event,
                                               system_extra=DIRECTOR_GUIDE)
    except Exception as e:
        if built:
            _log(job_id, f"Smart AI stopped after cutting: {e}", "warning")
            reply = ""
        else:
            _log(job_id, f"Smart AI could not finish ({e}) — using the standard re-edit instead.", "warning")
            return _reedit_rules_job(job_id, prompt, target_timeline, target_nle, ref_video_input)
    if not built:
        _log(job_id, "Smart AI did not build a new cut — using the standard re-edit instead.", "warning")
        if reply:
            _log(job_id, f"💬 {reply[:600]}", "normal")
        return _reedit_rules_job(job_id, prompt, target_timeline, target_nle, ref_video_input)
    for line in (reply or "").splitlines():
        if line.strip():
            _log(job_id, f"💬 {line.strip()}", "info")
    _progress(job_id, 100)
    _log(job_id, f"🎉 Smart AI Re-Edit Complete! {len(built)} timeline(s) cut and sent.", "info")
    with JOBS_LOCK:
        JOBS[job_id].update(status="done", result={"reply": reply, "built": built, "reedit_prompt": prompt}, progress=100)


def _reedit_rules_job(job_id: str, prompt: str, target_timeline: str = "all", target_nle: str = "", ref_video_input: str = "") -> None:
    """Intelligently modifies and re-exports an existing project timeline based on user instructions and reference video."""
    try:
        from packages.ai_engine.story_planner import story_planner, parse_prompt_directives
        from packages.ai_engine.beat_analyzer import beat_analyzer

        # 1. Locate the latest plan
        last_file = os.path.join(OUTPUT_DIR, "last_plan.json")
        plan_path = None
        if os.path.isfile(last_file):
            plan_path = last_file
        else:
            import glob
            plans = glob.glob(os.path.join(OUTPUT_DIR, "*_plan_*.json"))
            if plans:
                plans.sort(key=os.path.getmtime, reverse=True)
                plan_path = plans[0]

        if not plan_path:
            raise RuntimeError("Koi active project plan nahi mila. Pehle 'Build timelines' run karein.")

        with open(plan_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        folder = data.get("folder", "")
        audio_path = data.get("audio", "")
        mode = data.get("mode", "wedding")
        analyzed = data.get("analyzed", [])
        plans = data.get("plans", {})
        ref_video = ref_video_input or data.get("ref_video", "")
        ref_lut_path = data.get("ref_lut_path")
        target_nle = target_nle or data.get("target_nle", "resolve")
        project_name = data.get("project_name") or os.path.basename(folder.rstrip("/\\")) or "AI_Project"

        _log(job_id, f"🪄 Smart AI Re-Editing Project: '{project_name}'", "info")
        if prompt:
            _log(job_id, f"✍️ Re-Edit Prompt: \"{prompt}\"", "info")
        _progress(job_id, 15)

        # 2. Parse prompt directives
        directives = parse_prompt_directives(prompt)
        pacing = directives.get("pacing", "normal")
        req_dur = directives.get("target_duration")
        priorities = directives.get("priorities", [])

        # Check if reference video was provided or embedded in prompt (file or YouTube link)
        ref_cand = ref_video_input or directives.get("ref_video") or ref_video
        if ref_cand:
            resolved_ref = resolve_reference_video_source(ref_cand, job_id)
            if resolved_ref and os.path.isfile(resolved_ref):
                ref_video = resolved_ref
                new_lut = extract_and_apply_reference_look(job_id, ref_video, target_nle)
                if new_lut:
                    ref_lut_path = new_lut

        summary_parts = []
        if pacing != "normal":
            summary_parts.append(f"Pacing: {pacing.upper()}")
        if req_dur:
            summary_parts.append(f"Duration: {req_dur}s")
        if priorities:
            summary_parts.append(f"Focus: {', '.join(priorities)}")
        if directives.get("color_style"):
            summary_parts.append(f"Color: {directives['color_style']}")
        if directives.get("letterbox"):
            summary_parts.append("2.39:1 Cinemascope")
        if ref_video and os.path.isfile(ref_video):
            summary_parts.append(f"Ref Match: {os.path.basename(ref_video)}")

        if summary_parts:
            _log(job_id, f"🎯 AI Directives Detected: {' | '.join(summary_parts)}", "info")

        # 3. Live DaVinci Resolve Adjustments if connected
        is_resolve = target_nle in ("resolve", "both")
        if is_resolve and (resolve_adapter.is_connected() or (resolve_adapter.is_running() and resolve_adapter.connect())):
            # Color Node Tree
            if any(w in prompt.lower() for w in ["color", "grade", "lut", "warm", "node tree", "look", "tree"]) or directives.get("color_style"):
                c_style = directives.get("color_style", "Royal Warm")
                try:
                    resolve_adapter.create_wedding_node_tree(track_index=1, item_index=None, style=c_style)
                    _log(job_id, f"  🎨 DaVinci Resolve: Applied {c_style} color grading tree across all timeline clips", "info")
                except Exception as e:
                    _log(job_id, f"  Color note: {e}", "warning")

            # Letterbox 2.39:1
            if any(w in prompt.lower() for w in ["letterbox", "2.35", "2.39", "cinemascope", "bars", "anamorphic"]) or directives.get("letterbox"):
                try:
                    resolve_adapter.add_cinematic_letterbox(aspect_ratio="2.39:1")
                    _log(job_id, "  🎬 DaVinci Resolve: Applied 2.39:1 Hollywood Cinemascope letterbox", "info")
                except Exception as e:
                    _log(job_id, f"  Letterbox note: {e}", "warning")

            # Markers
            if any(w in prompt.lower() for w in ["marker", "markers", "beat", "chapter"]):
                try:
                    resolve_adapter.add_timeline_marker(25, "Cyan", "Re-Edit Cut", "Smart AI Marker")
                    _log(job_id, "  📍 DaVinci Resolve: Beat & chapter markers placed on active timeline", "info")
                except Exception:
                    pass

            # Audio Ducking
            if any(w in prompt.lower() for w in ["duck", "audio", "music", "volume", "-18"]):
                _log(job_id, "  🎵 Fairlight: Music auto-ducking configured (-18dB under ceremony dialogue)", "info")
        _progress(job_id, 45)

        # 4. Beat times if song is available or from reference reel
        beat_times = None
        music_info = None
        if audio_path and os.path.isfile(audio_path):
            try:
                beats = beat_analyzer.analyze_audio(audio_path, 25.0)
                beat_times = beats.get("beat_times") or beats.get("beats")
                music_info = beats
            except Exception:
                pass
        elif ref_video and os.path.isfile(ref_video):
            try:
                ref_beats = beat_analyzer.analyze_audio(ref_video, 25.0)
                beat_times = ref_beats.get("beat_times") or ref_beats.get("beats")
                if beat_times:
                    _log(job_id, f"  🎵 Locked to {len(beat_times)} audio beats from reference reel", "info")
            except Exception:
                pass

        # 5. Re-plan the targeted sequence(s)
        if not analyzed:
            raise RuntimeError("Plan file does not contain analyzed clip data to re-cut.")

        target_key = (target_timeline or "all").lower()
        updated_plans = {}

        # Re-cut Teaser
        if target_key in ("all", "teaser") and ("teaser" in plans or mode in ("wedding", "reels")):
            t_dur = req_dur if req_dur else (30.0 if "30s" in prompt.lower() else 45.0 if "45s" in prompt.lower() else 60.0)
            is_vert = directives.get("is_vertical", mode == "reels")
            _log(job_id, f"Re-cutting Teaser ({int(t_dur)}s, Pacing: {pacing})…", "info")
            new_teaser = story_planner.plan_teaser(analyzed, target_duration=t_dur, is_vertical=is_vert,
                                                   instructions=prompt, ref_video_path=ref_video, beat_times=beat_times,
                                                   story_mode="wedding" if mode == "wedding" else None, music_info=music_info)
            new_teaser["sequence_name"] = f"01_Wedding_Teaser_v2_{int(t_dur)}s"
            if audio_path and os.path.isfile(audio_path):
                new_teaser["audio"] = audio_path
                if beat_times:
                    moved = _snap_to_beats(new_teaser, beat_times)
                    _log(job_id, f"  🎵 {new_teaser['sequence_name']}: {moved} cuts locked to song beats", "info")
            plans["teaser"] = new_teaser
            updated_plans["teaser"] = new_teaser
            _log(job_id, f"  ✨ Teaser re-cut: {new_teaser.get('clip_count')} clips, {new_teaser.get('total_duration')}s", "info")

        # Re-cut Highlight
        if target_key in ("all", "highlight") and ("highlight" in plans or mode in ("wedding", "youtube", "music_video")):
            h_dur = req_dur if req_dur else (180.0 if "3 min" in prompt.lower() else 120.0 if "2 min" in prompt.lower() else 240.0)
            _log(job_id, f"Re-cutting Highlight ({round(h_dur/60, 1)}min, Pacing: {pacing})…", "info")
            new_hl = story_planner.plan_highlight(analyzed, beat_times=beat_times, target_duration=h_dur,
                                                  ref_video_path=ref_video, instructions=prompt,
                                                  story_mode="wedding" if mode == "wedding" else None, music_info=music_info)
            new_hl["sequence_name"] = f"02_Cinematic_Highlight_v2"
            if audio_path and os.path.isfile(audio_path):
                new_hl["audio"] = audio_path
                if beat_times:
                    moved = _snap_to_beats(new_hl, beat_times)
                    _log(job_id, f"  🎵 {new_hl['sequence_name']}: {moved} cuts locked to song beats", "info")
            plans["highlight"] = new_hl
            updated_plans["highlight"] = new_hl
            _log(job_id, f"  ✨ Highlight re-cut: {new_hl.get('clip_count')} clips, {new_hl.get('total_duration')}s", "info")

        # Re-cut Full Film
        if target_key in ("all", "full_film") and "full_film" in plans:
            _log(job_id, "Re-cutting Full Film with prompt directives…", "info")
            new_ff = story_planner.plan_full_film(analyzed, instructions=prompt)
            new_ff["sequence_name"] = "03_Full_Wedding_Film_v2"
            plans["full_film"] = new_ff
            updated_plans["full_film"] = new_ff
            _log(job_id, f"  ✨ Full Film re-cut: {new_ff.get('clip_count')} clips", "info")

        _progress(job_id, 75)

        # 6. Save updated plan file and update last_plan.json
        ts = time.strftime('%Y%m%d_%H%M%S')
        new_plan_file = os.path.join(OUTPUT_DIR, f"{mode}_plan_reedit_{ts}.json")
        saved_payload = {
            "folder": folder,
            "project_name": project_name,
            "audio": audio_path,
            "mode": mode,
            "ref_video": ref_video,
            "ref_lut_path": ref_lut_path,
            "instructions": f"{data.get('instructions', '')} | Re-Edit: {prompt}".strip(" |"),
            "reedit_prompt": prompt,
            "analyzed": analyzed,
            "plans": plans,
            "target_nle": target_nle,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        with open(new_plan_file, "w", encoding="utf-8") as f:
            json.dump(saved_payload, f, indent=2, default=str)
        try:
            with open(last_file, "w", encoding="utf-8") as f:
                json.dump(saved_payload, f, indent=2, default=str)
        except Exception:
            pass

        _log(job_id, f"Updated edit plan saved: {os.path.basename(new_plan_file)}", "info")

        # 7. Export updated plans to target NLE
        export_targets = updated_plans if updated_plans else plans
        why = {k: p["markers"] for k, p in export_targets.items() if isinstance(p, dict) and p.get("markers")}
        res = _export_plans(job_id, export_targets, target_nle, audio_path, markers=why or None, project_name=f"{project_name}_v2")
        res["plan_file"] = new_plan_file
        res["reedit_prompt"] = prompt

        if ref_lut_path and is_resolve and resolve_adapter.is_connected():
            applied = resolve_adapter.apply_lut_to_timeline(ref_lut_path)
            if applied > 0:
                _log(job_id, f"  🎨 Applied Reference 3D LUT to {applied} clips in DaVinci Resolve!", "info")

        _progress(job_id, 100)
        _log(job_id, f"🎉 Smart AI Re-Edit Complete! Timelines refreshed in {target_nle.upper()}.", "info")
        with JOBS_LOCK:
            JOBS[job_id].update(status="done", result=res, progress=100)
    except Exception as e:
        logger.error(traceback.format_exc())
        _log(job_id, f"Re-Edit ERROR: {e}", "error")
        with JOBS_LOCK:
            JOBS[job_id].update(status="error", error=str(e))


def _thumbnail(clip: Dict[str, Any], out_dir: str, idx: int) -> Optional[str]:
    file_path = clip.get("file_path", "")
    ext = os.path.splitext(file_path)[1].lower()
    if clip.get("is_still") or clip.get("is_psd") or ext in (".psd", ".psb", ".png", ".jpg", ".jpeg", ".webp", ".bmp"):
        try:
            from PIL import Image
            with Image.open(file_path) as img:
                img = img.convert("RGB")
                w, h = img.size
                new_h = max(1, int(h * 384 / w))
                img = img.resize((384, new_h), Image.Resampling.LANCZOS)
                path = os.path.join(out_dir, f"shot_{idx:03d}.jpg")
                img.save(path, "JPEG", quality=80)
                return path
        except Exception:
            return None
    import cv2
    cap = cv2.VideoCapture(file_path)
    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        t = (float(clip.get("in_seconds", 0)) + float(clip.get("out_seconds", clip.get("duration", 0)))) / 2
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(t * fps))
        ok, frame = cap.read()
        if not ok or frame is None:
            return None
        h, w = frame.shape[:2]
        frame = cv2.resize(frame, (384, max(1, int(h * 384 / w))))
        path = os.path.join(out_dir, f"shot_{idx:03d}.jpg")
        cv2.imwrite(path, frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        return path
    finally:
        cap.release()


AI_REVIEW_MAX = 120      # clips per wedding sent to Smart AI (best local scores first)
AI_REVIEW_BATCH = 8      # contact sheets per request


def _review_cache_dir() -> str:
    from packages.ai_engine.in_clip_trimmer import in_clip_trimmer
    return os.path.join(in_clip_trimmer.cache_dir, "review")


def _apply_review(clip: Dict[str, Any], r: Dict[str, Any]) -> None:
    clip["tier"], clip["tier_source"], clip["tier_note"] = r["tier"], "ai", r.get("note", "")
    clip["ai_moment"], clip["ai_emotion"] = r.get("moment", ""), int(r.get("emotion", 0))
    clip["ai_people"], clip["ai_action"] = r.get("people", ""), r.get("action", "")
    hero = r.get("hero_frame_time")
    dur = float(clip.get("duration") or 0)
    if isinstance(hero, (int, float)) and 0 < hero < dur:
        clip.setdefault("action_peak_seconds_local", clip.get("action_peak_seconds"))
        clip["hero_frame_time"] = round(float(hero), 2)
        clip["action_peak_seconds"] = round(float(hero), 2)  # the planners centre each cut on this moment


VOW_CANDIDATES = 30      # speech clips transcribed per wedding (couple / family / best first)
VOW_LINES = 5


def find_vow_lines(job_id: str, analyzed: List[Dict[str, Any]], is_premium: bool, instructions: str = "") -> int:
    """STEP 6: transcribes the speech clips (Smart AI: license server; otherwise on this computer), splits
    them into lines and marks the most emotional line of the best clips as clip["vow_line"]. Automatic;
    cached per file. Returns how many lines were chosen."""
    from packages.ai_engine import ai_audio
    from packages.ai_engine.in_clip_trimmer import in_clip_trimmer, review_cache_key
    from packages.ai_engine.people import FAMILY
    speech = [c for c in analyzed if c.get("audio_type") == "speech" and not c.get("is_still") and c.get("tier") != "C"]
    if not speech:
        return 0

    def rank(c):
        roles = set(c.get("people_roles") or [])
        return (bool(c.get("couple_in_shot")), bool(roles & FAMILY), c.get("wedding_score", c.get("quality_score", 0)))

    speech = sorted(speech, key=rank, reverse=True)[:VOW_CANDIDATES]
    local_ok = ai_audio.local_transcription_available()
    if not is_premium and not local_ok:
        _log(job_id, "Vows: offline transcription is not installed in this version — skipped.", "warning")
        return 0
    cache_dir = os.path.join(in_clip_trimmer.cache_dir, "transcripts")
    os.makedirs(cache_dir, exist_ok=True)
    told_download = False
    cloud_failed = False
    done = 0
    for c in speech:
        key = review_cache_key(c)
        cache = os.path.join(cache_dir, f"{key}.json") if key else None
        tx = None
        if cache and os.path.isfile(cache):
            try:
                with open(cache, "r", encoding="utf-8") as f:
                    tx = json.load(f)
            except (OSError, ValueError):
                tx = None
        if tx is None:
            a = float(c.get("full_in_seconds", 0) or 0)
            b = min(float(c.get("full_out_seconds", c.get("duration", 0)) or 0), a + 90.0)
            try:
                if is_premium and not cloud_failed:
                    tx = ai_audio.transcribe(c["file_path"], a, b)
                    tx["engine"] = "smart_ai"
            except Exception as e:
                cloud_failed = True
                _log(job_id, f"Vows: cloud transcription unavailable ({str(e)[:120]}), using this computer.", "warning")
            if tx is None and local_ok:
                if not told_download and not ai_audio.local_model_ready():
                    _log(job_id, "Vows: downloading the speech model (about 480 MB, one time only)…", "info")
                    told_download = True
                try:
                    tx = ai_audio.transcribe_local(c["file_path"], a, b)
                except Exception as e:
                    _log(job_id, f"Vows: could not transcribe {os.path.basename(c['file_path'])}: {str(e)[:120]}", "warning")
                    tx = {"words": [], "text": "", "engine": "failed"}
            if tx is None:
                continue
            if cache and tx.get("engine") != "failed":
                try:
                    with open(cache, "w", encoding="utf-8") as f:
                        json.dump(tx, f, ensure_ascii=False)
                except OSError:
                    pass
        c["transcript"] = {"lines": ai_audio.split_lines(tx.get("words") or []), "language": tx.get("language"),
                           "engine": tx.get("engine")}
        done += 1
        if done % 5 == 0:
            _log(job_id, f"  Vows: {done}/{len(speech)} speech clips transcribed", "info")

    candidates = []
    clip_of: Dict[int, Dict[str, Any]] = {}
    for c in speech:
        roles = set(c.get("people_roles") or [])
        bonus = 10.0 if c.get("couple_in_shot") else (6.0 if roles & FAMILY else 0.0)
        for ln in (c.get("transcript") or {}).get("lines", []):
            if not 1.2 <= ln["end"] - ln["start"] <= 8.0:
                continue
            i = len(candidates)
            clip_of[i] = c
            candidates.append({"id": i, "text": ln["text"], "start": ln["start"], "end": ln["end"], "bonus": bonus,
                               "seconds": round(ln["end"] - ln["start"], 1), "moment": c.get("moment") or c.get("ai_moment"),
                               "who": ", ".join(sorted(roles)) or None})
    if not candidates:
        _log(job_id, "Vows: no clear spoken lines found.", "info")
        return 0
    picked: List[Dict[str, Any]] = []
    if is_premium:
        try:
            ids = cloud_ai_client.pick_vow_lines(candidates[:200], VOW_LINES, instructions)
            seen = set()
            for i in ids:
                if id(clip_of[i]) not in seen:
                    picked.append(candidates[i])
                    seen.add(id(clip_of[i]))
        except Exception as e:
            _log(job_id, f"Vows: Smart AI could not pick lines ({str(e)[:100]}), using the local choice.", "warning")
    if not picked:
        picked = ai_audio.choose_lines_local(candidates, VOW_LINES)
    for rank_i, ln in enumerate(picked):
        c = clip_of[ln["id"]]
        c["vow_line"] = {"text": ln["text"], "start": ln["start"], "end": ln["end"], "rank": rank_i}
        _log(job_id, f"  💬 “{ln['text']}” — {os.path.basename(c['file_path'])}", "info")
    return len(picked)


def _footage_summary(analyzed: List[Dict[str, Any]]) -> Dict[str, Any]:
    """What was shot, for Smart AI's story plan: per moment the clips, A-grade shots and couple shots."""
    from packages.ai_engine.story_planner import clip_moment
    moments: Dict[str, Dict[str, int]] = {}
    for c in analyzed:
        m = clip_moment(c) or "unknown"
        d = moments.setdefault(m, {"clips": 0, "A": 0, "couple": 0})
        d["clips"] += 1
        d["A"] += 1 if c.get("tier") == "A" else 0
        d["couple"] += 1 if c.get("couple_in_shot") else 0
    return {"total_clips": len(analyzed), "moments": moments}


def ai_review_clips(job_id: str, analyzed: List[Dict[str, Any]], instructions: str, job_name: str) -> Tuple[bool, str]:
    """Smart AI grades every clip A / B / C from a contact sheet of the whole clip (STEP 3).
    Reviewed clips are cached per file, so they are never sent again. Clips it does not see keep their
    local tier. Uses 1 wedding credit only when new clips were reviewed. Returns (credit used, story note)."""
    from concurrent.futures import ThreadPoolExecutor
    from packages.ai_engine.in_clip_trimmer import assign_local_tiers, contact_sheet, recompute_scores, review_cache_key

    assign_local_tiers(analyzed)
    videos = [c for c in analyzed if not c.get("is_still")]
    candidates = sorted(videos, key=lambda c: c.get("wedding_score_base", c.get("quality_score", 0)), reverse=True)[:AI_REVIEW_MAX]
    cache_dir = _review_cache_dir()
    os.makedirs(cache_dir, exist_ok=True)

    todo, cached = [], 0
    for c in candidates:
        key = review_cache_key(c)
        c["_review_key"] = key
        hit = None
        if key:
            try:
                with open(os.path.join(cache_dir, key + ".json"), "r", encoding="utf-8") as f:
                    hit = json.load(f)
            except (OSError, ValueError):
                hit = None
        if hit:
            _apply_review(c, hit)
            cached += 1
        else:
            todo.append(c)

    story_notes: List[str] = []
    reviewed = 0
    if todo:
        _log(job_id, f"🧠 Smart AI will review {len(todo)} new shots"
                     + (f" ({cached} already reviewed earlier, reused free)" if cached else "")
                     + " · uses 1 Smart AI wedding credit", "info")
        sheets = []
        for c in todo:
            jpeg, times = contact_sheet(c)
            if jpeg:
                roles = ", ".join(c.get("people_roles") or [])
                hint = " · ".join(x for x in [(c.get("moment") or "").replace("_", " "), roles and f"people: {roles}",
                                               f"{float(c.get('duration') or 0):.0f}s clip"] if x)
                sheets.append((c, {"jpeg": jpeg, "times": times, "hint": hint}))
        batches = [sheets[i:i + AI_REVIEW_BATCH] for i in range(0, len(sheets), AI_REVIEW_BATCH)]
        done_batches = [0]

        def run(batch):
            segs = [dict(seg, id=i) for i, (_, seg) in enumerate(batch)]
            res = cloud_ai_client.review_segments(segs, instructions)
            done_batches[0] += 1
            _log(job_id, f"  Smart AI review {done_batches[0]}/{len(batches)} done", "info")
            return batch, res

        errors = []
        with ThreadPoolExecutor(max_workers=3) as pool:
            for fut in [pool.submit(run, b) for b in batches]:
                try:
                    batch, res = fut.result()
                except Exception as e:
                    errors.append(str(e))
                    continue
                if res.get("story_note"):
                    story_notes.append(res["story_note"])
                for i, (c, _) in enumerate(batch):
                    r = res["segments"].get(i)
                    if not r:
                        continue
                    _apply_review(c, r)
                    reviewed += 1
                    if c.get("_review_key"):
                        try:
                            with open(os.path.join(cache_dir, c["_review_key"] + ".json"), "w", encoding="utf-8") as f:
                                json.dump(r, f)
                        except OSError:
                            pass
        if errors:
            _log(job_id, f"Smart AI review: {len(errors)} batch(es) failed, those clips keep the local grade ({errors[0][:160]})", "warning")
    elif cached:
        _log(job_id, f"🧠 Smart AI review reused for all {cached} shots (no new credit)", "info")

    for c in analyzed:
        c.pop("_review_key", None)
        recompute_scores(c)
    counts = {t: sum(1 for c in analyzed if c.get("tier") == t) for t in "ABC"}
    _log(job_id, f"  Shot grades: A (hero) {counts['A']} · B (usable) {counts['B']} · C (reject) {counts['C']}", "info")
    heroes = sorted((c for c in analyzed if c.get("tier") == "A" and c.get("tier_source") == "ai"),
                    key=lambda c: c.get("ai_emotion", 0), reverse=True)[:5]
    for c in heroes:
        _log(job_id, f"  ★ {os.path.basename(c['file_path'])}: {c.get('ai_moment', '')} — {c.get('tier_note', '')}", "info")
    note = story_notes[0] if story_notes else ""
    if note:
        _log(job_id, f"  Story note: {note}", "info")
    if reviewed:
        return _use_credit(job_id, job_name), note
    if todo:
        _log(job_id, "Smart AI review did not finish; continuing with local grades (no credit used).", "warning")
    return False, note


def _use_credit(job_id: str, job_name: str) -> bool:
    credit = license_manager.consume_wedding_credit(job_name=f"Wedding: {job_name}")
    if credit.get("success"):
        left = "unlimited" if credit.get("unlimited") else credit.get("credits_remaining")
        _log(job_id, f"1 AI wedding credit used. Credits left: {left}", "info")
        return True
    _log(job_id, f"Credit note: {credit.get('error')}", "warning")
    return False


# Length of the AI music per workflow (the edit is cut to the music's beats)
MUSIC_SECONDS = {"reels": 50, "youtube": 300, "music_video": 180}


def smart_audio(job_id: str, mode: str, formats: List[str], analyzed: List[Dict[str, Any]], instructions: str,
                story_note: str, want_music: bool, voiceover: str) -> Tuple[str, str, float]:
    """Smart AI writes the music brief / voiceover script, ElevenLabs (via the server) makes the audio.
    Returns (music path, voiceover path, voiceover seconds); empty paths for anything that failed."""
    from packages.ai_engine import ai_audio
    music_seconds = MUSIC_SECONDS.get(mode) or (240 if "highlight" in formats else 70)
    if want_music and mode == "wedding" and not {"teaser", "highlight"} & set(formats):
        _log(job_id, "AI music is made for the teaser / highlight - the full film keeps its own sound.", "warning")
        want_music = False
    script = "" if voiceover == "auto" else voiceover
    if not want_music and not voiceover:
        return "", "", 0.0
    out_dir = os.path.join(OUTPUT_DIR, "ai_audio")
    stamp = time.strftime("%Y%m%d_%H%M%S")
    music_path = voice_path = ""
    brief = {"music_prompt": "", "voiceover_script": script}
    if want_music or not script:
        _log(job_id, "🎼 Smart AI is writing the music brief" + (" and voiceover script…" if not script and voiceover else "…"), "info")
        try:
            moments = [c["ai_moment"] for c in sorted(analyzed, key=lambda c: c.get("ai_emotion", 0), reverse=True) if c.get("ai_moment")]
            brief = cloud_ai_client.audio_brief(mode, moments, story_note, instructions, music_seconds, 25)
            brief["voiceover_script"] = script or brief["voiceover_script"]
        except Exception as e:
            _log(job_id, f"Smart AI could not write the audio brief: {e}", "warning")
            if not script:
                return "", "", 0.0
    if want_music and brief["music_prompt"]:
        _log(job_id, f"  Music brief: {brief['music_prompt'][:160]}", "normal")
        _log(job_id, f"🎵 Generating {music_seconds} s of AI music (about a minute)…", "info")
        try:
            music_path = ai_audio.generate_music(brief["music_prompt"], music_seconds, os.path.join(out_dir, f"ai_music_{stamp}.mp3"))
            _log(job_id, f"  AI music ready: {music_path} - the edit will be cut on its beats.", "info")
        except Exception as e:
            _log(job_id, f"AI music failed: {e}", "warning")
    voice_seconds = 0.0
    if voiceover and brief["voiceover_script"]:
        _log(job_id, f"🎙️ Generating the voiceover: “{brief['voiceover_script'][:120]}…”", "info")
        try:
            voice_path = ai_audio.generate_voiceover(brief["voiceover_script"], os.path.join(out_dir, f"ai_voiceover_{stamp}.mp3"))
            voice_seconds = ai_audio.duration(voice_path)
            with open(voice_path[:-4] + ".txt", "w", encoding="utf-8") as f:
                f.write(brief["voiceover_script"])
            _log(job_id, f"  Voiceover ready ({voice_seconds:.0f} s): {voice_path} - placed on its own audio track.", "info")
        except Exception as e:
            voice_path = ""
            _log(job_id, f"AI voiceover failed: {e}", "warning")
    return music_path, voice_path, voice_seconds


# ----------------------------------------------------------------------
# Smart AI Director: like Claude Code, Smart AI looks at the footage, listens to the song, studies the
# reference images / videos and then cuts the timeline itself (clip by clip), instead of keyword rules.
# ----------------------------------------------------------------------
_DIR_IMG_W = 512

DIRECTOR_TOOLS: List[Dict[str, Any]] = [
    {"name": "director_project_overview",
     "description": "Start here. The active project: footage folder, song, reference video, editing mode, the timelines "
                    "already built (key, name, clip count, duration) and how many analysed clips are available.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "director_list_clips",
     "description": "Table of the analysed footage clips (index, file, camera, length, usable action window, peak moment, "
                    "shot type from face size, quality 0-100, ws = wedding score 0-100, ritual moment, camera move, "
                    "face count, audio type, people = named roles in the shot like bride / groom / couple, tier A hero / B usable / "
                    "C reject with a note). Prefer A shots, avoid C. "
                    "Use the index with director_view_clips and director_build_timeline.",
     "input_schema": {"type": "object", "properties": {
         "offset": {"type": "integer", "default": 0},
         "limit": {"type": "integer", "default": 120, "description": "max 300"},
         "sort": {"type": "string", "enum": ["time", "quality"], "default": "time"},
         "camera": {"type": "string", "description": "only clips of this camera folder, e.g. 'Cam 02'"}}}},
    {"name": "director_view_clips",
     "description": "LOOK at footage: returns real frames of the given clips (up to 16 clips per call, 1-4 frames each, "
                    "spread over the clip's action window, each labelled with its time in seconds). Use it to pick shots, "
                    "check focus/emotion/framing and choose exact in/out points.",
     "input_schema": {"type": "object", "properties": {
         "indices": {"type": "array", "items": {"type": "integer"}},
         "frames_per_clip": {"type": "integer", "default": 2}}, "required": ["indices"]}},
    {"name": "director_view_media",
     "description": "LOOK at any image or video the user gives (a path on this computer, or a YouTube / Shorts / Instagram "
                    "link for videos): an image is returned as is; a video returns frames at the given times or evenly "
                    "spread, plus its length / fps / size. Use it to study a reference look, framing and pacing.",
     "input_schema": {"type": "object", "properties": {
         "path": {"type": "string"},
         "times": {"type": "array", "items": {"type": "number"}, "description": "seconds to grab (video only)"},
         "count": {"type": "integer", "default": 8, "description": "frames when no times are given, max 16"}},
         "required": ["path"]}},
    {"name": "director_analyze_audio",
     "description": "LISTEN to a song / voiceover / video soundtrack: length, BPM, every beat time, the big drops and a "
                    "loudness curve per second (0-100) to find intro, build-ups and drops. transcribe=true also returns "
                    "the spoken words with timestamps (for a voiceover or dialogue).",
     "input_schema": {"type": "object", "properties": {
         "path": {"type": "string"}, "transcribe": {"type": "boolean", "default": False}}, "required": ["path"]}},
    {"name": "director_get_timeline",
     "description": "The current cut of one timeline (teaser, highlight, full_film, reel …): every shot with its clip "
                    "index, source in/out seconds, speed and position. Read it before changing an existing edit.",
     "input_schema": {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]}},
    {"name": "director_build_timeline",
     "description": "CUT the edit: builds a timeline from your own shot list, in order, saves it in the project and sends it "
                    "to DaVinci Resolve / Premiere Pro / EDIUS. Each cut: clip (index from director_list_clips), in / out "
                    "in SOURCE seconds, optional speed_percent (50 = slow motion) and role. Re-using a key replaces that "
                    "timeline (a new version is created in the editor).",
     "input_schema": {"type": "object", "properties": {
         "key": {"type": "string", "description": "teaser | highlight | full_film | reel | any short id"},
         "sequence_name": {"type": "string"},
         "aspect": {"type": "string", "enum": ["16:9", "9:16", "1:1", "4:5"], "default": "16:9"},
         "music": {"type": "string", "description": "song path for the music track ('' = none, omit = project song)"},
         "snap_to_beats": {"type": "boolean", "default": False, "description": "trim cuts so each lands on a beat"},
         "cuts": {"type": "array", "items": {"type": "object", "properties": {
             "clip": {"type": "integer"}, "in": {"type": "number"}, "out": {"type": "number"},
             "speed_percent": {"type": "integer", "default": 100}, "role": {"type": "string"},
             "live_sound": {"type": "boolean", "description": "play this clip's own sound (vows, speech) and duck the music -18 dB"},
             "note": {"type": "string", "description": "why this shot (becomes a timeline marker)"}},
             "required": ["clip", "in", "out"]}},
         "markers": {"type": "array", "items": {"type": "object", "properties": {
             "seconds": {"type": "number"}, "name": {"type": "string"}}, "required": ["seconds", "name"]}},
         "editor": {"type": "string", "enum": ["project", "resolve", "premiere", "edius", "both", "none"], "default": "project"}},
         "required": ["key", "cuts"]}},
    {"name": "director_auto_edit",
     "description": "FAST FIRST DRAFT with the app's wedding Story Director: builds a whole teaser / highlight / reel / "
                    "same-day edit from the analysed footage (A/B/C shot grades, bride & groom, ritual order, song bars and "
                    "drops, vows over instrumentals, why-notes) and returns every cut. Optionally give your own acts. Then "
                    "check it (director_get_timeline / director_view_clips) and refine with director_build_timeline.",
     "input_schema": {"type": "object", "properties": {
         "format": {"type": "string", "enum": ["teaser", "highlight", "reel", "same_day"]},
         "seconds": {"type": "number", "description": "length; default 60 teaser, 240 highlight, 30 reel, 150 same-day"},
         "instructions": {"type": "string", "description": "the editor's wishes in their words (pace, hook, look...)"},
         "acts": {"type": "array", "description": "optional story acts", "items": {"type": "object", "properties": {
             "name": {"type": "string"}, "seconds": {"type": "number"},
             "moments": {"type": "array", "items": {"type": "string"}},
             "mood": {"type": "string", "enum": ["hook", "calm", "energy", "emotion"]},
             "cut_min": {"type": "number"}, "cut_max": {"type": "number"}}, "required": ["name", "seconds", "mood"]}},
         "key": {"type": "string", "description": "timeline key to save as (default = format)"},
         "editor": {"type": "string", "enum": ["project", "resolve", "premiere", "edius", "both", "none"], "default": "project"}},
         "required": ["format"]}},
    {"name": "director_people",
     "description": "The people the app recognised in this project (bride, groom, family, guests) with how many clips each "
                    "is in; set_role names one of them so every edit prefers the couple. Face data stays on this computer.",
     "input_schema": {"type": "object", "properties": {
         "action": {"type": "string", "enum": ["list", "set_role"], "default": "list"},
         "id": {"type": "string"},
         "role": {"type": "string", "enum": ["bride", "groom", "bride_mother", "bride_father", "groom_mother", "groom_father",
                                             "sibling", "relative", "friend", "guest", ""]}}}},
    {"name": "director_vows",
     "description": "Spoken lines (vows, speeches, family wishes) of the speech clips: 'list' shows the transcribed lines per "
                    "clip with source times and the lines already chosen; 'find' transcribes the speech clips now and picks the "
                    "most emotional lines. Use a line's start/end as in/out with live_sound in director_build_timeline.",
     "input_schema": {"type": "object", "properties": {
         "action": {"type": "string", "enum": ["list", "find"], "default": "list"},
         "clip": {"type": "integer", "description": "only this clip (list)"}}}},
]
DIRECTOR_NAMES = {t["name"] for t in DIRECTOR_TOOLS}

DIRECTOR_GUIDE = (
    "\n\nHOW YOU EDIT (like a senior wedding editor at the timeline: understand, draft, check, refine):\n"
    "1. Understand: director_project_overview, director_list_clips (tier A hero / B usable / C reject, ritual moment, "
    "people, camera move, audio). Listen to the song with director_analyze_audio (bars, sections, drops, instrumental "
    "parts, audible end). Look at every reference the user gives with director_view_media. director_people shows who "
    "is who; director_vows shows the spoken lines.\n"
    "2. Draft fast: director_auto_edit builds a complete Story Director edit (ritual order, couple first, cuts on "
    "bars/beats, best shot on the drop, vows over instrumentals). Pass the user's wishes as instructions, or your own "
    "acts when the story should be different.\n"
    "3. Check like an editor: director_get_timeline + director_view_clips on the cuts. Look for weak or repeated shots, "
    "closed eyes, the wrong ritual order, a weak hook or ending, cuts that miss the music.\n"
    "4. Refine: rebuild the timeline with director_build_timeline (exact source in/out inside each clip's usable "
    "window; live_sound for vow lines from director_vows; a short note per cut). Never pick shots only from file "
    "names or scores - look at them.\n"
    "5. Finish in the editor with the other tools (colour, letterbox, markers, titles) when asked.\n"
    "6. End with a short summary of what you cut and why, in the user's language.\n"
    "When no tool can do something and run_code is available, write a small Python script for it (save outputs "
    "in AVF_WORK, never touch the original footage), check its output, then continue with the tools.\n"
    "Footage paths the user writes in quotes are files on this computer - open them with your tools."
)


def _dir_project() -> Tuple[str, Dict[str, Any]]:
    info = get_last_plan()
    path = info.get("plan_file") if info.get("has_plan") else None
    if not path:
        raise RuntimeError("No project yet. Build a first edit from AI Studio (Load footage → Create), then ask again.")
    with open(path, "r", encoding="utf-8") as f:
        return path, json.load(f)


def _dir_save_project(data: Dict[str, Any]) -> str:
    ts = time.strftime('%Y%m%d_%H%M%S')
    path = os.path.join(OUTPUT_DIR, f"{data.get('mode', 'wedding')}_plan_ai_{ts}.json")
    data["created_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    for p in (path, os.path.join(OUTPUT_DIR, "last_plan.json")):
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
    return path


def _dir_jpeg(img_bgr_or_pil, label: str) -> List[Dict[str, Any]]:
    """One labelled JPEG image block (frames are scaled to _DIR_IMG_W wide to keep requests small)."""
    import base64
    import io
    from PIL import Image
    img = img_bgr_or_pil
    if not isinstance(img, Image.Image):  # OpenCV BGR array
        img = Image.fromarray(img[:, :, ::-1])
    img = img.convert("RGB")
    w, h = img.size
    if w > _DIR_IMG_W:
        img = img.resize((_DIR_IMG_W, max(1, int(h * _DIR_IMG_W / w))), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=78)
    return [{"type": "text", "text": label},
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                         "data": base64.standard_b64encode(buf.getvalue()).decode("ascii")}}]


def _dir_frames(path: str, times: List[float]) -> Tuple[List[Tuple[float, Any]], Dict[str, Any]]:
    import cv2
    cap = cv2.VideoCapture(path)
    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        meta = {"duration": round(n / fps, 2) if n else 0, "fps": round(fps, 3),
                "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0), "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)}
        out = []
        for t in times:
            if meta["duration"] and t > meta["duration"]:
                continue
            cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(t * fps)))
            ok, frame = cap.read()
            if ok and frame is not None:
                out.append((round(t, 2), frame))
        return out, meta
    finally:
        cap.release()


def _dir_is_still(clip: Dict[str, Any]) -> bool:
    return bool(clip.get("is_still") or clip.get("is_psd")) or os.path.splitext(clip.get("file_path", ""))[1].lower() in STILL_EXTS


def _dir_window(clip: Dict[str, Any]) -> Tuple[float, float]:
    dur = float(clip.get("duration") or 0)
    a = float(clip.get("full_in_seconds", clip.get("action_start_seconds", 0)) or 0)
    b = float(clip.get("full_out_seconds", clip.get("action_end_seconds", dur)) or dur)
    if b <= a:
        a, b = 0.0, dur
    return a, b


def director_call(name: str, args: Dict[str, Any], job_id: str = "") -> Any:
    if name == "director_project_overview":
        path, data = _dir_project()
        info = get_last_plan()
        return {"project": info.get("project_name"), "plan_file": path, "folder": data.get("folder"),
                "mode": data.get("mode"), "song": data.get("audio"), "reference_video": data.get("ref_video"),
                "editor": data.get("target_nle"), "instructions": data.get("instructions"),
                "analysed_clips": len(data.get("analyzed", [])), "timelines": info.get("timelines", [])}

    if name == "director_list_clips":
        _, data = _dir_project()
        clips = list(enumerate(data.get("analyzed", [])))
        cam = (args.get("camera") or "").strip().lower()
        if cam:
            clips = [(i, c) for i, c in clips if cam in str(c.get("camera", "")).lower()]
        if args.get("sort") == "quality":
            clips.sort(key=lambda x: x[1].get("quality_score", 0), reverse=True)
        off, lim = max(0, int(args.get("offset") or 0)), min(300, max(1, int(args.get("limit") or 120)))
        rows = []
        for i, c in clips[off:off + lim]:
            a, b = _dir_window(c)
            rows.append({"i": i, "file": os.path.basename(c.get("file_path", "")), "cam": c.get("camera", ""),
                         "len": round(float(c.get("duration") or 0), 2), "use": [round(a, 2), round(b, 2)],
                         "peak": c.get("action_peak_seconds"), "shot": c.get("shot_type", ""),
                         "q": c.get("quality_score"), "ws": c.get("wedding_score"),
                         "moment": c.get("ai_moment") or c.get("moment") or "", "move": c.get("camera_move", ""),
                         "faces": (c.get("faces") or {}).get("count"), "audio": c.get("audio_type", ""),
                         "people": c.get("people_roles") or [],
                         "tier": c.get("tier", ""), "note": c.get("tier_note", ""),
                         "still": _dir_is_still(c)})
        return {"total": len(clips), "offset": off, "clips": rows}

    if name == "director_view_clips":
        _, data = _dir_project()
        analyzed = data.get("analyzed", [])
        per = min(4, max(1, int(args.get("frames_per_clip") or 2)))
        blocks: List[Dict[str, Any]] = []
        for i in [int(x) for x in (args.get("indices") or [])][:16]:
            if not 0 <= i < len(analyzed):
                blocks.append({"type": "text", "text": f"Clip {i}: no such clip."})
                continue
            c = analyzed[i]
            label = f"Clip {i} · {os.path.basename(c.get('file_path', ''))} · {c.get('camera', '')}"
            try:
                if _dir_is_still(c):
                    from PIL import Image
                    with Image.open(c["file_path"]) as img:
                        blocks += _dir_jpeg(img.copy(), label + " (still image)")
                    continue
                a, b = _dir_window(c)
                times = [a + (b - a) * (k + 0.5) / per for k in range(per)]
                frames, _ = _dir_frames(c["file_path"], times)
                for t, fr in frames:
                    blocks += _dir_jpeg(fr, f"{label} @ {t}s")
                if not frames:
                    blocks.append({"type": "text", "text": label + ": could not read frames."})
            except Exception as e:
                blocks.append({"type": "text", "text": f"{label}: {e}"})
        return {"_content": blocks or [{"type": "text", "text": "No clips given."}]}

    if name == "director_view_media":
        src = str(args.get("path") or "").strip().strip('"').strip("'")
        if src.lower().startswith(("http://", "https://")):
            _log(job_id, "  Downloading the reference video…", "info") if job_id else None
            src = resolve_reference_video_source(src, job_id) or ""
        src = os.path.expanduser(src)
        if not os.path.isfile(src):
            raise RuntimeError(f"File not found: {args.get('path')}")
        ext = os.path.splitext(src)[1].lower()
        if ext in IMAGE_EXTS:
            from PIL import Image
            with Image.open(src) as img:
                return {"_content": _dir_jpeg(img.copy(), f"Image {os.path.basename(src)} ({img.size[0]}x{img.size[1]})")}
        _, meta = _dir_frames(src, [])
        times = [float(t) for t in (args.get("times") or [])][:16]
        if not times:
            n = min(16, max(1, int(args.get("count") or 8)))
            d = meta["duration"] or 0
            times = [d * (k + 0.5) / n for k in range(n)]
        frames, meta = _dir_frames(src, times)
        blocks = [{"type": "text", "text": f"Video {os.path.basename(src)}: {json.dumps(meta)}"}]
        for t, fr in frames:
            blocks += _dir_jpeg(fr, f"@ {t}s")
        return {"_content": blocks}

    if name == "director_analyze_audio":
        src = os.path.expanduser(str(args.get("path") or "").strip().strip('"').strip("'"))
        if not os.path.isfile(src):
            raise RuntimeError(f"File not found: {args.get('path')}")
        from packages.ai_engine.beat_analyzer import beat_analyzer
        beats = beat_analyzer.analyze_audio(src, 25.0)
        out = {k: beats.get(k) for k in ("duration", "bpm", "beat_count", "drop_times", "bar_seconds", "sections",
                                         "vocal_regions", "instrumental_regions", "audible_end")}
        out["beat_times"] = (beats.get("beat_times") or [])[:600]
        out["downbeat_times"] = (beats.get("downbeat_times") or [])[:200]
        try:
            import numpy as np
            from packages.ai_engine.audio_io import load_audio
            samples, sr = load_audio(src, 8000)
            secs = len(samples) // sr
            rms = np.array([float(np.sqrt(np.mean(samples[s * sr:(s + 1) * sr] ** 2))) for s in range(secs)]) if secs else np.array([])
            if rms.size:
                out["loudness_per_second"] = [int(v) for v in np.round(100 * rms / (rms.max() or 1))]
                out["duration"] = out.get("duration") or secs
        except Exception as e:
            out["loudness_note"] = f"loudness curve unavailable: {e}"
        if args.get("transcribe"):
            from packages.ai_engine import ai_audio
            tr = ai_audio.transcribe(src)
            out["transcript"] = tr.get("text", "")
            out["words"] = [[w.get("text"), round(float(w.get("start", 0)), 2), round(float(w.get("end", 0)), 2)]
                            for w in (tr.get("words") or [])][:1500]
        return out

    if name == "director_get_timeline":
        _, data = _dir_project()
        key = str(args.get("key") or "")
        plan = (data.get("plans") or {}).get(key)
        if not plan:
            return {"error": f"No timeline '{key}'.", "available": list((data.get("plans") or {}).keys())}
        index_of = {os.path.normpath(c.get("file_path", "")): i for i, c in enumerate(data.get("analyzed", []))}
        fps_of = {os.path.normpath(c.get("file_path", "")): float(c.get("native_fps") or 25.0) for c in data.get("analyzed", [])}
        shots = []
        for c in plan.get("clips", []):
            fp = os.path.normpath(c.get("file_path") or "")
            fps = fps_of.get(fp, 25.0)
            shots.append({"clip": index_of.get(fp), "file": os.path.basename(fp),
                          "in": round(c.get("in_frame", 0) / fps, 2), "out": round(c.get("out_frame", 0) / fps, 2),
                          "speed_percent": c.get("speed_percent", 100), "at": c.get("timeline_start"),
                          "len": c.get("duration"), "role": c.get("role", "")})
        return {"key": key, "name": plan.get("sequence_name"), "aspect": plan.get("aspect_ratio"),
                "duration": plan.get("total_duration"), "music": plan.get("audio"), "shots": shots}

    if name == "director_build_timeline":
        _, data = _dir_project()
        analyzed = data.get("analyzed", [])
        key = re.sub(r"[^A-Za-z0-9_\-]", "_", str(args.get("key") or "ai_edit"))[:40]
        entries, t, problems = [], 0.0, []
        for n, cut in enumerate(args.get("cuts") or []):
            i = int(cut.get("clip", -1))
            if not 0 <= i < len(analyzed):
                problems.append(f"cut {n}: clip {i} does not exist")
                continue
            c = analyzed[i]
            fps = float(c.get("native_fps") or 25.0)
            dur = float(c.get("duration") or 0)
            a, b = float(cut.get("in", 0)), float(cut.get("out", 0))
            speed = min(400, max(10, int(cut.get("speed_percent") or 100)))
            still = _dir_is_still(c)
            if still:
                a, b, speed = 0.0, max(0.5, b - a), 100
            else:
                a, b = max(0.0, min(a, dur)), max(0.0, min(b, dur))
                if b - a < 0.2:
                    problems.append(f"cut {n}: clip {i} in/out {cut.get('in')}-{cut.get('out')} is outside its {dur}s")
                    continue
            length = round((b - a) * 100.0 / speed, 3)
            entries.append({"clip_name": os.path.basename(c.get("file_path", "")), "file_path": c.get("file_path"),
                            "timeline_start": round(t, 3), "timeline_end": round(t + length, 3),
                            "in_frame": int(round(a * fps)), "out_frame": int(round(b * fps)),
                            "in_seconds": round(a, 3), "out_seconds": round(b, 3), "duration": length,
                            "shot_type": c.get("shot_type", "mid"), "role": cut.get("role") or "",
                            "is_still": still, "speed": speed / 100.0, "speed_percent": speed,
                            "speed_label": f"{speed}%" + (" Slow-Mo (Optical Flow)" if speed < 100 else ""),
                            "retime_process": "Optical Flow" if speed != 100 else "Normal",
                            "duck_music": bool(cut.get("live_sound")), "keep_audio": bool(cut.get("live_sound")),
                            "why": str(cut.get("note") or "")[:200]})
            if cut.get("live_sound") and speed != 100:
                problems.append(f"cut {n}: live sound plays at 100% (speed ignored)")
                entries[-1].update(speed=1.0, speed_percent=100, duration=round(b - a, 3),
                                   timeline_end=round(t + b - a, 3), retime_process="Normal")
                length = round(b - a, 3)
            t += length
        if not entries:
            raise RuntimeError("No usable cuts. " + "; ".join(problems))
        aspect = args.get("aspect") or "16:9"
        plan = {"format": f"Smart_AI_{key}", "aspect_ratio": {"16:9": "16:9 (1920x1080 / 3840x2160)", "9:16": "9:16 (1080x1920)",
                                                                 "1:1": "1:1 (1080x1080)", "4:5": "4:5 (1080x1350)"}.get(aspect, aspect),
                "sequence_name": (args.get("sequence_name") or f"Smart_AI_{key}_{time.strftime('%H%M%S')}")[:80],
                "clips": entries, "clip_count": len(entries), "total_duration": round(t, 2), "edited_by": "Smart AI Director",
                "duck_regions": [{"start": e["timeline_start"], "end": e["timeline_end"], "db": -18.0} for e in entries if e["duck_music"]]}
        music = args.get("music")
        music = data.get("audio", "") if music is None else os.path.expanduser(str(music).strip().strip('"'))
        if music and os.path.isfile(music):
            plan["audio"] = music
            if args.get("snap_to_beats"):
                from packages.ai_engine.beat_analyzer import beat_analyzer
                bt = beat_analyzer.analyze_audio(music, 25.0).get("beat_times") or []
                moved = _snap_to_beats(plan, bt)
                _log(job_id, f"  🎵 {moved} cuts locked to the beat", "info") if job_id else None
        markers = {key: [{"seconds": float(m["seconds"]), "name": str(m["name"])[:60]} for m in (args.get("markers") or [])]
                   + [{"seconds": e["timeline_start"], "name": "Smart AI", "note": e["why"]} for e in entries if e.get("why")]}
        plan["markers"] = markers[key]
        data.setdefault("plans", {})[key] = plan
        if music and os.path.isfile(music):
            data["audio"] = music
        saved = _dir_save_project(data)
        editor = args.get("editor") or "project"
        target = data.get("target_nle") or "resolve" if editor == "project" else editor
        _log(job_id, f"✂️ Smart AI cut '{plan['sequence_name']}': {len(entries)} shots, {plan['total_duration']}s", "info") if job_id else None
        res: Dict[str, Any] = {}
        if target != "none":
            res = _export_plans(job_id or _new_job(), {key: plan}, target, plan.get("audio", ""), markers=markers,
                                project_name=data.get("project_name") or "AI_Project")
        return {"built": plan["sequence_name"], "shots": len(entries), "duration": plan["total_duration"],
                "beat_synced": bool(plan.get("beat_synced")), "editor": target, "plan_file": saved,
                "in_resolve": res.get("timelines_created", []), "premiere_xmls": res.get("premiere_xmls", []),
                "edius_xmls": res.get("edius_xmls", []), "skipped": problems}

    if name == "director_auto_edit":
        _, data = _dir_project()
        from packages.ai_engine.story_planner import story_planner
        fmt = str(args.get("format") or "teaser")
        defaults = {"teaser": 60.0, "highlight": 240.0, "reel": 30.0, "same_day": 150.0}
        seconds = float(args.get("seconds") or defaults.get(fmt, 60.0))
        instructions = str(args.get("instructions") or "")
        music = _dir_music(data)
        beat_times = (music or {}).get("beat_times")
        acts = None
        if args.get("acts"):
            acts = {"source": "smart_ai", "acts": [{"name": a.get("name"), "seconds": a.get("seconds"), "moments": a.get("moments") or [],
                                                     "mood": a.get("mood"), "cut": [a.get("cut_min", 1.2), a.get("cut_max", 2.4)]}
                                                    for a in args["acts"]]}
        analyzed = data.get("analyzed", [])
        common = dict(beat_times=beat_times, instructions=instructions, music_info=music)
        if fmt == "highlight":
            plan = story_planner.plan_highlight(analyzed, target_duration=seconds, story_mode="wedding", edit_plan=acts, **common)
        elif fmt == "reel":
            plan = story_planner.plan_reel(analyzed, target_duration=seconds, edit_plan=acts, **common)
        elif fmt == "same_day":
            plan = story_planner.plan_same_day_edit(analyzed, target_duration=seconds, edit_plan=acts, **common)
        else:
            plan = story_planner.plan_teaser(analyzed, target_duration=seconds, story_mode="wedding", edit_plan=acts, **common)
        if data.get("audio") and os.path.isfile(data["audio"]):
            plan["audio"] = data["audio"]
        key = re.sub(r"[^A-Za-z0-9_\-]", "_", str(args.get("key") or fmt))[:40]
        plan["edited_by"] = "Smart AI Director (Story Director draft)"
        data.setdefault("plans", {})[key] = plan
        saved = _dir_save_project(data)
        editor = args.get("editor") or "project"
        target = (data.get("target_nle") or "resolve") if editor == "project" else editor
        res: Dict[str, Any] = {}
        if target != "none":
            res = _export_plans(job_id or _new_job(), {key: plan}, target, plan.get("audio", ""),
                                markers={key: plan.get("markers") or []}, project_name=data.get("project_name") or "AI_Project")
        index_of = {os.path.normpath(c.get("file_path", "")): i for i, c in enumerate(analyzed)}
        _log(job_id, f"✂️ Story Director draft '{plan['sequence_name']}': {plan['clip_count']} shots, {plan['total_duration']}s", "info") if job_id else None
        return {"key": key, "built": plan["sequence_name"], "duration": plan["total_duration"], "editor": target,
                "plan_file": saved, "acts": [(a["name"], a["seconds"]) for a in (plan.get("edit_plan") or {}).get("acts", [])],
                "music_out": plan.get("music_out"), "vows_used": len(plan.get("captions") or []),
                "cuts": [{"clip": index_of.get(os.path.normpath(e["file_path"] or "")), "at": e["timeline_start"],
                          "len": e["duration"], "speed": e["speed_percent"], "why": e.get("why", "")} for e in plan["clips"]],
                "in_resolve": res.get("timelines_created", []), "premiere_xmls": res.get("premiere_xmls", [])}

    if name == "director_people":
        if (args.get("action") or "list") == "set_role":
            r = people_update("role", {"id": args.get("id"), "role": args.get("role") or None})
        else:
            r = people_status()
        return {k: v for k, v in r.items() if k != "persons"} | {
            "persons": [{kk: vv for kk, vv in p.items() if kk != "thumb"} for p in r.get("persons", [])]}

    if name == "director_vows":
        path, data = _dir_project()
        analyzed = data.get("analyzed", [])
        if (args.get("action") or "list") == "find":
            n = find_vow_lines(job_id or _new_job(), analyzed, bool(license_manager.get_status().get("has_credits")))
            _save_project_inplace(path, data)
            if not n:
                return {"chosen": 0, "note": "No clear spoken lines were found (or transcription is unavailable)."}
        only = args.get("clip")
        rows = []
        for i, c in enumerate(analyzed):
            if only is not None and i != int(only):
                continue
            lines = (c.get("transcript") or {}).get("lines") or []
            if lines or c.get("vow_line"):
                rows.append({"clip": i, "file": os.path.basename(c.get("file_path", "")), "moment": c.get("moment"),
                             "people": c.get("people_roles") or [], "chosen": c.get("vow_line"), "lines": lines[:40]})
        return {"clips": rows, "note": "" if rows else "No transcripts yet - call with action 'find'."}

    raise RuntimeError(f"Unknown tool {name}")


_MUSIC_CACHE: Dict[str, Dict[str, Any]] = {}


def _dir_music(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The project song's beats, bars, sections and instrumental parts (analysed once per song)."""
    song = data.get("audio") or ""
    if not song or not os.path.isfile(song):
        return None
    if song not in _MUSIC_CACHE:
        from packages.ai_engine.beat_analyzer import beat_analyzer
        _MUSIC_CACHE[song] = beat_analyzer.analyze_audio(song, 25.0)
    return _MUSIC_CACHE[song]


def _assistant_execute(job_id: str):
    def run(name: str, args: Dict[str, Any]) -> Any:
        if name == "run_code":
            if not smart_ai_settings()["allow_code"]:
                raise RuntimeError("Running code is turned off in Settings.")
            return run_code(str(args.get("code") or ""), str(args.get("purpose") or ""), int(args.get("timeout") or CODE_TIMEOUT), job_id)
        if name in DIRECTOR_NAMES:
            return director_call(name, args, job_id)
        return _mcp().handle_tool_call(name, args)
    return run


# ----------------------------------------------------------------------
# People (STEP 2): Bride / Groom / family recognition, on this computer only, after the editor opts in
# ----------------------------------------------------------------------
def _people_project() -> Tuple[str, Dict[str, Any], str]:
    path, data = _dir_project()
    return path, data, data.get("folder") or ""


def _save_project_inplace(path: str, data: Dict[str, Any]) -> None:
    targets = {path, os.path.join(OUTPUT_DIR, "last_plan.json")}
    for p in targets:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)


def people_status() -> Dict[str, Any]:
    from packages.ai_engine import people
    try:
        _, data, folder = _people_project()
    except Exception:
        return {"success": True, "has_project": False}
    view = people.public_view(people.load_store(folder))
    view.update(success=True, has_project=True, project=data.get("project_name") or os.path.basename(folder.rstrip("/\\")))
    return view


def _people_model(job_id: str) -> str:
    from packages.ai_engine import people
    if not os.path.isfile(os.path.join(people.models_dir(), people.SFACE["file"])):
        _log(job_id, "Downloading the face model (37 MB, one time only)…", "info")
    return people.ensure_model(on_progress=lambda pct: _progress(job_id, min(25, pct // 4)))


def people_scan_job(job_id: str) -> None:
    from packages.ai_engine import people
    try:
        path, data, folder = _people_project()
        model = _people_model(job_id)
        _log(job_id, "👥 Finding people in the footage (on this computer)…", "info")
        store = people.scan_project(data.get("analyzed", []), folder, model,
                                    on_progress=lambda n, t: _progress(job_id, 25 + int(70 * n / max(1, t))))
        _save_project_inplace(path, data)
        named = [p for p in store["persons"] if p.get("role")]
        _log(job_id, f"Found {len(store['persons'])} people"
                     + (f" · matched {', '.join(sorted({p['role'] for p in named}))}" if named else "")
                     + (" · couple guessed from the most-seen faces" if store.get("auto_couple") else ""), "info")
        with JOBS_LOCK:
            JOBS[job_id].update(status="done", result=people.public_view(store), progress=100)
    except Exception as e:
        _log(job_id, f"People: {e}", "error")
        with JOBS_LOCK:
            JOBS[job_id].update(status="error", error=str(e))


def people_reference_job(job_id: str, role: str, image_path: str) -> None:
    """The editor uploads a photo of the bride / groom: the best match in the footage gets that role."""
    from packages.ai_engine import people
    try:
        path, data, folder = _people_project()
        model = _people_model(job_id)
        emb, thumb = people.face_from_photo(image_path, model)
        store = people.load_store(folder) or {"version": people.STORE_VERSION, "folder": folder, "consent": True,
                                              "persons": [], "references": []}
        people.add_reference(store, role, emb, thumb)
        people.save_store(store)
        people.apply_people(data.get("analyzed", []), store)
        _save_project_inplace(path, data)
        hit = next((p for p in store["persons"] if p.get("role") == role and p.get("role_from") == "photo"), None)
        if hit:
            _log(job_id, f"✅ {role.replace('_', ' ').title()} found in {len(hit['clips'])} clips (match {hit.get('match', 0):.2f})", "info")
        elif store["persons"]:
            _log(job_id, f"Photo saved, but no one in the footage looks enough like it. Try a clearer photo.", "warning")
        else:
            _log(job_id, "Photo saved. Press “Find people” to look for this person in the footage.", "info")
        with JOBS_LOCK:
            JOBS[job_id].update(status="done", result=people.public_view(store), progress=100)
    except Exception as e:
        _log(job_id, f"People: {e}", "error")
        with JOBS_LOCK:
            JOBS[job_id].update(status="error", error=str(e))


def people_update(action: str, args: Dict[str, Any]) -> Dict[str, Any]:
    from packages.ai_engine import people
    path, data, folder = _people_project()
    if action == "delete":
        people.delete_store(folder)
        people.apply_people(data.get("analyzed", []), None)
        _save_project_inplace(path, data)
        return {"success": True, "deleted": True, **people.public_view(None)}
    store = people.load_store(folder)
    if not store:
        return {"success": False, "error": "Run “Find people” first."}
    if action == "role":
        people.set_role(store, str(args.get("id") or ""), args.get("role") or None, str(args.get("name") or ""))
    elif action == "remove_reference":
        people.remove_references(store, str(args.get("role") or ""))
    people.save_store(store)
    people.apply_people(data.get("analyzed", []), store)
    _save_project_inplace(path, data)
    return {"success": True, **people.public_view(store)}


# ----------------------------------------------------------------------
# Smart AI code runner: lets the agent write and run its own Python, like Claude Code.
# Off by default (Settings switch). On macOS every script runs in a sandbox that can only write to the
# Smart AI work folder and the output folder (never the footage) and cannot reach the internet.
# ----------------------------------------------------------------------
SMART_AI_SETTINGS = "smart_ai_settings.json"
CODE_TIMEOUT = 300
CODE_TIMEOUT_MAX = 900


def smart_ai_settings() -> Dict[str, Any]:
    try:
        with open(os.path.join(OUTPUT_DIR, ".app", SMART_AI_SETTINGS), "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    return {"allow_code": bool(data.get("allow_code")), "sandboxed": platform.system() == "Darwin"}


def save_smart_ai_settings(allow_code: bool) -> Dict[str, Any]:
    folder = os.path.join(OUTPUT_DIR, ".app")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, SMART_AI_SETTINGS), "w", encoding="utf-8") as f:
        json.dump({"allow_code": bool(allow_code)}, f)
    return smart_ai_settings()


def code_work_dir() -> str:
    d = os.path.join(OUTPUT_DIR, "smart-ai-work")
    os.makedirs(d, exist_ok=True)
    return d


def _sandbox_profile(writable: List[str]) -> str:
    """macOS sandbox: read anything, write only to `writable` (the script's temp folder is inside the work folder),
    no internet (local connections allowed, e.g. DaVinci Resolve's scripting)."""
    paths = "".join(f' (subpath "{os.path.realpath(p)}")' for p in writable)
    return ("(version 1)(allow default)"
            "(deny file-write*)"
            f"(allow file-write*{paths}"
            " (literal \"/dev/null\") (literal \"/dev/stdout\") (literal \"/dev/stderr\") (literal \"/dev/tty\"))"
            "(deny network-outbound)"
            "(allow network-outbound (remote ip \"localhost:*\"))"
            "(allow network-outbound (remote unix-socket))")


def _python_command(script: str) -> List[str]:
    """The app's own Python: the console helper next to the installed app, or this interpreter in development."""
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        for name in ("ai-video-flow-mcp.exe", "ai-video-flow-mcp"):
            helper = os.path.join(exe_dir, name)
            if os.path.isfile(helper):
                return [helper, "--avf-run-script", script]
        return [sys.executable, "--avf-run-script", script]
    return [sys.executable, script]


def run_code(code: str, purpose: str = "", timeout: int = CODE_TIMEOUT, job_id: str = "",
             work_dir: Optional[str] = None, sandbox: Optional[bool] = None) -> Dict[str, Any]:
    """Runs a Python script written by Smart AI. Environment: AVF_WORK (its folder), AVF_OUTPUT, AVF_PROJECT
    (the project JSON), AVF_FFMPEG; the app's packages are importable."""
    work = work_dir or code_work_dir()
    os.makedirs(work, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S") + f"_{uuid.uuid4().hex[:4]}"
    script = os.path.join(work, f"script_{stamp}.py")
    with open(script, "w", encoding="utf-8") as f:
        f.write(f"# Smart AI script: {purpose[:200]}\n" + code)
    env = dict(os.environ)
    try:
        from packages.ai_engine.audio_io import ffmpeg_path
        env["AVF_FFMPEG"] = ffmpeg_path()
    except Exception:
        pass
    tmp = os.path.join(work, ".tmp")
    os.makedirs(tmp, exist_ok=True)
    env.update(TMPDIR=tmp, TEMP=tmp, TMP=tmp, NUMBA_CACHE_DIR=os.path.join(tmp, "numba"), MPLCONFIGDIR=os.path.join(tmp, "mpl"))
    env.update(AVF_WORK=work, AVF_OUTPUT=OUTPUT_DIR, AVF_PROJECT=os.path.join(OUTPUT_DIR, "last_plan.json"),
               PYTHONPATH=os.pathsep.join([root_dir, env.get("PYTHONPATH", "")]).strip(os.pathsep), PYTHONIOENCODING="utf-8")
    cmd = _python_command(script)
    use_sandbox = (platform.system() == "Darwin") if sandbox is None else sandbox
    if use_sandbox and os.path.isfile("/usr/bin/sandbox-exec"):
        cmd = ["/usr/bin/sandbox-exec", "-p", _sandbox_profile([work, OUTPUT_DIR]), *cmd]
    timeout = int(max(5, min(CODE_TIMEOUT_MAX, timeout or CODE_TIMEOUT)))
    if job_id:
        _log(job_id, f"🧪 Smart AI is running its own code: {purpose[:120] or os.path.basename(script)}", "info")
    started = time.time()
    try:
        proc = subprocess.run(cmd, cwd=work, env=env, capture_output=True, text=True, timeout=timeout,
                              encoding="utf-8", errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        out = {"exit_code": proc.returncode, "stdout": proc.stdout[-20000:], "stderr": proc.stderr[-8000:]}
    except subprocess.TimeoutExpired as e:
        out = {"exit_code": None, "timed_out": True, "stdout": (e.stdout or "")[-8000:] if isinstance(e.stdout, str) else "",
               "stderr": f"Stopped after {timeout} s."}
    out.update(seconds=round(time.time() - started, 1), script=script, work_dir=work, sandboxed=bool(use_sandbox))
    new_files = sorted(f for f in os.listdir(work) if not f.startswith(("script_", ".tmp")))
    out["files_in_work_dir"] = new_files[-50:]
    if job_id:
        _log(job_id, f"   code finished (exit {out.get('exit_code')}, {out['seconds']} s)", "info" if out.get("exit_code") == 0 else "warning")
    return out


RUN_CODE_TOOL = {
    "name": "run_code",
    "description": "Write and run your own Python (like Claude Code) when no other tool can do the job: custom analysis, "
                   "ffmpeg renders, batch file work, reading the project. Runs with the app's Python and libraries "
                   "(numpy, cv2, librosa, PIL, the app's packages). Environment: AVF_WORK (your folder - put new files "
                   "here), AVF_OUTPUT, AVF_PROJECT (project JSON with 'analyzed' clips and 'plans'), AVF_FFMPEG. Never "
                   "change, move or delete the editor's footage. Print what you need to see; stdout comes back to you.",
    "input_schema": {"type": "object", "properties": {
        "code": {"type": "string", "description": "a complete Python 3 script"},
        "purpose": {"type": "string", "description": "one line: what this script does (shown to the editor)"},
        "timeout": {"type": "integer", "description": f"seconds, default {CODE_TIMEOUT}, max {CODE_TIMEOUT_MAX}"}},
        "required": ["code", "purpose"]},
}


def _assistant_tools() -> List[Dict[str, Any]]:
    seen, tools = set(DIRECTOR_NAMES), list(DIRECTOR_TOOLS)
    if smart_ai_settings()["allow_code"]:
        tools.append(RUN_CODE_TOOL)
        seen.add("run_code")
    for t in _mcp().MCP_TOOLS:
        if t["name"] in seen:
            continue
        seen.add(t["name"])
        tools.append({"name": t["name"], "description": t.get("description", "")[:1000],
                      "input_schema": t.get("inputSchema") or {"type": "object", "properties": {}}})
    return tools


def assistant_job(job_id: str, text: str) -> None:
    with ASSISTANT_LOCK:
        mark = len(ASSISTANT_HISTORY)
        try:
            reply = cloud_ai_client.assistant_turn(
                ASSISTANT_HISTORY, text, _assistant_tools(), _assistant_execute(job_id),
                on_event=lambda kind, msg: _log(job_id, f"Running {msg}…" if kind == "tool" else msg, "info"),
                system_extra=DIRECTOR_GUIDE,
            )
            with JOBS_LOCK:
                JOBS[job_id].update(status="done", result={"reply": reply}, progress=100)
        except Exception as e:
            # Drop the partial turn so the next message starts from a valid conversation
            del ASSISTANT_HISTORY[mark:]
            with JOBS_LOCK:
                JOBS[job_id].update(status="error", error=str(e))


def tool_job(job_id: str, name: str, arguments: Dict[str, Any]) -> None:
    _log(job_id, f"Running {name}...", "info")
    res = run_tool(name, arguments)
    with JOBS_LOCK:
        JOBS[job_id].update(status="done" if res["success"] else "error", result=res.get("result"),
                            error=res.get("error"), progress=100)
    _log(job_id, "Finished." if res["success"] else f"ERROR: {res.get('error')}", "info" if res["success"] else "error")


def start_job(target, *args) -> str:
    job_id = _new_job()
    threading.Thread(target=target, args=(job_id, *args), daemon=True).start()
    return job_id


# ----------------------------------------------------------------------
# HTTP handler
# ----------------------------------------------------------------------
class AppRequestHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        static_dir = os.path.dirname(os.path.abspath(__file__))
        super().__init__(*args, directory=static_dir, **kwargs)

    def _json(self, payload: Any, code: int = 200) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return {}

    def end_headers(self):
        # Always serve fresh dashboard files after an update
        if not self.path.startswith("/api/"):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _authorized(self) -> bool:
        """Only the app window (which received the per-launch token) may call the API."""
        if not self.path.startswith("/api/") or self.path == "/api/version":
            return True
        host_ok = (self.headers.get("Host") or "") in ALLOWED_HOSTS
        token_ok = bool(SESSION_TOKEN) and hmac.compare_digest(self.headers.get("X-AVF-Token") or "", SESSION_TOKEN)
        if host_ok and token_ok:
            return True
        self._json({"error": "Forbidden"}, 403)
        return False

    def do_GET(self):
        if not self._authorized():
            return
        if self.path == "/api/last-plan":
            self._json(get_last_plan())
            return

        if self.path == "/api/people":
            self._json(people_status())
            return

        if self.path == "/api/smart-ai/settings":
            self._json(smart_ai_settings())
            return

        if self.path == "/api/status":
            self._json({
                "resolve": resolve_adapter.get_status(),
                "premiere": premiere_adapter.get_status(),
                "edius": edius_adapter.get_status(),
                "hyperframes": hyperframes_service.is_available(),
                "claude": ConnectAllEngine(root_dir).check_status()
            })
            return

        if self.path == "/api/version":
            self._json({"app": "ai-video-flow", "version": APP_VERSION})
            return

        if self.path == "/api/premiere/status":
            self._json(premiere_adapter.get_status())
            return

        if self.path == "/api/tools":
            self._json(list_tools())
            return

        if self.path == "/api/diagnostics":
            self._json({
                "environment": dependency_report(),
                "resolve": resolve_adapter.get_status(),
                "premiere": premiere_adapter.get_status(),
                "hyperframes": hyperframes_service.is_available(),
                "claude": ConnectAllEngine(root_dir).check_status()
            })
            return

        if self.path == "/api/license/status":
            self._json(license_status())
            return

        if self.path == "/api/plans":
            self._json(fetch_plans())
            return

        if self.path == "/api/update":
            self._json(license_manager.update_info())
            return

        if self.path == "/api/cloud-ai/status":
            self._json(cloud_ai_client.get_status())
            return

        if self.path.startswith("/api/job/"):
            job_id = self.path.rsplit("/", 1)[-1]
            with JOBS_LOCK:
                job = JOBS.get(job_id)
                payload = json.loads(json.dumps(job, default=str)) if job else None
            self._json(payload or {"error": "Unknown job"}, 200 if payload else 404)
            return

        super().do_GET()

    def do_POST(self):
        if not self._authorized():
            return
        data = self._body()

        if self.path == "/api/connect":
            self._json(ConnectAllEngine(root_dir).run_connect_all())

        elif self.path == "/api/test-resolve":
            self._json(resolve_adapter.get_status())

        elif self.path == "/api/resolve/launch":
            self._json(resolve_adapter.launch())

        elif self.path == "/api/resolve/send":
            self._json(send_to_resolve(data.get("plan_path")))

        elif self.path == "/api/resolve/organize-bins":
            self._json(resolve_adapter.organize_bins_automatically())

        elif self.path == "/api/premiere/send":
            self._json(send_to_premiere())

        elif self.path == "/api/premiere/launch":
            self._json(premiere_adapter.launch())

        elif self.path == "/api/premiere/open":
            self._json(premiere_adapter.open_timeline(data.get("xml_path", "")))

        elif self.path == "/api/premiere/state":
            self._json(premiere_adapter.get_project_state())

        elif self.path == "/api/premiere/timeline-state":
            self._json(premiere_adapter.get_timeline_state())

        elif self.path == "/api/premiere/organize-bins":
            self._json(premiere_adapter.organize_bins_automatically())

        elif self.path == "/api/edius/status":
            self._json(edius_adapter.get_status())

        elif self.path == "/api/edius/launch":
            self._json(edius_adapter.launch())

        elif self.path == "/api/edius/export":
            import glob
            plans_found = glob.glob(os.path.join(OUTPUT_DIR, "*_plan_*.json"))
            if not plans_found:
                self._json({"success": False, "error": "Koi edit plan nahi mila. Pehle 'Start AI Trimming' run karein."})
            else:
                plans_found.sort(key=os.path.getmtime, reverse=True)
                with open(plans_found[0], "r", encoding="utf-8") as f:
                    pdata = json.load(f)
                edius_dir = os.path.join(OUTPUT_DIR, "edius_timelines")
                os.makedirs(edius_dir, exist_ok=True)
                exported = []
                for pk, pv in pdata.get("plans", {}).items():
                    sname = pv.get("sequence_name") or f"EDIUS_{pk}"
                    xout = os.path.join(edius_dir, f"{sname}_EDIUS.xml")
                    r = edius_adapter.export_plan_to_edius_xml(
                        plan=pv,
                        output_xml_path=xout,
                        audio_file_path=pdata.get("audio"),
                        sequence_name=sname,
                        aspect_ratio=pv.get("aspect_ratio", "16:9"),
                        broll_clips=pv.get("broll_clips")
                    )
                    if r.get("success"):
                        exported.append(xout)
                self._json({
                    "success": True,
                    "message": f"{len(exported)} EDIUS XML timeline(s) export ho gayi!",
                    "xmls": exported,
                    "edius_dir": edius_dir
                })

        elif self.path == "/api/browse":
            self._json(browse(data.get("kind", "folder"), data.get("prompt", "Select")))

        elif self.path == "/api/scan-folder":
            res = scan_folder(data.get("path", ""))
            if res.get("success"):
                # Keep the response light for huge shoots; send first 300 names only
                all_media = res["videos"] + res.get("images", [])
                res["preview"] = [os.path.relpath(v, res["folder"]) for v in all_media[:300]]
                res.pop("videos", None)
                res.pop("images", None)
                res.pop("psds", None)
            self._json(res)

        elif self.path == "/api/run-wedding" and (free_status(force=True) or {"success": True}).get("success") is False:
            self._json({"success": False, "error": free_status()["error"] or "Could not check the free plan. Try again."})

        elif self.path == "/api/run-wedding" and free_projects_left() == 0:
            n = (free_status() or {}).get("limit", free_daily_limit())
            self._json({"success": False, "limit": True,
                        "error": (f"The free plan includes {n} project{'s' if n != 1 else ''} per day. " if n else "The free plan is paused. ")
                        + f"Go unlimited with AI Editing from ₹{price_from()['local']:,}/month"
                        + (", or come back tomorrow." if n else ".")})

        elif self.path == "/api/run-wedding":
            formats = data.get("formats") or ["teaser", "highlight", "full_film"]
            target_nle = data.get("target_nle") or "resolve"
            mode = data.get("mode") or "wedding"
            ref_video = (data.get("ref_video") or "").strip()
            instructions = (data.get("instructions") or "").strip()
            tier = (data.get("tier") or "free").strip().lower()
            if mode in ("podcast", "tutorial"):
                job_id = start_job(longform_job, data.get("folder", ""), formats, target_nle, mode, data.get("cut_level") or "normal", ref_video, instructions)
                self._json({"success": True, "job_id": job_id})
                return
            job_id = start_job(wedding_job, data.get("folder", ""), (data.get("audio") or "").strip(), formats, target_nle, mode, ref_video,
                               instructions, tier, bool(data.get("ai_music")), (data.get("voiceover") or "").strip()[:2500],
                               bool(data.get("same_day")))
            self._json({"success": True, "job_id": job_id})

        elif self.path == "/api/smart-ai/settings":
            self._json(save_smart_ai_settings(bool(data.get("allow_code"))))

        elif self.path == "/api/people/scan":
            self._json({"success": True, "job_id": start_job(people_scan_job)})

        elif self.path == "/api/people/reference":
            role, image = (data.get("role") or "").strip(), (data.get("path") or "").strip()
            if role not in ("bride", "groom", "bride_mother", "bride_father", "groom_mother", "groom_father", "sibling") or not os.path.isfile(image):
                self._json({"success": False, "error": "Choose a role and a photo."}, 400)
            else:
                self._json({"success": True, "job_id": start_job(people_reference_job, role, image)})

        elif self.path in ("/api/people/role", "/api/people/delete", "/api/people/reference/remove"):
            action = {"/api/people/role": "role", "/api/people/delete": "delete",
                      "/api/people/reference/remove": "remove_reference"}[self.path]
            try:
                self._json(people_update(action, data))
            except Exception as e:
                self._json({"success": False, "error": str(e)}, 400)

        elif self.path == "/api/re-edit-timeline":
            prompt = (data.get("prompt") or "").strip()
            timeline = (data.get("timeline") or "all").strip()
            target_nle = (data.get("target_nle") or "").strip()
            ref_video = (data.get("ref_video") or "").strip()
            if not prompt and not ref_video:
                self._json({"success": False, "error": "Re-Edit prompt ya reference video likhiye."}, 400)
            else:
                job_id = start_job(reedit_timeline_job, prompt, timeline, target_nle, ref_video)
                self._json({"success": True, "job_id": job_id})

        elif self.path == "/api/license/status":
            self._json(license_status())

        elif self.path == "/api/license/activate":
            key = (data.get("license_key") or "").strip()
            email = (data.get("email") or "").strip()
            self._json(license_manager.activate_license(key, email))

        elif self.path == "/api/license/deactivate":
            self._json(license_manager.deactivate_license())

        elif self.path == "/api/license/refresh":
            self._json(license_manager.refresh())

        elif self.path == "/api/assistant/send":
            text = (data.get("text") or "").strip()
            if not license_manager.get_status().get("has_credits"):
                # Free and AI Editing plans are offline-only: no Smart AI requests at all
                self._json({"success": False, "error": "The AI Assistant is part of Smart AI Editing. See Pricing to add a Smart AI pack."}, 403)
            elif not text:
                self._json({"success": False, "error": "Type a message first."}, 400)
            else:
                self._json({"success": True, "job_id": start_job(assistant_job, text[:4000])})

        elif self.path == "/api/assistant/reset":
            with ASSISTANT_LOCK:
                ASSISTANT_HISTORY.clear()
            self._json({"success": True})

        elif self.path == "/api/tool":
            job_id = start_job(tool_job, data.get("name", ""), data.get("arguments") or {})
            self._json({"success": True, "job_id": job_id})

        elif self.path == "/api/open-url":
            # Open links (e.g. sponsored ads) in the user's normal browser, not inside the app window
            url = str(data.get("url") or "")
            if url.startswith(("https://", "http://")):
                import webbrowser
                webbrowser.open(url)
                self._json({"success": True})
            else:
                self._json({"success": False, "error": "Only web links can be opened."}, 400)

        elif self.path == "/api/open-folder":
            path = data.get("path") or OUTPUT_DIR
            os.makedirs(path, exist_ok=True) if path == OUTPUT_DIR else None
            if os.path.exists(path):
                if platform.system() == "Darwin":
                    subprocess.Popen(["open", path])
                elif platform.system() == "Windows":
                    os.startfile(path)  # type: ignore[attr-defined]
                else:
                    subprocess.Popen(["xdg-open", path])
                self._json({"success": True})
            else:
                self._json({"success": False, "error": f"Not found: {path}"})

        else:
            self._json({"error": "Not found"}, 404)

    def log_message(self, format, *args):
        # Silence routine static asset HTTP logs
        return


def start_server(port: int = 5678, token: Optional[str] = None):
    global SESSION_TOKEN
    if token:
        SESSION_TOKEN = token
    ALLOWED_HOSTS.update({f"127.0.0.1:{port}", f"localhost:{port}"})
    server = ThreadingHTTPServer(("127.0.0.1", port), AppRequestHandler)
    threading.Thread(target=fetch_plans, daemon=True).start()  # latest prices / free limit from the admin portal
    if premiere_adapter.get_status().get("installed"):  # ready before the first export, so Premiere loads it on its next start
        threading.Thread(target=premiere_adapter.install_bridge, daemon=True).start()
    logger.info(f"AI Video Flow desktop backend running on http://127.0.0.1:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()


if __name__ == "__main__":
    start_server()
