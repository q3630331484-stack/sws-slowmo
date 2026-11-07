@echo off
rem Sword With Sauce Slow-Motion Plugin - English UI launcher (no console window)
chcp 65001 >nul 2>nul
cd /d "%~dp0"
set "SWS_LANG=en"
set "PYW=D:\PYTHON\pythonw.exe"
if exist "%PYW%" goto run
set "PYW=D:\PYTHON\python.exe"
if exist "%PYW%" goto run
set "PYW=pythonw"
:run
start "" "%PYW%" "%~dp0launcher.py"
exit /b 0
