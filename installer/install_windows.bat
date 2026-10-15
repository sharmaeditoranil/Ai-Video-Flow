@echo off
title AI Video Flow - One-Click Installer
color 0B
echo ==============================================================
echo        AI VIDEO FLOW - DAVINCI RESOLVE AI CONNECTOR
echo ==============================================================
echo.

echo [1/4] Checking Python Installation...
where python >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Python not found on your system!
    echo Please install Python 3.10+ from https://www.python.org/downloads/
    echo IMPORTANT: Make sure to check "Add Python to PATH" during installation.
    echo.
    pause
    exit /b 1
)

echo [2/4] Installing Required Dependencies...
python -m pip install --upgrade pip --quiet
python -m pip install -r requirements.txt --quiet
if %ERRORLEVEL% NEQ 0 (
    echo [WARNING] Retrying dependency installation...
    python -m pip install numpy opencv-python-headless librosa soundfile scipy opentimelineio scenedetect
)

echo [3/4] Connecting to Claude Desktop...
python installer\connect_all_engine.py

echo.
echo [4/4] Installation Complete!
echo ==============================================================
echo MANDATORY STEP IN DAVINCI RESOLVE:
echo 1. Open DaVinci Resolve
echo 2. Top Menu: Preferences -> System -> General
echo 3. Set "External scripting using" to: "Local" (or Network)
echo 4. Click Save
echo.
echo Restart Claude Desktop and you are ready to edit!
echo ==============================================================
pause
