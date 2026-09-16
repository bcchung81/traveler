@echo off
rem Receipt evidence (receipt-evidence) - start local AI + web app and open the browser.
rem Close this window or press Ctrl+C to stop both. Options: --port 8790 --vlm-port 8089 --no-browser
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\windows\run-app.ps1" %*
set "CODE=%ERRORLEVEL%"
if not "%CODE%"=="0" pause
exit /b %CODE%
