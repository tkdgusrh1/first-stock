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

REM 0. 제대로 된 폴더인지 확인
if not exist "main.py" (
    echo [X] 프로그램 파일^(main.py^)을 찾을 수 없습니다.
    echo.
    echo     압축을 풀지 않고 ZIP 안에서 바로 실행했거나,
    echo     이 파일만 따로 옮겨진 것 같습니다.
    echo     ZIP 전체를 풀고, 그 폴더 안의 이 파일을 다시 더블클릭해주세요.
    echo.
    echo     현재 위치: %cd%
    echo.
    pause
    exit /b 1
)

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
    echo     설치 화면 맨 아래 "Add Python to PATH" 체크박스를 꼭 켜야 합니다.
    echo     ^(이걸 안 켜면 설치해도 여기서 계속 못 찾습니다^)
    echo.
    echo     설치한 뒤 이 파일을 다시 더블클릭해주세요.
    echo.
    pause
    exit /b 1
)
%PYTHON% --version

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
