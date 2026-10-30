#!/usr/bin/env bash
set -euo pipefail

# ============================================================================
# AI Video Flow — Standalone Mobile App (.apk) Builder & Local Preview Runner
# Keeps Mobile App 100% separate from Windows/Mac Desktop packaging.
# ============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "========================================================"
echo "  AI Video Flow — Reels & Shorts Mobile App (.apk)"
echo "========================================================"

MODE="${1:-preview}"

if [[ "$MODE" == "apk" ]]; then
    echo "[1/3] Staging Python backend, AI engine & www assets into mobile_app/android..."
    PY_STAGE="$SCRIPT_DIR/android/app/src/main/python"
    ASSET_STAGE="$SCRIPT_DIR/android/app/src/main/assets/www"
    rm -rf "$PY_STAGE" "$ASSET_STAGE"
    mkdir -p "$PY_STAGE/packages" "$ASSET_STAGE"

    cp "$SCRIPT_DIR/mobile_backend.py" "$PY_STAGE/"
    cp "$SCRIPT_DIR/version.json" "$PY_STAGE/"
    cp -R "$SCRIPT_DIR/www" "$PY_STAGE/www"
    cp -R "$SCRIPT_DIR/www/." "$ASSET_STAGE/"
    touch "$PY_STAGE/packages/__init__.py"
    for pkg in ai_engine licensing core_premiere; do
        if [[ -d "$PROJECT_ROOT/packages/$pkg" ]]; then
            cp -R "$PROJECT_ROOT/packages/$pkg" "$PY_STAGE/packages/$pkg"
        fi
    done

    # Auto-detect OpenJDK 17 & Android SDK on macOS
    if [[ -d "/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home" ]]; then
        export JAVA_HOME="/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home"
        export PATH="$JAVA_HOME/bin:$PATH"
    fi
    if [[ -d "$HOME/Library/Android/sdk" ]]; then
        export ANDROID_HOME="$HOME/Library/Android/sdk"
        export ANDROID_SDK_ROOT="$HOME/Library/Android/sdk"
        echo "sdk.dir=$HOME/Library/Android/sdk" > "$SCRIPT_DIR/android/local.properties"
    fi

    echo "[2/3] Building Android APK with Gradle + Chaquopy..."
    cd "$SCRIPT_DIR/android"
    CACHED_GRADLE="$(find "$HOME/.gradle/wrapper/dists/gradle-8.9-bin" -name "gradle" -type f 2>/dev/null | head -n 1 || true)"
    if [[ -x "./gradlew" ]]; then
        ./gradlew assembleDebug
    elif [[ -n "$CACHED_GRADLE" && -x "$CACHED_GRADLE" ]]; then
        "$CACHED_GRADLE" assembleDebug
    elif command -v gradle >/dev/null 2>&1; then
        gradle assembleDebug
    else
        echo "Android SDK / Gradle not found locally."
        exit 1
    fi

    APK_OUT="$SCRIPT_DIR/android/app/build/outputs/apk/debug/app-debug.apk"
    if [[ -f "$APK_OUT" ]]; then
        cp "$APK_OUT" "$SCRIPT_DIR/AI-Video-Flow-Reels.apk"
        cp "$APK_OUT" "$PROJECT_ROOT/AI-Video-Flow-Reels.apk"
        echo "[3/3] ✅ APK Built Successfully:"
        echo "  -> $SCRIPT_DIR/AI-Video-Flow-Reels.apk"
        echo "  -> $PROJECT_ROOT/AI-Video-Flow-Reels.apk"
    fi
else
    echo "Starting Mobile Reels App Server on http://127.0.0.1:5680 ..."
    if [[ -x "$PROJECT_ROOT/.build-venv/bin/python" ]]; then
        PY_BIN="$PROJECT_ROOT/.build-venv/bin/python"
    elif [[ -x "$PROJECT_ROOT/.venv/bin/python" ]]; then
        PY_BIN="$PROJECT_ROOT/.venv/bin/python"
    else
        PY_BIN="python3"
    fi
    exec "$PY_BIN" "$SCRIPT_DIR/mobile_backend.py" --host 0.0.0.0 --port 5680
fi
