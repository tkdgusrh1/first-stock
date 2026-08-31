"""최신 코드로 갱신. 표준 라이브러리만 쓴다.

ZIP 을 매번 손으로 받아 푸는 과정에서 예전 버전이 계속 돌아가는 일이 잦아서,
더블클릭 한 번으로 코드만 갈아끼우도록 만들었다.
설정(config.yml)·기록(state.json)·직접 받아둔 파일은 건드리지 않는다.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

DEFAULT_REPO = "tkdgusrh1/first-stock"
BRANCH = "claude/sec-edgar-monitoring-bot-5xnl1o"

ROOT = Path(__file__).resolve().parent

# 저장소 이름을 바꿔도 따라가도록, git 설정에서 먼저 읽어본다.
# (ZIP 으로 받아 쓰는 경우에는 git 설정이 없으므로 위 기본값을 쓴다)
def _repo_from_git() -> str:
    config = ROOT / ".git" / "config"
    if not config.exists():
        return ""
    try:
        text = config.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    import re

    found = re.search(r"url\s*=\s*\S*github\.com[:/]([\w.-]+/[\w.-]+?)(?:\.git)?\s", text)
    return found.group(1) if found else ""


REPO = _repo_from_git() or DEFAULT_REPO
ZIP_URL = f"https://codeload.github.com/{REPO}/zip/refs/heads/{BRANCH}"

# 예전 버전에서 쓰던 폴더. 갱신할 때 지운다. 남겨두면 무엇이 진짜인지 헷갈리고,
# 옛 코드가 import 되어 이상하게 도는 일이 생긴다.
OBSOLETE = ("stockbot",)

# 사용자 데이터는 절대 덮어쓰지 않는다
KEEP = {
    "config.yml",
    "state.json",
    "watchlist.local.yml",
    ".env",
    ".venv",
    ".cache",
    "logs",
    "__pycache__",
}
KEEP_PREFIXES = ("company_tickers",)   # 직접 받아둔 티커 목록


def _keep(name: str) -> bool:
    return name in KEEP or name.startswith(KEEP_PREFIXES)


def _download(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "first-stock-updater"})
    with urllib.request.urlopen(request, timeout=60) as resp:
        return resp.read()


def _drop_obsolete(dst: Path) -> list[str]:
    """이름이 바뀌기 전 폴더를 치운다."""
    removed = []
    for name in OBSOLETE:
        old = dst / name
        if old.exists():
            shutil.rmtree(old, ignore_errors=True)
            removed.append(name)
    return removed


def _copy_tree(src: Path, dst: Path) -> tuple[int, list[str]]:
    """새 파일을 덮어쓴다. (바꾼 개수, 못 바꾼 파일들)

    폴더를 통째로 지웠다가 다시 만들지 않는다. 윈도우에서는 프로그램이 돌고
    있으면 폴더 안 파일 하나가 잠겨 있어도 삭제가 반쯤 실패하고, 그 뒤
    copytree 가 'File exists' 로 터진다. 업데이트가 실패하던 실제 원인이었다.

    한 파일이 잠겨 있어도 나머지는 계속 바꾸고, 못 바꾼 것만 모아서 알려준다.
    """
    copied, failed = 0, []
    for item in sorted(src.rglob("*")):
        relative = item.relative_to(src)
        if any(_keep(part) for part in relative.parts):
            continue
        target = dst / relative
        try:
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, target)
                copied += 1
        except OSError as exc:
            log_debug(f"{relative} 교체 실패: {exc}")
            failed.append(str(relative))
    return copied, failed


def _drop_stale(src: Path, dst: Path) -> list[str]:
    """새 버전에 없어진 예전 파일을 치운다.

    폴더를 통째로 지우는 대신 파일 하나씩 지운다. 하나가 잠겨 있어도
    나머지는 정리되고, 업데이트 전체가 실패하지 않는다.
    """
    removed = []
    for folder in [p for p in src.iterdir() if p.is_dir() and not _keep(p.name)]:
        here = dst / folder.name
        if not here.is_dir():
            continue
        for item in sorted(here.rglob("*"), reverse=True):   # 자식부터
            relative = item.relative_to(dst)
            if any(_keep(part) for part in relative.parts) or (src / relative).exists():
                continue
            try:
                if item.is_dir():
                    item.rmdir()                              # 비어 있을 때만
                else:
                    item.unlink()
                    removed.append(str(relative))
            except OSError as exc:
                log_debug(f"{relative} 정리 실패: {exc}")
    return removed


def _install(src: Path, dst: Path) -> tuple[int, list[str], list[str]]:
    """새 코드를 깔고, 없어진 옛 파일을 치운다. (바꾼 수, 못 바꾼 것, 치운 것)"""
    copied, failed = _copy_tree(src, dst)
    return copied, failed, _drop_stale(src, dst)


# 브랜치 이름에 '/' 가 있어 raw.githubusercontent.com 은 경로가 모호해진다.
# 그래서 ref 를 따로 넘길 수 있는 contents API 를 쓴다.
VERSION_URL = (
    f"https://api.github.com/repos/{REPO}/contents/stock_analysis/__init__.py?ref={BRANCH}"
)


def _version_tuple(text: str) -> tuple:
    return tuple(int(part) if part.isdigit() else 0 for part in str(text).split("."))


def check_latest(timeout: float = 15.0) -> tuple[str | None, bool]:
    """(최신 버전, 지금보다 새로운가). 확인 실패하면 (None, False)."""
    import base64
    import json
    import re

    try:
        request = urllib.request.Request(
            VERSION_URL,
            headers={"User-Agent": "first-stock-updater", "Accept": "application/vnd.github+json"},
        )
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        text = base64.b64decode(payload.get("content", "")).decode("utf-8", "replace")
    except Exception as exc:
        log_debug(f"버전 확인 실패: {exc}")
        return None, False

    match = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    if not match:
        return None, False
    latest = match.group(1)
    return latest, _version_tuple(latest) > _version_tuple(current_version())


def apply_update(timeout: float = 60.0) -> tuple[bool, str]:
    """코드를 최신으로 교체한다. (성공 여부, 메시지)

    돌고 있는 프로그램은 건드리지 않는다. 화면의 '지금 업데이트' 버튼이
    이 함수를 부르는데, 거기서 자기 자신을 멈추면 안 되기 때문이다.
    """
    try:
        payload = _download(ZIP_URL)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return False, "저장소를 찾지 못했습니다(비공개일 수 있습니다). 직접 내려받아 주세요."
        return False, f"내려받기 실패: HTTP {exc.code}"
    except Exception as exc:
        return False, f"내려받기 실패: {exc}"

    try:
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            archive = tmpdir / "source.zip"
            archive.write_bytes(payload)
            with zipfile.ZipFile(archive) as zf:
                zf.extractall(tmpdir)
            roots = [p for p in tmpdir.iterdir() if p.is_dir()]
            if not roots:
                return False, "받은 파일이 비어 있습니다."
            count, failed, stale = _install(roots[0], ROOT)
            dropped = _drop_obsolete(ROOT)
    except Exception as exc:
        return False, f"교체 중 오류: {exc}"

    if failed:
        shown = ", ".join(failed[:4]) + (f" 외 {len(failed) - 4}개" if len(failed) > 4 else "")
        return False, (
            f"{len(failed)}개 파일을 바꾸지 못했습니다 ({shown}). "
            "프로그램이 돌고 있으면 파일이 잠깁니다. '끄기' 를 먼저 하고 다시 시도해주세요."
        )

    message = f"파일 {count}개를 갱신했습니다."
    if stale:
        message += f" 없어진 옛 파일 {len(stale)}개는 정리했습니다."
    if dropped:
        message += f" 예전 폴더({', '.join(dropped)})는 정리했습니다."
    return True, message


def update_with_restart() -> tuple[bool, str]:
    """멈추고 → 갱신하고 → 원래 돌고 있었으면 다시 켠다.

    업데이트가 실패하던 가장 큰 이유가 '돌고 있는 채로 파일을 바꾸려 한 것'
    이었다. 사용자가 순서를 기억할 필요 없게 여기서 알아서 한다.
    """
    boot = _bootstrap()
    was_running = False
    if boot is not None:
        was_running = bool(boot.running_pids()) or bool(boot.running_url())
        if was_running:
            print("· 돌고 있는 감시를 잠깐 멈춥니다...")
            for pid in boot.running_pids():
                boot.kill(pid)
            time.sleep(1.5)
            if boot.running_pids():
                boot.ask_dashboard_to_quit()

    ok, message = apply_update()

    if not ok:
        return ok, message
    if not was_running:
        return ok, message + " '시작하기' 를 누르면 새 버전으로 시작합니다."
    if boot is None:
        return ok, message + " '시작하기' 를 다시 눌러주세요."
    try:
        boot.launch_detached()
        return ok, message + " 감시를 다시 켰습니다. (잠시 뒤 화면이 열립니다)"
    except Exception as exc:
        log_debug(f"다시 켜기 실패: {exc}")
        return ok, message + " 다시 켜지 못했으니 '시작하기' 를 눌러주세요."


def _bootstrap():
    """같은 폴더의 bootstrap 을 빌려 쓴다 (멈추기·다시 켜기)."""
    try:
        sys.path.insert(0, str(ROOT))
        import bootstrap

        return bootstrap
    except Exception as exc:
        log_debug(f"bootstrap 을 불러오지 못했습니다: {exc}")
        return None


def log_debug(message: str) -> None:
    import logging

    logging.getLogger("stock_analysis.updater").debug(message)


def current_version() -> str:
    """지금 폴더에 깔린 버전. **파일에서 직접 읽는다.**

    import 로 읽으면 이미 메모리에 올라온 값이 나온다. 업데이트를 마친 뒤에도
    바뀌기 전 버전이 찍혀서 '갱신했다는데 버전이 그대로' 로 보였다.
    """
    import re

    try:
        text = (ROOT / "stock_analysis" / "__init__.py").read_text(encoding="utf-8")
    except OSError:
        return "알 수 없음"
    found = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    return found.group(1) if found else "알 수 없음"


def main() -> int:
    print()
    print("=" * 58)
    print("  최신 버전으로 갱신")
    print("=" * 58)
    print(f"  현재 버전: {current_version()}")
    print(f"  받는 곳  : {REPO} ({BRANCH})")
    print()

    latest, newer = check_latest()
    if latest and not newer:
        print(f"✅ 이미 최신 버전입니다. (최신 {latest})")
        pause()
        return 0
    if latest:
        print(f"· 새 버전 {latest} 을(를) 받는 중...")
    else:
        print("· 버전 확인은 못 했지만 그대로 받아봅니다...")

    ok, message = update_with_restart()
    print()
    print(("✅ " if ok else "❌ ") + message)
    if ok:
        print(f"   새 버전: {current_version()}")
    print()
    pause()
    return 0 if ok else 1


def pause() -> None:
    try:
        input("엔터를 누르면 창이 닫힙니다...")
    except (EOFError, KeyboardInterrupt):
        pass


if __name__ == "__main__":
    sys.exit(main())
