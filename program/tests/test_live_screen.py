"""화면을 통째로 다시 그리지 않고 주가 칸만 갈아끼우기 · 차트 자료.

통째로 다시 불러오면 스크롤·펼친 칸·차트 확대가 전부 풀린다. 몇 초마다
바뀌는 건 주가 몇 칸뿐이라 그 칸만 보낸다.
"""

import json
import time
import urllib.request
from datetime import date, timedelta

import pytest

from stock_analysis import dashboard as D
from stock_analysis.dashboard import Dashboard, start_dashboard
from stock_analysis.ui.live import chart_data
from stock_analysis.ui.live import items as _items
from stock_analysis.metrics import Metrics
from stock_analysis.prices import Candle


def live_items(bot, market):
    return _items(bot, [t.ticker for t in bot.cached_targets() if t.market == market])


def _bars(count, start=date(2026, 1, 1), volume=True):
    return [Candle(start + timedelta(days=i), 100 + i, 102 + i, 99 + i, 101 + i,
                   volume=1000.0 * (i + 1) if volume else None) for i in range(count)]


def _cached(bot, **fields):
    target = bot.targets()[0]
    m = Metrics(ticker=target.ticker)
    m.price, m.price_change_pct = 150.0, 1.5
    for k, v in fields.items():
        setattr(m, k, v)
    bot._metrics_cache[target.cik] = m
    return target, m


# --- /live ---------------------------------------------------------------------
def test_live_sends_the_same_cells_the_page_draws(bot):
    """새로 받은 칸과 처음 그린 칸이 어긋나면 몇 초마다 모양이 바뀐다."""
    target, m = _cached(bot, spark=[1.0, 2.0, 3.0, 4.0, 5.0])

    from stock_analysis.ui import kit

    item = live_items(bot, "us")[target.ticker]

    assert item["price"] == kit.esc(kit.price_text(m))
    assert item["change"] == kit.change_html(m)
    assert item["spark"] == kit.spark_for(m)
    assert item["timeLong"] == kit.esc(kit.trade_time(m, long=True))
    # 처음 그린 쪽에도 같은 칸이 같은 모양으로 들어 있다
    html = Dashboard(bot).render()
    assert f'data-live="price" data-t="{target.ticker}">{item["price"]}<' in html


def test_live_never_asks_the_network(bot):
    """몇 초마다 불리는 자리다. 여기서 SEC 에 물으면 요청이 줄줄이 멈춘다."""
    _cached(bot)

    def refuse(*a, **k):
        raise AssertionError("몇 초마다 부르는 자리에서 네트워크를 썼습니다")

    bot.targets = refuse
    bot.edgar.ticker_map = refuse
    bot.prices.quote = refuse

    assert live_items(bot, "us")


def test_live_only_sends_the_market_on_screen(bot):
    _cached(bot)
    assert live_items(bot, "kr") == {}


def test_live_carries_todays_bar_for_the_chart(bot):
    target, _ = _cached(bot, bars=_bars(10))
    bar = live_items(bot, "us")[target.ticker]["bar"]

    assert bar["time"] == "2026-01-10"
    assert bar["close"] == 110
    assert bar["volume"] == 10000.0


def test_a_missing_volume_is_not_sent_as_zero(bot):
    """0 으로 보내면 차트에 '거래 없음' 막대가 그려진다."""
    target, _ = _cached(bot, bars=_bars(10, volume=False))
    bar = live_items(bot, "us")[target.ticker]["bar"]

    assert "volume" not in bar


# --- /bars ---------------------------------------------------------------------
def test_moving_averages_start_only_when_the_window_is_full(bot):
    """앞쪽 19일로 '20일 평균' 을 내면 그 구간 선이 거짓말을 한다."""
    target, _ = _cached(bot, bars=_bars(80))
    data = chart_data(bot, target.ticker)

    assert len(data["bars"]) == 80
    assert len(data["ma20"]) == 80 - 19
    assert len(data["ma60"]) == 80 - 59
    assert data["ma20"][0]["time"] == "2026-01-20"


def test_the_moving_average_is_a_plain_average(bot):
    target, _ = _cached(bot, bars=_bars(25))
    data = chart_data(bot, target.ticker)

    closes = [101 + i for i in range(25)]
    assert data["ma20"][-1]["value"] == pytest.approx(sum(closes[-20:]) / 20)


def test_too_few_bars_give_no_average(bot):
    target, _ = _cached(bot, bars=_bars(10))
    data = chart_data(bot, target.ticker)

    assert data["ma20"] == [] and data["ma60"] == []


def test_an_unknown_ticker_gets_an_empty_chart(bot):
    _cached(bot)
    assert chart_data(bot, "ZZZZ")["bars"] == []


# --- 페이지 ------------------------------------------------------------------
def test_the_page_loads_the_chart_from_this_computer(bot):
    """바깥 CDN 에서 받지 않는다. 인터넷이 끊겨도 떠야 하고, 차트를 그릴
    때마다 남의 서버에 흔적을 남길 이유가 없다."""
    target, _ = _cached(bot, bars=_bars(30))
    html = Dashboard(bot).render_path(f"/stock/{target.ticker}")

    assert '<script src="/static/lightweight-charts.js" defer>' in html
    assert '<script src="/static/chart.js" defer>' in html
    assert "unpkg" not in html and "jsdelivr" not in html and "googleapis" not in html
    # 글꼴도 같이 들고 다닌다
    css = (D.STATIC_DIR / "app.css").read_text(encoding="utf-8")
    assert 'url("/static/fonts/PretendardVariable.woff2")' in css
    assert (D.STATIC_DIR / "fonts" / "LICENSE-pretendard.txt").exists()


def test_the_chart_keeps_a_drawn_fallback(bot):
    """라이브러리를 못 불러와도 캔들이 통째로 사라지면 안 된다."""
    target, _ = _cached(bot, bars=_bars(30))
    html = Dashboard(bot).render_path(f"/stock/{target.ticker}")

    assert 'class="tv-chart"' in html
    assert 'class="tv-fallback"' in html and "c-body" in html


def test_the_whole_page_reloads_rarely_now(bot):
    """주가는 몇 초마다 따로 갈아끼운다. 통째 새로고침은 느린 칸을 위한 것뿐이다."""
    html = Dashboard(bot).render()
    assert 'http-equiv="refresh"' not in html          # 통째 새로고침을 걸지 않는다
    script = (D.STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert "LIVE_MS = 5000" in script                   # 숫자는 5초마다
    assert "'/status'" in script                        # 새 소식은 물어보고 알려준다


def test_the_bundled_chart_library_is_the_verified_one():
    """npm 에서 받은 그대로여야 한다. 누가 바꿔치기하면 안 된다."""
    import hashlib

    data = (D.STATIC_DIR / "lightweight-charts.js").read_bytes()
    assert data.startswith(b"/*!\n * @license\n * TradingView Lightweight Charts")
    assert "v4.2.3" in data[:200].decode()
    assert (D.STATIC_DIR / "LICENSE-lightweight-charts.txt").exists()
    # npm 의 lightweight-charts@4.2.3 (무결성 sha512 확인 후 복사) 의 sha256
    assert hashlib.sha256(data).hexdigest() == "c7dda807d662a95b3d257119ed315cec669e3bdf5aaece75c480a39307f23540"


# --- 실제로 서버를 띄워서 -----------------------------------------------------
@pytest.fixture
def server(bot):
    srv = start_dashboard(bot, port=8961, open_browser=False, preload=False)
    time.sleep(0.2)
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def _get(url):
    with urllib.request.urlopen(url, timeout=5) as r:
        return r.status, r.headers.get("Content-Type", ""), r.read()


def test_the_server_answers_live_bars_and_static(bot, server):
    target, _ = _cached(bot, bars=_bars(30))

    status, kind, body = _get(server + f"/live?m=us&t={target.ticker}")
    assert status == 200 and "json" in kind
    assert target.ticker in json.loads(body)["items"]

    status, kind, body = _get(server + f"/bars?t={target.ticker}")
    assert status == 200 and len(json.loads(body)["bars"]) == 30

    for name in ("lightweight-charts.js", "chart.js", "app.js"):
        status, kind, body = _get(server + f"/static/{name}")
        assert status == 200 and "javascript" in kind and body
    status, kind, body = _get(server + "/static/app.css")
    assert status == 200 and "css" in kind
    status, kind, body = _get(server + "/static/fonts/PretendardVariable.woff2")
    assert status == 200 and kind == "font/woff2" and len(body) > 100_000


def test_the_server_does_not_hand_out_other_files(bot, server):
    """/static/ 뒤에 아무 이름이나 넣어 설정 파일을 빼가면 안 된다."""
    import urllib.error

    for path in ("/static/../config.yml", "/static/LICENSE-lightweight-charts.txt",
                 "/static/%2e%2e/config.yml"):
        with pytest.raises(urllib.error.HTTPError) as err:
            _get(server + path)
        assert err.value.code == 404


def test_the_index_strip_is_refreshed_on_screen_too(bot, server):
    """'1분마다 갱신' 이라고 적어놓고 5분마다만 바뀌면 거짓말이다."""
    status, _, body = _get(server + "/live?m=us")
    assert "tape" in json.loads(body)

    html = Dashboard(bot).render()
    assert "data-live-tape" in html


def test_the_price_loop_also_refreshes_the_index_strip(bot, monkeypatch):
    """예전에는 5분짜리 감시 주기 안에서만 받았다."""
    import threading

    calls = []
    bot.refresh_prices = lambda: calls.append("prices") or 0
    bot.refresh_market = lambda force=False: calls.append("market")

    ran = {}

    def fake_thread(target, **kw):
        ran["loop"] = target
        return type("T", (), {"start": lambda s: None})()

    class _Stop(Exception):
        pass

    def fake_sleep(_seconds):
        if calls:                  # 한 바퀴 돌았으면 멈춘다
            raise _Stop

    monkeypatch.setattr(threading, "Thread", fake_thread)
    monkeypatch.setattr("time.sleep", fake_sleep)
    bot.start_price_loop()

    try:
        ran["loop"]()
    except _Stop:
        pass
    assert calls == ["prices", "market"]
