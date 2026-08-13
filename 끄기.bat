@echo off
REM ===========================================================
REM  Stop the background watcher (Windows)
REM  ASCII + CRLF only. Korean messages come from bootstrap.py.
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
echo   [!] Python is not installed.
echo.
pause
goto end

:run
%LAUNCHER% bootstrap.py stop

:end
