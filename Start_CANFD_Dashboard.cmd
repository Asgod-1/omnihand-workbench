@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Create the project environment first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m omnihand_usb dashboard --transport canfd --can-channel 1 --can-id 0x09 --open %*
if errorlevel 1 pause
