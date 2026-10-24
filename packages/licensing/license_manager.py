"""
AI Video Flow - License client

The license server (flow.aiphotoflow.in) signs the license state with an Ed25519 key that only the
server holds. This app ships with the public key, so it can verify the token offline but can never
create or alter one. A token is valid for a few days; the app refreshes it online in the background.

Premium AI work also goes through the server (credits are deducted there, Claude calls are relayed
there), so patching this module cannot unlock anything that costs money.
"""

import os
import sys
import json
import time
import base64
import hashlib
import platform
import subprocess
import threading
import logging
import urllib.request
import urllib.error
from typing import Dict, Any, Optional, Tuple

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.exceptions import InvalidSignature

from packages.licensing.public_key import LICENSE_PUBLIC_KEY_B64

logger = logging.getLogger("ai_video_flow.licensing")

APP_VERSION = "1.1.2"
SERVER_URL = os.environ.get("AVF_SERVER_URL", "https://flow.aiphotoflow.in/index.php").rstrip("/")
_CONFIG_DIR = os.path.expanduser("~/.ai-video-flow")
_LICENSE_FILE = os.path.join(_CONFIG_DIR, "license.json")
_PUBLIC_KEY = Ed25519PublicKey.from_public_bytes(base64.b64decode(LICENSE_PUBLIC_KEY_B64))


def _hardware_id() -> str:
    system = platform.system()
    try:
        if system == "Darwin":
            out = subprocess.run(["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                                 capture_output=True, text=True, timeout=5).stdout
            for line in out.splitlines():
                if "IOPlatformUUID" in line:
                    return line.split("=")[-1].strip().strip('"')
        elif system == "Windows":
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography",
                                0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
                return winreg.QueryValueEx(k, "MachineGuid")[0]
        else:
            for p in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
                if os.path.isfile(p):
                    return open(p, encoding="utf-8").read().strip()
    except Exception as e:
        logger.debug(f"Hardware id lookup failed: {e}")
    import uuid
    return f"{platform.node()}|{uuid.getnode()}"


def get_machine_fingerprint() -> str:
    return hashlib.sha256(f"avf-v2|{_hardware_id()}".encode("utf-8")).hexdigest()[:32].upper()


def _b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


class LicenseManager:
    def __init__(self):
        self.machine_id = get_machine_fingerprint()
        self.server_url = SERVER_URL
        self._lock = threading.RLock()
        self._license_key = ""
        self._claims: Optional[Dict[str, Any]] = None
        self._last_error = ""
        os.makedirs(_CONFIG_DIR, exist_ok=True)
        self._load()

    # ---------------- token handling ----------------
    def _verify(self, token: str, signature: str) -> Optional[Dict[str, Any]]:
        try:
            payload = _b64url_decode(token)
            _PUBLIC_KEY.verify(_b64url_decode(signature), payload)
            claims = json.loads(payload.decode("utf-8"))
        except (InvalidSignature, ValueError, TypeError) as e:
            logger.warning(f"License token rejected: {e}")
            return None
        if claims.get("machine_id") != self.machine_id:
            logger.warning("License token belongs to another computer.")
            return None
        return claims

    def _load(self) -> None:
        if not os.path.isfile(_LICENSE_FILE):
            return
        try:
            with open(_LICENSE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return
        self._license_key = data.get("license_key", "")
        if data.get("token") and data.get("signature"):
            self._claims = self._verify(data["token"], data["signature"])

    def _store(self, license_key: str, token: str, signature: str) -> bool:
        claims = self._verify(token, signature)
        if not claims or claims.get("license_key") != license_key:
            self._last_error = "The server sent an invalid license token."
            return False
        with open(_LICENSE_FILE, "w", encoding="utf-8") as f:
            json.dump({"license_key": license_key, "token": token, "signature": signature}, f)
        self._license_key, self._claims, self._last_error = license_key, claims, ""
        return True

    def _post(self, path: str, body: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        req = urllib.request.Request(
            f"{self.server_url}{path}",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": f"AIVideoFlow/{APP_VERSION}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as he:
            try:
                data = json.loads(he.read().decode("utf-8"))
                data["_from_server"] = True  # a real answer from the license server, not a proxy/hosting error page
                return he.code, data
            except Exception:
                return he.code, {"success": False, "error": f"Server error (HTTP {he.code})"}
        except Exception as e:
            return 0, {"success": False, "error": f"Could not reach the license server: {e}"}

    def _claims_valid(self) -> bool:
        c = self._claims
        if not c or c.get("status") != "active":
            return False
        now = time.time()
        # Token must be current; a clock set far before the issue time is not trusted either
        return c.get("issued_at", 0) - 3600 <= now <= c.get("valid_until", 0)

    # ---------------- public API ----------------
    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            c = self._claims or {}
            valid = self._claims_valid()
            plan_type = c.get("plan_type", "free")
            unlimited = plan_type == "unlimited"
            ai_enabled = valid and plan_type in ("per_wedding", "unlimited")
            credits = int(c.get("credits", 0))
            return {
                # any paid plan: unlimited local editing
                "is_premium": valid,
                "local_unlimited": valid,
                # Smart AI packs only
                "ai_enabled": ai_enabled,
                "has_credits": ai_enabled and (unlimited or credits > 0),
                "license_key": self._license_key,
                "machine_id": self.machine_id,
                "client_name": c.get("client_name", ""),
                "client_email": c.get("client_email", ""),
                "plan_name": c.get("plan_name", "") if valid else "Free Edition",
                "plan_type": c.get("plan_type", "free") if valid else "free",
                "credits_remaining": credits if valid else 0,
                "unlimited": unlimited and valid,
                "expires_at": c.get("expires_at"),
                "token_valid_until": c.get("valid_until"),
                "needs_online_check": bool(self._license_key) and not valid,
                "error": self._last_error,
                "server_url": self.server_url,
                "buy_url": f"{self.server_url}/buy",
            }

    def activate_license(self, license_key: str, email: Optional[str] = None) -> Dict[str, Any]:
        key = (license_key or "").strip().upper()
        if not key:
            return {"success": False, "error": "Please enter your license key."}
        status, data = self._post("/api/v1/license/activate", {
            "license_key": key, "machine_id": self.machine_id,
            "app_version": APP_VERSION, "platform": platform.system(),
        })
        if status == 200 and data.get("success"):
            with self._lock:
                if self._store(key, data.get("token", ""), data.get("signature", "")):
                    return {"success": True, "message": "License activated on this computer.", "status": self.get_status()}
            return {"success": False, "error": self._last_error}
        return {"success": False, "error": data.get("error", "Activation failed.")}

    def refresh(self) -> Dict[str, Any]:
        """Re-validates the license online (status, credits, expiry)."""
        if not self._license_key:
            return {"success": False, "error": "No license on this computer."}
        status, data = self._post("/api/v1/license/refresh", {"license_key": self._license_key, "machine_id": self.machine_id})
        with self._lock:
            if status == 200 and data.get("success"):
                self._store(self._license_key, data.get("token", ""), data.get("signature", ""))
                return {"success": True, "status": self.get_status()}
            if status in (403, 404) and data.get("_from_server"):
                # Revoked, expired or moved to another computer: drop the cached token now
                self._claims = None
            self._last_error = data.get("error", "")
        return {"success": False, "error": self._last_error}

    def refresh_in_background(self) -> None:
        threading.Thread(target=self.refresh, daemon=True).start()

    def consume_wedding_credit(self, job_name: str = "Wedding") -> Dict[str, Any]:
        """Deducts one AI wedding credit on the server. Requires internet."""
        if not self._license_key:
            return {"success": False, "error": "Activate a license to use AI Smart Premium."}
        status, data = self._post("/api/v1/license/consume", {
            "license_key": self._license_key, "machine_id": self.machine_id, "job_name": job_name[:120],
        })
        with self._lock:
            if status == 200 and data.get("success"):
                self._store(self._license_key, data.get("token", ""), data.get("signature", ""))
                st = self.get_status()
                return {"success": True, "credits_remaining": st["credits_remaining"], "unlimited": st["unlimited"]}
        return {"success": False, "error": data.get("error", "Could not use a credit.")}

    def plans(self) -> Dict[str, Any]:
        """Public plan list for the Pricing page."""
        try:
            with urllib.request.urlopen(f"{self.server_url}/api/v1/plans", timeout=10) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            return {"success": False, "error": f"Could not load plans: {e}", "plans": [], "buy_url": f"{self.server_url}/buy"}

    def ai_credentials(self) -> Tuple[str, str]:
        """License key + machine id that the Claude relay on the server authenticates."""
        return self._license_key, self.machine_id

    def deactivate_license(self) -> Dict[str, Any]:
        with self._lock:
            try:
                os.remove(_LICENSE_FILE)
            except FileNotFoundError:
                pass
            self._license_key, self._claims, self._last_error = "", None, ""
        return {"success": True, "message": "License removed from this computer. To move it to another computer, ask the admin to reset the computer lock."}


license_manager = LicenseManager()
