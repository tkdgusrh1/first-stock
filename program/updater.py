"""최신 코드로 갱신. 표준 라이브러리만 쓴다.

ZIP 을 매번 손으로 받아 푸는 과정에서 예전 버전이 계속 돌아가는 일이 잦아서,
더블클릭 한 번으로 코드만 갈아끼우도록 만들었다.
설정(config.yml)·기록(state.json)·직접 받아둔 파일은 건드리지 않는다.
"""

from __future__ import annotations

import shutil
import subprocess
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

# 코드는 이 program 폴더 안에 모여 있고, 바깥에는 '시작하기·업데이트·끄기' 만
# 둔다. 받은 ZIP 도 같은 모양이라, 안쪽은 안쪽끼리 바깥은 바깥끼리 맞춘다.
FOLDER_NAME = "program"


def outside() -> Path:
    """사용자가 누르는 파일들이 있는 바깥 폴더."""
    return ROOT.parent if ROOT.name == FOLDER_NAME else ROOT


# 저장소 이름을 바꿔도 따라가도록, git 설정에서 먼저 읽어본다.
# (ZIP 으로 받아 쓰는 경우에는 git 설정이 없으므로 위 기본값을 쓴다)
def _repo_from_git() -> str:
    import re

    for base in {ROOT, outside()}:
        config = base / ".git" / "config"
        if not config.exists():
            continue
        try:
            text = config.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        found = re.search(r"url\s*=\s*\S*github\.com[:/]([\w.-]+/[\w.-]+?)(?:\.git)?\s", text)
        if found:
            return found.group(1)
    return ""


REPO = _repo_from_git() or DEFAULT_REPO
ZIP_URL = f"https://codeload.github.com/{REPO}/zip/refs/heads/{BRANCH}"

# 예전 버전에서 쓰던 폴더. 갱신할 때 지운다. 남겨두면 무엇이 진짜인지 헷갈리고,
# 옛 코드가 import 되어 이상하게 도는 일이 생긴다.
OBSOLETE = ("stockbot",)

BACKUP_DIR = "이전버전"      # 갱신 직전 파일을 여기 보관한다

# 비공개 저장소에서 자동 업데이트가 막혔을 때 안내. 셋 다 실제로 되는 방법이다.
PRIVATE_HELP = (
    "저장소를 내려받지 못했습니다. 이 저장소가 비공개(private)라면 로그인 없이는 받을 수 없습니다. "
    "해결 방법 ① GitHub 저장소 Settings 맨 아래 Change visibility 에서 Public 으로 바꾸기 "
    "② 비공개를 유지하려면 config.yml 에 github_token: \"내 토큰\" 넣기 "
    "③ 지금 당장은 GitHub 에서 Code → Download ZIP 으로 받아 폴더에 덮어쓰기"
)

# 사용자 데이터는 절대 덮어쓰지 않는다
KEEP = {
    "config.yml",
    "state.json",
    "watchlist.local.yml",
    ".env",
    ".venv",
    ".cache",
    "logs",
    BACKUP_DIR,
    "__pycache__",
}
KEEP_PREFIXES = ("company_tickers",)   # 직접 받아둔 티커 목록


def _keep(name: str) -> bool:
    return name in KEEP or name.startswith(KEEP_PREFIXES)


# 비공개 저장소는 로그인 없이 내려받을 수 없다. 열쇠(토큰)가 있으면 쓴다.
# 환경변수 → config.yml 순으로 찾는다. 토큰은 절대 화면·로그에 찍지 않는다.
TOKEN_ENV = ("FIRST_STOCK_TOKEN", "GITHUB_TOKEN", "GH_TOKEN")


def github_token() -> str:
    import os
    import re

    for name in TOKEN_ENV:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    try:
        text = (ROOT / "config.yml").read_text(encoding="utf-8")
    except OSError:
        return ""
    found = re.search(r"""^\s*github_token\s*:\s*["']?([^"'\s#]+)""", text, re.M)
    return found.group(1) if found else ""


def _headers(extra: dict | None = None) -> dict:
    headers = {"User-Agent": "first-stock-updater"}
    token = github_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    headers.update(extra or {})
    return headers


def _download(url: str) -> bytes:
    request = urllib.request.Request(url, headers=_headers())
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


def _copy_outside(src: Path, dst: Path) -> tuple[int, list[str]]:
    """바깥 폴더의 '시작하기·업데이트·끄기' 와 설명서만 갈아끼운다.

    바깥에는 사용자가 누르는 파일만 있어야 한다. 그래서 **파일만** 옮기고
    폴더는 만들지 않는다 (안쪽 코드는 _copy_tree 가 따로 맡는다).
    """
    copied, failed = 0, []
    for item in sorted(src.iterdir()):
        if item.is_dir() or item.name.startswith(".") or _keep(item.name):
            continue
        try:
            shutil.copy2(item, dst / item.name)
            copied += 1
        except OSError as exc:
            log_debug(f"{item.name} 교체 실패: {exc}")
            failed.append(item.name)
    return copied, failed


def _install(src: Path, dst: Path) -> tuple[int, list[str], list[str]]:
    """새 코드를 깔고, 없어진 옛 파일을 치운다. (바꾼 수, 못 바꾼 것, 치운 것)

    받은 ZIP 안에 program 폴더가 있으면 그 **안쪽**을 지금 폴더에 맞춘다.
    그러지 않으면 program/program 처럼 한 겹 더 파고들어 가서, 갱신했다고
    말은 하는데 실제로 도는 코드는 그대로인 상태가 된다.
    """
    inner = src / FOLDER_NAME
    if inner.is_dir() and dst.name == FOLDER_NAME:
        copied, failed = _copy_tree(inner, dst)
        outer_copied, outer_failed = _copy_outside(src, dst.parent)
        return copied + outer_copied, failed + outer_failed, _drop_stale(inner, dst)

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
            VERSION_URL, headers=_headers({"Accept": "application/vnd.github+json"})
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
        if exc.code in (401, 403, 404):
            return False, PRIVATE_HELP
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


def _backup(files: list[str]) -> Path | None:
    """바꾸기 전 파일을 옮겨 담는다. 새 버전이 안 켜지면 이걸로 되돌린다."""
    folder = ROOT / BACKUP_DIR
    try:
        shutil.rmtree(folder, ignore_errors=True)
        for name in files:
            source = ROOT / name
            if not source.exists():
                continue
            target = folder / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        return folder
    except OSError as exc:
        log_debug(f"백업 실패: {exc}")
        return None


def _restore(folder: Path) -> bool:
    try:
        copied, failed = _copy_tree(folder, ROOT)
        return copied > 0 and not failed
    except OSError as exc:
        log_debug(f"되돌리기 실패: {exc}")
        return False


def _starts_up(python: Path) -> bool:
    """새 코드가 최소한 켜지기는 하는지 본다 (import 만 해본다)."""
    try:
        done = subprocess.run(
            [str(python), "-c", "import stock_analysis, main"],
            cwd=str(ROOT), capture_output=True, timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log_debug(f"새 코드 확인 실패: {exc}")
        return False
    if done.returncode != 0:
        log_debug(f"새 코드가 켜지지 않습니다: {done.stderr[-400:]!r}")
    return done.returncode == 0


def auto_update() -> tuple[bool, str]:
    """사람이 안 눌러도 알아서 갱신한다. 켜져 있는 프로그램이 스스로 부른다.

    자동으로 코드를 바꾸는 일이라 안전장치를 둔다.
      1) 바꾸기 전 파일을 백업한다
      2) 바꾼 뒤 새 코드가 켜지는지(import) 확인한다
      3) 안 켜지면 백업으로 되돌린다 — 자다 일어났더니 죽어 있으면 안 된다
    여기서는 다시 켜지 않는다. 켜는 일은 부른 쪽이 정한다.
    """
    before = [str(p.relative_to(ROOT)) for p in ROOT.rglob("*.py")
              if not any(_keep(part) for part in p.relative_to(ROOT).parts)]
    saved = _backup(before)

    ok, message = apply_update()
    if not ok:
        return False, message

    boot = _bootstrap()
    python = boot.venv_python() if boot else Path(sys.executable)
    if _starts_up(python):
        return True, message

    if saved and _restore(saved):
        return False, "새 버전이 제대로 켜지지 않아 이전 버전으로 되돌렸습니다."
    return False, "새 버전이 제대로 켜지지 않습니다. '업데이트' 를 다시 실행해주세요."


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


def ask_for_token() -> str:
    """비공개 저장소일 때 그 자리에서 열쇠를 받는다.

    안내문만 띄우고 끝내면 사용자는 창을 닫고 다시 막힌다. 어차피 여기까지
    왔으니 지금 붙여넣게 하는 편이 낫다. 한 번만 하면 다음부터는 그냥 된다.
    """
    print()
    print("  이 저장소는 비공개라 열쇠(토큰)가 있어야 자동으로 받을 수 있습니다.")
    print("  만드는 데 1분이면 됩니다.")
    print()
    print("   1. 아래 주소를 브라우저에 붙여넣기")
    print("      https://github.com/settings/personal-access-tokens/new")
    print("   2. Repository access → Only select repositories → first-stock 선택")
    print("   3. Permissions → Repository permissions → Contents 를 Read-only 로")
    print("   4. 맨 아래 Generate token → 나온 값(github_pat_... )을 복사")
    print()
    print("  (건너뛰려면 그냥 엔터. 저장소를 Public 으로 바꿔도 됩니다)")
    try:
        return input("  토큰 붙여넣기: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return ""


def save_token(token: str) -> bool:
    """config.yml 에 github_token 한 줄을 넣는다. 나머지 설정은 그대로 둔다."""
    import re

    path = ROOT / "config.yml"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False

    line = f'github_token: "{token}"'
    pattern = re.compile(r"^\s*github_token\s*:.*$", re.M)
    if pattern.search(text):
        text = pattern.sub(lambda _: line, text, count=1)
    else:
        text = text.rstrip("\n") + "\n\n# 비공개 저장소 자동 업데이트용 열쇠\n" + line + "\n"
    try:
        path.write_text(text, encoding="utf-8")
        return True
    except OSError as exc:
        log_debug(f"토큰 저장 실패: {exc}")
        return False


def try_token_and_save(token: str) -> bool:
    """열쇠가 실제로 통하는지 확인한 뒤에만 저장한다.

    안 통하는 값을 저장해두면 다음에도 똑같이 막히면서 원인만 헷갈려진다.
    """
    import os

    if not token:
        return False
    previous = os.environ.get("FIRST_STOCK_TOKEN")
    os.environ["FIRST_STOCK_TOKEN"] = token
    latest, _ = check_latest()
    if latest is None:
        if previous is None:
            os.environ.pop("FIRST_STOCK_TOKEN", None)
        else:
            os.environ["FIRST_STOCK_TOKEN"] = previous
        print("  ❌ 이 토큰으로는 저장소를 못 읽습니다. 권한(Contents: Read-only)과")
        print("     저장소 선택(first-stock)을 다시 확인해주세요.")
        return False

    print(f"  ✅ 열쇠가 통합니다. (저장소에서 버전 {latest} 확인)")
    if save_token(token):
        print("  config.yml 에 저장했습니다. 다음부터는 그냥 '업데이트' 만 누르시면 됩니다.")
    else:
        print("  다만 config.yml 에 저장하지 못했습니다. 이번에만 적용됩니다.")
    return True


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

    # 예전 구조(바깥에 코드가 흩어져 있던 시절)에서 넘어온 것이라면 먼저 정리한다.
    # 설정(config.yml)이 아직 바깥에 있으면 열쇠를 못 찾아 그대로 막힌다.
    boot = _bootstrap()
    if boot is not None and getattr(boot, "migrate", None):
        boot.migrate()

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

    # 비공개 저장소라 막힌 것이라면, 안내만 하고 끝내지 말고 지금 풀어준다.
    if not ok and message == PRIVATE_HELP:
        print()
        print("❌ 비공개 저장소라 자동으로 받지 못했습니다.")
        if try_token_and_save(ask_for_token()):
            print()
            print("· 다시 받아봅니다...")
            ok, message = update_with_restart()
        else:
            print()
            print(PRIVATE_HELP)
            pause()
            return 1

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
