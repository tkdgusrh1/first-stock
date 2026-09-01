"""이 컴퓨터가 어떤 물건인지 읽어온다.

화면 맨 위에 그대로 보여준다. 최적화를 누르기 전에 "내 컴퓨터를 제대로 보고 있구나"
를 확인할 수 있어야 안심하고 누를 수 있다. 값을 못 읽으면 지어내지 않고 비워둔다.
"""

from __future__ import annotations

import ctypes
import logging
import os
import platform
import sys
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

WINDOWS = sys.platform.startswith("win")

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

    @property
    def gpu(self) -> str:
        return " · ".join(self.gpus)


def read(registry, display=None) -> Spec:
    spec = Spec(
        cpu=_cpu(registry),
        cores=os.cpu_count() or 0,
        ram_gb=_ram_gb(),
        gpus=_gpus(registry),
        windows=_windows(registry),
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
