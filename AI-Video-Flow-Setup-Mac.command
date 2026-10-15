#!/bin/bash
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

echo "=============================================================="
echo "       AI VIDEO FLOW - 1-CLICK AUTOMATIC SETUP (MAC)"
echo "=============================================================="
echo ""

bash installer/install_mac.sh

echo ""
echo "Creating Application link on Desktop..."
ln -sf "$DIR/AI Video Flow.app" "$HOME/Desktop/AI Video Flow.app"

echo "Setup completed successfully! Opening AI Video Flow App..."
open "$DIR/AI Video Flow.app"
