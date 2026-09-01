@echo off
REM ===========================================================
REM  Undo the last optimization (Windows).
REM  Only needed when the screen will not open - the screen has
REM  the same button. ASCII + CRLF only.
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
echo   [!] Python is required but was not found.
echo.
pause
goto end

:run
%LAUNCHER% optimizer.py revert
echo.
pause

:end
