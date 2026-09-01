"""서든어택 설치 위치 찾기."""

from sudden_attack import game
from sudden_attack.winreg_io import STR, FakeRegistry, RegValue


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

    found = game.find(registry=registry, roots=[])
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

    assert game.find(registry=registry, roots=[]) is None


def test_found_by_scanning_a_drive(tmp_path):
    make_install(tmp_path)
    found = game.find(registry=FakeRegistry(), roots=[str(tmp_path)])
    assert found is not None and found.exe.name == "SuddenAttack.exe"


def test_a_path_typed_by_hand_wins(tmp_path):
    folder = make_install(tmp_path, folder="어딘가/서든")
    found = game.find(registry=FakeRegistry(), roots=[], saved=str(folder))
    assert found is not None
    assert found.source == "직접 넣은 경로"


def test_the_exe_itself_can_be_given(tmp_path):
    folder = make_install(tmp_path)
    found = game.find(registry=FakeRegistry(), roots=[], saved=str(folder / "SuddenAttack.exe"))
    assert found is not None and found.folder == folder


def test_a_wrong_path_gives_nothing_rather_than_a_guess(tmp_path):
    assert game.find(registry=FakeRegistry(), roots=[], saved=str(tmp_path / "없는곳")) is None


def test_nothing_found_is_not_an_error(tmp_path):
    assert game.find(registry=FakeRegistry(), roots=[str(tmp_path)]) is None
