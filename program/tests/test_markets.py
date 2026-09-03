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
