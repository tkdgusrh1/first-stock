"""서든어택 최적화 검사에서 같이 쓰는 가짜 컴퓨터.

powercfg 는 답만 정해놓고 돌려주면 안 된다. 전원 계획은 '복제하고 → 켜고 →
되돌릴 때 원래 걸로 돌아간 뒤 지운다' 는 순서 자체가 검사 대상이라, 가짜도 계획
목록과 지금 켜진 계획을 실제로 들고 있어야 한다.
"""

from pathlib import PureWindowsPath

from sudden_attack.display import FakeDisplay, Monitor
from sudden_attack.engine import Context
from sudden_attack.game import Install
from sudden_attack.shell import FakeShell, Result
from sudden_attack.winreg_io import DWORD, STR, FakeRegistry, RegValue

BALANCED = "381b4222-f694-41f0-9685-ff5bb260df2e"
HIGH = "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"


class FakeWindows(FakeShell):
    """powercfg 와 윈도우 보안 명령을 흉내 낸다."""

    def __init__(self):
        super().__init__()
        self.schemes = {BALANCED: "균형 조정", HIGH: "고성능"}
        self.active = BALANCED
        self.tuned: dict = {}          # {계획 GUID: [(묶음, 설정, 값), ...]}
        self.exclusions: list = []
        self._made = 0

    def run(self, args, timeout=60):
        self.calls.append(list(args))
        if args and args[0] == "powercfg":
            return self._powercfg(args[1:])
        if args and args[0] == "powershell":
            return self._defender(args[-1])
        return Result(ok=True)

    # --- powercfg -----------------------------------------------------
    def _powercfg(self, args) -> Result:
        verb = (args[0] if args else "").lstrip("/-").lower()
        rest = args[1:]

        if verb == "getactivescheme":
            return Result(ok=True, out=self._line(self.active))
        if verb == "list":
            return Result(ok=True, out="\n".join(self._line(g) for g in self.schemes))
        if verb == "duplicatescheme":
            source = rest[0]
            if source not in self.schemes:
                return Result(ok=False, err="그런 전원 구성표가 없습니다.", code=1)
            self._made += 1
            guid = f"{self._made:08d}-2222-3333-4444-555555555555"
            self.schemes[guid] = self.schemes[source]
            return Result(ok=True, out=self._line(guid))
        if verb == "changename":
            self.schemes[rest[0]] = rest[1]
            return Result(ok=True)
        if verb == "setactive":
            if rest[0] not in self.schemes:
                return Result(ok=False, err="그런 전원 구성표가 없습니다.", code=1)
            self.active = rest[0]
            return Result(ok=True)
        if verb == "delete":
            if rest[0] == self.active:
                return Result(ok=False, err="사용 중인 구성표는 지울 수 없습니다.", code=1)
            self.schemes.pop(rest[0], None)
            self.tuned.pop(rest[0], None)
            return Result(ok=True)
        if verb in ("setacvalueindex", "setdcvalueindex"):
            self.tuned.setdefault(rest[0], []).append(tuple(rest[1:]))
            return Result(ok=True)
        return Result(ok=True)

    def _line(self, guid: str) -> str:
        star = " *" if guid == self.active else ""
        return f"전원 구성표 GUID: {guid}  ({self.schemes.get(guid, '')}){star}"

    # --- 윈도우 보안 --------------------------------------------------
    def _defender(self, script: str) -> Result:
        if "Get-MpPreference" in script:
            return Result(ok=True, out="\n".join(self.exclusions))
        path = script.split('"')[1] if '"' in script else ""
        if "Add-MpPreference" in script:
            if path and path not in self.exclusions:
                self.exclusions.append(path)
            return Result(ok=True)
        if "Remove-MpPreference" in script:
            self.exclusions = [p for p in self.exclusions if p != path]
            return Result(ok=True)
        return Result(ok=True)


def fake_context(*, admin=True, windows=True, install=True, monitors=True,
                 registry=None, shell=None):
    return Context(
        registry=registry if registry is not None else seeded_registry(),
        shell=shell if shell is not None else FakeWindows(),
        display=FakeDisplay(
            screens=[Monitor("\\\\.\\DISPLAY1", "가짜 모니터", 1920, 1080, 60, 144)]
            if monitors
            else []
        ),
        install=Install(
            exe=PureWindowsPath(r"C:\Nexon\SuddenAttack\SuddenAttack.exe"),
            folder=PureWindowsPath(r"C:\Nexon\SuddenAttack"),
            source="검사용",
        )
        if install
        else None,
        admin=admin,
        windows=windows,
    )


def seeded_registry() -> FakeRegistry:
    """최적화 전의 평범한 윈도우. 몇몇 값은 이미 있고, 몇몇은 아예 없다."""
    registry = FakeRegistry()
    # 마우스 가속이 켜져 있는 기본 상태
    for name, value in (("MouseSpeed", "1"), ("MouseThreshold1", "6"), ("MouseThreshold2", "10")):
        registry.write("HKCU", r"Control Panel\Mouse", name, RegValue(value, STR))
    # 랜카드 두 장 — 하나는 IP 가 있고 하나는 없다
    base = r"SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces"
    registry.write("HKLM", rf"{base}\{{net-1}}", "DhcpIPAddress", RegValue("192.168.0.5", STR))
    registry.write("HKLM", rf"{base}\{{net-2}}", "DhcpIPAddress", RegValue("0.0.0.0", STR))
    # 배경 녹화는 켜져 있는 상태
    registry.write("HKCU", r"System\GameConfigStore", "GameDVR_Enabled", RegValue(1, DWORD))
    return registry
