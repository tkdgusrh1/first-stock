"""더블클릭 실행용 준비 스크립트.

시작 스크립트(.bat/.command)가 하던 일을 여기로 옮겼다. 이유:
  - cmd.exe 는 배치 파일에 한글(UTF-8)이 섞이면 코드페이지 문제로 오작동한다
  - 배치 파일은 줄바꿈이 CRLF 여야 하는데 편집 환경에 따라 쉽게 깨진다
파이썬은 두 문제 모두 없으므로, 배치 파일은 이 파일을 부르기만 한다.

표준 라이브러리만 쓴다. (requests/PyYAML 설치 전에 실행되기 때문)
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

MIN_PYTHON = (3, 9)
ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
IS_WINDOWS = os.name == "nt"

LOG_PATH = ROOT / "logs" / "실행기록.log"
PID_PATH = ROOT / "logs" / "실행중.pid"
MAX_LOG_BYTES = 5 * 1024 * 1024

DEFAULT_PORT = 8765
PORT_TRIES = 10          # 대시보드가 포트를 못 잡으면 +9 까지 옮겨 간다


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")


def venv_pythonw() -> Path:
    """창을 띄우지 않는 파이썬. 윈도우에만 있다."""
    quiet = VENV / "Scripts/pythonw.exe"
    return quiet if quiet.exists() else venv_python()


def say(*lines: str) -> None:
    for line in lines:
        print(line)


def fail(*lines: str) -> int:
    say("", *lines, "")
    pause()
    return 1


def pause() -> None:
    try:
        input("엔터를 누르면 창이 닫힙니다...")
    except (EOFError, KeyboardInterrupt):
        pass


def check_python() -> bool:
    if sys.version_info < MIN_PYTHON:
        current = ".".join(str(n) for n in sys.version_info[:3])
        say(
            f"❌ 파이썬 {MIN_PYTHON[0]}.{MIN_PYTHON[1]} 이상이 필요합니다. (지금: {current})",
            "",
            "   https://www.python.org/downloads/ 에서 최신 버전을 설치한 뒤",
            "   시작 파일을 다시 더블클릭해주세요.",
        )
        return False
    say(f"· 파이썬 {'.'.join(str(n) for n in sys.version_info[:3])} 확인")
    return True


def ensure_venv() -> bool:
    if venv_python().exists():
        return True

    say("· 처음 실행이라 준비를 좀 할게요 (1~2분)...")
    result = subprocess.run([sys.executable, "-m", "venv", str(VENV)], capture_output=True, text=True)
    if result.returncode != 0 or not venv_python().exists():
        detail = (result.stderr or result.stdout or "").strip()
        say("❌ 준비 공간(가상환경)을 만들지 못했습니다.")
        if "ensurepip" in detail or "python3-venv" in detail:
            say("", "   리눅스라면 아래 명령으로 필요한 패키지를 먼저 설치해주세요:",
                "       sudo apt install python3-venv")
        elif detail:
            say("", f"   원인: {detail.splitlines()[-1][:200]}")
        say("", "   폴더에 쓰기 권한이 없을 수도 있습니다.",
            "   바탕화면이나 다운로드 폴더로 옮긴 뒤 다시 시도해보세요.")
        return False
    return True


def ensure_packages() -> bool:
    python = str(venv_python())
    check = subprocess.run([python, "-c", "import requests, yaml"], capture_output=True)
    if check.returncode == 0:
        return True

    say("· 필요한 패키지를 설치하는 중...")
    subprocess.run([python, "-m", "pip", "install", "--quiet", "--upgrade", "pip"], capture_output=True)
    result = subprocess.run(
        [python, "-m", "pip", "install", "--quiet", "-r", str(ROOT / "requirements.txt")],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        say("❌ 패키지 설치에 실패했습니다.", "",
            "   인터넷 연결을 확인한 뒤 다시 시도해주세요.")
        if detail:
            say("", f"   원인: {detail.splitlines()[-1][:200]}")
        return False
    return True


# --------------------------------------------------------------------------
# 창 없이 뒤에서 돌리기
#
# 검은 창을 계속 띄워두면 실수로 닫아서 감시가 멈추고, 화면도 지저분하다.
# 그래서 준비와 설정 입력까지만 이 창에서 하고(물어볼 게 있으니 창이 필요하다),
# 실제 감시는 창 없는 프로세스로 넘긴 뒤 이 창은 닫는다.
# 대신 화면에 안 보이는 만큼 로그를 파일에 남긴다.
# --------------------------------------------------------------------------
def dashboard_port() -> int:
    """config.yml 에서 대시보드 포트를 읽는다. (yaml 없이 한 줄만 찾는다)"""
    import re

    try:
        text = (ROOT / "config.yml").read_text(encoding="utf-8")
    except OSError:
        return DEFAULT_PORT
    found = re.search(r"^\s+port\s*:\s*(\d+)", text, re.M)
    return int(found.group(1)) if found else DEFAULT_PORT


def running_url() -> str:
    """이미 돌고 있으면 그 주소를. 아니면 빈 문자열."""
    import urllib.error
    import urllib.request

    start = dashboard_port()
    for port in range(start, start + PORT_TRIES):
        url = f"http://127.0.0.1:{port}/"
        try:
            with urllib.request.urlopen(url + "healthz", timeout=1) as resp:
                if resp.read(8).strip() == b"ok":
                    return url
        except (urllib.error.URLError, OSError):
            continue
    return ""


def wait_until_up(seconds: float = 40.0) -> str:
    import time

    deadline = time.time() + seconds
    while time.time() < deadline:
        url = running_url()
        if url:
            return url
        time.sleep(0.5)
    return ""


def open_log():
    """로그 파일을 연다. 너무 커지면 지난 기록은 한 번 밀어둔다."""
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        if LOG_PATH.exists() and LOG_PATH.stat().st_size > MAX_LOG_BYTES:
            LOG_PATH.replace(LOG_PATH.with_suffix(".이전.log"))
    except OSError:
        pass
    return LOG_PATH.open("a", encoding="utf-8", errors="replace")


def launch_detached() -> None:
    """감시를 창 없는 프로세스로 넘긴다. 이 창을 닫아도 계속 돈다."""
    log = open_log()
    log.write(f"\n{'=' * 58}\n  감시 시작\n{'=' * 58}\n")
    log.flush()

    options = {}
    if IS_WINDOWS:
        # DETACHED_PROCESS: 이 콘솔에 묶이지 않는다 → 창을 닫아도 안 죽는다
        options["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    else:
        options["start_new_session"] = True

    child = subprocess.Popen(
        [str(venv_pythonw() if IS_WINDOWS else venv_python()), str(ROOT / "main.py"), "run"],
        cwd=str(ROOT), stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
        **options,
    )
    try:
        PID_PATH.write_text(str(child.pid), encoding="utf-8")
    except OSError:
        pass


def stop_running() -> int:
    """돌고 있는 감시를 멈춘다."""
    import signal

    try:
        pid = int(PID_PATH.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        say("", "돌고 있는 감시를 찾지 못했습니다.",
            "  (이미 멈췄거나, 다른 곳에서 시작한 것일 수 있습니다)", "")
        pause()
        return 0

    try:
        if IS_WINDOWS:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True, check=False)
        else:
            os.kill(pid, signal.SIGTERM)
    except (OSError, ProcessLookupError) as exc:
        say("", f"멈추지 못했습니다: {exc}", "")
        pause()
        return 1

    PID_PATH.unlink(missing_ok=True)
    say("", "감시를 멈췄습니다.", "  다시 시작하려면 '시작하기' 를 더블클릭하세요.", "")
    pause()
    return 0


def open_browser(url: str) -> bool:
    import webbrowser

    try:
        return bool(webbrowser.open(url))
    except Exception:
        return False


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "stop":
        return stop_running()

    say("", "=" * 58, "  관심 종목 감시 봇", "=" * 58, "")

    if not (ROOT / "main.py").exists():
        return fail(
            "❌ 프로그램 파일(main.py)을 찾을 수 없습니다.",
            "",
            "   ZIP 압축을 풀지 않고 그 안에서 바로 실행했거나,",
            "   시작 파일만 따로 옮겨진 것 같습니다.",
            "   ZIP 전체를 폴더에 푼 뒤, 그 폴더 안의 시작 파일을 더블클릭해주세요.",
            "",
            f"   현재 위치: {ROOT}",
        )

    if not check_python():
        pause()
        return 1

    already = running_url()
    if already:
        say("· 이미 뒤에서 돌고 있습니다. 화면만 다시 엽니다.", f"  {already}", "")
        open_browser(already)
        return 0

    if not ensure_venv() or not ensure_packages():
        pause()
        return 1

    # 설정은 물어볼 게 있어서 이 창에서 먼저 끝낸다.
    # (창을 없앤 뒤에는 질문을 띄울 자리가 없다)
    ready = subprocess.run(
        [str(venv_python()), str(ROOT / "main.py"), "ensure-config"], cwd=str(ROOT)
    )
    if ready.returncode != 0:
        say("", "설정이 끝나지 않아 시작하지 못했습니다.")
        pause()
        return 2

    say("", "· 감시를 뒤에서 시작합니다 (검은 창은 이제 닫힙니다)...")
    launch_detached()

    url = wait_until_up()
    if not url:
        say(
            "",
            "❌ 감시는 시작했는데 화면이 열리지 않습니다.",
            f"   무슨 일이 있었는지는 여기에 적혀 있습니다: {LOG_PATH}",
            "",
        )
        pause()
        return 1

    opened = open_browser(url)
    say(
        "",
        "=" * 58,
        "  ✅ 시작했습니다. 이제 브라우저 화면으로 보시면 됩니다.",
        "=" * 58,
        f"  화면    {url}",
        f"  기록    {LOG_PATH}",
        "",
        "  · 감시는 창 없이 뒤에서 계속 돕니다.",
        "  · 멈추려면 '끄기' 파일을 더블클릭하세요.",
        "",
    )
    if not opened:
        # 브라우저가 안 열렸다면 주소를 읽을 시간을 줘야 한다.
        say("  브라우저가 자동으로 열리지 않았습니다. 위 주소를 직접 열어주세요.", "")
        pause()
    return 0


if __name__ == "__main__":
    sys.exit(main())
