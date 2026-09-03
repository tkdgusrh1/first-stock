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
import time
from pathlib import Path

MIN_PYTHON = (3, 9)
ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
IS_WINDOWS = os.name == "nt"

# 예전에는 코드와 설정이 바깥 폴더에 전부 흩어져 있었다. 지금은 이 program
# 폴더 안에 모아 두고, 바깥에는 '시작하기·업데이트·끄기' 만 보이게 한다.
# 예전 구조로 쓰던 사람도 그대로 이어 쓸 수 있게 옮겨준다 (아래 migrate).
FOLDER_NAME = "program"


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


# --------------------------------------------------------------------------
# 예전 구조에서 넘어오기
#
# 예전 폴더는 이랬다:  main.py, bootstrap.py, stock_analysis/, config.yml, ...
# 지금은 그게 전부 program/ 안으로 들어갔다. 업데이트로 새 구조를 받은 사람은
# 바깥에 예전 파일이 그대로 남아 있는데, 그걸 두면
#   · 설정(config.yml)을 못 찾아 처음 실행처럼 다시 물어보고
#   · 예전 코드가 계속 돌면서 "업데이트했는데 그대로" 가 된다.
# 그래서 새 코드가 처음 켜질 때 한 번, 여기서 정리한다.
# --------------------------------------------------------------------------

# 사용자가 만든 것 — 옮긴다. 절대 지우지 않는다.
MINE = ("config.yml", "state.json", "watchlist.local.yml", ".env",
        ".cache", "logs", "이전버전")
MINE_PREFIXES = ("company_tickers",)

# 프로그램이 만든 것 — 새 위치에 다시 생기므로 치운다.
THEIRS = ("main.py", "bootstrap.py", "updater.py", "requirements.txt",
          "pytest.ini", "config.example.yml", "stock_analysis", "data", "tests",
          ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "stockbot")

# 개발용 파일. 받아 쓰는 사람에게는 눈에만 걸린다.
# 다만 git 으로 받아 쓰는 폴더라면 지우면 안 되니 그때는 그냥 둔다.
THEIRS_DEV = (".github", ".gitattributes", ".gitignore")


def outside() -> Path | None:
    """사용자가 누르는 파일들이 있는 바깥 폴더. 예전 구조면 None."""
    return ROOT.parent if ROOT.name == FOLDER_NAME else None


def legacy_root() -> Path | None:
    """예전 구조가 바깥에 남아 있으면 그 폴더. 아니면 None."""
    old = outside()
    if old is None:
        return None
    return old if (old / "main.py").exists() else None


def _take(source: Path, target: Path) -> None:
    """사용자 파일을 새 위치로 옮긴다. 이미 있으면 예전 것을 백업에 넣어 둔다."""
    import shutil

    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(target))
        return
    keep = ROOT / "이전버전" / source.name
    keep.parent.mkdir(parents=True, exist_ok=True)
    if keep.is_dir():
        shutil.rmtree(keep, ignore_errors=True)
    else:
        keep.unlink(missing_ok=True)
    shutil.move(str(source), str(keep))


def migrate() -> bool:
    """예전 폴더의 설정·기록을 가져오고 예전 코드를 치운다. 옮겼으면 True."""
    import shutil

    old = legacy_root()
    if old is None:
        return False

    say("· 폴더 구조가 바뀌었습니다. 쓰던 설정을 그대로 옮길게요...")

    # 예전 코드가 돌고 있으면 먼저 멈춘다. 안 그러면 옛 버전이 계속 돈다.
    for pid in running_pids():
        kill(pid)
    if running_url():
        ask_dashboard_to_quit()
    time.sleep(1.0)

    # git 으로 받아 쓰는 폴더면 개발용 파일을 지우면 안 된다 (되돌릴 수 없다)
    junk = THEIRS if (old / ".git").is_dir() else THEIRS + THEIRS_DEV

    moved = []
    for item in sorted(old.iterdir()):
        if item.name == FOLDER_NAME:
            continue
        if item.name in MINE or item.name.startswith(MINE_PREFIXES):
            try:
                _take(item, ROOT / item.name)
                moved.append(item.name)
            except OSError as exc:
                say(f"  · {item.name} 을(를) 옮기지 못했습니다: {exc}")
        elif item.name in junk:
            try:
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()
            except OSError as exc:
                log_note(f"{item.name} 정리 실패: {exc}")

    say(f"  → 끝났습니다. 옮긴 것: {', '.join(moved) or '없음'}",
        "  (준비 공간은 새로 만듭니다. 1~2분 걸릴 수 있어요)", "")
    return True


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

    # 출력이 로그 파일로 가면 윈도우는 cp949 로 쓴다. 거기엔 '🚨' 가 없어서
    # 이모지 한 글자에 기능이 통째로 멈춘다. 파이썬 자체를 UTF-8 로 못박는다.
    child = subprocess.Popen(
        [str(venv_pythonw() if IS_WINDOWS else venv_python()), str(ROOT / "main.py"), "run"],
        cwd=str(ROOT), stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
        env=dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8:replace"),
        **options,
    )
    try:
        PID_PATH.write_text(str(child.pid), encoding="utf-8")
    except OSError:
        pass


def ask_dashboard_to_quit() -> bool:
    """화면에 종료를 부탁한다. 기록해둔 번호(PID)가 없어도 이 길이 있다."""
    import urllib.error
    import urllib.request

    url = running_url()
    if not url:
        return False
    try:
        request = urllib.request.Request(url + "action", data=b"action=quit")
        urllib.request.urlopen(request, timeout=5).read()
    except (urllib.error.URLError, OSError):
        pass
    for _ in range(20):                 # 실제로 멈췄는지 확인한다
        if not running_url():
            return True
        time.sleep(0.5)
    return False


def _is_python(name: str) -> bool:
    return Path(name).name.lower().startswith(("python", "pythonw"))


def _python_processes() -> list[tuple[int, list[str], str]]:
    """돌고 있는 파이썬 프로세스 [(번호, 인자 목록, 명령줄 전체)].

    인자 목록은 리눅스·맥에서 정확하다. 윈도우는 명령줄이 한 덩어리로만
    오므로 인자 목록이 비고, 그때는 명령줄 전체로 대조한다.
    """
    mine = {os.getpid(), os.getppid()}
    found: list[tuple[int, str]] = []

    if IS_WINDOWS:
        script = (
            "Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*' } "
            "| ForEach-Object { \"$($_.ProcessId)`t$($_.CommandLine)\" }"
        )
        try:
            done = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True, text=True, timeout=40,
            )
        except (OSError, subprocess.SubprocessError):
            return []
        for line in done.stdout.splitlines():
            number, _, command = line.partition("\t")
            if number.strip().isdigit() and int(number) not in mine:
                found.append((int(number), [], command))
        return found

    proc = Path("/proc")
    if proc.is_dir():                          # 리눅스
        for entry in proc.iterdir():
            if not entry.name.isdigit() or int(entry.name) in mine:
                continue
            try:
                raw = (entry / "cmdline").read_bytes().decode("utf-8", "replace")
            except OSError:
                continue
            args = [a for a in raw.split("\0") if a]
            if args and _is_python(args[0]):
                found.append((int(entry.name), args, " ".join(args)))
        return found

    try:                                        # 맥
        done = subprocess.run(["ps", "-Ao", "pid=,command="],
                              capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return []
    for line in done.stdout.splitlines():
        number, _, command = line.strip().partition(" ")
        args = command.split()
        if number.isdigit() and int(number) not in mine and args and _is_python(args[0]):
            found.append((int(number), args, command))
    return found


def _main_py_in(command: str) -> str:
    """명령줄에서 main.py 경로를 뽑는다. 없으면 빈 문자열.

    윈도우 경로에는 빈칸이 들어갈 수 있어 통째로 훑는다.
    """
    import re

    found = re.search(r'([A-Za-z]:\\[^"\n]*?main\.py)', command)
    if found:
        return found.group(1)
    for token in command.split():
        if token.endswith("main.py"):
            return token
    return ""


def _is_our_program(main_py: str) -> bool:
    """그 main.py 가 정말 이 프로그램의 것인지.

    같은 폴더에 stock_analysis/app.py 가 있어야 한다. 이 확인이 없으면
    이름이 main.py 인 남의 파이썬 프로그램까지 끄게 된다.
    """
    try:
        return (Path(main_py).resolve().parent / "stock_analysis" / "app.py").exists()
    except OSError:
        return False


def running_pids() -> list[int]:
    """이 폴더의 main.py 를 돌리고 있는 파이썬을 **직접 찾아낸다.**

    적어둔 번호 파일이 지워졌든, 포트가 바뀌었든, 여러 개가 떠 있든 상관없이
    실제로 돌고 있는 것을 찾는다. 끄기가 확실해야 폴더를 지울 수 있다.

    조건을 좁게 잡는 게 중요하다. '경로가 들어 있는 프로세스' 를 다 잡으면
    이 폴더 이야기를 하고 있을 뿐인 터미널·편집기까지 끄게 된다.
    그래서 **파이썬이면서, 인자가 정확히 이 폴더의 main.py 인 것**만 고른다.

    폴더 구조가 바뀌기 전(바깥의 main.py)에 시작된 것도 같이 찾는다.
    그걸 못 찾으면 옛 버전이 계속 돌면서 포트를 쥐고 있게 된다.
    """
    wanted = {str(ROOT / "main.py")}
    old = outside()
    if old is not None:
        wanted.add(str(old / "main.py"))
    found = []
    for pid, args, command in _python_processes():
        # 인자를 볼 수 있으면 **정확히 일치**하는 것만 (부분일치는 위험하다.
        # 이 폴더 이야기를 하고 있을 뿐인 터미널까지 끄게 된다)
        if args:
            if wanted & set(args[1:]):
                found.append(pid)
        elif any(path in command for path in wanted):
            found.append(pid)          # 윈도우는 명령줄밖에 못 본다
    return found


def other_folder_pids() -> list[tuple[int, str]]:
    """**다른 폴더**에서 돌고 있는 같은 프로그램. [(번호, 폴더)]

    폴더를 옮기거나 복사해 쓰다 보면 옛 폴더의 프로그램이 계속 돈다.
    그러면 윈도우가 그 폴더를 붙잡고 있어서 지울 수가 없는데, 정작
    끄기는 자기 폴더 것만 찾으니 영영 못 끄는 상태가 된다.

    아무거나 끄지 않기 위해 두 가지를 모두 확인한다.
      · 파이썬이 main.py 를 돌리고 있고
      · 그 main.py 옆에 stock_analysis/app.py 가 있다 (이 프로그램이 맞다)
    """
    here = {str(ROOT)}
    old = outside()
    if old is not None:
        here.add(str(old))

    found: list[tuple[int, str]] = []
    for pid, args, command in _python_processes():
        main_py = _main_py_in(command) if not args else next(
            (a for a in args[1:] if a.endswith("main.py")), "")
        if not main_py or not _is_our_program(main_py):
            continue
        folder = str(Path(main_py).resolve().parent)
        if folder not in here:
            found.append((pid, folder))
    return found


def kill(pid: int) -> None:
    import signal

    try:
        if IS_WINDOWS:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True, check=False)
        else:
            os.kill(pid, signal.SIGTERM)
    except (OSError, ProcessLookupError) as exc:
        log_note(f"번호 {pid} 를 끝내지 못했습니다: {exc}")


def stop_running() -> int:
    """돌고 있는 감시를 멈춘다.

    한 가지 방법만 믿지 않는다. 세 가지를 다 해보고, **정말 멈췄는지 확인한
    다음에** 멈췄다고 말한다. 끄기가 안 되면 폴더를 지울 수도 없기 때문이다.
      1) 시작할 때 적어둔 번호로 끝내기
      2) 이 폴더의 main.py 를 돌리는 파이썬을 찾아서 끝내기 (번호 파일이 없어도 됨)
      3) 그래도 남아 있으면 화면에 종료를 부탁하기
    """
    targets = set(running_pids())
    try:
        targets.add(int(PID_PATH.read_text(encoding="utf-8").strip()))
    except (OSError, ValueError):
        pass

    if not targets and not running_url():
        PID_PATH.unlink(missing_ok=True)
        say("", "돌고 있는 감시가 없습니다.",
            "  (이미 멈췄거나, 다른 폴더에서 시작한 것일 수 있습니다)", "")
        pause()
        return 0

    say("", f"· 돌고 있는 것 {len(targets)}개를 멈추는 중...")
    for pid in targets:
        kill(pid)
    time.sleep(1.5)

    if running_pids() or running_url():
        ask_dashboard_to_quit()

    # 이 폴더 것을 다 껐는데도 뭔가 남았다면, 옛 폴더에서 돌던 것일 수 있다.
    # 폴더를 옮기거나 새로 받아 쓰면 이런 일이 생기는데, 그러면 윈도우가
    # 옛 폴더를 붙잡고 있어서 지울 수가 없다.
    others = other_folder_pids()
    if others:
        say("", "· 다른 폴더에서 돌고 있는 같은 프로그램도 찾았습니다:")
        for pid, folder in others:
            say(f"    번호 {pid}  {folder}")
        for pid, _folder in others:
            kill(pid)
        time.sleep(1.5)

    left = running_pids()
    if left or running_url():
        say(
            "",
            "❌ 아직 멈추지 않은 것이 있습니다.",
            f"   남은 번호: {', '.join(str(p) for p in left) or '알 수 없음'}",
            "",
            "   아래 중 하나를 해주세요.",
            "   1) 작업 관리자(Ctrl+Shift+Esc) → 세부 정보 → pythonw.exe 끝내기",
            "   2) 컴퓨터를 다시 켜기 (가장 확실합니다)",
            "",
        )
        pause()
        return 1

    PID_PATH.unlink(missing_ok=True)
    say("", "감시를 멈췄습니다."
        + (f" (다른 폴더 {len(others)}개 포함)" if others else ""),
        "  다시 시작하려면 '시작하기' 를 더블클릭하세요.",
        "  이제 이 폴더를 지우거나 옮길 수 있습니다.", "")
    pause()
    return 0


def log_note(message: str) -> None:
    try:
        with open_log() as fh:
            fh.write(message + "\n")
    except OSError:
        pass


def open_browser(url: str) -> bool:
    import webbrowser

    try:
        return bool(webbrowser.open(url))
    except Exception:
        return False


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "stop":
        migrate()                      # 예전 구조로 돌던 것도 같이 멈춘다
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

    # 예전 구조에서 넘어왔다면 여기서 정리한다. '이미 돌고 있나' 를 보기 전에
    # 해야 한다 — 옛 코드가 돌고 있으면 그걸 그대로 열어버리기 때문이다.
    migrate()

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
