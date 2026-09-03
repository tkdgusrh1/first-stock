"""티커만 보고 어느 나라 증시인지 가르는 규칙.

여기서 틀리면 한국 종목을 SEC 에 물어보게 되고, 그러면 '못 찾음' 만 쌓인다.
반대로 미국 종목을 DART 에 물어봐도 마찬가지다.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stock_analysis import markets  # noqa: E402


@pytest.mark.parametrize("ticker", ["005930", "005930.KS", "035720.KQ", "A005930", "000660"])
def test_a_six_digit_code_is_a_korean_stock(ticker):
    assert markets.market_of(ticker) == markets.KR


@pytest.mark.parametrize("ticker", ["AAPL", "BRK-B", "NVDA", "SPY", ""])
def test_letters_mean_the_us_market(ticker):
    assert markets.market_of(ticker) == markets.US


def test_a_five_digit_number_is_not_a_korean_code():
    """한국 종목 코드는 정확히 여섯 자리다. 어중간한 숫자를 넘기면 안 된다."""
    assert markets.market_of("12345") == markets.US
    assert markets.market_of("1234567") == markets.US


def test_the_six_digit_code_is_pulled_out_however_it_was_written():
    for ticker in ("005930", "005930.KS", "A005930", "005930.kq"):
        assert markets.code_of(ticker) == "005930"


def test_a_us_ticker_has_no_korean_code():
    assert markets.code_of("AAPL") == ""


# --- 시세 기호 --------------------------------------------------------------
def test_a_korean_stock_gets_the_yahoo_suffix():
    assert markets.price_symbol("005930") == "005930.KS"
    assert markets.price_symbol("035720.KQ") == "035720.KQ"


def test_without_a_board_both_are_tried():
    """코스피인지 코스닥인지 안 적혀 있으면 두 군데 다 봐야 찾는다."""
    assert markets.price_symbols("005930") == ["005930.KS", "005930.KQ"]


def test_a_known_board_is_not_guessed_again():
    assert markets.price_symbols("035720.KQ") == ["035720.KQ"]


def test_a_us_ticker_is_left_alone():
    assert markets.price_symbol("AAPL") == "AAPL"
    assert markets.price_symbols("AAPL") == ["AAPL"]


# --- 장이 열려 있나 ---------------------------------------------------------
#
# 휴장일 표를 손으로 적지 않는다. 설날·추석은 음력이라 해마다 날짜가 바뀌는데,
# 그걸 기억으로 적어 넣으면 **틀린 날 '장중' 이라고 말하게 된다.**
# 대신 시세 제공처(거래소)가 알려주는 상태를 쓴다.


def test_the_exchange_state_is_taken_as_it_comes():
    assert markets.state_from_feed("REGULAR") == markets.OPEN
    assert markets.state_from_feed("PRE") == markets.PRE
    assert markets.state_from_feed("POST") == markets.POST
    assert markets.state_from_feed("CLOSED") == markets.CLOSED


def test_a_state_we_do_not_know_is_not_guessed():
    """모르는 값을 '장중' 으로 읽으면 닫힌 장을 열렸다고 말하게 된다."""
    assert markets.state_from_feed("WEIRD") == markets.UNKNOWN_STATE
    assert markets.state_from_feed(None) == markets.UNKNOWN_STATE
    assert markets.state_from_feed("") == markets.UNKNOWN_STATE


def test_every_state_has_something_to_show():
    for key in (markets.OPEN, markets.PRE, markets.POST,
                markets.CLOSED, markets.UNKNOWN_STATE):
        assert markets.STATE_LABEL[key] and markets.STATE_ICON[key]


@pytest.mark.parametrize("market,hour,expected", [
    ("us", 10, markets.OPEN),        # 미 동부 10시 — 장중
    ("us", 8, markets.PRE),
    ("us", 17, markets.POST),
    ("kr", 10, markets.OPEN),        # 한국 10시 — 장중
    ("kr", 8, markets.PRE),
    ("kr", 16, markets.POST),
])
def test_the_clock_guess_follows_local_trading_hours(market, hour, expected):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    zone = markets.HOURS[market][2]
    weekday = datetime(2026, 9, 2, hour, 0, tzinfo=ZoneInfo(zone))   # 수요일
    state, shown = markets.state_by_clock(market, weekday)

    assert state == expected
    assert shown == f"{hour:02d}:00"


def test_the_weekend_is_closed():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    saturday = datetime(2026, 9, 5, 11, 0, tzinfo=ZoneInfo("Asia/Seoul"))
    assert markets.state_by_clock("kr", saturday)[0] == markets.CLOSED


def test_the_trading_hours_are_stated_for_each_market():
    assert "09:30~16:00" in markets.hours_text("us")
    assert "09:00~15:30" in markets.hours_text("kr")
