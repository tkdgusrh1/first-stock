@echo off
REM ===========================================================
REM  Sudden Attack optimizer (Windows) - double-click this file.
REM  Keep this file ASCII + CRLF: cmd.exe breaks on UTF-8 and LF.
REM  All Korean messages are printed by optimizer.py instead.
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
call :findpython
if not defined LAUNCHER goto nopython
%LAUNCHER% optimizer.py
goto end

:findpython
set LAUNCHER=
where py >nul 2>&1
if not errorlevel 1 set LAUNCHER=py -3
if defined LAUNCHER exit /b
where python >nul 2>&1
if not errorlevel 1 set LAUNCHER=python
exit /b

:nopython
echo.
echo   [!] Python is required but was not found.
echo.
echo   Opening the download page. During setup, be sure to check
echo   the "Add Python to PATH" box at the bottom of the screen.
echo.
start https://www.python.org/downloads/
pause

:end
