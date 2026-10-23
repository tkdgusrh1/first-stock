#!/usr/bin/env bash
# 가상 휴대폰에서 APK 를 깔고 켜 본다 (.github/workflows/android.yml 의 run 단계).
set -u
adb install -r FirstStock.apk || exit 1
adb logcat -c
adb shell am start -n io.firststock.app/.MainActivity || exit 1

ok=""
for _ in $(seq 1 90); do
  sleep 2
  adb logcat -d -s python.stdout python.stderr > python-log.txt 2>/dev/null
  if grep -q "화면 포트" python-log.txt; then ok=1; break; fi
  if grep -q "Traceback" python-log.txt; then break; fi
done
sleep 25                                  # 화면이 그려지고 첫 시세를 받을 시간
adb exec-out screencap -p > screen-home.png
adb logcat -d -s python.stdout python.stderr > python-log.txt
adb logcat -d | grep -iE "chromium|AndroidRuntime|FATAL" | tail -60 >> python-log.txt

# 같은 휴대폰의 다른 앱처럼 열쇠 없이 화면에 닿아 본다 — 막혀야 한다
port=$(grep -o "화면 포트 [0-9]*" python-log.txt | head -1 | awk '{print $3}')
if [ -n "$port" ]; then
  answer=$(adb shell "printf 'GET /healthz HTTP/1.0\r\n\r\n' | toybox nc -w 5 127.0.0.1 $port" | head -1 | tr -d '\r')
  echo "열쇠 없이 접속한 결과: $answer" | tee -a python-log.txt
  case "$answer" in
    *" 403"*) ;;
    *) echo "다른 앱이 열쇠 없이 들어올 때 403 이 아닙니다"; exit 1 ;;
  esac
fi

if grep -q "Traceback" python-log.txt; then
  echo "파이썬 오류:"; grep -A30 "Traceback" python-log.txt | head -80; exit 1
fi
[ -n "$ok" ] || { echo "화면이 켜지지 않았습니다"; tail -80 python-log.txt; exit 1; }
echo "켜짐 — 포트 $port"
