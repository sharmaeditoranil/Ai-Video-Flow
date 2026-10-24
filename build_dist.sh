#!/bin/bash
# Builds the distributable installers in dist/:
#   AI-Video-Flow-macOS.zip / .dmg  (full app folder + AI Video Flow.app + setup command)
#   AI-Video-Flow-Windows.zip       (full app folder + setup .bat)
set -e
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

COMMON=(.agents assets installer packages launch_app.py requirements.txt README.md SETUP_OTHER_SYSTEM.md)
EXCLUDES=(--exclude '__pycache__' --exclude '*.pyc' --exclude '.DS_Store')

# macOS
MAC="$STAGE/mac/AI Video Flow"
mkdir -p "$MAC"
rsync -a "${EXCLUDES[@]}" "${COMMON[@]}" "AI Video Flow.app" AI-Video-Flow-Setup-Mac.command "$MAC/"
chmod +x "$MAC/AI-Video-Flow-Setup-Mac.command" "$MAC/installer/install_mac.sh" "$MAC/AI Video Flow.app/Contents/MacOS/AI Video Flow"
xattr -cr "$STAGE/mac"
rm -f dist/AI-Video-Flow-macOS.zip dist/AI-Video-Flow-macOS.dmg
(cd "$STAGE/mac" && ditto -c -k --norsrc --noextattr --noacl --keepParent "AI Video Flow" "$DIR/dist/AI-Video-Flow-macOS.zip")
hdiutil create -volname "AI Video Flow" -srcfolder "$MAC" -ov -format UDZO dist/AI-Video-Flow-macOS.dmg >/dev/null

# Windows
WIN="$STAGE/win"
mkdir -p "$WIN"
rsync -a "${EXCLUDES[@]}" "${COMMON[@]}" AI-Video-Flow-Setup.bat AI-Video-Flow.bat AI-Video-Flow.vbs build_windows_exe.bat "$WIN/"
rm -f "$WIN/installer/install_mac.sh"
xattr -cr "$WIN"
rm -f dist/AI-Video-Flow-Windows.zip
(cd "$WIN" && zip -qr "$DIR/dist/AI-Video-Flow-Windows.zip" .)

ls -lh dist
