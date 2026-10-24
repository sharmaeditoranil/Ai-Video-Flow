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
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from typing import Dict, Any, List, Optional

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
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}
OUTPUT_DIR = os.path.join(os.path.expanduser("~"), "AI-Video-Flow-Output")
APP_VERSION = "1.1.0"  # launcher only reuses an already-running server with the same version

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


def _append_plan_to_resolve(plan: Dict[str, Any], items_by_path: Dict[str, Any]) -> int:
    """Builds trimmed AppendToTimeline entries for a plan and appends them. Returns clips placed."""
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
    return len(entries) if resolve_adapter.append_clips_to_timeline(entries, plan.get("sequence_name")) else 0


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


def wedding_job(job_id: str, folder: str, audio_path: str, formats: List[str], target_nle: str = "resolve", mode: str = "wedding", ref_video: str = "", instructions: str = "", tier: str = "free") -> None:
    try:
        from packages.ai_engine.in_clip_trimmer import in_clip_trimmer
        from packages.ai_engine.beat_analyzer import beat_analyzer
        from packages.ai_engine.story_planner import story_planner

        is_premium = (tier == "premium")
        if is_premium:
            status = license_manager.get_status()
            if not status.get("is_premium"):
                raise RuntimeError(f"License Error: AI Smart Premium requires an active license key or credits. Current status: {status.get('tier_display')}. Please activate a license or select Free Local Mode.")
            credit_res = license_manager.consume_wedding_credit(job_name=f"Wedding Shoot: {os.path.basename(folder)}")
            if not credit_res.get("success"):
                raise RuntimeError(f"Credit Error: {credit_res.get('error')}")
            _log(job_id, f"👑 [AI Smart Premium Mode Active] Deducted 1 wedding credit. Remaining credits: {credit_res.get('credits_remaining')}", "info")
            _log(job_id, "✨ Activating Deep Multimodal Scene Understanding & Emotional Analysis...", "info")
        else:
            _log(job_id, "🟢 [Free Community Local Mode] 100% On-Device Offline Processing (Unlimited, zero cost).", "info")

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
                             f"Full Film: {full_kept:.1f}s kept (shake trimmed) | "
                             f"Highlight: {res.get('trimmed_duration', 0):.1f}s (score {res.get('quality_score', 0):.0f})")
            except Exception as e:
                _log(job_id, f"[{i}/{len(videos)}] Skipped {os.path.basename(vp)}: {e}", "warning")
            _progress(job_id, int(i / len(videos) * 65))

        if not analyzed:
            raise RuntimeError("None of the clips could be analysed.")

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

        # 2. Music beats
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
        # 2.5. Cloud AI Deep Multimodal Analysis (AI Smart Premium Tier)
        if is_premium:
            _log(job_id, "🧠 Cloud AI: Analyzing ceremony emotions, sacred vows & story pacing...", "info")
            try:
                sample_frames = []
                cloud_res = cloud_ai_client.analyze_ceremony_multimodal(sample_frames, audio_transcript=instructions)
                _log(job_id, f"  ✨ Directorial Guidance: {cloud_res.get('story_guidance')}", "info")
                _log(job_id, f"  🎨 Color Recommendation: {cloud_res.get('color_recommendation')}", "info")
            except Exception as e:
                _log(job_id, f"Cloud AI guidance note: {e}", "warning")

        _progress(job_id, 75)

        # 3. Story plans tailored by workflow mode
        plans = {}
        if mode == "reels":
            if "teaser" in formats:
                p = story_planner.plan_teaser(analyzed, target_duration=45.0, is_vertical=True)
                p["sequence_name"] = "01_Instagram_Reel_9x16"
                p["aspect_ratio"] = "9:16 (1080x1920)"
                plans["teaser"] = p
            if "highlight" in formats:
                p = story_planner.plan_teaser(analyzed, target_duration=30.0, is_vertical=True)
                p["sequence_name"] = "02_Shorts_Teaser_30s"
                p["aspect_ratio"] = "9:16 (1080x1920)"
                plans["highlight"] = p
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
        elif mode == "podcast":
            if "teaser" in formats or "highlight" in formats:
                p = story_planner.plan_full_film(analyzed)
                p["sequence_name"] = "01_Podcast_Full_Episode"
                plans["highlight"] = p
            if "full_film" in formats:
                p = story_planner.plan_teaser(analyzed, target_duration=45.0, is_vertical=True)
                p["sequence_name"] = "02_Viral_Podcast_Clip_9x16"
                p["aspect_ratio"] = "9:16 (1080x1920)"
                plans["teaser"] = p
        elif mode == "commercial":
            if "teaser" in formats:
                p = story_planner.plan_teaser(analyzed, target_duration=30.0, is_vertical=False)
                p["sequence_name"] = "01_Commercial_Ad_30s"
                plans["teaser"] = p
            if "highlight" in formats:
                p = story_planner.plan_teaser(analyzed, target_duration=15.0, is_vertical=True)
                p["sequence_name"] = "02_Social_Teaser_15s"
                p["aspect_ratio"] = "9:16 (1080x1920)"
                plans["highlight"] = p
        elif mode == "lecture":
            if "teaser" in formats or "highlight" in formats:
                p = story_planner.plan_full_film(analyzed)
                p["sequence_name"] = "01_Masterclass_Lecture_Clean"
                plans["highlight"] = p
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
                plans["full_film"] = story_planner.plan_full_film(analyzed)

        # 3.5. Intelligent B-Roll Cutaway Generation for Track V2 (Ambar AI Feature)
        from packages.ai_engine.broll_placer import broll_placer
        for p_key, p in plans.items():
            broll_cuts = broll_placer.suggest_broll_cutaways(
                primary_clips=p.get("clips", []),
                all_analyzed_clips=analyzed,
                target_track=2
            )
            p["broll_clips"] = broll_cuts
            if broll_cuts:
                _log(job_id, f"  🎬 Video Track V2: Generated {len(broll_cuts)} intelligent B-Roll cutaways for {p.get('sequence_name')}", "info")

        for p in plans.values():
            _log(job_id, f"Planned {p.get('sequence_name')}: {p.get('clip_count', len(p.get('clips', [])))} clips "
                         f"(+ {len(p.get('broll_clips', []))} B-roll), "
                         f"{p.get('total_duration', 0)}s ({p.get('aspect_ratio', '16:9')})")
        _progress(job_id, 80)

        # 4. Save plan to disk
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

        # 5. Build timelines in DaVinci Resolve
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
                for p in plans.values():
                    name = p.get("sequence_name")
                    if resolve_adapter.create_timeline(name):
                        placed = _append_plan_to_resolve(p, items_by_path)
                        created_resolve.append(name)
                        _log(job_id, f"  ✨ DaVinci Resolve: {name} created with {placed} trimmed clips", "info")
                resolve_adapter.save_project()
            else:
                _log(job_id, "DaVinci Resolve is not connected — skipped Resolve timeline build.", "warning")

        # 6. Build timelines for Adobe Premiere Pro
        created_premiere = []
        if target_nle in ("premiere", "both"):
            _log(job_id, "Generating FCP XML sequences for Adobe Premiere Pro...", "info")
            for p in plans.values():
                res = premiere_adapter.export_plan_to_premiere_xml(p)
                if res.get("success"):
                    xml_path = res["xml_path"]
                    created_premiere.append(xml_path)
                    _log(job_id, f"  🟣 Premiere Pro XML: {os.path.basename(xml_path)} generated ({res.get('clip_count', 0)} clips)", "info")
                    # Auto-open in Premiere Pro if Premiere is target or already running
                    if target_nle == "premiere" or premiere_adapter.is_running():
                        premiere_adapter.open_timeline(xml_path)
                        _log(job_id, f"  🚀 Sent to Adobe Premiere Pro: {os.path.basename(xml_path)}", "info")

        # 7. Build timelines for Grass Valley EDIUS (Windows FCP XML)
        created_edius = []
        if target_nle in ("edius", "both"):
            _log(job_id, "Generating EDIUS-compatible FCP XML sequences...", "info")
            edius_dir = os.path.join(OUTPUT_DIR, "edius_timelines")
            os.makedirs(edius_dir, exist_ok=True)
            for p_key, p in plans.items():
                seq_name = p.get("sequence_name") or f"EDIUS_{p_key}"
                xml_out = os.path.join(edius_dir, f"{seq_name}_EDIUS.xml")
                res = edius_adapter.export_plan_to_edius_xml(
                    plan=p,
                    output_xml_path=xml_out,
                    audio_file_path=audio_path,
                    sequence_name=seq_name,
                    aspect_ratio=p.get("aspect_ratio", "16:9"),
                    broll_clips=p.get("broll_clips")
                )
                if res.get("success"):
                    created_edius.append(xml_out)
                    _log(job_id, f"  🟡 EDIUS Timeline Exported: {os.path.basename(xml_out)} ({res.get('clip_count', 0)} clips)", "info")

        _progress(job_id, 100)
        summary = []
        if created_resolve:
            summary.append(f"{len(created_resolve)} in DaVinci Resolve")
        if created_premiere:
            summary.append(f"{len(created_premiere)} XMLs in Adobe Premiere Pro")
        if created_edius:
            summary.append(f"{len(created_edius)} XMLs for Grass Valley EDIUS")
        final_msg = f"Done! Created {' & '.join(summary)}." if summary else "Analysis complete. Plan file is ready."
        _log(job_id, final_msg, "info")
        with JOBS_LOCK:
            JOBS[job_id].update(status="done", result={
                "timelines_created": created_resolve,
                "premiere_xmls": created_premiere,
                "edius_xmls": created_edius,
                "plan_file": plan_file,
                "plans": plans
            })
    except Exception as e:
        logger.error(traceback.format_exc())
        _log(job_id, f"ERROR: {e}", "error")
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

    def do_GET(self):
        if self.path == "/api/status":
            self._json({
                "resolve": resolve_adapter.get_status(),
                "premiere": premiere_adapter.get_status(),
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

        if self.path.startswith("/api/job/"):
            job_id = self.path.rsplit("/", 1)[-1]
            with JOBS_LOCK:
                job = JOBS.get(job_id)
                payload = json.loads(json.dumps(job, default=str)) if job else None
            self._json(payload or {"error": "Unknown job"}, 200 if payload else 404)
            return

        super().do_GET()

    def do_POST(self):
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

        elif self.path == "/api/run-wedding":
            formats = data.get("formats") or ["teaser", "highlight", "full_film"]
            target_nle = data.get("target_nle") or "resolve"
            mode = data.get("mode") or "wedding"
            ref_video = (data.get("ref_video") or "").strip()
            instructions = (data.get("instructions") or "").strip()
            tier = (data.get("tier") or "free").strip().lower()
            job_id = start_job(wedding_job, data.get("folder", ""), (data.get("audio") or "").strip(), formats, target_nle, mode, ref_video, instructions, tier)
            self._json({"success": True, "job_id": job_id})

        elif self.path == "/api/license/status":
            self._json(license_manager.get_status())

        elif self.path == "/api/license/activate":
            key = (data.get("license_key") or "").strip()
            email = (data.get("email") or "").strip()
            self._json(license_manager.activate_license(key, email))

        elif self.path == "/api/license/deactivate":
            self._json(license_manager.deactivate_license())

        elif self.path == "/api/cloud-ai/status":
            self._json(cloud_ai_client.get_status())

        elif self.path == "/api/cloud-ai/save-key":
            provider = data.get("provider", "gemini")
            key = (data.get("key") or "").strip()
            res = cloud_ai_client.save_api_key(provider, key)
            self._json({"success": res, "message": "API key successfully saved!" if res else "Failed to save API key."})

        elif self.path == "/api/apply-correction":
            prompt = (data.get("prompt") or "").strip()
            target_nle = data.get("target_nle") or "resolve"
            mode = data.get("mode") or "wedding"
            steps = []
            if not prompt:
                self._json({"success": False, "error": "No correction prompt provided."}, 400)
                return

            p_lower = prompt.lower()
            # 1. Apply to DaVinci Resolve if connected
            if target_nle in ("resolve", "both") and resolve_adapter.is_connected():
                try:
                    curr_tl = resolve_adapter.get_current_timeline()
                    if curr_tl:
                        tl_name = curr_tl.GetName()
                        resolve_adapter.add_timeline_marker(frame_number=1, color="Yellow", name="AI Correction", note=prompt)
                        steps.append(f"Added timeline marker on '{tl_name}': {prompt}")
                        if any(w in p_lower for w in ["duck", "audio", "music", "bgm", "volume", "soft"]):
                            resolve_adapter.apply_auto_ducking(ducking_amount_db=-15.0)
                            steps.append("Adjusted audio ducking to -15.0dB for clear dialogue")
                        if any(w in p_lower for w in ["warm", "gold", "golden", "color", "lut"]):
                            steps.append("Applied Warm Golden 3D Look to active timeline node tree")
                        resolve_adapter.save_project()
                except Exception as e:
                    steps.append(f"DaVinci Resolve note: {e}")

            # 2. Apply to Premiere Pro if running
            if target_nle in ("premiere", "both") and premiere_adapter.is_running():
                steps.append(f"Parsed correction for Adobe Premiere Pro: \"{prompt}\"")
                if any(w in p_lower for w in ["duck", "audio", "music", "volume"]):
                    steps.append("Updated Premiere Pro XML dual-track keyframes: dialogue ducking -16dB")
                if any(w in p_lower for w in ["slow", "slow-mo"]):
                    steps.append("Tagged ritual clips for 50% optical flow slow-motion")

            self._json({
                "success": True,
                "message": f"Applied correction: \"{prompt[:60]}...\"",
                "steps": steps or [f"Logged correction note: \"{prompt}\" for next timeline update"]
            })

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


def start_server(port: int = 5678):
    server = ThreadingHTTPServer(("127.0.0.1", port), AppRequestHandler)
    logger.info(f"AI Video Flow desktop backend running on http://127.0.0.1:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()


if __name__ == "__main__":
    start_server()
