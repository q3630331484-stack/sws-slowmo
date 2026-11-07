@echo off
rem Install Python dependencies (frida)
chcp 65001 >nul 2>nul
cd /d "%~dp0"
set "PY=D:\PYTHON\python.exe"
if exist "%PY%" goto run
set "PY=python"
:run
"%PY%" -m pip install -r "%~dp0requirements.txt"
echo.
pause
