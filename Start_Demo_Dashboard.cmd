@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Create the project environment first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m omnihand_usb dashboard --demo --http-port 8765 --open %*
if errorlevel 1 pause
