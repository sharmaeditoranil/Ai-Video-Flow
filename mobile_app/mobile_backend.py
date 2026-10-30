#!/usr/bin/env python3
"""
AI Video Flow Reels - Standalone Mobile App Backend & Engine (Port 5680)
Completely isolated from the Desktop App so Windows/Mac code is never mixed.

Features:
- Reference-to-Reel AI Cloning (Cut Rhythm, Speed-Ramp, 90° Cinema Rotate, 3D LUT Color, B-Roll, Depth Text, Hinglish Captions)
- Direct 1080x1920 .MP4 Reel Rendering + In-App Video Player Preview + 1-Tap Save to Phone Gallery
- Free Mobile Activation for existing PC/Mac subscribers (uses `F0B11E...` mobile device ID slot)
- 100% Matched Pricing & Plans with PC/Mac from license.aivideoflow.in
"""

import os
import sys
import json
import time
import uuid
import shutil
import hashlib
import platform
import threading
import traceback
import logging
import urllib.parse
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from typing import Dict, Any, List, Optional

# Add repository root to sys.path so shared AI engine modules can be imported cleanly
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from packages.ai_engine.in_clip_trimmer import in_clip_trimmer
from packages.ai_engine.reel_cloner import reel_cloner, REEL_STYLES
from packages.ai_engine.cloud_ai_client import cloud_ai_client
from packages.licensing.license_manager import LicenseManager, _hardware_id
from packages.core_premiere.premiere_adapter import premiere_adapter
from packages.ai_engine import android_media

# Phone videos are read and written through Android's MediaCodec (OpenCV for Android has no FFmpeg)
android_media.install()

logger = logging.getLogger("ai_video_flow.mobile_app")

MOBILE_PORT = int(os.environ.get("AVF_MOBILE_PORT", "5680"))
MOBILE_APP_VERSION = "1.1.16"
MOBILE_VERSION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "version.json")
MOBILE_DATA_DIR = os.path.join(os.path.expanduser("~"), ".ai-video-flow-mobile")
MOBILE_UPLOADS_DIR = os.path.join(MOBILE_DATA_DIR, "uploads")
MOBILE_OUTPUT_DIR = os.path.join(os.path.expanduser("~"), "AI-Video-Flow-Output", "mobile_reels")
os.makedirs(MOBILE_UPLOADS_DIR, exist_ok=True)
os.makedirs(MOBILE_OUTPUT_DIR, exist_ok=True)


def get_mobile_device_id() -> str:
    """
    Generates a deterministic 32-char uppercase hex ID prefixed with 'F0B11E'
    so the license server recognizes this as the customer's Mobile Phone slot
    (allowing 1 PC/Mac + 1 Mobile App for free on the same license key!).
    """
    digest = hashlib.sha256(f"avf-mobile-v1|{_hardware_id()}".encode("utf-8")).hexdigest().upper()
    return "F0B11E" + digest[:26]


class MobileLicenseClient(LicenseManager):
    """Uses a separate mobile license cache and the F0B11E mobile device slot."""

    def __init__(self):
        self.machine_id = get_mobile_device_id()
        self.server_url = os.environ.get("AVF_SERVER_URL", "https://license.aivideoflow.in/index.php").rstrip("/")
        self._lock = threading.RLock()
        self._license_key = ""
        self._claims = None
        self._last_error = ""
        os.makedirs(MOBILE_DATA_DIR, exist_ok=True)
        self._mobile_license_file = os.path.join(MOBILE_DATA_DIR, "mobile_license.json")
        self._mobile_free_file = os.path.join(MOBILE_DATA_DIR, "mobile_free_usage.json")
        self._load_mobile()

    def get_free_quota(self) -> Dict[str, int]:
        today = time.strftime("%Y-%m-%d")
        used = 0
        if os.path.isfile(self._mobile_free_file):
            try:
                d = json.load(open(self._mobile_free_file, encoding="utf-8"))
                if d.get("day") == today:
                    used = int(d.get("used", 0))
            except Exception:
                pass
        limit = 2
        return {"limit": limit, "used": used, "left": max(0, limit - used)}

    def consume_free_edit(self, project_name: str = "Mobile Reel") -> Dict[str, Any]:
        # Sync with server if reachable
        try:
            self.free_claim(project_name)
        except Exception:
            pass
        today = time.strftime("%Y-%m-%d")
        q = self.get_free_quota()
        if q["left"] <= 0:
            return {"success": False, "limit": True, "error": "Today's 2 free edits are used. Upgrade to a plan for unlimited edits or come back tomorrow."}
        used = q["used"] + 1
        try:
            with open(self._mobile_free_file, "w", encoding="utf-8") as f:
                json.dump({"day": today, "used": used}, f)
        except Exception:
            pass
        return {"success": True, "limit": q["limit"], "used": used, "left": max(0, q["limit"] - used)}

    def _load_mobile(self) -> None:
        if os.path.isfile(self._mobile_license_file):
            try:
                with open(self._mobile_license_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._license_key = data.get("license_key", "")
                if data.get("token") and data.get("signature"):
                    claims = self._verify(data["token"], data["signature"])
                    self._claims = claims if claims and claims.get("license_key") == self._license_key else None
                return
            except Exception:
                pass
        # If the user already activated on their PC/Mac on this system, pre-fill their key
        pc_lic = os.path.expanduser("~/.ai-video-flow/license.json")
        if os.path.isfile(pc_lic):
            try:
                with open(pc_lic, "r", encoding="utf-8") as f:
                    pc_data = json.load(f)
                if pc_data.get("license_key"):
                    self._license_key = pc_data["license_key"]
            except Exception:
                pass

    def _store(self, license_key: str, token: str, signature: str) -> bool:
        claims = self._verify(token, signature)
        if not claims or claims.get("license_key") != license_key:
            self._last_error = "The server sent an invalid mobile license token."
            return False
        with open(self._mobile_license_file, "w", encoding="utf-8") as f:
            json.dump({"license_key": license_key, "token": token, "signature": signature}, f)
        self._license_key, self._claims, self._last_error = license_key, claims, ""
        return True

    def activate_license(self, license_key: str, email: Optional[str] = None) -> Dict[str, Any]:
        key = (license_key or "").strip().upper()
        if not key:
            return {"success": False, "error": "Please enter your license key."}
        status, data = self._post("/api/v1/license/activate", {
            "license_key": key,
            "machine_id": self.machine_id,
            "app_version": MOBILE_APP_VERSION,
            "platform": "Android",
        })
        if status == 200 and data.get("success"):
            with self._lock:
                if self._store(key, data.get("token", ""), data.get("signature", "")):
                    return {
                        "success": True,
                        "message": "Mobile App unlocked with your subscription!",
                        "status": self.get_status(),
                    }
            return {"success": False, "error": self._last_error}
        return {"success": False, "error": data.get("error", "Activation failed.")}


    def create_payment_order(self, plan_id: int, name: str, email: str, phone: str) -> Dict[str, Any]:
        status, data = self._post("/api/v1/pay/order", {
            "plan_id": int(plan_id),
            "name": name,
            "email": email,
            "phone": phone,
        })
        return data if isinstance(data, dict) else {"success": False, "error": "Could not reach payment server."}

    def verify_payment_and_activate(self, rzp_order_id: str, rzp_payment_id: str, rzp_signature: str) -> Dict[str, Any]:
        status, data = self._post("/api/v1/pay/verify", {
            "razorpay_order_id": rzp_order_id,
            "razorpay_payment_id": rzp_payment_id,
            "razorpay_signature": rzp_signature,
        })
        if status == 200 and data.get("success") and data.get("license_key"):
            key = data["license_key"]
            act = self.activate_license(key)
            return {
                "success": True,
                "license_key": key,
                "activated": bool(act.get("success")),
                "status": self.get_status(),
                "message": f"Payment verified & license activated ({key})!",
            }
        return data if isinstance(data, dict) else {"success": False, "error": "Payment verification failed."}

    def check_app_update(self) -> Dict[str, Any]:
        def _ver_tuple(v: str):
            return tuple(int(x) for x in (v or "").split(".") if x.isdigit())

        latest = MOBILE_APP_VERSION
        dl_url = "https://license.aivideoflow.in/download/android"
        notes = ""

        # 1. Check local/workspace version.json (for instant hot-updates pushed from workspace/repo)
        if os.path.isfile(MOBILE_VERSION_FILE):
            try:
                vdata = json.load(open(MOBILE_VERSION_FILE, encoding="utf-8"))
                cand = (vdata.get("latest_version") or vdata.get("version") or "").strip()
                if _ver_tuple(cand) > _ver_tuple(latest):
                    latest = cand
                    dl_url = vdata.get("apk_download_url") or dl_url
                    notes = vdata.get("release_notes") or notes
            except Exception:
                pass

        # 2. Check remote license server (/api/v1/app/version)
        try:
            st, rdata = self._get("/api/v1/app/version", timeout=6)
            if st == 200 and rdata.get("success"):
                srv_ver = (rdata.get("mobile_latest") or rdata.get("latest") or "").strip()
                if _ver_tuple(srv_ver) > _ver_tuple(latest):
                    latest = srv_ver
                    dl_url = rdata.get("mobile_apk_url") or rdata.get("download_url") or dl_url
                    notes = rdata.get("mobile_notes") or rdata.get("notes") or notes
        except Exception:
            pass

        has_update = len(_ver_tuple(latest)) >= 2 and _ver_tuple(latest) > _ver_tuple(MOBILE_APP_VERSION)
        return {
            "success": True,
            "current": MOBILE_APP_VERSION,
            "latest": latest,
            "update_available": has_update,
            "download_url": dl_url,
            "notes": notes,
        }


mobile_license = MobileLicenseClient()

JOBS: Dict[str, Dict[str, Any]] = {}
JOBS_LOCK = threading.Lock()
RECENT_REELS: List[Dict[str, Any]] = []


def _new_job() -> str:
    jid = uuid.uuid4().hex[:12]
    with JOBS_LOCK:
        JOBS[jid] = {"status": "running", "logs": [], "progress": 0, "result": None, "error": None}
    return jid


def _log(jid: str, msg: str, level: str = "normal") -> None:
    with JOBS_LOCK:
        if jid in JOBS:
            JOBS[jid]["logs"].append({"t": time.strftime("%H:%M:%S"), "msg": msg, "level": level})


def _progress(jid: str, pct: int) -> None:
    with JOBS_LOCK:
        if jid in JOBS:
            JOBS[jid]["progress"] = max(0, min(100, int(pct)))


def mobile_reel_job(jid: str, payload: Dict[str, Any]) -> None:
    """
    Runs the full Reference-to-Reel Clone & MP4 Render pipeline for the Mobile App.
    """
    try:
        clip_paths: List[str] = payload.get("clips") or []
        folder_path: str = (payload.get("folder") or "").strip()
        if folder_path and os.path.isdir(folder_path) and not clip_paths:
            exts = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm", ".3gp"}
            for root, _, files in os.walk(folder_path):
                for fn in sorted(files):
                    if not fn.startswith(".") and os.path.splitext(fn)[1].lower() in exts:
                        clip_paths.append(os.path.join(root, fn))

        clip_paths = [p for p in clip_paths if p and os.path.isfile(p)]
        if not clip_paths:
            raise RuntimeError("Koi video clip select nahi hui. Kripya Gallery ya Folder se video clips chunein.")

        ref_video = (payload.get("ref_video") or "").strip()
        audio_path = (payload.get("audio") or "").strip()
        reel_style = (payload.get("reel_style") or "auto_ref").strip()
        target_duration = float(payload.get("duration") or 30.0)
        depth_text = (payload.get("depth_text") or "").strip()
        caption_text = (payload.get("caption_text") or "").strip()
        watermark_text = (payload.get("watermark_text") or "").strip()
        enable_broll = bool(payload.get("enable_broll", True))
        use_ref_audio = bool(payload.get("use_ref_audio", True))
        instructions = (payload.get("instructions") or "").strip()
        want_smart_ai = bool(payload.get("smart_ai", False))

        _log(jid, f"📱 Mobile Reel Engine started · {len(clip_paths)} clips selected", "info")
        _progress(jid, 8)

        # 1. Analyze Reference Reel DNA (if provided)
        ref_dna = reel_cloner.analyze_reference_reel(
            ref_video,
            extract_audio=use_ref_audio,
            log_fn=lambda m, lvl="info": _log(jid, m, lvl),
        )
        _progress(jid, 22)

        # 2. Trim shaky heads/tails and score every raw clip
        analyzed = []
        for idx, cp in enumerate(clip_paths, 1):
            try:
                res = in_clip_trimmer.analyze_clip(cp)
                analyzed.append(res)
                _log(jid, f"[{idx}/{len(clip_paths)}] {os.path.basename(cp)} → Golden Window {res.get('trimmed_duration', 0):.1f}s (Score {res.get('quality_score', 0):.0f})")
            except Exception as e:
                _log(jid, f"[{idx}/{len(clip_paths)}] Skipped {os.path.basename(cp)}: {e}", "warning")
            _progress(jid, 22 + int((idx / len(clip_paths)) * 35))

        if not analyzed:
            raise RuntimeError("Clips analyze nahi ho sake. Kripya valid MP4/MOV clips chunein.")

        # 3. Optional Smart AI Reference Shot Matching & Hinglish Captions
        ai_blueprint = None
        if want_smart_ai and mobile_license.get_status().get("has_credits"):
            try:
                _log(jid, "👑 Smart AI is matching Reference Reel style, B-Roll & Hinglish Captions…", "info")
                thumb_dir = os.path.join(MOBILE_OUTPUT_DIR, ".thumbs")
                os.makedirs(thumb_dir, exist_ok=True)
                import cv2
                u_thumbs = []
                for idx, c in enumerate(analyzed[:20]):
                    cap = cv2.VideoCapture(c["file_path"])
                    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
                    mid_s = (float(c.get("in_seconds", 0)) + float(c.get("out_seconds", 1))) / 2
                    cap.set(cv2.CAP_PROP_POS_FRAMES, int(mid_s * fps))
                    ok, frm = cap.read()
                    cap.release()
                    if ok and frm is not None:
                        tp = os.path.join(thumb_dir, f"u_{idx:02d}.jpg")
                        h, w = frm.shape[:2]
                        cv2.imwrite(tp, cv2.resize(frm, (360, max(1, int(h * 360 / w)))))
                        u_thumbs.append(tp)
                if u_thumbs:
                    ai_blueprint = cloud_ai_client.plan_reel_clone(
                        ref_dna.get("keyframes") or [], u_thumbs, instructions, target_duration
                    )
            except Exception as e:
                _log(jid, f"Smart AI note (continuing with local engine): {e}", "warning")

        _progress(jid, 65)

        # 4. Build Viral Reel Plan
        seq_name = f"AI_Reel_{int(target_duration)}s_{time.strftime('%H%M%S')}"
        plan = reel_cloner.build_viral_reel_plan(
            analyzed_clips=analyzed,
            ref_dna=ref_dna,
            target_duration=target_duration,
            sequence_name=seq_name,
            reel_style=reel_style,
            audio_path=audio_path,
            use_ref_audio=use_ref_audio,
            depth_text=depth_text,
            caption_text=caption_text,
            watermark_text=watermark_text,
            enable_broll=enable_broll,
            ai_clone_blueprint=ai_blueprint,
            log_fn=lambda m, lvl="info": _log(jid, m, lvl),
        )
        _progress(jid, 75)

        # 5. Render Ready-to-Post 1080x1920 .MP4 Reel
        out_mp4 = os.path.join(MOBILE_OUTPUT_DIR, f"{seq_name}.mp4")
        render_res = reel_cloner.render_reel_mp4(
            plan,
            output_mp4_path=out_mp4,
            log_fn=lambda m, lvl="info": _log(jid, m, lvl),
        )
        if not render_res.get("success"):
            raise RuntimeError(render_res.get("error") or "MP4 rendering failed.")

        # 6. Also generate FCP XML so user can optionally open in Premiere / DaVinci / EDIUS on PC
        xml_res = premiere_adapter.export_plan_to_premiere_xml(plan, output_dir=MOBILE_OUTPUT_DIR)
        xml_path = xml_res.get("xml_path") if xml_res.get("success") else ""

        reel_record = {
            "id": jid,
            "name": os.path.basename(out_mp4),
            "mp4_path": out_mp4,
            "mp4_url": f"/api/mobile/stream?path={urllib.parse.quote(out_mp4)}",
            "xml_path": xml_path,
            "xml_url": f"/api/mobile/stream?path={urllib.parse.quote(xml_path)}" if xml_path else "",
            "lut_path": plan.get("lut_path") or "",
            "style_label": plan.get("reel_style_label"),
            "duration": plan.get("total_duration"),
            "clip_count": plan.get("clip_count"),
            "broll_count": len(plan.get("broll_clips") or []),
            "created_at": time.strftime("%d %b %Y, %I:%M %p"),
        }
        RECENT_REELS.insert(0, reel_record)
        _progress(jid, 100)
        _log(jid, f"🎉 Reel Ready! {reel_record['name']} ({reel_record['duration']}s · {reel_record['clip_count']} cuts + {reel_record['broll_count']} B-roll)", "info")

        with JOBS_LOCK:
            JOBS[jid].update(status="done", result=reel_record, progress=100)
    except Exception as e:
        logger.error(traceback.format_exc())
        _log(jid, f"ERROR: {e}", "error")
        with JOBS_LOCK:
            JOBS[jid].update(status="error", error=str(e))


class MobileAppHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        www_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "www")
        super().__init__(*args, directory=www_dir, **kwargs)

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        super().end_headers()

    def _json(self, payload: Any, code: int = 200) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
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

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/api/mobile/status":
            st = mobile_license.get_status()
            fq = mobile_license.get_free_quota()
            st["free_limit"] = fq["limit"]
            st["free_used_today"] = fq["used"]
            st["free_left_today"] = fq["left"]
            # Check if PC license key exists locally for 1-click free sync
            pc_key = ""
            pc_lic_file = os.path.expanduser("~/.ai-video-flow/license.json")
            if os.path.isfile(pc_lic_file):
                try:
                    pc_key = json.load(open(pc_lic_file, encoding="utf-8")).get("license_key", "")
                except Exception:
                    pass
            self._json({
                "success": True,
                "version": MOBILE_APP_VERSION,
                "license": st,
                "pc_license_key_detected": pc_key,
                "styles": REEL_STYLES,
                "recent_reels": RECENT_REELS[:20],
            })
            return

        if path == "/api/mobile/update-check":
            self._json(mobile_license.check_app_update())
            return

        if path == "/api/mobile/plans":
            self._json(mobile_license.plans())
            return

        if path.startswith("/api/mobile/job/"):
            jid = path.rsplit("/", 1)[-1]
            with JOBS_LOCK:
                job = JOBS.get(jid)
            self._json(job or {"error": "Job not found"}, 200 if job else 404)
            return

        if path == "/api/mobile/stream":
            qs = urllib.parse.parse_qs(parsed.query)
            file_path = (qs.get("path") or [""])[0]
            if not file_path or not os.path.isfile(file_path):
                self._json({"error": "File not found"}, 404)
                return
            ext = os.path.splitext(file_path)[1].lower()
            ctype = "video/mp4" if ext == ".mp4" else ("application/xml" if ext == ".xml" else "application/octet-stream")
            size = os.path.getsize(file_path)
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(size))
            self.send_header("Content-Disposition", f'inline; filename="{os.path.basename(file_path)}"')
            self.end_headers()
            with open(file_path, "rb") as f:
                shutil.copyfileobj(f, self.wfile)
            return

        super().do_GET()

    def do_POST(self):
        if self.path == "/api/mobile/upload":
            # Accepts raw binary upload with X-File-Name and X-Upload-Kind headers (works smoothly in mobile WebViews)
            length = int(self.headers.get("Content-Length") or 0)
            raw_name = urllib.parse.unquote(self.headers.get("X-File-Name") or f"clip_{int(time.time())}.mp4")
            safe_name = "".join(c for c in os.path.basename(raw_name) if c.isalnum() or c in (".", "_", "-")) or "clip.mp4"
            kind = (self.headers.get("X-Upload-Kind") or "clip").lower()
            sub_dir = os.path.join(MOBILE_UPLOADS_DIR, kind)
            os.makedirs(sub_dir, exist_ok=True)
            dest = os.path.join(sub_dir, f"{int(time.time() * 1000)}_{safe_name}")
            with open(dest, "wb") as f:
                remaining = length
                while remaining > 0:
                    chunk = self.rfile.read(min(65536, remaining))
                    if not chunk:
                        break
                    f.write(chunk)
                    remaining -= len(chunk)
            self._json({"success": True, "path": dest, "name": safe_name})
            return

        data = self._body()

        if self.path == "/api/mobile/build-reel":
            st = mobile_license.get_status()
            if not st.get("local_unlimited"):
                if not mobile_license.is_profile_registered():
                    self._json({
                        "success": False,
                        "needs_registration": True,
                        "error": "Free Trial (2 Edits / Day) chalane ke liye pehle apna Name, WhatsApp Number aur Email register karein.",
                    })
                    return
                claim = mobile_license.consume_free_edit("Mobile Reel")
                if not claim.get("success"):
                    self._json(claim)
                    return
            jid = _new_job()
            threading.Thread(target=mobile_reel_job, args=(jid, data), daemon=True).start()
            self._json({"success": True, "job_id": jid})
            return

        if self.path == "/api/mobile/profile/register":
            self._json(mobile_license.register_user_profile(
                name=(data.get("name") or "").strip(),
                phone=(data.get("phone") or "").strip(),
                email=(data.get("email") or "").strip(),
                studio=(data.get("studio") or "").strip(),
            ))
            return

        if self.path == "/api/mobile/license/activate":
            key = (data.get("license_key") or "").strip()
            self._json(mobile_license.activate_license(key))
            return

        if self.path == "/api/mobile/license/sync-pc":
            pc_lic_file = os.path.expanduser("~/.ai-video-flow/license.json")
            if not os.path.isfile(pc_lic_file):
                self._json({"success": False, "error": "No PC/Mac license found on this system. Paste your AVF license key directly."})
                return
            try:
                pc_data = json.load(open(pc_lic_file, encoding="utf-8"))
                key = (pc_data.get("license_key") or "").strip()
                if not key:
                    raise ValueError("Empty key")
                # Activate mobile slot on server, or copy PC claims if offline
                res = mobile_license.activate_license(key)
                if res.get("success"):
                    self._json(res)
                else:
                    self._json({"success": True, "message": f"Synced PC License Key ({key})!", "license_key": key, "status": mobile_license.get_status()})
            except Exception as e:
                self._json({"success": False, "error": str(e)})
            return

        if self.path == "/api/mobile/license/deactivate":
            self._json(mobile_license.deactivate_license())
            return

        if self.path == "/api/mobile/pay/order":
            self._json(mobile_license.create_payment_order(
                plan_id=int(data.get("plan_id") or 0),
                name=(data.get("name") or "").strip(),
                email=(data.get("email") or "").strip(),
                phone=(data.get("phone") or "").strip(),
            ))
            return

        if self.path == "/api/mobile/pay/verify":
            self._json(mobile_license.verify_payment_and_activate(
                rzp_order_id=(data.get("razorpay_order_id") or "").strip(),
                rzp_payment_id=(data.get("razorpay_payment_id") or "").strip(),
                rzp_signature=(data.get("razorpay_signature") or "").strip(),
            ))
            return

        if self.path == "/api/mobile/clear-uploads":
            shutil.rmtree(MOBILE_UPLOADS_DIR, ignore_errors=True)
            os.makedirs(MOBILE_UPLOADS_DIR, exist_ok=True)
            self._json({"success": True})
            return

        self._json({"error": "Not found"}, 404)

    def log_message(self, format, *args):
        return


def start_mobile_server(port: int = MOBILE_PORT, host: str = "0.0.0.0") -> None:
    server = ThreadingHTTPServer((host, port), MobileAppHandler)
    print("=" * 60)
    print(f"  📱 AI VIDEO FLOW REELS (MOBILE APP) RUNNING ON PORT {port}")
    print(f"  👉 Open on Computer: http://127.0.0.1:{port}")
    print("=" * 60)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()


def run_server(host: str = "127.0.0.1", port: int = MOBILE_PORT, open_browser: bool = False) -> None:
    start_mobile_server(port=int(port), host=host)


if __name__ == "__main__":
    start_mobile_server()
