@echo off
title AI Video Flow - Build Windows App
color 0B
setlocal
cd /d "%~dp0.."
echo ==============================================================
echo        BUILDING AI VIDEO FLOW STANDALONE WINDOWS APP
echo ==============================================================
echo.

echo [1/4] Creating build environment (Python 3.10 - 3.12 required)...
if not exist ".build-venv\Scripts\python.exe" (
    python -m venv .build-venv || goto :fail
)
".build-venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
".build-venv\Scripts\python.exe" -m pip install -r requirements.txt -r packaging\requirements-build.txt --quiet || goto :fail

echo       Signing the Premiere Pro bridge...
".build-venv\Scripts\python.exe" packaging\sign_premiere_bridge.py || goto :fail

echo [2/4] Compiling with PyInstaller...
if exist build\dist rmdir /s /q build\dist
if exist build\work rmdir /s /q build\work
".build-venv\Scripts\pyinstaller.exe" --noconfirm --clean --log-level WARN --distpath build\dist --workpath build\work packaging\ai_video_flow.spec || goto :fail

echo [3/4] Creating installer...
set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if exist "%ISCC%" (
    "%ISCC%" /Q packaging\windows_installer.iss || goto :fail
    echo       dist\AI-Video-Flow-Windows-Setup.exe created
    echo       SHA-256 (paste in admin portal - Settings - update):
    certutil -hashfile dist\AI-Video-Flow-Windows-Setup.exe SHA256 | findstr /v ":"
) else (
    echo       Inno Setup 6 not found - creating portable ZIP instead.
    echo       Install it from https://jrsoftware.org/isdl.php for a Setup.exe
    powershell -NoProfile -Command "Compress-Archive -Force -Path 'build\dist\AI Video Flow' -DestinationPath 'dist\AI-Video-Flow-Windows-Portable.zip'" || goto :fail
    echo       dist\AI-Video-Flow-Windows-Portable.zip created
)

echo.
echo [4/4] Build Complete!
echo ==============================================================
pause
exit /b 0

:fail
echo.
echo [ERROR] Build failed. See messages above.
pause
exit /b 1
