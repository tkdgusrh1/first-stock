"""창 없이 띄우는 준비 스크립트.

여기서 틀리면 증상이 '아무 일도 안 일어남' 이라 원인을 찾기가 어렵다.
  · 포트를 잘못 읽으면 화면이 떴는데도 못 찾고 실패라고 말한다
  · 로그가 무한정 커지면 디스크를 먹는다
bootstrap 은 표준 라이브러리만 쓰기 때문에 여기서도 그렇게 검사한다.
"""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def boot(tmp_path, monkeypatch):
    """bootstrap 을 임시 폴더에서 도는 것처럼 불러온다."""
    spec = importlib.util.spec_from_file_location("boot_under_test", ROOT / "bootstrap.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "LOG_PATH", tmp_path / "logs" / "실행기록.log")
    monkeypatch.setattr(module, "PID_PATH", tmp_path / "logs" / "실행중.pid")
    return module


# --- 포트 찾기 --------------------------------------------------------------
def test_the_port_comes_from_the_config(boot, tmp_path):
    (tmp_path / "config.yml").write_text(
        'user_agent: "A b@c.com"\ndashboard:\n  enabled: true\n  port: 9123\n',
        encoding="utf-8",
    )
    assert boot.dashboard_port() == 9123


def test_a_missing_config_falls_back_to_the_usual_port(boot):
    assert boot.dashboard_port() == boot.DEFAULT_PORT


def test_a_config_without_a_port_falls_back(boot, tmp_path):
    (tmp_path / "config.yml").write_text('user_agent: "A b@c.com"\n', encoding="utf-8")
    assert boot.dashboard_port() == boot.DEFAULT_PORT


# --- 로그 -------------------------------------------------------------------
def test_the_log_file_is_created_where_expected(boot):
    with boot.open_log() as fh:
        fh.write("한 줄\n")
    assert "한 줄" in boot.LOG_PATH.read_text(encoding="utf-8")


def test_a_huge_log_is_rolled_over_instead_of_growing(boot, monkeypatch):
    monkeypatch.setattr(boot, "MAX_LOG_BYTES", 100)
    boot.LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    boot.LOG_PATH.write_text("오래된 기록\n" * 50, encoding="utf-8")
    assert boot.LOG_PATH.stat().st_size > boot.MAX_LOG_BYTES

    with boot.open_log() as fh:
        fh.write("새 기록\n")

    assert boot.LOG_PATH.read_text(encoding="utf-8") == "새 기록\n"
    assert boot.LOG_PATH.with_suffix(".이전.log").exists()      # 지난 기록도 남는다


# --- 멈추기 -----------------------------------------------------------------
def test_stopping_when_nothing_runs_says_so_instead_of_crashing(boot, monkeypatch, capsys):
    monkeypatch.setattr(boot, "pause", lambda: None)
    monkeypatch.setattr(boot, "running_url", lambda: "")
    monkeypatch.setattr(boot, "running_pids", lambda: [])

    assert boot.stop_running() == 0
    assert "돌고 있는 감시가 없습니다" in capsys.readouterr().out


def test_it_finds_the_process_even_without_the_marker_file(boot, monkeypatch, capsys):
    """번호를 적어둔 파일이 없어도 끌 수 있어야 한다.

    못 끄면 폴더를 지우지도 옮기지도 못한다. 한 가지 방법만 믿으면 안 된다.
    """
    monkeypatch.setattr(boot, "pause", lambda: None)
    monkeypatch.setattr(boot, "running_url", lambda: "")
    alive = [4242]
    monkeypatch.setattr(boot, "running_pids", lambda: list(alive))
    monkeypatch.setattr(boot, "kill", lambda pid: alive.remove(pid))

    assert boot.stop_running() == 0            # PID 파일이 없는 상태
    assert alive == []
    assert "멈췄습니다" in capsys.readouterr().out


def test_it_says_so_when_something_survives(boot, monkeypatch, capsys):
    """끝내지 못했으면 멈췄다고 말하면 안 된다. 사용자는 폴더를 지우려 한다."""
    monkeypatch.setattr(boot, "pause", lambda: None)
    monkeypatch.setattr(boot, "running_url", lambda: "")
    monkeypatch.setattr(boot, "running_pids", lambda: [4242])
    monkeypatch.setattr(boot, "kill", lambda pid: None)          # 안 죽는다
    monkeypatch.setattr(boot, "ask_dashboard_to_quit", lambda: False)

    assert boot.stop_running() == 1
    out = capsys.readouterr().out
    assert "아직 멈추지 않은" in out and "4242" in out
    assert "작업 관리자" in out and "다시 켜기" in out


def test_it_really_finds_a_running_process(boot, tmp_path):
    """프로세스 찾기가 실제로 되는지. 여기가 틀리면 끄기가 통째로 헛돈다."""
    import subprocess
    import sys
    import time as _t

    (tmp_path / "main.py").write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    child = subprocess.Popen([sys.executable, str(tmp_path / "main.py")])
    try:
        _t.sleep(0.5)
        assert child.pid in boot.running_pids()
    finally:
        child.kill()
        child.wait()
    _t.sleep(0.3)
    assert child.pid not in boot.running_pids()


def test_it_does_not_grab_anything_that_merely_mentions_the_folder(boot, tmp_path):
    """경로가 적혀 있다는 이유로 남의 프로세스를 끄면 안 된다.

    실제로 사고가 났다. 폴더 경로와 'main.py' 라는 글자가 들어 있다는 이유로
    명령을 실행 중이던 셸까지 끄기 대상으로 잡았다.
    """
    import subprocess
    import sys
    import time as _t

    (tmp_path / "main.py").write_text("x = 1\n", encoding="utf-8")
    # 폴더 경로와 main.py 를 말만 하고 있는 프로세스 (파이썬이지만 다른 일을 한다)
    talker = subprocess.Popen(
        [sys.executable, "-c", f"import time; _ = {str(tmp_path / 'main.py')!r}; time.sleep(20)"]
    )
    try:
        _t.sleep(0.5)
        assert talker.pid not in boot.running_pids()
    finally:
        talker.kill()
        talker.wait()


def test_it_never_targets_itself(boot):
    """자기 자신을 끄면 끄기가 도중에 죽는다."""
    import os as _os

    assert _os.getpid() not in boot.running_pids()
    assert _os.getppid() not in boot.running_pids()


def test_stopping_clears_the_marker(boot, monkeypatch, capsys):
    monkeypatch.setattr(boot, "pause", lambda: None)
    monkeypatch.setattr(boot.os, "kill", lambda pid, sig: None)
    boot.PID_PATH.parent.mkdir(parents=True, exist_ok=True)
    boot.PID_PATH.write_text("4242", encoding="utf-8")

    assert boot.stop_running() == 0
    assert not boot.PID_PATH.exists()
    assert "멈췄습니다" in capsys.readouterr().out
