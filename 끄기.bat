@echo off
REM ===========================================================
REM  Stop the background watcher (Windows)
REM  Keep this file ASCII + CRLF: cmd.exe breaks on UTF-8 and LF.
REM  All Korean messages are printed by the Python side instead.
REM  Python is found (or installed for this user) by tools\python.cmd
REM ===========================================================
cd /d "%~dp0program"
if not exist "bootstrap.py" goto nofolder

call "tools\python.cmd" noinstall
if not defined FS_PY goto nopython
%FS_PY% bootstrap.py stop
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
echo   Nothing to stop: Python is not installed, so the watcher is not running.
pause

:end
