@echo off
cd /d "%~dp0"
echo Capturing on port 8080 - leave this window open. Ctrl+C to stop.
mitmdump -q -s capture_addon.py --listen-port 8080
pause
