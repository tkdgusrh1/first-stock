#!/bin/bash
# ============================================================
#  맥 / 리눅스용 끄기 파일 — 더블클릭하면 감시가 멈춥니다.
#  다시 시작하려면 '시작하기' 를 더블클릭하세요.
# ============================================================
cd "$(dirname "$0")" || exit 1

PYTHON=""
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        PYTHON="$candidate"
        break
    fi
done

if [ -z "$PYTHON" ]; then
    echo "❌ 파이썬을 찾지 못했습니다."
    read -r -p "엔터를 누르면 창이 닫힙니다..."
    exit 1
fi

exec "$PYTHON" bootstrap.py stop
