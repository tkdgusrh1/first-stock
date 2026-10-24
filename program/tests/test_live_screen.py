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
    bot._metrics_cache[target.key] = m
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

    assert '<script src="/static/lightweight-charts.js?v=' in html
    assert '<script src="/static/chart.js?v=' in html
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


# --------------------------------------------------------------------------
# 모의 계좌 '지금 포지션' — 지금 가격으로 평가(기록은 그대로)
# --------------------------------------------------------------------------
def _paper_account(bot, paused=False):
    from stock_analysis.quant import strategies as strat
    from stock_analysis.quant.costs import CostModel
    from stock_analysis.quant.engine import Engine, Order, Position, Trade
    from stock_analysis.quant.sizing import RiskRules

    eng = Engine(strat.STRATEGIES["rotation"], RiskRules(), CostModel(0.0, 0.0, 0.0), 1000.0)
    eng.cash = 518.0                     # 1000 − XOM 101 + 120 − NVDA 301 − WMT 200
    eng.positions = {"NVDA": Position("NVDA", 3, 100.0, "2026-10-13", 90.0, fee=1.0),
                     "WMT": Position("WMT", 2, 100.0, "2026-10-23", 95.0)}
    eng.last_close = {"NVDA": 100.0, "WMT": 99.0, "AAPL": 200.0}
    eng.orders = [Order("AAPL", "buy", 1, "순위 상위", "2026-10-23")]
    eng.trades = [Trade("XOM", "2026-10-01", "2026-10-09", 1, 100.0, 120.0, 19.0, 0.19, "기한")]
    eng.events = [{"day": "2026-10-13", "text": "매수 NVDA 3주 @ 100.00"}, {"day": "2026-10-14", "text": "다른 기록"}]
    eng.last_day = "2026-10-23"
    bot.quant.set_paper("us", {"engine": eng.to_dict(), "started": "2026-10-01", "paused": paused})


def _quote(price, day="2026-10-24", change=None, **kw):
    from datetime import datetime, timezone

    from stock_analysis.prices import Quote

    return Quote("NVDA", price, day=day, change_pct=change, traded_at=datetime(2026, 10, 24, 14, 30, tzinfo=timezone.utc),
                 **kw)


def _row(view, ticker):
    return next(r for r in view["rows"] if r["ticker"] == ticker)


def test_paper_positions_are_valued_at_the_latest_price_and_fall_back_to_the_close(bot):
    from stock_analysis.quant import paper

    _paper_account(bot)
    quotes = {"NVDA": _quote(110.0, change=10.0, market_state="REGULAR", name="NVIDIA Corporation")}
    view = paper.live_positions(bot.quant.paper("us"), quotes)
    nvda, wmt = _row(view, "NVDA"), _row(view, "WMT")
    assert nvda["live"] and nvda["value"] == 330.0 and nvda["pnl"] == pytest.approx(330 - 301)   # 살 때 수수료 1 포함
    assert nvda["day_pnl"] == pytest.approx(3 * (110 - 100))                                  # 어제 종가 100 → 110
    assert not wmt["live"] and wmt["price"] == 99.0 and wmt["day_pnl"] is None               # 못 받으면 종가, 표시
    assert view["equity"] == pytest.approx(518 + 330 + 198) and view["total"] == pytest.approx(46)
    assert view["realized"] == pytest.approx(19) and view["unrealized"] == pytest.approx(27)  # 46 = 19 + 27
    assert view["day"] == pytest.approx(30.0) and not view["all_live"]
    assert view["orders"][0] == {"side": "buy", "ticker": "AAPL", "shares": 1, "reason": "순위 상위",
                                 "made": "2026-10-23", "price": 200.0, "live": False}
    assert view["recent"][0]["text"].startswith("매수")
    assert paper.live_tickers(bot.quant, "us") == ["NVDA", "WMT", "AAPL"]


def test_a_live_price_on_another_basis_is_not_multiplied_into_the_old_share_count(bot):
    """분할 당일(계좌는 장이 끝난 뒤 새 기준으로 바꾼다)·오래된 값은 쓰지 않는다 — 없는 -90% 를 만들지 않게."""
    from stock_analysis.quant import paper

    _paper_account(bot)
    account = bot.quant.paper("us")
    for quote in (_quote(11.0, change=10.0), _quote(95.0, day="2026-10-22", change=-1.0), _quote(110.0)):
        nvda = _row(paper.live_positions(account, {"NVDA": quote}), "NVDA")
        assert not nvda["live"] and nvda["value"] == 300.0                 # 마지막 종가 100 × 3주
    same_day = _row(paper.live_positions(account, {"NVDA": _quote(100.0, day="2026-10-23", change=2.0)}), "NVDA")
    assert same_day["live"] and same_day["day_pnl"] == pytest.approx(3 * (100 - 100 / 1.02))


def test_bought_today_counts_from_the_price_paid_and_a_touched_stop_is_flagged(bot):
    from stock_analysis.prices import Quote
    from stock_analysis.quant import paper

    _paper_account(bot)
    account = bot.quant.paper("us")
    wmt = Quote("WMT", 99.0, day="2026-10-23", change_pct=-3.0)              # 산 날(그 날 처리) 종가
    view = paper.live_positions(account, {"WMT": wmt, "NVDA": _quote(88.0, change=-12.0)})
    assert _row(view, "WMT")["day_pnl"] == pytest.approx(198 - 200)          # 어제 종가가 아니라 치른 돈부터
    assert _row(view, "NVDA")["touched"] and not _row(view, "WMT")["touched"]
    html = Dashboard(bot).render_path("/quant?m=us")
    assert "닿음" not in html                                                 # 받아 둔 가격이 없으면 표시도 없다
    bot._paper_quotes["us"] = {"NVDA": _quote(88.0, change=-12.0)}
    html = Dashboard(bot).render_path("/quant?m=us")
    assert "닿음</b> = 지금 가격이 손절선" in html
    _paper_account(bot, paused=True)
    html = Dashboard(bot).render_path("/quant?m=us")
    assert "멈춰 둔 계좌입니다" in html and "닿음</b> = " not in html


def test_the_price_loop_fetches_paper_holdings_that_are_not_on_the_watchlist(bot, monkeypatch):
    from stock_analysis import markets
    from stock_analysis.prices import Quote

    _paper_account(bot)
    asked, forgot = [], []

    class FakePrices:
        def forget_prices(self, symbol=""):
            forgot.append(symbol)

        def quote(self, symbol):
            asked.append(symbol)
            return Quote(symbol, 50.0) if symbol != "WMT" else None

    bot.prices = FakePrices()
    bot.refresh_paper_quotes()
    assert set(asked) == {"NVDA", "WMT", "AAPL"} and not forgot           # 캐시(1분)를 거친다 — 야후가 막지 않게
    assert set(bot.paper_quotes("us")) == {"NVDA", "AAPL"}                  # 못 받은 종목은 비워 둔다
    assert bot.paper_quotes("kr") == {}
    asked.clear()
    monkeypatch.setattr(markets, "state_by_clock", lambda market, moment=None: (markets.CLOSED, ""))
    bot._paper_quotes["us"]["WMT"] = Quote("WMT", 99.0)
    bot.refresh_paper_quotes()
    assert not asked                                                        # 장이 닫혀 있으면 10분에 한 번
    monkeypatch.setattr(markets, "state_by_clock", lambda market, moment=None: (markets.OPEN, ""))
    bot.refresh_paper_quotes()
    assert set(asked) == {"NVDA", "WMT", "AAPL"}


def test_paper_positions_show_on_the_quant_page_home_and_live_refresh(bot, server):
    _paper_account(bot)
    bot._paper_quotes["us"] = {"NVDA": _quote(110.0, change=10.0, name="NVIDIA Corporation")}
    quant = Dashboard(bot).render_path("/quant?m=us")
    assert "지금 포지션" in quant and 'data-live-paper="us"' in quant and "NVIDIA Corporation" in quant
    assert "+$29.00" in quant and "체결 대기 주문" in quant and "종가</span>" in quant
    assert "마지막 종가로 약 $200.00" in quant
    home = Dashboard(bot).render_path("/?m=us")
    assert "모의 계좌 지금" in home and 'data-paper-view="mini"' in home
    status, _, body = _get(server + "/live?m=us&paper=us&view=mini")
    payload = json.loads(body)
    assert status == 200 and "평가" in payload["paper"] and "NVDA" in payload["paper"]
    _, _, body = _get(server + "/live?m=us")
    assert "paper" not in json.loads(body)                                  # 묻지 않으면 보내지 않는다
