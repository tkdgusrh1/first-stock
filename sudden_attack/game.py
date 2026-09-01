"""서든어택이 어디에 깔려 있는지 찾는다.

몇몇 항목(전체 화면 최적화 끄기, 프로세스 우선순위, 백신 검사 제외)은 실행 파일
경로를 알아야 손댈 수 있다. 넥슨 런처는 설치 폴더를 사용자가 정할 수 있어서 한 곳에
박혀 있지 않다. 그래서 세 갈래로 찾는다.

  1) 프로그램 추가/제거 목록(레지스트리)에 적힌 설치 위치
  2) 넥슨이 쓰는 흔한 폴더들
  3) 그래도 못 찾으면 — 사용자가 화면에서 직접 경로를 넣는다

못 찾았으면 못 찾았다고 말한다. 아무 exe 나 골라잡지 않는다.
"""

from __future__ import annotations

import logging
import string
import sys
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

WINDOWS = sys.platform.startswith("win")

UNINSTALL_KEYS = [
    ("HKLM", r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ("HKLM", r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    ("HKCU", r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
]

# 이름에 이게 들어 있으면 서든어택으로 본다.
NAME_HINTS = ("suddenattack", "sudden attack", "서든어택", "서든")

# 넥슨 게임이 흔히 들어가는 자리. 드라이브는 실제로 있는 것만 훑는다.
FOLDER_HINTS = [
    r"Nexon",
    r"Program Files (x86)\Nexon",
    r"Program Files\Nexon",
    r"Games\Nexon",
    r"Nexon\Library",
]


@dataclass(frozen=True)
class Install:
    exe: Path
    folder: Path
    source: str          # 어디서 찾았는지 — 화면에 그대로 보여준다

    @property
    def exe_name(self) -> str:
        return self.exe.name


def find(registry=None, roots=None, saved: str | None = None) -> Install | None:
    """설치 위치를 찾는다. 못 찾으면 None."""
    if saved:
        found = _from_path(Path(saved), "직접 넣은 경로")
        if found:
            return found
        log.info("적어두신 경로에서 실행 파일을 못 찾았습니다: %s", saved)

    if registry is not None:
        found = _from_registry(registry)
        if found:
            return found

    for root in roots if roots is not None else _drives():
        for hint in FOLDER_HINTS:
            found = _scan(Path(root) / hint, "설치 폴더에서 찾음")
            if found:
                return found
    return None


def _from_registry(registry) -> Install | None:
    for root, base in UNINSTALL_KEYS:
        for name in registry.subkeys(root, base):
            path = f"{base}\\{name}"
            title = registry.read(root, path, "DisplayName")
            label = str(title.data).lower() if title and title.data else name.lower()
            if not any(hint in label for hint in NAME_HINTS):
                continue
            location = registry.read(root, path, "InstallLocation")
            if location and location.data:
                found = _scan(Path(str(location.data)), "프로그램 목록에 적힌 위치")
                if found:
                    return found
    return None


def _scan(folder: Path, source: str, depth: int = 2) -> Install | None:
    """폴더 아래에서 서든어택 실행 파일을 찾는다. 깊이를 제한해 오래 안 걸리게."""
    try:
        if not folder.is_dir():
            return None
    except OSError:
        return None

    found = _exe_in(folder, source)
    if found:
        return found
    if depth <= 0:
        return None

    try:
        children = sorted(folder.iterdir())
    except OSError:
        return None
    for child in children:
        try:
            if not child.is_dir():
                continue
        except OSError:
            continue
        # 이름이 관련 없어 보이는 폴더까지 다 뒤지면 느려진다. 한 단계는 봐준다.
        if depth == 2 and not any(hint in child.name.lower() for hint in NAME_HINTS + ("nexon",)):
            continue
        found = _scan(child, source, depth - 1)
        if found:
            return found
    return None


def _exe_in(folder: Path, source: str) -> Install | None:
    try:
        entries = sorted(folder.glob("*.exe"))
    except OSError:
        return None
    for exe in entries:
        stem = exe.stem.lower().replace(" ", "").replace("_", "")
        if stem.startswith("sudden") or stem in ("sa", "sa_main"):
            return Install(exe=exe, folder=folder, source=source)
    return None


def _from_path(given: Path, source: str) -> Install | None:
    try:
        if given.is_file() and given.suffix.lower() == ".exe":
            return Install(exe=given, folder=given.parent, source=source)
        if given.is_dir():
            return _exe_in(given, source) or _scan(given, source, depth=1)
    except OSError:
        return None
    return None


def _drives() -> list[str]:
    if not WINDOWS:
        return []
    drives = []
    for letter in string.ascii_uppercase:
        root = f"{letter}:\\"
        try:
            if Path(root).exists():
                drives.append(root)
        except OSError:
            continue
    return drives
