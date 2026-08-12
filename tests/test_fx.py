"""환율·지수 한 줄.

환율은 전부 1달러 기준이라는 약속이 깨지면 화면이 거짓말을 하게 된다.
그래서 '어떤 값을 어떻게 읽는가' 를 테스트로 고정한다.
"""

import json

from stock_analysis.fx import FX_SPECS, INDEX_SPECS, FxClient, Rate


def yahoo(price, previous):
    return json.dumps(
        {"chart": {"result": [{"meta": {"regularMarketPrice": price,
                                        "chartPreviousClose": previous}}]}}
    )


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
