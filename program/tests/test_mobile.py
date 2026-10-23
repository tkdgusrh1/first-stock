"""휴대폰 앱(APK) 안에서 켤 때 — 앱 폴더에 설정을 만들고, PC 전용 단추는 숨기고 막는다."""

import time
import urllib.request

import pytest

from stock_analysis import mobile


def test_release_tag_gives_the_version():
    assert mobile.parse_release({"tag_name": "android-v4.10.0"}) == "4.10.0"
    assert mobile.parse_release({"tag_name": "android-signing-key"}) is None
    assert mobile.parse_release({}) is None


@pytest.fixture
def phone_app(tmp_path, monkeypatch):
    monkeypatch.setattr(mobile, "_port", None)
    monkeypatch.chdir(tmp_path)
    for name in (mobile.ENV, "FIRST_STOCK_HOME"):
        monkeypatch.setenv(name, "")        # start() 가 바꾼 값을 시험이 끝나면 되돌린다
    started = []
    monkeypatch.setattr("stock_analysis.app.Bot.run_forever", lambda self, dashboard=None: started.append(dashboard))
    yield tmp_path / "app", started


def test_start_makes_its_own_folder_and_serves_the_screen(phone_app):
    home, started = phone_app
    port = mobile.start(str(home))
    assert mobile.start(str(home)) == port                   # 다시 불러도 한 번만 켠다
    assert (home / "config.yml").exists()
    assert mobile.active()
    from stock_analysis import secrets

    assert secrets.home() == home / "keys"                    # 열쇠도 앱 폴더 안
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/settings", timeout=10) as resp:
        html = resp.read().decode("utf-8")
    assert "휴대폰에서 보기" not in html and 'value="quit"' not in html
    assert mobile.APK_URL in html                             # 업데이트는 새 APK 로
    for _ in range(50):
        if started:
            break
        time.sleep(0.02)
    assert started == [False]                                 # 감시는 화면 없이 따로 돈다


def test_pc_only_actions_are_refused_on_the_phone(bot, monkeypatch):
    from stock_analysis.dashboard import Dashboard

    monkeypatch.setenv(mobile.ENV, "1")
    dash = Dashboard(bot)
    for action in sorted(mobile.PC_ONLY_ACTIONS):
        assert "휴대폰 앱에서는" in dash.run_action(action, {})
    assert dash.busy is None                                  # 뒤에서 아무 일도 시작하지 않았다
