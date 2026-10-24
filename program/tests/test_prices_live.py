"""시세 실시간 갱신 — 오늘 몫만 자주, 긴 과거는 드물게.

10년치 일봉을 5분마다 통째로 받으면 종목당 2,500줄을 되풀이해 받는 셈이고,
그러다 야후가 막으면 주가 자체가 안 나온다. 바뀌는 건 오늘 봉 하나뿐이다.
"""

import json
from datetime import datetime, timezone

from stock_analysis import prices as P


def _chart(days, opens=None, closes=None):
    """야후 차트 응답 흉내. days 는 (연,월,일) 목록."""
    stamps = [int(datetime(*d, 14, tzinfo=timezone.utc).timestamp()) for d in days]
    n = len(days)
    closes = closes or [100.0 + i for i in range(n)]
    opens = opens or [c - 1 for c in closes]
    return {"chart": {"result": [{
        "meta": {},
        "timestamp": stamps,
        "indicators": {"quote": [{
            "open": opens, "high": [c + 2 for c in closes],
            "low": [o - 2 for o in opens], "close": closes,
        }]},
    }]}}


class _Http:
    def __init__(self, by_range):
        self.by_range = by_range
        self.asked = []

    def get_text(self, url, **kw):
        span = url.split("range=")[1].split("&")[0]
        self.asked.append(span)
        if span not in self.by_range:
            raise OSError("no")
        return json.dumps(self.by_range[span])


LONG = [(2026, 9, d) for d in range(1, 29)]         # 확정된 과거
LIVE = [(2026, 9, 29), (2026, 9, 30)]               # 어제 · 오늘(진행 중)


def _client(long_days=LONG, live=None):
    http = _Http({"10y": _chart(long_days),
                  "5d": live or _chart(LIVE, closes=[500.0, 510.0])})
    return P.PriceClient(http), http


def test_the_long_history_is_fetched_once():
    """긴 과거는 하루에 한 줄 늘 뿐이다. 매 주기 받을 이유가 없다."""
    client, http = _client()

    client.candles("AAPL")
    client.forget_prices("AAPL")                 # 갱신 주기가 한 번 돈 셈
    client.candles("AAPL")

    assert http.asked.count("10y") == 1


def test_todays_bar_is_fetched_again_each_cycle():
    """바뀌는 건 오늘 봉이다. 그건 매 주기 새로 받아야 한다."""
    client, http = _client()

    client.candles("AAPL")
    client.forget_prices("AAPL")
    client.candles("AAPL")

    assert http.asked.count("5d") == 2


def test_the_live_tail_replaces_the_same_days():
    """같은 날이 두 번 나오면 차트에 봉이 겹쳐 그려진다."""
    client, _ = _client(long_days=LONG + [(2026, 9, 29)])

    bars = client.candles("AAPL")
    days = [b.day for b in bars]

    assert len(days) == len(set(days))           # 겹치는 날 없음
    assert bars[-1].close == 510.0               # 오늘은 새 값
    assert [b for b in bars if b.day.day == 29][0].close == 500.0   # 어제도 새 값


def test_the_price_history_ends_with_today():
    """52주 고점·수익률도 오늘 값을 봐야 한다."""
    client, _ = _client()

    rows = client.history("AAPL")
    assert rows[-1][0].day == 30
    assert rows[-1][1] == 510.0


def test_a_failed_live_fetch_falls_back_to_the_long_history():
    """오늘 몫을 못 받았다고 차트가 통째로 사라지면 안 된다."""
    http = _Http({"10y": _chart(LONG)})           # 5d 는 실패
    client = P.PriceClient(http)

    bars = client.candles("AAPL")
    assert len(bars) == len(LONG)


def test_a_missing_price_drops_that_day_only():
    """넷 중 하나가 없는 날을 종가로 메우면 있지도 않은 몸통을 그린다."""
    payload = _chart(LONG)
    payload["chart"]["result"][0]["indicators"]["quote"][0]["open"][3] = None

    bars = P._candles_from(payload["chart"]["result"][0])

    assert len(bars) == len(LONG) - 1
    assert all(None not in (b.open, b.high, b.low, b.close) for b in bars)


# --- 코스닥 종목도 주가가 떠야 한다 --------------------------------------------
class _Boards:
    """코스닥에만 있는 종목. .KS 로 물으면 없다고 한다."""

    def __init__(self, found_on):
        self.found_on = found_on
        self.asked = []

    def get_text(self, url, **kw):
        symbol = url.split("/chart/")[1].split("?")[0] if "/chart/" in url else url
        self.asked.append(symbol)
        if self.found_on not in symbol.upper():
            raise OSError("없는 종목")
        span = url.split("range=")[1].split("&")[0]
        payload = _chart(LONG if span == "10y" else LIVE)
        payload["chart"]["result"][0]["meta"] = {
            "regularMarketPrice": 123.0, "chartPreviousClose": 120.0,
            "symbol": symbol, "marketState": "REGULAR"}
        return json.dumps(payload)


def test_a_kosdaq_stock_is_found_after_kospi_misses():
    """시장을 모르면 코스피로 묻는다. 거기 없으면 코스닥으로 다시 물어야 한다.
    이게 없으면 코스닥 종목은 주가·캔들·52주가 전부 빈다."""
    http = _Boards(found_on=".KQ")
    client = P.PriceClient(http)

    assert client.candles("086520.KS")                 # 코스피로 물었지만
    assert any(".KQ" in s for s in http.asked)         # 코스닥에서 찾았다


def test_the_right_board_is_remembered():
    """한 번 알아냈으면 다음부터는 없는 쪽을 다시 두드리지 않는다."""
    http = _Boards(found_on=".KQ")
    client = P.PriceClient(http)

    client.candles("086520.KS")
    http.asked.clear()
    client.forget_prices("086520.KS")
    client.candles("086520.KS")

    assert not any(".KS" in s for s in http.asked)


def test_a_kospi_stock_does_not_ask_kosdaq():
    """코스피에서 바로 찾았으면 코스닥까지 물어볼 이유가 없다."""
    http = _Boards(found_on=".KS")
    client = P.PriceClient(http)

    client.candles("005930.KS")
    assert not any(".KQ" in s for s in http.asked)


def test_an_american_ticker_is_never_retried_on_a_korean_board():
    http = _Boards(found_on="NOPE")
    client = P.PriceClient(http)

    client.candles("AAPL")
    assert not any(".KQ" in s or ".KS" in s for s in http.asked)


def test_a_kosdaq_quote_is_found_too():
    """주가 한 줄도 코스닥으로 다시 물어야 한다. 캔들만 뜨고 주가가 비면 이상하다."""
    http = _Boards(found_on=".KQ")
    client = P.PriceClient(http)

    quote = client.quote("086520.KS")
    assert quote is not None and quote.price == 123.0


# --- 주가의 '언제' 는 거래 시각이다 --------------------------------------------
def test_the_quote_day_is_when_it_traded_not_when_we_asked():
    """금요일 종가를 월요일에 받았다고 월요일 주가처럼 보이면 안 된다."""
    friday_close = int(datetime(2026, 9, 25, 20, 0, tzinfo=timezone.utc).timestamp())
    quote = P._quote_from_meta("AAPL", {
        "regularMarketPrice": 250.0, "chartPreviousClose": 245.0,
        "regularMarketTime": friday_close, "gmtoffset": -4 * 3600,
    })

    assert quote.day == "2026-09-25"
    assert quote.traded_at.day == 25


def test_a_missing_trade_time_is_left_empty():
    """거래 시각을 모르는데 지금 시각으로 메우면 그게 거짓말이다."""
    quote = P._quote_from_meta("AAPL", {"regularMarketPrice": 250.0})

    assert quote.day is None
    assert quote.traded_at is None


def test_the_exchange_date_is_used_not_utc():
    """한국 장 마감(15:30)은 UTC 로 06:30 이다. 새벽 거래를 UTC 날짜로 적으면
    하루 어긋날 수 있다."""
    seoul_morning = int(datetime(2026, 9, 30, 23, 30, tzinfo=timezone.utc).timestamp())
    quote = P._quote_from_meta("005930.KS", {
        "regularMarketPrice": 75000.0, "regularMarketTime": seoul_morning,
        "gmtoffset": 9 * 3600,
    })

    assert quote.day == "2026-10-01"          # 서울 날짜로는 10월 1일 아침


# --- 한 번의 요청 · 전일 대비는 봉에서 -----------------------------------------
def test_quote_and_todays_bar_share_one_request():
    """같은 5일치 주소를 두 번 받던 것을 한 번으로. 같은 부담으로 두 배 자주 묻는다."""
    client, http = _client()

    client.quote("AAPL")
    client.candles("AAPL")

    assert http.asked.count("5d") == 1


def test_the_daily_change_uses_yesterdays_close_from_the_bars():
    """5일치 메타의 chartPreviousClose 는 5거래일 전 종가다. 그걸로 나누면
    '전일 대비' 가 아니라 약 1주일 등락이 된다."""
    payload = _chart(LIVE, closes=[500.0, 510.0])["chart"]["result"][0]
    payload["meta"] = {
        "regularMarketPrice": 510.0,
        "chartPreviousClose": 400.0,                      # 1주일 전 — 쓰면 안 된다
        "regularMarketTime": int(datetime(2026, 9, 30, 15, tzinfo=timezone.utc).timestamp()),
        "gmtoffset": 0,
    }

    quote = P._quote_from_meta("AAPL", payload["meta"], payload)

    assert round(quote.change_pct, 2) == 2.0          # (510-500)/500
    assert round(quote.change_pct, 2) != 27.5         # (510-400)/400 — 예전 값


def test_without_bars_the_change_is_left_empty():
    """어제 종가를 모르면 비운다. 다른 값으로 메우지 않는다."""
    quote = P._quote_from_meta("AAPL", {"regularMarketPrice": 250.0,
                                        "chartPreviousClose": 200.0})
    assert quote.change_pct is None


def test_volume_is_kept_and_a_missing_one_stays_empty():
    """거래량이 빈 날을 0 으로 메우면 '거래 없음' 이 된다."""
    payload = _chart(LONG)["chart"]["result"][0]
    volumes = [1000.0 * (i + 1) for i in range(len(LONG))]
    volumes[5] = None
    payload["indicators"]["quote"][0]["volume"] = volumes

    bars = P._candles_from(payload)

    assert bars[0].volume == 1000.0
    assert bars[5].volume is None


# --- 장 상태: 야후 차트 응답엔 marketState 가 없다 -------------------------------------
PERIODS = {"pre": {"start": 1791964800, "end": 1791984600},
           "regular": {"start": 1791984600, "end": 1792008000},
           "post": {"start": 1792008000, "end": 1792022400}}


def test_market_state_comes_from_todays_trading_periods():
    """윈도우 실기에서 받은 실제 SOXL 응답 모양. 정규장 중인데 '종가' 로 적던 문제."""
    from stock_analysis.prices import _state_from_periods

    meta = {"currentTradingPeriod": PERIODS}
    assert _state_from_periods(meta, 1792000709) == "REGULAR"     # 미 동부 13:58
    assert _state_from_periods(meta, 1791970000) == "PRE"
    assert _state_from_periods(meta, 1792010000) == "POST"
    assert _state_from_periods(meta, 1792030000) == "CLOSED"
    assert _state_from_periods({}, 1792000709) is None            # 모르면 모른다고


def test_quote_is_marked_regular_session_during_trading_hours(monkeypatch):
    from stock_analysis import prices

    monkeypatch.setattr(prices.time, "time", lambda: 1792000709)
    meta = {"regularMarketPrice": 110.47, "regularMarketTime": 1792000552, "gmtoffset": -14400,
            "currentTradingPeriod": PERIODS}
    quote = prices._quote_from_meta("SOXL", meta)
    assert quote.market_state == "REGULAR" and quote.state_label == "정규장"


def test_quote_carries_the_listed_product_name():
    """ETF 는 SEC 에 신탁 이름만 있다. 펀드 이름(3배·인버스가 적힌)은 시세 응답에 온다."""
    quote = P._quote_from_meta("SOXL", {"regularMarketPrice": 44.0,
                                        "longName": "Direxion Daily Semiconductor Bull 3X Shares"})
    assert quote.name == "Direxion Daily Semiconductor Bull 3X Shares"
    assert P._quote_from_meta("X", {"regularMarketPrice": 1.0, "shortName": "X Corp"}).name == "X Corp"


def test_a_split_seen_in_the_recent_bars_refetches_the_long_history():
    """분할 뒤 야후는 지난 값까지 새 기준으로 고친다. 긴 과거를 여섯 시간 기다리면 옛 과거에 새 최근 봉이 붙어 -90% 절벽이 생긴다."""
    days = LONG + LIVE
    before = [1000.0 + i for i in range(len(days))]
    after = [c / 10 for c in before]                                 # 10:1 분할 뒤 야후가 고쳐 적은 값
    http = _Http({"10y": _chart(days[:-1], closes=before[:-1]), "5d": _chart(days[-5:], closes=before[-5:])})
    client = P.PriceClient(http)
    client.candles("NVDA")
    http.by_range = {"10y": _chart(days[:-1], closes=after[:-1]), "5d": _chart(days[-5:], closes=after[-5:])}
    client.forget_prices("NVDA")                                     # 다음 주기 — 최근 봉만 새로 받는다
    closes = [b.close for b in client.candles("NVDA")]
    assert http.asked.count("10y") == 2
    assert max(closes) / min(closes) < 1.1                            # 절벽 없음
    client.forget_prices("NVDA")
    client.candles("NVDA")
    assert http.asked.count("10y") == 2                               # 맞으면 더 받지 않는다


def test_matching_recent_bars_do_not_refetch_the_long_history():
    days = LONG + LIVE
    closes = [100.0 + i for i in range(len(days))]
    http = _Http({"10y": _chart(days[:-1], closes=closes[:-1]), "5d": _chart(days[-5:], closes=closes[-5:])})
    client = P.PriceClient(http)
    client.candles("AAPL")
    client.forget_prices("AAPL")
    client.candles("AAPL")
    assert http.asked.count("10y") == 1


def test_a_lasting_difference_is_not_refetched_every_ten_minutes(monkeypatch):
    """다시 받아도 그대로 다르면 분할이 아니다(장중에 받은 봉 등) — 10분마다 10년치를 받으면 야후가 막는다."""
    days = LONG + LIVE
    closes = [100.0 + i for i in range(len(days))]
    other = list(closes)
    other[-3] *= 1.05                                                 # 5일 응답만 다른 날
    http = _Http({"10y": _chart(days[:-1], closes=closes[:-1]), "5d": _chart(days[-5:], closes=other[-5:])})
    client = P.PriceClient(http)
    clock = [1_000_000.0]
    monkeypatch.setattr(P.time, "time", lambda: clock[0])
    client.candles("AAPL")
    assert http.asked.count("10y") == 2                               # 한 번은 다시 받아 본다
    for _ in range(5):
        clock[0] += P.BASIS_RETRY + 1
        client.forget_prices("AAPL")
        client.candles("AAPL")
    assert http.asked.count("10y") == 2                               # 그 뒤로는 긴 과거 주기까지 묻지 않는다


def test_split_events_in_the_chart_are_kept():
    payload = _chart(LIVE)
    payload["chart"]["result"][0]["events"] = {"splits": {"1": {
        "date": int(datetime(2026, 9, 30, 13, 30, tzinfo=timezone.utc).timestamp()), "numerator": 10, "denominator": 1}}}
    http = _Http({"10y": _chart(LONG), "5d": payload})
    client = P.PriceClient(http)
    client.quote("NVDA")
    assert client.splits("NVDA") == [(datetime(2026, 9, 30).date(), 10.0)]
    assert [b.day for b in client.recent_bars("NVDA")] == [datetime(2026, 9, 29).date(), datetime(2026, 9, 30).date()]


def test_a_failed_refetch_marks_the_history_as_mixed_until_it_succeeds(monkeypatch):
    """분할 뒤 긴 과거를 다시 받지 못하면 옛 기준·새 기준이 섞인 채다 — 모의 계좌가 그 일봉으로 처리하지 않게 알린다."""
    days = LONG + LIVE
    before = [1000.0 + i for i in range(len(days))]
    after = [c / 10 for c in before]
    http = _Http({"10y": _chart(days[:-1], closes=before[:-1]), "5d": _chart(days[-5:], closes=before[-5:])})
    client = P.PriceClient(http)
    clock = [1_000_000.0]
    monkeypatch.setattr(P.time, "time", lambda: clock[0])
    client.candles("NVDA")
    assert not client.unsettled("NVDA")
    http.by_range = {"5d": _chart(days[-5:], closes=after[-5:])}           # 분할 — 10년치 받기는 실패
    client.forget_prices("NVDA")
    client.candles("NVDA")
    assert client.unsettled("NVDA")
    http.by_range["10y"] = _chart(days[:-1], closes=after[:-1])
    clock[0] += P.BASIS_RETRY + 1
    client.forget_prices("NVDA")
    closes = [b.close for b in client.candles("NVDA")]
    assert not client.unsettled("NVDA") and max(closes) / min(closes) < 1.1
