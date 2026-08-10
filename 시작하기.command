#!/bin/bash
# ============================================================
#  맥 / 리눅스용 시작 파일 — 더블클릭하면 실행됩니다.
#  (처음 한 번만: 이 파일 우클릭 → 열기 → "열기" 버튼)
# ============================================================
cd "$(dirname "$0")" || exit 1

echo
echo "============================================================"
echo "  관심 종목 감시 봇"
echo "============================================================"
echo

# 1. 파이썬 확인
PYTHON=""
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
            PYTHON="$candidate"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    echo "❌ 파이썬 3.9 이상이 필요합니다."
    echo
    echo "   https://www.python.org/downloads/ 에서 설치한 뒤"
    echo "   이 파일을 다시 더블클릭해주세요."
    echo
    read -r -p "엔터를 누르면 창이 닫힙니다..."
    exit 1
fi

# 2. 가상환경 (처음 한 번만 만들어짐)
if [ ! -d ".venv" ]; then
    echo "· 처음 실행이라 준비를 좀 할게요 (1~2분)..."
    "$PYTHON" -m venv .venv || { echo "❌ 가상환경 생성 실패"; read -r; exit 1; }
fi
# shellcheck disable=SC1091
source .venv/bin/activate

# 3. 필요한 패키지 (이미 있으면 건너뜀)
if ! python -c "import requests, yaml" >/dev/null 2>&1; then
    echo "· 필요한 패키지를 설치하는 중..."
    python -m pip install --quiet --upgrade pip
    python -m pip install --quiet -r requirements.txt || {
        echo "❌ 패키지 설치 실패. 인터넷 연결을 확인해주세요."
        read -r -p "엔터를 누르면 창이 닫힙니다..."
        exit 1
    }
fi

# 4. 실행 (설정이 없으면 자동으로 물어봅니다)
python main.py run

echo
echo "감시를 멈췄습니다."
read -r -p "엔터를 누르면 창이 닫힙니다..."
