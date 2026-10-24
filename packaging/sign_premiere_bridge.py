"""
Signs the Premiere Pro bridge extension (packages/core_premiere/cep_bridge) into a ZXP package.

Premiere loads a signed CEP extension without any debug setting. A self-signed certificate is
enough for CEP, so a fresh one is made on every build - nothing secret is stored. Adobe advises
signing on the platform the package will run on, so the Mac and Windows builds each run this.

Output: packages/core_premiere/aivideoflow_bridge.zxp (bundled into the app by the PyInstaller spec)
Usage:  python packaging/sign_premiere_bridge.py
"""

import os
import sys
import shutil
import secrets
import hashlib
import platform
import subprocess
import tempfile
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, "packages", "core_premiere", "cep_bridge")
OUTPUT = os.path.join(ROOT, "packages", "core_premiere", "aivideoflow_bridge.zxp")
TOOLS = os.path.join(ROOT, "build", "tools")

# Adobe's official ZXPSignCmd 4.1.3 (github.com/Adobe-CEP/CEP-Resources), pinned by checksum
BASE = "https://raw.githubusercontent.com/Adobe-CEP/CEP-Resources/master/ZXPSignCMD/4.1.3"
TOOL = {
    "Darwin": ("macOS/ZXPSignCmd", "ZXPSignCmd", "bc773fae0b97416fc7a462e7dadcc00270428a9913480c9b78b5606ff1cfb095"),
    "Windows": ("x64/ZXPSignCmd.exe", "ZXPSignCmd.exe", "ffc2223167225ce61d024eb463fc5ad1a1be16133f99ef334a646f7311916c98"),
}
TIMESTAMP_SERVERS = ["http://timestamp.digicert.com", "http://timestamp.sectigo.com"]


def tool_path() -> str:
    remote, name, sha = TOOL[platform.system()]
    path = os.path.join(TOOLS, name)
    if not os.path.isfile(path):
        os.makedirs(TOOLS, exist_ok=True)
        print(f"Downloading ZXPSignCmd: {BASE}/{remote}")
        with urllib.request.urlopen(f"{BASE}/{remote}", timeout=120) as resp:
            data = resp.read()
        with open(path, "wb") as f:
            f.write(data)
    with open(path, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    if digest != sha:
        os.remove(path)
        sys.exit(f"ZXPSignCmd checksum mismatch ({digest}) - refusing to use it.")
    os.chmod(path, 0o755)
    return path


def run(cmd):
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        cmd = ["arch", "-x86_64"] + cmd  # the macOS tool is Intel-only (runs under Rosetta)
    return subprocess.run(cmd, capture_output=True, text=True)


def main() -> None:
    tool = tool_path()
    with tempfile.TemporaryDirectory() as tmp:
        password = secrets.token_urlsafe(24)
        cert = os.path.join(tmp, "bridge_cert.p12")
        r = run([tool, "-selfSignedCert", "IN", "Bihar", "AI Video Flow", "AI Video Flow Premiere Bridge",
                 password, cert, "-validityDays", "7300"])
        if not os.path.isfile(cert):
            sys.exit(f"Could not create the certificate: {r.stdout}{r.stderr}")

        staging = os.path.join(tmp, "com.aivideoflow.bridge")
        shutil.copytree(SOURCE, staging, ignore=shutil.ignore_patterns(".DS_Store", "__MACOSX", "*.pyc"))
        zxp = os.path.join(tmp, "bridge.zxp")
        for tsa in TIMESTAMP_SERVERS + [None]:
            r = run([tool, "-sign", staging, zxp, cert, password] + (["-tsa", tsa] if tsa else []))
            if os.path.isfile(zxp):
                break
            print(f"Signing with timestamp server {tsa} failed: {(r.stdout + r.stderr).strip()[:200]}")
        if not os.path.isfile(zxp):
            sys.exit("Could not sign the Premiere bridge.")
        r = run([tool, "-verify", zxp])
        if "Signature verified successfully" not in r.stdout + r.stderr:
            sys.exit(f"Signature check failed: {r.stdout}{r.stderr}")
        shutil.copyfile(zxp, OUTPUT)
    print(f"Signed Premiere bridge: {OUTPUT}")


if __name__ == "__main__":
    main()
