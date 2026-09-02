#!/bin/bash
# ============================================================
#  맥 / 리눅스용 끄기 파일 — 더블클릭하면 감시가 멈춥니다
#  더블클릭하면 실행됩니다.
#  (맥에서 처음 한 번만: 이 파일 우클릭 → 열기 → "열기" 버튼)
#
#  실제 일은 program 폴더 안의 파이썬이 합니다.
#  이 파일은 파이썬을 찾아 넘겨주기만 합니다.
# ============================================================
cd "$(dirname "$0")/program" || {
    echo
    echo "❌ program 폴더를 찾지 못했습니다."
    echo "   내려받은 압축을 통째로 한 폴더에 푼 뒤, 그 안의 이 파일을 실행해주세요."
    echo
    read -r -p "엔터를 누르면 창이 닫힙니다..."
    exit 1
}

PYTHON=""
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        PYTHON="$candidate"
        break
    fi
done

if [ -z "$PYTHON" ]; then
    echo
    echo "❌ 파이썬이 설치되어 있지 않습니다."
    echo
    echo "   맥이라면 터미널에 아래 한 줄을 붙여넣어 설치할 수 있습니다:"
    echo "       xcode-select --install"
    echo
    echo "   또는 https://www.python.org/downloads/ 에서 설치한 뒤"
    echo "   이 파일을 다시 더블클릭해주세요."
    echo
    read -r -p "엔터를 누르면 창이 닫힙니다..."
    exit 1
fi

exec "$PYTHON" bootstrap.py stop
