#!/usr/bin/env python3
"""
AI Video Flow - Native Desktop Software Launcher
Launches the local backend controller and opens a native frameless app window.
"""

import os
import sys
import time
import shutil
import platform
import threading
import subprocess
import webbrowser

# Ensure project root is in sys.path
root_dir = os.path.dirname(os.path.abspath(__file__))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

PORT = 5678
APP_URL = f"http://127.0.0.1:{PORT}"


def start_backend():
    from packages.desktop_app.backend import start_server
    start_server(PORT)


def open_app_window():
    time.sleep(0.6)  # Give server a moment to bind port
    system = platform.system()

    # Try opening Chrome or Edge in native App Mode (no address bar, feels like native desktop software)
    if system == "Darwin":  # macOS
        chrome_app = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        brave_app = "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"
        edge_app = "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"

        for browser in [chrome_app, brave_app, edge_app]:
            if os.path.exists(browser):
                try:
                    subprocess.Popen([browser, f"--app={APP_URL}", "--window-size=1280,820"])
                    return
                except Exception:
                    pass

    elif system == "Windows":
        candidates = [
            os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
            os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe")
        ]
        for c in candidates:
            if os.path.exists(c):
                try:
                    subprocess.Popen([c, f"--app={APP_URL}", "--window-size=1280,820"])
                    return
                except Exception:
                    pass

    # Fallback to standard default browser
    webbrowser.open(APP_URL)


if __name__ == "__main__":
    print("=" * 60)
    print("     STARTING AI VIDEO FLOW DESKTOP SUITE")
    print("=" * 60)
    print(f"Backend Server: {APP_URL}")

    # Already running (app opened twice): just bring up another window
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if s.connect_ex(("127.0.0.1", PORT)) == 0:
            open_app_window()
            sys.exit(0)

    # Start backend server in a daemon thread
    server_thread = threading.Thread(target=start_backend, daemon=True)
    server_thread.start()

    # Open App Window
    open_app_window()

    print("AI Video Flow is running. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping AI Video Flow. Goodbye!")
        sys.exit(0)
