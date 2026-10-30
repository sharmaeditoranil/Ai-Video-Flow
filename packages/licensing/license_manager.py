"""
AI Video Flow - License client

The license server (license.aivideoflow.in) signs the license state with an Ed25519 key that only the
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
import ssl
import urllib.request
import urllib.error
from typing import Dict, Any, Optional, Tuple

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.exceptions import InvalidSignature

from packages.licensing.public_key import LICENSE_PUBLIC_KEY_B64

logger = logging.getLogger("ai_video_flow.licensing")


def _get_ssl_context() -> ssl.SSLContext:
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        pass
    try:
        return ssl.create_default_context()
    except Exception:
        return ssl._create_unverified_context()


def _urlopen(url_or_req, timeout=20):
    ctx = _get_ssl_context()
    try:
        return urllib.request.urlopen(url_or_req, timeout=timeout, context=ctx)
    except urllib.error.URLError as e:
        err_str = str(e)
        if "CERTIFICATE_VERIFY_FAILED" in err_str or "certificate verify failed" in err_str.lower():
            logger.warning("Local SSL certificate verification failed; using unverified context for license server (payload is Ed25519-signed).")
            try:
                unverified_ctx = ssl._create_unverified_context()
                return urllib.request.urlopen(url_or_req, timeout=timeout, context=unverified_ctx)
            except Exception:
                pass
        raise

APP_VERSION = "1.1.15"
SERVER_URL = os.environ.get("AVF_SERVER_URL", "https://license.aivideoflow.in/index.php").rstrip("/")
RELEASES_PAGE = "https://github.com/sharmaeditoranil/Ai-Video-Flow/releases/latest"
RELEASES_API = "https://api.github.com/repos/sharmaeditoranil/Ai-Video-Flow/releases/latest"
_CONFIG_DIR = os.path.expanduser("~/.ai-video-flow")
_LICENSE_FILE = os.path.join(_CONFIG_DIR, "license.json")
_PROFILE_FILE = os.path.join(_CONFIG_DIR, "user_profile.json")
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
        if claims.get("v") != 1 or not claims.get("license_key"):
            return None  # e.g. a free-project grant is never a license
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
            claims = self._verify(data["token"], data["signature"])
            self._claims = claims if claims and claims.get("license_key") == self._license_key else None

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
            with _urlopen(req, timeout=20) as resp:
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

    # ---------------- user profile registration (mandatory for Free Trial across Mobile, Mac & Windows) ----------------
    def get_user_profile(self) -> Dict[str, str]:
        pfile = getattr(self, "_profile_file", _PROFILE_FILE)
        for candidate in (pfile, _PROFILE_FILE):
            if os.path.isfile(candidate):
                try:
                    with open(candidate, "r", encoding="utf-8") as f:
                        d = json.load(f)
                    if isinstance(d, dict) and d.get("phone") and d.get("email"):
                        return {
                            "name": str(d.get("name") or "").strip(),
                            "phone": str(d.get("phone") or "").strip(),
                            "email": str(d.get("email") or "").strip(),
                            "studio": str(d.get("studio") or "").strip(),
                        }
                except Exception:
                    pass
        return {"name": "", "phone": "", "email": "", "studio": ""}

    def is_profile_registered(self) -> bool:
        if self._claims_valid():
            return True
        prof = self.get_user_profile()
        digits = "".join(ch for ch in prof.get("phone", "") if ch.isdigit())
        return bool(prof.get("name") and len(digits) >= 10 and "@" in prof.get("email", ""))

    def register_user_profile(self, name: str, phone: str, email: str, studio: str = "", platform_name: str = "") -> Dict[str, Any]:
        name = (name or "").strip()
        email = (email or "").strip().lower()
        digits = "".join(ch for ch in (phone or "") if ch.isdigit() or ch == "+")
        raw_digits = "".join(ch for ch in digits if ch.isdigit())
        studio = (studio or "").strip()
        if not name or len(raw_digits) < 10 or "@" not in email or "." not in email.split("@")[-1]:
            return {
                "success": False,
                "error": "Please enter your Full Name, 10-digit WhatsApp Number, and valid Email Address.",
            }
        prof = {"name": name, "phone": digits, "email": email, "studio": studio}
        pfile = getattr(self, "_profile_file", _PROFILE_FILE)
        os.makedirs(os.path.dirname(pfile), exist_ok=True)
        with open(pfile, "w", encoding="utf-8") as f:
            json.dump(prof, f)
        # Also mirror to shared desktop profile so registering once on the same machine syncs both
        try:
            os.makedirs(_CONFIG_DIR, exist_ok=True)
            with open(_PROFILE_FILE, "w", encoding="utf-8") as f:
                json.dump(prof, f)
        except Exception:
            pass

        plat = platform_name or ("Android" if str(self.machine_id).startswith("F0B11E") else platform.system())
        status, data = self._post("/api/v1/user/register", {
            "machine_id": self.machine_id,
            "name": name,
            "phone": digits,
            "email": email,
            "studio": studio,
            "platform": plat,
        })
        if status == 200 and data.get("license_key") and not self._license_key:
            self._license_key = data.get("license_key")
        return {
            "success": True,
            "synced_server": bool(status == 200 and data.get("success")),
            "license_key": data.get("license_key", ""),
            "user_profile": prof,
            "status": self.get_status(),
            "message": "Profile saved! You can now use your 2 Free Edits per day.",
        }

    # ---------------- public API ----------------
    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            c = self._claims or {}
            valid = self._claims_valid()
            prof = self.get_user_profile()
            registered = self.is_profile_registered()
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
                "client_name": c.get("client_name") or prof.get("name", ""),
                "client_email": c.get("client_email") or prof.get("email", ""),
                "client_phone": prof.get("phone", ""),
                "client_studio": prof.get("studio", ""),
                "registered": registered,
                "user_profile": prof,
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

    # ---------------- free plan (counted on the server, per computer per day) ----------------
    def free_status(self) -> Dict[str, Any]:
        status, data = self._post("/api/v1/free/status", {"machine_id": self.machine_id})
        if status == 200 and data.get("success"):
            if isinstance(data.get("user"), dict) and data["user"].get("phone") and not self.is_profile_registered():
                u = data["user"]
                try:
                    pfile = getattr(self, "_profile_file", _PROFILE_FILE)
                    with open(pfile, "w", encoding="utf-8") as f:
                        json.dump({
                            "name": u.get("name", ""),
                            "phone": u.get("phone", ""),
                            "email": u.get("email", ""),
                            "studio": u.get("studio", ""),
                        }, f)
                except Exception:
                    pass
            if data.get("license_key") and not self._license_key:
                self._license_key = data.get("license_key")
            return data
        return {"success": False, "offline": status == 0,
                "error": "The free plan needs an internet connection to start a project." if status == 0 else data.get("error", "")}

    def free_claim(self, job_name: str) -> Dict[str, Any]:
        """Uses one of today's 2 free projects. Requires profile registration (Name, WhatsApp Number, Email)."""
        if not self.is_profile_registered():
            return {
                "success": False,
                "needs_registration": True,
                "error": "Free Trial (2 Edits / Day) chalane ke liye pehle My Profile me apna Name, WhatsApp Number aur Email save karein.",
            }
        prof = self.get_user_profile()
        plat = "Android" if str(self.machine_id).startswith("F0B11E") else platform.system()
        # Sync lead to Admin Dashboard users table as well
        try:
            self._post("/api/v1/user/register", {
                "machine_id": self.machine_id,
                "name": prof.get("name", ""),
                "phone": prof.get("phone", ""),
                "email": prof.get("email", ""),
                "studio": prof.get("studio", ""),
                "platform": plat,
            })
        except Exception:
            pass

        status, data = self._post("/api/v1/free/claim", {
            "machine_id": self.machine_id,
            "job_name": job_name[:120],
            "user_profile": {**prof, "platform": plat},
        })
        if status == 0:
            return {"success": False, "error": "The free plan needs an internet connection to finish a project."}
        if status != 200 or not data.get("success"):
            return {
                "success": False,
                "needs_registration": bool(data.get("needs_registration")),
                "error": data.get("error", "Could not start a free project."),
                "limit": not data.get("needs_registration"),
            }
        try:
            payload = _b64url_decode(data.get("grant", ""))
            _PUBLIC_KEY.verify(_b64url_decode(data.get("signature", "")), payload)
            grant = json.loads(payload.decode("utf-8"))
        except (InvalidSignature, ValueError, TypeError):
            return {"success": False, "error": "The server sent an invalid free-project permission."}
        if grant.get("v") != "free" or grant.get("machine_id") != self.machine_id or float(grant.get("valid_until", 0)) < time.time():
            return {"success": False, "error": "The free-project permission is not valid for this computer."}
        return {"success": True, "left": max(0, int(grant["limit"]) - int(grant["used"])), "limit": int(grant["limit"])}

    def plans(self) -> Dict[str, Any]:
        """Public plan list for the Pricing page."""
        try:
            with _urlopen(f"{self.server_url}/api/v1/plans", timeout=10) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            return {"success": False, "error": f"Could not load plans: {e}", "plans": [], "buy_url": f"{self.server_url}/buy"}

    def update_info(self) -> Dict[str, Any]:
        """Newer app version, if any: the admin portal's setting first, else the latest GitHub release."""
        from packages.licensing.updater import platform_key, can_auto_install
        latest, url, notes, asset = "", "", "", None
        try:
            with _urlopen(f"{self.server_url}/api/v1/app/version", timeout=10) as resp:
                d = json.loads(resp.read().decode("utf-8"))
                latest, url, notes = d.get("latest") or "", d.get("download_url") or "", d.get("notes") or ""
            # Auto-update installer links only count when the server signed them (the connection may be unverified)
            if d.get("manifest"):
                payload = _b64url_decode(d["manifest"])
                _PUBLIC_KEY.verify(_b64url_decode(d.get("manifest_signature", "")), payload)
                m = json.loads(payload.decode("utf-8"))
                if m.get("v") == "update" and m.get("version") == latest:
                    asset = (m.get("assets") or {}).get(platform_key()) or None
        except Exception:
            pass
        if not latest:
            try:
                req = urllib.request.Request(RELEASES_API, headers={"User-Agent": f"AIVideoFlow/{APP_VERSION}"})
                with _urlopen(req, timeout=10) as resp:
                    d = json.loads(resp.read().decode("utf-8"))
                    latest, url = (d.get("tag_name") or "").lstrip("vV"), d.get("html_url") or ""
            except Exception as e:
                return {"available": False, "current": APP_VERSION, "error": str(e)}

        def ver(v: str) -> Tuple[int, ...]:
            try:
                return tuple(int(x) for x in v.split("."))
            except ValueError:
                return (0,)
        return {"available": bool(latest) and ver(latest) > ver(APP_VERSION), "current": APP_VERSION,
                "latest": latest, "url": url if url.startswith("https://") else RELEASES_PAGE, "notes": notes,
                "asset": asset, "auto": bool(asset) and can_auto_install()}

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
