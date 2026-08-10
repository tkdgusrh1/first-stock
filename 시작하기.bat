@echo off
chcp 65001 >nul
REM ============================================================
REM   윈도우용 시작 파일 - 더블클릭하면 실행됩니다.
REM ============================================================
cd /d "%~dp0"

echo.
echo ============================================================
echo   관심 종목 감시 봇
echo ============================================================
echo.

REM 1. 파이썬 확인
set PYTHON=
where py >nul 2>&1 && set PYTHON=py -3
if not defined PYTHON (
    where python >nul 2>&1 && set PYTHON=python
)
if not defined PYTHON (
    echo [X] 파이썬이 설치되어 있지 않습니다.
    echo.
    echo     https://www.python.org/downloads/ 에서 설치해주세요.
    echo     설치할 때 "Add Python to PATH" 를 꼭 체크하세요.
    echo.
    pause
    exit /b 1
)

REM 2. 가상환경 (처음 한 번만)
if not exist ".venv" (
    echo - 처음 실행이라 준비를 좀 할게요 ^(1~2분^)...
    %PYTHON% -m venv .venv
    if errorlevel 1 (
        echo [X] 가상환경 생성에 실패했습니다.
        pause
        exit /b 1
    )
)
call .venv\Scripts\activate.bat

REM 3. 필요한 패키지
python -c "import requests, yaml" >nul 2>&1
if errorlevel 1 (
    echo - 필요한 패키지를 설치하는 중...
    python -m pip install --quiet --upgrade pip
    python -m pip install --quiet -r requirements.txt
    if errorlevel 1 (
        echo [X] 패키지 설치에 실패했습니다. 인터넷 연결을 확인해주세요.
        pause
        exit /b 1
    )
)

REM 4. 실행 (설정이 없으면 자동으로 물어봅니다)
python main.py run

echo.
echo 감시를 멈췄습니다.
pause
