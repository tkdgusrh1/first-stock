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
    assert boot.stop_running() == 0
    assert "찾지 못했습니다" in capsys.readouterr().out


def test_stopping_clears_the_marker(boot, monkeypatch, capsys):
    monkeypatch.setattr(boot, "pause", lambda: None)
    monkeypatch.setattr(boot.os, "kill", lambda pid, sig: None)
    boot.PID_PATH.parent.mkdir(parents=True, exist_ok=True)
    boot.PID_PATH.write_text("4242", encoding="utf-8")

    assert boot.stop_running() == 0
    assert not boot.PID_PATH.exists()
    assert "멈췄습니다" in capsys.readouterr().out
