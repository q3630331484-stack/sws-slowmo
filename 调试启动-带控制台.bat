@echo off
rem Debug launcher (keeps a console window so errors are visible)
chcp 65001 >nul 2>nul
cd /d "%~dp0"
set "PY=D:\PYTHON\python.exe"
if exist "%PY%" goto run
set "PY=python"
:run
"%PY%" "%~dp0launcher.py"
echo.
echo Exit code: %errorlevel%
pause
