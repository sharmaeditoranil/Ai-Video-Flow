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
from packages.hyperframes_engine.hyperframes_service import hyperframes_service
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


def wedding_job(job_id: str, folder: str, audio_path: str, formats: List[str]) -> None:
    try:
        from packages.ai_engine.in_clip_trimmer import in_clip_trimmer
        from packages.ai_engine.beat_analyzer import beat_analyzer
        from packages.ai_engine.story_planner import story_planner

        _log(job_id, f"Scanning footage folder: {folder}", "info")
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
                _log(job_id, f"[{i}/{len(videos)}] {os.path.basename(vp)} → keep "
                             f"{res.get('in_seconds', 0):.1f}s–{res.get('out_seconds', 0):.1f}s "
                             f"(score {res.get('quality_score', 0):.0f})")
            except Exception as e:
                _log(job_id, f"[{i}/{len(videos)}] Skipped {os.path.basename(vp)}: {e}", "warning")
            _progress(job_id, int(i / len(videos) * 70))

        if not analyzed:
            raise RuntimeError("None of the clips could be analysed.")

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
        _progress(job_id, 75)

        # 3. Story plans
        plans = {}
        if "teaser" in formats:
            plans["teaser"] = story_planner.plan_teaser(analyzed)
        if "highlight" in formats:
            plans["highlight"] = story_planner.plan_highlight(analyzed, beat_times=beat_times)
        if "full_film" in formats:
            plans["full_film"] = story_planner.plan_full_film(analyzed)
        for p in plans.values():
            _log(job_id, f"Planned {p.get('sequence_name')}: {p.get('clip_count', len(p.get('clips', [])))} clips, "
                         f"{p.get('total_duration', 0)}s")
        _progress(job_id, 80)

        # 4. Save plan to disk
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        plan_file = os.path.join(OUTPUT_DIR, f"wedding_plan_{time.strftime('%Y%m%d_%H%M%S')}.json")
        with open(plan_file, "w", encoding="utf-8") as f:
            json.dump({"folder": folder, "audio": audio_path, "analyzed": analyzed, "plans": plans}, f, indent=2, default=str)
        _log(job_id, f"Edit plan saved: {plan_file}", "info")

        # 5. Build timelines in Resolve
        created = []
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
            _log(job_id, f"Imported {len(items)} clips.")
            for p in plans.values():
                name = p.get("sequence_name")
                if resolve_adapter.create_timeline(name):
                    placed = _append_plan_to_resolve(p, items_by_path)
                    created.append(name)
                    _log(job_id, f"  ✨ {name} created with {placed} trimmed clips", "info")
            resolve_adapter.save_project()
        else:
            _log(job_id, "DaVinci Resolve is not connected — timelines not built. Open Resolve with a project "
                         "(External scripting = Local) and run again. Plan file is saved.", "warning")

        _progress(job_id, 100)
        _log(job_id, "Done! Open DaVinci Resolve to review your timelines." if created else "Analysis complete.", "info")
        with JOBS_LOCK:
            JOBS[job_id].update(status="done", result={"timelines_created": created, "plan_file": plan_file, "plans": plans})
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
                "hyperframes": hyperframes_service.is_available(),
                "claude": ConnectAllEngine(root_dir).check_status()
            })
            return

        if self.path == "/api/version":
            self._json({"app": "ai-video-flow", "version": APP_VERSION})
            return

        if self.path == "/api/tools":
            self._json(list_tools())
            return

        if self.path == "/api/diagnostics":
            self._json({
                "environment": dependency_report(),
                "resolve": resolve_adapter.get_status(),
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
            job_id = start_job(wedding_job, data.get("folder", ""), (data.get("audio") or "").strip(), formats)
            self._json({"success": True, "job_id": job_id})

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
