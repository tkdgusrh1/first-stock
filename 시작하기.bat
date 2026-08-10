@echo off
REM ===========================================================
REM  Stock watcher launcher (Windows)
REM  Keep this file ASCII + CRLF: cmd.exe breaks on UTF-8 and LF.
REM  All Korean messages are printed by bootstrap.py instead.
REM ===========================================================
cd /d "%~dp0"

set LAUNCHER=
where py >nul 2>&1
if not errorlevel 1 set LAUNCHER=py -3
if defined LAUNCHER goto run

where python >nul 2>&1
if not errorlevel 1 set LAUNCHER=python
if defined LAUNCHER goto run
goto nopython

:run
%LAUNCHER% bootstrap.py
goto end

:nopython
echo.
echo   [!] Python is not installed / Python이 설치되어 있지 않습니다.
echo.
echo   Opening the download page. During setup, be sure to check
echo   the "Add Python to PATH" box at the bottom of the screen.
echo.
start https://www.python.org/downloads/
pause

:end
