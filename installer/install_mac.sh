#!/bin/bash
set -e

echo "=============================================================="
echo "       AI VIDEO FLOW - DAVINCI RESOLVE AI CONNECTOR (MAC)"
echo "=============================================================="
echo ""

# Find Python 3.10 or newer (the Claude SDK needs it)
PY_BIN=""
for cand in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$cand" &>/dev/null && "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
        PY_BIN=$(command -v "$cand")
        break
    fi
done
if [ -z "$PY_BIN" ]; then
    echo "[ERROR] Python 3.10 or newer not found!"
    echo "Please download and install Python 3.12 from https://www.python.org/downloads/mac-osx/"
    exit 1
fi

echo "[1/4] Found Python 3: $PY_BIN"

# Create / Update isolated runtime in ~/.ai-video-flow to prevent macOS sandbox permissions errors
APP_DIR="$HOME/.ai-video-flow/app"
VENV_DIR="$HOME/.ai-video-flow/venv"

echo "[2/4] Setting up isolated AI Video Flow runtime in $HOME/.ai-video-flow..."
mkdir -p "$APP_DIR"
rm -rf "$APP_DIR/packages" "$APP_DIR/installer" "$APP_DIR/assets" "$APP_DIR/requirements.txt" "$APP_DIR/launch_app.py"
cp -R packages "$APP_DIR/"
cp -R installer "$APP_DIR/"
cp -R assets "$APP_DIR/"
cp requirements.txt launch_app.py "$APP_DIR/"

if [ -x "$VENV_DIR/bin/python" ] && ! "$VENV_DIR/bin/python" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
    rm -rf "$VENV_DIR"  # created by an older Python - rebuild
fi
if [ ! -d "$VENV_DIR" ]; then
    "$PY_BIN" -m venv "$VENV_DIR"
fi

echo "Installing/verifying AI packages (numpy, opencv, librosa, scipy)..."
"$VENV_DIR/bin/pip" install --upgrade pip --quiet
"$VENV_DIR/bin/pip" install -r "$APP_DIR/requirements.txt" --quiet

echo "[3/4] Registering MCP Server with Claude Desktop..."
"$VENV_DIR/bin/python" "$APP_DIR/installer/connect_all_engine.py"

echo ""
echo "[4/4] Installation Complete!"
echo "=============================================================="
echo " MANDATORY STEP IN DAVINCI RESOLVE:"
echo " 1. Open DaVinci Resolve"
echo " 2. DaVinci Resolve -> Preferences -> System -> General"
echo " 3. Set 'External scripting using' to: 'Local'"
echo " 4. Click Save"
echo ""
echo " Restart Claude Desktop and you are ready to edit!"
echo "=============================================================="
