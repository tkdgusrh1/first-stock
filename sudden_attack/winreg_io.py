"""레지스트리 읽기·쓰기.

최적화 항목의 대부분은 결국 레지스트리 값 하나를 바꾸는 일이다. 그래서 이 파일은
"값 하나"를 다루는 것만 책임지고, 무엇을 바꿀지는 tweaks.py 가 정한다.

윈도우가 아니면 winreg 모듈 자체가 없다. 개발·검사는 리눅스에서 돌아가야 하므로
같은 생김새의 가짜 저장소(FakeRegistry)를 같이 둔다. 덕분에 "적용 → 되돌리기"가
정말 원래 값으로 돌아가는지 윈도우 없이도 검사할 수 있다.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass

log = logging.getLogger(__name__)

WINDOWS = sys.platform.startswith("win")

# 값의 종류. winreg 상수를 그대로 쓰면 윈도우가 아닌 곳에서 import 가 깨진다.
DWORD = "dword"
QWORD = "qword"
STR = "str"
BINARY = "binary"


@dataclass(frozen=True)
class RegValue:
    """레지스트리 값 하나. data 는 정수(dword/qword), 문자열(str), bytes(binary)."""

    data: object
    kind: str = DWORD

    def as_json(self) -> dict:
        if self.kind == BINARY and isinstance(self.data, (bytes, bytearray)):
            return {"kind": self.kind, "data": bytes(self.data).hex(), "hex": True}
        return {"kind": self.kind, "data": self.data}

    @classmethod
    def from_json(cls, raw: dict) -> "RegValue":
        data = raw.get("data")
        if raw.get("hex"):
            data = bytes.fromhex(str(data))
        return cls(data=data, kind=str(raw.get("kind") or DWORD))


class RegistryError(RuntimeError):
    """권한이 없거나 경로가 막혔을 때."""


class FakeRegistry:
    """메모리 위에서만 도는 저장소. 검사와 윈도우가 아닌 곳의 미리보기용."""

    def __init__(self, seed: dict | None = None):
        # {(root, path소문자): {name소문자: (원래이름, RegValue)}}
        self._store: dict[tuple[str, str], dict[str, tuple[str, RegValue]]] = {}
        for (root, path, name), value in (seed or {}).items():
            self.write(root, path, name, value)

    # --- 읽기 -----------------------------------------------------------
    def read(self, root: str, path: str, name: str) -> RegValue | None:
        entries = self._store.get((root, path.lower()))
        if not entries:
            return None
        found = entries.get(name.lower())
        return found[1] if found else None

    def key_exists(self, root: str, path: str) -> bool:
        return (root, path.lower()) in self._store

    def subkeys(self, root: str, path: str) -> list[str]:
        prefix = path.lower().rstrip("\\") + "\\"
        names: list[str] = []
        for stored_root, stored_path in self._store:
            if stored_root != root or not stored_path.startswith(prefix):
                continue
            tail = stored_path[len(prefix):].split("\\")[0]
            if tail and tail not in names:
                names.append(tail)
        return sorted(names)

    # --- 쓰기 -----------------------------------------------------------
    def write(self, root: str, path: str, name: str, value: RegValue) -> None:
        entries = self._store.setdefault((root, path.lower()), {})
        entries[name.lower()] = (name, value)

    def delete_value(self, root: str, path: str, name: str) -> None:
        entries = self._store.get((root, path.lower()))
        if entries:
            entries.pop(name.lower(), None)

    def delete_key(self, root: str, path: str) -> None:
        """빈 키만 지운다. 아래에 뭔가 남아 있으면 그냥 둔다."""
        target = path.lower()
        if any(p.startswith(target + "\\") for _, p in self._store):
            return
        entries = self._store.get((root, target))
        if entries:
            return
        self._store.pop((root, target), None)


class WindowsRegistry:
    """진짜 레지스트리. 32/64비트 갈림 없이 항상 64비트 쪽을 본다."""

    def __init__(self):
        import winreg  # 윈도우에서만 import 된다

        self._winreg = winreg
        self._roots = {"HKCU": winreg.HKEY_CURRENT_USER, "HKLM": winreg.HKEY_LOCAL_MACHINE}
        self._kinds = {
            DWORD: winreg.REG_DWORD,
            QWORD: winreg.REG_QWORD,
            STR: winreg.REG_SZ,
            BINARY: winreg.REG_BINARY,
        }
        self._back = {v: k for k, v in self._kinds.items()}
        self._wow64 = winreg.KEY_WOW64_64KEY

    def _root(self, root: str):
        try:
            return self._roots[root]
        except KeyError:
            raise RegistryError(f"모르는 최상위 키: {root}") from None

    # --- 읽기 -----------------------------------------------------------
    def read(self, root: str, path: str, name: str) -> RegValue | None:
        winreg = self._winreg
        try:
            with winreg.OpenKey(self._root(root), path, 0, winreg.KEY_READ | self._wow64) as key:
                data, kind = winreg.QueryValueEx(key, name)
        except FileNotFoundError:
            return None
        except OSError as exc:
            log.debug("레지스트리 읽기 실패 %s\\%s\\%s: %s", root, path, name, exc)
            return None
        return RegValue(data=data, kind=self._back.get(kind, STR))

    def key_exists(self, root: str, path: str) -> bool:
        winreg = self._winreg
        try:
            with winreg.OpenKey(self._root(root), path, 0, winreg.KEY_READ | self._wow64):
                return True
        except OSError:
            return False

    def subkeys(self, root: str, path: str) -> list[str]:
        winreg = self._winreg
        names: list[str] = []
        try:
            with winreg.OpenKey(self._root(root), path, 0, winreg.KEY_READ | self._wow64) as key:
                index = 0
                while True:
                    try:
                        names.append(winreg.EnumKey(key, index))
                    except OSError:
                        break
                    index += 1
        except OSError as exc:
            log.debug("하위 키 조회 실패 %s\\%s: %s", root, path, exc)
        return names

    # --- 쓰기 -----------------------------------------------------------
    def write(self, root: str, path: str, name: str, value: RegValue) -> None:
        winreg = self._winreg
        kind = self._kinds.get(value.kind, winreg.REG_SZ)
        try:
            key = winreg.CreateKeyEx(self._root(root), path, 0, winreg.KEY_WRITE | self._wow64)
            with key:
                winreg.SetValueEx(key, name, 0, kind, value.data)
        except PermissionError as exc:
            raise RegistryError(f"권한이 없습니다: {root}\\{path}\\{name}") from exc
        except OSError as exc:
            raise RegistryError(f"쓰지 못했습니다: {root}\\{path}\\{name} ({exc})") from exc

    def delete_value(self, root: str, path: str, name: str) -> None:
        winreg = self._winreg
        try:
            key = winreg.OpenKey(self._root(root), path, 0, winreg.KEY_SET_VALUE | self._wow64)
            with key:
                winreg.DeleteValue(key, name)
        except FileNotFoundError:
            return          # 이미 없다 — 되돌리기에서는 이게 정상이다
        except OSError as exc:
            raise RegistryError(f"지우지 못했습니다: {root}\\{path}\\{name} ({exc})") from exc

    def delete_key(self, root: str, path: str) -> None:
        """빈 키만 지운다.

        윈도우의 DeleteKeyEx 는 값이 남아 있어도 통째로 지운다. 되돌리기가 남의 설정까지
        쓸어버리면 안 되므로, 비었는지 직접 확인하고 나서 지운다.
        """
        winreg = self._winreg
        try:
            with winreg.OpenKey(self._root(root), path, 0,
                                winreg.KEY_READ | self._wow64) as key:
                subkeys, values, _ = winreg.QueryInfoKey(key)
            if subkeys or values:
                log.debug("비어 있지 않아 그냥 둡니다: %s\\%s", root, path)
                return
            winreg.DeleteKeyEx(self._root(root), path, self._wow64, 0)
        except OSError as exc:
            log.debug("키를 지우지 못했습니다 %s\\%s: %s", root, path, exc)


def open_registry():
    """이 컴퓨터에 맞는 저장소를 준다. 윈도우가 아니면 가짜를 준다."""
    if WINDOWS:
        try:
            return WindowsRegistry()
        except Exception as exc:      # pragma: no cover - 윈도우 전용
            log.warning("레지스트리를 열지 못했습니다(%s). 미리보기로만 돕니다.", exc)
    return FakeRegistry()
