"""최신 코드로 갱신. 표준 라이브러리만 쓴다.

ZIP 을 매번 손으로 받아 푸는 과정에서 예전 버전이 계속 돌아가는 일이 잦아서,
더블클릭 한 번으로 코드만 갈아끼우도록 만들었다.
설정(config.yml)·기록(state.json)·직접 받아둔 파일은 건드리지 않는다.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

REPO = "tkdgusrh1/first-stock"
BRANCH = "claude/sec-edgar-monitoring-bot-5xnl1o"
ZIP_URL = f"https://codeload.github.com/{REPO}/zip/refs/heads/{BRANCH}"

ROOT = Path(__file__).resolve().parent

# 사용자 데이터는 절대 덮어쓰지 않는다
KEEP = {
    "config.yml",
    "state.json",
    "watchlist.local.yml",
    ".env",
    ".venv",
    ".cache",
    "__pycache__",
}
KEEP_PREFIXES = ("company_tickers",)   # 직접 받아둔 티커 목록


def _keep(name: str) -> bool:
    return name in KEEP or name.startswith(KEEP_PREFIXES)


def _download(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "first-stock-updater"})
    with urllib.request.urlopen(request, timeout=60) as resp:
        return resp.read()


def _copy_tree(src: Path, dst: Path) -> int:
    copied = 0
    for item in src.iterdir():
        if _keep(item.name):
            continue
        target = dst / item.name
        if item.is_dir():
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
            shutil.copytree(item, target)
            copied += sum(1 for _ in target.rglob("*") if _.is_file())
        else:
            shutil.copy2(item, target)
            copied += 1
    return copied


# 브랜치 이름에 '/' 가 있어 raw.githubusercontent.com 은 경로가 모호해진다.
# 그래서 ref 를 따로 넘길 수 있는 contents API 를 쓴다.
VERSION_URL = (
    f"https://api.github.com/repos/{REPO}/contents/stockbot/__init__.py?ref={BRANCH}"
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
    """코드를 최신으로 교체한다. (성공 여부, 메시지)"""
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
            count = _copy_tree(roots[0], ROOT)
    except Exception as exc:
        return False, f"교체 중 오류: {exc}"

    return True, f"파일 {count}개를 갱신했습니다. 봇을 재시작하면 새 버전으로 동작합니다."


def log_debug(message: str) -> None:
    import logging

    logging.getLogger("stockbot.updater").debug(message)


def current_version() -> str:
    try:
        sys.path.insert(0, str(ROOT))
        from stockbot import __version__

        return __version__
    except Exception:
        return "알 수 없음"


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
        input("\n엔터를 누르면 창이 닫힙니다...")
        return 0
    if latest:
        print(f"· 새 버전 {latest} 을(를) 받는 중...")
    else:
        print("· 버전 확인은 못 했지만 그대로 받아봅니다...")

    ok, message = apply_update()
    print()
    print(("✅ " if ok else "❌ ") + message)
    if ok:
        print(f"   새 버전: {current_version()}")
        print()
        print("   이제 '시작하기' 를 다시 더블클릭하세요.")
    print()
    input("엔터를 누르면 창이 닫힙니다...")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
