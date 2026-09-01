"""모니터 주사율 확인·변경.

의외로 이게 체감이 제일 크다. 144Hz 모니터를 사놓고 윈도우가 60Hz 로 잡아둔 채
쓰는 경우가 흔한데, 그러면 게임 안에서 프레임이 아무리 나와도 눈에 보이는 건
초당 60장이다. 그래서 "설치돼 있는 모니터가 낼 수 있는 최대"로 올려준다.

윈도우 API(ctypes)를 직접 부른다. 바꾼 값은 되돌리기용으로 그대로 넘겨준다.
"""

from __future__ import annotations

import ctypes
import logging
import sys
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

WINDOWS = sys.platform.startswith("win")

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
