#!/bin/bash
# Builds the script-based Windows package in dist/:
#   AI-Video-Flow-Windows.zip  (full app folder + setup .bat, needs Python on the PC)
# macOS uses the standalone app instead: bash packaging/build_mac_app.sh
set -e
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

COMMON=(.agents assets installer packages launch_app.py requirements.txt README.md SETUP_OTHER_SYSTEM.md)
EXCLUDES=(--exclude '__pycache__' --exclude '*.pyc' --exclude '.DS_Store')

# Windows
WIN="$STAGE/win"
mkdir -p "$WIN"
rsync -a "${EXCLUDES[@]}" "${COMMON[@]}" AI-Video-Flow-Setup.bat AI-Video-Flow.bat AI-Video-Flow.vbs build_windows_exe.bat "$WIN/"
rm -f "$WIN/installer/install_mac.sh"
xattr -cr "$WIN"
rm -f dist/AI-Video-Flow-Windows.zip
(cd "$WIN" && zip -qr "$DIR/dist/AI-Video-Flow-Windows.zip" .)

ls -lh dist
