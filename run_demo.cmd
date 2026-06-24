@echo off
setlocal
cd /d "%~dp0"

set PROVIDER=%~1
if "%PROVIDER%"=="" set PROVIDER=deepseek

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_demo.ps1" -Provider "%PROVIDER%"
endlocal
