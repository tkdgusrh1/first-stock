@echo off
REM ===========================================================
REM  Update to the latest version (Windows)
REM  ASCII + CRLF only. Korean messages come from updater.py.
REM ===========================================================
cd /d "%~dp0"

set LAUNCHER=
where py >nul 2>&1
if not errorlevel 1 set LAUNCHER=py -3
if defined LAUNCHER goto run

where python >nul 2>&1
if not errorlevel 1 set LAUNCHER=python
if defined LAUNCHER goto run

echo.
echo   [!] Python is not installed / Python이 설치되어 있지 않습니다.
echo.
pause
goto end

:run
%LAUNCHER% updater.py

:end
