@echo off
title AI Video Flow - Build Windows EXE
color 0B
echo ==============================================================
echo        BUILDING AI VIDEO FLOW STANDALONE WINDOWS EXE
echo ==============================================================
echo.

echo [1/3] Installing PyInstaller...
python -m pip install pyinstaller pillow --quiet

echo [2/3] Compiling AI-Video-Flow.exe...
pyinstaller --noconfirm --onedir --windowed ^
    --name "AI-Video-Flow" ^
    --icon "assets\icon.ico" ^
    --add-data "packages;packages" ^
    --add-data "installer;installer" ^
    --add-data "assets;assets" ^
    launch_app.py

echo.
echo [3/3] Build Complete!
echo Standalone App folder generated in: dist\AI-Video-Flow\
echo You can run dist\AI-Video-Flow\AI-Video-Flow.exe
echo ==============================================================
pause
