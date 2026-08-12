"""내 매수가 대비 손익. 달러와 원화를 나눠서 본다."""

from stock_analysis.config import Watch
from stock_analysis.fx import MarketSnapshot, Rate
from stock_analysis.position import build, krw_rate_from, won


class FakeMetrics:
    def __init__(self, price):
        self.price = price


def snapshot(krw=1380.0):
    from datetime import datetime, timezone

    return MarketSnapshot(
        rates=[Rate("원", krw, 0.4, 2, "₩"), Rate("엔", 147.0, 0.1, 2, "¥")],
        indexes=[], fetched_at=datetime.now(timezone.utc),
    )


def test_profit_in_dollars_and_won():
    watch = Watch(ticker="RKLB", buy_price=38.40, buy_shares=120)
    position = build(watch, FakeMetrics(48.20), 1380.0)

    assert round(position.cost, 2) == 4608.0
    assert round(position.value, 2) == 5784.0
    assert round(position.profit, 2) == 1176.0
    assert round(position.profit_pct, 2) == 25.52
    assert round(position.profit_krw) == 1_622_880
    assert position.direction == "up"


def test_loss_is_marked_down():
    watch = Watch(ticker="RKLB", buy_price=60.0, buy_shares=10)
    position = build(watch, FakeMetrics(48.20), 1380.0)
    assert position.profit < 0
    assert position.direction == "down"


def test_without_both_numbers_there_is_no_position():
    assert build(Watch(ticker="RKLB"), FakeMetrics(48.2), 1380.0) is None
    assert build(Watch(ticker="RKLB", buy_price=38.4), FakeMetrics(48.2), 1380.0) is None
    assert build(Watch(ticker="RKLB", buy_shares=10), FakeMetrics(48.2), 1380.0) is None


def test_without_a_price_the_dollar_side_stays_empty():
    position = build(Watch(ticker="RKLB", buy_price=38.4, buy_shares=10), FakeMetrics(None), 1380.0)
    assert position is not None
    assert position.value is None and position.profit is None


def test_without_an_exchange_rate_only_dollars_are_shown():
    position = build(Watch(ticker="RKLB", buy_price=38.4, buy_shares=10), FakeMetrics(48.2), None)
    assert position.profit is not None
    assert position.profit_krw is None


def test_krw_rate_is_picked_out_of_the_snapshot():
    assert krw_rate_from(snapshot(1382.4)) == 1382.4
    assert krw_rate_from(None) is None


def test_won_is_readable_at_a_glance():
    assert won(1_622_880) == "162만원"
    assert won(163_000_000) == "1.63억원"
    assert won(-1_622_880) == "-162만원"
    assert won(None) == "-"
