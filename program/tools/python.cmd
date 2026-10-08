@echo off
REM ===========================================================
REM  Find a working Python 3.9+ for First Stock (Windows).
REM  Sets FS_PY to the command to run it.
REM  If none is found, installs Python for this user only
REM  (no admin rights) unless called with "noinstall".
REM  Keep this file ASCII + CRLF: cmd.exe breaks on UTF-8 and LF.
REM ===========================================================
set "FS_PY="
set "FS_HOME=%LOCALAPPDATA%\FirstStock\python"

call :find
if defined FS_PY exit /b 0
if /i "%~1"=="noinstall" exit /b 1

where powershell >nul 2>&1
if errorlevel 1 exit /b 1
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-python.ps1" -Target "%FS_HOME%"
call :find
if defined FS_PY exit /b 0
exit /b 1

:find
REM 1) the one we installed before  2) py launcher  3) python on PATH
REM 4) installed without "Add Python to PATH" (per-user, then all users)
call :try "%FS_HOME%\python.exe"
call :try py -3
call :try python
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do call :try "%%~D\python.exe"
for /d %%D in ("%ProgramFiles%\Python3*") do call :try "%%~D\python.exe"
exit /b 0

:try
REM Run it for real. The Microsoft Store "python" stub only prints a
REM message and fails here, so it is never picked by mistake.
if defined FS_PY exit /b 0
%* -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>&1
if errorlevel 1 exit /b 0
set FS_PY=%*
exit /b 0
