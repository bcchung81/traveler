@echo off
rem Receipt evidence (receipt-evidence) - Windows setup. Double-click this file.
rem Options: -Backend auto/cuda/vulkan/cpu  -ReinstallLlama  -SkipModel  -LawOc KEY  -DryRun
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\windows\setup.ps1" -Interactive %*
set "CODE=%ERRORLEVEL%"
echo.
pause
exit /b %CODE%
