@echo off
rem Sword With Sauce Slow-Motion Plugin - English UI + console (for troubleshooting)
chcp 65001 >nul 2>nul
cd /d "%~dp0"
set "SWS_LANG=en"
set "PY=D:\PYTHON\python.exe"
if exist "%PY%" goto run
set "PY=python"
:run
"%PY%" "%~dp0launcher.py"
echo.
pause
