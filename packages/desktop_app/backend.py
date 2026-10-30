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
from packages.licensing.updater import updater
from packages.ai_engine.cloud_ai_client import cloud_ai_client
from installer.connect_all_engine import ConnectAllEngine

logger = logging.getLogger("ai_video_flow.desktop_backend")

VIDEO_EXTS = {".mp4", ".mov", ".mxf", ".mts", ".m2ts", ".avi", ".mkv", ".m4v", ".braw", ".r3d", ".webm", ".mpg", ".mpeg", ".3gp"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".aif", ".aiff", ".ogg"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}
OUTPUT_DIR = os.path.join(os.path.expanduser("~"), "AI-Video-Flow-Output")
APP_VERSION = "1.1.15"  # launcher only reuses an already-running server with the same version

SESSION_TOKEN = os.environ.get("AVF_SESSION_TOKEN", "")
ALLOWED_HOSTS: set = set()

# Free plan: projects per day (any paid plan removes the limit). The admin portal can change it;
# the last plan list from the server is cached so prices and the limit also show offline.
FREE_DAILY_PROJECTS = 2
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
        cached = _cached_plans().get("free_daily_projects")
        if cached is not None:
            val = int(cached)
            return 0 if val == 0 else max(FREE_DAILY_PROJECTS, val)
        return FREE_DAILY_PROJECTS
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
        st = license_manager.free_status()
        if isinstance(st, dict) and st.get("success"):
            srv_lim = int(st.get("limit", FREE_DAILY_PROJECTS))
            used = int(st.get("used", 0))
            eff_lim = 0 if srv_lim == 0 else max(FREE_DAILY_PROJECTS, srv_lim)
            st["limit"] = eff_lim
            st["left"] = max(0, eff_lim - used)
        _FREE_CACHE.update(t=time.time(), data=st)
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
        "total_gb": round(total_bytes / (1024 ** 3), 2),
        "videos": videos,
        "audios": audios
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
        JOBS[job_id]["logs"].append({"t": time.strftime("%H:%M:%S"), "msg": msg, "level": level})


def _progress(job_id: str, pct: int) -> None:
    with JOBS_LOCK:
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
        entries.append({"mediaPoolItem": item, "startFrame": start, "endFrame": end})
    if not entries:
        return 0
    if not resolve_adapter.append_clips_to_timeline(entries, plan.get("sequence_name")):
        return 0

    # Place B-Roll cutaways on Video Track 2 (V2) when present (e.g. Reels & Shorts workflow)
    broll_list = plan.get("broll_clips") or []
    if broll_list:
        try:
            project = resolve_adapter._current_project
            tl = project.GetCurrentTimeline() if project else None
            if tl:
                while tl.GetTrackCount("video") < 2:
                    tl.AddTrack("video")
                tl_fps = float(project.GetSetting("timelineFrameRate") or 25.0)
                tl_start = int(tl.GetStartFrame() or 0)
                broll_entries = []
                for bc in broll_list:
                    b_item = items_by_path.get(os.path.normpath(bc.get("file_path") or ""))
                    if not b_item:
                        continue
                    b_in = int(bc.get("in_frame", 0))
                    b_out = max(b_in + 1, int(bc.get("out_frame", b_in + 1)) - 1)
                    broll_entries.append({
                        "mediaPoolItem": b_item,
                        "startFrame": b_in,
                        "endFrame": b_out,
                        "trackIndex": 2,
                        "recordFrame": tl_start + int(round(float(bc.get("timeline_start", 0)) * tl_fps)),
                        "mediaType": 1,
                    })
                if broll_entries:
                    project.GetMediaPool().AppendToTimeline(broll_entries)
        except Exception as e:
            logger.warning(f"Resolve B-roll V2 placement note: {e}")

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
            used = sorted({
                c["file_path"]
                for p in plans.values()
                for c in (list(p.get("clips") or []) + list(p.get("broll_clips") or []))
                if c.get("file_path")
            })
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
                        resolve_adapter.add_timeline_marker(int(m["seconds"] * fps), "Blue", m["name"], "")
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


def longform_job(job_id: str, folder: str, formats: List[str], target_nle: str, mode: str, cut_level: str = "normal") -> None:
    """Podcasts, tutorials and classes: removes silences/dead air, builds a clean edit, shorts or chapters."""
    try:
        import cv2
        import numpy as np
        import librosa
        from packages.ai_engine.audio_io import load_audio

        # Normal keeps natural breathing room; Tight cuts every hesitation
        top_db, min_gap, pad = (30.0, 0.45, 0.20) if cut_level != "tight" else (25.0, 0.25, 0.10)
        label = "Podcast" if mode == "podcast" else "Tutorial"

        scan = scan_folder(folder)
        if not scan["success"]:
            raise RuntimeError(scan["error"])
        videos = sorted(scan["videos"])
        if not videos:
            raise RuntimeError("No video files found in this folder (mp4, mov, mxf, mts, avi, mkv...).")
        _log(job_id, f"{label} mode · {len(videos)} recording(s) · silence cutting: {'Tight' if cut_level == 'tight' else 'Normal'}", "info")

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
        with open(plan_file, "w", encoding="utf-8") as f:
            json.dump({"folder": folder, "mode": mode, "plans": plans, "target_nle": target_nle, "audio": ""}, f, indent=2, default=str)
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


def wedding_job(job_id: str, folder: str, audio_path: str, formats: List[str], target_nle: str = "resolve", mode: str = "wedding",
                ref_video: str = "", instructions: str = "", tier: str = "free", ai_music: bool = False, voiceover: str = "",
                reel_options: Optional[Dict[str, Any]] = None) -> None:
    """`voiceover`: "" = none, "auto" = Smart AI writes the script, anything else = the script to speak."""
    try:
        from packages.ai_engine.in_clip_trimmer import in_clip_trimmer
        from packages.ai_engine.beat_analyzer import beat_analyzer
        from packages.ai_engine.story_planner import story_planner
        reel_opts = reel_options or {}

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
        if not videos:
            raise RuntimeError("No video files found in this folder (mp4, mov, mxf, mts, avi, mkv...).")
        _log(job_id, f"Found {len(videos)} video clips ({scan['total_gb']} GB).")

        # 1. Analyse every clip
        analyzed = []
        for i, vp in enumerate(videos, 1):
            try:
                res = in_clip_trimmer.analyze_clip(vp)
                analyzed.append(res)
                full_kept = res.get('full_duration', res.get('duration', 0))
                total_dur = res.get('duration', 0)
                _log(job_id, f"[{i}/{len(videos)}] {os.path.basename(vp)} ({total_dur:.1f}s) → "
                             f"Full Film: {full_kept:.1f}s kept (action starts at {res.get('action_start_seconds', 0):.1f}s) | "
                             f"Highlight: {res.get('trimmed_duration', 0):.1f}s (score {res.get('quality_score', 0):.0f})")
            except Exception as e:
                _log(job_id, f"[{i}/{len(videos)}] Skipped {os.path.basename(vp)}: {e}", "warning")
            _progress(job_id, int(i / len(videos) * 65))

        if not analyzed:
            raise RuntimeError("None of the clips could be analysed.")

        # Wedding only: put every camera's footage in real shoot-time order (other modes follow the music)
        from packages.ai_engine import multicam_sync
        multicam = False
        if mode == "wedding":
            sync_report = multicam_sync.sync(analyzed, folder)
            multicam = len(sync_report["cameras"]) > 1
            if multicam:
                _log(job_id, "Matching the cameras by their sound (songs, speeches, crowd)…", "info")
                multicam_sync.audio_place_wedding(analyzed, sync_report)
            analyzed.sort(key=lambda c: c["abs_start"])
        audio_synced_plan = None
        if mode in ("youtube", "music_video"):
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

        # 1.5. Analyze Reference Video (if provided by editor)
        ref_lut_path = None
        if ref_video and os.path.isfile(ref_video):
            try:
                _log(job_id, f"🎯 Analyzing Reference Reel / Video: {os.path.basename(ref_video)}", "info")
                import cv2
                from packages.ai_engine.color_transfer_engine import ColorTransferEngine
                cap = cv2.VideoCapture(ref_video)
                total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
                if total_frames > 0:
                    mid_frame = int(total_frames * 0.35)
                    cap.set(cv2.CAP_PROP_POS_FRAMES, mid_frame)
                    ret, frame = cap.read()
                    if ret and frame is not None:
                        os.makedirs(OUTPUT_DIR, exist_ok=True)
                        ref_frame_path = os.path.join(OUTPUT_DIR, "ref_extracted_frame.jpg")
                        cv2.imwrite(ref_frame_path, frame)
                        cte = ColorTransferEngine(luts_dir=os.path.join(OUTPUT_DIR, "luts"))
                        lut_res = cte.generate_3d_lut(ref_frame_path, lut_name="AI_Reference_Reel_Look")
                        if lut_res.get("success"):
                            ref_lut_path = lut_res.get("lut_path")
                            _log(job_id, f"  🎨 Extracted Reference 3D LUT: {os.path.basename(ref_lut_path)}", "info")
                cap.release()
            except Exception as e:
                _log(job_id, f"Reference video note: {e}", "warning")

        # 2. Smart AI: Claude reviews one frame per clip (1 credit only when it succeeds), then AI audio
        voice_path, voice_seconds = "", 0.0
        if is_premium:
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

        # 2.5. Music beats
        beat_times = None
        if audio_path:
            if os.path.isfile(audio_path):
                _log(job_id, f"Analysing music beats: {os.path.basename(audio_path)}", "info")
                beats = beat_analyzer.analyze_audio(audio_path, 25.0)
                beat_times = beats.get("beat_times") or beats.get("beats")
                _log(job_id, f"Tempo: {beats.get('bpm') or beats.get('tempo', '?')} BPM, "
                             f"{len(beat_times or [])} beats detected.")
            else:
                _log(job_id, f"Music file not found, continuing without beat-sync: {audio_path}", "warning")
        _progress(job_id, 75)

        # 3. Story plans tailored by workflow mode
        plans = {}
        rendered_reels = []
        if mode == "reels":
            from packages.ai_engine.reel_cloner import reel_cloner
            ref_dna = reel_cloner.analyze_reference_reel(
                ref_video,
                extract_audio=bool(reel_opts.get("use_ref_audio", True)),
                log_fn=lambda msg, lvl="info": _log(job_id, msg, lvl),
            )
            if ref_dna.get("lut_path"):
                ref_lut_path = ref_dna["lut_path"]
            if not audio_path and reel_opts.get("use_ref_audio", True) and ref_dna.get("ref_audio_path"):
                audio_path = ref_dna["ref_audio_path"]
                beat_times = ref_dna.get("beat_times") or beat_times

            ai_blueprint = None
            if is_premium and ref_dna.get("keyframes"):
                try:
                    thumb_dir = os.path.join(OUTPUT_DIR, ".thumbs")
                    os.makedirs(thumb_dir, exist_ok=True)
                    u_thumbs = [p for c in analyzed[:24] if (p := _thumbnail(c, thumb_dir, len(os.listdir(thumb_dir))))]
                    if u_thumbs:
                        _log(job_id, "🤖 Smart AI is matching Reference Reel style, shot order, B-roll & Hinglish captions…", "info")
                        ai_blueprint = cloud_ai_client.plan_reel_clone(ref_dna["keyframes"], u_thumbs, instructions, 45.0)
                except Exception as e:
                    _log(job_id, f"Reference AI style note: {e}", "warning")

            reel_style = reel_opts.get("reel_style") or "auto_ref"
            enable_broll = bool(reel_opts.get("enable_broll", True))
            depth_text = (reel_opts.get("depth_text") or "").strip()
            caption_text = (reel_opts.get("caption_text") or "").strip()
            watermark_text = (reel_opts.get("watermark_text") or "").strip()

            if "teaser" in formats or "render_mp4" in formats:
                plans["teaser"] = reel_cloner.build_viral_reel_plan(
                    analyzed_clips=analyzed,
                    ref_dna=ref_dna,
                    target_duration=45.0,
                    sequence_name="01_Instagram_Reel_9x16",
                    reel_style=reel_style,
                    audio_path=audio_path,
                    use_ref_audio=bool(reel_opts.get("use_ref_audio", True)),
                    depth_text=depth_text,
                    caption_text=caption_text,
                    watermark_text=watermark_text,
                    enable_broll=enable_broll,
                    ai_clone_blueprint=ai_blueprint,
                    log_fn=lambda msg, lvl="info": _log(job_id, msg, lvl),
                )
            if "highlight" in formats:
                plans["highlight"] = reel_cloner.build_viral_reel_plan(
                    analyzed_clips=analyzed,
                    ref_dna=ref_dna,
                    target_duration=30.0,
                    sequence_name="02_Shorts_Teaser_30s",
                    reel_style=reel_style,
                    audio_path=audio_path,
                    use_ref_audio=bool(reel_opts.get("use_ref_audio", True)),
                    depth_text=depth_text,
                    caption_text=caption_text,
                    watermark_text=watermark_text,
                    enable_broll=enable_broll,
                    ai_clone_blueprint=ai_blueprint,
                    log_fn=lambda msg, lvl="info": _log(job_id, msg, lvl),
                )
        elif mode == "youtube":
            if "teaser" in formats or "highlight" in formats:
                p = story_planner.plan_highlight(analyzed, beat_times=beat_times, target_duration=360.0)
                p["sequence_name"] = "01_YouTube_Main_Edit"
                plans["highlight"] = p
            if "full_film" in formats:
                p = story_planner.plan_teaser(analyzed, target_duration=60.0, is_vertical=True)
                p["sequence_name"] = "02_YouTube_Shorts_Highlights"
                p["aspect_ratio"] = "9:16 (1080x1920)"
                plans["teaser"] = p
        elif mode == "music_video":
            if "teaser" in formats or "highlight" in formats:
                p = story_planner.plan_highlight(analyzed, beat_times=beat_times, target_duration=180.0)
                p["sequence_name"] = "01_Music_Video_Cut"
                plans["highlight"] = p
            if "full_film" in formats:
                p = story_planner.plan_teaser(analyzed, target_duration=60.0, is_vertical=False)
                p["sequence_name"] = "02_Cinematic_Mood_Montage"
                plans["teaser"] = p
        else:  # Default: wedding
            if "teaser" in formats:
                plans["teaser"] = story_planner.plan_teaser(analyzed)
            if "highlight" in formats:
                plans["highlight"] = story_planner.plan_highlight(
                    analyzed, beat_times=beat_times, ref_video_path=ref_video
                )
            if "full_film" in formats:
                if multicam:
                    plans["full_film"] = multicam_sync.plan_multicam_tracks(analyzed)
                    plans["multicam_edit"] = multicam_sync.plan_multicam_film(analyzed)
                    plans["multicam_edit"]["sequence_name"] = "04_Multicam_Auto_Edit"
                    _log(job_id, f"  Full Film: each camera on its own track (V1–V{len(plans['full_film']['tracks'])}), "
                                 "all footage kept and lined up in time. 04_Multicam_Auto_Edit switches between them.", "info")
                else:
                    plans["full_film"] = story_planner.plan_full_film(analyzed)

        if audio_synced_plan:
            plans["audio_synced"] = audio_synced_plan

        for key, p in plans.items():
            if p.get("tracks"):
                continue
            if audio_path and os.path.isfile(audio_path) and key in ("teaser", "highlight"):
                p["audio"] = audio_path  # music bed under the edit
            if voice_path and key in ("teaser", "highlight"):
                p["voiceover"], p["voiceover_duration"] = voice_path, voice_seconds

        # YouTube and Music Video: the music leads - every cut lands on a beat (Reels already beat-snapped in reel_cloner)
        if mode in ("youtube", "music_video"):
            if beat_times:
                for p in plans.values():
                    if p.get("tracks"):
                        continue  # synced takes keep their real (song) positions
                    n = _snap_to_beats(p, beat_times)
                    _log(job_id, f"  🎵 {p.get('sequence_name')}: {n} cuts placed on the beat", "info")
            else:
                _log(job_id, "Add a song in AI Studio to cut this edit on the beat.", "warning")

        # 3.5. Non-reel modes keep a single primary track without extra B-roll overlay; Reels & Shorts preserves V2 B-Roll
        if mode != "reels":
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
        with open(plan_file, "w", encoding="utf-8") as f:
            json.dump({
                "folder": folder,
                "audio": audio_path,
                "mode": mode,
                "ref_video": ref_video,
                "ref_lut_path": ref_lut_path,
                "instructions": instructions,
                "analyzed": analyzed,
                "plans": plans,
                "target_nle": target_nle
            }, f, indent=2, default=str)
        _log(job_id, f"Edit plan saved: {plan_file}", "info")

        # 4.5. Render Ready-to-Post .MP4 Reel when requested in Reels & Shorts workflow
        if mode == "reels" and ("render_mp4" in formats or reel_opts.get("render_mp4")):
            from packages.ai_engine.reel_cloner import reel_cloner
            primary_reel = plans.get("teaser") or next(iter(plans.values()), None)
            if primary_reel:
                mp4_res = reel_cloner.render_reel_mp4(
                    primary_reel,
                    log_fn=lambda msg, lvl="info": _log(job_id, msg, lvl),
                )
                if mp4_res.get("success"):
                    rendered_reels.append(mp4_res["mp4_path"])

        result = _export_plans(job_id, plans, target_nle, audio_path, project_name=os.path.basename(folder.rstrip("/\\")))
        result["plan_file"] = plan_file
        if rendered_reels:
            result["rendered_reels"] = rendered_reels
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


def _thumbnail(clip: Dict[str, Any], out_dir: str, idx: int) -> Optional[str]:
    import cv2
    cap = cv2.VideoCapture(clip["file_path"])
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


def ai_review_clips(job_id: str, analyzed: List[Dict[str, Any]], instructions: str, job_name: str) -> Tuple[bool, str]:
    """Claude scores the best candidate clips; the scores reshape which shots the planners pick.
    Returns (credit used, story note)."""
    candidates = sorted(analyzed, key=lambda c: c.get("quality_score", 0), reverse=True)[:40]
    thumb_dir = os.path.join(OUTPUT_DIR, ".thumbs")
    os.makedirs(thumb_dir, exist_ok=True)
    pairs = []
    for clip in candidates:
        path = _thumbnail(clip, thumb_dir, len(pairs))
        if path:
            pairs.append((clip, path))
    if not pairs:
        _log(job_id, "AI review skipped: could not read frames from the clips.", "warning")
        return False, ""
    _log(job_id, f"Sending {len(pairs)} preview frames to Smart AI for review…", "info")
    try:
        review = cloud_ai_client.review_shots([p for _, p in pairs], instructions)
    except Exception as e:
        _log(job_id, f"AI review failed, continuing with the local edit (no credit used): {e}", "warning")
        return False, ""
    for i, (clip, _) in enumerate(pairs):
        s = review["shots"].get(i)
        if not s:
            continue
        clip["ai_moment"], clip["ai_emotion"] = s["moment"], s["emotion"]
        base = float(clip.get("quality_score", 0))
        clip["quality_score"] = round(base * 0.3 if not s["keep"] else base * 0.6 + float(s["emotion"]) * 0.4, 1)
    top = sorted((c for c, _ in pairs if c.get("ai_moment")), key=lambda c: c.get("ai_emotion", 0), reverse=True)[:5]
    for c in top:
        _log(job_id, f"  ★ {os.path.basename(c['file_path'])}: {c['ai_moment']} (emotion {c['ai_emotion']})", "info")
    if review.get("story_note"):
        _log(job_id, f"  Story note: {review['story_note']}", "info")
    return _use_credit(job_id, job_name), review.get("story_note", "")


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


def _assistant_tools() -> List[Dict[str, Any]]:
    seen, tools = set(), []
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
                ASSISTANT_HISTORY, text, _assistant_tools(), _mcp().handle_tool_call,
                on_event=lambda kind, name: _log(job_id, f"Running {name}…", "info"),
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

        if self.path == "/api/update/status":
            self._json(updater.status())
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
                res["preview"] = [os.path.relpath(v, res["folder"]) for v in res["videos"][:300]]
                res.pop("videos")
            self._json(res)

        elif self.path == "/api/run-wedding" and not license_manager.get_status().get("local_unlimited") and not license_manager.is_profile_registered():
            self._json({
                "success": False,
                "needs_registration": True,
                "error": "Free Trial (2 Edits / Day) chalane ke liye pehle My Profile me apna Name, WhatsApp Number aur Email register karein.",
            })

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
                job_id = start_job(longform_job, data.get("folder", ""), formats, target_nle, mode, data.get("cut_level") or "normal")
                self._json({"success": True, "job_id": job_id})
                return
            job_id = start_job(wedding_job, data.get("folder", ""), (data.get("audio") or "").strip(), formats, target_nle, mode, ref_video,
                               instructions, tier, bool(data.get("ai_music")), (data.get("voiceover") or "").strip()[:2500],
                               data.get("reel_options") or {})
            self._json({"success": True, "job_id": job_id})

        elif self.path == "/api/license/status":
            self._json(license_status())

        elif self.path == "/api/license/register-profile":
            self._json(license_manager.register_user_profile(
                name=(data.get("name") or "").strip(),
                phone=(data.get("phone") or "").strip(),
                email=(data.get("email") or "").strip(),
                studio=(data.get("studio") or "").strip(),
            ))

        elif self.path == "/api/license/activate":
            key = (data.get("license_key") or "").strip()
            email = (data.get("email") or "").strip()
            self._json(license_manager.activate_license(key, email))

        elif self.path == "/api/license/deactivate":
            self._json(license_manager.deactivate_license())

        elif self.path == "/api/license/refresh":
            self._json(license_manager.refresh())

        elif self.path == "/api/update/download":
            self._json(updater.start_download(license_manager.update_info()))

        elif self.path == "/api/update/install":
            with JOBS_LOCK:
                busy = any(j["status"] == "running" for j in JOBS.values())
            if busy:
                self._json({"success": False, "error": "A project is still running. Update once it finishes."}, 409)
            else:
                self._json(updater.install())

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
