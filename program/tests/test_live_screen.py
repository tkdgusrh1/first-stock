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
def _paper_account(bot, paused=False, last_day="2026-10-23"):
    from stock_analysis.quant import strategies as strat
    from stock_analysis.quant.costs import CostModel
    from stock_analysis.quant.engine import Engine, Order, Position, Trade
    from stock_analysis.quant.sizing import RiskRules

    eng = Engine(strat.STRATEGIES["rotation"], RiskRules(), CostModel(0.0, 0.0, 0.0), 1000.0)
    eng.cash = 518.0                     # 1000 − XOM 101 + 120 − NVDA 301 − WMT 200
    eng.positions = {"NVDA": Position("NVDA", 3, 100.0, "2026-10-13", 90.0, fee=1.0),
                     "WMT": Position("WMT", 2, 100.0, last_day, 95.0)}
    eng.last_close = {"NVDA": 100.0, "WMT": 99.0, "AAPL": 200.0}
    eng.last_seen = {"NVDA": last_day, "WMT": last_day, "AAPL": last_day}
    eng.orders = [Order("AAPL", "buy", 1, "순위 상위", last_day)]
    eng.trades = [Trade("XOM", "2026-10-01", "2026-10-09", 1, 100.0, 120.0, 19.0, 0.19, "기한")]
    eng.events = [{"day": "2026-10-13", "text": "매수 NVDA 3주 @ 100.00"}, {"day": "2026-10-14", "text": "다른 기록"}]
    eng.curve = [("2026-10-22", 1010.0), (last_day, 1016.0)]
    eng.last_day = last_day
    bot.quant.set_paper("us", {"engine": eng.to_dict(), "started": "2026-10-01", "paused": paused})


def _five_days(*rows):
    """(날짜, 시가, 고가, 저가, 종가) — 최근 5일 봉 흉내."""
    from datetime import date

    from stock_analysis.prices import Candle

    return [Candle(date.fromisoformat(d), o, h, lo, c) for d, o, h, lo, c in rows]


def _quote(sym, price, day, prev=None, **kw):
    from datetime import datetime, timezone

    from stock_analysis.prices import Quote

    return Quote(sym, price, day=day, change_pct=(price / prev - 1) * 100 if prev else None,
                 traded_at=datetime(2026, 10, 24, 14, 30, tzinfo=timezone.utc), **kw)


EVENING = {   # 10-23 장이 끝나고 기록까지 한 뒤 — 지금 가격 = 그 날 종가
    "quotes": {"NVDA": _quote("NVDA", 100.0, "2026-10-23", 98.0, name="NVIDIA Corporation"),
               "AAPL": _quote("AAPL", 200.0, "2026-10-23", 199.0)},
    "recent": {"NVDA": (_five_days(("2026-10-22", 97, 99, 96, 98), ("2026-10-23", 98, 101, 97, 100)), []),
               "AAPL": (_five_days(("2026-10-22", 198, 200, 197, 199), ("2026-10-23", 199, 201, 198, 200)), [])},
}
SESSION = {   # 10-24 장중 — NVDA 오름, WMT 장중 손절선 아래로, AAPL 매수 주문은 시가에 체결
    "quotes": {"NVDA": _quote("NVDA", 110.0, "2026-10-24", 100.0, name="NVIDIA Corporation"),
               "WMT": _quote("WMT", 96.0, "2026-10-24", 99.0),
               "AAPL": _quote("AAPL", 205.0, "2026-10-24", 200.0)},
    "recent": {"NVDA": (_five_days(("2026-10-23", 98, 101, 97, 100), ("2026-10-24", 104, 112, 103, 110)), []),
               "WMT": (_five_days(("2026-10-23", 100, 100, 98, 99), ("2026-10-24", 97, 98, 94, 96)), []),
               "AAPL": (_five_days(("2026-10-23", 199, 201, 198, 200), ("2026-10-24", 202, 206, 201, 205)), [])},
}


def _row(view, ticker):
    return next(r for r in view["rows"] if r["ticker"] == ticker)


def test_after_the_close_positions_are_valued_and_the_day_is_the_last_recorded_one(bot):
    from stock_analysis.quant import paper

    _paper_account(bot)
    view = paper.live_positions(bot.quant.paper("us"), EVENING["quotes"], "us", EVENING["recent"])
    nvda, wmt = _row(view, "NVDA"), _row(view, "WMT")
    assert nvda["live"] and nvda["value"] == 300.0 and nvda["pnl"] == pytest.approx(300 - 301)   # 살 때 수수료 1 포함
    assert not wmt["live"] and wmt["fallback"] == "종가" and wmt["price"] == 99.0                # 못 받으면 종가, 표시
    assert view["equity"] == pytest.approx(1016) and view["total"] == pytest.approx(16)
    assert view["realized"] == pytest.approx(19) and view["unrealized"] == pytest.approx(-3)   # 16 = 19 − 3
    assert view["day"] == pytest.approx(6) and view["day_from"] == "2026-10-22" and view["today"] is None
    assert view["orders"][0]["ticker"] == "AAPL" and view["orders"][0]["live"] and view["orders"][0]["price"] == 200.0
    assert view["recent"][0]["text"].startswith("매수") and not view["today_events"]
    assert paper.live_tickers(bot.quant, "us") == ["NVDA", "WMT", "AAPL"]


def test_during_the_next_session_the_engine_rules_are_previewed_on_todays_bar(bot):
    """장이 열리면 어젯밤 낸 주문은 규칙상 이미 시가에 체결됐고, 장중 손절도 이미 났다 — 엔진 규칙 그대로 보여 준다."""
    from stock_analysis.quant import paper

    _paper_account(bot)
    view = paper.live_positions(bot.quant.paper("us"), SESSION["quotes"], "us", SESSION["recent"])
    assert view["today"] == "2026-10-24"
    assert {r["ticker"] for r in view["rows"]} == {"NVDA", "AAPL"}               # WMT 는 장중 손절선 95 에서 팔림
    aapl = _row(view, "AAPL")
    assert aapl["today"] and aapl["cost"] == 202.0 and aapl["value"] == 205.0     # 시가 202 에 1주
    assert view["cash"] == pytest.approx(518 - 202 + 190)
    assert view["equity"] == pytest.approx(506 + 330 + 205)
    assert view["realized"] == pytest.approx(19 - 10) and view["unrealized"] == pytest.approx(29 + 3)
    assert view["day"] == pytest.approx(3 * 10 + 2 * (95 - 99) + 3) and view["day_from"] == "2026-10-23"
    texts = [e["text"] for e in view["today_events"]]
    assert texts[0].startswith("매수 AAPL 1주 @ 202.00") and "손절선 도달" in texts[1]
    assert not view["orders"]
    assert bot.quant.paper("us")["engine"]["cash"] == 518.0                      # 기록은 그대로(사본에서만 돌림)


def test_a_live_price_on_another_basis_is_not_multiplied_into_the_old_share_count(bot):
    """분할 당일(계좌는 장이 끝난 뒤 새 기준으로 바꾼다)·오래된 값·5일 봉에 없는 날은 쓰지 않는다."""
    from stock_analysis.quant import paper

    _paper_account(bot)
    account = bot.quant.paper("us")
    split = {"NVDA": (_five_days(("2026-10-23", 9.8, 10.1, 9.7, 10.0), ("2026-10-24", 10.4, 11.2, 10.3, 11.0)), [])}
    marked = {"NVDA": (SESSION["recent"]["NVDA"][0], ["2026-10-24"])}
    old = {"NVDA": (_five_days(("2026-10-21", 95, 96, 94, 95), ("2026-10-22", 97, 99, 96, 98)), [])}
    cases = [({"NVDA": _quote("NVDA", 11.0, "2026-10-24", 10.0)}, split),          # 5일 봉이 이미 새 기준
             ({"NVDA": _quote("NVDA", 11.0, "2026-10-24", 10.0)}, marked),         # 분할 기록이 마지막 기록일 뒤
             ({"NVDA": _quote("NVDA", 98.0, "2026-10-22", 97.0)}, old)]           # 계좌보다 오래된 값
    for quotes, recent in cases:
        view = paper.live_positions(account, quotes, "us", recent)
        nvda = _row(view, "NVDA")
        assert not nvda["live"] and nvda["value"] == 300.0 and view["today"] is None   # 마지막 종가 100 × 3주


def test_an_account_two_days_behind_is_valued_but_not_previewed(bot):
    from stock_analysis.quant import paper

    _paper_account(bot, last_day="2026-10-22")
    account = bot.quant.paper("us")
    account["engine"]["last_close"]["NVDA"] = 98.0
    recent = {"NVDA": (_five_days(("2026-10-22", 97, 99, 96, 98), ("2026-10-23", 98, 101, 97, 100),
                             ("2026-10-24", 104, 112, 103, 110)), [])}
    view = paper.live_positions(account, {"NVDA": _quote("NVDA", 110.0, "2026-10-24", 100.0)}, "us", recent)
    assert _row(view, "NVDA")["live"] and _row(view, "NVDA")["value"] == 330.0   # 그 날(10-22) 종가로 기준을 맞춤
    assert view["today"] is None and view["behind"] and view["day"] is None and view["orders"]


def test_a_paused_account_is_not_previewed(bot):
    from stock_analysis.quant import paper

    _paper_account(bot, paused=True)
    view = paper.live_positions(bot.quant.paper("us"), SESSION["quotes"], "us", SESSION["recent"])
    assert view["paused"] and view["today"] is None and {r["ticker"] for r in view["rows"]} == {"NVDA", "WMT"}


def test_fills_are_not_previewed_when_an_order_has_no_price_today(bot):
    """판 돈으로 사는 주문이 있는데 파는 종목 시세를 못 받았으면 — '현금 부족 취소' 같은 없는 일을 보여 주지 않는다."""
    from stock_analysis.quant import paper

    _paper_account(bot)
    quotes = {t: q for t, q in SESSION["quotes"].items() if t != "AAPL"}       # 주문 종목(AAPL) 시세 없음
    view = paper.live_positions(bot.quant.paper("us"), quotes, "us", SESSION["recent"])
    assert view["held_back"] and view["today"] is None and not view["today_events"]
    assert {r["ticker"] for r in view["rows"]} == {"NVDA", "WMT"} and view["orders"][0]["ticker"] == "AAPL"
    assert view["day"] is None and not view["behind"]


def test_the_preview_list_is_not_lost_when_the_journal_is_full(bot):
    from stock_analysis.quant import paper

    _paper_account(bot)
    account = bot.quant.paper("us")
    account["engine"]["events"] = [{"day": "2026-09-01", "text": f"기록 {k}"} for k in range(400)]   # 일지 상한
    view = paper.live_positions(account, SESSION["quotes"], "us", SESSION["recent"])
    assert [e["text"].split()[:2] for e in view["today_events"]] == [["매수", "AAPL"], ["매도", "WMT"]]


def test_the_price_loop_fetches_paper_holdings_that_are_not_on_the_watchlist(bot, monkeypatch):
    from stock_analysis import markets
    from stock_analysis.prices import Quote

    _paper_account(bot)
    asked = []

    class FakePrices:
        def quote(self, symbol):
            asked.append(symbol)
            return Quote(symbol, 50.0) if symbol != "WMT" else None

        def recent_bars(self, symbol):
            return []

        def splits(self, symbol):
            return []

    bot.prices = FakePrices()
    monkeypatch.setattr(markets, "state_by_clock", lambda market, moment=None: (markets.OPEN, ""))
    bot.refresh_paper_quotes()
    assert set(asked) == {"NVDA", "WMT", "AAPL"}
    assert set(bot.paper_quotes("us")) == {"NVDA", "AAPL"} and set(bot.paper_recent("us")) == {"NVDA", "AAPL"}
    assert bot.paper_quotes("kr") == {}
    asked.clear()
    monkeypatch.setattr(markets, "state_by_clock", lambda market, moment=None: (markets.CLOSED, ""))
    bot.refresh_paper_quotes()
    assert not asked                     # 장이 닫혀 있으면 10분에 한 번 — 받지 못한 종목(WMT)이 있어도
    monkeypatch.setattr(markets, "state_by_clock", lambda market, moment=None: (markets.OPEN, ""))
    bot.refresh_paper_quotes()
    assert set(asked) == {"NVDA", "WMT", "AAPL"}


def test_paper_positions_show_on_the_quant_page_home_and_live_refresh(bot, server):
    _paper_account(bot)
    bot._paper_quotes["us"], bot._paper_recent["us"] = SESSION["quotes"], SESSION["recent"]
    quant = Dashboard(bot).render_path("/quant?m=us")
    assert "지금 포지션" in quant and 'data-live-paper="us"' in quant and "NVIDIA Corporation" in quant
    assert "+$29.00" in quant and "오늘 삼" in quant and "오늘 지금까지 — 장이 끝난 뒤 기록" in quant
    assert "엔진 규칙대로 미리 반영" in quant and "손절선 도달" in quant
    home = Dashboard(bot).render_path("/?m=us")
    assert "모의 계좌 지금" in home and 'data-paper-view="mini"' in home
    status, _, body = _get(server + "/live?m=us&paper=us&view=mini")
    payload = json.loads(body)
    assert status == 200 and "평가" in payload["paper"] and "NVDA" in payload["paper"]
    _, _, body = _get(server + "/live?m=us")
    assert "paper" not in json.loads(body)                                  # 묻지 않으면 보내지 않는다
    _paper_account(bot, paused=True)
    assert "멈춰 둔 계좌입니다" in Dashboard(bot).render_path("/quant?m=us")
    from stock_analysis.quant import paper

    paper.WAITING["us"] = "NVDA"
    try:
        assert "분할 정리 대기: NVDA" in Dashboard(bot).render_path("/quant?m=us")
    finally:
        paper.WAITING.pop("us", None)
    bot.quant.set_paper("us", None)
    _, _, body = _get(server + "/live?m=us&paper=us")
    assert json.loads(body)["paper"] == ""                                  # 계좌가 없어지면 칸을 숨긴다(app.js)
