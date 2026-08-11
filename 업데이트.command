#!/bin/bash
# 최신 버전으로 갱신 — 더블클릭하면 실행됩니다.
cd "$(dirname "$0")" || exit 1

PYTHON=""
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        PYTHON="$candidate"
        break
    fi
done

if [ -z "$PYTHON" ]; then
    echo "❌ 파이썬이 설치되어 있지 않습니다."
    read -r -p "엔터를 누르면 창이 닫힙니다..."
    exit 1
fi

exec "$PYTHON" updater.py
