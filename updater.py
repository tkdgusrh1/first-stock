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

    try:
        print("· 내려받는 중...")
        payload = _download(ZIP_URL)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            print("❌ 저장소를 찾지 못했습니다 (404).")
            print("   저장소가 비공개이면 이 방법으로는 받을 수 없습니다.")
            print(f"   https://github.com/{REPO} 에서 직접 ZIP 을 받아주세요.")
        else:
            print(f"❌ 내려받기 실패: HTTP {exc.code}")
        input("\n엔터를 누르면 창이 닫힙니다...")
        return 1
    except Exception as exc:
        print(f"❌ 내려받기 실패: {exc}")
        print("   인터넷 연결을 확인해주세요.")
        input("\n엔터를 누르면 창이 닫힙니다...")
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        archive = tmpdir / "source.zip"
        archive.write_bytes(payload)

        print("· 압축 푸는 중...")
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(tmpdir)

        roots = [p for p in tmpdir.iterdir() if p.is_dir()]
        if not roots:
            print("❌ 받은 파일이 비어 있습니다.")
            input("\n엔터를 누르면 창이 닫힙니다...")
            return 1

        print("· 코드 교체 중... (설정과 기록은 그대로 둡니다)")
        count = _copy_tree(roots[0], ROOT)

    print()
    print(f"✅ 갱신 완료. 파일 {count}개를 새로 받았습니다.")
    print(f"   새 버전: {current_version()}  ← 파이썬을 다시 실행하면 반영됩니다")
    print()
    print("   이제 '시작하기' 를 다시 더블클릭하세요.")
    print()
    input("엔터를 누르면 창이 닫힙니다...")
    return 0


if __name__ == "__main__":
    sys.exit(main())
