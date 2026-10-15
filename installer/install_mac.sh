#!/bin/bash
set -e

echo "=============================================================="
echo "       AI VIDEO FLOW - DAVINCI RESOLVE AI CONNECTOR (MAC)"
echo "=============================================================="
echo ""

# Find Python 3
if command -v python3 &>/dev/null; then
    PY_BIN=$(command -v python3)
else
    echo "[ERROR] Python 3 not found!"
    echo "Please download and install Python 3.10+ from https://www.python.org/downloads/mac-osx/"
    exit 1
fi

echo "[1/4] Found Python 3: $PY_BIN"

# Create / Update isolated runtime in ~/.ai-video-flow to prevent macOS sandbox permissions errors
APP_DIR="$HOME/.ai-video-flow/app"
VENV_DIR="$HOME/.ai-video-flow/venv"

echo "[2/4] Setting up isolated AI Video Flow runtime in $HOME/.ai-video-flow..."
mkdir -p "$APP_DIR"
rm -rf "$APP_DIR/packages" "$APP_DIR/installer" "$APP_DIR/requirements.txt"
cp -R packages "$APP_DIR/"
cp -R installer "$APP_DIR/"
cp requirements.txt "$APP_DIR/"

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
