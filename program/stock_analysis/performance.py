"""수익률 비교 — 이 종목이 시장(지수)보다 잘 갔나.

받아 둔 일봉 종가로만 계산한다. 기준일에 값이 없으면(상장 전 등) 그 칸은 비운다.
배당은 넣지 않은 **주가 수익률**이다(야후 'Adj Close' 가 아니라 종가 기준).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from . import markets

INDEX = {markets.US: ("^GSPC", "S&P 500"), markets.KR: ("^KS11", "코스피")}
KOSDAQ = ("^KQ11", "코스닥")


@dataclass
class Row:
    label: str
    since: date
    stock: float | None          # 0.12 = +12%
    index: float | None

    @property
    def gap(self) -> float | None:
        if self.stock is None or self.index is None:
            return None
        return self.stock - self.index


def index_for(market: str, symbol: str = "") -> tuple[str, str]:
    if market == markets.KR and symbol.upper().endswith(".KQ"):
        return KOSDAQ
    return INDEX.get(market, INDEX[markets.US])


def close_on_or_before(history: list[tuple[date, float]], day: date) -> float | None:
    found = None
    for d, close in history:
        if d > day:
            break
        found = close
    return found


def change(history: list[tuple[date, float]], since: date) -> float | None:
    """since 날의 종가(그날이 휴장이면 그 전 거래일)에서 마지막 종가까지."""
    if len(history) < 2 or history[0][0] > since:
        return None
    base = close_on_or_before(history, since)
    last = history[-1][1]
    if not base or last is None:
        return None
    return last / base - 1


def periods(today: date) -> list[tuple[str, date]]:
    return [
        ("1주", today - timedelta(days=7)),
        ("1개월", today - timedelta(days=30)),
        ("3개월", today - timedelta(days=91)),
        ("올해", date(today.year - 1, 12, 31)),
        ("1년", today - timedelta(days=365)),
        ("3년", today - timedelta(days=3 * 365)),
        ("5년", today - timedelta(days=5 * 365)),
    ]


def compare(stock: list[tuple[date, float]], index: list[tuple[date, float]], today: date) -> list[Row]:
    stock = [(d, c) for d, c in stock if c]
    index = [(d, c) for d, c in index if c]
    return [Row(label, since, change(stock, since), change(index, since)) for label, since in periods(today)]


def moving_average(history: list[tuple[date, float]], days: int) -> float | None:
    closes = [c for _d, c in history if c][-days:]
    if len(closes) < days:
        return None
    return sum(closes) / days
