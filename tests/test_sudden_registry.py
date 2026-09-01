"""레지스트리 감싸개 — 되돌리기의 근거가 되는 부분이라 꼼꼼히 본다."""

from sudden_attack.winreg_io import BINARY, DWORD, STR, FakeRegistry, RegValue


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
