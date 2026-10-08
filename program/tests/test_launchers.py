"""다른 컴퓨터에서도 더블클릭 한 번으로 켜지는가.

다른 컴퓨터에서 안 켜지는 이유는 거의 셋이다.
  1) 파이썬이 없다 → 내 계정에만, 관리자 권한 없이 자동 설치한다
  2) 'python' 이 마이크로소프트 스토어 안내용 가짜다 → 실제로 실행해 보고 고른다
  3) 폴더째 복사해 온 .venv 가 원래 컴퓨터의 파이썬을 가리킨다 → 알아보고 새로 만든다
윈도우 배치 파일은 ASCII + CRLF 가 아니면 cmd.exe 가 엉뚱하게 읽고 창이 바로 닫힌다.
"""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

PROGRAM = Path(__file__).resolve().parent.parent
OUTSIDE = PROGRAM.parent
LAUNCHERS = [OUTSIDE / "시작하기.bat", OUTSIDE / "업데이트.bat", OUTSIDE / "끄기.bat"]
FINDER = PROGRAM / "tools" / "python.cmd"
INSTALLER = PROGRAM / "tools" / "install-python.ps1"


def _crlf_only(data: bytes) -> bool:
    return b"\n" not in data.replace(b"\r\n", b"")


@pytest.mark.parametrize("path", LAUNCHERS + [FINDER], ids=lambda p: p.name)
def test_batch_files_are_ascii_with_crlf(path):
    data = path.read_bytes()
    data.decode("ascii")
    assert _crlf_only(data)


def test_the_installer_script_has_a_bom_so_windows_powershell_reads_korean():
    data = INSTALLER.read_bytes()
    assert data.startswith(b"\xef\xbb\xbf")
    assert _crlf_only(data)


@pytest.mark.parametrize("path", LAUNCHERS, ids=lambda p: p.name)
def test_every_launcher_finds_python_through_the_shared_helper(path):
    text = path.read_text(encoding="ascii")
    assert 'call "tools\\python.cmd"' in text
    assert "%FS_PY%" in text


def test_stopping_never_installs_python():
    assert 'call "tools\\python.cmd" noinstall' in (OUTSIDE / "끄기.bat").read_text(encoding="ascii")


def test_python_is_tried_for_real_not_just_looked_up():
    """'where python' 은 스토어 안내용 가짜도 찾는다. 실행해 봐야 가려진다."""
    text = FINDER.read_text(encoding="ascii")
    assert "sys.version_info >= (3, 9)" in text
    assert "where python" not in text


def test_the_install_is_per_user_without_admin_and_signature_checked():
    text = INSTALLER.read_text(encoding="utf-8-sig")
    for option in ("InstallAllUsers=0", "PrependPath=0", "/quiet"):
        assert option in text
    assert "https://www.python.org/ftp/python/" in text
    assert "Get-AuthenticodeSignature" in text and "Python Software Foundation" in text


# --- 복사해 온 .venv ---------------------------------------------------------
@pytest.fixture
def boot(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("boot_launch", PROGRAM / "bootstrap.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "VENV", tmp_path / ".venv")
    return module


def test_a_venv_copied_from_another_computer_is_rebuilt(boot, monkeypatch):
    broken = boot.venv_python()
    broken.parent.mkdir(parents=True)
    broken.write_text("원래 컴퓨터의 파이썬을 가리키던 파일\n", encoding="utf-8")   # 실행이 안 된다

    made = []

    def fake_run(cmd, **kwargs):
        if cmd[1:3] == ["-m", "venv"]:
            made.append(cmd)
            boot.venv_python().parent.mkdir(parents=True, exist_ok=True)
            boot.venv_python().write_text("새 파이썬", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return subprocess.CompletedProcess(cmd, 1, "", "No Python at 'C:\\Users\\other\\...'")

    monkeypatch.setattr(boot.subprocess, "run", fake_run)
    assert boot.ensure_venv()
    assert made, "새 준비 공간을 만들어야 한다"
    assert boot.venv_python().read_text(encoding="utf-8") == "새 파이썬"


def test_a_working_venv_is_left_alone(boot, monkeypatch):
    boot.venv_python().parent.mkdir(parents=True)
    boot.venv_python().write_text("", encoding="utf-8")
    calls = []
    monkeypatch.setattr(boot.subprocess, "run",
                        lambda cmd, **kw: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", ""))
    assert boot.ensure_venv()
    assert all(c[1:3] != ["-m", "venv"] for c in calls)


@pytest.mark.skipif(sys.platform == "win32", reason="윈도우는 심볼릭 링크에 권한이 필요하다")
def test_venv_check_really_runs_python(boot):
    """실제 파이썬으로도 '돌아간다' 고 판정되는지 — 가짜 판정이 아닌지 확인."""
    boot.VENV.mkdir(parents=True)
    target = boot.venv_python()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.symlink_to(sys.executable)
    assert boot.venv_works()
