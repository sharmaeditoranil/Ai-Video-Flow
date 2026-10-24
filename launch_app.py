#!/usr/bin/env python3
"""
AI Video Flow - Native Desktop Software Launcher
Launches the local backend controller and opens a native app window.

Modes:
  launch_app.py          Desktop dashboard (native window, falls back to Chrome/Edge app mode or browser)
  launch_app.py --mcp    MCP stdio server for Claude Desktop (used by the standalone build)
"""

import os
import sys
import time
import socket
import platform
import threading
import subprocess
import webbrowser

FROZEN = getattr(sys, "frozen", False)

# Ensure project root is in sys.path
root_dir = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

PORT = 5678
APP_URL = f"http://127.0.0.1:{PORT}"
APP_VERSION = "1.1.0"
DATA_DIR = os.path.join(os.path.expanduser("~"), ".ai-video-flow")


def is_mcp_mode() -> bool:
    exe_name = os.path.basename(sys.executable).lower() if FROZEN else ""
    return "--mcp" in sys.argv or "mcp" in exe_name


def redirect_output_if_windowless():
    # Windowed builds have no console; keep prints/logging from failing and keep a log for support
    if sys.stdout is None or sys.stderr is None:
        os.makedirs(DATA_DIR, exist_ok=True)
        log = open(os.path.join(DATA_DIR, "app.log"), "a", buffering=1, encoding="utf-8")
        sys.stdout = sys.stdout or log
        sys.stderr = sys.stderr or log


def start_backend():
    from packages.desktop_app.backend import start_server
    start_server(PORT)


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def running_same_version(port: int) -> bool:
    import json
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/version", timeout=1) as r:
            info = json.loads(r.read().decode("utf-8"))
        return info.get("app") == "ai-video-flow" and info.get("version") == APP_VERSION
    except Exception:
        return False


def choose_port() -> int:
    """Returns a port to use; an already running same-version instance's port is reused."""
    global PORT, APP_URL
    for port in range(5678, 5700):
        if not port_in_use(port) or running_same_version(port):
            PORT, APP_URL = port, f"http://127.0.0.1:{port}"
            return port
    return PORT


def wait_for_backend(timeout: float = 15.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", PORT)) == 0:
                return True
        time.sleep(0.15)
    return False


def open_native_window() -> bool:
    """Opens a real desktop window (WKWebView on macOS, WebView2 on Windows). Blocks until closed."""
    try:
        import webview
    except Exception:
        return False
    try:
        webview.create_window("AI Video Flow", APP_URL, width=1280, height=820, min_size=(960, 640))
        webview.start(private_mode=False, storage_path=os.path.join(DATA_DIR, "webview"))
        return True
    except Exception as e:
        print(f"Native window unavailable ({e}); falling back to browser window.")
        return False


def open_app_window():
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


def run_dashboard():
    print("=" * 60)
    print("     STARTING AI VIDEO FLOW DESKTOP SUITE")
    print("=" * 60)
    # An older or different app on the port gets skipped instead of showing its outdated dashboard
    choose_port()
    print(f"Backend Server: {APP_URL}")

    # Already running (app opened twice): just bring up another window
    if wait_for_backend(timeout=0.2):
        if not open_native_window():
            open_app_window()
        return

    # Start backend server in a daemon thread
    server_thread = threading.Thread(target=start_backend, daemon=True)
    server_thread.start()
    wait_for_backend()

    # Native window: the app quits when the window is closed
    if open_native_window():
        return

    open_app_window()
    print("AI Video Flow is running. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping AI Video Flow. Goodbye!")


if __name__ == "__main__":
    if is_mcp_mode():
        from packages.mcp_server.server import run_mcp_server
        run_mcp_server()
    else:
        redirect_output_if_windowless()
        run_dashboard()
    sys.exit(0)
