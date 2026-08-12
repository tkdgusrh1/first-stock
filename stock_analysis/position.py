"""내가 산 가격 대비 지금 얼마인가.

미국 주식은 두 번 움직인다. 주가가 오르고, 환율이 또 움직인다.
달러로는 벌었는데 원화로는 덜 벌 수도, 그 반대일 수도 있다.
그래서 둘을 나눠서 보여준다.

여기서는 지금 환율만 쓴다. 살 때 환율을 기억해 두지 않으므로
'원화 기준 손익' 은 지금 환율로 환산한 값이라는 뜻을 화면에 밝혀둔다.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Position:
    ticker: str
    buy_price: float
    shares: float
    price: float | None = None
    krw_rate: float | None = None      # 1달러 = ? 원 (지금 환율)

    @property
    def cost(self) -> float:
        return self.buy_price * self.shares

    @property
    def value(self) -> float | None:
        return self.price * self.shares if self.price else None

    @property
    def profit(self) -> float | None:
        return self.value - self.cost if self.value is not None else None

    @property
    def profit_pct(self) -> float | None:
        if self.price is None or not self.buy_price:
            return None
        return (self.price - self.buy_price) / self.buy_price * 100

    @property
    def profit_krw(self) -> float | None:
        profit = self.profit
        return profit * self.krw_rate if (profit is not None and self.krw_rate) else None

    @property
    def direction(self) -> str:
        profit = self.profit
        if profit is None:
            return ""
        return "up" if profit >= 0 else "down"


def build(watch, metrics, krw_rate: float | None) -> Position | None:
    """설정에 매수가와 수량이 둘 다 있을 때만 만든다."""
    buy_price = getattr(watch, "buy_price", None)
    shares = getattr(watch, "buy_shares", None)
    if not buy_price or not shares:
        return None
    return Position(
        ticker=watch.ticker,
        buy_price=float(buy_price),
        shares=float(shares),
        price=getattr(metrics, "price", None) if metrics else None,
        krw_rate=krw_rate,
    )


def krw_rate_from(snapshot) -> float | None:
    """환율 스냅샷에서 원화 값만 꺼낸다."""
    if snapshot is None:
        return None
    for rate in getattr(snapshot, "rates", []):
        if rate.label == "원":
            return rate.value
    return None


def won(value: float | None) -> str:
    if value is None:
        return "-"
    sign = "-" if value < 0 else ""
    value = abs(value)
    if value >= 1e8:
        return f"{sign}{value / 1e8:,.2f}억원"
    if value >= 1e4:
        return f"{sign}{value / 1e4:,.0f}만원"
    return f"{sign}{value:,.0f}원"
