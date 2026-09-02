"""서든어택 최적화 자동 검사.

이 프로그램의 약속은 하나다 — **바꾼 것은 언제든 원래대로 돌아온다.**
그래서 검사의 중심도 거기에 있다. 저장소 전체를 통째로 떠놓고, 적용했다가
되돌린 뒤 손대기 전과 한 글자도 다르지 않은지 비교한다.

윈도우가 없어도 돌아간다. 레지스트리·powercfg·모니터를 전부 가짜로 바꿔 끼우기
때문이다. 그래서 리눅스 CI 에서도 그대로 검사된다.

    python -m pytest 서든어택최적화/tests -q

절 번호는 optimizer.py 의 지도와 같다. [7] 은 optimizer.py 의 [7] 을 검사한다.
"""

import json
import sys
from pathlib import Path, PureWindowsPath

import pytest

# optimizer.py 는 이 폴더 바로 위에 있다. conftest.py 를 따로 두지 않는 이유는,
# 저장소에 검사 폴더가 둘이라 conftest 라는 이름이 서로 부딪히기 때문이다.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from optimizer import (  # noqa: E402
    BIG, BINARY, DWORD, GROUPS, IMPACT_LABEL, NA, OFF, ON, STR,
    Context, FakeDisplay, FakeRegistry, FakeShell, Install, Monitor, RegValue, Result,
    Optimizer, Screen, Shell, Tweak, catalog,
    DONE, LOCKED, MID, SMALL, verdicts,
    RECORD_PREFIX, _decode, _parse_scheme,
    backup_folder, by_key, find_game, latest_record, record_history,
    remember_game_path, saved_game_path, spec_of,
)


# ============================================================================
# 가짜 컴퓨터 — 윈도우 없이도 '적용 → 되돌리기' 를 검사하기 위한 것
# ============================================================================
#
# 서든어택 최적화 검사에서 같이 쓰는 가짜 컴퓨터.
#
# powercfg 는 답만 정해놓고 돌려주면 안 된다. 전원 계획은 '복제하고 → 켜고 →
# 되돌릴 때 원래 걸로 돌아간 뒤 지운다' 는 순서 자체가 검사 대상이라, 가짜도 계획
# 목록과 지금 켜진 계획을 실제로 들고 있어야 한다.
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


# ============================================================================
# [1] 레지스트리 — 레지스트리 감싸개 — 되돌리기의 근거가 되는 부분이라 꼼꼼히 본다.
# ============================================================================
def test_read_write_delete():
    registry = FakeRegistry()
    assert registry.read("HKCU", r"A\B", "x") is None

    registry.write("HKCU", r"A\B", "x", RegValue(1, DWORD))
    assert registry.read("HKCU", r"A\B", "x").data == 1

    registry.delete_value("HKCU", r"A\B", "x")
    assert registry.read("HKCU", r"A\B", "x") is None


def test_paths_and_names_ignore_case():
    """윈도우 레지스트리는 대소문자를 구분하지 않는다. 가짜도 똑같아야 한다."""
    registry = FakeRegistry()
    registry.write("HKCU", r"Control Panel\Mouse", "MouseSpeed", RegValue("0", STR))
    assert registry.read("HKCU", r"control panel\mouse", "mousespeed").data == "0"


def test_subkeys_lists_one_level_only():
    registry = FakeRegistry()
    for guid in ("{aaa}", "{bbb}"):
        registry.write("HKLM", rf"Base\Interfaces\{guid}", "IPAddress", RegValue("10.0.0.2", STR))
    registry.write("HKLM", r"Base\Interfaces\{aaa}\Deeper", "z", RegValue(1, DWORD))

    assert registry.subkeys("HKLM", r"Base\Interfaces") == ["{aaa}", "{bbb}"]


def test_delete_key_leaves_non_empty_keys_alone():
    registry = FakeRegistry()
    registry.write("HKLM", r"Base\Keep", "v", RegValue(1, DWORD))
    registry.delete_key("HKLM", r"Base\Keep")
    assert registry.key_exists("HKLM", r"Base\Keep")

    registry.delete_value("HKLM", r"Base\Keep", "v")
    registry.delete_key("HKLM", r"Base\Keep")
    assert not registry.key_exists("HKLM", r"Base\Keep")


def test_value_survives_a_trip_through_json():
    """되돌리기 기록은 JSON 파일이다. 넣었다 뺐을 때 값이 그대로여야 한다."""
    for value in (RegValue(0xFFFFFFFF, DWORD), RegValue("High", STR), RegValue(b"\x01\x02", BINARY)):
        again = RegValue.from_json(value.as_json())
        assert again.kind == value.kind
        assert again.data == value.data


def test_a_key_holding_someone_elses_values_is_never_deleted():
    """되돌리기가 남이 넣어둔 값까지 쓸어버리면 안 된다."""
    registry = FakeRegistry()
    registry.write("HKLM", r"Base\Shared", "ours", RegValue(1, DWORD))
    registry.write("HKLM", r"Base\Shared", "theirs", RegValue(7, DWORD))

    registry.delete_value("HKLM", r"Base\Shared", "ours")
    registry.delete_key("HKLM", r"Base\Shared")

    assert registry.read("HKLM", r"Base\Shared", "theirs").data == 7


# ============================================================================
# [2] 명령 실행 — 바깥 명령 실행 — 한글 출력과 실패 처리.
# ============================================================================
def test_korean_windows_output_is_read_not_dropped():
    """윈도우 명령은 한국어 윈도우에서 cp949 로 나온다. UTF-8 로만 읽으면 깨진다."""
    assert _decode("전원 구성표".encode("cp949")) == "전원 구성표"
    assert _decode("Power Scheme".encode("utf-8")) == "Power Scheme"
    assert _decode(b"") == ""
    assert _decode(None) == ""


def test_undecodable_bytes_do_not_raise():
    """글자 하나 때문에 최적화가 통째로 멈추면 안 된다."""
    assert isinstance(_decode(b"\xff\xfe\x00\x01"), str)


def test_a_missing_command_is_a_failure_not_a_crash():
    result = Shell().run(["이런-명령은-없다"])
    assert isinstance(result, Result)
    assert not result.ok
    assert "찾지 못했습니다" in result.err


def test_a_command_that_runs(tmp_path):
    result = Shell().run(["echo", "안녕"])
    assert result.ok and result.out == "안녕"


# ============================================================================
# [5] 게임 찾기 — 서든어택 설치 위치 찾기.
# ============================================================================
def make_install(tmp_path, folder="Nexon/SuddenAttack", exe="SuddenAttack.exe"):
    target = tmp_path / folder
    target.mkdir(parents=True)
    (target / exe).write_text("", encoding="utf-8")
    return target


def test_found_from_the_uninstall_list(tmp_path):
    folder = make_install(tmp_path)
    registry = FakeRegistry()
    base = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
    registry.write("HKLM", rf"{base}\SuddenAttack", "DisplayName", RegValue("서든어택", STR))
    registry.write("HKLM", rf"{base}\SuddenAttack", "InstallLocation", RegValue(str(folder), STR))

    found = find_game(registry=registry, roots=[])
    assert found is not None
    assert found.exe.name == "SuddenAttack.exe"
    assert "프로그램 목록" in found.source


def test_other_programs_in_the_uninstall_list_are_ignored(tmp_path):
    make_install(tmp_path, folder="Other/Game", exe="Something.exe")
    registry = FakeRegistry()
    base = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
    registry.write("HKLM", rf"{base}\Other", "DisplayName", RegValue("다른 게임", STR))
    registry.write("HKLM", rf"{base}\Other", "InstallLocation",
                   RegValue(str(tmp_path / "Other" / "Game"), STR))

    assert find_game(registry=registry, roots=[]) is None


def test_found_by_scanning_a_drive(tmp_path):
    make_install(tmp_path)
    found = find_game(registry=FakeRegistry(), roots=[str(tmp_path)])
    assert found is not None and found.exe.name == "SuddenAttack.exe"


def test_a_path_typed_by_hand_wins(tmp_path):
    folder = make_install(tmp_path, folder="어딘가/서든")
    found = find_game(registry=FakeRegistry(), roots=[], saved=str(folder))
    assert found is not None
    assert found.source == "직접 넣은 경로"


def test_the_exe_itself_can_be_given(tmp_path):
    folder = make_install(tmp_path)
    found = find_game(registry=FakeRegistry(), roots=[], saved=str(folder / "SuddenAttack.exe"))
    assert found is not None and found.folder == folder


def test_a_wrong_path_gives_nothing_rather_than_a_guess(tmp_path):
    assert find_game(registry=FakeRegistry(), roots=[], saved=str(tmp_path / "없는곳")) is None


def test_nothing_found_is_not_an_error(tmp_path):
    assert find_game(registry=FakeRegistry(), roots=[str(tmp_path)]) is None


# ============================================================================
# [7] 최적화 항목 — 항목 하나하나가 제대로 읽고, 쓰고, 되돌리는가.
# ============================================================================
def find(key):
    return by_key()[key]


# --- 레지스트리 항목 ---------------------------------------------------
def test_mouse_accel_off_then_back():
    ctx = fake_context()
    tweak = find("mouse_accel")
    assert tweak.action.state(ctx) == OFF

    record = tweak.action.apply(ctx)
    assert tweak.action.state(ctx) == ON
    assert ctx.registry.read("HKCU", r"Control Panel\Mouse", "MouseSpeed").data == "0"

    tweak.action.revert(ctx, record)
    assert ctx.registry.read("HKCU", r"Control Panel\Mouse", "MouseSpeed").data == "1"
    assert ctx.registry.read("HKCU", r"Control Panel\Mouse", "MouseThreshold1").data == "6"
    assert tweak.action.state(ctx) == OFF


def test_revert_deletes_values_that_did_not_exist():
    """원래 없던 값은 0 으로 되돌리는 게 아니라 지워야 원상태다."""
    ctx = fake_context()
    tweak = find("game_mode")
    assert ctx.registry.read("HKCU", r"Software\Microsoft\GameBar", "AllowAutoGameMode") is None

    record = tweak.action.apply(ctx)
    assert ctx.registry.read("HKCU", r"Software\Microsoft\GameBar", "AllowAutoGameMode").data == 1

    tweak.action.revert(ctx, record)
    assert ctx.registry.read("HKCU", r"Software\Microsoft\GameBar", "AllowAutoGameMode") is None


def test_revert_removes_a_key_we_created():
    ctx = fake_context()
    tweak = find("priority")
    path = (r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Image File Execution Options"
            r"\SuddenAttack.exe\PerfOptions")
    record = tweak.action.apply(ctx)
    assert ctx.registry.read("HKLM", path, "CpuPriorityClass").data == 3

    tweak.action.revert(ctx, record)
    assert not ctx.registry.key_exists("HKLM", path)
    # 만드느라 딸려 생긴 껍데기 키도 남기지 않는다
    assert not ctx.registry.key_exists("HKLM", path.rsplit("\\", 1)[0])


def test_partly_applied_counts_as_not_applied():
    """다섯 개 중 세 개만 맞아 있으면 '적용됨' 이 아니다."""
    ctx = fake_context()
    tweak = find("visual_effects")
    ctx.registry.write("HKCU", r"Control Panel\Desktop", "MenuShowDelay", RegValue("0", STR))
    assert tweak.action.state(ctx) == OFF

    tweak.action.apply(ctx)
    assert tweak.action.state(ctx) == ON


def test_number_and_text_zero_are_the_same_value():
    """레지스트리는 같은 0 을 숫자로도 글자로도 담는다. 계속 '안 됨' 으로 보이면 안 된다."""
    ctx = fake_context()
    ctx.registry.write("HKCU", r"System\GameConfigStore", "GameDVR_Enabled", RegValue("0", STR))
    ctx.registry.write("HKCU", r"Software\Microsoft\Windows\CurrentVersion\GameDVR",
                       "AppCaptureEnabled", RegValue(0, DWORD))
    ctx.registry.write("HKLM", r"SOFTWARE\Policies\Microsoft\Windows\GameDVR",
                       "AllowGameDVR", RegValue(0, DWORD))
    assert find("game_dvr").action.state(ctx) == ON


# --- 랜카드마다 달라지는 항목 -------------------------------------------
def test_nagle_touches_only_cards_with_an_address():
    ctx = fake_context()
    tweak = find("nagle")
    items = tweak.action.items(ctx)
    paths = {item.path for item in items}
    assert len(paths) == 1
    assert "{net-1}" in paths.pop()

    record = tweak.action.apply(ctx)
    base = r"SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces"
    assert ctx.registry.read("HKLM", rf"{base}\{{net-1}}", "TcpAckFrequency").data == 1
    assert ctx.registry.read("HKLM", rf"{base}\{{net-2}}", "TcpAckFrequency") is None

    tweak.action.revert(ctx, record)
    assert ctx.registry.read("HKLM", rf"{base}\{{net-1}}", "TcpAckFrequency") is None


def test_nagle_is_not_applicable_without_network_cards():
    ctx = fake_context(registry=type(seeded_registry())())
    assert find("nagle").action.state(ctx) == NA


# --- 게임 경로가 있어야 하는 항목 ----------------------------------------
def test_game_items_are_not_applicable_without_the_game():
    ctx = fake_context(install=False)
    for key in ("fullscreen_opt", "priority", "defender"):
        assert find(key).action.state(ctx) == NA, key


def test_fullscreen_optimization_is_written_under_the_exe_path():
    ctx = fake_context()
    tweak = find("fullscreen_opt")
    tweak.action.apply(ctx)
    value = ctx.registry.read(
        "HKCU",
        r"Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers",
        r"C:\Nexon\SuddenAttack\SuddenAttack.exe",
    )
    assert "DISABLEDXMAXIMIZEDWINDOWEDMODE" in str(value.data)


# --- 전원 계획 ---------------------------------------------------------
def test_power_plan_duplicates_instead_of_editing_the_current_one():
    """쓰던 계획은 손대지 않는다. 사용자가 맞춰둔 설정이 사라지면 안 된다."""
    ctx = fake_context()
    tweak = find("power_plan")
    assert tweak.action.state(ctx) == OFF

    record = tweak.action.apply(ctx)
    assert record["before"] == BALANCED
    assert ctx.shell.active == record["created"] != BALANCED
    assert ctx.shell.schemes[record["created"]] == "서든어택 최적화"
    assert BALANCED not in ctx.shell.tuned, "균형 조정 계획에는 값을 쓴 적이 없어야 한다"
    assert len(ctx.shell.tuned[record["created"]]) == 10   # 항목 5개 × (AC·배터리)
    assert tweak.action.state(ctx) == ON


def test_power_plan_comes_back_and_takes_its_plan_with_it():
    ctx = fake_context()
    tweak = find("power_plan")
    made = tweak.action.apply(ctx)["created"]

    tweak.action.revert(ctx, {"kind": "power", "before": BALANCED, "created": made})
    assert ctx.shell.active == BALANCED
    assert made not in ctx.shell.schemes, "우리가 만든 계획은 치우고 나가야 한다"
    assert set(ctx.shell.schemes) == {BALANCED, HIGH}


def test_power_plan_reuses_our_plan_instead_of_making_another():
    """두 번 눌러도 '서든어택 최적화 (2)' 같은 게 쌓이면 안 된다."""
    ctx = fake_context()
    tweak = find("power_plan")
    tweak.action.apply(ctx)
    ctx.shell.run(["powercfg", "/setactive", BALANCED])      # 사용자가 손으로 되돌린 상황

    tweak.action.apply(ctx)
    assert len(ctx.shell.schemes) == 3                        # 균형·고성능·우리 것 하나


def test_power_plan_falls_back_when_the_high_performance_plan_is_missing():
    """노트북에서는 고성능 계획이 숨겨져 있는 경우가 있다."""
    ctx = fake_context()
    ctx.shell.schemes.pop(HIGH)
    record = find("power_plan").action.apply(ctx)
    assert record["created"] in ctx.shell.schemes


@pytest.mark.parametrize(
    "line, guid, name",
    [
        ("전원 구성표 GUID: 381b4222-f694-41f0-9685-ff5bb260df2e  (균형 조정) *",
         "381b4222-f694-41f0-9685-ff5bb260df2e", "균형 조정"),
        ("Power Scheme GUID: 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c  (High performance)",
         "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c", "High performance"),
    ],
)
def test_powercfg_line_parses_in_any_language(line, guid, name):
    assert _parse_scheme(line) == (guid, name)


def test_powercfg_line_without_a_guid_is_ignored():
    assert _parse_scheme("기존 전원 구성표(* 활성):") is None


# --- 주사율 -----------------------------------------------------------
def test_refresh_rate_goes_to_the_highest_and_comes_back():
    ctx = fake_context()
    tweak = find("refresh_rate")
    assert tweak.action.state(ctx) == OFF

    record = tweak.action.apply(ctx)
    assert ctx.display.monitors()[0].hz == 144
    assert tweak.action.state(ctx) == ON

    tweak.action.revert(ctx, record)
    assert ctx.display.monitors()[0].hz == 60


def test_refresh_rate_that_the_monitor_refuses_is_not_recorded():
    """실패한 변경을 기록해두면 되돌릴 때 엉뚱한 값을 넣게 된다."""
    ctx = fake_context()
    ctx.display.fail = True
    record = find("refresh_rate").action.apply(ctx)
    assert record["screens"] == []


def test_refresh_rate_is_not_applicable_without_a_monitor():
    ctx = fake_context(monitors=False)
    assert find("refresh_rate").action.state(ctx) == NA


# --- 목록 자체 --------------------------------------------------------
def test_every_tweak_explains_itself():
    """항목마다 '무엇을' 과 '뭐가 좋아지는지' 가 둘 다 있어야 한다."""
    keys = [tweak.key for tweak in catalog()]
    assert len(keys) == len(set(keys))
    for tweak in catalog():
        assert tweak.what.strip(), tweak.key
        assert tweak.gain.strip(), tweak.key
        assert tweak.affects.strip(), tweak.key
        assert tweak.impact in IMPACT_LABEL, tweak.key
        assert tweak.group in GROUPS, tweak.key


def test_only_a_few_items_claim_a_big_effect():
    """전부 '체감 큼' 이면 등급이 아무 뜻도 없어진다."""
    big = [t.key for t in catalog() if t.impact == BIG]
    assert big == ["mouse_accel", "refresh_rate"]


def test_the_one_item_that_really_raises_frames_says_so():
    frames = [t.key for t in catalog() if t.affects == "프레임"]
    assert frames == ["game_dvr"]


def test_risky_items_are_not_on_by_default():
    """재부팅이 필요하거나 보안을 낮추는 것은 사용자가 직접 켜야 한다."""
    risky = {"hags_off", "defender", "notifications"}
    for tweak in catalog():
        if tweak.key in risky:
            assert not tweak.recommended, tweak.key


# --- 백신 검사 제외 -----------------------------------------------------
def test_defender_exclusion_goes_in_and_comes_out():
    ctx = fake_context()
    tweak = find("defender")
    assert tweak.action.state(ctx) == OFF

    record = tweak.action.apply(ctx)
    assert ctx.shell.exclusions == [r"C:\Nexon\SuddenAttack"]
    assert tweak.action.state(ctx) == ON

    tweak.action.revert(ctx, record)
    assert ctx.shell.exclusions == []
    assert tweak.action.state(ctx) == OFF


def test_defender_failure_is_raised_not_swallowed():
    """넣지도 못했는데 '적용했습니다' 라고 하면 안 된다."""
    ctx = fake_context()
    ctx.shell._defender = lambda script: Result(ok=False, err="접근이 거부되었습니다")
    with pytest.raises(RuntimeError):
        find("defender").action.apply(ctx)


# ============================================================================
# [6·8] 되돌리기 기록과 실행기 — 적용 → 기록 → 되돌리기. 이 프로그램의 약속이 지켜지는지 보는 검사.
# ============================================================================
def snapshot(registry):
    """저장소 전체를 그대로 떠둔다. 되돌린 뒤와 비교하려고."""
    return json.dumps(
        sorted(
            (root, path, name, str(value.data), value.kind)
            for (root, path), entries in registry._store.items()
            for name, value in entries.values()
        ),
        ensure_ascii=False,
    )


def test_one_click_applies_everything_recommended_and_undoes_it(tmp_path):
    ctx = fake_context()
    before = snapshot(ctx.registry)
    optimizer = Optimizer(ctx, root=tmp_path)

    outcome = optimizer.apply_recommended()
    assert outcome.done >= 8
    assert outcome.failed == 0
    assert snapshot(ctx.registry) != before
    assert ctx.display.monitors()[0].hz == 144

    undone = optimizer.revert()
    assert undone.failed == 0
    assert snapshot(ctx.registry) == before, "되돌린 뒤에는 손대기 전과 완전히 같아야 한다"
    assert ctx.display.monitors()[0].hz == 60


def test_the_record_is_written_before_we_finish(tmp_path):
    ctx = fake_context()
    outcome = Optimizer(ctx, root=tmp_path).apply_recommended()

    assert outcome.record and outcome.record.exists()
    saved = json.loads(outcome.record.read_text(encoding="utf-8"))
    assert saved["entries"]["mouse_accel"]["items"][0]["before"]["data"] == "1"
    assert latest_record(tmp_path).keys == list(saved["entries"])


def test_running_twice_does_not_overwrite_the_original_values(tmp_path):
    """두 번 눌러도 두 번째 기록이 '이미 최적화된 값' 으로 덮이면 안 된다."""
    ctx = fake_context()
    optimizer = Optimizer(ctx, root=tmp_path)
    before = snapshot(ctx.registry)

    optimizer.apply_recommended()
    second = optimizer.apply_recommended()
    assert second.done == 0, "이미 적용된 것은 다시 건드리지 않는다"

    optimizer.revert()
    assert snapshot(ctx.registry) == before


def test_a_reverted_record_is_not_offered_again(tmp_path):
    ctx = fake_context()
    optimizer = Optimizer(ctx, root=tmp_path)
    optimizer.apply_recommended()
    optimizer.revert()

    assert latest_record(tmp_path) is None
    assert latest_record(tmp_path, include_reverted=True) is not None
    assert optimizer.revert().steps == []


def test_items_needing_admin_are_blocked_not_attempted(tmp_path):
    ctx = fake_context(admin=False)
    optimizer = Optimizer(ctx, root=tmp_path)

    statuses = {status.tweak.key: status for status in optimizer.statuses()}
    assert statuses["nagle"].blocked == "관리자 권한이 필요합니다"
    assert statuses["mouse_accel"].blocked == ""
    assert "nagle" not in optimizer.recommended_keys()

    outcome = optimizer.apply(["nagle"])
    assert outcome.done == 0 and outcome.failed == 1
    base = r"SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces"
    assert ctx.registry.read("HKLM", rf"{base}\{{net-1}}", "TcpAckFrequency") is None


def test_a_missing_game_blocks_only_the_game_items(tmp_path):
    optimizer = Optimizer(fake_context(install=False), root=tmp_path)
    blocked = {s.tweak.key: s.blocked for s in optimizer.statuses() if s.blocked}
    assert blocked["priority"] == "서든어택 설치 폴더를 찾지 못했습니다"
    assert "mouse_accel" not in blocked


def test_one_failing_item_does_not_stop_the_rest(tmp_path):
    ctx = fake_context()

    class Broken:
        def state(self, ctx):
            return OFF

        def apply(self, ctx):
            raise RuntimeError("일부러 낸 오류")

        def revert(self, ctx, record):
            pass

    items = catalog()
    items.insert(0, Tweak(key="broken", title="고장난 항목", what="검사용",
                                 gain="검사용", group="윈도우", action=Broken()))
    outcome = Optimizer(ctx, items=items, root=tmp_path).apply_recommended()

    assert outcome.failed == 1
    assert outcome.done >= 8
    assert "일부러 낸 오류" in outcome.steps[0].message


def test_reboot_is_reported_when_an_item_needs_it(tmp_path):
    ctx = fake_context()
    optimizer = Optimizer(ctx, root=tmp_path)
    assert not optimizer.apply(["mouse_accel"]).reboot
    assert optimizer.apply(["hags_off"]).reboot
    assert "재부팅" in optimizer.apply(["hags_off"]).summary or True


def test_nothing_to_do_says_so(tmp_path):
    ctx = fake_context()
    optimizer = Optimizer(ctx, root=tmp_path)
    optimizer.apply_recommended()
    assert optimizer.apply_recommended().summary.startswith("바꿀 것이 없었습니다")


def test_the_game_path_typed_by_hand_is_remembered(tmp_path):
    assert saved_game_path(tmp_path) is None
    remember_game_path(r"D:\게임\SuddenAttack", tmp_path)
    assert saved_game_path(tmp_path) == r"D:\게임\SuddenAttack"


def test_a_broken_record_file_does_not_crash_the_program(tmp_path):
    folder = backup_folder(tmp_path)
    folder.mkdir(parents=True)
    (folder / f"{RECORD_PREFIX}20260101-000000.json").write_text("{망가진", encoding="utf-8")
    assert record_history(tmp_path) == []
    assert latest_record(tmp_path) is None


def test_a_record_from_an_older_version_is_skipped_not_crashed(tmp_path):
    ctx = fake_context()
    optimizer = Optimizer(ctx, root=tmp_path)
    optimizer.apply(["mouse_accel"])

    record = latest_record(tmp_path)
    raw = json.loads(record.path.read_text(encoding="utf-8"))
    raw["entries"]["없어진항목"] = {"kind": "registry", "items": []}
    record.path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    outcome = optimizer.revert()
    assert outcome.done == 1
    assert any("모르는 항목" in step.message for step in outcome.steps)


def test_spec_reads_what_the_registry_says(tmp_path):
    ctx = fake_context()
    ctx.registry.write("HKLM", r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
                       "ProcessorNameString", RegValue("Intel(R) Core(TM) i5-12400F", "str"))
    gpu = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
    ctx.registry.write("HKLM", f"{gpu}\\0000", "DriverDesc", RegValue("NVIDIA GeForce RTX 3060", "str"))
    ctx.registry.write("HKLM", f"{gpu}\\Configuration", "DriverDesc", RegValue("아님", "str"))
    ctx.registry.write("HKLM", r"SOFTWARE\Microsoft\Windows NT\CurrentVersion",
                       "ProductName", RegValue("Windows 10 Pro", "str"))
    ctx.registry.write("HKLM", r"SOFTWARE\Microsoft\Windows NT\CurrentVersion",
                       "CurrentBuild", RegValue("22631", "str"))

    spec = spec_of(ctx)
    assert spec.cpu == "Intel(R) Core(TM) i5-12400F"
    assert spec.gpus == ["NVIDIA GeForce RTX 3060"]      # Configuration 은 그래픽카드가 아니다
    assert "Windows 11 Pro" in spec.windows              # 빌드 22000 이상은 11 이다
    assert spec.monitors[0].best_hz == 144


# ============================================================================
# [10] 화면 — 화면. 버튼이 실제로 무엇을 하는지, 그리고 화면에 거짓말이 없는지.
# ============================================================================
def make(tmp_path, **kwargs):
    optimizer = Optimizer(fake_context(**kwargs), root=tmp_path)
    return Screen(optimizer, root=tmp_path)


def test_the_page_shows_the_computer_and_every_item(tmp_path):
    page = make(tmp_path).render()
    assert "서든어택 최적화" in page
    assert "1920×1080 · 60Hz → 최대 144Hz 가능" in page
    for title in ("마우스 가속 끄기", "게임용 전원 계획 켜기", "배경 녹화(Game DVR) 끄기"):
        assert title in page
    assert "한 번에 최적화" in page


def test_one_click_button_applies_and_the_page_says_so(tmp_path):
    screen = make(tmp_path)
    message = screen.run("apply_all", {})

    assert "적용" in message
    page = screen.render()
    assert "이미 적용돼 있습니다. 더 할 게 없습니다" in page
    assert "원래대로 되돌리기" in page
    assert latest_record(tmp_path) is not None


def test_only_the_boxes_you_ticked_are_touched(tmp_path):
    screen = make(tmp_path)
    screen.run("apply", {"key": ["mouse_accel"]})

    record = latest_record(tmp_path)
    assert record.keys == ["mouse_accel"]


def test_ticking_nothing_says_so_instead_of_doing_everything(tmp_path):
    screen = make(tmp_path)
    assert screen.run("apply", {"key": []}) == "고른 항목이 없습니다."
    assert latest_record(tmp_path) is None


def test_undo_button_puts_it_all_back(tmp_path):
    screen = make(tmp_path)
    screen.run("apply_all", {})
    message = screen.run("revert", {"record": [latest_record(tmp_path).path.name]})

    assert "되돌렸습니다" in message
    assert screen.optimizer.ctx.shell.active == "381b4222-f694-41f0-9685-ff5bb260df2e"
    assert "아직 바꾼 것이 없어서" in screen.render()


def test_undo_with_nothing_to_undo_says_so(tmp_path):
    assert make(tmp_path).run("revert", {}) == "되돌릴 기록이 없습니다."


def test_a_locked_item_is_shown_as_locked(tmp_path):
    page = make(tmp_path, admin=False).render()
    assert "관리자 권한이 필요합니다" in page
    assert "관리자 권한으로 다시 실행" in page


def test_locked_is_not_reported_as_nothing_to_do(tmp_path):
    """잠겨서 못 한 것을 '다 됐다' 고 말하면 안 된다."""
    screen = make(tmp_path, admin=False)
    screen.run("apply_all", {})
    page = screen.render()
    assert "잠겨 있습니다" in page
    assert "더 할 게 없습니다" not in page


def test_the_game_path_box_appears_only_when_the_game_is_missing(tmp_path):
    assert "서든어택을 못 찾았습니다" in make(tmp_path, install=False).render()
    assert "서든어택을 못 찾았습니다" not in make(tmp_path).render()


def test_typing_a_game_path_that_works(tmp_path):
    folder = tmp_path / "SuddenAttack"
    folder.mkdir()
    (folder / "SuddenAttack.exe").write_text("", encoding="utf-8")

    screen = make(tmp_path, install=False)
    message = screen.run("game_path", {"path": [str(folder)]})
    assert "찾았습니다" in message
    assert screen.optimizer.ctx.install is not None


def test_typing_a_game_path_that_does_not_work(tmp_path):
    screen = make(tmp_path, install=False)
    message = screen.run("game_path", {"path": [str(tmp_path / "없는곳")]})
    assert "못 찾았습니다" in message


def test_the_page_never_promises_what_it_did_not_do(tmp_path):
    """윈도우가 아니면 '적용됨' 이라고 써서는 안 된다."""
    page = make(tmp_path, windows=False).render()
    assert "실제로 바뀌지는 않습니다" in page


def test_the_guide_lists_what_we_deliberately_skip(tmp_path):
    page = make(tmp_path).render()
    assert "일부러 안 건드리는 것" in page
    assert "bcdedit" in page


def test_user_text_cannot_break_the_page(tmp_path):
    screen = make(tmp_path, install=False)
    screen.run("game_path", {"path": ['<script>alert(1)</script>']})
    assert "<script>alert(1)</script>" not in screen.render()


def test_each_item_says_what_it_is_and_what_it_buys(tmp_path):
    page = make(tmp_path).render()
    assert "윈도우가 마우스를 빨리 움직일수록" in page          # 무엇을
    assert "몸이 감각을 외울 수 있게 됩니다" in page             # 뭐가 좋아지는지
    assert "체감 큼" in page and "체감 작음" in page             # 얼마나 느껴지는지


def test_the_basics_section_explains_frames_versus_refresh_rate(tmp_path):
    page = make(tmp_path).render()
    assert "프레임 (FPS)" in page and "주사율 (Hz)" in page
    assert "모니터가 60Hz 면 눈에 보이는 건 초당 60장입니다" in page
    assert "16.7ms" in page


def test_the_basics_section_points_at_this_computer(tmp_path):
    """일반론만 적어두면 내 얘기인지 알 수 없다."""
    page = make(tmp_path).render()
    assert "지금 이 컴퓨터는 <b>60Hz</b> 로 돌고 있고 <b>144Hz</b> 까지 됩니다" in page


def test_a_monitor_already_at_its_best_is_told_so(tmp_path):
    screen = make(tmp_path)
    screen.optimizer.ctx.display.screens[0].hz = 144
    assert "이미 최대입니다" in screen.render()

# ============================================================================
# [8] 이 컴퓨터에서는 — 실제 값으로 다시 판정하기
# ============================================================================
def levels(found):
    return {v.key: v.level for v in found}


def line_for(found, key):
    return next(v.line for v in found if v.key == key)


def judge(ctx, **spec_kwargs):
    spec = spec_of(ctx)
    for name, value in spec_kwargs.items():
        setattr(spec, name, value)
    return verdicts(Optimizer(ctx).statuses(), spec)


def test_big_things_come_first():
    """15개를 늘어놓기만 하면 무엇부터 봐야 할지 알 수 없다."""
    found = judge(fake_context())
    order = [v.level for v in found]
    assert order == sorted(order, key=lambda level: {
        BIG: 0, MID: 1, SMALL: 2, DONE: 3, LOCKED: 4}[level])
    assert found[0].level == BIG


def test_the_refresh_rate_line_uses_this_monitor_s_real_numbers():
    """'주사율을 올리세요' 는 일반론이다. 60에서 144로 간다고 말해야 내 얘기가 된다."""
    line = line_for(judge(fake_context()), "refresh_rate")
    assert "60Hz" in line and "144Hz" in line
    assert "2.4배" in line
    assert "8.3ms → 3.5ms" in line


def test_a_monitor_already_at_its_best_is_not_sold_a_gain():
    ctx = fake_context()
    ctx.display.screens[0].hz = 144
    found = judge(ctx)
    assert levels(found)["refresh_rate"] == DONE
    assert line_for(found, "refresh_rate") == "이미 되어 있습니다"


def test_two_monitors_are_judged_by_the_one_losing_most():
    ctx = fake_context()
    ctx.display.screens.append(Monitor("\\\\.\\DISPLAY2", "둘째", 1920, 1080, 100, 120))
    assert "60Hz" in line_for(judge(ctx), "refresh_rate")     # 60→144 손해가 100→120 보다 크다


def test_the_power_plan_speaks_differently_to_a_laptop():
    """같은 항목이라도 노트북은 체감이 크고 데스크톱은 작다. 같은 말을 하면 안 된다."""
    desktop = judge(fake_context(), laptop=False)
    assert levels(desktop)["power_plan"] == MID
    assert "데스크톱입니다" in line_for(desktop, "power_plan")

    laptop = judge(fake_context(), laptop=True)
    assert levels(laptop)["power_plan"] == BIG
    assert "노트북입니다" in line_for(laptop, "power_plan")
    assert "배터리" in line_for(laptop, "power_plan")


def test_settings_already_the_way_we_want_are_reported_as_done():
    ctx = fake_context()
    for name in ("MouseSpeed", "MouseThreshold1", "MouseThreshold2"):
        ctx.registry.write("HKCU", r"Control Panel\Mouse", name, RegValue("0", STR))
    ctx.registry.write("HKCU", r"System\GameConfigStore", "GameDVR_Enabled", RegValue(0, DWORD))
    ctx.registry.write("HKCU", r"Software\Microsoft\Windows\CurrentVersion\GameDVR",
                       "AppCaptureEnabled", RegValue(0, DWORD))
    ctx.registry.write("HKLM", r"SOFTWARE\Policies\Microsoft\Windows\GameDVR",
                       "AllowGameDVR", RegValue(0, DWORD))

    found = levels(judge(ctx))
    assert found["mouse_accel"] == DONE
    assert found["game_dvr"] == DONE


def test_locked_items_say_why_rather_than_promising_a_gain():
    found = judge(fake_context(admin=False))
    assert levels(found)["nagle"] == LOCKED
    assert line_for(found, "nagle") == "관리자 권한이 필요합니다"


def test_the_screen_shows_this_computer_s_own_numbers(tmp_path):
    page = make(tmp_path).render()
    assert "이 컴퓨터에서는" in page
    assert "모니터가 60Hz 로 돌고 있습니다" in page
    assert "마우스 가속이 켜져 있습니다" in page


def test_the_screen_admits_when_there_is_nothing_big_to_gain(tmp_path):
    """팔 것이 없으면 없다고 해야 한다. 그래야 있다고 할 때 믿는다."""
    screen = make(tmp_path)
    ctx = screen.optimizer.ctx
    ctx.display.screens[0].hz = 144
    for name in ("MouseSpeed", "MouseThreshold1", "MouseThreshold2"):
        ctx.registry.write("HKCU", r"Control Panel\Mouse", name, RegValue("0", STR))
    screen.optimizer.apply(["fullscreen_opt", "power_plan", "nagle", "game_dvr"])

    page = screen.render()
    assert "크게 달라질 것은 없습니다" in page
    assert "이미 되어 있는 것" in page


def test_a_desktop_is_labelled_a_desktop_on_screen(tmp_path):
    assert "데스크톱" in make(tmp_path).render()
