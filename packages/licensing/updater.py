"""
AI Video Flow - Auto update

The license server publishes the latest version with one installer link per platform in a manifest
signed with the same Ed25519 key as licenses (see LicenseManager.update_info). This module downloads
that installer in the background over strictly verified HTTPS, checks its SHA-256, and on "Restart &
Update" hands it to a small helper script that waits for the app to quit, installs it, and reopens it.

Only the standalone builds (PyInstaller .app / Windows installer) can update themselves; a source
install keeps the old "Download" link.
"""

import os
import sys
import ssl
import glob
import shutil
import hashlib
import platform
import tempfile
import threading
import subprocess
import urllib.request
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger("ai_video_flow.updater")

FROZEN = getattr(sys, "frozen", False)
_UPDATES_DIR = os.path.join(os.path.expanduser("~/.ai-video-flow"), "updates")


def platform_key() -> str:
    """Manifest asset name for this build: windows, mac-arm64 or mac-x86_64."""
    system = platform.system()
    if system == "Windows":
        return "windows"
    if system == "Darwin":
        return "mac-" + ("arm64" if platform.machine() == "arm64" else "x86_64")
    return system.lower()


def _mac_app_bundle() -> str:
    # sys.executable is ".../AI Video Flow.app/Contents/MacOS/AI Video Flow"
    bundle = os.path.abspath(os.path.join(os.path.dirname(sys.executable), "..", ".."))
    return bundle if bundle.endswith(".app") else ""


def can_auto_install() -> bool:
    if not FROZEN:
        return False
    system = platform.system()
    if system == "Windows":
        return True
    if system == "Darwin":
        bundle = _mac_app_bundle()
        # A copy run straight from the DMG (read-only) or a folder the user can't write needs the manual route
        return bool(bundle) and os.access(os.path.dirname(bundle), os.W_OK) and os.access(bundle, os.W_OK)
    return False


def _strict_ssl() -> ssl.SSLContext:
    # Never the unverified fallback the license calls use: this file gets executed
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class Updater:
    def __init__(self):
        self._lock = threading.Lock()
        self._state: Dict[str, Any] = {"state": "idle", "version": "", "progress": 0, "error": ""}
        self._path = ""

    def status(self) -> Dict[str, Any]:
        with self._lock:
            return {**self._state, "auto": can_auto_install()}

    def start_download(self, info: Dict[str, Any]) -> Dict[str, Any]:
        """Starts downloading the installer named in a verified update_info(); safe to call repeatedly."""
        asset = info.get("asset") or {}
        version = info.get("latest", "")
        if not (info.get("available") and asset.get("url") and can_auto_install()):
            return {"success": False, "error": "Automatic update is not available for this installation."}
        if not asset["url"].startswith("https://"):
            return {"success": False, "error": "The update link is not secure."}
        with self._lock:
            if self._state["version"] == version and self._state["state"] in ("downloading", "ready", "installing"):
                return {"success": True, **self._state}
            self._state = {"state": "downloading", "version": version, "progress": 0, "error": ""}
        threading.Thread(target=self._download, args=(version, asset), daemon=True).start()
        return {"success": True, **self.status()}

    def _set(self, **kw) -> None:
        with self._lock:
            self._state.update(kw)

    def _download(self, version: str, asset: Dict[str, str]) -> None:
        from packages.licensing.license_manager import APP_VERSION
        ext = ".exe" if platform.system() == "Windows" else ".dmg"
        folder = os.path.join(_UPDATES_DIR, version)
        target = os.path.join(folder, f"AI-Video-Flow-{version}{ext}")
        expected = (asset.get("sha256") or "").strip().lower()
        try:
            # Drop installers of other versions
            for old in glob.glob(os.path.join(_UPDATES_DIR, "*")):
                if os.path.abspath(old) != os.path.abspath(folder):
                    shutil.rmtree(old, ignore_errors=True)
            os.makedirs(folder, exist_ok=True)

            if not (os.path.exists(target) and expected and _sha256(target) == expected):
                part = target + ".part"
                req = urllib.request.Request(asset["url"], headers={"User-Agent": f"AIVideoFlow/{APP_VERSION}"})
                with urllib.request.urlopen(req, timeout=60, context=_strict_ssl()) as resp, open(part, "wb") as out:
                    total = int(resp.headers.get("Content-Length") or 0)
                    done = 0
                    for chunk in iter(lambda: resp.read(1 << 20), b""):
                        out.write(chunk)
                        done += len(chunk)
                        if total:
                            self._set(progress=min(99, int(done * 100 / total)))
                if expected and _sha256(part) != expected:
                    os.remove(part)
                    raise ValueError("The downloaded file failed the security check (SHA-256 mismatch).")
                os.replace(part, target)

            self._path = target
            self._set(state="ready", progress=100)
            logger.info("Update %s downloaded to %s", version, target)
        except Exception as e:
            logger.warning("Update download failed: %s", e)
            self._set(state="error", error=str(e))

    def install(self) -> Dict[str, Any]:
        """Launches the helper that installs the downloaded update once this app quits, then quits."""
        with self._lock:
            if self._state["state"] != "ready" or not os.path.exists(self._path):
                return {"success": False, "error": "The update is not downloaded yet."}
            self._state["state"] = "installing"
        try:
            if platform.system() == "Windows":
                self._spawn_windows()
            else:
                self._spawn_mac()
        except Exception as e:
            self._set(state="error", error=f"Could not start the installer: {e}")
            return {"success": False, "error": str(e)}
        # Give the HTTP reply time to reach the window, then quit so the helper can replace the files
        threading.Timer(1.5, lambda: os._exit(0)).start()
        return {"success": True}

    def _spawn_windows(self) -> None:
        app_dir = os.path.dirname(sys.executable)
        exe = os.path.join(app_dir, os.path.basename(sys.executable))
        # Program Files needs the installer's elevated (all users) mode; a per-user install does not
        all_users = "" if os.access(app_dir, os.W_OK) else " /ALLUSERS"
        script = os.path.join(tempfile.gettempdir(), "avf_update.cmd")
        with open(script, "w", encoding="utf-8") as f:
            f.write(
                "@echo off\r\n"
                ":wait\r\n"
                f'tasklist /FI "PID eq {os.getpid()}" 2>nul | find "{os.getpid()}" >nul && (ping -n 2 127.0.0.1 >nul & goto wait)\r\n'
                f'"{self._path}" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS{all_users} /DIR="{app_dir}"\r\n'
                f'start "" "{exe}"\r\n'
            )
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        subprocess.Popen(["cmd.exe", "/c", script], creationflags=flags, close_fds=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _spawn_mac(self) -> None:
        bundle = _mac_app_bundle()
        log = os.path.join(os.path.expanduser("~/.ai-video-flow"), "update.log")
        script = os.path.join(tempfile.gettempdir(), "avf_update.sh")

        def q(s: str) -> str:
            return "'" + s.replace("'", "'\\''") + "'"

        with open(script, "w", encoding="utf-8") as f:
            f.write(f"""#!/bin/bash
PID={os.getpid()}; DMG={q(self._path)}; APP={q(bundle)}
while kill -0 "$PID" 2>/dev/null; do sleep 0.5; done
MNT="$(mktemp -d /tmp/avf_update.XXXXXX)"
if ! hdiutil attach -nobrowse -readonly -noautoopen -mountpoint "$MNT" "$DMG"; then
    open "$DMG"; exit 1
fi
NEW="$MNT/AI Video Flow.app"
if [ -d "$NEW" ] && rm -rf "$APP.new" && ditto "$NEW" "$APP.new"; then
    rm -rf "$APP.old"
    mv "$APP" "$APP.old" && mv "$APP.new" "$APP" && rm -rf "$APP.old" || {{ [ -d "$APP" ] || mv "$APP.old" "$APP"; }}
    xattr -dr com.apple.quarantine "$APP" 2>/dev/null
    hdiutil detach "$MNT" -quiet
    open "$APP"
else
    rm -rf "$APP.new"
    hdiutil detach "$MNT" -quiet
    open "$DMG"
fi
""")
        os.chmod(script, 0o700)
        with open(log, "a") as out:
            subprocess.Popen(["/bin/bash", script], start_new_session=True, close_fds=True,
                             stdin=subprocess.DEVNULL, stdout=out, stderr=out)


updater = Updater()
