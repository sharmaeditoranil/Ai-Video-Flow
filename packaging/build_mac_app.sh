#!/bin/bash
# Builds the standalone macOS app (Python + all AI libraries bundled, no install needed)
#   dist/AI-Video-Flow-Mac-<arch>.dmg   drag-to-Applications installer
# Usage: bash packaging/build_mac_app.sh   [PYTHON=/path/to/python3.12]
set -e
ROOT="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"
cd "$ROOT"

PY="${PYTHON:-$(command -v python3.12 || command -v python3.11 || command -v python3)}"
ARCH="$(uname -m)"
VENV="$ROOT/.build-venv"
echo "[1/4] Build Python: $PY ($("$PY" --version)) on $ARCH"

if [ ! -x "$VENV/bin/python" ]; then
    "$PY" -m venv "$VENV"
fi
"$VENV/bin/pip" install --upgrade pip --quiet
"$VENV/bin/pip" install -r requirements.txt -r packaging/requirements-build.txt --quiet

echo "      Signing the Premiere Pro bridge..."
"$VENV/bin/python" packaging/sign_premiere_bridge.py

echo "[2/4] Building AI Video Flow.app with PyInstaller..."
rm -rf build/work "build/dist/AI Video Flow" "build/dist/AI Video Flow.app"
"$VENV/bin/pyinstaller" --noconfirm --clean --log-level WARN \
    --distpath build/dist --workpath build/work packaging/ai_video_flow.spec

APP="build/dist/AI Video Flow.app"
codesign --force --deep --sign - "$APP"

echo "[3/4] Smoke test: MCP helper answers initialize..."
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' \
    | "$APP/Contents/MacOS/ai-video-flow-mcp" 2>/dev/null | grep -q '"serverInfo"' \
    && echo "      MCP helper OK" || { echo "      MCP helper FAILED"; exit 1; }

echo "[4/4] Creating DMG..."
DMG_STAGE="build/dmg"
rm -rf "$DMG_STAGE" && mkdir -p "$DMG_STAGE"
cp -R "$APP" "$DMG_STAGE/"
ln -s /Applications "$DMG_STAGE/Applications"
cp packaging/INSTALL_GUIDE.txt "$DMG_STAGE/Install Guide.txt"
OUT="dist/AI-Video-Flow-Mac-$ARCH.dmg"
rm -f "$OUT"
hdiutil create -volname "AI Video Flow" -srcfolder "$DMG_STAGE" -ov -format UDZO "$OUT" >/dev/null
rm -rf "$DMG_STAGE"

echo ""
echo "Done: $OUT ($(du -h "$OUT" | cut -f1))"
echo "SHA-256 (paste in admin portal → Settings → update): $(shasum -a 256 "$OUT" | cut -d' ' -f1)"
