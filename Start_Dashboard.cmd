@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run python scripts\bootstrap.py --download-sdk first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m omnihand_usb dashboard --open %*
if errorlevel 1 pause
