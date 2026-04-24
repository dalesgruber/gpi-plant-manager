@echo off
REM Double-click to start the Zira Station Dashboard on http://localhost:8765
cd /d "%~dp0"
echo.
echo  Zira Station Dashboard
echo  Opening on http://localhost:8765/
echo  Press Ctrl+C in this window to stop.
echo.
start "" "http://localhost:8765/"
".venv\Scripts\python.exe" -m uvicorn zira_dashboard.app:app --host 127.0.0.1 --port 8765
