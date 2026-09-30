"""환율·지수 한 줄.

환율은 전부 1달러 기준이라는 약속이 깨지면 화면이 거짓말을 하게 된다.
그래서 '어떤 값을 어떻게 읽는가' 를 테스트로 고정한다.
"""

import json

from stock_analysis.fx import FX_SPECS, INDEX_SPECS, FxClient, Rate


def yahoo(price, previous, week_ago=None):
    """야후 5일치 응답 흉내. 어제 종가(previous)는 **봉에만** 있다.

    chartPreviousClose 는 '구간 시작 직전 종가' 라서 5일치에서는 약 1주일 전
    값이다. 일부러 다른 값(week_ago)을 넣어, 그걸 잘못 쓰면 시험이 깨지게 한다.
    """
    day = 86400
    today = 1_790_000_000
    return json.dumps({"chart": {"result": [{
        "meta": {"regularMarketPrice": price, "regularMarketTime": today + 3600,
                 "gmtoffset": 0,
                 "chartPreviousClose": week_ago if week_ago is not None else previous * 0.9},
        "timestamp": [today - day, today],
        "indicators": {"quote": [{"open": [previous, previous], "high": [previous, price],
                                  "low": [previous, previous], "close": [previous, price]}]},
    }]}})


class FakeHttp:
    def __init__(self, mapping=None, fail=()):
        self.mapping = mapping or {}
        self.fail = set(fail)
        self.calls = []

    def get_text(self, url, **kwargs):
        self.calls.append(url)
        for key in self.fail:
            if key in url:
                raise RuntimeError("막힘")
        for key, payload in self.mapping.items():
            if key in url:
                return payload
        raise RuntimeError("모르는 주소")


def test_every_currency_the_user_asked_for_is_covered():
    labels = {label for label, *_ in FX_SPECS}
    assert labels == {"원", "엔", "위안", "유로"}


def test_index_strip_includes_the_fear_gauge():
    labels = {label for label, *_ in INDEX_SPECS}
    assert {"S&P 500", "나스닥", "다우", "VIX"} <= labels


def test_rates_are_quoted_per_one_dollar():
    http = FakeHttp({"KRW=X": yahoo(1380.5, 1375.0)})
    client = FxClient(http)
    snapshot = client.refresh(force=True)

    won = next(r for r in snapshot.rates if r.label == "원")
    assert won.value == 1380.5
    assert won.text == "₩1,380.50"
    assert round(won.change_pct, 2) == 0.40
    assert won.direction == "up"          # 달러가 비싸짐 = 원화 약세


def test_stooq_is_used_when_yahoo_is_blocked():
    http = FakeHttp(
        {"usdkrw": "Symbol,Date,Time,Open,High,Low,Close,Volume\nUSDKRW,2026-08-12,10:00,1370,1385,1369,1380,0\n"},
        fail=["query1.finance.yahoo.com"],
    )
    snapshot = FxClient(http).refresh(force=True)
    won = next(r for r in snapshot.rates if r.label == "원")
    assert won.value == 1380
    assert won.source == "Stooq"


def test_a_dead_network_does_not_crash_the_page():
    client = FxClient(FakeHttp(fail=["http"]))
    assert client.refresh(force=True) is None
    assert client.cached() is None


def test_previous_values_survive_a_failed_refresh():
    http = FakeHttp({"KRW=X": yahoo(1380.5, 1375.0)})
    client = FxClient(http)
    client.refresh(force=True)

    http.fail.add("http")
    client.refresh(force=True)
    assert client.cached() is not None      # 직전 값을 계속 보여준다
    assert client.cached().rates[0].value == 1380.5


def test_cache_is_reused_until_it_goes_stale():
    http = FakeHttp({"KRW=X": yahoo(1380.5, 1375.0)})
    client = FxClient(http, ttl=3600)
    client.refresh(force=True)
    calls = len(http.calls)
    client.refresh()                        # 아직 신선하다 → 네트워크를 다시 쓰지 않는다
    assert len(http.calls) == calls


def test_negative_change_reads_as_a_falling_dollar():
    rate = Rate(label="원", value=1300.0, change_pct=-1.2, symbol="₩")
    assert rate.direction == "down"



# --- '전일 대비' 는 정말 전일 대비여야 한다 ----------------------------------
def test_the_daily_change_is_against_yesterday_not_a_week_ago():
    """5일치 응답의 chartPreviousClose 는 약 1주일 전 종가다. 그걸로 나눈 값을
    '전일 대비' 라고 적어 왔다. 봉에서 어제 종가를 찾아 써야 한다."""
    http = FakeHttp({"KRW=X": yahoo(1380.0, previous=1375.0, week_ago=1300.0)})
    snapshot = FxClient(http).refresh(force=True)

    won = next(r for r in snapshot.rates if r.label == "원")
    assert round(won.change_pct, 2) == 0.36        # (1380-1375)/1375
    assert round(won.change_pct, 2) != 6.15        # (1380-1300)/1300 — 예전 값


def test_the_backup_source_does_not_pass_off_intraday_as_daily():
    """Stooq 한 줄짜리에는 어제 종가가 없다. (종가-시가)/시가 는 '오늘 시가
    대비' 인데 '전일 대비' 자리에 넣어 왔다. 모르면 비워 둔다."""
    http = FakeHttp(
        {"usdkrw": "Symbol,Date,Time,Open,High,Low,Close,Volume\n"
                   "USDKRW,2026-08-12,10:00,1370,1385,1369,1380,0\n"},
        fail=("KRW=X",),
    )
    snapshot = FxClient(http).refresh(force=True)

    won = next(r for r in snapshot.rates if r.label == "원")
    assert won.value == 1380
    assert won.change_pct is None
