@echo off
cd /d "%~dp0"
py web_test_server.py --open
if errorlevel 1 (
  python web_test_server.py --open
)
