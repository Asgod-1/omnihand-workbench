@echo off
setlocal
cd /d "%~dp0"
".venv\Scripts\python.exe" -m omnihand_usb stop %*
if errorlevel 1 pause
