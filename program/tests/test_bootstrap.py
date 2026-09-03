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


# --- 예전 구조에서 넘어오기 -------------------------------------------------
#
# 코드와 설정이 바깥에 흩어져 있던 시절에서 program 폴더 안으로 옮겼다.
# 여기서 틀리면 **쓰던 설정이 사라진 것처럼 보인다** — 처음 실행인 줄 알고
# 이름·이메일·종목을 다시 물어보고, 감시 기록도 0 부터 다시 쌓는다.


@pytest.fixture
def moved(tmp_path, monkeypatch):
    """program 폴더 구조를 흉내낸다. (바깥 폴더 / program)"""
    spec = importlib.util.spec_from_file_location("boot_moved", ROOT / "bootstrap.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    inside = tmp_path / "program"
    inside.mkdir()
    (inside / "main.py").write_text("x = 1\n", encoding="utf-8")

    monkeypatch.setattr(module, "ROOT", inside)
    monkeypatch.setattr(module, "LOG_PATH", inside / "logs" / "실행기록.log")
    monkeypatch.setattr(module, "PID_PATH", inside / "logs" / "실행중.pid")
    monkeypatch.setattr(module, "running_pids", lambda: [])
    monkeypatch.setattr(module, "running_url", lambda: "")
    monkeypatch.setattr(module.time, "sleep", lambda *_: None)
    return module


def _old_layout(outside):
    """업데이트로 새 구조를 받았지만 바깥에 예전 파일이 남아 있는 상태."""
    (outside / "main.py").write_text("옛날 코드\n", encoding="utf-8")
    (outside / "bootstrap.py").write_text("옛날 코드\n", encoding="utf-8")
    (outside / "config.yml").write_text('user_agent: "A b@c.com"\n', encoding="utf-8")
    (outside / "state.json").write_text('{"seen": 12}', encoding="utf-8")
    (outside / "stock_analysis").mkdir()
    (outside / "stock_analysis" / "app.py").write_text("옛날 코드\n", encoding="utf-8")
    (outside / "logs").mkdir()
    (outside / "logs" / "실행기록.log").write_text("지난 기록\n", encoding="utf-8")


def test_settings_and_history_come_along(moved, tmp_path):
    """쓰던 설정·기록이 새 폴더로 따라와야 한다. 이게 안 되면 처음부터 다시다."""
    _old_layout(tmp_path)

    assert moved.migrate() is True

    inside = tmp_path / "program"
    assert (inside / "config.yml").read_text(encoding="utf-8").startswith("user_agent")
    assert (inside / "state.json").read_text(encoding="utf-8") == '{"seen": 12}'
    assert (inside / "logs" / "실행기록.log").exists()
    # 바깥에는 더 이상 남아 있지 않다
    assert not (tmp_path / "config.yml").exists()
    assert not (tmp_path / "state.json").exists()


def test_the_old_code_is_cleared_away(moved, tmp_path):
    """예전 코드를 남겨두면 그게 계속 돌면서 '업데이트했는데 그대로' 가 된다."""
    _old_layout(tmp_path)

    moved.migrate()

    assert not (tmp_path / "main.py").exists()
    assert not (tmp_path / "bootstrap.py").exists()
    assert not (tmp_path / "stock_analysis").exists()


def test_a_users_own_file_is_never_thrown_away(moved, tmp_path):
    """모르는 파일은 건드리지 않는다. 사용자가 넣어둔 것일 수 있다."""
    _old_layout(tmp_path)
    (tmp_path / "내메모.txt").write_text("사둔 이유\n", encoding="utf-8")

    moved.migrate()

    assert (tmp_path / "내메모.txt").exists()


def test_it_does_not_overwrite_what_is_already_there(moved, tmp_path):
    """새 폴더에 이미 설정이 있으면 그게 지금 쓰는 것이다. 덮어쓰지 않는다."""
    _old_layout(tmp_path)
    inside = tmp_path / "program"
    (inside / "config.yml").write_text('user_agent: "지금 쓰는 것 x@y.com"\n', encoding="utf-8")

    moved.migrate()

    assert "지금 쓰는 것" in (inside / "config.yml").read_text(encoding="utf-8")
    # 예전 것도 버리지 않고 백업에 둔다
    assert (inside / "이전버전" / "config.yml").exists()


def test_nothing_happens_when_there_is_nothing_to_move(moved, tmp_path):
    assert moved.migrate() is False


def test_a_watcher_left_over_from_the_old_layout_is_found(moved, tmp_path, monkeypatch):
    """바깥 main.py 로 시작된 옛 프로세스도 찾아야 끄기·업데이트가 통한다."""
    import subprocess
    import sys
    import time as _t

    monkeypatch.undo()          # running_pids 를 진짜로 돌린다
    inside = tmp_path / "program"
    monkeypatch.setattr(moved, "ROOT", inside)
    old_main = tmp_path / "main.py"
    old_main.write_text("import time; time.sleep(20)\n", encoding="utf-8")

    child = subprocess.Popen([sys.executable, str(old_main)])
    try:
        _t.sleep(0.5)
        assert child.pid in moved.running_pids()
    finally:
        child.kill()
        child.wait()


def test_a_git_checkout_keeps_its_own_files(moved, tmp_path):
    """git 으로 받아 쓰는 폴더에서는 개발용 파일을 지우면 안 된다."""
    _old_layout(tmp_path)
    (tmp_path / ".git").mkdir()
    (tmp_path / ".gitignore").write_text("config.yml\n", encoding="utf-8")

    moved.migrate()

    assert (tmp_path / ".gitignore").exists()


def test_a_plain_download_does_not_keep_developer_files(moved, tmp_path):
    """받아 쓰기만 하는 사람에게 .github 같은 건 눈에만 걸린다."""
    _old_layout(tmp_path)
    (tmp_path / ".gitignore").write_text("config.yml\n", encoding="utf-8")
    (tmp_path / ".github").mkdir()

    moved.migrate()

    assert not (tmp_path / ".gitignore").exists()
    assert not (tmp_path / ".github").exists()


# --- 다른 폴더에서 돌고 있는 것 ---------------------------------------------
#
# 폴더를 옮기거나 새로 받아 쓰면 옛 폴더의 프로그램이 계속 돈다. 그러면
# 윈도우가 그 폴더를 붙잡고 있어서 지울 수가 없는데, 정작 끄기는 자기
# 폴더 것만 찾으니 영영 못 끄는 상태가 된다. 실제로 그 일이 있었다.


def test_a_copy_running_in_another_folder_is_found(boot, tmp_path, monkeypatch):
    import subprocess
    import sys
    import time as _t

    other = tmp_path / "옛폴더"
    (other / "stock_analysis").mkdir(parents=True)
    (other / "stock_analysis" / "app.py").write_text("x = 1\n", encoding="utf-8")
    old_main = other / "main.py"
    old_main.write_text("import time; time.sleep(20)\n", encoding="utf-8")

    child = subprocess.Popen([sys.executable, str(old_main)])
    try:
        _t.sleep(0.5)
        assert child.pid in [pid for pid, _folder in boot.other_folder_pids()]
        assert child.pid not in boot.running_pids()      # 이 폴더 것은 아니다
    finally:
        child.kill()
        child.wait()


def test_someone_elses_main_py_is_left_alone(boot, tmp_path):
    """이름이 main.py 라는 이유로 남의 프로그램을 끄면 안 된다.

    옆에 stock_analysis/app.py 가 있어야 이 프로그램으로 본다.
    """
    import subprocess
    import sys
    import time as _t

    stranger = tmp_path / "남의프로그램"
    stranger.mkdir()
    other_main = stranger / "main.py"
    other_main.write_text("import time; time.sleep(20)\n", encoding="utf-8")

    child = subprocess.Popen([sys.executable, str(other_main)])
    try:
        _t.sleep(0.5)
        assert child.pid not in [pid for pid, _f in boot.other_folder_pids()]
    finally:
        child.kill()
        child.wait()


def test_our_own_folder_is_not_called_another_folder(boot, tmp_path):
    (tmp_path / "stock_analysis").mkdir()
    (tmp_path / "stock_analysis" / "app.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "main.py").write_text("x = 1\n", encoding="utf-8")

    assert all(folder != str(tmp_path) for _pid, folder in boot.other_folder_pids())
