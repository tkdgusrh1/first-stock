#!/usr/bin/env python3
"""서든어택 최적화 — 윈도우 설정을 게임에 맞게 한 번에 바꾸고, 언제든 되돌린다.

**이 파일 하나가 프로그램 전부입니다.** 파이썬만 있으면 다른 건 아무것도 필요 없고,
이 폴더째 복사해서 다른 컴퓨터에 옮겨도 그대로 돕니다.

쓰는 법
    서든어택 최적화.bat 을 더블클릭 → 브라우저에 화면이 뜹니다 → 파란 버튼 하나.
    되돌리려면 되돌리기.bat, 또는 화면의 '원래대로 되돌리기' 버튼.

    터미널에서 쓰려면:
        python optimizer.py            화면 띄우기 (기본)
        python optimizer.py status     지금 상태만 출력
        python optimizer.py apply      권장 항목 바로 적용
        python optimizer.py revert     마지막 최적화 되돌리기

파일 안 지도 — 고칠 일이 있으면 [7] 만 보시면 됩니다
    [1]  레지스트리     윈도우 설정값을 읽고 쓴다
    [2]  명령 실행      powercfg · PowerShell · 관리자 권한
    [3]  모니터         주사율 확인과 변경
    [4]  컴퓨터 정보    CPU · 그래픽 · 메모리 · 윈도우
    [5]  게임 찾기      서든어택이 어디 깔려 있나
    [6]  되돌리기 기록  바꾸기 전 값을 파일로 남긴다
    [7]  최적화 항목    ← 항목을 더하고 빼는 곳
    [8]  실행기         적용하고, 기록하고, 되돌린다
    [9]  안내문         자동으로 못 바꾸는 것들
    [10] 화면           브라우저에 뜨는 페이지
    [11] 시작 지점      더블클릭과 명령줄

지키는 것
    · 되돌릴 수 있는 것만 바꾼다. 바꾸기 전 값을 못 읽는 설정은 아예 안 넣었다.
    · 부팅을 건드리지 않는다 (bcdedit · 페이지 파일 · 서비스 정지 전부 뺐다).
    · 효과를 부풀리지 않는다. 작은 건 작다고 적는다.
    · 게임 파일은 건드리지 않는다. 윈도우 설정만 바꾼다.

윈도우 전용이지만, 윈도우 API 를 쓰는 부분은 전부 가짜로 바꿔 끼울 수 있어서
다른 운영체제에서도 화면을 열어보고 검사를 돌릴 수 있다 (tests/ 참고).
"""

from __future__ import annotations

import argparse
import ctypes
import html
import json
import logging
import os
import platform
import re
import socket
import string
import subprocess
import sys
import threading
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# 화면 아래에 표시된다. 무엇이 돌고 있는지 바로 확인할 수 있게 올려둔다.
__version__ = "2.1.0"

log = logging.getLogger("서든어택최적화")

# 윈도우가 아니면 winreg 도 powercfg 도 없다. 화면은 열리되 아무것도 바꾸지 않는다.
WINDOWS = sys.platform.startswith("win")

ROOT = Path(__file__).resolve().parent


# ============================================================================
# [1] 레지스트리 — 윈도우 설정값을 읽고 쓴다
# ============================================================================
#
# 레지스트리 읽기·쓰기.
#
# 최적화 항목의 대부분은 결국 레지스트리 값 하나를 바꾸는 일이다. 그래서 이 파일은
# "값 하나"를 다루는 것만 책임지고, 무엇을 바꿀지는 [7] 최적화 항목이 정한다.
#
# 윈도우가 아니면 winreg 모듈 자체가 없다. 개발·검사는 리눅스에서 돌아가야 하므로
# 같은 생김새의 가짜 저장소(FakeRegistry)를 같이 둔다. 덕분에 "적용 → 되돌리기"가
# 정말 원래 값으로 돌아가는지 윈도우 없이도 검사할 수 있다.

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


# ============================================================================
# [2] 명령 실행 — powercfg · PowerShell · 관리자 권한
# ============================================================================
#
# 바깥 명령 실행(powercfg, netsh, PowerShell)과 관리자 권한 확인.
#
# 레지스트리로 안 되는 것들 — 전원 계획, 백신 예외 — 은 결국 명령을 부르게 된다.
# 그 호출을 한 군데로 모아두면 검사할 때 가짜 실행기를 끼워 넣기 쉽다.

# 창을 띄우지 않고 부른다. 최적화 중에 검은 창이 계속 깜빡이면 고장 난 줄 안다.
_NO_WINDOW = 0x08000000 if WINDOWS else 0


@dataclass
class Result:
    ok: bool
    out: str = ""
    err: str = ""
    code: int = 0


class Shell:
    """진짜 명령 실행기."""

    def run(self, args: list[str], timeout: int = 60) -> Result:
        log.debug("실행: %s", " ".join(args))
        try:
            done = subprocess.run(
                args,
                capture_output=True,
                timeout=timeout,
                creationflags=_NO_WINDOW,
            )
        except FileNotFoundError:
            return Result(ok=False, err=f"{args[0]} 을(를) 찾지 못했습니다.", code=-1)
        except subprocess.TimeoutExpired:
            return Result(ok=False, err=f"{args[0]} 이(가) 응답하지 않습니다.", code=-1)
        except OSError as exc:
            return Result(ok=False, err=str(exc), code=-1)

        # 윈도우 명령들은 한국어 윈도우에서 cp949 로 뱉는다. 깨진 글자 하나 때문에
        # 최적화가 통째로 멈추면 안 되니 못 읽는 글자는 버리고 넘어간다.
        out = _decode(done.stdout)
        err = _decode(done.stderr)
        return Result(ok=done.returncode == 0, out=out, err=err, code=done.returncode)

    def powershell(self, script: str, timeout: int = 90) -> Result:
        return self.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            timeout=timeout,
        )


@dataclass
class FakeShell:
    """검사용. 부른 명령을 적어두고, 미리 정해둔 답을 돌려준다."""

    replies: dict = field(default_factory=dict)
    calls: list = field(default_factory=list)
    default: Result = field(default_factory=lambda: Result(ok=True))

    def run(self, args: list[str], timeout: int = 60) -> Result:
        self.calls.append(list(args))
        joined = " ".join(args).lower()
        for needle, reply in self.replies.items():
            if needle.lower() in joined:
                return reply
        return self.default

    def powershell(self, script: str, timeout: int = 90) -> Result:
        return self.run(["powershell", "-Command", script], timeout=timeout)


def _decode(raw: bytes | None) -> str:
    if not raw:
        return ""
    for encoding in ("utf-8", "cp949", "cp1252"):
        try:
            return raw.decode(encoding).strip()
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace").strip()


def is_admin() -> bool:
    """관리자 권한으로 돌고 있는가.

    HKLM 을 건드리는 항목(네트워크·시스템 반응성 등)은 관리자가 아니면 실패한다.
    실패한 뒤에 알려주면 늦으니 화면에 먼저 띄우려고 확인한다.
    """
    if not WINDOWS:
        return os.geteuid() == 0 if hasattr(os, "geteuid") else False
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:       # pragma: no cover - 윈도우 전용
        return False


def relaunch_as_admin(script: str) -> bool:
    """관리자 권한으로 자기 자신을 다시 띄운다. 띄웠으면 True."""
    if not WINDOWS:
        return False
    try:                    # pragma: no cover - 윈도우 전용
        import ctypes

        from pathlib import Path

        target = Path(script).resolve()
        rc = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, f'"{target}"', str(target.parent), 1
        )
        return rc > 32
    except Exception as exc:  # pragma: no cover - 윈도우 전용
        log.warning("관리자 권한으로 다시 띄우지 못했습니다: %s", exc)
        return False


def open_shell():
    return Shell() if WINDOWS else FakeShell()


# ============================================================================
# [3] 모니터 — 주사율 확인과 변경
# ============================================================================
#
# 모니터 주사율 확인·변경.
#
# 의외로 이게 체감이 제일 크다. 144Hz 모니터를 사놓고 윈도우가 60Hz 로 잡아둔 채
# 쓰는 경우가 흔한데, 그러면 게임 안에서 프레임이 아무리 나와도 눈에 보이는 건
# 초당 60장이다. 그래서 "설치돼 있는 모니터가 낼 수 있는 최대"로 올려준다.
#
# 윈도우 API(ctypes)를 직접 부른다. 바꾼 값은 되돌리기용으로 그대로 넘겨준다.

ENUM_CURRENT_SETTINGS = -1
CDS_UPDATEREGISTRY = 0x00000001
CDS_TEST = 0x00000002
DISP_CHANGE_SUCCESSFUL = 0

DM_BITSPERPEL = 0x00040000
DM_PELSWIDTH = 0x00080000
DM_PELSHEIGHT = 0x00100000
DM_DISPLAYFREQUENCY = 0x00400000

DISPLAY_DEVICE_ATTACHED_TO_DESKTOP = 0x00000001


@dataclass
class Monitor:
    device: str          # \\.\DISPLAY1
    label: str           # 사람이 읽는 이름
    width: int
    height: int
    hz: int
    best_hz: int         # 지금 해상도에서 낼 수 있는 최대

    @property
    def already_best(self) -> bool:
        return self.best_hz <= self.hz


class _POINTL(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class DEVMODEW(ctypes.Structure):
    _fields_ = [
        ("dmDeviceName", ctypes.c_wchar * 32),
        ("dmSpecVersion", ctypes.c_ushort),
        ("dmDriverVersion", ctypes.c_ushort),
        ("dmSize", ctypes.c_ushort),
        ("dmDriverExtra", ctypes.c_ushort),
        ("dmFields", ctypes.c_ulong),
        ("dmPosition", _POINTL),
        ("dmDisplayOrientation", ctypes.c_ulong),
        ("dmDisplayFixedOutput", ctypes.c_ulong),
        ("dmColor", ctypes.c_short),
        ("dmDuplex", ctypes.c_short),
        ("dmYResolution", ctypes.c_short),
        ("dmTTOption", ctypes.c_short),
        ("dmCollate", ctypes.c_short),
        ("dmFormName", ctypes.c_wchar * 32),
        ("dmLogPixels", ctypes.c_ushort),
        ("dmBitsPerPel", ctypes.c_ulong),
        ("dmPelsWidth", ctypes.c_ulong),
        ("dmPelsHeight", ctypes.c_ulong),
        ("dmDisplayFlags", ctypes.c_ulong),
        ("dmDisplayFrequency", ctypes.c_ulong),
        ("dmICMMethod", ctypes.c_ulong),
        ("dmICMIntent", ctypes.c_ulong),
        ("dmMediaType", ctypes.c_ulong),
        ("dmDitherType", ctypes.c_ulong),
        ("dmReserved1", ctypes.c_ulong),
        ("dmReserved2", ctypes.c_ulong),
        ("dmPanningWidth", ctypes.c_ulong),
        ("dmPanningHeight", ctypes.c_ulong),
    ]


class DISPLAY_DEVICEW(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_ulong),
        ("DeviceName", ctypes.c_wchar * 32),
        ("DeviceString", ctypes.c_wchar * 128),
        ("StateFlags", ctypes.c_ulong),
        ("DeviceID", ctypes.c_wchar * 128),
        ("DeviceKey", ctypes.c_wchar * 128),
    ]


class WindowsDisplay:
    """진짜 모니터."""

    def monitors(self) -> list[Monitor]:
        user32 = ctypes.windll.user32
        found: list[Monitor] = []
        index = 0
        while True:
            device = DISPLAY_DEVICEW()
            device.cb = ctypes.sizeof(DISPLAY_DEVICEW)
            if not user32.EnumDisplayDevicesW(None, index, ctypes.byref(device), 0):
                break
            index += 1
            if not device.StateFlags & DISPLAY_DEVICE_ATTACHED_TO_DESKTOP:
                continue

            current = DEVMODEW()
            current.dmSize = ctypes.sizeof(DEVMODEW)
            if not user32.EnumDisplaySettingsW(
                device.DeviceName, ENUM_CURRENT_SETTINGS, ctypes.byref(current)
            ):
                continue

            best = self._best_hz(device.DeviceName, current)
            found.append(
                Monitor(
                    device=device.DeviceName,
                    label=device.DeviceString or device.DeviceName,
                    width=int(current.dmPelsWidth),
                    height=int(current.dmPelsHeight),
                    hz=int(current.dmDisplayFrequency),
                    best_hz=best,
                )
            )
        return found

    def _best_hz(self, name: str, current: DEVMODEW) -> int:
        """지금 해상도·색 심도를 유지한 채 낼 수 있는 가장 높은 주사율.

        해상도까지 같이 올리지는 않는다. 해상도는 취향이고, 멋대로 바꾸면
        게임 설정이 어긋나거나 글자가 작아져서 놀라게 된다.
        """
        user32 = ctypes.windll.user32
        best = int(current.dmDisplayFrequency)
        mode = DEVMODEW()
        mode.dmSize = ctypes.sizeof(DEVMODEW)
        number = 0
        while user32.EnumDisplaySettingsW(name, number, ctypes.byref(mode)):
            number += 1
            if (
                mode.dmPelsWidth == current.dmPelsWidth
                and mode.dmPelsHeight == current.dmPelsHeight
                and mode.dmBitsPerPel == current.dmBitsPerPel
                # 1Hz 는 "드라이버가 알아서" 라는 뜻이라 올리면 안 된다
                and 1 < int(mode.dmDisplayFrequency) < 1000
            ):
                best = max(best, int(mode.dmDisplayFrequency))
        return best

    def set_hz(self, device: str, hz: int) -> bool:
        user32 = ctypes.windll.user32
        mode = DEVMODEW()
        mode.dmSize = ctypes.sizeof(DEVMODEW)
        if not user32.EnumDisplaySettingsW(device, ENUM_CURRENT_SETTINGS, ctypes.byref(mode)):
            return False
        mode.dmDisplayFrequency = hz
        mode.dmFields = DM_PELSWIDTH | DM_PELSHEIGHT | DM_BITSPERPEL | DM_DISPLAYFREQUENCY

        # 먼저 시험만 해본다. 모니터가 못 받는 값을 그냥 넣으면 화면이 까맣게 죽는다.
        tested = user32.ChangeDisplaySettingsExW(device, ctypes.byref(mode), None, CDS_TEST, None)
        if tested != DISP_CHANGE_SUCCESSFUL:
            log.warning("%s 을(를) %dHz 로 바꿀 수 없습니다(코드 %s).", device, hz, tested)
            return False

        applied = user32.ChangeDisplaySettingsExW(
            device, ctypes.byref(mode), None, CDS_UPDATEREGISTRY, None
        )
        if applied != DISP_CHANGE_SUCCESSFUL:
            log.warning("%s 주사율 변경 실패(코드 %s).", device, applied)
            return False
        return True


@dataclass
class FakeDisplay:
    """검사·미리보기용."""

    screens: list = field(default_factory=list)
    changes: list = field(default_factory=list)
    fail: bool = False

    def monitors(self) -> list[Monitor]:
        return list(self.screens)

    def set_hz(self, device: str, hz: int) -> bool:
        self.changes.append((device, hz))
        if self.fail:
            return False
        for screen in self.screens:
            if screen.device == device:
                screen.hz = hz
        return True


def open_display():
    if WINDOWS:
        try:
            return WindowsDisplay()
        except Exception as exc:      # pragma: no cover - 윈도우 전용
            log.warning("모니터 정보를 읽지 못했습니다: %s", exc)
    return FakeDisplay()


# ============================================================================
# [4] 컴퓨터 정보 — CPU · 그래픽 · 메모리 · 윈도우
# ============================================================================
#
# 이 컴퓨터가 어떤 물건인지 읽어온다.
#
# 화면 맨 위에 그대로 보여준다. 최적화를 누르기 전에 "내 컴퓨터를 제대로 보고 있구나"
# 를 확인할 수 있어야 안심하고 누를 수 있다. 값을 못 읽으면 지어내지 않고 비워둔다.

CPU_KEY = r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
GPU_KEY = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
WINDOWS_KEY = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion"


@dataclass
class Spec:
    cpu: str = ""
    cores: int = 0
    ram_gb: float = 0.0
    gpus: list = field(default_factory=list)
    windows: str = ""
    monitors: list = field(default_factory=list)
    laptop: bool = False

    @property
    def gpu(self) -> str:
        return " · ".join(self.gpus)

    @property
    def shape(self) -> str:
        return "노트북" if self.laptop else "데스크톱"


def read_spec(registry, display=None) -> Spec:
    spec = Spec(
        cpu=_cpu(registry),
        cores=os.cpu_count() or 0,
        ram_gb=_ram_gb(),
        gpus=_gpus(registry),
        windows=_windows(registry),
        laptop=is_laptop(),
    )
    if display is not None:
        try:
            spec.monitors = display.monitors()
        except Exception as exc:
            log.debug("모니터를 읽지 못했습니다: %s", exc)
    return spec


def _cpu(registry) -> str:
    value = registry.read("HKLM", CPU_KEY, "ProcessorNameString")
    if value and value.data:
        return " ".join(str(value.data).split())
    return platform.processor() or platform.machine()


def _gpus(registry) -> list[str]:
    """그래픽 드라이버가 등록해 둔 이름을 읽는다. 노트북은 두 개가 잡힌다."""
    found: list[str] = []
    for child in registry.subkeys("HKLM", GPU_KEY):
        if not child.isdigit():         # Configuration, Properties 같은 건 카드가 아니다
            continue
        value = registry.read("HKLM", f"{GPU_KEY}\\{child}", "DriverDesc")
        if value and value.data:
            name = str(value.data).strip()
            if name and name not in found:
                found.append(name)
    return found


def _windows(registry) -> str:
    product = registry.read("HKLM", WINDOWS_KEY, "ProductName")
    release = registry.read("HKLM", WINDOWS_KEY, "DisplayVersion")
    build = registry.read("HKLM", WINDOWS_KEY, "CurrentBuild")
    parts = [str(v.data).strip() for v in (product, release) if v and v.data]
    if build and build.data:
        # 윈도우 11 은 레지스트리에 아직 'Windows 10' 이라고 적혀 있다. 빌드로 바로잡는다.
        try:
            if int(str(build.data)) >= 22000 and parts and "10" in parts[0]:
                parts[0] = parts[0].replace("10", "11")
        except ValueError:
            pass
        parts.append(f"빌드 {build.data}")
    if parts:
        return " · ".join(parts)
    return f"{platform.system()} {platform.release()}"


class _MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def _ram_gb() -> float:
    if WINDOWS:
        try:                        # pragma: no cover - 윈도우 전용
            status = _MEMORYSTATUSEX()
            status.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return round(status.ullTotalPhys / (1024 ** 3), 1)
        except Exception as exc:    # pragma: no cover - 윈도우 전용
            log.debug("메모리 크기를 읽지 못했습니다: %s", exc)
        return 0.0
    try:
        return round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / (1024 ** 3), 1)
    except (ValueError, OSError, AttributeError):
        return 0.0


class _SYSTEM_POWER_STATUS(ctypes.Structure):
    _fields_ = [
        ("ACLineStatus", ctypes.c_ubyte),
        ("BatteryFlag", ctypes.c_ubyte),
        ("BatteryLifePercent", ctypes.c_ubyte),
        ("SystemStatusFlag", ctypes.c_ubyte),
        ("BatteryLifeTime", ctypes.c_ulong),
        ("BatteryFullLifeTime", ctypes.c_ulong),
    ]


def is_laptop() -> bool:
    """배터리가 달려 있으면 노트북으로 본다.

    같은 항목이라도 노트북과 데스크톱에서 체감이 다르다. 특히 전원 계획이 그렇다.
    노트북은 CPU 가 절전하려고 속도를 크게 낮춰서 차이가 확 나는데, 데스크톱은
    원래 잘 안 낮춘다. "이 컴퓨터에서는 어떤가" 를 말하려면 이걸 알아야 한다.
    """
    if not WINDOWS:
        return False
    try:                            # pragma: no cover - 윈도우 전용
        status = _SYSTEM_POWER_STATUS()
        if ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
            return status.BatteryFlag != 128     # 128 = 시스템 배터리 없음
    except Exception as exc:        # pragma: no cover - 윈도우 전용
        log.debug("배터리를 확인하지 못했습니다: %s", exc)
    return False


# ============================================================================
# [5] 게임 찾기 — 서든어택이 어디 깔려 있나
# ============================================================================
#
# 서든어택이 어디에 깔려 있는지 찾는다.
#
# 몇몇 항목(전체 화면 최적화 끄기, 프로세스 우선순위, 백신 검사 제외)은 실행 파일
# 경로를 알아야 손댈 수 있다. 넥슨 런처는 설치 폴더를 사용자가 정할 수 있어서 한 곳에
# 박혀 있지 않다. 그래서 세 갈래로 찾는다.
#
#   1) 프로그램 추가/제거 목록(레지스트리)에 적힌 설치 위치
#   2) 넥슨이 쓰는 흔한 폴더들
#   3) 그래도 못 찾으면 — 사용자가 화면에서 직접 경로를 넣는다
#
# 못 찾았으면 못 찾았다고 말한다. 아무 exe 나 골라잡지 않는다.

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


def find_game(registry=None, roots=None, saved: str | None = None) -> Install | None:
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


# ============================================================================
# [6] 되돌리기 기록 — 바꾸기 전 값을 파일로 남긴다
# ============================================================================
#
# 바꾸기 전 값을 파일로 남긴다.
#
# 이 프로그램에서 제일 중요한 파일이다. 최적화는 언제든 되돌릴 수 있어야 하고,
# 되돌리기의 근거는 오직 여기 적힌 '원래 값'이다. 그래서 적용은 기록을 먼저 저장한
# 뒤에 한다 — 중간에 전원이 나가도 되돌릴 근거는 남아 있어야 한다.

BACKUP_FOLDER = "backup"
RECORD_PREFIX = "최적화기록-"


@dataclass
class Record:
    path: Path
    when: datetime
    entries: dict = field(default_factory=dict)
    computer: str = ""
    reverted: str = ""

    @property
    def label(self) -> str:
        return self.when.strftime("%Y년 %m월 %d일 %H:%M")

    @property
    def keys(self) -> list[str]:
        return list(self.entries)


def backup_folder(root: Path | None = None) -> Path:
    base = Path(root) if root else ROOT
    return base / BACKUP_FOLDER


def save_record(entries: dict, root: Path | None = None, when: datetime | None = None) -> Path:
    """되돌리기 기록을 새 파일로 남기고 그 경로를 준다."""
    when = when or datetime.now()
    target = backup_folder(root)
    target.mkdir(parents=True, exist_ok=True)
    path = target / f"{RECORD_PREFIX}{when:%Y%m%d-%H%M%S}.json"

    payload = {
        "when": when.isoformat(timespec="seconds"),
        "computer": _computer(),
        "entries": entries,
    }
    # 임시 파일에 다 쓰고 나서 이름을 바꾼다. 쓰다 만 기록이 남으면 되돌리기가 막힌다.
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)
    log.info("되돌리기 기록을 남겼습니다: %s", path.name)
    return path


def record_history(root: Path | None = None) -> list[Record]:
    """새 기록이 앞에 오도록 정렬해서 전부 준다."""
    target = backup_folder(root)
    if not target.is_dir():
        return []
    records = []
    for path in sorted(target.glob(f"{RECORD_PREFIX}*.json"), reverse=True):
        record = load_record(path)
        if record:
            records.append(record)
    return records


def latest_record(root: Path | None = None, include_reverted: bool = False) -> Record | None:
    for record in record_history(root):
        if record.reverted and not include_reverted:
            continue
        return record
    return None


def load_record(path: Path) -> Record | None:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("기록을 읽지 못했습니다 %s: %s", path, exc)
        return None
    if not isinstance(raw, dict):
        return None
    return Record(
        path=Path(path),
        when=_parse(raw.get("when"), Path(path)),
        entries=raw.get("entries") or {},
        computer=str(raw.get("computer") or ""),
        reverted=str(raw.get("reverted") or ""),
    )


def mark_reverted(record: Record, when: datetime | None = None) -> None:
    """되돌린 기록에 표시를 남긴다. 같은 기록으로 두 번 되돌리지 않기 위해서다."""
    when = when or datetime.now()
    try:
        raw = json.loads(record.path.read_text(encoding="utf-8"))
        raw["reverted"] = when.isoformat(timespec="seconds")
        record.path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        record.reverted = raw["reverted"]
    except (OSError, ValueError) as exc:
        log.warning("되돌림 표시를 남기지 못했습니다: %s", exc)


def _parse(value, path: Path) -> datetime:
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        try:
            return datetime.fromtimestamp(path.stat().st_mtime)
        except OSError:
            return datetime.now()


def _computer() -> str:
    try:
        return socket.gethostname()
    except OSError:
        return ""


# ============================================================================
# [7] 최적화 항목 — 항목을 더하고 빼는 곳
# ============================================================================
#
# 무엇을 바꿀 것인가 — 최적화 항목 전부.
#
# 원칙 네 가지를 지킨다.
#
#   1. **되돌릴 수 있는 것만 넣는다.** 바꾸기 전 값을 못 읽어오는 설정은 아예 안 넣는다.
#   2. **부팅을 건드리지 않는다.** bcdedit(HPET·플랫폼 클럭), 페이지 파일 삭제,
#      서비스 무더기 정지 같은 건 넣지 않았다. 효과는 불확실한데 잘못되면 윈도우가
#      안 켜지거나 소리가 안 난다. "최적화 프로그램 돌렸더니 컴퓨터가 이상해졌다"는
#      사고는 대부분 여기서 난다.
#   3. **효과를 부풀리지 않는다.** 항목마다 왜 하는지 한 줄로 적어두고, 체감이 작은
#      것은 작다고 쓴다.
#   4. **위험한 건 기본으로 켜지 않는다.** recommended=False 는 사용자가 직접 눌러야 한다.

ON = "on"           # 이미 적용돼 있다
OFF = "off"         # 아직 아니다
UNKNOWN = "unknown"  # 읽어봤는데 판단이 안 선다
NA = "na"           # 이 컴퓨터에서는 해당 없음 (게임을 못 찾았다 등)

# 게임 안에서 얼마나 느껴지는가. 화면에 그대로 뱃지로 나간다.
BIG = "big"
MID = "mid"
SMALL = "small"
IMPACT_LABEL = {BIG: "체감 큼", MID: "체감 보통", SMALL: "체감 작음"}

# 고성능 전원 계획. 윈도우가 기본으로 갖고 있는 값이라 어느 PC에서나 같다.
HIGH_PERFORMANCE = "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"
PLAN_NAME = "서든어택 최적화"

_GUID = re.compile(r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}")


@dataclass(frozen=True)
class RegItem:
    root: str
    path: str
    name: str
    value: RegValue


# ---------------------------------------------------------------------------
# 실제로 값을 바꾸는 부분
# ---------------------------------------------------------------------------
class Action:
    """상태 확인 / 적용 / 되돌리기. 적용은 되돌리기에 필요한 기록을 돌려준다."""

    def state(self, ctx) -> str:
        raise NotImplementedError

    def apply(self, ctx) -> dict:
        raise NotImplementedError

    def revert(self, ctx, record: dict) -> None:
        raise NotImplementedError


class RegistryAction(Action):
    """레지스트리 값 몇 개를 정해진 값으로 맞춘다. 대부분의 항목이 여기 해당한다."""

    def __init__(self, items: list[RegItem]):
        self._items = items

    def items(self, ctx) -> list[RegItem]:
        return self._items

    def after_apply(self, ctx) -> None:
        """값을 다 쓴 뒤에 할 일이 있으면 여기서. (예: 다시 로그인 없이 바로 적용)"""

    def state(self, ctx) -> str:
        items = self.items(ctx)
        if not items:
            return NA
        for item in items:
            current = ctx.registry.read(item.root, item.path, item.name)
            if current is None or not _same(current.data, item.value.data):
                return OFF
        return ON

    def apply(self, ctx) -> dict:
        saved = []
        for item in self.items(ctx):
            before = ctx.registry.read(item.root, item.path, item.name)
            saved.append(
                {
                    "root": item.root,
                    "path": item.path,
                    "name": item.name,
                    "before": before.as_json() if before else None,
                    "key_existed": ctx.registry.key_exists(item.root, item.path),
                }
            )
            ctx.registry.write(item.root, item.path, item.name, item.value)
        self.after_apply(ctx)
        return {"kind": "registry", "items": saved}

    def revert(self, ctx, record: dict) -> None:
        for saved in reversed(record.get("items") or []):
            root, path, name = saved["root"], saved["path"], saved["name"]
            before = saved.get("before")
            if before is None:
                # 원래 없던 값이다. 0 으로 되돌리는 게 아니라 지워야 원상태다.
                ctx.registry.delete_value(root, path, name)
                if not saved.get("key_existed", True):
                    ctx.registry.delete_key(root, path)
                    # 우리가 만드느라 딸려 생긴 빈 껍데기(예: IFEO\\SuddenAttack.exe)도 치운다.
                    # 비어 있을 때만 지워지므로 남의 키를 건드릴 일은 없다.
                    parent = path.rsplit("\\", 1)[0]
                    if parent and parent != path:
                        ctx.registry.delete_key(root, parent)
            else:
                ctx.registry.write(root, path, name, RegValue.from_json(before))
        self.after_apply(ctx)


class MouseAction(RegistryAction):
    """마우스 가속(포인터 정밀도 향상) 끄기.

    값만 써두면 다시 로그인해야 먹는다. 그러면 "눌렀는데 아무 변화가 없다"가 되므로
    윈도우에 바로 알려서 그 자리에서 적용시킨다.
    """

    def after_apply(self, ctx) -> None:
        if not ctx.windows:
            return
        try:                        # pragma: no cover - 윈도우 전용
            speed = ctx.registry.read("HKCU", r"Control Panel\Mouse", "MouseSpeed")
            values = (ctypes.c_int * 3)(
                int(str(_read(ctx, "MouseThreshold1")) or 0),
                int(str(_read(ctx, "MouseThreshold2")) or 0),
                int(str(speed.data) if speed else 0),
            )
            SPI_SETMOUSE, UPDATE_AND_TELL = 0x0004, 0x0003
            ctypes.windll.user32.SystemParametersInfoW(
                SPI_SETMOUSE, 0, ctypes.byref(values), UPDATE_AND_TELL
            )
        except Exception as exc:    # pragma: no cover - 윈도우 전용
            log.debug("마우스 설정을 즉시 적용하지 못했습니다: %s", exc)


class NagleAction(RegistryAction):
    """네트워크 카드마다 Nagle 알고리즘을 끈다.

    Nagle 은 작은 꾸러미를 모았다가 한 번에 보내서 회선을 아끼는 기능이다. 파일을
    받을 때는 이득이지만, 총 쏜 사실 하나를 보내는 게임에서는 그 '모으는 시간'이
    그대로 지연이 된다. 랜카드 개수만큼 경로가 달라서 실행할 때 찾는다.
    """

    def __init__(self):
        super().__init__([])

    def items(self, ctx) -> list[RegItem]:
        base = r"SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces"
        found = []
        for guid in ctx.registry.subkeys("HKLM", base):
            path = f"{base}\\{guid}"
            # IP 주소가 잡혀 있는 카드만. 안 쓰는 가상 어댑터까지 건드릴 이유가 없다.
            has_ip = any(
                _has_address(ctx.registry.read("HKLM", path, name))
                for name in ("DhcpIPAddress", "IPAddress")
            )
            if not has_ip:
                continue
            found.append(RegItem("HKLM", path, "TcpAckFrequency", RegValue(1, DWORD)))
            found.append(RegItem("HKLM", path, "TCPNoDelay", RegValue(1, DWORD)))
        return found


class LayersAction(RegistryAction):
    """서든어택 실행 파일에 '전체 화면 최적화 끄기 + 높은 DPI 재정의'를 걸어둔다.

    전체 화면 최적화는 윈도우가 게임을 몰래 창 모드로 돌리는 기능이다. 알트탭은
    빨라지지만 화면이 한 단계 더 거쳐 나가서 입력이 늦게 느껴진다.
    """

    def __init__(self):
        super().__init__([])

    def items(self, ctx) -> list[RegItem]:
        if not ctx.install:
            return []
        return [
            RegItem(
                "HKCU",
                r"Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers",
                str(ctx.install.exe),
                RegValue("~ DISABLEDXMAXIMIZEDWINDOWEDMODE HIGHDPIAWARE", STR),
            )
        ]


class PriorityAction(RegistryAction):
    """서든어택이 켜질 때 CPU 우선순위를 '높음'으로 시작하게 한다.

    작업 관리자에서 매번 손으로 바꾸던 것과 같은 값(높음)이다. '실시간'은 넣지
    않았다 — 실시간은 윈도우 자신보다 위라서 마우스까지 멎을 수 있다.
    """

    def __init__(self):
        super().__init__([])

    def items(self, ctx) -> list[RegItem]:
        if not ctx.install:
            return []
        base = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Image File Execution Options"
        return [
            RegItem(
                "HKLM",
                f"{base}\\{ctx.install.exe_name}\\PerfOptions",
                "CpuPriorityClass",
                RegValue(3, DWORD),         # 3 = 높음
            )
        ]


class PowerPlanAction(Action):
    """게임용 전원 계획을 따로 만들어서 켠다.

    쓰던 계획을 고치지 않고 **복제해서** 만든다. 되돌리기는 원래 계획으로 되돌아간
    뒤 우리가 만든 것을 지우는 것이라, 사용자가 손수 맞춰둔 설정이 사라질 일이 없다.
    """

    SETTINGS = [
        # (묶음 GUID, 설정 GUID, 값, 설명)
        ("2a737441-1930-4402-8d77-b2bebba308a3", "48e6b7a6-50f5-4782-a5d4-53bb8f07e226", 0,
         "USB 선택적 절전 해제 (게임 중 마우스가 잠깐 멎는 일 방지)"),
        ("501a4d13-42af-4429-9fd1-a8218c268e20", "ee12f906-d277-404b-b6da-e5fa1a576df5", 0,
         "PCI Express 링크 절전 끄기 (그래픽카드가 졸지 않게)"),
        ("54533251-82be-4824-96c1-47b60b740d00", "893dee8e-2bef-41e0-89c6-b55d0929964c", 100,
         "프로세서 최소 상태 100%"),
        ("54533251-82be-4824-96c1-47b60b740d00", "bc5038f7-23e0-4960-96da-33abaf5935ec", 100,
         "프로세서 최대 상태 100%"),
        ("0012ee47-9041-4b5d-9b77-535fba8b1442", "6738e2c4-e8a5-4a42-b16a-e040e769756e", 0,
         "하드디스크 절전 끄기"),
    ]

    def state(self, ctx) -> str:
        active = self._active(ctx)
        if active is None:
            return UNKNOWN
        return ON if PLAN_NAME in active[1] else OFF

    def apply(self, ctx) -> dict:
        active = self._active(ctx)
        if active is None:
            raise RuntimeError("지금 전원 계획을 읽지 못했습니다.")
        before, before_name = active
        if PLAN_NAME in before_name:
            # 이미 우리 계획을 쓰고 있다. 또 만들지 말고 값만 다시 맞춘다.
            self._tune(ctx, before)
            return {"kind": "power", "before": None, "created": None, "retuned": before}

        created = self._find_ours(ctx) or self._duplicate(ctx, before)
        if not created:
            raise RuntimeError("전원 계획을 만들지 못했습니다.")
        ctx.shell.run(["powercfg", "/changename", created, PLAN_NAME, "서든어택 할 때 쓰는 계획"])
        self._tune(ctx, created)
        result = ctx.shell.run(["powercfg", "/setactive", created])
        if not result.ok:
            raise RuntimeError(f"전원 계획을 켜지 못했습니다: {result.err or result.out}")
        return {"kind": "power", "before": before, "created": created}

    def revert(self, ctx, record: dict) -> None:
        before = record.get("before")
        created = record.get("created")
        if before:
            ctx.shell.run(["powercfg", "/setactive", before])
        if created:
            # 켜져 있는 계획은 못 지운다. 위에서 원래 것으로 돌린 뒤라 지워진다.
            ctx.shell.run(["powercfg", "/delete", created])

    # --- 안쪽 ---------------------------------------------------------
    def _active(self, ctx) -> tuple[str, str] | None:
        result = ctx.shell.run(["powercfg", "/getactivescheme"])
        if not result.ok:
            return None
        return _parse_scheme(result.out)

    def _find_ours(self, ctx) -> str | None:
        result = ctx.shell.run(["powercfg", "/list"])
        if not result.ok:
            return None
        for line in result.out.splitlines():
            parsed = _parse_scheme(line)
            if parsed and PLAN_NAME in parsed[1]:
                return parsed[0]
        return None

    def _duplicate(self, ctx, fallback: str) -> str | None:
        for source in (HIGH_PERFORMANCE, fallback):
            result = ctx.shell.run(["powercfg", "/duplicatescheme", source])
            if result.ok:
                found = _GUID.search(result.out)
                if found:
                    return found.group(0)
            log.debug("전원 계획 복제 실패(%s): %s", source, result.err or result.out)
        return None

    def _tune(self, ctx, scheme: str) -> None:
        for group, setting, value, label in self.SETTINGS:
            for mode in ("/setacvalueindex", "/setdcvalueindex"):
                result = ctx.shell.run(["powercfg", mode, scheme, group, setting, str(value)])
                if not result.ok:
                    # 노트북에만 있는 항목, 데스크톱에만 있는 항목이 섞여 있다.
                    # 하나 실패했다고 나머지를 포기할 이유는 없다.
                    log.debug("전원 항목 건너뜀 (%s): %s", label, result.err or result.out)


class RefreshRateAction(Action):
    """모니터를 지금 해상도에서 낼 수 있는 가장 높은 주사율로 올린다."""

    def state(self, ctx) -> str:
        screens = ctx.display.monitors()
        if not screens:
            return NA
        return ON if all(screen.already_best for screen in screens) else OFF

    def apply(self, ctx) -> dict:
        changed = []
        for screen in ctx.display.monitors():
            if screen.already_best:
                continue
            before = screen.hz          # 바꾸기 '전' 값이어야 한다. 바꾼 뒤에 읽으면 늦다.
            if ctx.display.set_hz(screen.device, screen.best_hz):
                changed.append({"device": screen.device, "hz": before})
        return {"kind": "display", "screens": changed}

    def revert(self, ctx, record: dict) -> None:
        for screen in record.get("screens") or []:
            ctx.display.set_hz(screen["device"], int(screen["hz"]))


class DefenderExclusionAction(Action):
    """윈도우 보안(Defender)의 실시간 검사에서 게임 폴더를 뺀다.

    게임이 읽는 파일마다 백신이 한 번씩 훑으면 렉이 걸린다. 대신 그 폴더는 검사를
    안 하게 되므로, 공식 경로로 설치한 게임 폴더에만 걸어야 한다. 그래서 기본으로
    켜두지 않았다.
    """

    def state(self, ctx) -> str:
        if not ctx.install:
            return NA
        if not ctx.windows:
            return UNKNOWN
        result = ctx.shell.powershell("(Get-MpPreference).ExclusionPath -join [char]10")
        if not result.ok:
            return UNKNOWN
        target = str(ctx.install.folder).lower().rstrip("\\")
        listed = [line.strip().lower().rstrip("\\") for line in result.out.splitlines()]
        return ON if target in listed else OFF

    def apply(self, ctx) -> dict:
        if not ctx.install:
            return {"kind": "defender", "paths": []}
        folder = str(ctx.install.folder)
        result = ctx.shell.powershell(f'Add-MpPreference -ExclusionPath "{folder}"')
        if not result.ok:
            raise RuntimeError(f"검사 제외를 넣지 못했습니다: {result.err or result.out}")
        return {"kind": "defender", "paths": [folder]}

    def revert(self, ctx, record: dict) -> None:
        for folder in record.get("paths") or []:
            ctx.shell.powershell(f'Remove-MpPreference -ExclusionPath "{folder}"')


# ---------------------------------------------------------------------------
# 항목 목록
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Tweak:
    key: str
    title: str
    what: str                   # 이게 무엇을 켜고 끄는 건지
    gain: str                   # 바꾸면 뭐가 좋아지는지
    group: str
    action: Action
    impact: str = MID           # 게임 안에서 얼마나 느껴지는지
    affects: str = ""           # 어느 쪽이 좋아지는지 (에임·핑·프레임…)
    recommended: bool = True    # '한 번에 최적화' 에 포함되는가
    admin: bool = False         # 관리자 권한이 있어야 하는가
    reboot: bool = False        # 재부팅해야 먹는가
    note: str = ""              # 알아둬야 할 것


GROUPS = ["입력", "화면", "전원", "네트워크", "윈도우", "게임"]


def catalog() -> list[Tweak]:
    """최적화 항목 전부. 순서가 화면 순서다.

    impact 는 부풀리지 않는다. 이 프로그램에서 프레임 숫자를 실제로 올려주는 항목은
    Game DVR 끄기 정도뿐이고, 나머지는 대부분 '화면이 뜨는 지연'과 '순간 끊김' 쪽이다.
    작은 건 작다고 적어야 큰 게 믿긴다.
    """
    return [
        # --- 입력 -------------------------------------------------------
        Tweak(
            key="mouse_accel",
            title="마우스 가속 끄기",
            what="윈도우가 마우스를 빨리 움직일수록 커서를 더 많이 움직여주는 기능을 끕니다.",
            gain="같은 거리를 움직이면 언제나 같은 만큼 조준이 돕니다. 몸이 감각을 "
                 "외울 수 있게 됩니다. 프레임은 1도 안 오르지만 에임에서는 이게 제일 큽니다.",
            impact=BIG,
            affects="에임",
            group="입력",
            action=MouseAction([
                RegItem("HKCU", r"Control Panel\Mouse", "MouseSpeed", RegValue("0", STR)),
                RegItem("HKCU", r"Control Panel\Mouse", "MouseThreshold1", RegValue("0", STR)),
                RegItem("HKCU", r"Control Panel\Mouse", "MouseThreshold2", RegValue("0", STR)),
            ]),
            note="누르면 바로 적용됩니다. 제어판의 '포인터 정확도 향상' 체크가 풀립니다.",
        ),
        # --- 화면 -------------------------------------------------------
        Tweak(
            key="refresh_rate",
            title="모니터 주사율 최대로",
            what="모니터가 1초에 화면을 몇 번 새로 그릴지를, 지금 해상도에서 낼 수 있는 "
                 "최대로 올립니다. 해상도는 건드리지 않습니다.",
            gain="60→144Hz 면 눈에 보이는 장면이 2.4배가 됩니다. 움직이는 적이 뚝뚝 "
                 "끊기지 않고, 화면에 뜨기까지 기다리는 시간이 평균 8ms 에서 3.5ms 로 "
                 "줄어듭니다. 이미 최대로 돼 있으면 바뀌는 게 없습니다.",
            impact=BIG,
            affects="화면",
            group="화면",
            action=RefreshRateAction(),
        ),
        Tweak(
            key="fullscreen_opt",
            title="전체 화면 최적화 끄기 (서든어택)",
            what="윈도우가 전체 화면 게임을 몰래 '테두리 없는 창'으로 바꿔 돌리는 "
                 "기능을 끕니다.",
            gain="게임 화면이 윈도우를 한 번 덜 거치고 모니터로 갑니다. 대략 한 장면만큼 "
                 "덜 기다립니다. 느끼는 사람과 못 느끼는 사람이 갈립니다.",
            impact=MID,
            affects="지연",
            group="화면",
            action=LayersAction(),
            note="서든어택 설치 경로를 찾아야 적용됩니다.",
        ),
        Tweak(
            key="visual_effects",
            title="윈도우 화면 효과 줄이기",
            what="창이 부드럽게 열리고 닫히는 애니메이션, 메뉴가 뜨는 지연을 없앱니다.",
            gain="게임 프레임은 오르지 않습니다. 대신 알트탭이 눈에 띄게 빨라집니다.",
            impact=SMALL,
            affects="알트탭",
            group="화면",
            action=RegistryAction([
                RegItem("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Explorer\VisualEffects",
                        "VisualFXSetting", RegValue(2, DWORD)),
                RegItem("HKCU", r"Control Panel\Desktop", "MenuShowDelay", RegValue("0", STR)),
                RegItem("HKCU", r"Control Panel\Desktop", "DragFullWindows", RegValue("0", STR)),
                RegItem("HKCU", r"Control Panel\Desktop\WindowMetrics",
                        "MinAnimate", RegValue("0", STR)),
                RegItem("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced",
                        "TaskbarAnimations", RegValue(0, DWORD)),
            ]),
            note="다시 로그인하면 완전히 적용됩니다.",
        ),
        # --- 전원 -------------------------------------------------------
        Tweak(
            key="power_plan",
            title="게임용 전원 계획 켜기",
            what="CPU가 한가할 때 속도를 낮추는 절전 동작을 끕니다. 쓰던 계획을 고치는 게 "
                 "아니라 복제해서 새로 만들기 때문에 원래 설정은 그대로 남습니다.",
            gain="평균 프레임보다 '순간적으로 뚝 떨어지는 것'이 줄어듭니다. 노트북이나 "
                 "오래된 CPU 일수록 차이가 큽니다. 최신 데스크톱이면 작습니다.",
            impact=MID,
            affects="끊김",
            group="전원",
            action=PowerPlanAction(),
            admin=True,
            note="노트북이라면 배터리로 쓸 때 사용 시간이 줄어듭니다.",
        ),
        # --- 네트워크 ---------------------------------------------------
        Tweak(
            key="nagle",
            title="네트워크 지연 줄이기 (Nagle 끄기)",
            what="작은 신호를 잠깐 모았다가 한꺼번에 보내는 윈도우 기능을 끕니다. "
                 "파일 받을 땐 이득이지만 게임에는 손해입니다.",
            gain="총 쏜 신호가 모으는 시간 없이 바로 나갑니다. 평균 핑보다 '핑이 갑자기 "
                 "튀는 것'이 줄어듭니다. 회선 상태에 따라 차이가 크고, 아예 못 느끼는 "
                 "경우도 있습니다.",
            impact=MID,
            affects="핑",
            group="네트워크",
            action=NagleAction(),
            admin=True,
        ),
        Tweak(
            key="net_throttle",
            title="네트워크 속도 제한 풀기",
            what="윈도우는 멀티미디어 재생을 위해 초당 패킷 수를 묶어둡니다. 게임에는 "
                 "필요 없는 제한이라 풉니다.",
            gain="게임 신호가 그 제한에 걸려 밀리지 않습니다. 위 Nagle 끄기와 같은 방향인데 "
                 "효과는 그보다 작습니다.",
            impact=SMALL,
            affects="핑",
            group="네트워크",
            action=RegistryAction([
                RegItem("HKLM",
                        r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile",
                        "NetworkThrottlingIndex", RegValue(0xFFFFFFFF, DWORD)),
            ]),
            admin=True,
        ),
        # --- 윈도우 -----------------------------------------------------
        Tweak(
            key="mmcss_games",
            title="게임에 CPU·GPU 우선 배정",
            what="윈도우가 갖고 있는 '게임' 작업 등급의 우선순위를 올립니다.",
            gain="배경에서 도는 프로그램 때문에 생기는 순간 끊김이 줄어듭니다. 크롬 탭이나 "
                 "디스코드를 많이 켜둘수록 차이가 납니다.",
            impact=SMALL,
            affects="끊김",
            group="윈도우",
            action=RegistryAction([
                RegItem("HKLM",
                        r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile",
                        "SystemResponsiveness", RegValue(10, DWORD)),
                RegItem("HKLM",
                        r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile"
                        r"\Tasks\Games", "GPU Priority", RegValue(8, DWORD)),
                RegItem("HKLM",
                        r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile"
                        r"\Tasks\Games", "Priority", RegValue(6, DWORD)),
                RegItem("HKLM",
                        r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile"
                        r"\Tasks\Games", "Scheduling Category", RegValue("High", STR)),
                RegItem("HKLM",
                        r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile"
                        r"\Tasks\Games", "SFIO Priority", RegValue("High", STR)),
            ]),
            admin=True,
        ),
        Tweak(
            key="game_mode",
            title="윈도우 게임 모드 켜기",
            what="게임이 앞에 있을 때 윈도우 업데이트와 배경 작업을 미루게 합니다.",
            gain="게임 도중에 윈도우가 갑자기 딴짓을 시작하는 일이 줄어듭니다.",
            impact=SMALL,
            affects="끊김",
            group="윈도우",
            action=RegistryAction([
                RegItem("HKCU", r"Software\Microsoft\GameBar",
                        "AllowAutoGameMode", RegValue(1, DWORD)),
                RegItem("HKCU", r"Software\Microsoft\GameBar",
                        "AutoGameModeEnabled", RegValue(1, DWORD)),
            ]),
        ),
        Tweak(
            key="game_dvr",
            title="배경 녹화(Game DVR) 끄기",
            what="Xbox Game Bar 가 게임 화면을 늘 몰래 녹화하고 있는 것을 끕니다.",
            gain="이 프로그램에서 프레임 숫자를 실제로 올려주는 거의 유일한 항목입니다. "
                 "켜져 있었다면 몇 % 를 돌려받습니다. 이미 꺼져 있었으면 변화가 없습니다.",
            impact=MID,
            affects="프레임",
            group="윈도우",
            action=RegistryAction([
                RegItem("HKCU", r"System\GameConfigStore",
                        "GameDVR_Enabled", RegValue(0, DWORD)),
                RegItem("HKCU", r"Software\Microsoft\Windows\CurrentVersion\GameDVR",
                        "AppCaptureEnabled", RegValue(0, DWORD)),
                RegItem("HKLM", r"SOFTWARE\Policies\Microsoft\Windows\GameDVR",
                        "AllowGameDVR", RegValue(0, DWORD)),
            ]),
            admin=True,
        ),
        Tweak(
            key="notifications",
            title="알림 팝업 끄기",
            what="게임 도중 오른쪽 아래에서 튀어나오는 윈도우 알림을 막습니다.",
            gain="프레임과는 관계가 없습니다. 결정적인 순간에 화면이 가려지지 않습니다.",
            impact=SMALL,
            affects="방해",
            group="윈도우",
            action=RegistryAction([
                RegItem("HKCU", r"Software\Microsoft\Windows\CurrentVersion\PushNotifications",
                        "ToastEnabled", RegValue(0, DWORD)),
            ]),
            recommended=False,
            note="게임이 아닐 때도 알림이 안 옵니다. 필요하면 되돌리기로 돌아옵니다.",
        ),
        Tweak(
            key="hags_off",
            title="하드웨어 가속 GPU 일정 예약 끄기",
            what="그래픽카드가 작업 순서를 직접 정하게 하는 기능을 끕니다.",
            gain="서든어택처럼 프레임이 아주 높게 나오는 가벼운 게임에서는 끄는 쪽이 프레임 "
                 "간격이 고른 경우가 많습니다. 다만 컴퓨터마다 결과가 달라서, 켜보고 "
                 "직접 비교해 보시는 게 맞습니다.",
            impact=SMALL,
            affects="끊김",
            group="윈도우",
            action=RegistryAction([
                RegItem("HKLM", r"SYSTEM\CurrentControlSet\Control\GraphicsDrivers",
                        "HwSchMode", RegValue(1, DWORD)),
            ]),
            recommended=False,
            admin=True,
            reboot=True,
        ),
        # --- 게임 -------------------------------------------------------
        Tweak(
            key="priority",
            title="서든어택 우선순위 '높음'",
            what="게임이 켜질 때부터 CPU를 먼저 받게 합니다. 작업 관리자에서 매번 손으로 "
                 "바꾸던 것과 같은 값입니다.",
            gain="CPU가 여유로우면 차이가 없습니다. 배경에 켜둔 게 많을 때 도움이 됩니다.",
            impact=SMALL,
            affects="끊김",
            group="게임",
            action=PriorityAction(),
            admin=True,
            note="서든어택 설치 경로를 찾아야 적용됩니다. '실시간'은 위험해서 넣지 않았습니다.",
        ),
        Tweak(
            key="defender",
            title="백신 실시간 검사에서 게임 폴더 빼기",
            what="윈도우 보안의 실시간 검사 대상에서 게임 폴더를 뺍니다.",
            gain="맵을 처음 불러올 때 생기는 순간 렉이 줄어듭니다. 교전 중 프레임과는 "
                 "관계가 적습니다.",
            impact=SMALL,
            affects="로딩",
            group="게임",
            action=DefenderExclusionAction(),
            recommended=False,
            admin=True,
            note="그 폴더는 검사를 안 하게 됩니다. 공식 경로로 설치한 게임에만 쓰세요.",
        ),
    ]


def by_key() -> dict[str, Tweak]:
    return {tweak.key: tweak for tweak in catalog()}


# ---------------------------------------------------------------------------
def _same(left, right) -> bool:
    """레지스트리에서 읽은 값과 우리가 원하는 값이 같은가.

    같은 0 이라도 어떤 건 숫자 0, 어떤 건 문자열 "0" 으로 들어 있다. 글자로 맞춰본다.
    """
    if isinstance(left, (bytes, bytearray)) or isinstance(right, (bytes, bytearray)):
        return bytes(left or b"") == bytes(right or b"")
    return str(left).strip().lower() == str(right).strip().lower()


def _has_address(value) -> bool:
    if value is None or value.data in (None, "", []):
        return False
    data = value.data
    if isinstance(data, (list, tuple)):
        return any(str(item).strip() not in ("", "0.0.0.0") for item in data)
    return str(data).strip() not in ("", "0.0.0.0")


def _read(ctx, name: str):
    found = ctx.registry.read("HKCU", r"Control Panel\Mouse", name)
    return found.data if found else 0


def _parse_scheme(text: str) -> tuple[str, str] | None:
    """powercfg 출력 한 줄에서 GUID 와 계획 이름을 뽑는다.

    한국어 윈도우는 '전원 구성표 GUID: ... (균형 조정)', 영어는 'Power Scheme GUID: ...
    (Balanced)' 로 나온다. 글자는 다르지만 생김새는 같아서 GUID 와 괄호만 본다.
    """
    found = _GUID.search(text or "")
    if not found:
        return None
    name = ""
    opened = text.find("(", found.end())
    closed = text.rfind(")")
    if opened != -1 and closed > opened:
        name = text[opened + 1:closed]
    return found.group(0), name


# ============================================================================
# [8] 실행기 — 적용하고, 기록하고, 되돌린다
# ============================================================================
#
# 적용하고, 기록하고, 되돌린다.
#
# 항목 하나가 실패해도 나머지는 계속한다. 최적화는 15개를 다 해야 의미가 있는 게
# 아니라 되는 것부터 하나씩 쌓이는 일이고, 하나 막혔다고 전부 멈추면 사용자는
# 무엇이 됐고 무엇이 안 됐는지 알 수 없게 된다.

STATE_LABEL = {ON: "적용됨", OFF: "안 됨", UNKNOWN: "확인 불가", NA: "해당 없음"}

PATH_FILE = "게임경로.txt"


@dataclass
class Context:
    registry: object
    shell: object
    display: object
    install: object = None
    admin: bool = False
    windows: bool = WINDOWS


@dataclass
class Status:
    tweak: Tweak
    state: str
    blocked: str = ""       # 비어 있지 않으면 지금은 못 한다는 뜻

    @property
    def label(self) -> str:
        return STATE_LABEL.get(self.state, self.state)

    @property
    def can_apply(self) -> bool:
        return not self.blocked and self.state in (OFF, UNKNOWN)


@dataclass
class Step:
    key: str
    title: str
    ok: bool
    message: str


@dataclass
class Outcome:
    steps: list = field(default_factory=list)
    record: Path | None = None
    reboot: bool = False

    @property
    def done(self) -> int:
        return sum(1 for step in self.steps if step.ok)

    @property
    def failed(self) -> int:
        return sum(1 for step in self.steps if not step.ok)

    @property
    def summary(self) -> str:
        if not self.steps:
            return "바꿀 것이 없었습니다. 이미 다 되어 있습니다."
        parts = [f"{self.done}개 적용"]
        if self.failed:
            parts.append(f"{self.failed}개 실패")
        if self.reboot:
            parts.append("일부는 재부팅 후에 적용됩니다")
        return " · ".join(parts)


class Optimizer:
    def __init__(self, ctx: Context, items: list[Tweak] | None = None, root: Path | None = None):
        self.ctx = ctx
        self.items = items if items is not None else catalog()
        self.root = root

    # --- 보기 ---------------------------------------------------------
    def statuses(self) -> list[Status]:
        found = []
        for tweak in self.items:
            try:
                state = tweak.action.state(self.ctx)
            except Exception as exc:
                log.debug("%s 상태 확인 실패: %s", tweak.key, exc)
                state = UNKNOWN
            found.append(Status(tweak=tweak, state=state, blocked=self._blocked(tweak, state)))
        return found

    def _blocked(self, tweak: Tweak, state: str) -> str:
        if state == NA:
            if tweak.key in ("fullscreen_opt", "priority", "defender"):
                return "서든어택 설치 폴더를 찾지 못했습니다"
            return "이 컴퓨터에는 해당하지 않습니다"
        if tweak.admin and not self.ctx.admin:
            return "관리자 권한이 필요합니다"
        if not self.ctx.windows:
            return "윈도우에서만 적용됩니다"
        return ""

    def recommended_keys(self) -> list[str]:
        """'한 번에 최적화' 를 눌렀을 때 손댈 항목."""
        return [
            status.tweak.key
            for status in self.statuses()
            if status.tweak.recommended and status.can_apply
        ]

    # --- 적용 ---------------------------------------------------------
    def apply(self, keys: list[str]) -> Outcome:
        wanted = [tweak for tweak in self.items if tweak.key in set(keys)]
        outcome = Outcome()
        entries: dict = {}
        when = datetime.now()

        for tweak in wanted:
            try:
                state = tweak.action.state(self.ctx)
            except Exception as exc:
                log.debug("%s 상태 확인 실패: %s", tweak.key, exc)
                state = UNKNOWN

            blocked = self._blocked(tweak, state)
            if blocked:
                outcome.steps.append(Step(tweak.key, tweak.title, False, blocked))
                continue
            if state == ON:
                continue            # 이미 되어 있다. 손대면 되돌릴 값만 더럽혀진다.

            try:
                entries[tweak.key] = tweak.action.apply(self.ctx)
            except Exception as exc:
                log.warning("%s 적용 실패: %s", tweak.key, exc)
                outcome.steps.append(Step(tweak.key, tweak.title, False, str(exc)))
                continue

            outcome.steps.append(Step(tweak.key, tweak.title, True, "적용했습니다"))
            if tweak.reboot:
                outcome.reboot = True
            # 항목 하나 끝날 때마다 기록을 갱신한다. 중간에 멈춰도 여기까지는 되돌린다.
            outcome.record = save_record(entries, root=self.root, when=when)

        return outcome

    def apply_recommended(self) -> Outcome:
        return self.apply(self.recommended_keys())

    # --- 되돌리기 -----------------------------------------------------
    def revert(self, record: Record | None = None) -> Outcome:
        record = record or latest_record(self.root)
        outcome = Outcome()
        if record is None:
            return outcome

        catalog = by_key()
        # 넣은 순서의 반대로 되돌린다. 전원 계획처럼 순서가 있는 것 때문에 그렇다.
        for key in reversed(list(record.entries)):
            tweak = catalog.get(key)
            if tweak is None:
                outcome.steps.append(Step(key, key, False, "모르는 항목이라 건너뜁니다"))
                continue
            try:
                tweak.action.revert(self.ctx, record.entries[key])
            except Exception as exc:
                log.warning("%s 되돌리기 실패: %s", key, exc)
                outcome.steps.append(Step(key, tweak.title, False, str(exc)))
                continue
            outcome.steps.append(Step(key, tweak.title, True, "되돌렸습니다"))
            if tweak.reboot:
                outcome.reboot = True

        if not outcome.failed:
            mark_reverted(record)
        outcome.record = record.path
        return outcome


# --- 이 컴퓨터에서는 뭐가 달라지나 -----------------------------------------
DONE = "done"           # 이미 되어 있다 — 눌러도 달라질 게 없다
LOCKED = "locked"       # 지금은 못 한다

VERDICT_LABEL = {BIG: "크게", MID: "보통", SMALL: "조금", DONE: "이미 됨", LOCKED: "잠김"}
VERDICT_ORDER = {BIG: 0, MID: 1, SMALL: 2, DONE: 3, LOCKED: 4}


@dataclass
class Verdict:
    key: str
    title: str
    level: str
    line: str           # 이 컴퓨터의 실제 값으로 쓴 한 줄

    @property
    def label(self) -> str:
        return VERDICT_LABEL.get(self.level, self.level)


def verdicts(statuses, spec) -> list[Verdict]:
    """이 컴퓨터에서 실제로 무엇이 달라지는지, 큰 것부터.

    항목 목록은 "이 항목이 일반적으로 어떤가" 를 말한다. 그것만으로는
    "그래서 내 컴퓨터에서는 뭐가 달라지는데?" 에 답이 안 된다. 같은 '주사율 최대로'
    라도 이미 144Hz 인 사람에게는 아무 일도 안 일어나고, 60Hz 인 사람에게는 이 목록
    전체에서 제일 큰 변화다.

    그래서 여기서는 읽어온 실제 값으로 다시 판정한다 — 지금 주사율이 몇인지,
    노트북인지, 그 설정이 이미 꺼져 있는지.
    """
    found = []
    for status in statuses:
        tweak = status.tweak
        if status.state == ON:
            found.append(Verdict(tweak.key, tweak.title, DONE, "이미 되어 있습니다"))
        elif status.blocked:
            found.append(Verdict(tweak.key, tweak.title, LOCKED, status.blocked))
        else:
            level, line = _verdict_for(tweak, spec)
            found.append(Verdict(tweak.key, tweak.title, level, line))
    # 같은 등급 안에서는 목록 순서를 지킨다 (파이썬 정렬은 순서를 흐트러뜨리지 않는다)
    found.sort(key=lambda verdict: VERDICT_ORDER.get(verdict.level, 9))
    return found


def _verdict_for(tweak: Tweak, spec) -> tuple[str, str]:
    """항목 하나를 이 컴퓨터 기준으로 다시 말한다. 해당 없으면 원래 설명 그대로."""
    if tweak.key == "refresh_rate":
        behind = [screen for screen in spec.monitors if not screen.already_best]
        if behind:
            # 여러 대면 가장 손해가 큰 모니터를 기준으로 말한다
            screen = max(behind, key=lambda s: s.best_hz - s.hz)
            times = screen.best_hz / screen.hz if screen.hz else 0
            wait_now = 500 / screen.hz if screen.hz else 0
            wait_after = 500 / screen.best_hz if screen.best_hz else 0
            return BIG, (
                f"모니터가 {screen.hz}Hz 로 돌고 있습니다. {screen.best_hz}Hz 로 올리면 "
                f"눈에 보이는 장면이 {times:.1f}배가 되고, 방금 일어난 일이 화면에 뜨기까지 "
                f"평균 {wait_now:.1f}ms → {wait_after:.1f}ms 로 줄어듭니다."
            )
    if tweak.key == "power_plan":
        if spec.laptop:
            return BIG, (
                "노트북입니다. 노트북은 CPU 가 절전하려고 속도를 크게 낮추기 때문에 이 "
                "항목의 차이가 데스크톱보다 훨씬 큽니다. 대신 배터리로 쓸 때 사용 시간이 "
                "줄어듭니다."
            )
        return MID, ("데스크톱입니다. 노트북만큼은 아니지만, 평균 프레임보다 "
                     "'순간적으로 뚝 떨어지는 것'이 줄어듭니다.")
    if tweak.key == "mouse_accel":
        return BIG, ("마우스 가속이 켜져 있습니다. 지금은 손을 빨리 움직일수록 조준이 더 "
                     "많이 돕니다. 끄면 같은 거리가 언제나 같은 만큼이 됩니다.")
    if tweak.key == "game_dvr":
        return MID, ("배경 녹화가 켜져 있습니다. 게임 화면을 늘 몰래 녹화하고 있다는 뜻이고, "
                     "끄면 그만큼 프레임을 돌려받습니다.")
    return tweak.impact, tweak.gain


# ---------------------------------------------------------------------------
def build_context(root: Path | None = None) -> Context:
    """이 컴퓨터에 맞는 도구들을 열고, 서든어택을 찾아둔다."""
    registry = open_registry()
    ctx = Context(
        registry=registry,
        shell=open_shell(),
        display=open_display(),
        admin=is_admin(),
        windows=WINDOWS,
    )
    ctx.install = find_game(registry=registry, saved=saved_game_path(root))
    return ctx


def spec_of(ctx: Context) -> Spec:
    return read_spec(ctx.registry, ctx.display)


def saved_game_path(root: Path | None = None) -> str | None:
    path = backup_folder(root) / PATH_FILE
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


def remember_game_path(value: str, root: Path | None = None) -> None:
    target = backup_folder(root)
    target.mkdir(parents=True, exist_ok=True)
    (target / PATH_FILE).write_text(value.strip(), encoding="utf-8")


# ============================================================================
# [9] 안내문 — 자동으로 못 바꾸는 것들
# ============================================================================
#
# 프로그램이 대신 못 해주는 것들 — 그리고 일부러 안 하는 것들.
#
# 게임 안 옵션과 그래픽카드 제어판은 회사마다 화면이 다르고 버전마다 바뀐다.
# 잘못 짚으면 엉뚱한 값을 건드리게 되므로 자동으로 바꾸지 않는다. 대신 무엇을 어떻게
# 두면 되는지 적어서 보여준다.
#
# 맨 아래 '일부러 안 건드리는 것' 은 빼먹은 게 아니라 뺀 것이다. 그 이유까지 적어둔다.

@dataclass
class Section:
    title: str
    lead: str = ""
    items: list = field(default_factory=list)     # (무엇, 어떻게) 짝


IN_GAME = Section(
    title="게임 안에서 (직접)",
    lead="서든어택을 켜고 옵션에서 맞추시면 됩니다. 여기가 사실 제일 크게 바뀝니다.",
    items=[
        ("화면 모드", "전체 화면. 창 모드나 테두리 없는 창은 윈도우를 한 번 더 "
                   "거쳐 나가서 입력이 늦게 느껴집니다."),
        ("해상도", "모니터 원래 해상도. 낮추면 프레임은 오르지만 화면이 늘어나 보이고 "
                "적이 작아집니다."),
        ("수직 동기(V-Sync)", "끄기. 프레임을 모니터에 맞춰 붙잡아 두는 기능이라 "
                          "그만큼 입력이 늦어집니다."),
        ("그림자 · 효과 · 안티앨리어싱", "낮음 또는 끄기. 프레임도 오르지만 적을 가리는 "
                                "화면 요소가 줄어드는 이득이 더 큽니다."),
        ("프레임 제한", "모니터 주사율보다 넉넉히 높게, 또는 해제."),
        ("마우스 감도", "게임 안에서만 조절하세요. 윈도우 포인터 속도는 가운데(6단)에 "
                    "두는 것이 1:1 로 전달됩니다."),
    ],
)

NVIDIA = Section(
    title="NVIDIA 제어판 (직접)",
    lead="바탕화면 우클릭 → NVIDIA 제어판 → 3D 설정 관리 → 프로그램 설정에서 서든어택 선택.",
    items=[
        ("전원 관리 모드", "최고 성능 선호"),
        ("저지연 모드", "켬 (가능하면 '최고')"),
        ("수직 동기", "끔"),
        ("텍스처 필터링 - 품질", "고성능"),
        ("셰이더 캐시 크기", "10GB 이상 또는 무제한 — 맵을 처음 볼 때 튀는 렉이 줄어듭니다"),
    ],
)

AMD = Section(
    title="AMD 소프트웨어 (직접)",
    lead="AMD Software → 게임 → 서든어택 선택 (또는 그래픽 → 게임).",
    items=[
        ("Radeon Anti-Lag", "사용"),
        ("수직 동기 대기", "항상 끄기"),
        ("텍스처 필터링 품질", "성능"),
        ("표면 형식 최적화", "사용"),
        ("Radeon Boost / Chill", "끄기 — 프레임을 일부러 낮추는 기능입니다"),
    ],
)

INTEL = Section(
    title="Intel 그래픽 설정 (직접)",
    lead="Intel Graphics Command Center → 게임.",
    items=[
        ("Adaptive Sync / 수직 동기", "끄기"),
        ("이미지 선명화", "끄기"),
        ("전원 → 그래픽 성능 기본 설정", "최대 성능"),
    ],
)

MONITOR = Section(
    title="모니터 본체 (직접)",
    lead="모니터 아래 버튼으로 들어가는 설정입니다. 여기가 막혀 있으면 윈도우에서 "
         "주사율을 올려도 최대가 안 나옵니다.",
    items=[
        ("주사율(Refresh Rate)", "게이밍 모니터는 최대 주사율을 모니터 메뉴에서 따로 켜야 "
                             "하는 경우가 많습니다. 한 번 확인해 보세요."),
        ("오버드라이브 / 응답속도", "중간 단계. 제일 높은 단계는 오히려 잔상이 생깁니다."),
        ("케이블", "144Hz 이상이면 DisplayPort 를 쓰세요. 오래된 HDMI 는 주사율이 막힙니다."),
    ],
)

AVOIDED = Section(
    title="일부러 안 건드리는 것",
    lead="다른 '최적화 프로그램' 들이 흔히 손대는 것들입니다. 빠뜨린 게 아니라, "
         "얻는 것보다 잃는 게 커서 뺐습니다.",
    items=[
        ("bcdedit — HPET · 플랫폼 클럭", "잘못 되면 윈도우가 아예 안 켜집니다. "
                                   "프레임이 오른다는 근거도 확실하지 않습니다."),
        ("가상 메모리(페이지 파일) 끄기", "메모리가 모자라는 순간 게임이 경고 없이 꺼집니다."),
        ("윈도우 서비스 대량 정지", "소리가 안 나거나 업데이트가 멈추는 식으로 "
                            "한참 뒤에 조용히 고장 납니다."),
        ("레지스트리 청소 · 임시파일 삭제", "프레임과 관계가 없습니다. "
                                 "지운 것을 되돌릴 수도 없습니다."),
        ("게임 파일 자체 수정", "게임을 바꾸는 일이라 계정이 막힐 수 있습니다. "
                        "이 프로그램은 윈도우 설정만 건드립니다."),
    ],
)


def guide_sections(spec=None) -> list[Section]:
    """이 컴퓨터에 맞는 안내만 골라준다."""
    chosen = [IN_GAME]
    names = " ".join(getattr(spec, "gpus", []) or []).lower()
    picked = False
    for needle, section in (
        (("nvidia", "geforce", "rtx ", "gtx "), NVIDIA),
        (("radeon", "amd "), AMD),
        (("intel", "arc ", "iris"), INTEL),
    ):
        if any(word in names for word in needle):
            chosen.append(section)
            picked = True
    if not picked:
        # 그래픽카드를 못 읽었으면 셋 다 보여준다. 골라 읽으시면 된다.
        chosen.extend([NVIDIA, AMD, INTEL])
    chosen.append(MONITOR)
    chosen.append(AVOIDED)
    return chosen


# ============================================================================
# [10] 화면 — 브라우저에 뜨는 페이지
# ============================================================================
#
# 브라우저에 뜨는 화면. 외부 라이브러리 없이 파이썬 표준 http.server 만 쓴다.
#
# 화면에서 제일 큰 것은 '한 번에 최적화' 버튼 하나다. 그 아래에 무엇을 왜 바꾸는지,
# 지금 어떤 상태인지, 그리고 되돌리는 방법이 있다. 눌러야 할 것이 하나라는 게 이
# 프로그램의 요점이고, 나머지는 눌러도 되는지 판단할 재료다.

BADGE = {ON: ("적용됨", "ok"), OFF: ("안 됨", "no"), UNKNOWN: ("확인 불가", "hm"), NA: ("해당 없음", "hm")}


def esc(value) -> str:
    return html.escape(str(value if value is not None else ""))


class Screen:
    def __init__(self, optimizer: Optimizer, root=None):
        self.optimizer = optimizer
        self.root = root
        self.notice = ""
        self.result = None
        self._lock = threading.Lock()

    # --- 화면 ---------------------------------------------------------
    def render(self) -> str:
        ctx = self.optimizer.ctx
        spec = spec_of(ctx)
        statuses = self.optimizer.statuses()
        ready = [s for s in statuses if s.tweak.recommended and s.can_apply]
        record = latest_record(self.root)

        body = [
            _head(spec, ctx),
            _notice(self.notice),
            _result(self.result),
            _hero(ready, statuses),
            _verdicts_box(statuses, spec),
            _basics(spec),
            _game_box(ctx),
            _items(statuses),
            _revert_box(record, record_history(self.root)),
            _guide(spec),
            _footer(),
        ]
        return _PAGE.format(style=_STYLE, body="\n".join(body))

    def render_bye(self) -> str:
        return _PAGE.format(
            style=_STYLE,
            body='<div class="card center"><h2>끝났습니다</h2>'
                 "<p class=muted>이 창은 닫으셔도 됩니다.</p></div>",
        )

    # --- 버튼 ---------------------------------------------------------
    def run(self, action: str, params: dict) -> str:
        # 두 번 눌러도 한 번씩 차례로 처리한다. 겹쳐 돌면 되돌리기 기록이 엉킨다.
        with self._lock:
            return self._run(action, params)

    def _run(self, action: str, params: dict) -> str:
        if action == "apply_all":
            self.result = self.optimizer.apply_recommended()
            return self.result.summary
        if action == "apply":
            keys = params.get("key") or []
            if not keys:
                self.result = None
                return "고른 항목이 없습니다."
            self.result = self.optimizer.apply(keys)
            return self.result.summary
        if action == "revert":
            which = (params.get("record") or [""])[0]
            record = self._record(which)
            if record is None:
                self.result = None
                return "되돌릴 기록이 없습니다."
            self.result = self.optimizer.revert(record)
            if not self.result.steps:
                return "되돌릴 것이 없었습니다."
            return f"{self.result.done}개를 원래대로 되돌렸습니다."
        if action == "game_path":
            value = (params.get("path") or [""])[0].strip()
            remember_game_path(value, self.root)
            self.optimizer.ctx.install = _find(self.optimizer.ctx, value)
            self.result = None
            if self.optimizer.ctx.install:
                return f"찾았습니다: {self.optimizer.ctx.install.exe}"
            return "그 경로에서 실행 파일을 못 찾았습니다. 서든어택 폴더나 exe 를 넣어주세요."
        if action == "admin":
            self.result = None
            if relaunch_as_admin(sys.argv[0]):
                return "관리자 권한으로 새 창을 띄웠습니다. 이 창은 닫으셔도 됩니다."
            return "관리자 권한으로 다시 띄우지 못했습니다. 시작 파일을 우클릭 → 관리자 권한으로 실행 해주세요."
        return ""

    def _record(self, which: str):
        if which:
            for record in record_history(self.root):
                if record.path.name == which:
                    return record
            return None
        return latest_record(self.root)


def _find(ctx, value: str):
    return find_game(registry=ctx.registry, saved=value)


# ---------------------------------------------------------------------------
# 조각들
# ---------------------------------------------------------------------------
def _head(spec, ctx) -> str:
    facts = []
    if spec.cpu:
        cores = f" ({spec.cores}코어)" if spec.cores else ""
        facts.append(("CPU", f"{spec.cpu}{cores}"))
    if spec.gpu:
        facts.append(("그래픽", spec.gpu))
    if spec.ram_gb:
        facts.append(("메모리", f"{spec.ram_gb:g} GB"))
    if spec.windows:
        facts.append(("윈도우", spec.windows))
    facts.append(("형태", spec.shape))
    for screen in spec.monitors:
        mark = "" if screen.already_best else f" → 최대 {screen.best_hz}Hz 가능"
        facts.append(("모니터", f"{screen.width}×{screen.height} · {screen.hz}Hz{mark}"))

    rows = "".join(
        f'<div class="fact"><span>{esc(name)}</span><b>{esc(value)}</b></div>'
        for name, value in facts
    )
    warn = ""
    if not ctx.windows:
        warn = ('<div class="warn">지금은 윈도우가 아닙니다. 화면은 볼 수 있지만 '
                "실제로 바뀌지는 않습니다.</div>")
    elif not ctx.admin:
        warn = (
            '<div class="warn">관리자 권한이 아닙니다. 네트워크·전원처럼 컴퓨터 전체에 '
            "걸리는 항목은 잠겨 있습니다."
            '<form method="post" action="/action" class="inline">'
            '<input type="hidden" name="action" value="admin">'
            '<button class="mini">관리자 권한으로 다시 실행</button></form></div>'
        )
    return (
        '<header><h1>서든어택 최적화</h1>'
        '<p class="muted">윈도우 설정을 게임에 맞게 한 번에 바꿉니다. '
        "바꾼 값은 전부 기록해 두므로 언제든 되돌릴 수 있습니다.</p>"
        f'<div class="facts">{rows}</div>{warn}</header>'
    )


def _hero(ready, statuses) -> str:
    total = len([s for s in statuses if s.tweak.recommended])
    done = len([s for s in statuses if s.tweak.recommended and s.state == ON])
    if ready:
        line = f"권장 {total}개 중 <b>{len(ready)}개</b>를 아직 안 했습니다."
        button = f'<button class="go">한 번에 최적화 ({len(ready)}개)</button>'
    else:
        locked = [s for s in statuses if s.tweak.recommended and s.blocked]
        if locked:
            line = (f"권장 {total}개 중 {len(locked)}개가 <b>잠겨 있습니다.</b> "
                    f"{esc(locked[0].blocked)}.")
        else:
            line = f"권장 {total}개 중 {done}개가 이미 적용돼 있습니다. 더 할 게 없습니다."
        button = '<button class="go" disabled>지금 할 수 있는 것이 없습니다</button>'
    return (
        '<section class="hero">'
        f"<p>{line}</p>"
        '<form method="post" action="/action" onsubmit="wait(this)">'
        '<input type="hidden" name="action" value="apply_all">'
        f"{button}</form>"
        '<p class="muted small">누르면 아래 목록에서 ✅ 표시된 권장 항목만 적용합니다. '
        "몇 초 걸립니다.</p></section>"
    )


def _verdicts_box(statuses, spec) -> str:
    """이 컴퓨터에서 실제로 달라지는 것만 큰 순서대로.

    아래 항목 목록은 15개를 전부 같은 무게로 늘어놓는다. 그것만 보면 "그래서 내가
    뭘 얻는데?" 를 알 수 없다. 여기서는 실제로 읽어온 값으로 판정한 것만, 큰 것부터,
    많아야 서너 줄로 보여준다. 크게 달라질 게 없으면 없다고 말한다.
    """
    found = verdicts(statuses, spec)
    notable = [v for v in found if v.level in (BIG, MID)]
    small = [v for v in found if v.level == SMALL]
    done = [v for v in found if v.level == DONE]
    locked = [v for v in found if v.level == LOCKED]

    if notable:
        rows = "".join(
            f'<div class="v-row"><span class="imp {esc(v.level)}">{esc(v.label)}</span>'
            f'<div><b>{esc(v.title)}</b><span>{esc(v.line)}</span></div></div>'
            for v in notable
        )
        head = f"눌렀을 때 <b>실제로 달라지는 것</b>은 {len(notable)}개입니다."
    else:
        rows = ""
        head = ("이 컴퓨터에서 <b>크게 달라질 것은 없습니다.</b> "
                "중요한 것들은 이미 맞춰져 있습니다.")

    tail = []
    if small:
        tail.append(f"체감이 작은 것 {len(small)}개")
    if done:
        tail.append(f"이미 되어 있는 것 {len(done)}개")
    if locked:
        tail.append(f"잠긴 것 {len(locked)}개")
    rest = ""
    if tail:
        lines = "".join(
            f'<div class="v-mini"><span>{esc(v.label)}</span><b>{esc(v.title)}</b>'
            f"<span>{esc(v.line)}</span></div>"
            for v in small + done + locked
        )
        rest = f'<details><summary>{esc(" · ".join(tail))}</summary>{lines}</details>'

    return (f'<section class="card verdicts"><h3>이 컴퓨터에서는</h3>'
            f"<p>{head}</p>{rows}{rest}</section>")


def _basics(spec) -> str:
    """프레임·주사율·지연·핑이 각각 뭔지, 그리고 이 게임에서 뭐가 진짜 병목인지.

    '체감이 어느 정도냐' 는 질문에 답하려면 먼저 무엇의 체감인지가 갈려야 한다.
    프레임과 주사율을 같은 것으로 알고 있으면 어떤 설명도 와닿지 않는다.
    """
    counts: dict[str, int] = {}
    for tweak in catalog():
        if tweak.affects:
            counts[tweak.affects] = counts.get(tweak.affects, 0) + 1
    chips = "".join(
        f'<span class="chip">{esc(name)} {count}</span>'
        for name, count in sorted(counts.items(), key=lambda pair: -pair[1])
    )

    hz = max((screen.hz for screen in spec.monitors), default=0)
    best = max((screen.best_hz for screen in spec.monitors), default=0)
    mine = ""
    if hz and best > hz:
        mine = (f'<p class="mine">지금 이 컴퓨터는 <b>{hz}Hz</b> 로 돌고 있고 '
                f'<b>{best}Hz</b> 까지 됩니다. 위 버튼이 고쳐줍니다.</p>')
    elif hz:
        mine = f'<p class="mine">지금 이 컴퓨터는 <b>{hz}Hz</b> — 이미 최대입니다.</p>'

    return f"""<details class="basics"><summary>먼저 — 서든어택은 '프레임'이 문제가 아닙니다.
그럼 뭐가 문제일까요?</summary>
<h4>네 가지는 서로 다른 것입니다</h4>
<div class="g-row"><b>프레임 (FPS)</b><span>컴퓨터가 1초에 <b>그리는</b> 장면 수 ·
그래픽카드와 게임 옵션이 정합니다</span></div>
<div class="g-row"><b>주사율 (Hz)</b><span>모니터가 1초에 <b>보여주는</b> 장면 수 ·
모니터와 윈도우 설정이 정합니다</span></div>
<div class="g-row"><b>입력 지연</b><span>마우스를 움직이고 화면에 나타나기까지 걸리는 시간</span></div>
<div class="g-row"><b>핑 (ms)</b><span>내가 쏜 신호가 서버까지 갔다 오는 시간</span></div>

<h4>서든어택은 프레임이 남습니다</h4>
<p>2005년에 나온 게임이라 요즘 컴퓨터에서는 프레임이 이미 넘칩니다.
그런데 <b>프레임이 300이든 400이든, 모니터가 60Hz 면 눈에 보이는 건 초당 60장입니다.</b>
남는 프레임을 더 늘리는 건 아무 의미가 없습니다.</p>
{mine}

<h4>그래서 진짜 병목은 셋입니다</h4>
<div class="g-row"><b>① 모니터가 몇 장 보여주나</b><span>주사율 — 셋 중 제일 큽니다</span></div>
<div class="g-row"><b>② 내 손이 화면에 얼마나 빨리 나타나나</b>
<span>마우스 가속, 전체 화면 최적화</span></div>
<div class="g-row"><b>③ 쏜 게 서버에 얼마나 빨리 닿나</b><span>Nagle, 네트워크 제한</span></div>

<h4>숫자로 보면</h4>
<p>60Hz 는 장면 하나가 <b>16.7ms</b> 동안 그대로 멈춰 있습니다. 그래서 방금 일어난 일이
화면에 뜨기까지 평균 8ms 를 기다립니다. 144Hz 는 6.9ms 라 평균 3.5ms 입니다.<br>
이 4~5ms 차이는 '반응 속도가 빨라진다' 기보다 <b>움직이는 적이 끊기지 않고 이어져
보인다</b> 는 쪽으로 옵니다. 그래서 숫자보다 체감이 큽니다.</p>

<h4>프레임을 정말 올리고 싶다면</h4>
<p>그건 윈도우가 아니라 <b>게임 안 옵션</b>입니다. 그림자·효과 끄기, 수직 동기 끄기.
이 화면 맨 아래 <b>직접 하셔야 하는 것</b> 을 보세요. 거기가 제일 크게 갈립니다.</p>

<h4>이 프로그램이 손대는 곳</h4>
<p class="chips">{chips}</p>
<p class="muted small">프레임 숫자를 실제로 올려주는 항목은 배경 녹화 끄기 하나뿐입니다.
나머지는 지연과 끊김 쪽입니다. 부풀려 적지 않았습니다.</p>
</details>"""


def _game_box(ctx) -> str:
    if ctx.install:
        return (
            '<section class="card slim"><b>서든어택</b> '
            f'<span class="path">{esc(ctx.install.exe)}</span> '
            f'<span class="muted small">({esc(ctx.install.source)})</span>'
            '<form method="post" action="/action" class="inline right">'
            '<input type="hidden" name="action" value="game_path">'
            '<input name="path" placeholder="다른 경로로 바꾸기" size="28">'
            '<button class="mini">바꾸기</button></form></section>'
        )
    return (
        '<section class="card slim miss"><b>서든어택을 못 찾았습니다.</b> '
        '<span class="muted small">설치 폴더나 실행 파일 경로를 넣어주세요. '
        "게임에 직접 거는 3개 항목(전체 화면 최적화·우선순위·검사 제외)에만 필요합니다.</span>"
        '<form method="post" action="/action" class="inline right">'
        '<input type="hidden" name="action" value="game_path">'
        '<input name="path" placeholder="C:\\Nexon\\SuddenAttack" size="30">'
        '<button class="mini">찾기</button></form></section>'
    )


def _items(statuses) -> str:
    blocks = []
    for group in GROUPS:
        rows = [s for s in statuses if s.tweak.group == group]
        if not rows:
            continue
        blocks.append(f'<h3 class="group">{esc(group)}</h3>' + "".join(_item(s) for s in rows))
    return (
        '<form method="post" action="/action" onsubmit="wait(this)">'
        '<input type="hidden" name="action" value="apply">'
        '<section class="list">' + "".join(blocks) + "</section>"
        '<div class="bar"><button class="sub">고른 것만 적용</button>'
        '<span class="muted small">체크를 풀면 그 항목은 건드리지 않습니다.</span></div>'
        "</form>"
    )


def _item(status) -> str:
    tweak = status.tweak
    label, kind = BADGE.get(status.state, ("?", "hm"))
    tags = []
    if tweak.admin:
        tags.append("관리자")
    if tweak.reboot:
        tags.append("재부팅 필요")
    if not tweak.recommended:
        tags.append("선택")
    tag_html = "".join(f'<span class="tag">{esc(t)}</span>' for t in tags)

    checked = " checked" if (tweak.recommended and status.can_apply) else ""
    disabled = " disabled" if status.blocked else ""
    blocked = f'<div class="blocked">{esc(status.blocked)}</div>' if status.blocked else ""
    note = f'<div class="note">{esc(tweak.note)}</div>' if tweak.note else ""
    area = f'<span class="area">{esc(tweak.affects)}</span>' if tweak.affects else ""
    return (
        f'<label class="row{" off" if status.blocked else ""}">'
        f'<input type="checkbox" name="key" value="{esc(tweak.key)}"{checked}{disabled}>'
        '<div class="body">'
        f'<div class="title">{esc(tweak.title)}{tag_html}'
        f'<span class="badge {kind}">{esc(label)}</span></div>'
        f'<div class="what">{esc(tweak.what)}</div>'
        f'<div class="gain"><span class="imp {esc(tweak.impact)}">'
        f'{esc(IMPACT_LABEL.get(tweak.impact, ""))}</span>{area}{esc(tweak.gain)}</div>'
        f"{note}{blocked}"
        "</div></label>"
    )


def _revert_box(record, records) -> str:
    if record is None:
        past = ""
        if records:
            past = ('<p class="muted small">되돌린 기록만 남아 있습니다. '
                    "지금 적용된 것은 없습니다.</p>")
        return ('<section class="card"><h3>되돌리기</h3>'
                '<p class="muted">아직 바꾼 것이 없어서 되돌릴 것도 없습니다.</p>'
                f"{past}</section>")

    changed = ", ".join(record.keys[:6]) + (" …" if len(record.keys) > 6 else "")
    others = ""
    rest = [r for r in records if r.path != record.path]
    if rest:
        lines = "".join(
            f"<li>{esc(r.label)} — {'되돌림' if r.reverted else f'{len(r.keys)}개'}</li>"
            for r in rest[:5]
        )
        others = f'<details><summary>지난 기록 {len(rest)}건</summary><ul>{lines}</ul></details>'
    return (
        '<section class="card"><h3>되돌리기</h3>'
        f'<p><b>{esc(record.label)}</b> 에 {len(record.keys)}개를 바꿨습니다.</p>'
        f'<p class="muted small">{esc(changed)}</p>'
        '<form method="post" action="/action" onsubmit="wait(this)">'
        '<input type="hidden" name="action" value="revert">'
        f'<input type="hidden" name="record" value="{esc(record.path.name)}">'
        '<button class="undo">원래대로 되돌리기</button></form>'
        '<p class="muted small">바꾸기 전 값을 그대로 다시 넣습니다. '
        "원래 없던 값은 지웁니다.</p>"
        f"{others}</section>"
    )


def _guide(spec) -> str:
    blocks = []
    for section in guide_sections(spec):
        rows = "".join(
            f'<div class="g-row"><b>{esc(what)}</b><span>{esc(how)}</span></div>'
            for what, how in section.items
        )
        lead = f'<p class="muted small">{esc(section.lead)}</p>' if section.lead else ""
        blocks.append(
            f'<details class="g"><summary>{esc(section.title)}</summary>{lead}{rows}</details>'
        )
    return '<section class="card"><h3>직접 하셔야 하는 것</h3>' + "".join(blocks) + "</section>"


def _notice(text: str) -> str:
    return f'<div class="notice">{esc(text)}</div>' if text else ""


def _result(outcome) -> str:
    if not outcome or not outcome.steps:
        return ""
    rows = "".join(
        f'<li class="{"ok" if step.ok else "bad"}">{esc(step.title)} — {esc(step.message)}</li>'
        for step in outcome.steps
    )
    reboot = ('<p class="warn-inline">재부팅해야 적용되는 항목이 있습니다.</p>'
              if outcome.reboot else "")
    return f'<section class="card result"><h3>{esc(outcome.summary)}</h3><ul>{rows}</ul>{reboot}</section>'


def _footer() -> str:
    return (
        '<footer class="muted small">서든어택 최적화 v'
        f"{esc(__version__)} · 바꾼 값은 <code>backup/</code> 폴더에 기록됩니다 · "
        "게임 파일은 건드리지 않습니다</footer>"
    )


# ---------------------------------------------------------------------------
_STYLE = """
:root {
  color-scheme: light dark;
  --bg:#f6f7f9; --fg:#1b1d21; --muted:#6b7280; --card:#fff; --line:#e5e7eb;
  --go:#2563eb; --go-fg:#fff; --ok:#16a34a; --no:#9ca3af; --bad:#dc2626; --warn:#b45309;
  --warnbg:#fef3c7;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg:#14161a; --fg:#e8eaed; --muted:#9aa1ab; --card:#1c1f24; --line:#2b2f36;
    --go:#3b82f6; --ok:#22c55e; --no:#6b7280; --bad:#f87171; --warn:#fbbf24; --warnbg:#3a2f14;
  }
}
* { box-sizing:border-box; }
body { margin:0; padding:28px 20px 60px; background:var(--bg); color:var(--fg);
  font-family:-apple-system,"Segoe UI","Malgun Gothic",system-ui,sans-serif;
  max-width:860px; margin-inline:auto; line-height:1.6; }
h1 { font-size:1.6rem; margin:0 0 6px; }
h3 { margin:0 0 12px; font-size:1.05rem; }
.muted { color:var(--muted); }
.small { font-size:.85rem; }
.center { text-align:center; }
header { margin-bottom:22px; }
.facts { display:grid; grid-template-columns:repeat(auto-fit,minmax(230px,1fr));
  gap:8px; margin-top:14px; }
.fact { background:var(--card); border:1px solid var(--line); border-radius:10px;
  padding:9px 12px; font-size:.86rem; display:flex; gap:8px; justify-content:space-between; }
.fact span { color:var(--muted); white-space:nowrap; }
.fact b { font-weight:600; text-align:right; overflow-wrap:anywhere; }
.warn { margin-top:14px; padding:12px 14px; border-radius:10px; background:var(--warnbg);
  color:var(--warn); border:1px solid var(--warn); font-size:.9rem; }
.warn-inline { color:var(--warn); font-size:.9rem; }
.hero { background:var(--card); border:1px solid var(--line); border-radius:16px;
  padding:24px; text-align:center; margin-bottom:16px; }
.hero p { margin:0 0 14px; }
button { font:inherit; cursor:pointer; border-radius:10px; border:1px solid var(--line);
  background:var(--card); color:var(--fg); padding:9px 16px; }
button:disabled { opacity:.5; cursor:default; }
.go { background:var(--go); color:var(--go-fg); border:none; font-size:1.15rem;
  font-weight:700; padding:16px 34px; border-radius:12px; }
.go:disabled { background:var(--no); }
.sub { font-weight:600; }
.undo { border-color:var(--bad); color:var(--bad); font-weight:600; }
.mini { padding:6px 12px; font-size:.85rem; }
.card { background:var(--card); border:1px solid var(--line); border-radius:14px;
  padding:18px 20px; margin-bottom:16px; }
.card.slim { padding:12px 16px; font-size:.9rem; }
.card.miss { border-color:var(--warn); }
.path { font-family:ui-monospace,Consolas,monospace; font-size:.85rem; overflow-wrap:anywhere; }
.inline { display:inline; }
.right { float:right; }
.list { margin-bottom:12px; }
.group { margin:22px 0 8px; font-size:.8rem; letter-spacing:.08em; color:var(--muted); }
.row { display:flex; gap:12px; align-items:flex-start; background:var(--card);
  border:1px solid var(--line); border-radius:12px; padding:14px 16px; margin-bottom:8px;
  cursor:pointer; }
.row.off { opacity:.62; cursor:default; }
.row input { margin-top:5px; width:17px; height:17px; flex:none; }
.body { flex:1; min-width:0; }
.title { font-weight:600; display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
.what { color:var(--muted); font-size:.88rem; margin-top:3px; }
.gain { font-size:.88rem; margin-top:5px; }
.imp { font-size:.72rem; font-weight:700; padding:1px 8px; border-radius:999px;
  margin-right:6px; white-space:nowrap; }
.imp.big { background:var(--go); color:#fff; }
.imp.mid { background:var(--warnbg); color:var(--warn); }
.imp.small { background:var(--bg); color:var(--muted); border:1px solid var(--line); }
.area { font-size:.72rem; padding:1px 8px; border-radius:999px; margin-right:8px;
  border:1px solid var(--line); color:var(--muted); white-space:nowrap; }
details.basics { background:var(--card); border:1px solid var(--line); border-radius:14px;
  padding:16px 20px; margin-bottom:16px; }
details.basics > summary { cursor:pointer; font-weight:700; font-size:1rem; }
details.basics h4 { margin:18px 0 8px; font-size:.92rem; }
details.basics p { font-size:.9rem; margin:6px 0; }
.mine { background:var(--bg); border-radius:8px; padding:9px 12px; }
.verdicts > p { margin:0 0 12px; }
.v-row { display:flex; gap:10px; align-items:flex-start; padding:10px 0;
  border-top:1px solid var(--line); }
.v-row > div { display:flex; flex-direction:column; gap:2px; min-width:0; }
.v-row b { font-size:.95rem; }
.v-row span:not(.imp) { color:var(--muted); font-size:.88rem; }
.v-row .imp { margin-top:3px; }
.verdicts details { margin-top:12px; }
.verdicts summary { cursor:pointer; color:var(--muted); font-size:.88rem; }
.v-mini { display:flex; gap:8px; padding:5px 0; font-size:.84rem; flex-wrap:wrap; }
.v-mini > span:first-child { color:var(--muted); min-width:52px; }
.v-mini > span:last-child { color:var(--muted); flex:1; min-width:200px; }
.chips { display:flex; gap:6px; flex-wrap:wrap; }
.chip { font-size:.78rem; padding:2px 10px; border-radius:999px; background:var(--bg);
  border:1px solid var(--line); }
.note { font-size:.82rem; margin-top:5px; color:var(--warn); }
.blocked { font-size:.82rem; margin-top:5px; color:var(--bad); }
.badge { margin-left:auto; font-size:.75rem; padding:2px 9px; border-radius:999px;
  border:1px solid var(--line); white-space:nowrap; }
.badge.ok { color:var(--ok); border-color:var(--ok); }
.badge.no { color:var(--muted); }
.badge.hm { color:var(--warn); border-color:var(--warn); }
.tag { font-size:.7rem; padding:1px 7px; border-radius:999px; background:var(--bg);
  border:1px solid var(--line); color:var(--muted); }
.bar { display:flex; gap:12px; align-items:center; margin-bottom:22px; flex-wrap:wrap; }
.notice { background:var(--card); border:1px solid var(--go); border-radius:10px;
  padding:11px 15px; margin-bottom:16px; }
.result ul { margin:0; padding-left:18px; }
.result li.ok::marker { color:var(--ok); }
.result li.bad { color:var(--bad); }
details.g { border:1px solid var(--line); border-radius:10px; padding:10px 14px;
  margin-bottom:8px; }
details.g summary { cursor:pointer; font-weight:600; }
.g-row { display:flex; gap:12px; padding:6px 0; border-top:1px solid var(--line);
  font-size:.88rem; flex-wrap:wrap; }
.g-row b { min-width:180px; font-weight:600; }
.g-row span { color:var(--muted); flex:1; }
footer { margin-top:28px; text-align:center; }
input[type=text], input:not([type]) { font:inherit; padding:6px 10px; border-radius:8px;
  border:1px solid var(--line); background:var(--bg); color:var(--fg); }
"""

_PAGE = """<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>서든어택 최적화</title>
<style>{style}</style>
<script>
// 몇 초 걸리는 작업이다. 아무 반응이 없으면 두 번 누르게 되고, 두 번 누르면
// 되돌리기 기록이 두 개 생긴다. 누른 순간 버튼을 잠근다.
function wait(form) {{
  var button = form.querySelector('button');
  if (button) {{ button.disabled = true; button.textContent = '하는 중…'; }}
}}
</script>
{body}
"""


# ---------------------------------------------------------------------------
class _Handler(BaseHTTPRequestHandler):
    screen: Screen = None

    def log_message(self, fmt, *args):
        log.debug("화면 %s", fmt % args)

    def _guard(self) -> bool:
        # 이 프로그램은 컴퓨터 설정을 바꾼다. 같은 컴퓨터에서 연 것만 받는다.
        if self.client_address[0] not in ("127.0.0.1", "::1"):
            self.send_error(403, "localhost only")
            return False
        return True

    def do_GET(self):
        if not self._guard():
            return
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._html(self.screen.render())
        elif path == "/healthz":
            self._respond(b"ok", "text/plain; charset=utf-8")
        else:
            self.send_error(404)

    def do_POST(self):
        if not self._guard():
            return
        if urlparse(self.path).path != "/action":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length") or 0)
        params = parse_qs(self.rfile.read(length).decode("utf-8"))
        action = (params.get("action") or [""])[0]

        self.screen.notice = self.screen.run(action, params)
        self.send_response(303)
        self.send_header("Location", "/")
        self.end_headers()

    def _html(self, text: str):
        self._respond(text.encode("utf-8"), "text/html; charset=utf-8")

    def _respond(self, payload: bytes, content_type: str):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)


def start_screen(screen: Screen, port: int = 8770, open_browser: bool = True):
    handler = type("Handler", (_Handler,), {"screen": screen})
    server = None
    for candidate in range(port, port + 10):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", candidate), handler)
            port = candidate
            break
        except OSError as exc:
            log.debug("포트 %s 사용 중: %s", candidate, exc)
    if server is None:
        raise OSError(f"{port}~{port + 9} 포트가 모두 사용 중입니다.")

    url = f"http://127.0.0.1:{port}/"
    log.info("화면: %s", url)
    if open_browser:
        threading.Timer(0.7, lambda: _open(url)).start()
    return server, url


def _open(url: str) -> None:
    try:
        webbrowser.open(url)
    except Exception as exc:
        log.warning("브라우저를 열지 못했습니다(%s). 직접 %s 에 접속하세요.", exc, url)


# ============================================================================
# [11] 시작 지점 — 더블클릭과 명령줄
# ============================================================================
def use_utf8_output() -> None:
    """한글이 깨지지 않게. 윈도우 콘솔은 기본이 cp949 라서 그냥 두면 글자가 깨진다."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def build_optimizer(root: Path = ROOT) -> Optimizer:
    return Optimizer(build_context(root), root=root)


def cmd_screen(args) -> int:
    optimizer = build_optimizer()
    screen = Screen(optimizer, root=ROOT)
    server, url = start_screen(screen, port=args.port, open_browser=not args.no_browser)

    print()
    print("  서든어택 최적화 v" + __version__)
    print("  화면:", url)
    if WINDOWS and not is_admin():
        print("  ! 관리자 권한이 아닙니다 — 일부 항목이 잠깁니다.")
        print("    바탕화면의 시작 파일을 우클릭 → '관리자 권한으로 실행' 하시면 전부 풀립니다.")
    print()
    print("  이 창을 닫거나 Ctrl+C 를 누르면 끝납니다.")
    print()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("  끝냅니다.")
    finally:
        server.shutdown()
        server.server_close()
    return 0


def cmd_status(args) -> int:
    optimizer = build_optimizer()
    ctx = optimizer.ctx
    spec = spec_of(ctx)

    print()
    print(f"  CPU     {spec.cpu or '-'}")
    print(f"  그래픽  {spec.gpu or '-'}")
    print(f"  메모리  {spec.ram_gb or '-'} GB")
    print(f"  윈도우  {spec.windows or '-'}")
    for screen in spec.monitors:
        extra = "" if screen.already_best else f"  (최대 {screen.best_hz}Hz 가능)"
        print(f"  모니터  {screen.width}x{screen.height} {screen.hz}Hz{extra}")
    print(f"  서든어택 {ctx.install.exe if ctx.install else '못 찾음'}")
    print(f"  권한    {'관리자' if ctx.admin else '일반 사용자'}")
    print()

    marks = {"on": "[적용됨]", "off": "[ 안됨 ]", "unknown": "[확인??]", "na": "[해당없음]"}
    for status in optimizer.statuses():
        note = f"  ({status.blocked})" if status.blocked else ""
        print(f"  {marks.get(status.state, '[  ?  ]')} {status.tweak.title}{note}")

    record = latest_record(ROOT)
    print()
    if record:
        print(f"  되돌릴 기록: {record.label} ({len(record.keys)}개)")
    else:
        print("  되돌릴 기록이 없습니다.")
    return 0


def cmd_apply(args) -> int:
    optimizer = build_optimizer()
    keys = args.only or optimizer.recommended_keys()
    if not keys:
        print("  바꿀 것이 없습니다. 이미 다 되어 있습니다.")
        return 0

    outcome = optimizer.apply(keys)
    print()
    for step in outcome.steps:
        print(f"  {'O' if step.ok else 'X'}  {step.title} — {step.message}")
    print()
    print("  " + outcome.summary)
    if outcome.record:
        print(f"  되돌리기 기록: {outcome.record.name}")
        print("  되돌리려면: 되돌리기.bat 을 더블클릭하거나 python optimizer.py revert")
    return 0 if not outcome.failed else 1


def cmd_revert(args) -> int:
    optimizer = build_optimizer()
    outcome = optimizer.revert()
    if not outcome.steps:
        print("  되돌릴 기록이 없습니다.")
        return 0
    print()
    for step in outcome.steps:
        print(f"  {'O' if step.ok else 'X'}  {step.title} — {step.message}")
    print()
    print(f"  {outcome.done}개를 원래대로 되돌렸습니다.")
    return 0 if not outcome.failed else 1


def main(argv=None) -> int:
    use_utf8_output()
    parser = argparse.ArgumentParser(
        prog="sudden", description="서든어택 최적화 — 윈도우 설정을 게임에 맞게 한 번에"
    )
    parser.add_argument("--verbose", action="store_true", help="자세한 기록 출력")
    sub = parser.add_subparsers(dest="command")

    screen = sub.add_parser("screen", help="화면 띄우기 (기본)")
    screen.add_argument("--port", type=int, default=8770)
    screen.add_argument("--no-browser", action="store_true")
    screen.set_defaults(func=cmd_screen)

    sub.add_parser("status", help="지금 상태 보기").set_defaults(func=cmd_status)

    apply_cmd = sub.add_parser("apply", help="권장 항목 적용")
    apply_cmd.add_argument("--only", nargs="*", help="이 항목만 (예: mouse_accel refresh_rate)")
    apply_cmd.set_defaults(func=cmd_apply)

    sub.add_parser("revert", help="마지막 최적화 되돌리기").set_defaults(func=cmd_revert)

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)-7s %(name)s: %(message)s",
    )
    if not getattr(args, "func", None):
        args.port, args.no_browser = 8770, False
        return cmd_screen(args)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())