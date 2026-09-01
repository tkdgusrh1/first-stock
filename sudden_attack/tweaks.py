"""무엇을 바꿀 것인가 — 최적화 항목 전부.

원칙 네 가지를 지킨다.

  1. **되돌릴 수 있는 것만 넣는다.** 바꾸기 전 값을 못 읽어오는 설정은 아예 안 넣는다.
  2. **부팅을 건드리지 않는다.** bcdedit(HPET·플랫폼 클럭), 페이지 파일 삭제,
     서비스 무더기 정지 같은 건 넣지 않았다. 효과는 불확실한데 잘못되면 윈도우가
     안 켜지거나 소리가 안 난다. "최적화 프로그램 돌렸더니 컴퓨터가 이상해졌다"는
     사고는 대부분 여기서 난다.
  3. **효과를 부풀리지 않는다.** 항목마다 왜 하는지 한 줄로 적어두고, 체감이 작은
     것은 작다고 쓴다.
  4. **위험한 건 기본으로 켜지 않는다.** recommended=False 는 사용자가 직접 눌러야 한다.
"""

from __future__ import annotations

import ctypes
import logging
import re
from dataclasses import dataclass

from .winreg_io import DWORD, STR, RegValue

log = logging.getLogger(__name__)

ON = "on"           # 이미 적용돼 있다
OFF = "off"         # 아직 아니다
UNKNOWN = "unknown"  # 읽어봤는데 판단이 안 선다
NA = "na"           # 이 컴퓨터에서는 해당 없음 (게임을 못 찾았다 등)

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
    why: str                    # 왜 하는지 — 화면에 그대로 나간다
    group: str
    action: Action
    recommended: bool = True    # '한 번에 최적화' 에 포함되는가
    admin: bool = False         # 관리자 권한이 있어야 하는가
    reboot: bool = False        # 재부팅해야 먹는가
    note: str = ""              # 알아둬야 할 것


GROUPS = ["입력", "화면", "전원", "네트워크", "윈도우", "게임"]


def catalog() -> list[Tweak]:
    """최적화 항목 전부. 순서가 화면 순서다."""
    return [
        # --- 입력 -------------------------------------------------------
        Tweak(
            key="mouse_accel",
            title="마우스 가속 끄기",
            why="같은 거리를 움직여도 빠르게 움직이면 더 많이 도는 기능을 끕니다. "
                "에임이 손에 붙는 데 이게 제일 큽니다.",
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
            why="144Hz 모니터를 60Hz 로 쓰고 있으면 게임 프레임이 아무리 높아도 "
                "눈에 보이는 건 초당 60장입니다. 해상도는 건드리지 않습니다.",
            group="화면",
            action=RefreshRateAction(),
        ),
        Tweak(
            key="fullscreen_opt",
            title="전체 화면 최적화 끄기 (서든어택)",
            why="윈도우가 게임을 몰래 창 모드로 돌리는 기능입니다. 화면이 한 단계 더 "
                "거쳐 나가서 입력이 늦게 느껴집니다.",
            group="화면",
            action=LayersAction(),
            note="서든어택 설치 경로를 찾아야 적용됩니다.",
        ),
        Tweak(
            key="visual_effects",
            title="윈도우 화면 효과 줄이기",
            why="창이 부드럽게 열리고 닫히는 효과를 끕니다. 프레임이 크게 오르진 "
                "않지만 알트탭이 눈에 띄게 빨라집니다.",
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
            why="CPU가 절전하려고 속도를 낮추지 않게 합니다. 쓰던 계획을 고치는 게 "
                "아니라 복제해서 새로 만들기 때문에 원래 설정은 그대로 남습니다.",
            group="전원",
            action=PowerPlanAction(),
            admin=True,
            note="노트북이라면 배터리로 쓸 때 사용 시간이 줄어듭니다.",
        ),
        # --- 네트워크 ---------------------------------------------------
        Tweak(
            key="nagle",
            title="네트워크 지연 줄이기 (Nagle 끄기)",
            why="작은 신호를 모았다가 한꺼번에 보내는 기능을 끕니다. 파일 받을 땐 "
                "이득이지만 총 쏜 신호 하나를 보내는 게임에선 그 시간이 그대로 핑입니다.",
            group="네트워크",
            action=NagleAction(),
            admin=True,
        ),
        Tweak(
            key="net_throttle",
            title="네트워크 속도 제한 풀기",
            why="윈도우는 멀티미디어 재생을 위해 초당 패킷 수를 묶어둡니다. 게임에는 "
                "필요 없는 제한이라 풉니다.",
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
            why="윈도우가 갖고 있는 '게임' 작업 등급의 우선순위를 올립니다. "
                "배경 프로그램보다 게임을 먼저 처리합니다.",
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
            why="게임이 앞에 있을 때 윈도우 업데이트와 배경 작업을 미룹니다.",
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
            why="Xbox Game Bar 가 게임 화면을 늘 몰래 녹화하고 있습니다. 프레임을 "
                "직접 깎아먹는 기능이라 끕니다.",
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
            why="게임 도중 오른쪽 아래에서 튀어나오는 알림을 막습니다.",
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
            why="서든어택처럼 프레임이 아주 높게 나오는 가벼운 게임에서는 이 기능을 "
                "끄는 쪽이 프레임이 고르게 나오는 경우가 많습니다. 다만 컴퓨터마다 "
                "다르니 켜보고 비교해 보세요.",
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
            why="게임이 켜질 때부터 CPU를 먼저 받게 합니다. 작업 관리자에서 매번 손으로 "
                "바꾸던 것과 같은 값입니다.",
            group="게임",
            action=PriorityAction(),
            admin=True,
            note="서든어택 설치 경로를 찾아야 적용됩니다. '실시간'은 위험해서 넣지 않았습니다.",
        ),
        Tweak(
            key="defender",
            title="백신 실시간 검사에서 게임 폴더 빼기",
            why="게임이 파일을 읽을 때마다 윈도우 보안이 훑으면 순간 렉이 생깁니다.",
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
