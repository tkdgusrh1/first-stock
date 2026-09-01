@echo off
REM ===========================================================
REM  Undo the last optimization (Windows)
REM  ASCII + CRLF only. Korean messages come from sudden.py.
REM ===========================================================
cd /d "%~dp0"

if "%~1"=="elevated" goto haveadmin
net session >nul 2>&1
if not errorlevel 1 goto haveadmin
powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -ArgumentList 'elevated' -Verb RunAs" >nul 2>&1
if errorlevel 1 goto haveadmin
exit /b

:haveadmin
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
%LAUNCHER% sudden.py revert
echo.
pause

:end
