#!/bin/bash
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

echo "=============================================================="
echo "       AI VIDEO FLOW - 1-CLICK AUTOMATIC SETUP (MAC)"
echo "=============================================================="
echo ""

# Downloaded files are quarantined by Gatekeeper, which runs the .app from a random read-only location
xattr -dr com.apple.quarantine "$DIR" 2>/dev/null

bash installer/install_mac.sh || { echo "Setup failed. See messages above."; read -n 1 -s -r -p "Press any key to close..."; exit 1; }

echo ""
echo "Installing AI Video Flow.app..."
if [ -w "/Applications" ]; then
    APP_DEST="/Applications"
else
    APP_DEST="$HOME/Applications"
    mkdir -p "$APP_DEST"
fi
rm -rf "$APP_DEST/AI Video Flow.app"
cp -R "$DIR/AI Video Flow.app" "$APP_DEST/"
chmod +x "$APP_DEST/AI Video Flow.app/Contents/MacOS/AI Video Flow"
touch "$APP_DEST/AI Video Flow.app"

echo "Creating Application link on Desktop..."
rm -f "$HOME/Desktop/AI Video Flow.app"
ln -s "$APP_DEST/AI Video Flow.app" "$HOME/Desktop/AI Video Flow.app"

echo "Setup completed successfully! Opening AI Video Flow App..."
open "$APP_DEST/AI Video Flow.app"
