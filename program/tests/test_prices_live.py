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
