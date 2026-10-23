"""휴대폰 앱(APK) 안에서 켤 때 — 앱 폴더에 설정을 만들고, PC 전용 단추는 숨기고 막는다."""

import time
import urllib.error
import urllib.request

import pytest

from stock_analysis import mobile


def test_release_tag_gives_the_version():
    assert mobile.parse_release({"tag_name": "android-v4.10.0"}) == "4.10.0"
    assert mobile.parse_release({"tag_name": "android-signing-key"}) is None
    assert mobile.parse_release({}) is None


@pytest.fixture
def phone_app(tmp_path, monkeypatch):
    from stock_analysis import dashboard

    monkeypatch.setattr(mobile, "_port", None)
    monkeypatch.setattr(mobile, "_key", "")
    monkeypatch.chdir(tmp_path)
    for name in (mobile.ENV, "FIRST_STOCK_HOME"):
        monkeypatch.setenv(name, "")        # start() 가 바꾼 값을 시험이 끝나면 되돌린다
    started = []
    monkeypatch.setattr("stock_analysis.app.Bot.run_forever", lambda self, dashboard=None: started.append(dashboard))
    # 진짜 Bot 이라 화면을 켜면 시세·지표를 인터넷에서 받기 시작한다. 그 스레드는 시험이 끝난 뒤에도 돌아
    # 작업 폴더로 돌아온 뒤 .cache/macro.json 같은 상대 경로 파일을 진짜 프로그램 폴더에 쓴다 — 켜지 않는다.
    monkeypatch.setattr(dashboard.Dashboard, "load_initial", lambda self: None)
    monkeypatch.setattr(dashboard.Dashboard, "start_market_refresh", lambda self, interval=60.0: None)

    def offline(*args, **kwargs):
        raise OSError("시험은 인터넷에 나가지 않는다")

    monkeypatch.setattr("requests.sessions.Session.request", offline)
    servers = []
    real_start = dashboard.start_dashboard

    def start_dashboard(*args, **kwargs):
        servers.append(real_start(*args, **kwargs))
        return servers[-1]

    monkeypatch.setattr(dashboard, "start_dashboard", start_dashboard)
    yield tmp_path / "app", started
    for server in servers:
        server.shutdown()
        server.server_close()


def test_start_makes_its_own_folder_and_serves_the_screen(phone_app):
    home, started = phone_app
    entry = mobile.start(str(home))
    assert mobile.start(str(home)) == entry                  # 다시 불러도 한 번만 켠다
    assert (home / "config.yml").exists()
    assert mobile.active()
    from stock_analysis import secrets

    assert secrets.home() == home / "keys"                    # 열쇠도 앱 폴더 안
    base = entry.split("/?")[0]
    # 같은 휴대폰의 다른 앱: 열쇠 없이 오면 아무것도 보여주지 않는다
    for path in ("/settings", "/healthz", "/static/app.css", "/?fs_key=wrong"):
        with pytest.raises(urllib.error.HTTPError) as refused:
            urllib.request.urlopen(base + path, timeout=10)
        assert refused.value.code == 403
    # 앱 화면: 처음 주소의 열쇠를 쿠키로 받아 두고, 그다음부터는 열쇠 없는 주소로 다닌다
    app = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())
    with app.open(entry, timeout=10) as resp:
        assert resp.url == base + "/"
    # 이상한 열쇠(한글)를 보내도 서버가 터지지 않고 거절한다
    with pytest.raises(urllib.error.HTTPError) as refused:
        urllib.request.urlopen(base + "/?fs_key=%ED%95%9C", timeout=10)
    assert refused.value.code == 403
    # 앱 화면을 다시 만들며 첫 주소를 또 열어도 열쇠는 주소에 남지 않는다
    with app.open(entry, timeout=10) as resp:
        assert resp.url == base + "/"
        assert mobile.app_key() not in resp.read().decode("utf-8")
    assert mobile.app_key() not in secrets.redact(f"GET {entry} HTTP/1.1")      # 기록에 남아도 가린다
    # 화면이 쓰는 이름(캘린더의 k=)은 열쇠로 보지 않는다 — 예전에는 캘린더를 누르면 홈으로 튕겼다
    with app.open(base + "/calendar?k=earn&d=2026-10-23", timeout=10) as resp:
        assert resp.url == base + "/calendar?k=earn&d=2026-10-23"
        assert "캘린더" in resp.read().decode("utf-8")
    with app.open(base + "/settings", timeout=10) as resp:
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


def test_apk_bundles_what_the_program_needs():
    """앱 안의 파이썬에는 requirements.txt 의 것이 다 있어야 하고, 시간대 자료(tzdata)도 따로 넣어야 한다.

    안드로이드에는 /usr/share/zoneinfo 가 없어 tzdata 가 없으면 미 동부 시각이 늘 -4 시간으로 고정된다.
    """
    import re
    from pathlib import Path

    program = Path(__file__).resolve().parent.parent
    gradle = (program / "mobile" / "app" / "build.gradle").read_text(encoding="utf-8")
    bundled = {name.lower() for name in re.findall(r'^\s*install\s+"([^"]+)"', gradle, re.M)}
    needed = {re.split(r"[<>=;\s]", line, maxsplit=1)[0].lower()
              for line in (program / "requirements.txt").read_text(encoding="utf-8").splitlines()
              if line.strip() and not line.startswith("#")}
    assert needed <= bundled
    assert "tzdata" in bundled


def test_apk_is_built_only_from_the_update_branch():
    """PC 판이 받는 브랜치에서만 APK 를 낸다 — 다른 브랜치의 코드가 '최신 APK' 로 휴대폰에 깔리지 않게."""
    from pathlib import Path

    import updater
    import yaml

    workflow = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "android.yml"
    if not workflow.exists():
        pytest.skip("저장소 밖(업데이트로 받은 폴더)에는 .github 가 없다")
    spec = yaml.safe_load(workflow.read_text(encoding="utf-8"))
    on = spec.get("on", spec.get(True))           # YAML 1.1 은 'on' 을 True 로 읽는다
    assert on["push"]["branches"] == [updater.BRANCH]
    assert spec["permissions"] == {"contents": "read"}
    publish = spec["jobs"]["publish"]
    assert "run" in publish["needs"]               # 가상 휴대폰에서 켜진 뒤에만 올린다
