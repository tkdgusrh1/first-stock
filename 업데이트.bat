@echo off
REM ===========================================================
REM  Update to the latest version (Windows)
REM  Keep this file ASCII + CRLF: cmd.exe breaks on UTF-8 and LF.
REM  All Korean messages are printed by the Python side instead.
REM ===========================================================
cd /d "%~dp0program"
if not exist "updater.py" goto nofolder

set LAUNCHER=
where py >nul 2>&1
if not errorlevel 1 set LAUNCHER=py -3
if defined LAUNCHER goto run

where python >nul 2>&1
if not errorlevel 1 set LAUNCHER=python
if defined LAUNCHER goto run
goto nopython

:run
%LAUNCHER% updater.py
goto end

:nofolder
echo.
echo   [!] The "program" folder is missing.
echo       Unzip the whole download into one folder, then run this file
echo       from inside that folder.
echo.
pause
goto end

:nopython
echo.
echo   [!] Python is not installed.
echo.
echo   Opening the download page. During setup, be sure to check
echo   the "Add Python to PATH" box at the bottom of the screen.
echo.
start https://www.python.org/downloads/
pause

:end
