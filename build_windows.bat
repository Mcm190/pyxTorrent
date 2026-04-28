@echo off
REM Build pyxTorrent.exe on Windows using PyInstaller.
REM Run this from a Python venv that has libtorrent + pyinstaller installed:
REM    python -m venv .venv
REM    .venv\Scripts\activate
REM    pip install -r requirements.txt
REM    build_windows.bat

setlocal
where pyinstaller >nul 2>&1
if errorlevel 1 (
  echo pyinstaller not found on PATH. Activate the venv first.
  exit /b 1
)

pyinstaller --noconfirm --clean pyxtorrent.spec
if errorlevel 1 (
  echo Build failed.
  exit /b 1
)

echo.
echo Built: dist\pyxTorrent.exe
