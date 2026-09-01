@echo off
REM ===========================================================
REM  Sudden Attack optimizer launcher (Windows)
REM  Keep this file ASCII + CRLF: cmd.exe breaks on UTF-8 and LF.
REM  All Korean messages are printed by sudden.py instead.
REM ===========================================================
cd /d "%~dp0"

REM --- ask for administrator rights, once -----------------------------
REM  Power plan / network / MMCSS items live under HKLM and need it.
REM  The "elevated" argument makes sure we never ask twice in a loop.
if "%~1"=="elevated" goto haveadmin
net session >nul 2>&1
if not errorlevel 1 goto haveadmin

echo.
echo   Asking Windows for administrator rights...
powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -ArgumentList 'elevated' -Verb RunAs" >nul 2>&1
if errorlevel 1 goto noadmin
exit /b

:noadmin
echo   Continuing as a normal user - some items will stay locked.

:haveadmin
set LAUNCHER=
where py >nul 2>&1
if not errorlevel 1 set LAUNCHER=py -3
if defined LAUNCHER goto run

where python >nul 2>&1
if not errorlevel 1 set LAUNCHER=python
if defined LAUNCHER goto run
goto nopython

:run
%LAUNCHER% sudden.py
goto end

:nopython
echo.
echo   [!] Python is not installed / Python is required.
echo.
echo   Opening the download page. During setup, be sure to check
echo   the "Add Python to PATH" box at the bottom of the screen.
echo.
start https://www.python.org/downloads/
pause

:end
