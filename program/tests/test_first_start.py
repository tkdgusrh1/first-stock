"""처음 켤 때 아무것도 묻지 않는다 — 이메일은 화면에서 한 번.

지키는 약속:
1. 이메일이 없으면 SEC 로 요청을 **보내지 않는다**(연락처 없이 두드리면 막힌다).
2. 화면 위쪽 띠에서 넣으면 config.yml 한 줄만 바뀌고, 다시 켜지 않아도 바로 쓴다.
"""

import pytest

from stock_analysis.dashboard import Dashboard
from stock_analysis.http import HttpClient, SecContactMissing


class Boom:
    def get(self, *a, **k):
        raise AssertionError("SEC 로 요청이 나가면 안 됩니다")


def test_no_email_means_no_request_to_sec():
    http = HttpClient("")
    http.session = Boom()
    assert not http.sec_ready
    with pytest.raises(SecContactMissing):
        http.get("https://www.sec.gov/files/company_tickers.json")


def test_other_sites_still_work_without_email(monkeypatch):
    http = HttpClient("")
    seen = []

    class Ok:
        status_code = 200

    class Session:
        headers = {}

        def get(self, url, **k):
            seen.append(url)
            return Ok()

    http.session = Session()
    http.get("https://query1.finance.yahoo.com/v8/finance/chart/AAPL")
    assert seen


def test_setting_the_email_turns_sec_on_without_restart():
    http = HttpClient("")
    http.set_user_agent("FirstStock hong@gmail.com")
    assert http.sec_ready
    assert http.session.headers["User-Agent"] == "FirstStock hong@gmail.com"


def test_the_screen_asks_for_the_email_and_saves_it(bot, tmp_path):
    path = tmp_path / "config.yml"
    path.write_text('user_agent: ""\nwatchlist: []\n', encoding="utf-8")
    bot.config.path = path
    bot.config.user_agent = ""
    bot.http = HttpClient("")
    dash = Dashboard(bot)
    dash._background = lambda message, func: message      # 시험에서는 실제로 불러오지 않는다
    html = dash.render_path("/?m=us")
    assert "이메일 한 번만 넣어주세요" in html
    assert "이메일 한 번만" not in dash.render_path("/?m=kr")          # 한국 화면은 SEC 가 필요 없다

    assert "형식이 아닙니다" in dash.run_action("contact", {"email": ["not-an-email"]})
    message = dash.run_action("contact", {"email": ["hong@gmail.com"], "back": ["/?m=us"]})
    assert "저장" in message or "불러오는" in message
    assert bot.http.sec_ready
    assert 'user_agent: "FirstStock hong@gmail.com"' in path.read_text(encoding="utf-8")
    assert "이메일 한 번만 넣어주세요" not in dash.render_path("/settings?m=us")
