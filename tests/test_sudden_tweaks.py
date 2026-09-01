"""항목 하나하나가 제대로 읽고, 쓰고, 되돌리는가."""

import pytest
from sudden_fakes import BALANCED, HIGH, fake_context, seeded_registry

from sudden_attack import tweaks
from sudden_attack.tweaks import NA, OFF, ON, _parse_scheme, by_key
from sudden_attack.winreg_io import DWORD, STR, RegValue


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
    keys = [tweak.key for tweak in tweaks.catalog()]
    assert len(keys) == len(set(keys))
    for tweak in tweaks.catalog():
        assert tweak.what.strip(), tweak.key
        assert tweak.gain.strip(), tweak.key
        assert tweak.affects.strip(), tweak.key
        assert tweak.impact in tweaks.IMPACT_LABEL, tweak.key
        assert tweak.group in tweaks.GROUPS, tweak.key


def test_only_a_few_items_claim_a_big_effect():
    """전부 '체감 큼' 이면 등급이 아무 뜻도 없어진다."""
    big = [t.key for t in tweaks.catalog() if t.impact == tweaks.BIG]
    assert big == ["mouse_accel", "refresh_rate"]


def test_the_one_item_that_really_raises_frames_says_so():
    frames = [t.key for t in tweaks.catalog() if t.affects == "프레임"]
    assert frames == ["game_dvr"]


def test_risky_items_are_not_on_by_default():
    """재부팅이 필요하거나 보안을 낮추는 것은 사용자가 직접 켜야 한다."""
    risky = {"hags_off", "defender", "notifications"}
    for tweak in tweaks.catalog():
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
    from sudden_attack.shell import Result

    ctx = fake_context()
    ctx.shell._defender = lambda script: Result(ok=False, err="접근이 거부되었습니다")
    with pytest.raises(RuntimeError):
        find("defender").action.apply(ctx)
