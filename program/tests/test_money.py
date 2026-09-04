"""돈을 그 나라 단위로 적는다.

'삼성전자 $75,000' 은 읽는 사람을 통째로 오해하게 만든다. 한국 주식은
원화로, 만·억·조로 끊어 적어야 크기가 잡힌다.
"""

from stock_analysis import money
from stock_analysis.metrics import Metrics, build_dart_metrics
from stock_analysis.position import Position


# --- 한 주 값은 줄이지 않는다 -------------------------------------------------
def test_a_korean_share_price_is_written_in_won():
    assert money.price(75000, money.KRW) == "75,000원"


def test_a_share_price_is_never_shortened():
    """75,000원을 '7.5만원' 으로 적으면 호가를 읽을 수 없다."""
    shown = money.price(75000, money.KRW)
    assert "만" not in shown and "억" not in shown


def test_an_american_share_price_keeps_cents():
    assert money.price(185.42) == "$185.42"


def test_a_penny_value_in_won_keeps_its_decimals():
    """1원 미만이 0원으로 반올림되면 값이 사라진 것처럼 보인다."""
    assert money.price(0.42, money.KRW) == "0.42원"


# --- 큰 값은 만·억·조로 끊는다 -----------------------------------------------
def test_revenue_in_won_uses_korean_units():
    """신문에 적는 방식 그대로. '300.9조원' 은 한국에서 쓰는 말이 아니다."""
    assert money.amount(300_870_903_000_000, money.KRW) == "300조 8,709억원"
    assert money.amount(1_234_500_000_000, money.KRW) == "1조 2,345억원"
    assert money.amount(2_000_000_000_000, money.KRW) == "2조원"      # 억이 0이면 뺀다
    assert money.amount(302_400_000_000, money.KRW) == "3,024억원"
    assert money.amount(45_000_000, money.KRW) == "4,500만원"
    assert money.amount(3_200, money.KRW) == "3,200원"


def test_a_small_amount_keeps_a_decimal():
    """1.5억을 '2억' 으로 적으면 3분의 1이 사라진다."""
    assert money.amount(150_000_000, money.KRW) == "1.5억원"


def test_rounding_carries_up_into_the_next_unit():
    """9,999.99억을 '10,000억원' 으로 적으면 안 된다."""
    assert money.amount(999_999_999_999, money.KRW) == "1조원"


def test_a_loss_keeps_its_sign():
    assert money.amount(-1_500_000_000_000, money.KRW).startswith("-")
    assert money.amount(-1_500_000_000).startswith("-")


def test_american_amounts_are_unchanged():
    assert money.amount(302_100_000_000) == "$302.10B"
    assert money.amount(45_600_000) == "$45.60M"


def test_a_missing_value_is_not_invented():
    assert money.amount(None, money.KRW) == "-"
    assert money.price(None, money.KRW) == "-"
    assert money.span(None, None, money.KRW) == "-"


# --- 범위·주식수 --------------------------------------------------------------
def test_a_won_range_puts_the_unit_once():
    assert money.span(68000, 88000, money.KRW) == "68,000 ~ 88,000원"


def test_a_dollar_range_marks_both_sides():
    assert money.span(150.5, 260.25) == "$150.50 ~ $260.25"


def test_share_counts_follow_the_same_cuts():
    assert money.shares(5_969_782_550, money.KRW) == "59.70억주"
    assert money.shares(15_000_000_000) == "15,000M"


# --- 지표에 붙어 있나 ---------------------------------------------------------
def test_dart_metrics_are_marked_as_won():
    """DART 는 원화 재무제표다. 표시가 없으면 화면이 달러로 적는다."""
    m = build_dart_metrics("005930", None)
    assert m.currency == money.KRW


def test_sec_metrics_stay_in_dollars():
    assert Metrics(ticker="AAPL").currency == money.USD


# --- 원화 종목에 환율을 곱하면 안 된다 ---------------------------------------
def test_a_korean_position_is_not_multiplied_by_the_exchange_rate():
    """이미 원화인데 환율을 곱하면 손익이 1,300배로 부풀어 완전히 틀린다."""
    position = Position(ticker="005930", buy_price=70000, shares=10,
                        price=75000, krw_rate=1355.0, currency=money.KRW)

    assert position.profit == 50000
    assert position.profit_krw == 50000        # 환산 없음
    assert position.in_won


def test_an_american_position_is_still_converted():
    position = Position(ticker="AAPL", buy_price=180.0, shares=10,
                        price=190.0, krw_rate=1355.0)

    assert position.profit == 100.0
    assert position.profit_krw == 100.0 * 1355.0
    assert not position.in_won
