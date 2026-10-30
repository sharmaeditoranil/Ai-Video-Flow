#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
chmod +x "$DIR/build_apk.sh"
"$DIR/build_apk.sh" preview
