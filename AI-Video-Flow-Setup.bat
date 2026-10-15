@echo off
title AI Video Flow - One-Click Auto Installer
color 0B
echo ==============================================================
echo        AI VIDEO FLOW - DAVINCI RESOLVE AI CONNECTOR
echo               One-Click Zero-Touch Setup
echo ==============================================================
echo.

:: 1. Check Python
echo [1/4] Checking Python 3.10+...
where python >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Python not found on your PC!
    echo Please download and install Python 3.10+ from:
    echo   https://www.python.org/downloads/
    echo.
    echo IMPORTANT: Make sure to check "Add Python to PATH" on the first screen.
    echo.
    pause
    exit /b 1
)

:: 2. Install Dependencies
echo [2/4] Installing Required AI Libraries...
python -m pip install --upgrade pip --quiet
python -m pip install -r requirements.txt --quiet
if %ERRORLEVEL% NEQ 0 (
    echo Retrying dependencies installation...
    python -m pip install numpy opencv-python-headless librosa soundfile scipy opentimelineio scenedetect
)

:: 3. Connect to Claude Desktop
echo [3/4] Connecting to Claude Desktop MCP & DaVinci Resolve...
python installer\connect_all_engine.py

:: 4. Create Desktop Shortcut via VBScript
echo [4/4] Creating Desktop Shortcut...
set SCRIPT="%TEMP%\create_shortcut.vbs"
echo Set oWS = WScript.CreateObject("WScript.Shell") > %SCRIPT%
echo sLinkFile = oWS.SpecialFolders("Desktop") ^& "\AI Video Flow.lnk" >> %SCRIPT%
echo Set oLink = oWS.CreateShortcut(sLinkFile) >> %SCRIPT%
echo oLink.TargetPath = "%~dp0AI-Video-Flow.vbs" >> %SCRIPT%
echo oLink.WorkingDirectory = "%~dp0" >> %SCRIPT%
echo oLink.Description = "AI Video Flow - DaVinci Resolve AI Connector" >> %SCRIPT%
echo oLink.IconLocation = "%~dp0assets\icon.ico" >> %SCRIPT%
echo oLink.Save >> %SCRIPT%
cscript /nologo %SCRIPT%
del %SCRIPT%

echo.
echo ==============================================================
echo  INSTALLATION COMPLETED SUCCESSFULLY!
echo  A shortcut "AI Video Flow" has been created on your Desktop.
echo.
echo  REMINDER: In DaVinci Resolve:
echo  Preferences -^> System -^> General -^> External scripting = Local
echo ==============================================================
echo.
echo Launching AI Video Flow Desktop Suite now...
start "" "%~dp0AI-Video-Flow.bat"
timeout /t 3 >nul
exit /b 0
