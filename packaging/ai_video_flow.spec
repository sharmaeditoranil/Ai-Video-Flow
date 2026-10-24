# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the standalone AI Video Flow app (macOS .app / Windows folder).
# Produces two executables sharing one runtime:
#   "AI Video Flow"      windowed desktop dashboard
#   "ai-video-flow-mcp"  console MCP stdio server that Claude Desktop launches
# Build:  pyinstaller --noconfirm packaging/ai_video_flow.spec

import os
import sys
from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform == "win32"
VERSION = "1.1.4"

datas = [
    (os.path.join(ROOT, "packages", "desktop_app", "index.html"), "packages/desktop_app"),
    (os.path.join(ROOT, "packages", "desktop_app", "app.js"), "packages/desktop_app"),
    (os.path.join(ROOT, "packages", "desktop_app", "style.css"), "packages/desktop_app"),
    (os.path.join(ROOT, "packages", "desktop_app", "logo.png"), "packages/desktop_app"),
    (os.path.join(ROOT, "packages", "desktop_app", "ads"), "packages/desktop_app/ads"),
    (os.path.join(ROOT, "packages", "desktop_app", "nle"), "packages/desktop_app/nle"),
    (os.path.join(ROOT, "packages", "resolve_workflow"), "packages/resolve_workflow"),
    (os.path.join(ROOT, "assets", "icon_1024.png"), "assets"),
]
binaries = []
hiddenimports = collect_submodules("packages") + collect_submodules("installer")

for pkg in ["librosa", "scenedetect", "opentimelineio", "soundfile", "webview", "anthropic", "imageio_ffmpeg"]:
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception as e:
        print(f"[spec] skipping {pkg}: {e}")

a = Analysis(
    [os.path.join(ROOT, "launch_app.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "matplotlib", "IPython", "pytest", "PyQt5", "PyQt6", "PySide2", "PySide6"],
    noarchive=False,
)
pyz = PYZ(a.pure)

icon = os.path.join(ROOT, "assets", "icon.icns" if IS_MAC else "icon.ico")

app_exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AI Video Flow",
    console=False,
    icon=icon,
    upx=False,
)

mcp_exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ai-video-flow-mcp",
    console=True,
    icon=icon,
    upx=False,
)

coll = COLLECT(
    app_exe,
    mcp_exe,
    a.binaries,
    a.datas,
    upx=False,
    name="AI Video Flow",
)

if IS_MAC:
    app = BUNDLE(
        coll,
        name="AI Video Flow.app",
        icon=icon,
        bundle_identifier="com.aivideoflow.desktop",
        version=VERSION,
        info_plist={
            "CFBundleName": "AI Video Flow",
            "CFBundleDisplayName": "AI Video Flow",
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "LSMinimumSystemVersion": "11.0",
            "NSHighResolutionCapable": True,
            "NSAppleEventsUsageDescription": "AI Video Flow uses Finder dialogs to let you choose footage folders and music files.",
            "NSDesktopFolderUsageDescription": "AI Video Flow reads your footage folders.",
            "NSDocumentsFolderUsageDescription": "AI Video Flow reads your footage folders.",
            "NSDownloadsFolderUsageDescription": "AI Video Flow reads your footage folders.",
            "NSRemovableVolumesUsageDescription": "AI Video Flow reads footage from external drives.",
        },
    )
