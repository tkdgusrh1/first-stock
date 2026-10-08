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
REM 4) installs registered in Windows (also ones without "Add to PATH")
REM 5) the default install folders
call :try "%FS_HOME%\python.exe"
call :try py -3
call :try python
for /f "tokens=2,*" %%A in ('reg query "HKCU\Software\Python\PythonCore" /s /v ExecutablePath 2^>nul ^| findstr /i /c:"ExecutablePath"') do call :try "%%B"
for /f "tokens=2,*" %%A in ('reg query "HKLM\Software\Python\PythonCore" /s /v ExecutablePath 2^>nul ^| findstr /i /c:"ExecutablePath"') do call :try "%%B"
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do call :try "%%~D\python.exe"
for /d %%D in ("%ProgramFiles%\Python3*") do call :try "%%~D\python.exe"
exit /b 0

:try
REM Run it for real and accept it only when it exits with exactly 0.
REM - the Microsoft Store "python" stub prints a message and fails
REM - a Python with missing DLLs crashes with a negative exit code
REM   ("if errorlevel 1" would miss that, "&&" does not)
REM - "call" also runs .bat shims (pyenv-win); "<nul" stops any
REM   install prompt from waiting for a key behind a hidden output
if defined FS_PY exit /b 0
call %* -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" <nul >nul 2>&1 && set FS_PY=%*
exit /b 0
